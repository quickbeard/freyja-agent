"""FR-010, FR-012, FR-013, FR-018, SC-002: calling a tool server's tools in a run.

A tool the operator listed as read-only runs like one of Freyja's own file tools: the call and
its result stream, nothing pauses, and a tool error is a result the run continues from. Every
case runs against the stub server inside the test process.
"""

import asyncio
import base64
import json
import logging
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
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
