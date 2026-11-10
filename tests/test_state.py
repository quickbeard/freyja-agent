"""FR-007, SC-005: a thread's state is its transcript, its run marker, and its pending proposal."""

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.runtime.agent_tasks import AgentTaskRecord
from freyja_agent.runtime.runtime import Runtime
from tests.fakes import FailingChatModel, ScriptedChatModel

Events = list[tuple[str, Any]]
PROPOSED = {"channel": "#ops", "message": "Nightly report is ready."}
LS = AIMessage(content="", tool_calls=[{"id": "c1", "name": "ls", "args": {"path": "/"}}])
NOTIFY = AIMessage(
    content="", tool_calls=[{"id": "n1", "name": "send_notification", "args": PROPOSED}]
)


def run(client: TestClient, thread: str, message: str, sse_events: Any) -> Events:
    with client.stream("POST", f"/threads/{thread}/runs", json={"message": message}) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def state(client: TestClient, thread: str) -> dict[str, Any]:
    response = client.get(f"/threads/{thread}/state")
    assert response.status_code == 200, response.text
    return response.json()


def test_completed_turn_shows_the_full_transcript_in_order(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = ScriptedChatModel(script=[LS], final_text="Here is what I found.")
    with TestClient(make_app(make_runtime(model))) as client:
        run(client, "st-1", "list the workspace", sse_events)
        body = state(client, "st-1")

    assert body["thread_id"] == "st-1"
    assert body["run_active"] is False
    assert body["pending"] is None
    assert body["updated_at"]
    messages = body["messages"]
    assert [m["type"] for m in messages] == ["human", "ai", "tool", "ai"]
    assert messages[0]["content"] == "list the workspace"
    assert messages[1]["tool_calls"] == [{"id": "c1", "name": "ls", "args": {"path": "/"}}]
    assert messages[2]["name"] == "ls"
    assert messages[2]["tool_call_id"] == "c1"
    assert messages[3]["content"] == "Here is what I found."
    assert messages[3]["tool_calls"] == []


def test_paused_thread_shows_the_proposal_the_stream_reported(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = ScriptedChatModel(script=[NOTIFY], final_text="Acknowledged.")
    with TestClient(make_app(make_runtime(model))) as client:
        events = run(client, "st-2", "notify", sse_events)
        body = state(client, "st-2")

    kind, interrupt = events[-1]
    assert kind == "interrupt"
    assert body["pending"] == interrupt
    assert body["pending"]["action_requests"][0]["args"] == PROPOSED
    # Freyja's own tool: neither a tool server's nor an agent's (feature 003, FR-013).
    assert body["pending"]["action_requests"][0]["server"] is None
    assert body["pending"]["action_requests"][0]["agent"] is None
    assert body["run_active"] is False
    proposal = body["messages"][-1]
    assert proposal["type"] == "ai"
    assert proposal["tool_calls"][0]["name"] == "send_notification"


AGENT_TASK_FIELDS = {
    "agent",
    "task_id",
    "message",
    "status",
    "started_at",
    "last_checked_at",
    "ended_at",
    "outcome_delivered",
}


def test_a_thread_without_agent_tasks_lists_none(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        run(client, "st-at-1", "hi", sse_events)
        assert state(client, "st-at-1")["agent_tasks"] == []


def test_agent_tasks_are_listed_oldest_first_with_every_field(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    runtime = make_runtime(ScriptedChatModel())
    start = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

    def task(task_id: str, minutes: int, **changes: Any) -> AgentTaskRecord:
        record = AgentTaskRecord(
            agent="stub",
            task_id=task_id,
            thread_id="st-at-2",
            context_id="ctx-1",
            message=f"message {task_id}",
            state="working",
            started_at=start + timedelta(minutes=minutes),
            last_checked_at=start + timedelta(minutes=minutes),
        )
        return replace(record, **changes)

    async def record() -> None:
        # Recorded newest first; listed oldest first. Another thread's task is not listed.
        ended = start + timedelta(minutes=30)
        await runtime.agent_tasks.record_task(
            task("t-2", 20, state="completed", ended_at=ended, outcome="done", outcome_ready=True)
        )
        await runtime.agent_tasks.record_task(task("t-1", 10, state="input_required"))
        await runtime.agent_tasks.record_task(replace(task("t-0", 0), thread_id="elsewhere"))

    with TestClient(make_app(runtime)) as client:
        run(client, "st-at-2", "hi", sse_events)
        client.portal.call(record)
        tasks = state(client, "st-at-2")["agent_tasks"]

    assert all(set(entry) == AGENT_TASK_FIELDS for entry in tasks)
    assert tasks == [
        {
            "agent": "stub",
            "task_id": "t-1",
            "message": "message t-1",
            "status": "input_required",
            "started_at": "2026-11-10T09:10:00Z",
            "last_checked_at": "2026-11-10T09:10:00Z",
            "ended_at": None,
            "outcome_delivered": True,
        },
        {
            "agent": "stub",
            "task_id": "t-2",
            "message": "message t-2",
            "status": "completed",
            "started_at": "2026-11-10T09:20:00Z",
            "last_checked_at": "2026-11-10T09:20:00Z",
            "ended_at": "2026-11-10T09:30:00Z",
            # Ready and not yet recorded in the thread by a run.
            "outcome_delivered": False,
        },
    ]


def test_failed_run_leaves_the_turn_with_nothing_pending(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = FailingChatModel(fail_on_call=1, error="provider unavailable")
    with TestClient(make_app(make_runtime(model))) as client:
        events = run(client, "st-3", "hi", sse_events)
        body = state(client, "st-3")

    assert events[-1][0] == "error"
    assert body["pending"] is None
    assert body["run_active"] is False
    assert [m["type"] for m in body["messages"]] == ["human"]


def test_never_used_thread_is_not_found(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        response = client.get("/threads/never/state")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "thread_not_found"


def test_run_active_is_true_while_a_run_streams_and_false_once_it_closes(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    gate = threading.Event()
    release = threading.Event()

    class BlockingModel(ScriptedChatModel):
        """Blocks on its second call so the second run is observable mid-stream."""

        def next_message(self) -> AIMessage:
            if self.calls == 2:
                gate.set()
                release.wait(timeout=5)
            return super().next_message()

    runtime = make_runtime(BlockingModel(final_text="done"))
    with TestClient(make_app(runtime)) as client:
        run(client, "st-4", "first", sse_events)
        assert state(client, "st-4")["run_active"] is False

        second: dict[str, Any] = {}

        def stream_second() -> None:
            second["events"] = run(client, "st-4", "second", sse_events)

        worker = threading.Thread(target=stream_second)
        worker.start()
        assert gate.wait(timeout=5)
        during = state(client, "st-4")
        release.set()
        worker.join(timeout=10)
        after = state(client, "st-4")

    assert during["run_active"] is True
    assert after["run_active"] is False
    assert second["events"][-1] == ("end", {"status": "completed"})
    assert [m["content"] for m in after["messages"] if m["type"] == "human"] == ["first", "second"]
