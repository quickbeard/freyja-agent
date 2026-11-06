"""FR-003, FR-004, FR-005, SC-004: tool servers are connected at startup or startup fails.

Every case runs against the stub server inside the test process. A failure is raised before
the app serves anything, names the server, and leaves no connection open.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.agent.catalog import Classification
from freyja_agent.config import McpServerConfig
from freyja_agent.delegates.mcp_servers import ToolServer, ToolServerError, open_tool_servers
from tests.conftest import McpApp, StubLink
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import STANDARD_TOOLS, STUB_ENV, StubServer, make_stub

SERVERS_LOGGER = "freyja_agent.delegates.mcp_servers"
COMMAND = {"command": "stub-command", "read_only": ["lookup"]}
ADDRESS = {"url": "http://127.0.0.1:8765/mcp"}


def owner_tasks() -> list[str]:
    """The names of the tasks that hold a launched server's connection open."""
    return sorted(
        task.get_name() for task in asyncio.all_tasks() if task.get_name().startswith("tool-")
    )


async def open_with(
    entries: dict[str, dict[str, Any]],
    links: dict[str, StubLink],
    startup_timeout: float = 5,
) -> tuple[ToolServer, ...]:
    """Open the servers, return the links, and close them again."""
    configs = {name: McpServerConfig(**entry) for name, entry in entries.items()}
    async with open_tool_servers(
        configs,
        connect=lambda name, _config, _on_error_output: links[name].target(),
        call_timeout=5,
        startup_timeout=startup_timeout,
    ) as servers:
        assert all(server.reachable and server.error is None for server in servers)
        return servers


def stub_links(**stubs: StubServer) -> dict[str, StubLink]:
    return {name: StubLink(stub) for name, stub in stubs.items()}


def test_tools_are_loaded_at_startup_and_offered_to_the_model(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=SERVERS_LOGGER)
    model = ScriptedChatModel(final_text="hello")
    made = make_mcp_app(model, {"stub": COMMAND})
    with (
        TestClient(made.app) as client,
        client.stream("POST", "/threads/s-1/runs", json={"message": "hi"}) as response,
    ):
        events = sse_events(response.iter_lines())
        runtime = made.runtime

    assert events[-1] == ("end", {"status": "completed"})
    assert set(STANDARD_TOOLS) <= set(model.bound_tool_names)
    (server,) = runtime.servers
    assert (server.name, server.connection) == ("stub", "command")
    assert [tool.name for tool in server.tools] == list(STANDARD_TOOLS)
    assert {tool.name: tool.classification for tool in server.tools}["lookup"] == (
        Classification.READ_ONLY
    )
    assert all(
        tool.classification is Classification.GATED
        for tool in server.tools
        if tool.name != "lookup"
    )
    lines = [r.getMessage() for r in caplog.records if r.name == SERVERS_LOGGER]
    assert f"Tool server stub (command): {len(STANDARD_TOOLS)} tools, 1 read-only" in lines
    # Listing is all startup does: no tool was called.
    assert made.stubs["stub"].calls == []


def test_a_gated_server_tool_pauses_before_it_is_called(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    """The gate exists from the first phase: no later phase may be what makes a call safe."""
    proposal = AIMessage(
        content="", tool_calls=[{"id": "c1", "name": "record_note", "args": {"note": "x"}}]
    )
    made = make_mcp_app(ScriptedChatModel(script=[proposal]), {"stub": COMMAND})
    with (
        TestClient(made.app) as client,
        client.stream("POST", "/threads/s-2/runs", json={"message": "note it"}) as response,
    ):
        events = sse_events(response.iter_lines())
    kind, pending = events[-1]
    assert kind == "interrupt"
    assert pending["action_requests"][0]["name"] == "record_note"
    assert made.stubs["stub"].calls == []
    assert made.stubs["stub"].notes == []


async def test_both_forms_of_connection_are_accepted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=SERVERS_LOGGER)
    links = stub_links(launched=make_stub("launched", only=["lookup"]), remote=make_stub("remote"))
    links["remote"].stub.remove_tool("lookup")
    servers = await open_with({"launched": COMMAND, "remote": ADDRESS}, links)
    assert [(s.name, s.connection) for s in servers] == [
        ("launched", "command"),
        ("remote", "address"),
    ]
    # A launched server keeps one connection; a server at an address was connected to list
    # its tools and is connected again for each call.
    assert links["launched"].connections == 1
    assert links["remote"].connections == 1
    lines = [r.getMessage() for r in caplog.records if r.name == SERVERS_LOGGER]
    assert "Tool server launched (command): 1 tools, 1 read-only" in lines
    assert f"Tool server remote (address): {len(STANDARD_TOOLS) - 1} tools, 0 read-only" in lines


