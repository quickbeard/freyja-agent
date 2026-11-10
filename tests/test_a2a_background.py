"""003 User Story 4, part 1: outcomes of pending tasks reach the thread exactly once.

FR-018, FR-020, SC-003. An outcome is recorded as Freyja's ``check_agent_tasks`` call and its
result. A user's run records it ahead of the user's message; otherwise a background run started
by the worker records it once the thread is free, and goes through the same gate as any run.
"""

import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatResult
from pydantic import Field

from freyja_agent.runtime.agent_tasks import AgentTaskRecord, InMemoryAgentTaskStore
from tests.conftest import A2aApp
from tests.fakes import FakeClock, LoopingChatModel, ScriptedChatModel
from tests.test_a2a_pending import (
    INTERVAL,
    PENDING,
    TASK,
    busy,
    go_pending,
    handoff,
    pending_app,
    record,
    run,
    shared,
    state,
    stub,
    tasks,
    tick,
    tool_results,
    wait_for,
)

NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "n1",
            "name": "send_notification",
            "args": {"channel": "#ops", "message": "The report is ready."},
        }
    ],
)
DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {
                "description": "Have the stub compile it.",
                "subagent_type": "general-purpose",
            },
        }
    ],
)
CHECK = AIMessage(content="", tool_calls=[{"id": "k1", "name": "check_agent_tasks", "args": {}}])


