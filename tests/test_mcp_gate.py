"""FR-006 to FR-009, FR-019, SC-001: every effectful tool server tool waits for a person.

A server tool the operator did not list as read-only pauses before the call, naming its server.
Approve, edit, and reject behave as they do for ``send_notification``; an edit is checked
against the tool's declared input; and a decision is applied under the configuration in force.
What reached a server is read from the stub's own record of the calls it received.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Interrupt

from freyja_agent.agent.catalog import Catalog, CatalogTool, Classification
from freyja_agent.api.decisions import build_resume_command
from freyja_agent.api.schemas import EditDecision
from freyja_agent.api.serialize import interrupt_to_pending
from tests.conftest import McpApp
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import StubServer, make_stub

Events = list[tuple[str, Any]]
ALL = ["approve", "edit", "reject"]

# Only `lookup` is exempt; every other tool of the stub is gated.
STUB = {"stub": {"command": "stub-command", "read_only": ["lookup"]}}
NOTE = {"note": "ship it", "priority": 2}

TASK = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {
                "description": "Record the note 'ship it' on the server.",
                "subagent_type": "general-purpose",
            },
        }
    ],
)


def propose(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    """One model step that proposes the given tool calls."""
    return AIMessage(
        content="",
        tool_calls=[
            {"id": f"c{index}", "name": name, "args": args}
            for index, (name, args) in enumerate(calls, start=1)
        ],
    )


def stream(client: TestClient, path: str, body: dict[str, Any], sse_events: Any) -> Events:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def run(client: TestClient, thread: str, sse_events: Any, message: str = "go") -> Events:
    return stream(client, f"/threads/{thread}/runs", {"message": message}, sse_events)


def pending(client: TestClient, thread: str) -> dict[str, Any]:
    state = client.get(f"/threads/{thread}/state").json()
    assert state["pending"] is not None, state
    return state["pending"]


def resume_body(client: TestClient, thread: str, decisions: list[dict[str, Any]]) -> dict:
    return {"interrupt_id": pending(client, thread)["interrupt_id"], "decisions": decisions}


def resume(
    client: TestClient, thread: str, decisions: list[dict[str, Any]], sse_events: Any
) -> Events:
    body = resume_body(client, thread, decisions)
    return stream(client, f"/threads/{thread}/resume", body, sse_events)


def refuse(client: TestClient, thread: str, decisions: list[dict[str, Any]]) -> list[dict]:
    """Submit decisions that must be refused with 422; return the error entries."""
    response = client.post(f"/threads/{thread}/resume", json=resume_body(client, thread, decisions))
    assert response.status_code == 422, response.text
    return response.json()["detail"]


def tool_messages(model: ScriptedChatModel) -> list[ToolMessage]:
    """The tool results the model was shown on its most recent call."""
    return [message for message in model.seen[-1] if isinstance(message, ToolMessage)]


def restarted(
    make_mcp_app: Callable[..., McpApp],
    first: McpApp,
    model: ScriptedChatModel,
    servers: dict[str, dict[str, Any]],
    stubs: dict[str, StubServer] | None = None,
) -> McpApp:
    """The service after a stop and a start with another server list, on the same database."""
    runtime = first.runtime
    return make_mcp_app(
        model,
        servers,
        stubs=stubs if stubs is not None else first.stubs,
        instance_id="instance-b",
        saver=runtime.agent.checkpointer,
        store=runtime.agent.store,
        locks=runtime.locks,
    )


# The decisions (User Story 2, scenarios 1 to 8).


def test_a_gated_tool_pauses_and_names_its_server(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[propose(("record_note", NOTE))]), STUB)
    with TestClient(made.app) as client:
        events = run(client, "g-1", sse_events)
        state = client.get("/threads/g-1/state").json()

    kind, data = events[-1]
    assert kind == "interrupt"
    assert data["action_requests"] == [
        {
            "name": "record_note",
            "server": "stub",
            # A tool server's tool goes to no agent (feature 003, FR-013).
            "agent": None,
            "args": NOTE,
            "description": "stub: Record a note on the server.",
            "allowed_decisions": ALL,
        }
    ]
    assert data["allowed_decisions"] == ALL
    assert state["pending"] == data
    # The server labels this tool read-only; the operator did not list it, so it is gated,
    # and nothing has reached the server.
    assert made.stubs["stub"].calls == []
    assert made.stubs["stub"].notes == []


def test_approve_makes_exactly_one_call_with_the_proposed_arguments(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[propose(("record_note", NOTE))], final_text="Recorded.")
    made = make_mcp_app(model, STUB)
    with TestClient(made.app) as client:
        run(client, "g-2", sse_events)
        events = resume(client, "g-2", [{"type": "approve"}], sse_events)
        state = client.get("/threads/g-2/state").json()

    assert events[-1] == ("end", {"status": "completed"})
    assert state["pending"] is None
    assert made.stubs["stub"].calls == [("record_note", NOTE)]
    (result,) = tool_messages(model)
    assert result.content == "recorded note 1: ship it"


def test_edit_makes_one_call_with_the_edited_arguments(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[propose(("record_note", NOTE))]), STUB)
    edited = {"note": "hold it", "priority": 9}
    with TestClient(made.app) as client:
        run(client, "g-3", sse_events)
        events = resume(client, "g-3", [{"type": "edit", "args": edited}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert made.stubs["stub"].calls == [("record_note", edited)]


def test_reject_makes_no_call_and_the_reason_reaches_the_model(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[propose(("record_note", NOTE))], final_text="Not done.")
    made = make_mcp_app(model, STUB)
    reason = "Not on this server."
    with TestClient(made.app) as client:
        run(client, "g-4", sse_events)
        events = resume(client, "g-4", [{"type": "reject", "message": reason}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert made.stubs["stub"].calls == []
    (result,) = tool_messages(model)
    assert reason in result.content


def test_a_tool_the_configuration_does_not_mention_is_gated(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    servers = {"stub": {"command": "stub-command"}}  # no read-only list at all
    made = make_mcp_app(ScriptedChatModel(script=[propose(("lookup", {"key": "a"}))]), servers)
    with TestClient(made.app) as client:
        events = run(client, "g-5", sse_events)
    kind, data = events[-1]
    assert kind == "interrupt"
    assert (data["action_requests"][0]["name"], data["action_requests"][0]["server"]) == (
        "lookup",
        "stub",
    )
    assert made.stubs["stub"].calls == []


def test_a_proposal_made_by_the_subagent_pauses_the_same_way(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    # Call 1 delegates; call 2, inside the subagent, proposes the gated tool.
    model = ScriptedChatModel(script=[TASK, propose(("record_note", NOTE))], final_text="Done.")
    made = make_mcp_app(model, STUB)
    with TestClient(made.app) as client:
        events = run(client, "g-6", sse_events)
        kind, data = events[-1]
        assert kind == "interrupt"
        (action,) = data["action_requests"]
        assert (action["name"], action["server"], action["args"]) == ("record_note", "stub", NOTE)
        assert made.stubs["stub"].calls == []

        events = resume(client, "g-6", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert made.stubs["stub"].calls == [("record_note", NOTE)]


def test_a_step_with_a_gated_and_a_read_only_tool_holds_both(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    step = propose(("lookup", {"key": "alpha"}), ("record_note", NOTE))
    made = make_mcp_app(ScriptedChatModel(script=[step]), STUB)
    with TestClient(made.app) as client:
        events = run(client, "g-7", sse_events)
        kind, data = events[-1]
        assert kind == "interrupt"
        # Only the gated call needs a decision, and neither call has run.
        assert [action["name"] for action in data["action_requests"]] == ["record_note"]
        assert made.stubs["stub"].calls == []

        events = resume(client, "g-7", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert sorted(made.stubs["stub"].calls, key=lambda entry: entry[0]) == [
        ("lookup", {"key": "alpha"}),
        ("record_note", NOTE),
    ]


def test_two_gated_tools_in_one_step_each_get_their_own_decision(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    servers = {"notes": {"command": "stub-command"}, "reports": {"url": "http://127.0.0.1:9/mcp"}}
    stubs = {
        "notes": make_stub("notes", only=["record_note"]),
        "reports": make_stub("reports", only=["file_report"]),
    }
    report = {"title": "Weekly", "opts": {"tags": ["ops"], "draft": True}}
    step = propose(("record_note", NOTE), ("file_report", report))
    made = make_mcp_app(ScriptedChatModel(script=[step]), servers, stubs=stubs)
    with TestClient(made.app) as client:
        events = run(client, "g-8", sse_events)
        kind, data = events[-1]
        assert kind == "interrupt"
        assert [(a["name"], a["server"]) for a in data["action_requests"]] == [
            ("record_note", "notes"),
            ("file_report", "reports"),
        ]
        assert stubs["notes"].calls == [] and stubs["reports"].calls == []

        decisions = [{"type": "reject", "message": "no notes today"}, {"type": "approve"}]
        events = resume(client, "g-8", decisions, sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert stubs["notes"].calls == []
    assert stubs["reports"].calls == [("file_report", report)]


def test_freyjas_own_gated_tool_has_no_server(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    notify = ("send_notification", {"channel": "#ops", "message": "hello"})
    made = make_mcp_app(ScriptedChatModel(script=[propose(notify)]), STUB)
    with TestClient(made.app) as client:
        events = run(client, "g-9", sse_events)
    (action,) = events[-1][1]["action_requests"]
    assert action["name"] == "send_notification"
    assert action["server"] is None
    assert action["allowed_decisions"] == ALL


# Edit validation against the tool's declared input (FR-009, research R9).

REPORT = {"title": "Weekly", "opts": {"tags": ["ops"]}}


@pytest.mark.parametrize(
    ("args", "location", "kind"),
    [
        pytest.param(
            {"title": 5, "opts": {"tags": ["ops"]}}, ["title"], "value_error", id="wrong-type"
        ),
        pytest.param(
            {"title": "t", "opts": {"tags": ["ops", 7]}},
            ["opts", "tags", 1],
            "value_error",
            id="wrong-element-in-nested-list",
        ),
        pytest.param({"title": "t"}, ["opts"], "missing", id="missing-required"),
        pytest.param(
            {"title": "t", "opts": {}}, ["opts", "tags"], "missing", id="missing-nested-required"
        ),
        pytest.param(
            {**REPORT, "extra": "x"}, ["extra"], "extra_forbidden", id="argument-not-named"
        ),
    ],
)
def test_an_edit_the_declared_input_does_not_accept_is_refused(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    args: dict[str, Any],
    location: list[Any],
    kind: str,
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[propose(("file_report", REPORT))]), STUB)
    with TestClient(made.app) as client:
        run(client, "e-1", sse_events)
        before = pending(client, "e-1")
        errors = refuse(client, "e-1", [{"type": "edit", "args": args}])
        assert [error["loc"] for error in errors] == [["body", "decisions", 0, "args", *location]]
        assert errors[0]["type"] == kind
        assert all({"type", "loc", "msg"} <= set(error) for error in errors)
        # The proposal is untouched and nothing reached the server.
        assert pending(client, "e-1") == before
        assert made.stubs["stub"].calls == []

        valid = {"title": "Edited", "opts": {"tags": ["edited"], "draft": True}}
        events = resume(client, "e-1", [{"type": "edit", "args": valid}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert made.stubs["stub"].calls == [("file_report", valid)]


def test_every_problem_in_an_edit_is_reported_at_once(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[propose(("file_report", REPORT))]), STUB)
    with TestClient(made.app) as client:
        run(client, "e-2", sse_events)
        errors = refuse(
            client, "e-2", [{"type": "edit", "args": {"title": 5, "opts": {"tags": "x"}, "a": 1}}]
        )
    assert sorted(error["loc"][4:] for error in errors) == [["a"], ["opts", "tags"], ["title"]]


def test_a_tool_with_no_declared_input_does_not_accept_an_edit(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[propose(("ping", {}))]), STUB)
    with TestClient(made.app) as client:
        events = run(client, "e-3", sse_events)
        (action,) = events[-1][1]["action_requests"]
        # The pause itself says an edit is not available.
        assert action["allowed_decisions"] == ["approve", "reject"]
        assert events[-1][1]["allowed_decisions"] == ["approve", "reject"]
        errors = refuse(client, "e-3", [{"type": "edit", "args": {}}])
        assert errors[0]["loc"] == ["body", "decisions", 0, "type"]
        assert made.stubs["stub"].calls == []

        events = resume(client, "e-3", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert made.stubs["stub"].calls == [("ping", {})]


# A proposal decided under a changed configuration (FR-019, research R9). Each case pauses
# under one server list and resumes on a second runtime that shares the persistence.


def paused(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    thread: str,
    step: AIMessage,
    servers: dict[str, dict[str, Any]] = STUB,
) -> tuple[McpApp, dict[str, Any]]:
    """Pause ``thread`` on ``step`` and stop the service; return the app and the proposal."""
    made = make_mcp_app(ScriptedChatModel(script=[step]), servers)
    with TestClient(made.app) as client:
        events = run(client, thread, sse_events)
    kind, proposal = events[-1]
    assert kind == "interrupt"
    assert made.stubs["stub"].calls == []
    return made, proposal


def test_a_tool_that_is_still_gated_is_decided_against_the_current_server(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    first, proposal = paused(make_mcp_app, sse_events, "f-1", propose(("record_note", NOTE)))
    received: list[dict[str, Any]] = []

    def record_note(text: str, urgent: bool = False) -> str:
        """Record a note, in the server's new form."""
        received.append({"text": text, "urgent": urgent})
        return f"recorded: {text}"

    # After the restart another server provides the tool, with another declared input.
    moved = make_stub("archive", only=[], extra={"record_note": record_note})
    second = restarted(
        make_mcp_app,
        first,
        ScriptedChatModel(final_text="Recorded."),
        {"archive": {"command": "stub-command"}},
        stubs={"archive": moved},
    )
    with TestClient(second.app) as client:
        now = pending(client, "f-1")
        assert now["interrupt_id"] == proposal["interrupt_id"]
        (action,) = now["action_requests"]
        assert (action["server"], action["allowed_decisions"]) == ("archive", ALL)
        # The edit is checked against the input the tool declares now, not the one it had.
        errors = refuse(client, "f-1", [{"type": "edit", "args": {"note": "x", "priority": 1}}])
        assert sorted(error["loc"][-1] for error in errors) == ["note", "priority", "text"]
        events = resume(client, "f-1", [{"type": "edit", "args": {"text": "moved"}}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert received == [{"text": "moved", "urgent": False}]
    assert first.stubs["stub"].calls == []


@pytest.mark.parametrize("decision", [{"type": "approve"}, {"type": "reject", "message": "no"}])
def test_a_tool_that_was_removed_is_never_called(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, decision: dict[str, Any]
) -> None:
    first, proposal = paused(make_mcp_app, sse_events, "f-2", propose(("record_note", NOTE)))
    model = ScriptedChatModel(final_text="That tool is gone.")
    second = restarted(make_mcp_app, first, model, {})  # no tool servers any more
    with TestClient(second.app) as client:
        now = pending(client, "f-2")
        assert now["interrupt_id"] == proposal["interrupt_id"]
        (action,) = now["action_requests"]
        assert action["server"] is None
        assert action["allowed_decisions"] == ["approve", "reject"]
        errors = refuse(client, "f-2", [{"type": "edit", "args": NOTE}])
        assert "no longer configured" in errors[0]["msg"]

        events = resume(client, "f-2", [decision], sse_events)
        state = client.get("/threads/f-2/state").json()

    assert events[-1] == ("end", {"status": "completed"})
    assert state["pending"] is None
    # Whatever the decision, the call was not made, and the model was told why.
    assert first.stubs["stub"].calls == []
    (result,) = tool_messages(model)
    assert result.status == "error"
    assert "record_note" in result.content and "not a valid tool" in result.content


def test_a_tool_that_is_now_read_only_accepts_approve_only(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    first, proposal = paused(make_mcp_app, sse_events, "f-3", propose(("record_note", NOTE)))
    now_exempt = {"stub": {"command": "stub-command", "read_only": ["lookup", "record_note"]}}
    second = restarted(make_mcp_app, first, ScriptedChatModel(final_text="Recorded."), now_exempt)
    with TestClient(second.app) as client:
        now = pending(client, "f-3")
        assert now["interrupt_id"] == proposal["interrupt_id"]
        (action,) = now["action_requests"]
        assert (action["server"], action["allowed_decisions"]) == ("stub", ["approve"])
        assert now["allowed_decisions"] == ["approve"]
        for refused in ({"type": "edit", "args": NOTE}, {"type": "reject", "message": "no"}):
            errors = refuse(client, "f-3", [refused])
            assert "no longer needs a decision" in errors[0]["msg"]
            assert errors[0]["loc"] == ["body", "decisions", 0, "type"]
        assert first.stubs["stub"].calls == []

        events = resume(client, "f-3", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    # It ran as proposed, once.
    assert first.stubs["stub"].calls == [("record_note", NOTE)]


def test_one_of_two_proposals_no_longer_needing_a_decision(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    step = propose(("record_note", NOTE), ("ping", {}))
    first, proposal = paused(make_mcp_app, sse_events, "f-4", step)
    assert [a["name"] for a in proposal["action_requests"]] == ["record_note", "ping"]
    now_exempt = {"stub": {"command": "stub-command", "read_only": ["lookup", "record_note"]}}
    second = restarted(make_mcp_app, first, ScriptedChatModel(final_text="ok"), now_exempt)
    with TestClient(second.app) as client:
        now = pending(client, "f-4")
        assert [a["allowed_decisions"] for a in now["action_requests"]] == [
            ["approve"],
            ["approve", "reject"],
        ]
        # One decision per listed action, as always; only the gated one reaches the gate.
        events = resume(
            client, "f-4", [{"type": "approve"}, {"type": "reject", "message": "no"}], sse_events
        )

    assert events[-1] == ("end", {"status": "completed"})
    assert first.stubs["stub"].calls == [("record_note", NOTE)]


def test_a_call_that_became_gated_blocks_the_resume_until_the_configuration_is_restored(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    step = propose(("record_note", NOTE), ("lookup", {"key": "alpha"}))
    first, proposal = paused(make_mcp_app, sse_events, "f-5", step)
    assert [a["name"] for a in proposal["action_requests"]] == ["record_note"]

    # `lookup` leaves the read-only list while the step that calls it is paused.
    stricter = {"stub": {"command": "stub-command"}}
    second = restarted(make_mcp_app, first, ScriptedChatModel(final_text="ok"), stricter)
    caplog.set_level(logging.ERROR, logger="freyja_agent.api.streaming")
    with TestClient(second.app) as client:
        events = resume(client, "f-5", [{"type": "approve"}], sse_events)
        kind, data = events[-1]
        assert (kind, data["code"]) == ("error", "internal")
        # Nothing from the step ran, and the proposal is still the one that was pending.
        assert first.stubs["stub"].calls == []
        assert pending(client, "f-5")["interrupt_id"] == proposal["interrupt_id"]
        assert client.portal.call(second.runtime.locks.is_active, "f-5") is False
        blocked = client.post("/threads/f-5/runs", json={"message": "never mind"})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "decision_pending"
    # The explanation is in the service log, where the operator looks.
    logged = "\n".join(
        f"{record.getMessage()} {record.exc_text or ''}" for record in caplog.records
    )
    assert "hanging tool calls" in logged

    # Restoring the configuration the proposal was made under lets it be decided.
    third = restarted(make_mcp_app, first, ScriptedChatModel(final_text="ok"), STUB)
    with TestClient(third.app) as client:
        events = resume(client, "f-5", [{"type": "approve"}], sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    assert sorted(name for name, _ in first.stubs["stub"].calls) == ["lookup", "record_note"]


def test_a_new_call_to_a_tool_that_is_no_longer_configured_is_an_error_result(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    first = make_mcp_app(
        ScriptedChatModel(script=[propose(("lookup", {"key": "a"}))], final_text="ok"), STUB
    )
    with TestClient(first.app) as client:
        assert run(client, "f-6", sse_events)[-1] == ("end", {"status": "completed"})
    assert first.stubs["stub"].calls == [("lookup", {"key": "a"})]

    # The server is removed; the old thread still shows the old call and its result.
    model = ScriptedChatModel(script=[propose(("lookup", {"key": "b"}))], final_text="Gone.")
    second = restarted(make_mcp_app, first, model, {})
    with TestClient(second.app) as client:
        events = run(client, "f-6", sse_events, message="look up b")
        transcript = client.get("/threads/f-6/state").json()["messages"]

    assert events[-1] == ("end", {"status": "completed"})
    assert first.stubs["stub"].calls == [("lookup", {"key": "a"})]
    assert any(m["type"] == "tool" and m["content"] == "value-of-a" for m in transcript)
    # The model sees the old result and, for the new call, an error result.
    old_result, result = tool_messages(model)
    assert old_result.content == "value-of-a"
    assert result.status == "error" and "not a valid tool" in result.content


# The edit check on its own, for declared inputs the stub does not have.


def pending_edit(schema: dict[str, Any]) -> tuple[dict[str, Any], Catalog]:
    """A pending proposal for one gated server tool whose declared input is ``schema``."""

    async def noop(**_arguments: Any) -> str:
        return "ok"

    tool = StructuredTool(name="odd", description="odd", args_schema=schema, coroutine=noop)
    catalog = Catalog(
        tools=(
            CatalogTool(
                name="odd",
                server="stub",
                description="odd",
                input_schema=schema,
                classification=Classification.GATED,
                allowed_decisions=("approve", "edit", "reject"),
                tool=tool,
            ),
        )
    )
    interrupt = Interrupt(value={"action_requests": [{"name": "odd", "args": {}}]}, id="int-9")
    return interrupt_to_pending(interrupt, catalog), catalog


def edit_of(args: dict[str, Any]) -> list[EditDecision]:
    return [EditDecision(type="edit", args=args)]


def test_an_open_declared_input_accepts_an_argument_it_does_not_name() -> None:
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "additionalProperties": True,
    }
    pending_proposal, catalog = pending_edit(schema)
    command = build_resume_command(pending_proposal, edit_of({"q": "x", "more": 1}), catalog)
    (decision,) = command.resume["int-9"]["decisions"]
    # The arguments go to the server exactly as the approver wrote them.
    assert decision == {
        "type": "edit",
        "edited_action": {"name": "odd", "args": {"q": "x", "more": 1}},
    }


def test_a_closed_declared_input_reports_each_unnamed_argument_once() -> None:
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "additionalProperties": False,
    }
    pending_proposal, catalog = pending_edit(schema)
    with pytest.raises(HTTPException) as refused:
        build_resume_command(pending_proposal, edit_of({"q": "x", "a": 1, "b": 2}), catalog)
    assert [entry["loc"][-1] for entry in refused.value.detail] == ["a", "b"]
    assert {entry["type"] for entry in refused.value.detail} == {"extra_forbidden"}


def test_a_declared_input_that_points_elsewhere_is_not_fetched() -> None:
    # A server could name a schema at an address; checking an edit must not go and get it.
    schema = {
        "type": "object",
        "properties": {"q": {"$ref": "http://192.0.2.1:9/schemas/q.json"}},
    }
    pending_proposal, catalog = pending_edit(schema)
    started = time.monotonic()
    with pytest.raises(HTTPException) as refused:
        build_resume_command(pending_proposal, edit_of({"q": "x"}), catalog)
    assert time.monotonic() - started < 1
    assert refused.value.status_code == 422
    assert "cannot be edited" in refused.value.detail[0]["msg"]


def test_the_edit_keeps_the_tool_of_the_proposal() -> None:
    pending_proposal, catalog = pending_edit({"type": "object", "properties": {"q": {}}})
    command = build_resume_command(pending_proposal, edit_of({"q": 1}), catalog)
    assert command.resume["int-9"]["decisions"][0]["edited_action"]["name"] == "odd"