async def test_a_server_with_no_tools_is_accepted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=SERVERS_LOGGER)
    (server,) = await open_with(
        {"quiet": {"command": "stub-command"}}, stub_links(quiet=make_stub(empty=True))
    )
    assert server.tools == ()
    lines = [r.getMessage() for r in caplog.records if r.name == SERVERS_LOGGER]
    assert "Tool server quiet (command): 0 tools, 0 read-only" in lines


@pytest.mark.parametrize("entry", [COMMAND, ADDRESS], ids=["command", "address"])
async def test_an_unreachable_server_fails_startup_and_is_named(entry: dict[str, Any]) -> None:
    links = stub_links(tracker=make_stub())
    links["tracker"].down = True
    with pytest.raises(ToolServerError) as refused:
        await open_with({"tracker": {**entry, "read_only": []}}, links)
    message = str(refused.value)
    assert "Tool server 'tracker'" in message
    assert ("could not be started" if "command" in entry else "could not be reached") in message
    assert "ConnectionRefusedError" in message
    assert owner_tasks() == []


async def test_every_unreachable_server_is_named_and_the_others_are_closed() -> None:
    links = stub_links(first=make_stub(), second=make_stub(), third=make_stub())
    links["first"].down = True
    links["third"].down = True
    with pytest.raises(ToolServerError) as refused:
        await open_with(
            {name: {"command": "stub-command"} for name in ("first", "second", "third")}, links
        )
    message = str(refused.value)
    assert message.index("'first'") < message.index("'third'")
    assert "'second'" not in message
    # The server that did open was closed again: nothing holds a connection.
    assert links["second"].connections == 1
    assert owner_tasks() == []


async def test_a_server_that_never_answers_fails_at_the_startup_limit() -> None:
    links = stub_links(silent=make_stub(), fine=make_stub("fine", only=["ping"]))
    links["silent"].hang = True
    started = time.monotonic()
    with pytest.raises(ToolServerError) as refused:
        await open_with(
            {"silent": {"command": "stub-command"}, "fine": {"command": "stub-command"}},
            links,
            startup_timeout=0.3,
        )
    assert time.monotonic() - started < 5
    assert "Tool server 'silent'" in str(refused.value)
    assert "did not answer within 0.3 seconds" in str(refused.value)
    assert owner_tasks() == []


async def test_a_read_only_name_the_server_does_not_provide_fails_startup() -> None:
    with pytest.raises(ToolServerError) as refused:
        await open_with(
            {"stub": {"command": "stub-command", "read_only": ["lookup", "lokup"]}},
            stub_links(stub=make_stub()),
        )
    assert "Tool server 'stub'" in str(refused.value)
    assert "'lokup'" in str(refused.value)
    assert "'lookup'" not in str(refused.value)
    assert owner_tasks() == []


async def test_two_servers_with_the_same_tool_name_fail_startup() -> None:
    with pytest.raises(ToolServerError) as refused:
        await open_with(
            {"one": {"command": "stub-command"}, "two": {"url": "http://localhost:9/mcp"}},
            stub_links(one=make_stub(only=["lookup"]), two=make_stub(only=["lookup", "ping"])),
        )
    message = str(refused.value)
    assert "'lookup'" in message and "'one'" in message and "'two'" in message
    assert owner_tasks() == []


