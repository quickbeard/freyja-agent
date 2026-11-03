"""FR-009 to FR-013, SC-002, SC-003: the approval gate on send_notification and the resume route."""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from freyja_agent.runtime.runtime import Runtime
from tests.fakes import ScriptedChatModel

ACTIONS = "freyja_agent.actions"
PROPOSED = {"channel": "#ops", "message": "Nightly report is ready."}
Events = list[tuple[str, Any]]


def notify(call_id: str = "n1", **args: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "send_notification", "args": {**PROPOSED, **args}}],
    )


def stream(client: TestClient, path: str, body: dict, sse_events: Any, **params: Any) -> Events:
    with client.stream("POST", path, json=body, params=params) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def run(client: TestClient, thread: str, message: str, sse_events: Any, **params: Any) -> Events:
    return stream(client, f"/threads/{thread}/runs", {"message": message}, sse_events, **params)


def resume(
    client: TestClient, thread: str, decisions: list, sse_events: Any, **params: Any
) -> Events:
    return stream(
        client, f"/threads/{thread}/resume", {"decisions": decisions}, sse_events, **params
    )


def action_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == ACTIONS]


def updates(events: Events, node: str) -> list[dict]:
    return [data for kind, data in events if kind == "update" and data["node"] == node]


async def pending_count(runtime: Runtime, thread: str) -> int:
    snapshot = await runtime.agent.aget_state({"configurable": {"thread_id": thread}})
    return len(snapshot.interrupts)


@pytest.fixture(autouse=True)
def capture_actions(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=ACTIONS)


