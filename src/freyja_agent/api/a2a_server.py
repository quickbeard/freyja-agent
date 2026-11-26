"""Freyja as an A2A server: the card and the operations (feature 009, contracts/a2a-server.md).

Another agent hands Freyja a task over A2A 1.0, through the JSON-RPC binding the protocol's
SDK serves. Two routes, added by the application's lifespan while the feature is on and
taken out again when it ends (research R3):

- ``GET /.well-known/agent-card.json``, the card, built for each request from the address
  it was asked under;
- ``POST /a2a``, the SDK's JSON-RPC dispatcher behind Freyja's own checks.

The checks run in the contract's order, each before the next is looked at: the origin
(403, as the thread routes refuse a page of another origin), the credential (401: a request
with an ``Authorization`` header is refused until named callers exist), the size (413,
before the body is read whole); then the SDK's dispatcher checks the protocol's version and
the operation; then ``FreyjaRequestHandler`` checks the message's form, and
``ReceivedTasks`` the conversation and the limits (research R5).

This module is the one that imports the SDK's server modules and the protocol's types
(plan, Structure Decision): ``ReceivedTasks`` keeps the tasks, starts their runs, and says
what changed, and everything here only turns that into the protocol's shapes. The SDK's own
handler, which runs a task in a process that a restart loses, is not used (research R1).
"""

from __future__ import annotations

import contextlib
import functools
import logging
import re
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, NoReturn, override

from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Artifact,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, PROTOCOL_VERSION_CURRENT
from a2a.utils.errors import (
    A2AError,
    ContentTypeNotSupportedError,
    ExtendedAgentCardNotConfiguredError,
    InternalError,
    InvalidParamsError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Message as AsgiMessage

from freyja_agent.api.guards import refuse_other_origins
from freyja_agent.api.received import (
    Change,
    Ended,
    NotCancelableError,
    Piece,
    ReceivedTasks,
    RefusedError,
    Status,
    View,
)
from freyja_agent.runtime.received_tasks import LOCAL, ReceivedTask, new_id

logger = logging.getLogger(__name__)

CARD_PATH = AGENT_CARD_WELL_KNOWN_PATH
RPC_PATH = "/a2a"
# The most a request may hold; a message of 32,000 characters fits several times over (R5).
MAX_BODY = 256 * 1024
# A message's text, at most (FR-008), and a conversation id, at most.
MAX_TEXT = 32_000
MAX_CONTEXT_ID = 128
# The output modes a caller may accept for Freyja's reply to be sent to it.
TEXT_MODES = frozenset({"text/plain", "text/*", "*/*"})
# Where the route puts who is calling, for the context the handler is given (research R5).
CALLER_KEY = "freyja.a2a.caller"
# The one artifact a completed task has: Freyja's reply.
REPLY = "reply"
# A task's id, as the service makes it.
TASK_ID = re.compile(r"[0-9a-f]{32}")

DESCRIPTION = (
    "A general-purpose orchestrator. It takes a request in plain language, works on it with "
    "its tools and the agents it can hand work to, and asks a person before any action with "
    "effects outside its workspace."
)
UNAUTHORIZED = (
    "This address takes a request only with a caller's bearer token in the Authorization header."
)
TOO_LARGE = "A request to this address may hold at most 256 KiB."
INTERNAL = "An internal error."
# The message itself (InvalidParamsError), each a fixed sentence of the contract.
NO_TEXT = "The message holds no text."
TOO_LONG = "The message is longer than 32,000 characters."
TASK_NAMED = (
    "A message names no task: a task takes one message. Continue a conversation by its contextId."
)
CONTEXT_TOO_LONG = "The contextId is longer than 128 characters."
# Not now (UnsupportedOperationError), each with its reason in the error's data.
BUSY = (
    "This conversation takes no message now: its last task has not ended, or work it "
    "started is still going on."
)
TASK_LIMIT = "A limit on tasks in progress was reached. Send again when one has ended."
STREAM_LIMIT = "This task has as many streams as it takes. Read it with GetTask."

_STATES = {
    "working": TaskState.TASK_STATE_WORKING,
    "completed": TaskState.TASK_STATE_COMPLETED,
    "failed": TaskState.TASK_STATE_FAILED,
    "canceled": TaskState.TASK_STATE_CANCELED,
}


@functools.cache
def _version() -> str:
    """The service's version, read once: reading it reads the package's files."""
    try:
        return version("freyja-agent")
    except PackageNotFoundError:  # pragma: no cover - the package is always installed
        return "0.0.0"


def card(address: str) -> AgentCard:
    """The card, for callers that reach Freyja at ``address`` (contracts/a2a-server.md).

    It names no delegate agent, tool server, tool, model, profile, or caller, and is the
    same for every caller. At the service's own address it declares no security.
    """
    return AgentCard(
        name="Freyja",
        description=DESCRIPTION,
        version=_version(),
        supported_interfaces=[
            AgentInterface(
                url=address.rstrip("/") + RPC_PATH,
                protocol_binding="JSONRPC",
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=False, extended_agent_card=False
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="request",
                name="Take a request",
                description="Send a request in plain language; the result is Freyja's reply.",
                tags=["general"],
            )
        ],
    )


async def _card(request: Request) -> Response:
    """The card, with the scheme and host it was asked under."""
    return JSONResponse(MessageToDict(card(str(request.base_url))))


def _refusal(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"detail": {"code": code, "message": message}}, status_code=status, headers=headers
    )


