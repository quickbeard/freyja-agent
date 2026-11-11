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
    a_send,
    a_task,
    test_a_conversation_is_kept_by_its_first_context,
    test_a_pending_task_is_claimed_by_one_instance_until_the_claim_expires,
    test_a_send_is_recorded_once_and_finished,
    test_an_unknown_column_is_refused,
    test_an_update_can_require_the_state_it_expects,
    test_follow_ups_are_set_and_cleared_per_thread,
    test_ready_outcomes_wait_for_delivery_once,
    test_tasks_are_listed_oldest_first_and_never_dropped,
    test_the_same_tool_call_id_in_another_step_is_another_send,
    test_threads_with_a_due_retry_are_listed_once_it_is_due,
)

# The contract's tests, collected here against Postgres.
__all__ = [
    "test_a_conversation_is_kept_by_its_first_context",
    "test_a_pending_task_is_claimed_by_one_instance_until_the_claim_expires",
    "test_a_send_is_recorded_once_and_finished",
    "test_an_unknown_column_is_refused",
    "test_an_update_can_require_the_state_it_expects",
    "test_follow_ups_are_set_and_cleared_per_thread",
    "test_ready_outcomes_wait_for_delivery_once",
    "test_tasks_are_listed_oldest_first_and_never_dropped",
    "test_the_same_tool_call_id_in_another_step_is_another_send",
    "test_threads_with_a_due_retry_are_listed_once_it_is_due",
]


@pytest.fixture
async def store(settings: Settings) -> AsyncIterator[AgentTaskStore]:
    persistence = await open_persistence(settings)
    try:
        store = PostgresAgentTaskStore(persistence.pool)
        await store.setup()
        await store.setup()  # idempotent
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


TABLES = {"freyja_agent_conversations", "freyja_agent_sends", "freyja_agent_tasks"}


async def test_the_three_tables_are_created_once_and_again_without_harm(
    store: AgentTaskStore, settings: Settings
) -> None:
    await store.setup()
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
    assert names == TABLES
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