def outcomes(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The outcome results recorded in a thread, in order."""
    return [m for m in messages if m["type"] == "tool" and m["name"] == "check_agent_tasks"]


def seen_outcomes(model: ScriptedChatModel, call: int) -> list[ToolMessage]:
    return [
        m for m in model.seen[call] if isinstance(m, ToolMessage) and m.name == "check_agent_tasks"
    ]


def ready(client: TestClient, made: A2aApp, thread: str, release: Callable[[], None]) -> None:
    """Let the held task finish and have the worker record its outcome without delivering it."""
    release()
    wait_for(client, made.runtime, TASK, "completed")
    with busy(client, made.runtime, thread):
        tick(client, made.runtime)
    assert record(client, made, TASK).outcome_ready


def approve(client: TestClient, thread: str, sse_events: Any) -> list[tuple[str, Any]]:
    body = {
        "interrupt_id": state(client, thread)["pending"]["interrupt_id"],
        "decisions": [{"type": "approve"}],
    }
    with client.stream("POST", f"/threads/{thread}/resume", json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def test_an_outcome_is_delivered_by_one_background_run(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")], final_text="The report is ready.")
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-1", sse_events)
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        calls = model.calls
        tick(client, made.runtime)
        after = state(client, "b-1")
        assert model.calls == calls + 1
        # A second tick finds nothing left to deliver.
        tick(client, made.runtime)
        again = state(client, "b-1")
        assert model.calls == calls + 1

    call, result, reply = after["messages"][-3:]
    assert (call["type"], call["name"]) == ("ai", "freyja")
    assert [c["name"] for c in call["tool_calls"]] == ["check_agent_tasks"]
    assert call["tool_calls"][0]["args"] == {"task_ids": [TASK]}
    assert call["id"].startswith(f"a2a-outcome-call-stub-{TASK}")
    assert result["id"].startswith(f"a2a-outcome-stub-{TASK}")
    assert result["tool_call_id"] == call["tool_calls"][0]["id"]
    assert result["content"].startswith(f"Agent 'stub' completed task {TASK}:")
    assert "hold: report" in result["content"]
    assert (reply["type"], reply["content"]) == ("ai", "The report is ready.")
    # The orchestrator received the outcome as the last thing before its reply.
    assert seen_outcomes(model, -1)[-1].content == result["content"]
    assert after["agent_tasks"][0]["status"] == "completed"
    assert after["agent_tasks"][0]["ended_at"] is not None
    assert after["agent_tasks"][0]["outcome_delivered"] is True
    assert again["messages"] == after["messages"]
    assert not any(m["type"] == "human" for m in after["messages"][2:])


def test_a_recorded_outcome_is_not_recorded_again_after_a_restart(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = stub()
    database = shared(clock)
    store: InMemoryAgentTaskStore = database["store"]
    model = ScriptedChatModel(script=[handoff("hold: report")], final_text="The report is ready.")
    made = pending_app(make_a2a_app, model, agent, **database)

    async def lost(_agent: str, _task_id: str) -> None:
        raise RuntimeError("the service stopped before it marked the outcome delivered")

    with TestClient(made.app) as client:
        go_pending(client, "b-2", sse_events)
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        with monkeypatch.context() as patch:
            patch.setattr(store, "mark_delivered", lost)
            tick(client, made.runtime)
        assert tasks(client, "b-2") == [(TASK, "completed", False)]
        assert len(outcomes(state(client, "b-2")["messages"])) == 1

    restarted_model = ScriptedChatModel(final_text="Said twice.")
    restarted = pending_app(
        make_a2a_app, restarted_model, agent, instance_id="instance-b", **database
    )
    with TestClient(restarted.app) as client:
        clock.advance(3 * INTERVAL + 1)
        tick(client, restarted.runtime)
        after = state(client, "b-2")

    # The check found the outcome already in the thread: marked delivered, not recorded again.
    assert restarted_model.calls == 0
    assert len(outcomes(after["messages"])) == 1
    assert after["agent_tasks"][0]["outcome_delivered"] is True


def test_an_outcome_recorded_before_a_restart_is_delivered_after_it(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    agent = stub()
    database = shared(clock)
    made = pending_app(
        make_a2a_app, ScriptedChatModel(script=[handoff("hold: report")]), agent, **database
    )
    with TestClient(made.app) as client:
        go_pending(client, "b-3", sse_events)
        ready(client, made, "b-3", agent.release)

    restarted_model = ScriptedChatModel(final_text="The report is ready.")
    restarted = pending_app(
        make_a2a_app, restarted_model, agent, instance_id="instance-b", **database
    )
    with TestClient(restarted.app) as client:
        tick(client, restarted.runtime)
        tick(client, restarted.runtime)
        after = state(client, "b-3")

    assert restarted_model.calls == 1
    assert len(outcomes(after["messages"])) == 1
    assert after["messages"][-1]["content"] == "The report is ready."


def test_a_busy_thread_gets_its_outcome_once_it_is_free(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")])
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-4", sse_events)
        ready(client, made, "b-4", agent.release)
        assert outcomes(state(client, "b-4")["messages"]) == []
        tick(client, made.runtime)
        delivered = outcomes(state(client, "b-4")["messages"])
    assert len(delivered) == 1


def test_a_paused_thread_waits_and_a_resume_records_nothing(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report"), NOTIFY], final_text="Noted.")
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        assert run(client, "b-5", "Report, then notify.", sse_events)[-1][0] == "interrupt"
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        tick(client, made.runtime)
        # The thread waits on a decision: the outcome is recorded at the task, not delivered.
        paused = state(client, "b-5")
        assert paused["pending"] is not None
        assert outcomes(paused["messages"]) == []
        assert approve(client, "b-5", sse_events)[-1] == ("end", {"status": "completed"})
        resumed = state(client, "b-5")
        assert outcomes(resumed["messages"]) == []
        assert resumed["agent_tasks"][0]["outcome_delivered"] is False
        tick(client, made.runtime)
        after = state(client, "b-5")

    assert len(outcomes(after["messages"])) == 1
    assert after["agent_tasks"][0]["outcome_delivered"] is True


def test_a_users_run_records_the_outcome_before_the_message(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")], final_text="It is ready.")
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-6", sse_events)
        ready(client, made, "b-6", agent.release)
        events = run(client, "b-6", "Is the report ready?", sse_events)
        assert events[-1] == ("end", {"status": "completed"})
        calls = model.calls
        tick(client, made.runtime)
        after = state(client, "b-6")

    assert model.calls == calls  # no background run follows
    seen = model.seen[-1]
    kinds = [(type(m).__name__, getattr(m, "name", None)) for m in seen[-3:]]
    assert kinds == [
        ("AIMessage", "freyja"),
        ("ToolMessage", "check_agent_tasks"),
        ("HumanMessage", None),
    ]
    assert isinstance(seen[-1], HumanMessage) and seen[-1].content == "Is the report ready?"
    assert len(outcomes(after["messages"])) == 1
    assert after["agent_tasks"][0]["outcome_delivered"] is True


def test_a_background_run_that_proposes_a_gated_action_pauses(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(
        script=[handoff("hold: report"), AIMessage(content="Pending."), NOTIFY],
        final_text="Notified.",
    )
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-7", sse_events)
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        tick(client, made.runtime)
        paused = state(client, "b-7")
        assert paused["run_active"] is False
        (action,) = paused["pending"]["action_requests"]
        assert action["name"] == "send_notification"
        assert approve(client, "b-7", sse_events)[-1] == ("end", {"status": "completed"})
        after = state(client, "b-7")

    assert after["pending"] is None
    assert after["messages"][-1]["content"] == "Notified."
    assert len(outcomes(after["messages"])) == 1


def test_several_outcomes_are_delivered_by_one_run_in_the_order_they_arrived(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    agent = stub()
    model = ScriptedChatModel(
        script=[handoff("slow 0.6: first", call_id="h1"), handoff("hold: second", call_id="h2")],
        final_text="Both are done.",
    )
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-8", sse_events)
        assert [t[:2] for t in tasks(client, "b-8")] == [
            ("task-1", "working"),
            ("task-2", "working"),
        ]
        wait_for(client, made.runtime, "task-1", "completed")
        with busy(client, made.runtime, "b-8"):
            tick(client, made.runtime)
            clock.advance(INTERVAL)
            agent.release()
            wait_for(client, made.runtime, "task-2", "completed")
            tick(client, made.runtime)
        calls = model.calls
        tick(client, made.runtime)
        after = state(client, "b-8")

    assert model.calls == calls + 1
    call = next(
        m for m in after["messages"] if m["type"] == "ai" and m["id"].startswith("a2a-outcome")
    )
    # One check per task, each answered by its own result: a provider takes one result per call.
    assert [c["args"] for c in call["tool_calls"]] == [
        {"task_ids": ["task-1"]},
        {"task_ids": ["task-2"]},
    ]
    delivered = outcomes(after["messages"])
    assert [m["tool_call_id"] for m in delivered] == [c["id"] for c in call["tool_calls"]]
    assert [m["content"].split(":")[0] for m in delivered] == [
        "Agent 'stub' completed task task-1",
        "Agent 'stub' completed task task-2",
    ]


def test_a_task_a_subagent_started_is_delivered_to_the_orchestrator(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(
        script=[DELEGATE, handoff("hold: report", call_id="s1")],
        final_text="Handled.",
    )
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-9", sse_events)
        assert tasks(client, "b-9") == [(TASK, "working", False)]
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        tick(client, made.runtime)
        after = state(client, "b-9")

    # The outcome is in the orchestrator's own messages, not the subagent's.
    (delivered,) = outcomes(after["messages"])
    assert delivered["content"].startswith(f"Agent 'stub' completed task {TASK}:")
    assert after["agent_tasks"][0]["outcome_delivered"] is True


# T040: what the orchestrator is told about pending tasks, and the check on demand.


def pending_lines(message: SystemMessage) -> list[str]:
    return [line for line in message.text.splitlines() if line.startswith("- Agent 'stub', task")]


def test_every_model_call_is_told_of_the_threads_pending_tasks(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")], final_text="It is pending.")
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-10", sse_events)
        started = record(client, made, TASK).started_at
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        tick(client, made.runtime)
        run(client, "b-10", "Thanks.", sse_events)

    systems = [call[0] for call in model.seen]
    assert all(isinstance(system, SystemMessage) for system in systems)
    first, during, delivered, later = (pending_lines(s) for s in systems)
    assert first == []
    assert during == [f"- Agent 'stub', task {TASK}: working, started {started.isoformat()}"]
    assert delivered == [] and later == []


def test_check_agent_tasks_reads_the_threads_tasks_without_a_pause(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(
        script=[
            handoff("hold: report"),
            CHECK,
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "k2",
                        "name": "check_agent_tasks",
                        "args": {"task_ids": [TASK, "unknown-task"]},
                    }
                ],
            ),
        ]
    )
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        events = go_pending(client, "b-11", sse_events)
        catalog = {tool["name"]: tool for tool in client.get("/tools").json()["tools"]}
        empty = run(client, "b-12", "hello", sse_events)
        agent.release()

    every, named = tool_results(events, "check_agent_tasks")
    assert every["content"].startswith(f"- Agent 'stub', task {TASK}: working, started ")
    assert "hold: report" in every["content"]
    assert named["content"].splitlines()[0] == every["content"].splitlines()[0]
    assert "No agent task unknown-task on this thread." in named["content"]
    assert not any(kind == "interrupt" for kind, _ in events)
    assert catalog["check_agent_tasks"]["classification"] == "read_only"
    assert catalog["check_agent_tasks"]["allowed_decisions"] == []
    assert empty[-1] == ("end", {"status": "completed"})
    assert tool_results(events, "handoff_to_stub")[0]["content"] == PENDING


def test_the_model_is_told_how_pending_tasks_are_reported(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel()
    made = pending_app(make_a2a_app, model, stub())
    with TestClient(made.app) as client:
        run(client, "b-13", "hi", sse_events)
    system = " ".join(model.seen[0][0].text.split())
    assert "still working on when the hand-off returns is pending" in system
    assert "do not hand it off again" in system
    assert "Its outcome is reported later in this thread, as a check_agent_tasks result" in system
    assert "agent tasks, use check_agent_tasks." in system
    assert "Cancel a pending or given-up task only when the person asks you to" in system
    assert "never cancel one on your own, also not when a task was given up." in system


def test_the_worker_watches_and_delivers_on_its_own(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")], final_text="The report is ready.")
    made = make_a2a_app(
        model,
        {"stub": {"read_only": True}},
        stubs={"stub": agent},
        a2a_call_timeout_seconds=0.3,
        a2a_poll_interval_seconds=0.05,
        run_worker=True,
    )
    with TestClient(made.app) as client:
        go_pending(client, "b-14", sse_events)
        agent.release()
        deadline = time.monotonic() + 5
        while tasks(client, "b-14") != [(TASK, "completed", True)]:
            assert time.monotonic() < deadline, tasks(client, "b-14")
            time.sleep(0.05)
        after = state(client, "b-14")
    assert len(outcomes(after["messages"])) == 1


def test_a_failure_is_delivered_as_an_error_result(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold fail: report")], final_text="It failed.")
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-15", sse_events)
        agent.release()
        wait_for(client, made.runtime, TASK, "failed")
        tick(client, made.runtime)
        after = state(client, "b-15")

    (delivered,) = seen_outcomes(model, -1)
    assert delivered.status == "error"
    assert delivered.content.startswith(f"Agent 'stub' task {TASK} failed.")
    assert after["agent_tasks"][0]["status"] == "failed"


def test_a_question_delivered_later_and_its_answer_deliver_each_outcome(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    agent = stub()
    model = ScriptedChatModel(
        script=[
            handoff("hold ask: plan the release"),
            AIMessage(content="It is under way."),
            AIMessage(content="The stub asks: Which date should it be?"),
            handoff("slow 0.6: Friday.", call_id="h2", task_id=TASK),
        ],
        final_text="Noted.",
    )
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "b-16", sse_events)
        agent.release()
        wait_for(client, made.runtime, TASK, "input_required")
        # The question arrives through the worker and a background run asks the person.
        tick(client, made.runtime)
        assert tasks(client, "b-16") == [(TASK, "input_required", True)]
        clock.advance(INTERVAL)
        # The answer is still being worked on at the limit: pending again, nothing delivered.
        events = run(client, "b-16", "Friday.", sse_events)
        assert tool_results(events, "handoff_to_stub")[0]["content"] == PENDING
        assert tasks(client, "b-16") == [(TASK, "working", False)]
        wait_for(client, made.runtime, TASK, "completed")
        clock.advance(INTERVAL)
        tick(client, made.runtime)
        after = state(client, "b-16")

    question, answer = outcomes(after["messages"])
    assert question["content"].startswith(f"Agent 'stub' needs input for task {TASK}:")
    assert answer["content"].startswith(f"Agent 'stub' completed task {TASK}:")
    assert "Answered: slow 0.6: Friday." in answer["content"]
    assert question["id"] != answer["id"]
    assert after["agent_tasks"][0]["status"] == "completed"
    assert after["agent_tasks"][0]["outcome_delivered"] is True


# Part 2 (T050): a background run that fails is tried again, and nothing is repeated.

RETRY = 60.0
WORKER = "freyja_agent.delegates.delegation_worker"


class FlakyChatModel(ScriptedChatModel):
    """Raises on the listed call numbers, as a provider outage does; scripted otherwise."""

    fail_on: set[int] = Field(default_factory=set)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.calls + 1 in self.fail_on:
            self.calls += 1
            self.seen.append(list(messages))
            raise RuntimeError("provider unavailable")
        return super()._generate(messages, stop, run_manager, **kwargs)


def retry_app(
    make_a2a_app: Callable[..., A2aApp], model: ScriptedChatModel, agent: Any, **options: Any
) -> A2aApp:
    return pending_app(
        make_a2a_app,
        model,
        agent,
        background_retry_seconds=RETRY,
        background_run_attempts=3,
        **options,
    )


def ready_outcome(client: TestClient, made: A2aApp, thread: str, outcome: str) -> None:
    """Record a finished task with its outcome waiting, as the worker does when it sees one."""
    now = client.portal.call(made.store.now)
    client.portal.call(
        made.store.record_task,
        AgentTaskRecord(
            agent="stub",
            task_id=TASK,
            thread_id=thread,
            context_id="ctx",
            message="compile the report",
            state="completed",
            started_at=now,
            last_checked_at=now,
            ended_at=now,
            outcome=outcome,
            outcome_ready=True,
        ),
    )


def follow_up(client: TestClient, made: A2aApp) -> tuple[int, Any, str | None]:
    task = record(client, made, TASK)
    return task.followup_attempts, task.followup_next_at, task.followup_error


def test_a_failed_background_run_is_tried_again_until_it_succeeds(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    agent = stub()
    model = FlakyChatModel(
        script=[handoff("hold: report"), AIMessage(content="Pending.")],
        final_text="The report is ready.",
        fail_on={3, 4},
    )
    made = retry_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "r-1", sse_events)
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        tick(client, made.runtime)
        assert model.calls == 3
        attempts, next_at, error = follow_up(client, made)
        assert (attempts, error) == (1, "model_error")
        assert next_at == client.portal.call(made.store.now) + timedelta(seconds=RETRY)
        # Not before the retry interval.
        clock.advance(RETRY - 1)
        tick(client, made.runtime)
        assert model.calls == 3
        clock.advance(1)
        tick(client, made.runtime)
        assert model.calls == 4
        assert follow_up(client, made)[0] == 2
        clock.advance(RETRY)
        tick(client, made.runtime)
        after = state(client, "r-1")
        cleared = follow_up(client, made)

    assert model.calls == 5
    assert len(outcomes(after["messages"])) == 1
    # Each attempt continued from the last step: the model saw the outcome once each time.
    assert [len(seen_outcomes(model, call)) for call in (2, 3, 4)] == [1, 1, 1]
    assert after["messages"][-1]["content"] == "The report is ready."
    assert len(agent.record.messages) == 1
    assert cleared == (0, None, None)


def test_retries_stop_after_the_configured_attempts_and_the_last_failure_is_logged(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=WORKER)
    model = FlakyChatModel(final_text="It is ready.", fail_on={1, 2, 3})
    made = retry_app(make_a2a_app, model, stub())
    with TestClient(made.app) as client:
        ready_outcome(client, made, "r-2", "Agent 'stub' completed task task-1:\n\nThe report.")
        for _ in range(5):
            tick(client, made.runtime)
            clock.advance(RETRY)
        assert model.calls == 3
        after = state(client, "r-2")
        attempts, next_at, error = follow_up(client, made)

    assert (attempts, next_at, error) == (3, None, "model_error")
    assert len(outcomes(after["messages"])) == 1
    gave_up = [r.getMessage() for r in caplog.records if "gave up" in r.getMessage()]
    assert len(gave_up) == 1
    assert "r-2" in gave_up[0] and TASK in gave_up[0]


def test_a_send_in_flight_when_an_attempt_failed_is_not_sent_again(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = stub()
    model = ScriptedChatModel(
        script=[handoff("follow up: send the summary", call_id="f1")],
        final_text="Done.",
    )
    made = retry_app(make_a2a_app, model, agent)

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database went away after the send")

    with TestClient(made.app) as client:
        ready_outcome(client, made, "r-3", "Agent 'stub' completed task task-1:\n\nThe report.")
        with monkeypatch.context() as patch:
            patch.setattr(made.store, "finish_send", lost)
            tick(client, made.runtime)
        assert follow_up(client, made)[:1] == (1,)
        assert follow_up(client, made)[2] == "internal"
        clock.advance(RETRY)
        tick(client, made.runtime)
        after = state(client, "r-3")

    assert [m.text for m in agent.record.messages] == ["follow up: send the summary"]
    (result,) = [m for m in after["messages"] if m["name"] == "handoff_to_stub"]
    assert "may or may not have received" in result["content"]
    assert len(outcomes(after["messages"])) == 1
    assert after["messages"][-1]["content"] == "Done."


def test_an_attempt_that_failed_before_recording_the_outcome_records_it_on_retry(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = shared(clock)
    saver = database["saver"]
    model = ScriptedChatModel(final_text="It is ready.")
    made = retry_app(make_a2a_app, model, stub(), **database)
    put = saver.aput
    failures = iter([True])

    async def flaky_put(*args: Any, **kwargs: Any) -> Any:
        if next(failures, False):
            raise RuntimeError("the database went away before the input was saved")
        return await put(*args, **kwargs)

    with TestClient(made.app) as client:
        ready_outcome(client, made, "r-4", "Agent 'stub' completed task task-1:\n\nThe report.")
        monkeypatch.setattr(saver, "aput", flaky_put)
        tick(client, made.runtime)
        assert model.calls == 0
        task = record(client, made, TASK)
        assert (task.delivered_at, task.followup_attempts) == (None, 1)
        # The retry waits for its interval, not for the next tick.
        tick(client, made.runtime)
        assert model.calls == 0
        clock.advance(RETRY)
        tick(client, made.runtime)
        after = state(client, "r-4")

    assert model.calls == 1
    assert len(outcomes(after["messages"])) == 1
    assert after["agent_tasks"][0]["outcome_delivered"] is True


def test_retries_stop_once_another_run_completes_but_not_after_one_that_failed(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    model = FlakyChatModel(final_text="Hello.", fail_on={1, 2})
    made = retry_app(make_a2a_app, model, stub())
    with TestClient(made.app) as client:
        ready_outcome(client, made, "r-5", "Agent 'stub' completed task task-1:\n\nThe report.")
        tick(client, made.runtime)
        assert follow_up(client, made)[0] == 1
        # A user's run that fails does not count: the retry is still due.
        assert run(client, "r-5", "Are you there?", sse_events)[-1][1]["code"] == "model_error"
        assert follow_up(client, made)[0] == 1
        # One that completes does.
        assert run(client, "r-5", "Hello?", sse_events)[-1][0] == "end"
        assert follow_up(client, made) == (0, None, None)
        calls = model.calls
        clock.advance(RETRY)
        tick(client, made.runtime)
        after = state(client, "r-5")

    assert model.calls == calls
    assert len(outcomes(after["messages"])) == 1


def test_remaining_attempts_resume_after_a_restart(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    database = shared(clock)
    failing = FlakyChatModel(fail_on={1})
    made = retry_app(make_a2a_app, failing, stub(), **database)
    with TestClient(made.app) as client:
        ready_outcome(client, made, "r-6", "Agent 'stub' completed task task-1:\n\nThe report.")
        tick(client, made.runtime)
        assert follow_up(client, made)[0] == 1

    model = ScriptedChatModel(final_text="The report is ready.")
    restarted = retry_app(make_a2a_app, model, stub(), instance_id="instance-b", **database)
    with TestClient(restarted.app) as client:
        tick(client, restarted.runtime)
        assert model.calls == 0
        clock.advance(RETRY)
        tick(client, restarted.runtime)
        after = state(client, "r-6")

    assert model.calls == 1
    assert len(outcomes(after["messages"])) == 1
    assert after["messages"][-1]["content"] == "The report is ready."


def test_a_background_run_at_the_step_limit_is_not_retried(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    model = LoopingChatModel()
    made = retry_app(make_a2a_app, model, stub(), max_steps=6)
    with TestClient(made.app) as client:
        ready_outcome(client, made, "r-7", "Agent 'stub' completed task task-1:\n\nThe report.")
        tick(client, made.runtime)
        calls = model.calls
        clock.advance(RETRY)
        tick(client, made.runtime)
        after = state(client, "r-7")
        attempts = follow_up(client, made)

    assert model.calls == calls
    assert attempts == (0, None, None)
    # The outcome is in the thread, so it counts as delivered.
    assert len(outcomes(after["messages"])) == 1
    assert after["agent_tasks"][0]["outcome_delivered"] is True


def test_a_retried_attempt_that_proposes_a_gated_hand_off_pauses(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    agent = stub()
    model = FlakyChatModel(
        script=[handoff("send the summary", call_id="f1")], final_text="Sent.", fail_on={1}
    )
    made = retry_app(make_a2a_app, model, agent, read_only=False)
    with TestClient(made.app) as client:
        ready_outcome(client, made, "r-8", "Agent 'stub' completed task task-1:\n\nThe report.")
        tick(client, made.runtime)
        clock.advance(RETRY)
        tick(client, made.runtime)
        paused = state(client, "r-8")
        assert [a["name"] for a in paused["pending"]["action_requests"]] == ["handoff_to_stub"]
        assert agent.record.messages == []
        assert follow_up(client, made) == (0, None, None)
        assert approve(client, "r-8", sse_events)[-1] == ("end", {"status": "completed"})

    assert [m.text for m in agent.record.messages] == ["send the summary"]
