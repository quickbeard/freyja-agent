"""The schema's migrations against a real Postgres (``runtime/migrations.py``).

What only a database shows: a new database gets every table and its version, a database
that has the schema is left alone, a deployment older than the migrations is taken through
the first one with its rows, two instances that start together apply a migration once, and
a database this build cannot explain, or a table it cannot get, stops the start with nothing
changed. The rules that need no database are in ``tests/test_migrations.py``.
"""

import asyncio
import time
from collections.abc import AsyncIterator

import psycopg
import pytest
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from freyja_agent.config import Settings
from freyja_agent.llm.store import PostgresLlmProfileStore
from freyja_agent.runtime import migrations
from freyja_agent.runtime.agent_tasks import PostgresAgentTaskStore
from freyja_agent.runtime.locks import PostgresRunLocks
from freyja_agent.runtime.migrations import (
    MIGRATIONS,
    Migration,
    SchemaBusyError,
    SchemaError,
    SchemaMismatchError,
    SchemaNewerError,
    checksum,
    migrate,
)
from freyja_agent.runtime.persistence import Pool, open_persistence
from freyja_agent.runtime.saved_agents import PostgresSavedAgentStore
from freyja_agent.runtime.thread_index import PostgresThreadIndex

TABLES = {
    "freyja_run_locks",
    "freyja_agent_conversations",
    "freyja_agent_sends",
    "freyja_agent_tasks",
    "freyja_agent_calls",
    "freyja_llm_profiles",
    "freyja_llm_state",
    "freyja_agents",
    "freyja_agents_state",
    "freyja_threads",
    "freyja_a2a_conversations",
    "freyja_a2a_tasks",
    "freyja_schema_migrations",
}
OWN_TABLES = (
    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'freyja%'"
)
# Semgrep's rule for SQLAlchemy takes a statement composed with psycopg's ``sql`` module for
# one built from strings, so the two below that name a table are marked ``nosemgrep`` for that
# rule. The table is an identifier from ``TABLES``.
ROWS = "SELECT version, name, checksum, applied_at FROM freyja_schema_migrations ORDER BY version"


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[psycopg.AsyncConnection]:
    """A connection of the test's own, beside the service's pool."""
    async with await psycopg.AsyncConnection.connect(
        settings.database_uri, autocommit=True
    ) as conn:
        yield conn


async def start(settings: Settings) -> None:
    """One start of the service's persistence, and its end."""
    await (await open_persistence(settings)).close()


async def tables(db: psycopg.AsyncConnection) -> set[str]:
    return {row[0] for row in await (await db.execute(OWN_TABLES)).fetchall()}


async def rows(db: psycopg.AsyncConnection) -> list[tuple]:
    return await (await db.execute(ROWS)).fetchall()


