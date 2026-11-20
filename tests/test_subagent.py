"""Principle I through the nested path: the gate and the step limit hold inside the subagent."""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.runtime.runtime import Runtime
from tests.fakes import LoopingChatModel, ScriptedChatModel

ACTIONS = "freyja_agent.actions"

TASK = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {
                "description": "Notify #ops that the nightly report is ready.",
                "subagent_type": "general-purpose",
            },
        }
    ],
)
NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "n1",
            "name": "send_notification",
            "args": {"channel": "#ops", "message": "Nightly report is ready."},
        }
    ],
)


def stream(client: TestClient, path: str, body: dict, sse_events: Any) -> list[tuple[str, Any]]:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def test_a_proposal_raised_inside_the_subagent_pauses_the_run(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=ACTIONS)
    # Parent and subagent share the model: call 1 delegates, call 2 (inside the subagent)
    # proposes the gated action.
    model = ScriptedChatModel(script=[TASK, NOTIFY], final_text="Delegated and done.")
    runtime = make_runtime(model)
    with TestClient(make_app(runtime)) as client:
        events = stream(client, "/threads/s-1/runs", {"message": "delegate it"}, sse_events)
        kind, data = events[-1]
        assert kind == "interrupt"
        assert data["action_requests"][0]["name"] == "send_notification"
        # Freyja's own tool names no server, also when the subagent proposes it (FR-008).
        assert data["action_requests"][0]["server"] is None
        assert [r for r in caplog.records if r.name == ACTIONS] == []
        # The subagent's own model call (the second) is told the same thread id as the parent.
        assert "thread `s-1`" in model.seen[0][0].text
        assert "thread `s-1`" in model.seen[1][0].text

        decision = {"interrupt_id": data["interrupt_id"], "decisions": [{"type": "approve"}]}
        events = stream(client, "/threads/s-1/resume", decision, sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    records = [r for r in caplog.records if r.name == ACTIONS]
    assert len(records) == 1
    assert "channel=#ops" in records[0].getMessage()


def test_the_step_limit_bounds_a_looping_subagent(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    class TaskThenLoop(LoopingChatModel):
        def next_message(self) -> AIMessage:
            if self.calls == 1:
                return TASK.model_copy(deep=True)
            return super().next_message()

    model = TaskThenLoop()
    runtime = make_runtime(model, max_steps=12)
    with TestClient(make_app(runtime)) as client:
        events = stream(client, "/threads/s-2/runs", {"message": "delegate it"}, sse_events)
    kind, data = events[-1]
    assert kind == "error"
    assert data["code"] == "step_limit"
    # Without the bound reaching into the subagent this would be thousands of calls.
    assert model.calls <= 2 * 12


def _pending(client: TestClient, thread: str) -> dict[str, Any]:
    state = client.get(f"/threads/{thread}/state").json()
    assert state["pending"] is not None, state
    return state["pending"]


@pytest.mark.parametrize(
    ("first", "other"),
    [
        ({"type": "approve"}, {"type": "reject", "message": "Not now."}),
        ({"type": "reject", "message": "Not now."}, {"type": "approve"}),
    ],
    ids=["approved-then-rejected", "rejected-then-approved"],
)
def test_a_resume_after_a_decided_step_takes_no_other_decision(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    first: dict[str, Any],
    other: dict[str, Any],
) -> None:
    """A resume decides the subagent's proposal, the gate's step completes, and the
    checkpoint after it cannot be written. The thread still shows the proposal, because the
    orchestrator's step that runs the subagent did not complete. The next resume continues
    past the gate with the first decisions, whatever it sends; until this was fixed it said
    `completed` and carried out the first decision. Other decisions are now refused, and the
    same ones continue the thread."""
    caplog.set_level(logging.WARNING, logger=ACTIONS)
    model = ScriptedChatModel(script=[TASK, NOTIFY], final_text="Delegated and done.")
    runtime = make_runtime(model)
    with TestClient(make_app(runtime)) as client:
        interrupted = stream(client, "/threads/s-3/runs", {"message": "delegate it"}, sse_events)
        assert interrupted[-1][0] == "interrupt"
        saver = runtime.agent.checkpointer
        write = saver.aput
        failed: list[bool] = []

        async def checkpoint(*args: Any) -> Any:
            if not failed:
                failed.append(True)
                raise RuntimeError("the database went away for a moment")
            return await write(*args)

        monkeypatch.setattr(saver, "aput", checkpoint)
        proposal = _pending(client, "s-3")
        decide = {"interrupt_id": proposal["interrupt_id"], "decisions": [first]}
        broken = stream(client, "/threads/s-3/resume", decide, sse_events)
        still = _pending(client, "s-3")
        refused = client.post(
            "/threads/s-3/resume",
            json={"interrupt_id": still["interrupt_id"], "decisions": [other]},
        )
        after_refusal = _pending(client, "s-3")
        continued = stream(client, "/threads/s-3/resume", decide, sse_events)

    assert failed == [True] and broken[-1][0] == "error"
    assert still["interrupt_id"] == proposal["interrupt_id"]
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "already_decided"
    assert f"({first['type']})" in detail["message"]
    # Nothing ran: the proposal is as it was.
    assert after_refusal["interrupt_id"] == proposal["interrupt_id"]
    assert continued[-1] == ("end", {"status": "completed"})
    # The first decision, and it alone, was carried out once.
    sent = [r for r in caplog.records if r.name == ACTIONS]
    assert len(sent) == (1 if first["type"] == "approve" else 0)
