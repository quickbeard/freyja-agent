"""009: the store of conversations and received tasks on a real Postgres (data-model.md).

The rules are the cases of ``tests/test_received_tasks.py``, run here on
``PostgresReceivedTaskStore``. This file adds what only a database shows: the tables as the
data model has them, and two instances that accept at once under the advisory lock.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from freyja_agent.config import Settings
from freyja_agent.runtime.persistence import open_persistence
from freyja_agent.runtime.received_tasks import PostgresReceivedTaskStore
from tests.test_received_tasks import CASES, LIMITS, Case, accept

COLUMNS = {
    "freyja_a2a_conversations": {
        "caller": ("text", "NO", None),
        "context_id": ("text", "NO", None),
        "thread_id": ("text", "NO", None),
        "created_at": ("timestamp with time zone", "NO", None),
    },
    "freyja_a2a_tasks": {
        "task_id": ("text", "NO", None),
        "caller": ("text", "NO", None),
        "context_id": ("text", "NO", None),
        "thread_id": ("text", "NO", None),
        "hops": ("integer", "NO", None),
        "state": ("text", "NO", None),
        "reason": ("text", "YES", None),
        "result": ("text", "YES", None),
        "created_at": ("timestamp with time zone", "NO", None),
        "started_at": ("timestamp with time zone", "YES", None),
        "base_checkpoint": ("text", "YES", None),
        "ended_at": ("timestamp with time zone", "YES", None),
        "released_at": ("timestamp with time zone", "YES", None),
        "reject_attempts": ("integer", "NO", "0"),
        "reject_after": ("timestamp with time zone", "YES", None),
    },
}


@pytest.fixture
async def pool(settings: Settings) -> AsyncIterator[AsyncConnectionPool]:
    persistence = await open_persistence(settings)
    try:
        yield persistence.pool
    finally:
        await persistence.close()


@pytest.fixture
async def store(pool: AsyncConnectionPool) -> PostgresReceivedTaskStore:
    made = PostgresReceivedTaskStore(pool)
    await made.setup()
    return made


@pytest.mark.parametrize("case", CASES, ids=[case.__name__ for case in CASES])
async def test_the_postgres_store(case: Case, store: PostgresReceivedTaskStore) -> None:
    await case(store)


async def test_the_tables_are_the_data_model_s(
    pool: AsyncConnectionPool, store: PostgresReceivedTaskStore
) -> None:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT table_name, column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_name LIKE 'freyja_a2a_%'"
            )
        ).fetchall()
        indexes = await (
            await conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename LIKE 'freyja_a2a_%'"
            )
        ).fetchall()
    found: dict[str, dict[str, tuple[str, str, str | None]]] = {}
    for row in rows:
        found.setdefault(row["table_name"], {})[row["column_name"]] = (
            row["data_type"],
            row["is_nullable"],
            row["column_default"],
        )
    assert found == COLUMNS
    definitions = {row["indexname"]: row["indexdef"] for row in indexes}
    assert "UNIQUE" in definitions["freyja_a2a_conversations_thread_id_key"]
    assert "(caller, context_id)" in definitions["freyja_a2a_conversations_pkey"]
    assert "(task_id)" in definitions["freyja_a2a_tasks_pkey"]
    assert "(caller, context_id, created_at)" in definitions["freyja_a2a_tasks_conversation"]
    assert "(thread_id)" in definitions["freyja_a2a_tasks_thread"]
    assert "WHERE (released_at IS NULL)" in definitions["freyja_a2a_tasks_unreleased"]


async def test_two_instances_accept_under_one_lock(settings: Settings) -> None:
    first = await open_persistence(settings)
    second = await open_persistence(settings)
    try:
        one = PostgresReceivedTaskStore(first.pool)
        two = PostgresReceivedTaskStore(second.pool)
        await one.setup()
        await two.setup()
        both = await asyncio.gather(accept(one, context="new"), accept(two, context="new"))
        assert sorted(str(found.refused) for found in both) == ["None", "busy"]
        limited = await asyncio.gather(
            *(
                accept(store, "alpha", f"x{n}", **{**LIMITS, "caller_limit": 1})
                for n, store in enumerate([one, two, one, two])
            )
        )
        assert [found.refused for found in limited].count(None) == 1
    finally:
        await first.close()
        await second.close()
