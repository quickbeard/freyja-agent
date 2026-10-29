"""One active run per thread, enforced as an expiring lease.

The lease lives in the shared database so the rule holds across every service instance
(FR-006). A run holds the lease while it streams, a heartbeat renews it, and ``release`` drops
it on every exit path. A lease whose owner died is never released explicitly; it expires.

Two implementations share one protocol: ``PostgresRunLocks`` for the service and
``InMemoryRunLocks`` with an injectable clock for tests (constitution principle III: the
routes and the stream loop are identical; only the lease store is swapped).
"""

import time
from collections.abc import Callable
from typing import Protocol

from psycopg_pool import AsyncConnectionPool


class RunLocks(Protocol):
    """Lease operations. ``owner`` identifies one run on one instance."""

    async def setup(self) -> None: ...

    async def acquire(self, thread_id: str, owner: str) -> bool: ...

    async def renew(self, thread_id: str, owner: str) -> None: ...

    async def release(self, thread_id: str, owner: str) -> None: ...

    async def is_active(self, thread_id: str) -> bool: ...


class InMemoryRunLocks:
    """Lease store for tests. ``clock`` returns seconds; tests inject one they can advance."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._rows: dict[str, tuple[str, float]] = {}

    async def setup(self) -> None:
        return None

    async def acquire(self, thread_id: str, owner: str) -> bool:
        now = self._clock()
        row = self._rows.get(thread_id)
        if row is not None and row[1] >= now:
            return False
        self._rows[thread_id] = (owner, now + self.ttl_seconds)
        return True

    async def renew(self, thread_id: str, owner: str) -> None:
        row = self._rows.get(thread_id)
        if row is not None and row[0] == owner:
            self._rows[thread_id] = (owner, self._clock() + self.ttl_seconds)

    async def release(self, thread_id: str, owner: str) -> None:
        row = self._rows.get(thread_id)
        if row is not None and row[0] == owner:
            del self._rows[thread_id]

    async def is_active(self, thread_id: str) -> bool:
        row = self._rows.get(thread_id)
        return row is not None and row[1] > self._clock()


# Every statement is a single atomic command, so concurrent instances cannot interleave inside
# one operation. Parameters are bound by psycopg; no values are formatted into the SQL.
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS freyja_run_locks (
    thread_id   text PRIMARY KEY,
    owner       text NOT NULL,
    acquired_at timestamptz NOT NULL,
    expires_at  timestamptz NOT NULL
)
"""

_ACQUIRE = """
INSERT INTO freyja_run_locks (thread_id, owner, acquired_at, expires_at)
VALUES (%(thread_id)s, %(owner)s, now(), now() + %(ttl)s * interval '1 second')
ON CONFLICT (thread_id) DO UPDATE
    SET owner = EXCLUDED.owner,
        acquired_at = EXCLUDED.acquired_at,
        expires_at = EXCLUDED.expires_at
    WHERE freyja_run_locks.expires_at < now()
RETURNING owner
"""

_RENEW = """
UPDATE freyja_run_locks
   SET expires_at = now() + %(ttl)s * interval '1 second'
 WHERE thread_id = %(thread_id)s AND owner = %(owner)s
"""

_RELEASE = """
DELETE FROM freyja_run_locks
 WHERE thread_id = %(thread_id)s AND owner = %(owner)s
"""

_IS_ACTIVE = """
SELECT EXISTS (
    SELECT 1 FROM freyja_run_locks
     WHERE thread_id = %(thread_id)s AND expires_at > now()
) AS active
"""


class PostgresRunLocks:
    """Lease store backed by the ``freyja_run_locks`` table through the shared pool."""

    def __init__(self, pool: AsyncConnectionPool, ttl_seconds: float) -> None:
        self._pool = pool
        self.ttl_seconds = float(ttl_seconds)

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(_CREATE_TABLE)

    async def acquire(self, thread_id: str, owner: str) -> bool:
        params = {"thread_id": thread_id, "owner": owner, "ttl": self.ttl_seconds}
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_ACQUIRE, params)
            return await cursor.fetchone() is not None

    async def renew(self, thread_id: str, owner: str) -> None:
        params = {"thread_id": thread_id, "owner": owner, "ttl": self.ttl_seconds}
        async with self._pool.connection() as conn:
            await conn.execute(_RENEW, params)

    async def release(self, thread_id: str, owner: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(_RELEASE, {"thread_id": thread_id, "owner": owner})

    async def is_active(self, thread_id: str) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_IS_ACTIVE, {"thread_id": thread_id})
            row = await cursor.fetchone()
            return bool(row and row["active"])
