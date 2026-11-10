"""A stand-in A2A agent for the suite, built with the SDK's own server classes (003 research R16).

The suite serves it inside the test process through ``tests.asgi_stream``; run as a module it
serves on the loopback interface for the transport test and the quickstart:

    uv run python -m tests.a2a_stub --http 9998

It records every message and every cancel it receives, and behaves by a keyword at the start of
the message text:

- (default) completes with the message and the conversation's context id;
- ``ask``: asks one question (input required) and completes once answered in the same task;
- ``slow <seconds>``: stays working that long, then completes;
- ``hold``: stays working until the test calls ``StubAgent.release()``;
- ``fail``, ``reject``, ``auth``: fails, rejects, or asks for authentication;
- ``parts``: completes with a text, a data, a raw-bytes, and a URL part;
- ``progress <n>``: sends n status updates before completing;
- ``message``: answers with a direct message instead of a task.

Card options cover what startup must refuse or ignore: no streaming, security requirements,
input modes without text, no JSON-RPC interface, another advertised address, no skills, and a
description that claims to be read-only. ``task_ids`` replaces the server's task id generator,
for replies with malformed ids (the SDK picks the id before the agent sees the message, so this
is an option rather than a keyword).
"""

import argparse
import asyncio
from collections.abc import Callable
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
from a2a.server.events import EventQueue
from a2a.server.id_generator import IDGenerator, IDGeneratorContext
from a2a.server.request_handlers import DefaultRequestHandler
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
from starlette.responses import RedirectResponse
from starlette.routing import Route

CARD_PATH = "/.well-known/agent-card.json"
DEFAULT_URL = "http://127.0.0.1:9999/"
ELSEWHERE = "http://elsewhere.example:1234/a2a"


@dataclass
class ReceivedMessage:
    text: str
    # Every part as (kind, text or None, metadata) in order.
    parts: list[tuple[str, str | None, dict[str, Any]]]
    context_id: str
    task_id: str
    message_id: str


@dataclass
class StubRecord:
    messages: list[ReceivedMessage] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)


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
        message = context.message
        text = get_message_text(message) if message is not None else ""
        self.stub.record.messages.append(
            ReceivedMessage(
                text=text,
                parts=_parts(message) if message is not None else [],
                context_id=context.context_id or "",
                task_id=context.task_id or "",
                message_id=message.message_id if message is not None else "",
            )
        )
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

        # An answer to an earlier question continues that task.
        if task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            await updater.add_artifact(parts=[new_text_part(f"Answered: {text}")])
            await updater.complete()
            return

        await updater.update_status(TaskState.TASK_STATE_WORKING, message=say("Working on it."))
        if keyword == "ask":
            await updater.requires_input(message=say(self.stub.question))
            return
        if keyword == "fail":
            await updater.failed(message=say("The stub failed this task on purpose."))
            return
        if keyword == "reject":
            await updater.reject(message=say("The stub does not take this task."))
            return
        if keyword == "auth":
            await updater.requires_auth(message=say("The stub needs you to sign in."))
            return
        if keyword == "progress":
            for step in range(1, int(rest.split()[0]) + 1):
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING, message=say(f"Step {step}")
                )
        if keyword in ("slow", "hold"):
            stop = self.stub.cancel_events.setdefault(task.id, asyncio.Event())
            wait = (
                asyncio.sleep(float(rest.split()[0].rstrip(":")))
                if keyword == "slow"
                else self.stub.released.wait()
            )
            waiter = asyncio.ensure_future(wait)
            stopper = asyncio.ensure_future(stop.wait())
            await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
            stopper.cancel()
            if stop.is_set():
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

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        self.stub.record.cancels.append(task_id)
        self.stub.cancel_events.setdefault(task_id, asyncio.Event()).set()
        updater = TaskUpdater(
            event_queue=event_queue, task_id=task_id, context_id=context.context_id or ""
        )
        await updater.cancel()


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
) -> AgentCard:
    card = AgentCard(
        name=f"Stub Agent {name}",
        description=description,
        version="1.0.0",
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
                    name="Echo",
                    description="Answers with what it was sent.",
                    tags=["test"],
                ),
                AgentSkill(
                    id="report", name="Report", description="Writes a short report.", tags=["test"]
                ),
            ]
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

    def release(self) -> None:
        """Let every ``hold`` task finish."""
        self.released.set()


def make_stub(
    name: str = "stub",
    *,
    card: AgentCard | None = None,
    redirect_card_to: str | None = None,
    task_ids: Callable[[], str] | None = None,
) -> StubAgent:
    """Build a stand-in agent. Its JSON-RPC endpoint is at the path its card advertises."""
    card = card or stub_card(name)
    record = StubRecord()
    stub = StubAgent(app=None, card=card, record=record)
    handler = DefaultRequestHandler(
        agent_executor=_Executor(stub),
        task_store=InMemoryTaskStore(),
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
    uvicorn.run(stub.app, host="127.0.0.1", port=args.http)


if __name__ == "__main__":
    main()
