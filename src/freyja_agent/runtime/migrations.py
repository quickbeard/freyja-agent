"""The schema of Freyja's own tables, and the one way it changes.

The schema is a list of migrations, ``MIGRATIONS``, applied in order when the service
starts (``migrate``, called by ``open_persistence``). The table ``freyja_schema_migrations``
keeps one row for each migration a database has had, so a database says which version it is
at, and a start that finds nothing to apply changes nothing and takes no lock on any other
table. The LangGraph checkpointer and store keep their own tables and their own versions in
the same way; this module leaves them alone.

The rules, each of which ``pending`` or ``migrate`` enforces:

- A schema change is a new migration at the end of the list. A migration that was released
  is never edited: a database keeps the checksum of each migration as it was applied, and a
  start refuses a database whose checksum differs from this build's.
- Migrations only go forward. There is no way down: a change that has to be undone is
  undone by another migration, or by restoring a backup taken before it.
- A database a newer build migrated is refused, so that a build is never run against a
  schema it does not know.
- Each migration is one transaction. It is applied whole or not at all, and a start that
  fails leaves the database at the last version that was applied whole.
- Two instances that start together take turns: every start holds one advisory lock while
  it reads the version and applies a migration.
- A migration waits ``LOCK_TIMEOUT`` at most for a table another session holds, and then
  stops the start with an error that names the statement, instead of waiting behind a long
  reader with every query on the table waiting behind it in turn.

So that an older build still works while a newer one is rolled out, a migration adds and
does not take away: a new column is nullable or has a default, and a column or a table is
dropped only by a later release than the one that stopped using it.
"""

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg
from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# How long a migration waits for a table another session holds, in Postgres's own notation.
LOCK_TIMEOUT = "5s"
# The transaction-level advisory lock every start holds while it reads and changes the schema.
_SCHEMA_LOCK = 0x4652534348  # "FRSCH"
# How much of a statement an error repeats. A statement is this file's own text, never a value.
_STATEMENT_SHOWN = 300


class SchemaError(RuntimeError):
    """The database's schema could not be brought to this build's version. Stops startup."""


class SchemaNewerError(SchemaError):
    """The database has a migration this build does not know: a newer build migrated it."""


class SchemaMismatchError(SchemaError):
    """The database's migrations are not the first ones of this build's list, as applied."""


class SchemaBusyError(SchemaError):
    """A migration could not get a table within ``LOCK_TIMEOUT``. Nothing of it was applied."""


@dataclass(frozen=True)
class Migration:
    """One step of the schema: statements that run in order, in one transaction.

    ``statements`` are fixed text, written out in this file. A statement that cannot run
    inside a transaction, such as ``CREATE INDEX CONCURRENTLY``, cannot be a migration.
    """

    version: int
    name: str
    statements: tuple[LiteralString, ...]


def checksum(migration: Migration) -> str:
    """What a database keeps of a migration it was given: a hash of its statements.

    Runs of whitespace count as one space, so a statement can be indented again without
    becoming another migration. Any other change, a comment included, is one.
    """
    text = "\n".join(" ".join(statement.split()) for statement in migration.statements)
    return hashlib.sha256(text.encode()).hexdigest()


