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
        assert [r for r in caplog.records if r.name == ACTIONS] == []
        # The subagent's own model call (the second) is told the same thread id as the parent.
        assert "thread `s-1`" in model.seen[0][0].text
        assert "thread `s-1`" in model.seen[1][0].text

        events = stream(
            client, "/threads/s-1/resume", {"decisions": [{"type": "approve"}]}, sse_events
        )
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
