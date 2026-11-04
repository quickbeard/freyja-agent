"""R7, FR-006: the SQL lease against a real database, through the pool the service uses."""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from freyja_agent.config import Settings
from freyja_agent.runtime.locks import PostgresRunLocks
from freyja_agent.runtime.persistence import open_persistence

THREAD = "pg-lock-1"


@pytest.fixture
async def pool(settings: Settings) -> AsyncIterator[AsyncConnectionPool]:
    persistence = await open_persistence(settings)
    try:
        yield persistence.pool
    finally:
        await persistence.close()


@pytest.fixture
async def locks(pool: AsyncConnectionPool) -> PostgresRunLocks:
    locks = PostgresRunLocks(pool, ttl_seconds=60)
    await locks.setup()
    return locks


async def expires_in(pool: AsyncConnectionPool, thread_id: str) -> float:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT EXTRACT(EPOCH FROM expires_at - now()) AS seconds "
            "FROM freyja_run_locks WHERE thread_id = %(thread_id)s",
            {"thread_id": thread_id},
        )
        row = await cursor.fetchone()
    assert row is not None
    return float(row["seconds"])


async def set_expiry(pool: AsyncConnectionPool, thread_id: str, seconds_from_now: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE freyja_run_locks SET expires_at = now() + %(delta)s * interval '1 second' "
            "WHERE thread_id = %(thread_id)s",
            {"delta": seconds_from_now, "thread_id": thread_id},
        )


async def test_acquire_is_exclusive_until_released(locks: PostgresRunLocks) -> None:
    assert await locks.acquire(THREAD, "a:1") is True
    assert await locks.acquire(THREAD, "b:1") is False
    assert await locks.is_active(THREAD) is True
    assert await locks.acquire("pg-lock-other", "b:1") is True

    await locks.release(THREAD, "b:1")  # wrong owner: no-op
    assert await locks.is_active(THREAD) is True
    await locks.release(THREAD, "a:1")
    assert await locks.is_active(THREAD) is False
    assert await locks.acquire(THREAD, "b:1") is True


async def test_renew_moves_expires_at_forward_for_the_owner_only(
    locks: PostgresRunLocks, pool: AsyncConnectionPool
) -> None:
    await locks.acquire(THREAD, "a:1")
    await set_expiry(pool, THREAD, 5)
    assert await expires_in(pool, THREAD) < 6

    await locks.renew(THREAD, "b:1")  # wrong owner: no-op
    assert await expires_in(pool, THREAD) < 6

    await locks.renew(THREAD, "a:1")
    assert await expires_in(pool, THREAD) > 50


async def test_an_expired_row_is_taken_over_by_a_new_owner(
    locks: PostgresRunLocks, pool: AsyncConnectionPool
) -> None:
    await locks.acquire(THREAD, "a:1")
    await set_expiry(pool, THREAD, -1)
    assert await locks.is_active(THREAD) is False

    assert await locks.acquire(THREAD, "b:1") is True
    assert await locks.is_active(THREAD) is True
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT owner FROM freyja_run_locks WHERE thread_id = %(thread_id)s",
            {"thread_id": THREAD},
        )
        row = await cursor.fetchone()
    assert row is not None and row["owner"] == "b:1"
    # The old owner's late release must not clear the new lease.
    await locks.release(THREAD, "a:1")
    assert await locks.is_active(THREAD) is True
