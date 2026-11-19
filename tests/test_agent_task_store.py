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
    ProposedCall,
    call_namespace,
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


# Feature 005, T052: the proposed calls (research R8, data-model.md "Proposed call").


def a_call(
    tool_call_id: str = "call-1",
    *,
    thread_id: str = "th-1",
    namespace: str = "",
    agent: str = "stub",
    agent_id: str | None = None,
    gated: bool = True,
) -> ProposedCall:
    return ProposedCall(
        thread_id=thread_id,
        namespace=namespace,
        tool_call_id=tool_call_id,
        agent=agent,
        agent_id=agent_id,
        gated=gated,
    )


def keys(calls: list[ProposedCall]) -> list[tuple[str, str]]:
    return [(call.namespace, call.tool_call_id) for call in calls]


async def test_a_proposed_call_is_kept_as_it_was_proposed(
    store: AgentTaskStore, advance: Advance
) -> None:
    assert await store.get_call("th-1", "", "call-1") is None
    assert await store.list_calls("th-1") == []
    await advance(1)
    before = await store.now()
    await store.replace_calls(
        "th-1",
        "",
        [a_call("call-1", agent_id="0af3", gated=False), a_call("call-2", agent="hello")],
    )
    saved = await store.get_call("th-1", "", "call-1")
    assert saved is not None
    assert (saved.thread_id, saved.namespace, saved.tool_call_id) == ("th-1", "", "call-1")
    assert (saved.agent, saved.agent_id, saved.gated) == ("stub", "0af3", False)
    # The store's own clock says when, whatever the caller's.
    assert saved.proposed_at is not None and saved.proposed_at >= before
    environment = await store.get_call("th-1", "", "call-2")
    assert environment is not None
    assert (environment.agent, environment.agent_id, environment.gated) == ("hello", None, True)
    assert keys(await store.list_calls("th-1")) == [("", "call-1"), ("", "call-2")]
    # Another thread, another namespace, and another id each find nothing.
    assert await store.get_call("th-2", "", "call-1") is None
    assert await store.get_call("th-1", "tools:x", "call-1") is None
    assert await store.get_call("th-1", "", "call-3") is None


async def test_a_reply_replaces_the_calls_of_its_thread_and_namespace_and_no_other(
    store: AgentTaskStore,
) -> None:
    await store.replace_calls("th-1", "", [a_call("old-1"), a_call("old-2")])
    await store.replace_calls("th-1", "tools:x", [a_call("sub-1", namespace="tools:x")])
    await store.replace_calls("th-2", "", [a_call("other-1", thread_id="th-2")])

    await store.replace_calls("th-1", "", [a_call("new-1")])

    assert keys(await store.list_calls("th-1")) == [("", "new-1"), ("tools:x", "sub-1")]
    assert keys(await store.list_calls("th-2")) == [("", "other-1")]
    # A reply that calls no agent leaves its namespace with no call.
    await store.replace_calls("th-1", "tools:x", [])
    assert keys(await store.list_calls("th-1")) == [("", "new-1")]


async def test_one_tool_call_id_in_two_namespaces_of_a_thread_is_two_calls(
    store: AgentTaskStore,
) -> None:
    await store.replace_calls("th-1", "", [a_call("call-1", agent="stub")])
    await store.replace_calls(
        "th-1", "tools:x", [a_call("call-1", namespace="tools:x", agent="hello", agent_id="77aa")]
    )
    here = await store.get_call("th-1", "", "call-1")
    there = await store.get_call("th-1", "tools:x", "call-1")
    assert here is not None and (here.agent, here.agent_id) == ("stub", None)
    assert there is not None and (there.agent, there.agent_id) == ("hello", "77aa")
    assert len(await store.list_calls("th-1")) == 2


async def test_one_tool_call_id_twice_in_one_reply_keeps_the_later_call(
    store: AgentTaskStore,
) -> None:
    # Nothing makes a provider keep the id unique within a reply either.
    await store.replace_calls(
        "th-1", "", [a_call("call-1", agent="stub"), a_call("call-1", agent="hello")]
    )
    (kept,) = await store.list_calls("th-1")
    assert kept.agent == "hello"


async def test_a_call_is_stored_under_the_thread_and_namespace_it_is_replaced_for(
    store: AgentTaskStore,
) -> None:
    # What the call itself says of its place is not what decides where it is kept.
    await store.replace_calls("th-1", "", [a_call("call-1", thread_id="th-9", namespace="else")])
    assert keys(await store.list_calls("th-1")) == [("", "call-1")]
    assert await store.list_calls("th-9") == []


async def test_a_call_is_deleted_alone_and_a_thread_s_calls_are_cleared_together(
    store: AgentTaskStore,
) -> None:
    await store.replace_calls("th-1", "", [a_call("call-1"), a_call("call-2")])
    await store.replace_calls("th-1", "tools:x", [a_call("call-1", namespace="tools:x")])
    await store.replace_calls("th-2", "", [a_call("call-1", thread_id="th-2")])

    await store.delete_call("th-1", "", "call-1")
    assert keys(await store.list_calls("th-1")) == [("", "call-2"), ("tools:x", "call-1")]
    # Deleting what is not there changes nothing.
    await store.delete_call("th-1", "", "call-1")
    await store.delete_call("th-1", "tools:y", "call-2")
    assert len(await store.list_calls("th-1")) == 2

    await store.clear_calls("th-1")
    assert await store.list_calls("th-1") == []
    assert keys(await store.list_calls("th-2")) == [("", "call-1")]
    await store.clear_calls("th-1")


@pytest.mark.parametrize(
    ("checkpoint_ns", "namespace"),
    [
        # On the orchestrator the model's node and the tools' node both give the empty one.
        ("model:0f3a", ""),
        ("tools:77c1", ""),
        ("", ""),
        # In a subagent both give the subagent's own.
        ("tools:9b2e|model:41aa", "tools:9b2e"),
        ("tools:9b2e|tools:c0de", "tools:9b2e"),
        ("tools:9b2e|tools:1111|model:2222", "tools:9b2e|tools:1111"),
    ],
)
def test_a_call_s_namespace_is_what_its_model_and_its_tool_share(
    checkpoint_ns: str, namespace: str
) -> None:
    assert call_namespace(checkpoint_ns) == namespace