def checked(migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    """The list as a tuple, once its versions are known to be 1, 2, 3 and so on in order."""
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(
            f"Migrations are numbered from 1 with no gap, in the order they are applied; "
            f"this list is numbered {versions}."
        )
    return tuple(migrations)


def pending(applied: Mapping[int, str], migrations: Sequence[Migration]) -> list[Migration]:
    """The migrations a database still needs, given the checksum of each one it has had.

    Raises when the database cannot be explained by this build's list: it has a migration
    the list lacks, one of its migrations was applied as another text, or it skipped one.
    """
    known = {migration.version: migration for migration in migrations}
    newer = sorted(version for version in applied if version not in known)
    if newer:
        raise SchemaNewerError(
            f"The database is at schema version {newer[-1]} and this build knows "
            f"{len(known)}: a newer build of the service migrated it. Run that build, or "
            "point this one at another database."
        )
    for version in sorted(applied):
        if applied[version] != checksum(known[version]):
            raise SchemaMismatchError(
                f"Migration {version} ({known[version].name}) was applied to this database "
                "as another text than this build has. A migration is never edited once it "
                "is released; a change to the schema is a new migration."
            )
    newest = max(applied, default=0)
    skipped = [version for version in known if version < newest and version not in applied]
    if skipped:
        raise SchemaMismatchError(
            f"The database has migration {newest} and not migration {skipped[0]} "
            f"({known[skipped[0]].name}), which comes before it. Migrations are applied in "
            "order; one that is added later takes the next number."
        )
    return [migration for migration in migrations if migration.version not in applied]


_LOCK = "SELECT pg_advisory_xact_lock(%(key)s)"
_CREATE_VERSIONS = """
CREATE TABLE IF NOT EXISTS freyja_schema_migrations (
    version    integer PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""
_APPLIED = "SELECT version, checksum FROM freyja_schema_migrations"
# For the transaction it is set in, as SET LOCAL is, and with the value as a parameter.
_SET_LOCK_TIMEOUT = "SELECT set_config('lock_timeout', %(timeout)s, true)"
_RECORD = (
    "INSERT INTO freyja_schema_migrations (version, name, checksum)"
    " VALUES (%(version)s, %(name)s, %(checksum)s)"
)


def _shown(statement: str) -> str:
    """A statement on one line, cut, for an error."""
    return " ".join(statement.split())[:_STATEMENT_SHOWN]


def _named(exc: psycopg.Error) -> str:
    """An error of the database by its kind, its code, and the names it gives: no row's value."""
    parts = [type(exc).__name__]
    if exc.sqlstate:
        parts.append(f"SQLSTATE {exc.sqlstate}")
    for label, name in (
        ("table", exc.diag.table_name),
        ("column", exc.diag.column_name),
        ("constraint", exc.diag.constraint_name),
    ):
        if name:
            parts.append(f"{label} {name}")
    return ", ".join(parts)


async def migrate(
    pool: AsyncConnectionPool[Any], migrations: Sequence[Migration] | None = None
) -> int:
    """Apply every migration the database lacks, in order, and return its version.

    Each round takes the advisory lock, reads what the database has had, and applies the
    next migration with its row, all in one transaction, so an instance that starts beside
    this one waits for the round to end and then finds the migration applied. A database
    that needs nothing gets one short transaction that changes nothing.

    The pool may hand out connections with any row factory: the one reading here names its own.
    """
    migrations = checked(MIGRATIONS if migrations is None else migrations)
    async with pool.connection() as conn:
        while True:
            statement = ""
            migration = None
            try:
                async with conn.transaction():
                    await conn.execute(_LOCK, {"key": _SCHEMA_LOCK})
                    await conn.execute(_CREATE_VERSIONS)
                    async with conn.cursor(row_factory=dict_row) as cursor:
                        await cursor.execute(_APPLIED)
                        applied = {
                            row["version"]: row["checksum"] for row in await cursor.fetchall()
                        }
                    todo = pending(applied, migrations)
                    if not todo:
                        return len(migrations)
                    migration = todo[0]
                    await conn.execute(_SET_LOCK_TIMEOUT, {"timeout": LOCK_TIMEOUT})
                    for statement in migration.statements:
                        await conn.execute(statement)
                    await conn.execute(
                        _RECORD,
                        {
                            "version": migration.version,
                            "name": migration.name,
                            "checksum": checksum(migration),
                        },
                    )
            except errors.LockNotAvailable:
                if migration is None:  # not one of a migration's statements: not ours to word
                    raise
                raise SchemaBusyError(
                    f"Migration {migration.version} ({migration.name}) could not change the "
                    f"schema within {LOCK_TIMEOUT}: another session is holding a table it "
                    f"needs. Nothing of the migration was applied. Start the service again "
                    f"once that session has ended. The statement that waited: {_shown(statement)}"
                ) from None
            except psycopg.Error as exc:
                if migration is None:
                    raise
                # The database's own text may repeat a value of a row, so it is left out and
                # the error is not chained: what is said is the kind of error and the names
                # the database gives for it.
                raise SchemaError(
                    f"Migration {migration.version} ({migration.name}) failed and nothing of "
                    f"it was applied: {_named(exc)}. The statement that failed: "
                    f"{_shown(statement)}"
                ) from None
            logger.info("Schema: applied migration %d (%s)", migration.version, migration.name)


# A column added to a table that exists in deployments, as migration 1 adds it: looked for
# first, and added only when it is missing. ALTER TABLE takes the table for itself even when
# it then finds the column there, and would wait behind any reader of the table.
_ADD_AGENT_REF = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'freyja_agent_tasks' AND column_name = 'agent_ref'
    ) THEN
        ALTER TABLE freyja_agent_tasks ADD COLUMN agent_ref text;
    END IF;