async def _bounded_body(request: Request) -> bytes | None:
    """The request's body; None when it holds more than ``MAX_BODY``, read no further."""
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _guarded(dispatch: Callable[[Request], Awaitable[Response]]) -> Callable[..., Any]:
    """``POST /a2a``: the checks in front of the SDK's dispatcher, in the contract's order."""
    other_origin = refuse_other_origins("Tasks")

    async def endpoint(request: Request) -> Response:
        try:
            other_origin(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        # Named callers and their tokens come later (FR-005); until then any credential is
        # refused, before the body is read.
        if "authorization" in request.headers:
            return _refusal(401, "unauthorized", UNAUTHORIZED, {"WWW-Authenticate": "Bearer"})
        body = await _bounded_body(request)
        if body is None:
            return _refusal(413, "too_large", TOO_LARGE)
        sent = False

        async def receive() -> AsgiMessage:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            # After the body, what the client does next, such as leaving a stream.
            return await request.receive()

        scope = {**request.scope, CALLER_KEY: LOCAL}
        return await dispatch(Request(scope, receive))

    return endpoint


class _Caller(User):
    """Who is calling, by the name the route found (research R5)."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


class _CallerContext(DefaultServerCallContextBuilder):
    """The SDK's context, which keeps the request's headers for the version check, with the
    caller the route named; a request that reached here without one is refused."""

    def build_user(self, request: Request) -> User:
        caller = request.scope.get(CALLER_KEY)
        if not isinstance(caller, str) or not caller:
            raise InternalError(INTERNAL)
        return _Caller(caller)


def _status(view: View) -> TaskStatus:
    status = TaskStatus(state=_STATES[view.state])
    if view.sentence is not None:
        status.message.CopyFrom(_agent_message(view, view.sentence))
    status.timestamp.FromDatetime(datetime.now(UTC))
    return status


def _agent_message(view: View, text: str) -> Message:
    return Message(
        message_id=uuid.uuid4().hex,
        role=Role.ROLE_AGENT,
        parts=[Part(text=text)],
        task_id=view.task.task_id,
        context_id=view.task.context_id,
    )


def _reply(text: str) -> Artifact:
    return Artifact(artifact_id=REPLY, name=REPLY, parts=[Part(text=text)])


def task_of(view: View) -> Task:
    """A received task as the protocol has it: no history, and its reply once completed."""
    task = Task(id=view.task.task_id, context_id=view.task.context_id, status=_status(view))
    if view.state == "completed":
        task.artifacts.append(_reply(view.result or ""))
    return task


def _status_update(view: View) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=view.task.task_id, context_id=view.task.context_id, status=_status(view)
    )


def _piece(view: View, text: str, *, whole: bool = False) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=view.task.task_id,
        context_id=view.task.context_id,
        artifact=_reply(text),
        append=not whole,
        last_chunk=whole,
    )


def _message_of(params: SendMessageRequest) -> tuple[str, str]:
    """The text and the conversation id of a message whose form the contract takes.

    Raises the contract's error for each thing it refuses, in its order: push
    notifications, accepted output modes without text, a part that is not text, then the
    message itself (FR-008, FR-026a).
    """
    configuration = params.configuration
    if configuration.HasField("task_push_notification_config"):
        raise PushNotificationNotSupportedError
    modes = [mode.split(";")[0].strip().lower() for mode in configuration.accepted_output_modes]
    if modes and not TEXT_MODES.intersection(modes):
        raise ContentTypeNotSupportedError
    message = params.message
    texts = []
    for part in message.parts:
        if part.WhichOneof("content") != "text":
            raise ContentTypeNotSupportedError
        texts.append(part.text)
    text = "\n".join(texts)
    if not text.strip():
        raise InvalidParamsError(NO_TEXT)
    if len(text) > MAX_TEXT:
        raise InvalidParamsError(TOO_LONG)
    if message.task_id:
        raise InvalidParamsError(TASK_NAMED)
    if len(message.context_id) > MAX_CONTEXT_ID:
        raise InvalidParamsError(CONTEXT_TOO_LONG)
    return text, message.context_id


async def _events(first: View, changes: AsyncGenerator[Change, None]) -> AsyncGenerator[Any, None]:
    """A task's changes as the protocol's events, to its end (contracts/a2a-server.md): a
    status for each sentence, a piece of the reply for each piece, and at the end the whole
    reply of a completed task and the last status."""
    async with contextlib.aclosing(changes) as found:
        async for change in found:
            if isinstance(change, Status):
                yield _status_update(View(first.task, "working", change.sentence, None))
            elif isinstance(change, Piece):
                yield _piece(first, change.text)
            elif isinstance(change, Ended):
                if change.view.state == "completed":
                    yield _piece(change.view, change.view.result or "", whole=True)
                yield _status_update(change.view)
                return


def _guard[F: Callable[..., Awaitable[Any]]](method: F) -> F:
    """A handler's method that answers whatever it did not expect with the internal error."""

    @functools.wraps(method)
    async def guarded(self: FreyjaRequestHandler, *args: Any, **kwargs: Any) -> Any:
        try:
            return await method(self, *args, **kwargs)
        except A2AError:
            raise
        except Exception as exc:
            self._internal(method.__name__, exc)

    # functools.wraps gives the wrapper the method's name and signature, and its own type
    # says a wrapper, not the method's type.
    return guarded  # pyright: ignore[reportReturnType]


class FreyjaRequestHandler(RequestHandler):
    """The protocol's operations, on ``ReceivedTasks`` (contracts/a2a-server.md).

    Five operations are offered: send, with and without a stream, reading a task,
    subscribing to it, and canceling it. Every other refuses as the contract says. Whatever
    goes wrong otherwise is answered with the protocol's internal error and a fixed
    sentence, and logged by its kind, never with its text sent (research R1). A stream of a
    task counts against its streams on this instance (``stream_limit``, research R13).
    """

    def __init__(self, received: ReceivedTasks) -> None:
        self.received = received

    def _internal(self, what: str, exc: BaseException) -> NoReturn:
        logger.error(
            "A2A %s failed: %s: %s", what, type(exc).__name__, self.received.clean(str(exc))
        )
        raise InternalError(INTERNAL) from None

    async def _accept(self, params: SendMessageRequest, context: ServerCallContext) -> Any:
        text, context_id = _message_of(params)
        try:
            return await self.received.accept(context.user.user_name, context_id or new_id(), text)
        except RefusedError as exc:
            if exc.why == "busy":
                raise UnsupportedOperationError(
                    BUSY, data={"reason": "conversation_busy"}
                ) from None
            raise UnsupportedOperationError(TASK_LIMIT, data={"reason": "task_limit"}) from None

    async def _own(self, task_id: str, context: ServerCallContext) -> ReceivedTask:
        """The caller's own task by its id; an id the service could not have made is not
        looked up, and a task that is not the caller's is answered as one never made."""
        if TASK_ID.fullmatch(task_id) is None:
            raise TaskNotFoundError
        task = await self.received.store.get(task_id, context.user.user_name)
        if task is None:
            raise TaskNotFoundError
        return task

    def _stream(self, task_id: str) -> None:
        if not self.received.take_stream(task_id):
            raise UnsupportedOperationError(STREAM_LIMIT, data={"reason": "stream_limit"})

    @override
    @_guard
    async def on_message_send(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> Task | Message:
        accepted = await self._accept(params, context)
        settings = self.received.runtime.settings
        wait = 0 if params.configuration.return_immediately else settings.a2a_server_wait_seconds
        return task_of(await self.received.settle(accepted, wait))

    @override
    async def on_message_send_stream(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> AsyncGenerator[Any, None]:
        try:
            accepted = await self._accept(params, context)
        except A2AError:
            raise
        except Exception as exc:
            self._internal("on_message_send_stream", exc)
        events, following = accepted.events, False
        task_id = accepted.task.task_id
        # A new task: the first of its streams.
        self.received.take_stream(task_id)
        try:
            first = self.received.as_accepted(accepted.task)
            yield task_of(first)
            following = True
            async for event in _events(first, self.received.follow(accepted.task, events)):
                yield event
        except A2AError:
            raise
        except Exception as exc:
            self._internal("on_message_send_stream", exc)
        finally:
            self.received.give_stream(task_id)
            if not following:
                self.received.leave(events)

    @override
    @_guard
    async def on_get_task(self, params: GetTaskRequest, context: ServerCallContext) -> Task:
        # No history is returned, whatever ``historyLength`` asks.
        return task_of(await self.received.view(await self._own(params.id, context)))

    @override
    async def on_list_tasks(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        raise UnsupportedOperationError

    @override
    @_guard
    async def on_cancel_task(
        self, params: CancelTaskRequest, context: ServerCallContext
    ) -> Task | None:
        """The task, canceled: a run stops after the step under way, and a proposal is
        rejected by rule (research R11). Nothing of the request but the id is read."""
        task = await self._own(params.id, context)
        try:
            return task_of(await self.received.cancel(task))
        except NotCancelableError:
            raise TaskNotCancelableError from None

    @override
    async def on_subscribe_to_task(
        self, params: SubscribeToTaskRequest, context: ServerCallContext
    ) -> AsyncGenerator[Any, None]:
        """The task as it stands, then its changes to its end; refused for a task that has
        ended, and past the streams one task takes on this instance."""
        try:
            first = await self.received.view(await self._own(params.id, context))
            if first.state != "working":
                raise UnsupportedOperationError
            self._stream(first.task.task_id)
        except A2AError:
            raise
        except Exception as exc:
            self._internal("on_subscribe_to_task", exc)
        try:
            yield task_of(first)
            changes = self.received.follow(first.task, said=first.sentence)
            async for event in _events(first, changes):
                yield event
        except A2AError:
            raise
        except Exception as exc:
            self._internal("on_subscribe_to_task", exc)
        finally:
            self.received.give_stream(first.task.task_id)

    @override
    async def on_create_task_push_notification_config(
        self, params: TaskPushNotificationConfig, context: ServerCallContext
    ) -> TaskPushNotificationConfig:
        raise PushNotificationNotSupportedError

    @override
    async def on_get_task_push_notification_config(
        self, params: GetTaskPushNotificationConfigRequest, context: ServerCallContext
    ) -> TaskPushNotificationConfig:
        raise PushNotificationNotSupportedError

    @override
    async def on_list_task_push_notification_configs(
        self, params: ListTaskPushNotificationConfigsRequest, context: ServerCallContext
    ) -> ListTaskPushNotificationConfigsResponse:
        raise PushNotificationNotSupportedError

    @override
    async def on_delete_task_push_notification_config(
        self, params: DeleteTaskPushNotificationConfigRequest, context: ServerCallContext
    ) -> None:
        raise PushNotificationNotSupportedError

    @override
    async def on_get_extended_agent_card(
        self, params: GetExtendedAgentCardRequest, context: ServerCallContext
    ) -> AgentCard:
        raise ExtendedAgentCardNotConfiguredError


def routes(received: ReceivedTasks) -> list[Route]:
    """The card's route and the operations' route at the service's own address."""
    dispatcher = create_jsonrpc_routes(
        FreyjaRequestHandler(received), RPC_PATH, context_builder=_CallerContext()
    )[0]
    return [
        Route(CARD_PATH, _card, methods=["GET"]),
        Route(RPC_PATH, _guarded(dispatcher.endpoint), methods=["POST"]),
    ]
