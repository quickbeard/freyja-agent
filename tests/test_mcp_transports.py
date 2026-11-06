"""FR-001, FR-002, FR-005, FR-012, FR-014: the two real forms of connection (research R13).

The rest of the suite connects to the stub inside the test process, which replaces exactly one
thing: the function that turns a server entry into something to connect to. These tests cover
that function, ``default_connect``. One launches the stub as a command and talks to it over
pipes; the others serve it over HTTP on the loopback interface. They use the test interpreter
and what ``uv sync`` installed: no network beyond loopback, no external server.

They also re-check, on every upgrade of the MCP library, two things the design relies on it
for: the environment a launched process receives, and that a redirect to another host is not
followed.
"""

import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from freyja_agent.api.app import create_app
from freyja_agent.config import McpServerConfig, Settings
from freyja_agent.delegates.mcp_servers import ToolServerError, default_connect, open_tool_servers
from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.runtime import Runtime
from tests.fakes import FakeClock, ScriptedChatModel
from tests.mcp_stub import STARTS_FILE_ENV, STUB_ENV, StubServer, make_stub

STUB_FILE = Path(__file__).resolve().parent / "mcp_stub.py"
GIVEN = "zz-41c7-value-given-to-the-server"
Events = list[tuple[str, Any]]


def call(tool: str, call_id: str = "c1", /, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": tool, "args": args}])


def run(client: TestClient, thread: str, sse_events: Any) -> Events:
    with client.stream("POST", f"/threads/{thread}/runs", json={"message": "go"}) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def tool_results(client: TestClient, runtime: Runtime, thread: str) -> list[ToolMessage]:
    async def transcript() -> list[Any]:
        snapshot = await runtime.agent.aget_state({"configurable": {"thread_id": thread}})
        return snapshot.values["messages"]

    return [m for m in client.portal.call(transcript) if isinstance(m, ToolMessage)]


def status(client: TestClient, name: str) -> dict[str, Any]:
    return next(s for s in client.get("/tools").json()["servers"] if s["name"] == name)


@pytest.fixture
def make_real_app(
    workspace: Path, clock: FakeClock
) -> Callable[..., tuple[FastAPI, list[Runtime]]]:
    """An app assembled the production way, connecting with the production ``connect``."""

    def _make(
        model: BaseChatModel, servers: dict[str, dict[str, Any]], **overrides: Any
    ) -> tuple[FastAPI, list[Runtime]]:
        settings = Settings(workspace_dir=str(workspace), mcp_servers=servers, **overrides)
        runtimes: list[Runtime] = []

        @asynccontextmanager
        async def open_runtime(_settings: Settings) -> AsyncIterator[Runtime]:
            async with Runtime.assemble(
                settings,
                checkpointer=InMemorySaver(),
                store=InMemoryStore(),
                locks=InMemoryRunLocks(settings.run_lock_ttl_seconds, clock=clock.now),
                model=model,
                instance_id="instance-a",
            ) as runtime:
                runtimes.append(runtime)
                yield runtime

        return create_app(open_runtime=open_runtime), runtimes

    return _make


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until_gone(pid: int, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


# A launched server, over pipes.


def launched(*flags: str, read_only: list[str], pass_env: list[str]) -> dict[str, dict[str, Any]]:
    return {
        "stub": {
            "command": sys.executable,
            "args": [str(STUB_FILE), *flags],
            "pass_env": pass_env,
            "read_only": read_only,
        }
    }


def test_a_launched_server_gets_only_its_own_variables_and_its_output_is_captured(
    make_real_app: Callable[..., tuple[FastAPI, list[Runtime]]],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(STUB_ENV, GIVEN)
    monkeypatch.setenv("FREYJA_NOT_FOR_THE_SERVER", "zz-private-to-the-service")
    assert os.environ["FREYJA_DATABASE_URI"]  # the service's own secret is in its environment
    servers = launched(
        "--chatty-stdout", read_only=["whoami", "env_has", "lookup"], pass_env=[STUB_ENV]
    )
    model = ScriptedChatModel(
        script=[
            call("lookup", key="alpha"),
            call("whoami", "c2"),
            call("env_has", "c3", name=STUB_ENV),
            call("env_has", "c4", name="FREYJA_NOT_FOR_THE_SERVER"),
            call("env_has", "c5", name="FREYJA_DATABASE_URI"),
            call("env_has", "c6", name="PATH"),
        ],
        final_text="ok",
    )
    app, runtimes = make_real_app(model, servers)
    with TestClient(app) as client:
        events = run(client, "p-1", sse_events)
        results = [message.content for message in tool_results(client, runtimes[0], "p-1")]
        state = client.get("/threads/p-1/state").text
        (server,) = runtimes[0].servers

    assert events[-1] == ("end", {"status": "completed"})
    assert (server.connection, server.reachable) == ("command", True)
    # The process saw the variable named for it and the basic ones, and nothing else of the
    # service's environment. Its token came back redacted, which also shows it had the value.
    assert results == ["value-of-alpha", "token is ***", "set", "unset", "unset", "set"]
    log = "\n".join(f"{r.name}: {r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    servers_log = [
        r.getMessage() for r in caplog.records if r.name == "freyja_agent.delegates.mcp_servers"
    ]
    # What the server wrote to its error output is in the log, under its name, redacted.
    assert "Tool server stub: stub: started with token ***" in servers_log
    assert "Tool server stub: stub: whoami called with token ***" in servers_log
    # The line it wrote to its standard output is not a protocol message. The MCP library
    # logs that, with a traceback that would quote the line; the record is kept and the
    # quote is not.
    library = [r for r in caplog.records if r.name.startswith("mcp.")]
    assert any("Failed to parse JSONRPC message" in r.getMessage() for r in library)
    assert all(r.exc_info is None for r in library)
    assert "my token is" not in log
    for place, text in (("log", log), ("state", state), ("stream", str(events))):
        assert GIVEN not in text, place
        # Not in part either: a shortened quote would show the end of the value.
        assert GIVEN[-16:] not in text, place
    # Nothing the server printed reached the service's own error output.
    captured = capfd.readouterr()
    assert "stub:" not in captured.err and GIVEN not in captured.err
    assert GIVEN not in captured.out


def test_a_launched_server_that_dies_is_unreachable_and_is_not_launched_again(
    make_real_app: Callable[..., tuple[FastAPI, list[Runtime]]],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    starts = tmp_path / "starts"
    monkeypatch.setenv(STARTS_FILE_ENV, str(starts))
    servers = launched(read_only=["pid", "lookup"], pass_env=[STARTS_FILE_ENV])
    model = ScriptedChatModel(script=[call("pid")], final_text="ok")
    app, runtimes = make_real_app(model, servers)
    with TestClient(app) as client:
        run(client, "p-2", sse_events)
        (answer,) = tool_results(client, runtimes[0], "p-2")
        pid = int(answer.content)
        assert alive(pid) and pid != os.getpid()

        os.kill(pid, signal.SIGKILL)
        assert wait_until_gone(pid)
        for attempt in ("p-3", "p-4"):
            model.script.append(call("lookup", f"c-{attempt}", key="alpha"))
            events = run(client, attempt, sse_events)
            (result,) = tool_results(client, runtimes[0], attempt)
            # An error result, and the run still completes.
            assert events[-1] == ("end", {"status": "completed"})
            assert result.status == "error" and "could not be reached" in result.content
        down = status(client, "stub")
        assert down["reachable"] is False and down["error"]
        # The catalog still lists the server's tools.
        assert down["tool_count"] > 0

    # The service started the process once and never again.
    assert starts.read_text().split() == [str(pid)]


def test_stopping_the_service_stops_a_launched_server(
    make_real_app: Callable[..., tuple[FastAPI, list[Runtime]]], sse_events: Any
) -> None:
    servers = launched(read_only=["pid"], pass_env=[])
    app, runtimes = make_real_app(ScriptedChatModel(script=[call("pid")], final_text="ok"), servers)
    with TestClient(app) as client:
        run(client, "p-5", sse_events)
        (answer,) = tool_results(client, runtimes[0], "p-5")
        pid = int(answer.content)
        assert alive(pid)
    assert wait_until_gone(pid)


# A server at an address, over HTTP on the loopback interface.


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Served:
    """An ASGI app served on 127.0.0.1 from a thread, until ``stop`` is called."""

    def __init__(self, app: Any, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError(f"nothing is listening on port {port}")
            time.sleep(0.02)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def serve() -> Iterator[Callable[[Any, int], Served]]:
    running: list[Served] = []

    def _serve(app: Any, port: int) -> Served:
        running.append(Served(app, port))
        return running[-1]

    yield _serve
    for served in running:
        served.stop()


def accepts_connections(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def test_a_server_at_an_address_is_connected_per_call_and_recovers(
    make_real_app: Callable[..., tuple[FastAPI, list[Runtime]]],
    sse_events: Any,
    serve: Callable[[Any, int], Served],
) -> None:
    port = free_port()
    first: StubServer = make_stub("web")
    running = serve(first.streamable_http_app(), port)
    servers = {"web": {"url": f"http://127.0.0.1:{port}/mcp", "read_only": ["lookup"]}}
    model = ScriptedChatModel(script=[call("lookup", key="alpha")], final_text="ok")
    app, runtimes = make_real_app(model, servers)
    with TestClient(app) as client:
        assert status(client, "web")["connection"] == "address"
        run(client, "h-1", sse_events)
        (result,) = tool_results(client, runtimes[0], "h-1")
        assert result.content == "value-of-alpha"
        assert first.calls == [("lookup", {"key": "alpha"})]

        # The operator's server stops.
        running.stop()
        model.script.append(call("lookup", "c2", key="beta"))
        events = run(client, "h-2", sse_events)
        (result,) = tool_results(client, runtimes[0], "h-2")
        assert events[-1] == ("end", {"status": "completed"})
        assert result.status == "error" and "could not be reached" in result.content
        down = status(client, "web")
        assert down["reachable"] is False and down["error"]
        # Why it failed is shown; where the server is, is not (FR-014).
        catalog = client.get("/tools").text
        for hidden in (f"127.0.0.1:{port}", str(port), "/mcp"):
            assert hidden not in catalog, hidden

        # It comes back on the same port; the service is not restarted.
        second = make_stub("web")
        serve(second.streamable_http_app(), port)
        model.script.append(call("lookup", "c3", key="gamma"))
        run(client, "h-3", sse_events)
        (result,) = tool_results(client, runtimes[0], "h-3")
        assert result.status == "success" and result.content == "value-of-gamma"
        assert second.calls == [("lookup", {"key": "gamma"})]
        up = status(client, "web")
        assert up["reachable"] is True and up["error"] is None

    # The service did not launch this server, so stopping the service leaves it running.
    assert accepts_connections(port)


async def test_a_redirect_to_another_host_is_not_followed(
    serve: Callable[[Any, int], Served],
) -> None:
    redirected: list[str] = []
    followed: list[str] = []
    elsewhere = free_port()

    async def redirect(request: Request) -> Response:
        redirected.append(request.method)
        return RedirectResponse(f"http://127.0.0.1:{elsewhere}/mcp", status_code=307)

    async def record(request: Request) -> Response:
        followed.append(f"{request.method} {request.url.path}")
        return JSONResponse({})

    methods = ["GET", "POST", "DELETE"]
    local = free_port()
    serve(Starlette(routes=[Route("/mcp", redirect, methods=methods)]), local)
    # Another origin: a different port on this machine stands in for another host.
    serve(Starlette(routes=[Route("/{path:path}", record, methods=methods)]), elsewhere)

    configs = {"web": McpServerConfig(url=f"http://127.0.0.1:{local}/mcp")}
    with pytest.raises(ToolServerError, match="Tool server 'web' could not be reached"):
        async with open_tool_servers(
            configs, connect=default_connect, call_timeout=5, startup_timeout=5
        ):
            pytest.fail("a server that only redirects must not be usable")

    # The service reached the configured address, was sent elsewhere, and did not go.
    assert redirected
    assert followed == []
