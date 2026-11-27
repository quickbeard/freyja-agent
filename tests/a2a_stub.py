"""A stand-in A2A agent for the suite, built with the SDK's own server classes (003 research R16).

The suite serves it inside the test process through ``tests.asgi_stream``; run as a module it
serves on the loopback interface for the transport test and the quickstart:

    uv run python -m tests.a2a_stub --http 9998

It records every message and every cancel it receives, and behaves by a keyword at the start of
the message text:

- (default) completes with the message and the conversation's context id;
- ``ask``: asks one question (input required) and completes once answered in the same task,
  after ``<seconds>`` when the answer starts with ``slow <seconds>``; such an answer first sends
  a working status with ``working_text``;
- ``slow <seconds>``: stays working that long, then completes;
- ``hold``: stays working until the test calls ``StubAgent.release()``, then completes; ``hold
  <ending>`` ends as ``fail``, ``reject``, ``auth``, ``ask``, or ``cancel`` instead, so a task
  that is already pending can end each way. While it waits, ``StubAgent.report(text)`` makes
  it send a working status with that text, or with no message for None (feature 008);
- ``fail``, ``reject``, ``auth``: fails, rejects, or asks for authentication;
- ``parts``: completes with a text, a data, a raw-bytes, and a URL part;
- ``chunks``: completes with one artifact sent in several updates: a draft, then its
  replacement, then two chunks appended to it, with a data part after the text;
- ``progress <n>``: sends n status updates before completing;
- ``steps <n> <seconds>``: stays working and sends "Step k of n" for k = 1 to n, the first
  ``<seconds>`` after its first working status and each later one ``<seconds>`` after the one
  before, then completes right after the last (feature 008, for a run by hand too);
- ``message``: answers with a direct message instead of a task.

Feature 009: the stand-in keeps the hand-over count each message carried,
``metadata["freyja.hops"]``, in its record (``ReceivedMessage.hops``, None for a message that
carried none), and the metadata each cancel request carried (``StubRecord.cancel_hops``).
Served by hand it writes one line per message to its terminal, with the count, as the
quickstart reads it (specs/009-a2a-server/quickstart.md, section 5).

Every task's first working status carries ``StubAgent.working_text``, "Working on it." unless a
test changes it; with None it carries no message.

Card options cover what startup must refuse or ignore: no streaming, security requirements,
input modes without text, no JSON-RPC interface, another advertised address, no skills, and a
description that claims to be read-only. ``task_ids`` replaces the server's task id generator,
for replies with malformed ids (the SDK picks the id before the agent sees the message, so this
is an option rather than a keyword).

Feature 005 reads a card under limits, so the card can also be made large (``skill_count``,
``tag_count``, ``text_length``), and the stand-in can serve something else where its card
would be: any bytes with any status (``card_body``, ``card_status``), the body compressed
whatever the request asked for (``card_encoding``), or sent with no length announced
(``card_chunked``).
"""

import argparse
import asyncio
import gzip
import json
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from a2a.helpers import (
    get_message_text,
    new_data_part,
    new_raw_part,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
    new_url_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.agent_execution.simple_request_context_builder import (
    SimpleRequestContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.id_generator import IDGenerator, IDGeneratorContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
    TaskState,
)
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette
from starlette.responses import RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

CARD_PATH = "/.well-known/agent-card.json"
DEFAULT_URL = "http://127.0.0.1:9999/"
ELSEWHERE = "http://elsewhere.example:1234/a2a"
# How a replaced card body is typed, and the size of the pieces a chunked one is sent in.
JSON = "application/json"
CHUNK = 64 * 1024
# How long ``StubAgent.report`` waits for the held tasks to store what it asked them to send.
REPORT_SECONDS = 5.0
# Where a message carries the count of hand-overs between agents it has passed (feature 009).
HOPS_METADATA = "freyja.hops"


@dataclass
class ReceivedMessage:
    text: str
    # Every part as (kind, text or None, metadata) in order.
    parts: list[tuple[str, str | None, dict[str, Any]]]
    context_id: str
    task_id: str
    message_id: str
    # The hand-over count the message carried, as it was sent; None when it carried none.
    hops: Any = None


@dataclass
class StubRecord:
    messages: list[ReceivedMessage] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)
    # The hand-over count each cancel request carried, None for one that carried none.
    cancel_hops: list[Any] = field(default_factory=list)