@pytest.fixture
def gated(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> tuple[Runtime, FastAPI, ScriptedChatModel]:
    model = ScriptedChatModel(script=[notify()], final_text="Acknowledged.")
    runtime = make_runtime(model)
    return runtime, make_app(runtime), model


def test_proposal_pauses_the_run_before_executing(
    gated: tuple[Runtime, FastAPI, ScriptedChatModel], sse_events: Any, caplog: Any
) -> None:
    runtime, app, _ = gated
    with TestClient(app) as client:
        events = run(client, "g-1", "tell #ops the report is ready", sse_events)
        assert client.portal.call(pending_count, runtime, "g-1") == 1

    kind, data = events[-1]
    assert kind == "interrupt"
    assert data["interrupt_id"]
    assert data["action_requests"] == [
        {
            "name": "send_notification",
            "args": PROPOSED,
            "description": "Send a notification to a channel (placeholder: logs only).",
        }
    ]
    assert data["allowed_decisions"] == ["approve", "edit", "reject"]
    assert updates(events, "tools") == []
    assert action_records(caplog) == []


def test_approve_executes_once_with_the_proposed_args(
    gated: tuple[Runtime, FastAPI, ScriptedChatModel], sse_events: Any, caplog: Any
) -> None:
    runtime, app, _ = gated
    with TestClient(app) as client:
        run(client, "g-2", "notify", sse_events)
        events = resume(client, "g-2", [{"type": "approve"}], sse_events)
        assert client.portal.call(pending_count, runtime, "g-2") == 0

    assert events[0][1]["mode"] == "resume"
    assert events[-1] == ("end", {"status": "completed"})
    tool_result = updates(events, "tools")[0]["data"]["messages"][0]
    assert tool_result["type"] == "tool"
    assert "Notification to #ops recorded" in tool_result["content"]
    records = action_records(caplog)
    assert len(records) == 1
    assert "channel=#ops message=Nightly report is ready." in records[0].getMessage()
    assert any(
        kind == "message" and data["message"]["content"] == "Acknowledged." for kind, data in events
    )


def test_edit_executes_with_the_edited_args_and_keeps_the_proposal_in_history(
    gated: tuple[Runtime, FastAPI, ScriptedChatModel], sse_events: Any, caplog: Any
) -> None:
    runtime, app, _ = gated
    edited = {"channel": "#ops-staging", "message": "Nightly report is ready (staging)."}
    with TestClient(app) as client:
        run(client, "g-3", "notify", sse_events)
        events = resume(client, "g-3", [{"type": "edit", "args": edited}], sse_events)

        async def transcript(runtime: Runtime) -> list:
            snapshot = await runtime.agent.aget_state({"configurable": {"thread_id": "g-3"}})
            return snapshot.values["messages"]

        messages = client.portal.call(transcript, runtime)

    assert events[-1] == ("end", {"status": "completed"})
    records = action_records(caplog)
    assert len(records) == 1
    assert (
        "channel=#ops-staging message=Nightly report is ready (staging)." in records[0].getMessage()
    )
    assert "channel=#ops message" not in records[0].getMessage()
    # The model's original proposal stays in the transcript; the tool result records the edit.
    proposal = next(m for m in messages if m.type == "ai" and m.tool_calls)
    assert proposal.tool_calls[0]["args"] == PROPOSED
    result = next(m for m in messages if isinstance(m, ToolMessage))
    assert "#ops-staging" in result.content


def test_reject_skips_the_action_and_relays_the_reason(
    gated: tuple[Runtime, FastAPI, ScriptedChatModel], sse_events: Any, caplog: Any
) -> None:
    runtime, app, model = gated
    reason = "Not needed, I will tell the team myself."
    with TestClient(app) as client:
        run(client, "g-4", "notify", sse_events)
        events = resume(client, "g-4", [{"type": "reject", "message": reason}], sse_events)
        assert client.portal.call(pending_count, runtime, "g-4") == 0

    assert events[-1] == ("end", {"status": "completed"})
    assert updates(events, "tools") == []
    assert action_records(caplog) == []
    relayed = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert relayed and reason in relayed[-1].content


def test_resume_with_nothing_pending_is_refused(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        run(client, "g-5", "hello", sse_events)
        response = client.post("/threads/g-5/resume", json={"decisions": [{"type": "approve"}]})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "nothing_pending"


def test_resume_on_a_never_used_thread_is_not_found(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        response = client.post("/threads/never/resume", json={"decisions": [{"type": "approve"}]})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "thread_not_found"


@pytest.mark.parametrize(
    "decisions",
    [
        pytest.param([{"type": "launch"}], id="unknown-type"),
        pytest.param([{"type": "approve"}, {"type": "approve"}], id="wrong-count"),
        pytest.param([{"type": "edit", "args": {"channel": "#ops"}}], id="edit-missing-field"),
        pytest.param(
            [{"type": "edit", "args": {"channel": 5, "message": "x"}}], id="edit-wrong-type"
        ),
        pytest.param([], id="empty"),
    ],
)
def test_malformed_decisions_are_refused_and_the_proposal_stays(
    gated: tuple[Runtime, FastAPI, ScriptedChatModel],
    sse_events: Any,
    caplog: Any,
    decisions: list,
) -> None:
    runtime, app, _ = gated
    with TestClient(app) as client:
        run(client, "g-6", "notify", sse_events)
        response = client.post("/threads/g-6/resume", json={"decisions": decisions})
        assert response.status_code == 422, response.text
        assert isinstance(response.json()["detail"], list)
        assert client.portal.call(pending_count, runtime, "g-6") == 1
        # The proposal is still decidable.
        events = resume(client, "g-6", [{"type": "approve"}], sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    assert len(action_records(caplog)) == 1


def test_new_message_on_a_paused_thread_is_refused(
    gated: tuple[Runtime, FastAPI, ScriptedChatModel], sse_events: Any
) -> None:
    runtime, app, _ = gated
    with TestClient(app) as client:
        run(client, "g-7", "notify", sse_events)
        response = client.post("/threads/g-7/runs", json={"message": "never mind"})
        assert client.portal.call(pending_count, runtime, "g-7") == 1
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "decision_pending"


def test_two_proposals_in_one_step_take_one_decision_each(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
    caplog: Any,
) -> None:
    two = AIMessage(
        content="",
        tool_calls=[
            {"id": "a", "name": "send_notification", "args": {"channel": "#a", "message": "x"}},
            {"id": "b", "name": "send_notification", "args": {"channel": "#b", "message": "y"}},
        ],
    )
    runtime = make_runtime(ScriptedChatModel(script=[two], final_text="done"))
    with TestClient(make_app(runtime)) as client:
        events = run(client, "g-8", "notify both", sse_events)
        assert [a["args"]["channel"] for a in events[-1][1]["action_requests"]] == ["#a", "#b"]
        events = resume(
            client, "g-8", [{"type": "approve"}, {"type": "reject", "message": "skip"}], sse_events
        )
    assert events[-1] == ("end", {"status": "completed"})
    records = action_records(caplog)
    assert len(records) == 1
    assert "channel=#a " in records[0].getMessage()


def test_debug_flag_on_resume_adds_debug_events(
    gated: tuple[Runtime, FastAPI, ScriptedChatModel], sse_events: Any
) -> None:
    _, app, _ = gated
    with TestClient(app) as client:
        run(client, "g-9", "notify", sse_events)
        events = resume(client, "g-9", [{"type": "approve"}], sse_events, debug="true")
    assert events[0][1]["debug"] is True
    assert "debug" in [kind for kind, _ in events]
    assert events[-1] == ("end", {"status": "completed"})
