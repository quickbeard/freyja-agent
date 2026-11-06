"""FR-010, FR-012, FR-013, FR-018, SC-002: calling a tool server's tools in a run.

A tool the operator listed as read-only runs like one of Freyja's own file tools: the call and
its result stream, nothing pauses, and a tool error is a result the run continues from. Every
case runs against the stub server inside the test process.
"""

import asyncio
import base64
import json
import logging
import os
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from freyja_agent.delegates.mcp_servers import ToolServerError
from freyja_agent.delegates.mcp_tools import result_text
from freyja_agent.runtime.runtime import Runtime
from tests.conftest import McpApp
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import STUB_ENV, make_stub

SERVERS_LOGGER = "freyja_agent.delegates.mcp_servers"
Events = list[tuple[str, Any]]

# Everything a test calls here is listed as read-only, so nothing pauses.
READ_ONLY = ["lookup", "fail", "picture", "sound", "attachment", "stats"]
STUB = {"stub": {"command": "stub-command", "read_only": READ_ONLY}}

TASK = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {
                "description": "Look up the key alpha and report its value.",
                "subagent_type": "general-purpose",
            },
        }
    ],
)


def call(name: str, call_id: str = "c1", **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def run(client: TestClient, thread: str, message: str, sse_events: Any) -> Events:
    with client.stream("POST", f"/threads/{thread}/runs", json={"message": message}) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def kinds(events: Events) -> list[str]:
    return [kind for kind, _ in events]


def tool_updates(events: Events) -> list[dict[str, Any]]:
    """The tool result messages the stream reported, in order."""
    return [
        message
        for kind, data in events
        if kind == "update" and data["node"] == "tools"
        for message in data["data"]["messages"]
    ]


async def transcript(runtime: Runtime, thread: str) -> list[BaseMessage]:
    snapshot = await runtime.agent.aget_state({"configurable": {"thread_id": thread}})
    return snapshot.values["messages"]


def tool_results(client: TestClient, runtime: Runtime, thread: str) -> list[ToolMessage]:
    messages = client.portal.call(transcript, runtime, thread)
    return [message for message in messages if isinstance(message, ToolMessage)]


def test_a_read_only_tool_runs_without_a_pause(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[call("lookup", key="alpha")], final_text="It is value-of-alpha."
    )
    made = make_mcp_app(model, STUB)
    with TestClient(made.app) as client:
        events = run(client, "c-1", "what is under alpha?", sse_events)
        (result,) = tool_results(client, made.runtime, "c-1")
        (server,) = made.runtime.servers

    assert "interrupt" not in kinds(events)
    assert events[-1] == ("end", {"status": "completed"})
    proposed = [
        message["tool_calls"]
        for kind, data in events
        if kind == "update" and data["node"] == "model"
        for message in data["data"]["messages"]
        if message["tool_calls"]
    ]
    assert proposed == [[{"id": "c1", "name": "lookup", "args": {"key": "alpha"}}]]
    (reported,) = tool_updates(events)
    assert (reported["type"], reported["name"], reported["tool_call_id"]) == (
        "tool",
        "lookup",
        "c1",
    )
    assert reported["content"] == "value-of-alpha"
    assert result.status == "success"
    # The reply was written after the model saw the result.
    assert any(isinstance(m, ToolMessage) and m.content == "value-of-alpha" for m in model.seen[-1])
    assert made.stubs["stub"].calls == [("lookup", {"key": "alpha"})]
    assert server.reachable is True and server.error is None


def test_a_read_only_tool_runs_through_the_subagent(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    # Parent and subagent share the model: call 1 delegates, call 2 (inside the subagent)
    # calls the tool, call 3 is the subagent's report, call 4 the parent's reply.
    model = ScriptedChatModel(script=[TASK, call("lookup", "s1", key="alpha")], final_text="Done.")
    made = make_mcp_app(model, STUB)
    with TestClient(made.app) as client:
        events = run(client, "c-2", "delegate the lookup", sse_events)

    assert "interrupt" not in kinds(events)
    assert events[-1] == ("end", {"status": "completed"})
    assert made.stubs["stub"].calls == [("lookup", {"key": "alpha"})]
    # The subagent's next model call saw the tool's result.
    assert any(isinstance(m, ToolMessage) and m.content == "value-of-alpha" for m in model.seen[2])
    assert model.calls == 4


def test_a_tool_error_is_a_result_and_the_run_continues(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=SERVERS_LOGGER)
    model = ScriptedChatModel(
        script=[call("fail", reason="disk-is-full-zz")], final_text="The lookup failed."
    )
    made = make_mcp_app(model, STUB)
    with TestClient(made.app) as client:
        events = run(client, "c-3", "try it", sse_events)
        (result,) = tool_results(client, made.runtime, "c-3")
        active = client.portal.call(made.runtime.locks.is_active, "c-3")
        (server,) = made.runtime.servers
        # The thread is usable afterwards.
        again = run(client, "c-3", "thanks", sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert "interrupt" not in kinds(events) and "error" not in kinds(events)
    assert result.status == "error"
    assert "stub failure: disk-is-full-zz" in result.content
    (reported,) = tool_updates(events)
    assert "stub failure: disk-is-full-zz" in reported["content"]
    # The model was given the error and wrote the reply after it.
    assert any(isinstance(m, ToolMessage) and m.status == "error" for m in model.seen[1])
    assert active is False
    assert again[-1] == ("end", {"status": "completed"})
    # The server answered, so it is still reachable.
    assert server.reachable is True and server.error is None
    warnings = [r.getMessage() for r in caplog.records if r.name == SERVERS_LOGGER]
    assert len(warnings) == 1
    assert "stub" in warnings[0] and "fail" in warnings[0] and "tool_error" in warnings[0]
    # A log line names the server and the tool, never the arguments.
    assert "disk-is-full-zz" not in warnings[0]
    assert made.stubs["stub"].calls == [("fail", {"reason": "disk-is-full-zz"})]


def test_a_credential_in_a_result_or_a_tool_error_is_redacted(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    given = "zz-9f3e-value-given-to-the-server"
    monkeypatch.setenv(STUB_ENV, given)
    servers = {
        "stub": {
            "command": "stub-command",
            "pass_env": [STUB_ENV],
            "read_only": ["whoami", "fail"],
        }
    }
    # The stub prints its token in a result, and repeats whatever reason it is given in an error.
    model = ScriptedChatModel(
        script=[call("whoami"), call("fail", "c2", reason=f"refused for {given}")],
        final_text="ok",
    )
    made = make_mcp_app(model, servers)
    with TestClient(made.app) as client:
        events = run(client, "c-7", "who are you?", sse_events)
        success, error = tool_results(client, made.runtime, "c-7")
        state = client.get("/threads/c-7/state").text

    assert events[-1] == ("end", {"status": "completed"})
    assert success.content == "token is ***"
    assert error.status == "error" and "refused for ***" in error.content
    # Neither the stream nor the thread holds the value the server was given. The argument the
    # model wrote is the model's own text and is not a server's output.
    assert given not in json.dumps(tool_updates(events))
    assert all(given not in str(message.content) for message in (success, error))
    assert state.count(given) == 1  # only in the model's own tool call


# Results (FR-018, research R6): text and structured data reach the orchestrator; every other
# part becomes a note naming its kind.


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        pytest.param("picture", "a picture of one pixel\n\n[image omitted: image/png]", id="image"),
        pytest.param("sound", "[audio omitted: audio/wav]", id="audio-only"),
        pytest.param("attachment", "[file omitted: application/octet-stream]", id="file-only"),
    ],
)
def test_a_non_text_part_becomes_a_note_naming_its_kind(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, tool: str, expected: str
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[call(tool)], final_text="ok"), STUB)
    with TestClient(made.app) as client:
        events = run(client, "c-4", "go", sse_events)
        (result,) = tool_results(client, made.runtime, "c-4")
        state = client.get("/threads/c-4/state").text

    assert events[-1] == ("end", {"status": "completed"})
    # A result with such parts is a normal result, not an error.
    assert result.status == "success"
    assert result.content == expected
    assert tool_updates(events)[0]["content"] == expected
    # Nothing binary is stored in the thread or sent on the stream.
    for payload in (b"\x89PNG", b"RIFF", b"\x00\x01\x02stub-attachment"):
        encoded = base64.b64encode(payload).decode().rstrip("=")
        assert encoded not in state
        assert encoded not in json.dumps(events)


def test_structured_data_is_delivered_once(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[call("stats")], final_text="ok"), STUB)
    with TestClient(made.app) as client:
        run(client, "c-5", "go", sse_events)
        (result,) = tool_results(client, made.runtime, "c-5")
    # The server sends the same data as a text block and as structured content.
    assert json.loads(result.content) == {"calls": 1, "notes": 0}
    assert result.content.count('"calls"') == 1