def hops_of(metadata: Any) -> Any:
    """The hand-over count in a message's or a request's metadata, a whole number as an
    ``int``; None when there is none."""
    found = MessageToDict(metadata).get(HOPS_METADATA)
    if isinstance(found, float) and found.is_integer():
        return int(found)
    return found


def _parts(message: Any) -> list[tuple[str, str | None, dict[str, Any]]]:
    out = []
    for part in message.parts:
        kind = part.WhichOneof("content") or "unknown"
        metadata = MessageToDict(part.metadata) if part.HasField("metadata") else {}
        out.append((kind, part.text if kind == "text" else None, metadata))
    return out


class _Executor(AgentExecutor):
    def __init__(self, stub: "StubAgent") -> None:
        self.stub = stub

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.stub.loop = asyncio.get_running_loop()
        message = context.message
        text = get_message_text(message) if message is not None else ""
        self.stub.record.messages.append(
            ReceivedMessage(
                text=text,
                parts=_parts(message) if message is not None else [],
                context_id=context.context_id or "",
                task_id=context.task_id or "",
                message_id=message.message_id if message is not None else "",
                hops=hops_of(message.metadata) if message is not None else None,
            )
        )
        if self.stub.echo:
            count = self.stub.record.messages[-1].hops
            sys.stdout.write(f"Received, hand-over count {count}: {text}\n")
            sys.stdout.flush()
        keyword, _, rest = text.strip().partition(" ")
        keyword = keyword.rstrip(":").lower()

        if keyword == "message":
            await event_queue.enqueue_event(
                new_text_message(f"Direct reply: {rest}", context_id=context.context_id)
            )
            return

        task = context.current_task
        if task is None:
            task = new_task_from_user_message(message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)

        def say(words: str) -> Any:
            return new_text_message(words, context_id=task.context_id, task_id=task.id)

        def working(words: str | None) -> Any:
            """A working status with ``words``, or with no message for None."""
            return updater.update_status(
                TaskState.TASK_STATE_WORKING, message=say(words) if words is not None else None
            )

        # An answer to an earlier question continues that task; one starting with ``slow
        # <seconds>`` reports ``working_text`` and takes that long.
        if task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            if keyword == "slow":
                await working(self.stub.working_text)
                await asyncio.sleep(float(rest.split()[0].rstrip(":")))
            await updater.add_artifact(parts=[new_text_part(f"Answered: {text}")])
            await updater.complete()
            return

        await working(self.stub.working_text)
        if await self._ended(keyword, updater, say):
            return
        if keyword == "progress":
            for step in range(1, int(rest.split()[0]) + 1):
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING, message=say(f"Step {step}")
                )
        stop = self.stub.cancel_events.setdefault(task.id, asyncio.Event())
        if keyword == "steps":
            count, seconds = rest.split()[:2]
            total = int(count)
            for step in range(1, total + 1):
                if await _stopped(stop, asyncio.sleep(float(seconds.rstrip(":")))):
                    return
                await working(f"Step {step} of {total}")
        if keyword in ("slow", "hold"):
            if keyword == "slow":
                if await _stopped(stop, asyncio.sleep(float(rest.split()[0].rstrip(":")))):
                    return
            elif not await self._hold(task.id, stop, working, context.call_context):
                return
            ending = rest.split()[0].rstrip(":").lower() if keyword == "hold" and rest else ""
            if ending == "cancel":
                await updater.cancel(message=say("The stub canceled this task itself."))
                return
            if await self._ended(ending, updater, say):
                return
        if keyword == "chunks":
            # One artifact id throughout: the draft is replaced, and the chunks are appended.
            await updater.add_artifact(parts=[new_text_part("A draft.")], artifact_id="report")
            await updater.add_artifact(
                parts=[new_text_part("The quarterly ")], artifact_id="report", last_chunk=False
            )
            await updater.add_artifact(
                parts=[new_text_part("report is ")],
                artifact_id="report",
                append=True,
                last_chunk=False,
            )
            await updater.add_artifact(
                parts=[new_text_part("ready."), new_data_part({"pages": 4})],
                artifact_id="report",
                append=True,
                last_chunk=True,
            )
            await updater.complete()
            return
        if keyword == "parts":
            parts = [
                new_text_part("Here is the report."),
                new_data_part({"total": 3, "ok": True}),
                new_raw_part(b"\x89PNG", media_type="image/png", filename="chart.png"),
                new_url_part("https://files.example/report.pdf", media_type="application/pdf"),
            ]
        else:
            parts = [new_text_part(f"Received in context {task.context_id}: {text}")]
        await updater.add_artifact(parts=parts)
        await updater.complete()

    async def _hold(
        self,
        task_id: str,
        stop: asyncio.Event,
        working: Callable[[str | None], Awaitable[None]],
        call: ServerCallContext,
    ) -> bool:
        """Wait for the release, sending each report meanwhile; False when canceled.

        A report made before the task began to wait is not this task's. Each one is sent
        once, and the count of those sent is noted only once the status is in the task store,
        so that ``StubAgent.report`` returns when a read of the task would show it.
        """
        stub = self.stub
        # Under the lock ``report`` appends under, so a report is either before this task
        # began to wait, and not its own, or waited for by ``report`` until it is stored.
        with stub.lock:
            sent = len(stub.reports)
            stub.holding[task_id] = sent
        try:
            while True:
                reported = stub.reported
                if stop.is_set():
                    return False
                if len(stub.reports) > sent:
                    for words in stub.reports[sent:]:
                        if stop.is_set():
                            return False
                        await working(words)
                        await stub.stored(task_id, words, call)
                        sent += 1
                        with stub.lock:
                            stub.holding[task_id] = sent
                    continue
                waiters = {
                    asyncio.ensure_future(stub.released.wait()),
                    asyncio.ensure_future(stop.wait()),
                    asyncio.ensure_future(reported.wait()),
                }
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                for waiter in waiters:
                    waiter.cancel()
                if stop.is_set():
                    return False
                if stub.released.is_set():
                    return True
        finally:
            with stub.lock:
                stub.holding.pop(task_id, None)

    async def _ended(self, keyword: str, updater: TaskUpdater, say: Any) -> bool:
        """End the task the way ``keyword`` asks, if it names an ending; True when it did."""
        if keyword == "ask":
            await updater.requires_input(message=say(self.stub.question))
        elif keyword == "fail":
            await updater.failed(message=say("The stub failed this task on purpose."))
        elif keyword == "reject":
            await updater.reject(message=say("The stub does not take this task."))
        elif keyword == "auth":
            await updater.requires_auth(message=say("The stub needs you to sign in."))
        else:
            return False
        return True

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        self.stub.record.cancels.append(task_id)
        self.stub.cancel_events.setdefault(task_id, asyncio.Event()).set()
        updater = TaskUpdater(
            event_queue=event_queue, task_id=task_id, context_id=context.context_id or ""
        )
        await updater.cancel()