async def test_a_server_tool_named_like_one_of_freyjas_own_fails_startup() -> None:
    def ls(path: str) -> str:
        return path

    with pytest.raises(ToolServerError) as refused:
        await open_with(
            {"files": {"command": "stub-command"}},
            stub_links(files=make_stub(only=[], extra={"ls": ls})),
        )
    assert "Tool server 'files'" in str(refused.value)
    assert "'ls'" in str(refused.value)
    assert "Freyja's own" in str(refused.value)


@pytest.mark.parametrize(
    "name", ["bad name", "dotted.name", "n" * 65], ids=["space", "dot", "long"]
)
async def test_a_tool_name_a_model_cannot_be_offered_fails_startup(name: str) -> None:
    def tool() -> str:
        return "x"

    with pytest.raises(ToolServerError) as refused:
        await open_with(
            {"odd": {"command": "stub-command"}},
            stub_links(odd=make_stub(only=[], extra={name: tool})),
        )
    assert "Tool server 'odd'" in str(refused.value)
    assert f"'{name}'" in str(refused.value)


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
async def test_a_pass_env_variable_that_is_unset_or_empty_fails_startup(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(STUB_ENV, raising=False)
    else:
        monkeypatch.setenv(STUB_ENV, value)
    links = stub_links(tracker=make_stub())
    with pytest.raises(ToolServerError) as refused:
        await open_with({"tracker": {"command": "stub-command", "pass_env": [STUB_ENV]}}, links)
    assert "Tool server 'tracker'" in str(refused.value)
    assert f"'{STUB_ENV}'" in str(refused.value)
    # Refused before any connection was attempted.
    assert links["tracker"].connections == 0


async def test_a_set_pass_env_variable_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STUB_ENV, "tok-123")
    (server,) = await open_with(
        {"tracker": {"command": "stub-command", "pass_env": [STUB_ENV]}},
        stub_links(tracker=make_stub()),
    )
    assert server.redact("the token is tok-123") == "the token is ***"


async def test_what_a_server_lists_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STUB_ENV, "tok-123")
    (server,) = await open_with(
        {"tracker": {"command": "stub-command", "pass_env": [STUB_ENV]}},
        stub_links(tracker=make_stub(leak_token_in_listing=True)),
    )
    leaky = next(tool for tool in server.tools if tool.name == "token_help")
    listed = (
        f"{leaky.description} {leaky.input_schema} {leaky.server_hints} {leaky.tool.description}"
    )
    assert "tok-123" not in listed
    assert listed.count("***") >= 4


async def test_leaving_the_context_closes_every_connection() -> None:
    links = stub_links(one=make_stub(only=["lookup"]), two=make_stub(only=["ping"]))
    configs = {
        "one": McpServerConfig(command="stub-command"),
        "two": McpServerConfig(command="stub-command"),
    }
    async with open_tool_servers(
        configs,
        connect=lambda name, _config, _on_error_output: links[name].target(),
        call_timeout=5,
        startup_timeout=5,
    ):
        assert owner_tasks() == ["tool-server:one", "tool-server:two"]
    assert owner_tasks() == []


def test_a_failed_startup_serves_nothing(make_mcp_app: Callable[..., McpApp]) -> None:
    made = make_mcp_app(ScriptedChatModel(), {"tracker": COMMAND})
    made.links["tracker"].down = True
    with pytest.raises(ToolServerError, match="Tool server 'tracker'"), TestClient(made.app):
        pytest.fail("the app must not start with a tool server it cannot reach")
    assert made.runtimes == []


def test_stopping_the_app_closes_the_tool_servers(make_mcp_app: Callable[..., McpApp]) -> None:
    made = make_mcp_app(ScriptedChatModel(), {"stub": COMMAND})
    with TestClient(made.app) as client:
        assert client.get("/healthz").status_code == 200
        (server,) = made.runtime.servers
        owner = server._owner
        assert owner is not None and not owner.done()
    assert owner.done()
    assert server._client is None