def test_structured_data_without_a_text_block_is_delivered_as_json(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    def raw_stats() -> CallToolResult:
        return CallToolResult(content=[], structured_content={"count": 3, "label": "three"})

    stub = make_stub(extra={"raw_stats": raw_stats})
    servers = {"stub": {"command": "stub-command", "read_only": ["raw_stats"]}}
    made = make_mcp_app(
        ScriptedChatModel(script=[call("raw_stats")], final_text="ok"),
        servers,
        stubs={"stub": stub},
    )
    with TestClient(made.app) as client:
        run(client, "c-6", "go", sse_events)
        (result,) = tool_results(client, made.runtime, "c-6")
    assert result.status == "success"
    assert json.loads(result.content) == {"count": 3, "label": "three"}


def test_result_blocks_are_converted_in_order() -> None:
    result = CallToolResult(
        content=[
            TextContent(type="text", text="first"),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(uri="file:///notes.txt", text="embedded text"),
            ),
            ImageContent(type="image", data="AAAA", mime_type="image/jpeg"),
            AudioContent(type="audio", data="AAAA", mime_type="audio/mpeg"),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri="file:///a.pdf", blob="AAAA", mime_type="application/pdf"
                ),
            ),
            ResourceLink(type="resource_link", name="report", uri="https://example.invalid/r/1"),
            TextContent(type="text", text="last"),
        ],
        structured_content={"ignored": "because there is text"},
    )
    assert result_text(result) == "\n\n".join(
        [
            "first",
            "embedded text",
            "[image omitted: image/jpeg]",
            "[audio omitted: audio/mpeg]",
            "[file omitted: application/pdf]",
            "[resource link omitted: https://example.invalid/r/1]",
            "last",
        ]
    )