async def _stopped(stop: asyncio.Event, wait: Awaitable[Any]) -> bool:
    """Wait for ``wait`` or for the task's cancel, whichever comes first; True on the cancel."""
    waiter = asyncio.ensure_future(wait)
    stopper = asyncio.ensure_future(stop.wait())
    await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
    waiter.cancel()
    stopper.cancel()
    return stop.is_set()


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The event loop running in this thread, or None."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _status_text(task: Any) -> str | None:
    """The text of a stored task's status message, or None when it has none."""
    if not task.status.HasField("message"):
        return None
    return get_message_text(task.status.message)


class _CallableIds(IDGenerator):
    def __init__(self, make: Callable[[], str]) -> None:
        self._make = make

    def generate(self, context: IDGeneratorContext) -> str:
        return self._make()


def stub_card(
    name: str = "stub",
    *,
    url: str = DEFAULT_URL,
    streaming: bool = True,
    security: bool = False,
    input_modes: tuple[str, ...] = ("text/plain",),
    binding: str = "JSONRPC",
    skills: bool = True,
    description: str = "A stand-in agent for Freyja's tests.",
    skill_count: int | None = None,
    tag_count: int | None = None,
    text_length: int | None = None,
) -> AgentCard:
    """The stand-in's card.

    ``skill_count`` gives it that many skills in place of the usual two, ``tag_count`` that
    many tags on each skill, and ``text_length`` makes its name, description, and version, and
    each skill's name and description, exactly that many characters long: padded when
    shorter, cut when longer. ``skills=False`` gives no skill whatever ``skill_count`` says.
    """
    for option, value in (("skill_count", skill_count), ("tag_count", tag_count)):
        if value is not None and value < 0:
            raise ValueError(f"{option} cannot be negative")
    if text_length is not None and text_length < 0:
        raise ValueError("text_length cannot be negative")

    def text(value: str) -> str:
        return value if text_length is None else value.ljust(text_length, "x")[:text_length]

    tags = ["test"] if tag_count is None else [f"tag-{n}" for n in range(tag_count)]
    card = AgentCard(
        name=text(f"Stub Agent {name}"),
        description=text(description),
        version=text("1.0.0"),
        default_input_modes=list(input_modes),
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=streaming),
        supported_interfaces=[
            AgentInterface(protocol_binding=binding, url=url, protocol_version="1.0")
        ],
        skills=(
            [
                AgentSkill(
                    id="echo",
                    name=text("Echo"),
                    description=text("Answers with what it was sent."),
                    tags=tags,
                ),
                AgentSkill(
                    id="report",
                    name=text("Report"),
                    description=text("Writes a short report."),
                    tags=tags,
                ),
                *(
                    AgentSkill(
                        id=f"skill-{n}",
                        name=text(f"Skill {n}"),
                        description=text(f"Does thing {n}."),
                        tags=tags,
                    )
                    for n in range(2, skill_count or 0)
                ),
            ][: 2 if skill_count is None else skill_count]
            if skills
            else []
        ),
    )
    if security:
        card.security_schemes["bearer"].CopyFrom(
            SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="bearer"))
        )
        card.security_requirements.append(
            SecurityRequirement(schemes={"bearer": StringList(list=[])})
        )
    return card


