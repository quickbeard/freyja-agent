"""003 research R8, R9: the agent task store's contract against a real database.

The tests are the ones in ``tests/test_agent_task_store.py``; only the fixtures differ. Claims
expire by real time here, so ``advance`` waits.
"""

import asyncio
from collections.abc import AsyncIterator

import psycopg
import pytest

from freyja_agent.config import Settings
from freyja_agent.runtime.agent_tasks import AgentTaskStore, PostgresAgentTaskStore
from freyja_agent.runtime.persistence import open_persistence
from tests.test_agent_task_store import (
    Advance,
    a_call,
    a_send,
    a_task,
    test_a_call_is_deleted_alone_and_a_thread_s_calls_are_cleared_together,
    test_a_call_is_stored_under_the_thread_and_namespace_it_is_replaced_for,
    test_a_conversation_is_kept_by_its_first_context,
    test_a_decision_is_recorded_on_the_calls_it_names_and_no_other,
    test_a_pending_task_is_claimed_by_one_instance_until_the_claim_expires,
    test_a_proposed_call_is_kept_as_it_was_proposed,
    test_a_reply_replaces_the_calls_of_its_thread_and_namespace_and_no_other,
    test_a_send_is_recorded_once_and_finished,
    test_a_task_has_no_progress_until_it_is_given_some,
    test_a_task_keeps_the_ref_of_the_agent_it_was_started_with,
    test_a_task_that_does_not_exist_is_left_alone,
    test_an_outcome_is_marked_delivered_once,
    test_an_outcome_is_marked_delivered_only_while_it_is_the_one_that_was_delivered,
    test_an_unknown_column_is_refused,
    test_an_update_can_require_the_state_it_expects,
    test_follow_ups_are_set_and_cleared_per_thread,
    test_nothing_that_ends_or_gives_up_a_task_clears_its_progress,
    test_one_tool_call_id_in_two_namespaces_of_a_thread_is_two_calls,
    test_one_tool_call_id_twice_in_one_reply_keeps_the_later_call,
    test_pending_tasks_are_counted_by_agent_and_ref,
    test_pending_tasks_are_given_up_by_ref,
    test_progress_is_kept_with_the_task_and_read_back_everywhere,
    test_progress_is_set_only_by_an_update_that_names_it,
    test_ready_outcomes_wait_for_delivery_once,
    test_tasks_are_listed_oldest_first_and_never_dropped,
    test_the_oldest_outcome_that_waits_is_as_old_as_the_time_since_it_was_learned,
    test_the_ref_of_a_task_cannot_be_changed,
    test_the_same_tool_call_id_in_another_step_is_another_send,
    test_threads_with_a_due_retry_are_listed_once_it_is_due,
)

# The contract's tests, collected here against Postgres.
__all__ = [
    "test_a_call_is_deleted_alone_and_a_thread_s_calls_are_cleared_together",
    "test_a_call_is_stored_under_the_thread_and_namespace_it_is_replaced_for",
    "test_a_conversation_is_kept_by_its_first_context",
    "test_a_decision_is_recorded_on_the_calls_it_names_and_no_other",
    "test_a_pending_task_is_claimed_by_one_instance_until_the_claim_expires",
    "test_a_proposed_call_is_kept_as_it_was_proposed",
    "test_a_reply_replaces_the_calls_of_its_thread_and_namespace_and_no_other",
    "test_a_send_is_recorded_once_and_finished",
    "test_a_task_has_no_progress_until_it_is_given_some",
    "test_a_task_keeps_the_ref_of_the_agent_it_was_started_with",
    "test_a_task_that_does_not_exist_is_left_alone",
    "test_an_outcome_is_marked_delivered_once",
    "test_an_outcome_is_marked_delivered_only_while_it_is_the_one_that_was_delivered",
    "test_an_unknown_column_is_refused",
    "test_an_update_can_require_the_state_it_expects",
    "test_follow_ups_are_set_and_cleared_per_thread",
    "test_nothing_that_ends_or_gives_up_a_task_clears_its_progress",
    "test_one_tool_call_id_in_two_namespaces_of_a_thread_is_two_calls",
    "test_one_tool_call_id_twice_in_one_reply_keeps_the_later_call",
    "test_pending_tasks_are_counted_by_agent_and_ref",
    "test_pending_tasks_are_given_up_by_ref",
    "test_progress_is_kept_with_the_task_and_read_back_everywhere",
    "test_progress_is_set_only_by_an_update_that_names_it",
    "test_ready_outcomes_wait_for_delivery_once",
    "test_tasks_are_listed_oldest_first_and_never_dropped",
    "test_the_oldest_outcome_that_waits_is_as_old_as_the_time_since_it_was_learned",
    "test_the_ref_of_a_task_cannot_be_changed",
    "test_the_same_tool_call_id_in_another_step_is_another_send",
    "test_threads_with_a_due_retry_are_listed_once_it_is_due",
]


@pytest.fixture
async def store(settings: Settings) -> AsyncIterator[AgentTaskStore]:
    persistence = await open_persistence(settings)
    try:
        store = PostgresAgentTaskStore(persistence.pool)
        yield store
    finally:
        await persistence.close()


@pytest.fixture
def advance() -> Advance:
    async def _advance(seconds: float) -> None:
        await asyncio.sleep(seconds)

    return _advance