def test_an_unknown_block_and_a_missing_mime_type_still_get_a_note() -> None:
    odd = SimpleNamespace(type="hologram")
    bare = SimpleNamespace(type="image", mime_type=None)
    mixed = SimpleNamespace(content=[odd, bare], structured_content=None)
    empty = SimpleNamespace(content=[], structured_content=None)
    assert result_text(mixed) == "[hologram content omitted]\n\n[image omitted: unknown type]"
    assert result_text(empty) == ""


# FR-013: calls from concurrent runs to the same server each receive their own result.


class EchoingModel(ScriptedChatModel):
    """Asks for the key named in the human message, then repeats the tool's answer."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        last = messages[-1]
        if isinstance(last, HumanMessage):
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": f"c-{last.content}",
                        "name": "slow_lookup",
                        "args": {"key": last.content},
                    }
                ],
            )
        else:
            reply = AIMessage(content=str(last.content))
        return ChatResult(generations=[ChatGeneration(message=reply)])


def test_concurrent_calls_each_receive_their_own_result(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    active = 0
    most_at_once = 0

    async def slow_lookup(key: str) -> str:
        nonlocal active, most_at_once
        active += 1
        most_at_once = max(most_at_once, active)
        await asyncio.sleep(0.1)
        active -= 1
        return f"value-of-{key}"

    stub = make_stub(extra={"slow_lookup": slow_lookup})
    servers = {"stub": {"command": "stub-command", "read_only": ["slow_lookup"]}}
    made = make_mcp_app(EchoingModel(), servers, stubs={"stub": stub})
    keys = [f"key{i}" for i in range(10)]
    replies: dict[str, str] = {}

    with TestClient(made.app) as client:

        def one_run(key: str) -> None:
            events = run(client, f"cc-{key}", key, sse_events)
            assert events[-1] == ("end", {"status": "completed"})
            replies[key] = next(
                data["message"]["content"]
                for kind, data in reversed(events)
                if kind == "message" and data["message"]["content"]
            )

        workers = [threading.Thread(target=one_run, args=(key,)) for key in keys]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)

    assert replies == {key: f"value-of-{key}" for key in keys}
    # The calls overlapped on the one connection; none waited for another to finish.
    assert most_at_once > 1


# Failures during a run (User Story 4, FR-012, SC-005): a slow or absent server is an error
# result, a warning, and a status in the catalog; nothing is tried twice.

ADDRESS = {"stub": {"url": "http://127.0.0.1:8765/mcp", "read_only": ["lookup", "slow"]}}


def server_status(client: TestClient, name: str = "stub") -> dict[str, Any]:
    catalog = client.get("/tools").json()
    return next(server for server in catalog["servers"] if server["name"] == name)


def warnings_of(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == SERVERS_LOGGER and record.levelno == logging.WARNING
    ]


def test_a_call_that_exceeds_the_time_limit_is_an_error_result_and_is_not_repeated(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=SERVERS_LOGGER)
    model = ScriptedChatModel(script=[call("slow", seconds=3)], final_text="It took too long.")
    servers = {"stub": {"command": "stub-command", "read_only": ["lookup", "slow"]}}
    made = make_mcp_app(model, servers, mcp_call_timeout_seconds=0.2)
    with TestClient(made.app) as client:
        before = server_status(client)
        events = run(client, "t-1", "wait for it", sse_events)
        (result,) = tool_results(client, made.runtime, "t-1")
        active = client.portal.call(made.runtime.locks.is_active, "t-1")
        after = server_status(client)
        tool_count = len(client.get("/tools").json()["tools"])

        # The thread is usable, and the server answers the next call on the same connection.
        model.script.append(call("lookup", "c2", key="alpha"))
        again = run(client, "t-1", "now look up alpha", sse_events)
        recovered = server_status(client)

    assert events[-1] == ("end", {"status": "completed"})
    assert "interrupt" not in kinds(events) and "error" not in kinds(events)
    assert result.status == "error"
    assert "'slow'" in result.content and "'stub'" in result.content
    assert "0.2 seconds" in result.content
    assert "may or may not have happened" in result.content
    assert active is False
    # One attempt reached the server, and nothing tried again.
    assert made.stubs["stub"].called("slow") == [{"seconds": 3}]
    (warning,) = warnings_of(caplog)
    assert "stub" in warning and "slow" in warning and "timeout" in warning
    # The catalog shows the outcome of that attempt, and still lists the server's tools.
    assert before["reachable"] is True and before["error"] is None
    assert after["reachable"] is False
    assert after["error"] and len(after["error"]) <= 500 and "0.2 seconds" in after["error"]
    assert after["checked_at"] > before["checked_at"]
    assert after["tool_count"] == before["tool_count"] and tool_count > after["tool_count"]
    assert again[-1] == ("end", {"status": "completed"})
    assert made.stubs["stub"].called("lookup") == [{"key": "alpha"}]
    assert recovered["reachable"] is True and recovered["error"] is None


def test_an_unreachable_server_is_an_error_result_and_recovers_on_the_next_call(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=SERVERS_LOGGER)
    model = ScriptedChatModel(script=[call("lookup", key="alpha")], final_text="Done.")
    made = make_mcp_app(model, ADDRESS)
    with TestClient(made.app) as client:
        before = server_status(client)
        # The operator's server goes away after startup.
        made.links["stub"].down = True
        events = run(client, "u-1", "look up alpha", sse_events)
        (result,) = tool_results(client, made.runtime, "u-1")
        active = client.portal.call(made.runtime.locks.is_active, "u-1")
        down = server_status(client)
        tools_while_down = [t["name"] for t in client.get("/tools").json()["tools"]]

        # It is answering again; no restart of the service.
        made.links["stub"].down = False
        model.script.append(call("lookup", "c2", key="beta"))
        again = run(client, "u-2", "look up beta", sse_events)
        (second,) = tool_results(client, made.runtime, "u-2")
        up = server_status(client)

    assert events[-1] == ("end", {"status": "completed"})
    assert "error" not in kinds(events)
    assert result.status == "error"
    assert "'stub'" in result.content and "'lookup'" in result.content
    assert "could not be reached" in result.content
    assert active is False
    assert made.stubs["stub"].calls == [("lookup", {"key": "beta"})]
    (warning,) = warnings_of(caplog)
    assert "stub" in warning and "lookup" in warning and "unreachable" in warning
    assert down["reachable"] is False
    assert down["error"] == "ConnectionRefusedError: the stub server is down"
    assert down["checked_at"] > before["checked_at"]
    assert "lookup" in tools_while_down
    assert again[-1] == ("end", {"status": "completed"})
    assert second.status == "success" and second.content == "value-of-beta"
    assert up["reachable"] is True and up["error"] is None
    assert up["checked_at"] > down["checked_at"]


def test_an_approved_call_that_times_out_is_not_repeated(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[call("slow", seconds=3)], final_text="Unknown outcome.")
    servers = {"stub": {"command": "stub-command"}}  # `slow` is gated here
    made = make_mcp_app(model, servers, mcp_call_timeout_seconds=0.2)
    with TestClient(made.app) as client:
        events = run(client, "t-2", "do the slow thing", sse_events)
        assert events[-1][0] == "interrupt"
        assert made.stubs["stub"].calls == []
        proposal = events[-1][1]["interrupt_id"]
        with client.stream(
            "POST",
            "/threads/t-2/resume",
            json={"interrupt_id": proposal, "decisions": [{"type": "approve"}]},
        ) as response:
            resumed = sse_events(response.iter_lines())
        (result,) = tool_results(client, made.runtime, "t-2")
        state = client.get("/threads/t-2/state").json()

    # The approval authorized one attempt. It is reported, not retried, and nothing is pending.
    assert resumed[-1] == ("end", {"status": "completed"})
    assert result.status == "error" and "may or may not have happened" in result.content
    assert made.stubs["stub"].called("slow") == [{"seconds": 3}]
    assert state["pending"] is None and state["run_active"] is False


# Credentials (FR-002, SC-008, research R12): a value given to a server appears nowhere in the
# form it was supplied.

GIVEN = "zz-9f3e-value-given-to-the-server"


def test_a_credential_appears_nowhere_a_run_can_show_it(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(STUB_ENV, GIVEN)

    def denied() -> str:
        """Fail, quoting the token the server was started with."""
        raise ToolError(f"denied for holder of {os.environ[STUB_ENV]}")

    stub = make_stub(leak_token_in_listing=True, extra={"denied": denied})
    servers = {
        "stub": {
            "command": "stub-command",
            "pass_env": [STUB_ENV],
            "read_only": ["whoami", "denied", "token_help"],
        }
    }
    model = ScriptedChatModel(
        script=[call("whoami"), call("denied", "c2"), call("token_help", "c3", topic="all")],
        final_text="ok",
    )
    made = make_mcp_app(model, servers, stubs={"stub": stub})
    with TestClient(made.app) as client:
        events = run(client, "s-1", "who are you?", sse_events)
        results = tool_results(client, made.runtime, "s-1")
        state = client.get("/threads/s-1/state").text
        catalog = client.get("/tools").text

    assert events[-1] == ("end", {"status": "completed"})
    assert [message.content for message in results[:2]] == [
        "token is ***",
        "Error executing tool denied: denied for holder of ***",
    ]
    stream = json.dumps(events)
    log = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    for place, text in (("stream", stream), ("state", state), ("catalog", catalog), ("log", log)):
        assert GIVEN not in text, place
    # The server's own listing carried it three times: description, schema text, and a hint.
    assert catalog.count("***") >= 3
    assert "token is ***" in stream and "token is ***" in state


def test_a_credential_in_a_connection_failure_is_redacted(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(STUB_ENV, GIVEN)
    servers = {"stub": {"command": "stub-command", "pass_env": [STUB_ENV], "read_only": ["lookup"]}}
    model = ScriptedChatModel(script=[call("lookup", key="alpha")], final_text="ok")
    made = make_mcp_app(model, servers)
    with TestClient(made.app) as client:
        # A launched server's connection is held open, so make the held client fail.
        (server,) = made.runtime.servers

        async def refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError(f"handshake rejected for {GIVEN}")

        monkeypatch.setattr(server._client, "call_tool", refuse)
        events = run(client, "s-2", "look up alpha", sse_events)
        (result,) = tool_results(client, made.runtime, "s-2")
        status = server_status(client)
        state = client.get("/threads/s-2/state").text

    assert events[-1] == ("end", {"status": "completed"})
    assert result.status == "error"
    assert status["reachable"] is False
    assert status["error"] == "ConnectionError: handshake rejected for ***"
    log = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    for place, text in (("stream", json.dumps(events)), ("state", state), ("log", log)):
        assert GIVEN not in text, place
    assert "handshake rejected for ***" in log


def test_a_credential_in_a_startup_failure_is_redacted(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STUB_ENV, GIVEN)
    servers = {"stub": {"command": "stub-command", "pass_env": [STUB_ENV]}}
    made = make_mcp_app(ScriptedChatModel(), servers)
    made.links["stub"].down = True
    made.links["stub"].down_reason = f"refused the token {GIVEN}"
    with pytest.raises(ToolServerError) as refused, TestClient(made.app):
        pytest.fail("the app must not start")
    assert GIVEN not in str(refused.value)
    assert "refused the token ***" in str(refused.value)


def test_a_servers_address_is_kept_out_of_its_status_and_the_log(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    url = "http://127.0.0.1:8765/mcp?key=zz-query-value"
    servers = {"stub": {"url": url, "read_only": ["lookup"]}}
    model = ScriptedChatModel(script=[call("lookup", key="alpha")], final_text="ok")
    made = make_mcp_app(model, servers)
    with TestClient(made.app) as client:
        made.links["stub"].down = True
        made.links["stub"].down_reason = f"cannot connect to {url} (tried 127.0.0.1:8765)"
        events = run(client, "s-3", "look up alpha", sse_events)
        status = server_status(client)
        catalog = client.get("/tools").text

    assert events[-1] == ("end", {"status": "completed"})
    # The catalog shows that the server is unreachable and why, without where it is (FR-014).
    assert (
        status["error"] == "ConnectionRefusedError: cannot connect to <address> (tried <address>)"
    )
    log = "\n".join(r.getMessage() for r in caplog.records)
    for hidden in ("zz-query-value", "127.0.0.1:8765", "/mcp"):
        assert hidden not in catalog, hidden
        assert hidden not in json.dumps(events), hidden
    assert "zz-query-value" not in log
