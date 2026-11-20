"""What the tests of feature 007's console runs share (tests/test_unattended_*.py)."""

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests.conftest import parse_sse
from tests.mcp_stub import StubServer, make_stub

Events = list[tuple[str, Any]]

NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)


def ls(call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": "ls", "args": {"path": "/"}}])


def hold_call(call_id: str = "h1", name: str = "hold") -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"id": call_id, "name": name, "args": {"label": "work"}}]
    )


class Gate:
    """A tool step the test holds open: the tool waits inside until ``open()``."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.opened = threading.Event()
        self.calls = 0

    def tool(self) -> Callable[..., Any]:
        async def hold(label: str) -> str:
            """Do a piece of work that takes as long as the test says."""
            self.calls += 1
            self.reached.set()
            assert await asyncio.to_thread(self.opened.wait, 10), "the held tool was never opened"
            return f"{label} finished"

        return hold

    def wait(self) -> None:
        assert self.reached.wait(5), "no run reached the held tool"

    def open(self) -> None:
        self.opened.set()


def held_server(gate: Gate, *, gated: bool) -> tuple[dict[str, Any], StubServer]:
    """A tool server whose ``hold`` tool waits on ``gate``; gated unless it is read-only."""
    stub = make_stub("work", only=[], extra={"hold": gate.tool()})
    servers = {"work": {"command": "stub", "read_only": [] if gated else ["hold"]}}
    return servers, stub


def stream(client: TestClient, path: str, body: dict[str, Any]) -> Events:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        return list(parse_sse(response.iter_lines()))


def leave(client: TestClient, path: str, body: dict[str, Any], at: str | None = None) -> None:
    """Open a run's stream and leave it: at once, or at the first line equal to ``at``."""
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        if at is None:
            return
        for line in response.iter_lines():
            if line == at:
                return


class Reader(threading.Thread):
    """Reads a run's stream to its end in a thread of its own."""

    def __init__(self, client: TestClient, path: str, body: dict[str, Any]) -> None:
        super().__init__(daemon=True)
        self.client, self.path, self.body = client, path, body
        self.events: Events = []
        self.started_reading = threading.Event()

    def run(self) -> None:
        with self.client.stream("POST", self.path, json=self.body) as response:
            assert response.status_code == 200, response.read()
            lines = []
            for line in response.iter_lines():
                self.started_reading.set()
                lines.append(line)
        self.events = list(parse_sse(lines))

    def finish(self) -> Events:
        self.join(timeout=10)
        assert not self.is_alive(), "the stream did not end"
        return self.events


def idle(client: TestClient, thread: str) -> None:
    """Wait until the thread's run has given its lease back."""
    deadline = time.monotonic() + 10
    while client.get(f"/threads/{thread}/state").json().get("run_active"):
        assert time.monotonic() < deadline, f"a run is still active on {thread}"
        time.sleep(0.02)


def row(client: TestClient, thread: str) -> dict[str, Any]:
    response = client.get(f"/threads/{thread}")
    assert response.status_code == 200, response.text
    return response.json()


def until(check: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + 5
    while not check():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


def kinds(state: dict[str, Any]) -> list[str]:
    return [message["type"] for message in state["messages"]]


def shape(events: Events) -> list[Any]:
    """What two runs of one script have in common: no ids, no run ids, no times."""
    seen: list[Any] = []
    for kind, data in events:
        if kind == "start":
            seen.append((kind, data["mode"], data["debug"]))
        elif kind == "update":
            seen.append((kind, data["node"]))
        elif kind == "message":
            message = data["message"]
            seen.append((kind, message["type"], str(message["content"])))
        elif kind == "interrupt":
            seen.append((kind, [request["name"] for request in data["action_requests"]]))
        else:
            seen.append((kind, data))
    return seen