# T041: the tables themselves, and two instances on one database.


@pytest.fixture
async def other_store(settings: Settings) -> AsyncIterator[AgentTaskStore]:
    """A second instance's store: its own pool on the same database."""
    persistence = await open_persistence(settings)
    try:
        yield PostgresAgentTaskStore(persistence.pool)
    finally:
        await persistence.close()


TABLES = {
    "freyja_agent_conversations",
    "freyja_agent_sends",
    "freyja_agent_tasks",
    # Feature 005: the proposed calls (research R8).
    "freyja_agent_calls",
}


async def test_the_tables_and_the_index_of_tasks_by_thread_are_there(
    store: AgentTaskStore, settings: Settings
) -> None:
    async with await psycopg.AsyncConnection.connect(settings.database_uri) as conn:
        cursor = await conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            " AND tablename LIKE 'freyja_agent_%'"
        )
        names = {row[0] for row in await cursor.fetchall()}
        cursor = await conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'freyja_agent_tasks'"
        )
        indexes = {row[0] for row in await cursor.fetchall()}
    # Every table of the service is there by now; these four are this store's.
    assert names >= TABLES
    assert "freyja_agent_tasks_thread" in indexes


async def test_two_instances_claiming_at_once_take_each_task_once(
    store: AgentTaskStore, other_store: AgentTaskStore
) -> None:
    for number in range(20):
        await store.record_task(await a_task(store, f"t{number}"))
    first, second = await asyncio.gather(
        store.claim_due("instance-a", 30), other_store.claim_due("instance-b", 30)
    )
    taken = [task.task_id for task in first] + [task.task_id for task in second]
    assert sorted(taken) == sorted(f"t{number}" for number in range(20))
    assert {task.watcher for task in first} <= {"instance-a"}
    assert {task.watcher for task in second} <= {"instance-b"}
    # Nobody else gets them while the claims hold.
    assert await other_store.claim_due("instance-c", 30) == []


async def test_an_expired_claim_passes_to_the_other_instance(
    store: AgentTaskStore, other_store: AgentTaskStore
) -> None:
    await store.record_task(await a_task(store, "t1"))
    assert [t.task_id for t in await store.claim_due("instance-a", 1)] == ["t1"]
    assert await other_store.claim_due("instance-b", 1) == []
    await asyncio.sleep(1.2)
    (taken,) = await other_store.claim_due("instance-b", 1)
    assert taken.watcher == "instance-b"


async def test_sends_and_conversations_outlive_the_pool(
    store: AgentTaskStore, settings: Settings
) -> None:
    await store.set_context("th-1", "stub", "ctx-1")
    await store.start_send(await a_send(store))
    await store.finish_send("th-1", "tools:a", "call-1", result="done", task_id="t1")
    persistence = await open_persistence(settings)
    try:
        again = PostgresAgentTaskStore(persistence.pool)
        assert await again.get_context("th-1", "stub") == "ctx-1"
        send = await again.get_send("th-1", "tools:a", "call-1")
    finally:
        await persistence.close()
    assert send is not None and (send.result, send.task_id) == ("done", "t1")


async def test_proposed_calls_outlive_the_pool_and_are_stamped_by_the_database(
    store: AgentTaskStore, settings: Settings
) -> None:
    before = await store.now()
    await store.replace_calls("th-1", "tools:x", [a_call("call-1", agent_id="0af3", gated=False)])
    persistence = await open_persistence(settings)
    try:
        again = PostgresAgentTaskStore(persistence.pool)
        call = await again.get_call("th-1", "tools:x", "call-1")
    finally:
        await persistence.close()
    assert call is not None
    assert (call.agent, call.agent_id, call.gated) == ("stub", "0af3", False)
    assert call.proposed_at is not None and call.proposed_at >= before


async def test_a_replacement_that_fails_leaves_the_earlier_calls_in_place(
    store: AgentTaskStore,
) -> None:
    await store.replace_calls("th-1", "", [a_call("call-1")])
    # A NUL cannot be stored: the second insert fails, and the delete before it is undone.
    with pytest.raises(psycopg.Error):
        await store.replace_calls("th-1", "", [a_call("call-2"), a_call("bad", agent="a\x00b")])
    assert [call.tool_call_id for call in await store.list_calls("th-1")] == ["call-1"]


async def test_progress_round_trips_through_every_reading_of_a_task(
    store: AgentTaskStore,
) -> None:
    learned = await store.now()
    # Several lines, characters outside ASCII, and 2,000 characters in all.
    text = "Step 2 of 3\nline two \u00e9\U0001f600" + "x" * 1977
    await store.record_task(await a_task(store, "recorded", progress=text, progress_at=learned))
    await store.record_task(await a_task(store, "updated"))
    assert await store.update_task("stub", "updated", progress=text, progress_at=learned)
    claimed = {task.task_id: task for task in await store.claim_due("instance-a", 30)}
    listed = {task.task_id: task for task in await store.list_tasks("th-1")}
    for task_id in ("recorded", "updated"):
        found = await store.get_task("stub", task_id)
        for task in (found, listed[task_id], claimed[task_id]):
            assert task is not None
            assert (task.progress, task.progress_at) == (text, learned)