@dataclass
class StubAgent:
    """One stand-in agent: its ASGI app, its card, and what it received."""

    app: Any
    card: AgentCard
    record: StubRecord
    question: str = "Which date should it be?"
    released: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_events: dict[str, asyncio.Event] = field(default_factory=dict)
    # The text of every task's first working status; None sends that status with no message.
    working_text: str | None = "Working on it."
    # The task store the stand-in's server keeps, and the loop its tasks run in.
    task_store: InMemoryTaskStore | None = None
    loop: asyncio.AbstractEventLoop | None = None
    # Every report made, in order, and the event the next one sets.
    reports: list[str | None] = field(default_factory=list)
    reported: asyncio.Event = field(default_factory=asyncio.Event)
    # Each waiting ``hold`` task, by id, with how many reports it has sent and stored.
    holding: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Whether each message is written to the terminal, as when served by hand.
    echo: bool = False

    def release(self) -> None:
        """Let every ``hold`` task finish.

        From the test's own thread the event is set in the stand-in's loop, which is woken
        for it; before any task ran, or once that loop has stopped, it is set at once.
        """
        loop = self.loop
        if loop is None or not loop.is_running() or _running_loop() is loop:
            self.released.set()
        else:
            loop.call_soon_threadsafe(self.released.set)

    def report(self, text: str | None) -> None:
        """Have every waiting ``hold`` task send one working status with ``text``.

        With None the status carries no message. Each task goes on waiting for its release,
        its cancel, or the next report. Called from the test's own thread, it returns once
        every such task's status is in the stand-in's task store, so the next read of a task
        shows it; called in the stand-in's own loop, it returns at once. A report with no
        task waiting is a mistake of the test's, and raises.
        """
        with self.lock:
            self.reports.append(text)
            wanted = len(self.reports)
            waiting = list(self.holding)
        loop = self.loop
        if not waiting or loop is None:
            raise RuntimeError("no hold task is waiting for a report")
        if _running_loop() is loop:
            self._wake()
            return
        loop.call_soon_threadsafe(self._wake)
        deadline = time.monotonic() + REPORT_SECONDS
        while True:
            with self.lock:
                # A task that stopped waiting meanwhile, released or canceled, is not behind.
                behind = [task for task in waiting if self.holding.get(task, wanted) < wanted]
            if not behind:
                return
            if time.monotonic() > deadline:
                raise TimeoutError(f"the stand-in did not store the report for {behind}")
            time.sleep(0.005)

    def _wake(self) -> None:
        """In the stand-in's loop: wake every waiting task for the reports made so far."""
        reported, self.reported = self.reported, asyncio.Event()
        reported.set()

    async def task(self, task_id: str) -> Any:
        """The task as the stand-in's server keeps it, read without a request: a test that
        counts the requests the agent received can wait on it."""
        assert self.task_store is not None
        return await self.task_store.get(task_id, ServerCallContext())

    async def stored(self, task_id: str, text: str | None, call: ServerCallContext) -> None:
        """Wait until the task store holds a working status with ``text`` for the task."""
        assert self.task_store is not None
        deadline = time.monotonic() + REPORT_SECONDS
        while True:
            task = await self.task_store.get(task_id, call)
            if (
                task is not None
                and task.status.state == TaskState.TASK_STATE_WORKING
                and _status_text(task) == text
            ):
                return
            if time.monotonic() > deadline:
                raise TimeoutError(f"the stand-in did not store a report for {task_id}")
            await asyncio.sleep(0.005)


