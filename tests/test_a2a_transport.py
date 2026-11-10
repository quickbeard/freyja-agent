"""003 research R16: agents served by uvicorn on loopback, reached through the production transport.

FR-002, FR-009. The only tests in the suite that open sockets, and only on 127.0.0.1: each
stand-in runs in a uvicorn server on a free port, in a thread of its own, and Freyja reaches it
through ``default_transport`` as it would reach a real agent.
"""

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import uvicorn
from a2a.types import GetTaskRequest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from freyja_agent.delegates.a2a_agents import state_name
from tests.a2a_stub import make_stub
from tests.conftest import A2aApp
from tests.fakes import ScriptedChatModel
from tests.test_a2a_handoff import DONE, handoff, progress, run, tool_results


class Listener:
    """One ASGI app served by uvicorn on 127.0.0.1, in a thread with its own event loop."""

    def __init__(self, app: Any, port: int = 0) -> None:
        self.app = app
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "Listener":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(self.app, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 5
        while not self._server.started:
            assert time.monotonic() < deadline, "the listener did not start"
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=5)
        self._server = self._thread = None


@contextmanager
def serving(app: Any, port: int = 0) -> Iterator[Listener]:
    listener = Listener(app, port).start()
    try:
        yield listener
    finally:
        listener.stop()


def over_loopback(
    make_a2a_app: Callable[..., A2aApp],
    url: str,
    script: list[AIMessage],
    **options: Any,
) -> A2aApp:
    return make_a2a_app(
        ScriptedChatModel(script=script, final_text="Done."),
        {"hello": {"url": url, "read_only": True}},
        network=True,
        **options,
    )


def test_a_hand_off_over_loopback_streams_progress_before_the_result(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    with serving(make_stub("hello").app) as agent:
        made = over_loopback(make_a2a_app, agent.url, [handoff("progress 2")])
        with TestClient(made.app) as client:
            events = run(client, "net-1", sse_events)

    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"].startswith("Agent 'hello' completed task ")
    texts = [update["text"] for update in progress(events)]
    assert texts[:3] == ["Working on it.", "Step 1", "Step 2"]
    nodes = [data["node"] for kind, data in events if kind == "update"]
    assert nodes.index("tools") > max(i for i, node in enumerate(nodes) if node == "agent")


def test_a_slow_task_becomes_pending_and_a_tick_delivers_it(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    with serving(make_stub("hello").app) as agent:
        made = over_loopback(
            make_a2a_app, agent.url, [handoff("slow 1: report")], a2a_call_timeout_seconds=0.3
        )
        with TestClient(made.app) as client:
            events = run(client, "net-2", sse_events)
            (task,) = client.get("/threads/net-2/state").json()["agent_tasks"]
            (link,) = made.runtime.agents
            assert link.client is not None
            deadline = time.monotonic() + 5
            while (
                state_name(
                    client.portal.call(
                        link.client.get_task, GetTaskRequest(id=task["task_id"])
                    ).status.state
                )
                != "completed"
            ):
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert made.runtime.worker is not None
            client.portal.call(made.runtime.worker.tick)
            after = client.get("/threads/net-2/state").json()

    (result,) = tool_results(events, "handoff_to_hello")
    assert "it is pending" in result["content"]
    assert task["status"] == "working"
    delivered = [m for m in after["messages"] if m["name"] == "check_agent_tasks"]
    assert len(delivered) == 1
    assert delivered[0]["content"].startswith(f"Agent 'hello' completed task {task['task_id']}:")
    assert after["agent_tasks"][0]["outcome_delivered"] is True


def test_a_redirected_card_fails_startup_and_the_target_receives_nothing(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    received: list[str] = []

    async def anything(request: Request) -> PlainTextResponse:
        received.append(request.url.path)
        return PlainTextResponse("{}")

    elsewhere = Starlette(routes=[Route("/{path:path}", anything, methods=["GET", "POST"])])
    with serving(elsewhere) as target:
        redirecting = make_stub(
            "hello", redirect_card_to=f"{target.url}/.well-known/agent-card.json"
        )
        with serving(redirecting.app) as agent:
            made = over_loopback(make_a2a_app, agent.url, [])
            with pytest.raises(Exception, match="Agent 'hello'"), TestClient(made.app):
                pass
    assert received == []


def test_an_agent_stopped_and_started_again_is_usable_without_a_restart(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    script = [
        handoff("first", call_id="h1"),
        DONE,
        handoff("second", call_id="h2"),
        DONE,
        handoff("third", call_id="h3"),
    ]
    first = make_stub("hello")
    agent = Listener(first.app).start()
    try:
        made = over_loopback(make_a2a_app, agent.url, script)
        with TestClient(made.app) as client:
            ok = run(client, "net-4", sse_events)
            agent.stop()
            failed = run(client, "net-4", sse_events)
            down = client.get("/tools").json()["agents"][0]
            again = make_stub("hello")
            agent = Listener(again.app, agent.port).start()
            recovered = run(client, "net-4", sse_events)
            up = client.get("/tools").json()["agents"][0]
    finally:
        agent.stop()

    assert tool_results(ok, "handoff_to_hello")[0]["content"].startswith("Agent 'hello' completed")
    (error,) = tool_results(failed, "handoff_to_hello")
    assert error["content"].startswith("Agent 'hello' could not be reached:")
    assert "127.0.0.1" not in error["content"]
    assert down["reachable"] is False
    (result,) = tool_results(recovered, "handoff_to_hello")
    assert result["content"].startswith("Agent 'hello' completed task ")
    assert up["reachable"] is True
    assert [m.text for m in first.record.messages] == ["first"]
    assert [m.text for m in again.record.messages] == ["third"]
