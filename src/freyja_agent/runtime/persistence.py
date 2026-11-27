"""Postgres persistence for the service process.

One connection pool is shared by the LangGraph checkpointer, the store, and Freyja's own
stores. It is opened in the FastAPI lifespan and closed on shutdown (FR-008). A database that
cannot be reached at startup raises an error naming the connection, so the process exits
instead of serving requests that would fail. So does a database whose schema cannot be
brought to this build's version (``runtime/migrations.py``).
"""

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from freyja_agent.config import Settings
from freyja_agent.runtime.migrations import migrate

# How long startup waits for the first connection before giving up with a clear error.
CONNECT_TIMEOUT_SECONDS = 10.0
# How long the readiness check waits for a connection from the pool.
PING_TIMEOUT_SECONDS = 2.0


class DatabaseUnavailableError(RuntimeError):
    """Raised at startup when the configured database cannot be reached."""


# The pool as the service opens it and every store is handed it: each connection returns a
# row as a dict, by the ``row_factory`` given below.
Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


async def one_row(cursor: AsyncCursor[DictRow]) -> DictRow:
    """The row of a statement that always returns one, such as a count or a ``RETURNING``
    of a row the transaction holds; a statement that returned none is an error here, where
    it is found, and not at the first use of the row."""
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("A statement that returns one row returned none.")
    return row


@dataclass
class Persistence:
    pool: Pool
    saver: AsyncPostgresSaver
    store: AsyncPostgresStore

    async def close(self) -> None:
        await self.pool.close()

    async def ping(self) -> None:
        """Ask the database one statement that reads nothing; raises when it cannot be
        asked, which is what ``/readyz`` reports."""
        async with self.pool.connection(timeout=PING_TIMEOUT_SECONDS) as connection:
            await connection.execute("SELECT 1")


def redact_uri(uri: str) -> str:
    """Return the URI with any password replaced, for log and error messages."""
    parts = urlsplit(uri)
    if parts.password is None:
        return uri
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    user = f"{parts.username}:***@" if parts.username is not None else "***@"
    return urlunsplit((parts.scheme, user + host, parts.path, parts.query, parts.fragment))


async def open_persistence(settings: Settings) -> Persistence:
    """Open the pool, make the tables, and return the ready components.

    The LangGraph checkpointer and store set up their own tables; Freyja's are brought to
    this build's schema version by ``migrate``. Every store is given the pool after that, so
    none of them makes or changes a table.
    """
    pool: Pool = AsyncConnectionPool(
        settings.database_uri,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
        min_size=1,
        max_size=10,
    )
    try:
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_SECONDS)
    except Exception as exc:
        await pool.close()
        raise DatabaseUnavailableError(
            f"Could not connect to the database at {redact_uri(settings.database_uri)}: {exc}"
        ) from exc
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        store = AsyncPostgresStore(pool)
        await store.setup()
        await migrate(pool)
    except Exception:
        await pool.close()
        raise
    return Persistence(pool=pool, saver=saver, store=store)
