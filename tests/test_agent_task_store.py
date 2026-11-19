"""The agent task store's contract (003 research R8, R9), against the in-memory store.

``tests/integration/test_a2a_tasks_postgres.py`` runs the same tests against the Postgres
store. The ``store`` fixture is the store under test and ``advance(seconds)`` moves its clock
forward: the fake clock here, real waiting there.
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from freyja_agent.runtime.agent_tasks import (
    AgentSend,
    AgentTaskRecord,
    AgentTaskStore,
    InMemoryAgentTaskStore,
)
from tests.fakes import FakeClock

Advance = Callable[[float], Awaitable[None]]


@pytest.fixture
def store(clock: FakeClock) -> AgentTaskStore:
    return InMemoryAgentTaskStore(clock=clock.now)


@pytest.fixture
def advance(clock: FakeClock) -> Advance:
    async def _advance(seconds: float) -> None:
        clock.advance(seconds)

    return _advance


async def a_task(store: AgentTaskStore, task_id: str = "t1", **changes: object) -> AgentTaskRecord:
    now = await store.now()
    task = AgentTaskRecord(
        agent="stub",
        task_id=task_id,
        thread_id="th-1",
        context_id="ctx-1",
        message="compile the report",
        state="working",
        started_at=now,
        last_checked_at=now,
    )
    return replace(task, **changes)


async def test_a_conversation_is_kept_by_its_first_context(store: AgentTaskStore) -> None:
    assert await store.get_context("th-1", "stub") is None
    await store.set_context("th-1", "stub", "ctx-1")
    await store.set_context("th-1", "stub", "ctx-2")
    assert await store.get_context("th-1", "stub") == "ctx-1"
    assert await store.get_context("th-2", "stub") is None
    assert await store.get_context("th-1", "other") is None


async def a_send(store: AgentTaskStore, step: str = "tools:a", **changes: Any) -> AgentSend:
    send = AgentSend(
        thread_id="th-1",
        step=step,
        tool_call_id="call-1",
        agent="stub",
        kind="handoff",
        message_id=f"m-{step}",
        request="digest-1",
        sent_at=await store.now(),
    )
    return replace(send, **changes)


async def test_a_send_is_recorded_once_and_finished(store: AgentTaskStore) -> None:
    send = await a_send(store)
    assert await store.start_send(send) is True
    assert await store.start_send(replace(send, message_id="m-2")) is False
    assert [s.tool_call_id for s in await store.in_flight_sends("th-1")] == ["call-1"]
    await store.finish_send("th-1", "tools:a", "call-1", result="done", task_id="t1")
    finished = await store.get_send("th-1", "tools:a", "call-1")
    assert finished is not None
    assert (finished.message_id, finished.request, finished.result, finished.task_id) == (
        "m-tools:a",
        "digest-1",
        "done",
        "t1",
    )
    assert await store.in_flight_sends("th-1") == []


async def test_the_same_tool_call_id_in_another_step_is_another_send(
    store: AgentTaskStore,
) -> None:
    # A provider may give two calls on one thread the same id; the step tells them apart.
    assert await store.start_send(await a_send(store, "tools:a")) is True
    assert await store.start_send(await a_send(store, "tools:b", request="digest-2")) is True
    await store.finish_send("th-1", "tools:a", "call-1", result="first")
    first = await store.get_send("th-1", "tools:a", "call-1")
    second = await store.get_send("th-1", "tools:b", "call-1")
    assert first is not None and second is not None
    assert (first.result, second.result, second.request) == ("first", None, "digest-2")
    assert await store.get_send("th-1", "tools:c", "call-1") is None
    assert [s.step for s in await store.in_flight_sends("th-1")] == ["tools:b"]


async def test_tasks_are_listed_oldest_first_and_never_dropped(
    store: AgentTaskStore, advance: Advance
) -> None:
    await store.record_task(await a_task(store, "t1"))
    await advance(1)
    await store.record_task(await a_task(store, "t2"))
    await store.update_task("stub", "t1", state="completed", ended_at=await store.now())
    listed = await store.list_tasks("th-1")
    assert [(t.task_id, t.state) for t in listed] == [("t1", "completed"), ("t2", "working")]
    assert await store.list_tasks("th-other") == []


async def test_an_unknown_column_is_refused(store: AgentTaskStore) -> None:
    await store.record_task(await a_task(store))
    with pytest.raises(ValueError, match="thread_id"):
        await store.update_task("stub", "t1", thread_id="th-2")


async def test_a_pending_task_is_claimed_by_one_instance_until_the_claim_expires(
    store: AgentTaskStore, advance: Advance
) -> None:
    await store.record_task(await a_task(store, "t1"))
    await store.record_task(await a_task(store, "t2", state="input_required"))
    await store.record_task(await a_task(store, "t3", state="completed"))
    first = await store.claim_due("instance-a", 1)
    assert sorted(t.task_id for t in first) == ["t1", "t2"]
    assert await store.claim_due("instance-b", 1) == []
    # The holder renews its own claim.
    assert len(await store.claim_due("instance-a", 1)) == 2
    await advance(1.5)
    taken = await store.claim_due("instance-b", 1)
    assert sorted(t.task_id for t in taken) == ["t1", "t2"]
    assert all(t.watcher == "instance-b" for t in taken)


async def test_ready_outcomes_wait_for_delivery_once(
    store: AgentTaskStore, advance: Advance
) -> None:
    await store.record_task(await a_task(store, "t1"))
    await store.record_task(await a_task(store, "t2"))
    await store.update_task("stub", "t2", state="failed", outcome="no", outcome_ready=True)
    await advance(1)
    await store.update_task(
        "stub",
        "t1",
        state="completed",
        outcome="yes",
        outcome_ready=True,
        last_checked_at=await store.now(),
    )
    assert [t.task_id for t in await store.ready_outcomes("th-1")] == ["t2", "t1"]
    assert await store.threads_with_ready_outcomes() == ["th-1"]
    await store.mark_delivered("stub", "t2")
    await store.mark_delivered("stub", "t1")
    assert await store.ready_outcomes("th-1") == []
    assert await store.threads_with_ready_outcomes() == []
    delivered = await store.get_task("stub", "t1")
    assert delivered is not None and delivered.outcome_delivered


async def test_follow_ups_are_set_and_cleared_per_thread(store: AgentTaskStore) -> None:
    await store.record_task(await a_task(store, "t1"))
    next_at = await store.now() + timedelta(seconds=60)
    await store.set_followup([("stub", "t1")], attempts=1, next_at=next_at, error="model_error")
    task = await store.get_task("stub", "t1")
    assert task is not None
    assert (task.followup_attempts, task.followup_next_at, task.followup_error) == (
        1,
        next_at,
        "model_error",
    )
    await store.clear_followups("th-1")
    task = await store.get_task("stub", "t1")
    assert task is not None
    assert (task.followup_attempts, task.followup_next_at, task.followup_error) == (0, None, None)


async def test_an_update_can_require_the_state_it_expects(store: AgentTaskStore) -> None:
    # A cancel and a late status check can cross; the one that finds the task moved on loses.
    await store.record_task(await a_task(store))
    assert await store.update_task("stub", "t1", when_state=("working",), state="canceled")
    assert not await store.update_task("stub", "t1", when_state=("working",), state="completed")
    task = await store.get_task("stub", "t1")
    assert task is not None and task.state == "canceled"
    assert await store.update_task("stub", "t1", outcome="any state")


async def test_a_task_that_does_not_exist_is_left_alone(store: AgentTaskStore) -> None:
    """T078: both stores answer the same way, so the suite sees what the service would."""
    assert not await store.update_task("stub", "missing", state="completed")
    assert not await store.update_task(
        "stub", "missing", when_state=("working",), state="completed"
    )
    await store.mark_delivered("stub", "missing")
    await store.set_followup([("stub", "missing")], attempts=1, next_at=None, error="internal")
    assert await store.get_task("stub", "missing") is None
    assert await store.list_tasks("th-1") == []
    # An update that sets nothing reports that nothing was updated.
    await store.record_task(await a_task(store))
    assert not await store.update_task("stub", "t1")
    assert not await store.update_task("stub", "t1", when_state=("working",))


async def test_an_outcome_is_marked_delivered_once(store: AgentTaskStore, advance: Advance) -> None:
    """T078: a second mark keeps the time of the first delivery."""
    await store.record_task(await a_task(store, outcome="done", outcome_ready=True))
    await store.mark_delivered("stub", "t1")
    first = await store.get_task("stub", "t1")
    assert first is not None and first.delivered_at is not None
    await advance(0.05)
    await store.mark_delivered("stub", "t1")
    second = await store.get_task("stub", "t1")
    assert second is not None and second.delivered_at == first.delivered_at


async def test_threads_with_a_due_retry_are_listed_once_it_is_due(
    store: AgentTaskStore, advance: Advance
) -> None:
    await store.record_task(await a_task(store, "t1"))
    await store.record_task(await a_task(store, "t2", thread_id="th-2"))
    soon = await store.now() + timedelta(seconds=1)
    await store.set_followup([("stub", "t1")], attempts=1, next_at=soon, error="model_error")
    await store.set_followup([("stub", "t2")], attempts=3, next_at=None, error="model_error")
    assert await store.threads_with_due_followups() == []
    await advance(1.5)
    assert await store.threads_with_due_followups() == ["th-1"]


# Feature 005 (T008): a task says which agent, at which address, it was started with.

MOVED = "Stopped waiting for task {task_id} of agent '{agent}', which was moved."


async def test_a_task_keeps_the_ref_of_the_agent_it_was_started_with(store: AgentTaskStore) -> None:
    await store.record_task(await a_task(store, "t1"))
    await store.record_task(await a_task(store, "t2", agent_ref="0af3:1"))
    plain = await store.get_task("stub", "t1")
    saved = await store.get_task("stub", "t2")
    assert plain is not None and plain.agent_ref is None
    assert saved is not None and saved.agent_ref == "0af3:1"
    assert [task.agent_ref for task in await store.list_tasks("th-1")] == [None, "0af3:1"]
    (claimed,) = [t for t in await store.claim_due("instance-a", 30) if t.task_id == "t2"]
    assert claimed.agent_ref == "0af3:1"


async def test_the_ref_of_a_task_cannot_be_changed(store: AgentTaskStore) -> None:
    await store.record_task(await a_task(store, "t1", agent_ref="0af3:1"))
    with pytest.raises(ValueError, match="agent_ref"):
        await store.update_task("stub", "t1", agent_ref="0af3:2")
    task = await store.get_task("stub", "t1")
    assert task is not None and task.agent_ref == "0af3:1"


async def test_pending_tasks_are_given_up_by_ref(store: AgentTaskStore, advance: Advance) -> None:
    await store.record_task(await a_task(store, "first", agent_ref="0af3:1"))
    await store.record_task(
        await a_task(store, "second", agent_ref="0af3:2", state="input_required")
    )
    await store.record_task(await a_task(store, "other-agent", agent_ref="77aa:1"))
    await store.record_task(await a_task(store, "environment"))
    await store.record_task(
        await a_task(store, "ended", agent_ref="0af3:1", state="completed", outcome="done")
    )
    await advance(1)
    before = await store.now()

    assert await store.give_up_pending(["0af3:1", "0af3:2"], MOVED) == 2

    for task_id in ("first", "second"):
        task = await store.get_task("stub", task_id)
        assert task is not None
        assert task.state == "given_up"
        assert (
            task.outcome == f"Stopped waiting for task {task_id} of agent 'stub', which was moved."
        )
        assert task.outcome_ready and task.delivered_at is None
        assert task.ended_at is not None and task.ended_at >= before
        assert task.last_checked_at == task.ended_at
    # Another agent's task, an environment agent's, and one that had ended are as they were.
    for task_id, state in (("other-agent", "working"), ("environment", "working")):
        task = await store.get_task("stub", task_id)
        assert task is not None and (task.state, task.outcome, task.outcome_ready) == (
            state,
            None,
            False,
        )
    ended = await store.get_task("stub", "ended")
    assert ended is not None and (ended.state, ended.outcome) == ("completed", "done")
    # Its thread is told, like any task given up.
    assert await store.threads_with_ready_outcomes() == ["th-1"]
    # Nothing is left to give up, and no ref gives nothing up.
    assert await store.give_up_pending(["0af3:1", "0af3:2"], MOVED) == 0
    assert await store.give_up_pending([], MOVED) == 0


async def test_pending_tasks_are_counted_by_agent_and_ref(store: AgentTaskStore) -> None:
    assert await store.pending_counts() == {}
    await store.record_task(await a_task(store, "a", agent_ref="0af3:1"))
    await store.record_task(await a_task(store, "b", agent_ref="0af3:1", state="input_required"))
    await store.record_task(await a_task(store, "c", agent_ref="0af3:2", thread_id="th-2"))
    await store.record_task(await a_task(store, "d"))
    await store.record_task(await a_task(store, "e", agent="hello"))
    await store.record_task(await a_task(store, "f", agent_ref="0af3:1", state="completed"))
    await store.record_task(await a_task(store, "g", state="given_up"))
    assert await store.pending_counts() == {
        ("stub", "0af3:1"): 2,
        ("stub", "0af3:2"): 1,
        ("stub", None): 1,
        ("hello", None): 1,
    }