async def has_column(db: psycopg.AsyncConnection, table: str, column: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return await cursor.fetchone() is not None


# A new database, and one that has the schema.


async def test_a_new_database_gets_every_table_and_the_build_s_version(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    assert await tables(db) == set()
    await start(settings)
    assert await tables(db) == TABLES
    assert [row[:3] for row in await rows(db)] == [
        (migration.version, migration.name, checksum(migration)) for migration in MIGRATIONS
    ]


async def test_a_second_start_applies_nothing_and_keeps_what_the_stores_wrote(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    first = await open_persistence(settings)
    try:
        assert await PostgresRunLocks(first.pool, ttl_seconds=60).acquire("t-1", "run-a")
        await PostgresThreadIndex(first.pool).run_started("t-1", "run-a")
        agents = PostgresSavedAgentStore(first.pool)
        profiles = PostgresLlmProfileStore(first.pool)
        before = (await agents.revision(), await profiles.state())
    finally:
        await first.close()
    applied = await rows(db)

    second = await open_persistence(settings)
    try:
        assert await rows(db) == applied  # the same rows, with the time they were applied at
        assert not await PostgresRunLocks(second.pool, ttl_seconds=60).acquire("t-1", "run-b")
        row = await PostgresThreadIndex(second.pool).get("t-1")
        assert row is not None and row.run_owner == "run-a"
        assert (
            await PostgresSavedAgentStore(second.pool).revision(),
            await PostgresLlmProfileStore(second.pool).state(),
        ) == before
    finally:
        await second.close()


async def test_a_start_that_applies_nothing_waits_for_no_table(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    """A session that holds every table for itself, as a migration by hand or a restore
    would: a start that has nothing to apply asks for none of them."""
    await start(settings)
    async with await psycopg.AsyncConnection.connect(settings.database_uri) as holder:
        for table in sorted(TABLES - {"freyja_schema_migrations"}):
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            await holder.execute(
                sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(sql.Identifier(table))
            )
        await asyncio.wait_for(start(settings), timeout=5)
        await holder.rollback()


async def test_the_lock_timeout_is_set_for_a_migration_and_not_for_the_connection(
    settings: Settings,
) -> None:
    persistence = await open_persistence(settings)
    try:
        async with persistence.pool.connection() as conn:
            row = await (await conn.execute("SHOW lock_timeout")).fetchone()
    finally:
        await persistence.close()
    assert row == {"lock_timeout": "0"}


# A deployment older than the migrations: it has the tables and no row of any migration.


# Every version the build's list holds, in order.
VERSIONS = [migration.version for migration in MIGRATIONS]


async def the_first_migration(settings: Settings) -> None:
    """Apply the first migration alone: the schema a deployment had before migrations."""
    async with AsyncConnectionPool(settings.database_uri, open=False) as pool:
        await migrate(pool, MIGRATIONS[:1])


async def a_deployment_before_migrations(settings: Settings, db: psycopg.AsyncConnection) -> None:
    """The schema as the stores made it at every start, which is the first migration's,
    with a row in a table or two, and no row of any migration."""
    await the_first_migration(settings)
    await db.execute("DROP TABLE freyja_schema_migrations")
    await db.execute(
        "INSERT INTO freyja_threads (thread_id, created_at, changed_at, run_owner, runs)"
        " VALUES ('t-old', now(), now(), 'run-old', 1)"
    )
    await db.execute("UPDATE freyja_agents_state SET revision = 7 WHERE id = 1")


async def test_a_deployment_before_migrations_is_taken_through_the_first_one_with_its_rows(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    await a_deployment_before_migrations(settings, db)
    persistence = await open_persistence(settings)
    try:
        assert [row[0] for row in await rows(db)] == VERSIONS
        old = await PostgresThreadIndex(persistence.pool).get("t-old")
        assert old is not None and (old.run_owner, old.runs) == ("run-old", 1)
        # The row every state table is seeded with was there, and was not made again.
        assert await PostgresSavedAgentStore(persistence.pool).revision() == 7
    finally:
        await persistence.close()
    cursor = await db.execute("SELECT count(*) FROM freyja_agents_state")
    assert await cursor.fetchone() == (1,)


async def test_a_deployment_that_has_every_column_does_not_wait_behind_a_reader(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    """A long reader, such as a backup, holds the tables. The first migration finds every
    table, index, and column there and asks for nothing a reader keeps it from. (A later
    migration that adds a column needs its table to itself, and waits behind such a reader as
    the last test of this part shows.)"""
    await a_deployment_before_migrations(settings, db)
    async with await psycopg.AsyncConnection.connect(settings.database_uri) as reader:
        for table in sorted(TABLES - {"freyja_schema_migrations"}):
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            await reader.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        # The reader's transaction is open and holds its lock on every table.
        await asyncio.wait_for(the_first_migration(settings), timeout=5)
        await reader.rollback()
    assert [row[0] for row in await rows(db)] == [1]


async def test_the_agents_mode_is_added_as_auto_and_takes_no_other_value(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    """Migration 2 (feature 010): a database at the first migration gains the agents' mode,
    ``auto``, so a deployment behaves as it did; the revision it had is kept."""
    await the_first_migration(settings)
    await db.execute("UPDATE freyja_agents_state SET revision = 7 WHERE id = 1")
    assert not await has_column(db, "freyja_agents_state", "mode")
    persistence = await open_persistence(settings)
    try:
        agents = PostgresSavedAgentStore(persistence.pool)
        assert (await agents.mode(), await agents.revision()) == ("auto", 7)
    finally:
        await persistence.close()
    assert [row[0] for row in await rows(db)] == VERSIONS
    with pytest.raises(psycopg.errors.CheckViolation):
        await db.execute("UPDATE freyja_agents_state SET mode = 'sometimes' WHERE id = 1")


# ``freyja_agent_tasks`` as feature 003 created it: without ``agent_ref`` (005 research R2)
# and without the kept progress (008 research R2).
TASKS_OF_003 = """
    CREATE TABLE freyja_agent_tasks (
        agent             text NOT NULL,
        task_id           text NOT NULL,
        thread_id         text NOT NULL,
        context_id        text NOT NULL,
        message           text NOT NULL,
        state             text NOT NULL,
        started_at        timestamptz NOT NULL,
        last_checked_at   timestamptz NOT NULL,
        ended_at          timestamptz,
        outcome           text,
        outcome_ready     boolean NOT NULL DEFAULT false,
        delivered_at      timestamptz,
        watcher           text,
        watch_until       timestamptz,
        followup_attempts integer NOT NULL DEFAULT 0,
        followup_next_at  timestamptz,
        followup_error    text,
        PRIMARY KEY (agent, task_id)
    )
"""
A_TASK_OF_003 = (
    "INSERT INTO freyja_agent_tasks (agent, task_id, thread_id, context_id, message, state,"
    " started_at, last_checked_at) VALUES ('stub', 'old', 'th-1', 'ctx-1', 'm', 'working',"
    " now(), now())"
)
# The table of proposed calls as 005's fifth phase created it: without ``decided``.
CALLS_BEFORE_DECIDED = """
    CREATE TABLE freyja_agent_calls (
        thread_id    text NOT NULL,
        namespace    text NOT NULL,
        tool_call_id text NOT NULL,
        agent        text NOT NULL,
        agent_id     text,
        gated        boolean NOT NULL,
        proposed_at  timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (thread_id, namespace, tool_call_id)
    )
"""
A_CALL_BEFORE_DECIDED = (
    "INSERT INTO freyja_agent_calls (thread_id, namespace, tool_call_id, agent, agent_id, gated)"
    " VALUES ('th-1', '', 'call-1', 'stub', NULL, true)"
)
ADDED = [
    ("freyja_agent_tasks", "agent_ref"),
    ("freyja_agent_tasks", "progress"),
    ("freyja_agent_tasks", "progress_at"),
    ("freyja_agent_calls", "decided"),
]


async def test_the_columns_later_features_added_are_added_and_earlier_rows_read_without_them(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    """005 research R2, 005 FR-022, 008 FR-021: a task recorded before a column existed has
    no ref and no kept progress, and a call proposed before ``decided`` was decided by nobody
    the store knows of, so no gated tool sends it."""
    await db.execute(TASKS_OF_003)
    await db.execute(A_TASK_OF_003)
    await db.execute(CALLS_BEFORE_DECIDED)
    await db.execute(A_CALL_BEFORE_DECIDED)
    assert [await has_column(db, *added) for added in ADDED] == [False] * 4

    persistence = await open_persistence(settings)
    try:
        assert [await has_column(db, *added) for added in ADDED] == [True] * 4
        cursor = await db.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'freyja_agent_tasks'"
            " AND column_name IN ('agent_ref', 'progress', 'progress_at') ORDER BY column_name"
        )
        assert await cursor.fetchall() == [
            ("agent_ref", "text", "YES"),
            ("progress", "text", "YES"),
            ("progress_at", "timestamp with time zone", "YES"),
        ]
        store = PostgresAgentTaskStore(persistence.pool)
        old = await store.get_task("stub", "old")
        assert old is not None and old.state == "working"
        assert (old.agent_ref, old.progress, old.progress_at) == (None, None, None)
        earlier = await store.get_call("th-1", "", "call-1")
        assert earlier is not None and earlier.decided is False
        # And the store works on both tables from here on.
        assert await store.pending_counts() == {("stub", None): 1}
        learned = await store.now()
        assert await store.update_task(
            "stub", "old", when_state=("working",), progress="Half done", progress_at=learned
        )
        kept = await store.get_task("stub", "old")
        assert kept is not None and (kept.progress, kept.progress_at) == ("Half done", learned)
        await store.mark_decided("th-1", "", ["call-1"])
        marked = await store.get_call("th-1", "", "call-1")
        assert marked is not None and marked.decided is True
    finally:
        await persistence.close()
    # A second start changes nothing.
    await start(settings)
    assert [row[0] for row in await rows(db)] == VERSIONS


async def test_a_column_that_cannot_be_added_within_the_lock_timeout_stops_the_start(
    settings: Settings, db: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding a column needs the table to itself, and waits behind any reader of it. The
    start gives up at the timeout, says which statement waited, and changes nothing."""
    await db.execute(TASKS_OF_003)
    await db.execute(A_TASK_OF_003)
    monkeypatch.setattr(migrations, "LOCK_TIMEOUT", "1s")
    async with await psycopg.AsyncConnection.connect(settings.database_uri) as reader:
        await reader.execute("SELECT count(*) FROM freyja_agent_tasks")
        began = time.monotonic()
        with pytest.raises(SchemaBusyError) as refused:
            await start(settings)
        assert 0.8 < time.monotonic() - began < 5
        await reader.rollback()
    message = str(refused.value)
    assert "Migration 1 (the schema before migrations)" in message
    assert "within 1s" in message
    assert "ALTER TABLE freyja_agent_tasks ADD COLUMN agent_ref text" in message
    assert refused.value.__cause__ is None
    # Nothing of the migration was applied: no column, no other table, no row of a version.
    assert not await has_column(db, "freyja_agent_tasks", "agent_ref")
    assert await tables(db) == {"freyja_agent_tasks"}
    # And the next start, with the reader gone, applies it whole.
    await start(settings)
    assert await has_column(db, "freyja_agent_tasks", "agent_ref")
    assert await tables(db) == TABLES


# A database this build cannot explain.


async def test_a_database_a_newer_build_migrated_stops_the_start_and_is_left_as_it_is(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    await start(settings)
    newer = len(MIGRATIONS) + 1
    await db.execute(
        "INSERT INTO freyja_schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
        (newer, "a newer build's", "0" * 64),
    )
    before = await rows(db)
    with pytest.raises(SchemaNewerError) as refused:
        await start(settings)
    assert f"schema version {newer}" in str(refused.value)
    assert await rows(db) == before


async def test_a_migration_applied_as_another_text_stops_the_start(
    settings: Settings, db: psycopg.AsyncConnection
) -> None:
    await start(settings)
    await db.execute("UPDATE freyja_schema_migrations SET checksum = %s", ("0" * 64,))
    with pytest.raises(SchemaMismatchError) as refused:
        await start(settings)
    assert "Migration 1 (the schema before migrations)" in str(refused.value)


# A list of the test's own, to see a migration fail and two instances meet.


@pytest.fixture
async def pools(settings: Settings) -> AsyncIterator[list[Pool]]:
    """Two pools on one database: two instances of the service."""
    opened = [await open_persistence(settings), await open_persistence(settings)]
    try:
        yield [persistence.pool for persistence in opened]
    finally:
        for persistence in opened:
            await persistence.close()


def and_then(*added: Migration) -> list[Migration]:
    """The build's migrations and more after them, numbered on."""
    return [
        *MIGRATIONS,
        *(
            Migration(len(MIGRATIONS) + n, migration.name, migration.statements)
            for n, migration in enumerate(added, start=1)
        ),
    ]


NOTES = Migration(0, "notes", ("CREATE TABLE freyja_test_notes (note text PRIMARY KEY)",))


async def test_two_instances_that_start_together_apply_a_migration_once(
    pools: list[Pool], db: psycopg.AsyncConnection
) -> None:
    """The statement is not one that can run twice, and it is slow enough for the two to
    meet: the second waits for the first and finds the migration applied."""
    slow = Migration(
        0, "slow", ("SELECT pg_sleep(0.5)", "CREATE TABLE freyja_test_slow (id integer)")
    )
    listed = and_then(slow)
    began = time.monotonic()
    versions = await asyncio.gather(migrate(pools[0], listed), migrate(pools[1], listed))
    assert versions == [len(listed), len(listed)]
    assert 0.5 <= time.monotonic() - began < 5
    assert [row[1] for row in await rows(db)][-1:] == ["slow"]
    assert len(await rows(db)) == len(listed)


async def test_a_migration_that_fails_is_not_applied_in_part_and_the_one_before_it_stays(
    pools: list[Pool], db: psycopg.AsyncConnection
) -> None:
    broken = Migration(
        0,
        "broken",
        (
            "CREATE TABLE freyja_test_half (id integer)",
            "ALTER TABLE freyja_test_missing ADD x text",
        ),
    )
    with pytest.raises(SchemaError) as refused:
        await migrate(pools[0], and_then(NOTES, broken))
    message = str(refused.value)
    assert f"Migration {len(MIGRATIONS) + 2} (broken) failed" in message
    assert "UndefinedTable" in message and "SQLSTATE 42P01" in message
    assert "ALTER TABLE freyja_test_missing ADD x text" in message
    # The migration before it was applied whole, and nothing of the broken one was.
    assert [row[1] for row in await rows(db)][-1] == "notes"
    assert "freyja_test_notes" in await tables(db)
    assert "freyja_test_half" not in await tables(db)
    # Mended, it is applied by the next start.
    mended = Migration(0, "broken", ("CREATE TABLE freyja_test_half (id integer)",))
    assert await migrate(pools[1], and_then(NOTES, mended)) == len(MIGRATIONS) + 2
    assert "freyja_test_half" in await tables(db)


async def test_a_failed_migration_s_error_names_the_constraint_and_repeats_no_row_s_value(
    pools: list[Pool], db: psycopg.AsyncConnection
) -> None:
    """The database's own text for a unique violation repeats the value, which may be an
    agent's address. The error says the kind, the table, and the constraint."""
    await migrate(pools[0], and_then(NOTES))
    await db.execute("INSERT INTO freyja_test_notes VALUES ('http://127.0.0.1:59123/agent')")
    await db.execute("CREATE TABLE freyja_test_copies (note text CONSTRAINT one_copy UNIQUE)")
    copy = (
        "INSERT INTO freyja_test_copies SELECT note FROM freyja_test_notes"
        " UNION ALL SELECT note FROM freyja_test_notes"
    )
    with pytest.raises(SchemaError) as refused:
        await migrate(pools[0], and_then(NOTES, Migration(0, "copy", (copy,))))
    message = str(refused.value)
    assert "UniqueViolation" in message
    assert "table freyja_test_copies" in message and "constraint one_copy" in message
    assert "59123" not in message and "127.0.0.1" not in message
    assert refused.value.__cause__ is None and refused.value.__suppress_context__ is True