END
$$
"""
_ADD_DECIDED = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'freyja_agent_calls' AND column_name = 'decided'
    ) THEN
        ALTER TABLE freyja_agent_calls ADD COLUMN decided boolean NOT NULL DEFAULT false;
    END IF;
END
$$
"""
_ADD_PROGRESS = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'freyja_agent_tasks' AND column_name = 'progress'
    ) THEN
        ALTER TABLE freyja_agent_tasks ADD COLUMN progress text;
    END IF;
END
$$
"""
_ADD_PROGRESS_AT = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'freyja_agent_tasks' AND column_name = 'progress_at'
    ) THEN
        ALTER TABLE freyja_agent_tasks ADD COLUMN progress_at timestamptz;
    END IF;
END
$$
"""

# Migration 1 is the schema as the stores made it at every start before there were
# migrations (features 001 to 009, request 3). It is written so that it can be applied to a
# database that already has some or all of it, which every deployment older than this
# module does: a table and an index are made only when missing, and so are the four columns
# that features 005 and 008 added to tables that existed. The statements are in the order
# the stores ran them in. Every later migration is applied to a known schema, once, and
# needs none of this care.
_BASELINE = Migration(
    version=1,
    name="the schema before migrations",
    statements=(
        # The per-thread run lease (feature 001).
        """
        CREATE TABLE IF NOT EXISTS freyja_run_locks (
            thread_id   text PRIMARY KEY,
            owner       text NOT NULL,
            acquired_at timestamptz NOT NULL,
            expires_at  timestamptz NOT NULL
        )
        """,
        # Agent conversations, the send ledger, and agent tasks (feature 003).
        """
        CREATE TABLE IF NOT EXISTS freyja_agent_conversations (
            thread_id  text NOT NULL,
            agent      text NOT NULL,
            context_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (thread_id, agent)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS freyja_agent_sends (
            thread_id    text NOT NULL,
            step         text NOT NULL,
            tool_call_id text NOT NULL,
            agent        text NOT NULL,
            kind         text NOT NULL,
            message_id   text NOT NULL,
            request      text NOT NULL,
            task_id      text,
            sent_at      timestamptz NOT NULL,
            result       text,
            PRIMARY KEY (thread_id, step, tool_call_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS freyja_agent_tasks (
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
            agent_ref         text,
            progress          text,
            progress_at       timestamptz,
            PRIMARY KEY (agent, task_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS freyja_agent_tasks_thread ON freyja_agent_tasks (thread_id)",
        # The calls of agents' tools that a model proposed (feature 005, research R8). Every
        # read and every delete is by the thread, or by the thread and the namespace, which
        # the primary key serves.
        """
        CREATE TABLE IF NOT EXISTS freyja_agent_calls (
            thread_id    text NOT NULL,
            namespace    text NOT NULL,
            tool_call_id text NOT NULL,
            agent        text NOT NULL,
            agent_id     text,
            gated        boolean NOT NULL,
            decided      boolean NOT NULL DEFAULT false,
            proposed_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (thread_id, namespace, tool_call_id)
        )
        """,
        # The columns added to tables that existed in deployments: a task's ``agent_ref``
        # (005 research R2), a proposed call's ``decided``, which the table created by 005's
        # fifth phase lacks, and a task's kept progress and its time (008 research R2). A
        # boolean with a constant default, and a nullable column with none, are added
        # without rewriting the table.
        _ADD_AGENT_REF,
        _ADD_DECIDED,
        _ADD_PROGRESS,
        _ADD_PROGRESS_AT,
        # LLM profiles and the active choice (feature 004).
        """
        CREATE TABLE IF NOT EXISTS freyja_llm_profiles (
            id                text PRIMARY KEY,
            name              text NOT NULL,
            provider          text NOT NULL,
            model             text NOT NULL,
            base_url          text,
            api_key_encrypted text NOT NULL,
            key_hint          text NOT NULL,
            responses_api     boolean NOT NULL DEFAULT true,
            config_version    integer NOT NULL DEFAULT 1,
            last_test_ok      boolean,
            last_test_at      timestamptz,
            last_test_step    text,
            last_test_reason  text,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now()
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS freyja_llm_profiles_name"
        " ON freyja_llm_profiles (lower(name))",
        """
        CREATE TABLE IF NOT EXISTS freyja_llm_state (
            id                smallint PRIMARY KEY CHECK (id = 1),
            active_profile_id text REFERENCES freyja_llm_profiles (id) ON DELETE RESTRICT,
            revision          bigint NOT NULL DEFAULT 0,
            updated_at        timestamptz NOT NULL DEFAULT now()
        )
        """,
        "INSERT INTO freyja_llm_state (id) VALUES (1) ON CONFLICT DO NOTHING",
        # The agents an operator saved (feature 005).
        """
        CREATE TABLE IF NOT EXISTS freyja_agents (
            id              text PRIMARY KEY,
            name            text NOT NULL,
            url             text NOT NULL,
            destination     text NOT NULL,
            read_only       boolean NOT NULL DEFAULT false,
            address_version integer NOT NULL DEFAULT 1,
            version         integer NOT NULL DEFAULT 1,
            card            jsonb NOT NULL,
            card_read_at    timestamptz NOT NULL DEFAULT now(),
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT freyja_agents_name UNIQUE (name),
            CONSTRAINT freyja_agents_destination UNIQUE (destination)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS freyja_agents_state (
            id         smallint PRIMARY KEY CHECK (id = 1),
            revision   bigint NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        "INSERT INTO freyja_agents_state (id) VALUES (1) ON CONFLICT DO NOTHING",
        # The index of threads (feature 007).
        """
        CREATE TABLE IF NOT EXISTS freyja_threads (
            thread_id      text PRIMARY KEY,
            first_message  text,
            created_at     timestamptz NOT NULL,
            changed_at     timestamptz NOT NULL,
            run_owner      text,
            runs           bigint NOT NULL DEFAULT 0,
            run_unattended boolean NOT NULL DEFAULT false,
            stop_owner     text,
            noted          text,
            error_code     text,
            error_message  text,
            noted_at       timestamptz,
            settled        text,
            agents_pending boolean NOT NULL DEFAULT false
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS freyja_threads_settled_changed
            ON freyja_threads (settled, changed_at DESC)
        """,
        # The conversations and received tasks of other agents (feature 009).
        """
        CREATE TABLE IF NOT EXISTS freyja_a2a_conversations (
            caller text NOT NULL,
            context_id text NOT NULL,
            thread_id text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL,
            PRIMARY KEY (caller, context_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS freyja_a2a_tasks (
            task_id text PRIMARY KEY,
            caller text NOT NULL,
            context_id text NOT NULL,
            thread_id text NOT NULL,
            hops integer NOT NULL,
            state text NOT NULL,
            reason text,
            result text,
            created_at timestamptz NOT NULL,
            started_at timestamptz,
            base_checkpoint text,
            ended_at timestamptz,
            released_at timestamptz,
            reject_attempts integer NOT NULL DEFAULT 0,
            reject_after timestamptz
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS freyja_a2a_tasks_conversation
            ON freyja_a2a_tasks (caller, context_id, created_at)
        """,
        "CREATE INDEX IF NOT EXISTS freyja_a2a_tasks_thread ON freyja_a2a_tasks (thread_id)",
        """
        CREATE INDEX IF NOT EXISTS freyja_a2a_tasks_unreleased
            ON freyja_a2a_tasks (released_at) WHERE released_at IS NULL
        """,
    ),
)

# Every migration, in the order it is applied. A new one goes at the end with the next
# number, and its checksum is added to ``RELEASED`` in ``tests/test_migrations.py``.
MIGRATIONS: tuple[Migration, ...] = checked((_BASELINE,))