class _Handler(DefaultRequestHandler):
    """The SDK's handler, which also keeps what each cancel request carried (feature 009):
    the executor is not given a cancel's request."""

    def __init__(self, stub: StubAgent, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.stub = stub

    async def on_cancel_task(self, params: Any, context: ServerCallContext) -> Any:
        self.stub.record.cancel_hops.append(hops_of(params.metadata))
        return await super().on_cancel_task(params, context)


def make_stub(
    name: str = "stub",
    *,
    card: AgentCard | None = None,
    redirect_card_to: str | None = None,
    task_ids: Callable[[], str] | None = None,
    card_body: bytes | None = None,
    card_status: int = 200,
    card_encoding: str | None = None,
    card_chunked: bool = False,
) -> StubAgent:
    """Build a stand-in agent. Its JSON-RPC endpoint is at the path its card advertises.

    ``card_body`` is served where the card would be, with ``card_status``: a document of any
    size, a body that is no card, or nothing but a 404. ``card_encoding="gzip"`` serves the
    body, the card's own unless ``card_body`` is given, compressed and marked as such, whether
    or not the request accepts that. ``card_chunked`` sends the body in pieces with no length
    announced. None of the four goes with ``redirect_card_to``, which serves no body.
    """
    if card_encoding not in (None, "gzip"):
        raise ValueError(f"unknown card_encoding: {card_encoding}")
    replaced = (
        card_body is not None or card_status != 200 or card_encoding is not None or card_chunked
    )
    if replaced and redirect_card_to is not None:
        raise ValueError("redirect_card_to serves no body: give it no card_* option")
    card = card or stub_card(name)
    record = StubRecord()
    stub = StubAgent(app=None, card=card, record=record, task_store=InMemoryTaskStore())
    handler = _Handler(
        stub,
        agent_executor=_Executor(stub),
        task_store=stub.task_store,
        agent_card=card,
        request_context_builder=(
            SimpleRequestContextBuilder(task_id_generator=_CallableIds(task_ids))
            if task_ids is not None
            else None
        ),
    )
    rpc_path = urlsplit(card.supported_interfaces[0].url).path or "/"
    if redirect_card_to is not None:
        card_routes = [
            Route(CARD_PATH, lambda _request: RedirectResponse(redirect_card_to, status_code=307))
        ]
    elif replaced:
        body = card_body if card_body is not None else json.dumps(agent_card_to_dict(card)).encode()
        headers = {}
        if card_encoding == "gzip":
            body = gzip.compress(body)
            headers["content-encoding"] = "gzip"

        async def pieces() -> Any:
            for start in range(0, len(body), CHUNK):
                yield body[start : start + CHUNK]

        def served(_request: Any) -> Response:
            if card_chunked:
                return StreamingResponse(
                    pieces(), status_code=card_status, media_type=JSON, headers=headers
                )
            return Response(body, status_code=card_status, media_type=JSON, headers=headers)

        card_routes = [Route(CARD_PATH, served)]
    else:
        card_routes = create_agent_card_routes(card)
    stub.app = Starlette(routes=[*card_routes, *create_jsonrpc_routes(handler, rpc_path)])
    return stub


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve the stand-in A2A agent on loopback.")
    parser.add_argument("--http", type=int, required=True, metavar="PORT")
    parser.add_argument("--name", default="stub")
    parser.add_argument("--no-streaming", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.http}/"
    stub = make_stub(args.name, card=stub_card(args.name, url=url, streaming=not args.no_streaming))
    stub.echo = True
    uvicorn.run(stub.app, host="127.0.0.1", port=args.http)


if __name__ == "__main__":
    main()
