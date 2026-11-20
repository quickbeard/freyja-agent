"""The index of threads (feature 007, data-model.md): which threads have run, and where each
stands.

A thread's conversation, its proposal, and its agent tasks are its state, kept by the
checkpointer and the agent task store. What nothing kept until now is which threads there
are and how each one's last run ended. This module keeps that, one row per thread:

- ``ThreadIndex`` is the store, ``PostgresThreadIndex`` for the service and
  ``InMemoryThreadIndex`` for the suite.
- ``IndexedRunLocks`` wraps the run lease. Every run takes the lease first and gives it back
  last, whoever starts it, so the lease is where the index learns of a run (research R6). The
  wrapper's own writes never fail the lease.
- ``settle_thread`` and ``open_thread`` read a thread's state once and keep what a row needs
  of it; ``status_of`` turns a row into the one status a client is shown (research R7).

A write that is for one run names that run's lease owner and changes nothing when the row
has moved on to another run, so a late write cannot touch a later run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

import anyio

from freyja_agent.runtime.locks import RunLocks

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from freyja_agent.runtime.agent_tasks import AgentTaskStore

logger = logging.getLogger(__name__)

# How much of a thread's first message a row keeps (spec FR-005).
FIRST_MESSAGE_LIMIT = 200
# How much of a failed run's reason a row keeps.
ERROR_MESSAGE_LIMIT = 2000
# How many rows the list returns, beyond the threads that need a decision (spec FR-005).
LIST_LIMIT = 100
# The most the lease waits for the index to be told of a run. Keeping the list must not hold
# a run up (spec FR-023), and without a limit a write waits as long as the pool makes it.
INDEX_WRITE_SECONDS = 5.0

# How a run ended as whoever read its stream saw it. Only a run started through the
# unattended routes has a reader of the service's own, so only such a run is noted.
Noted = Literal["failed", "stopped"]
# What a thread's state said when its last run was looked at.
Settled = Literal["paused", "finished", "unfinished"]
ThreadStatus = Literal[
    "running", "needs_decision", "failed", "stopped", "waiting_for_agent", "done"
]


@dataclass(frozen=True)
class ThreadRecord:
    """One thread as the index has it (data-model.md "Thread record")."""

    thread_id: str
    # The beginning of the thread's first message from a person; None until it is known.
    first_message: str | None
    created_at: datetime
    # The start of the run in progress, or the thread's last checkpoint as read when its
    # last run was settled.
    changed_at: datetime
    # The lease owner of the last run that started and did not tell the index it ended,
    # ``<instance>:<run>``. A run that died with its instance leaves it; whether a run is in
    # progress is the lease's to say, never this field's.
    run_owner: str | None = None
    # How many runs have started on the thread. A write made from a reading of the thread's
    # state names the number it saw, and is refused once another run has started.
    runs: int = 0
    # Whether that run, or the last one, was started through the unattended routes.
    run_unattended: bool = False
    # The ``run_owner`` a stop was asked for; it counts only while it equals ``run_owner``.
    stop_owner: str | None = None
    # How the last run that had a reader of the service's own ended, and when that was
    # noted. It is kept until a later run records a step: a request that is refused after it
    # took the lease is no run, and must not wipe what the last run left.
    noted: Noted | None = None
    error_code: str | None = None
    error_message: str | None = None
    noted_at: datetime | None = None
    # None while a run is in progress and until its end has been looked at.
    settled: Settled | None = None
    # Whether an agent task of the thread was pending when the row was settled.
    agents_pending: bool = False


def _cut(text: str | None, limit: int) -> str | None:
    """The beginning of ``text``, as it can be kept.

    A person's message may hold what a text column does not take: the database refuses a NUL
    character, and a lone surrogate cannot be encoded. Either would fail the write for one
    row at every reading of the list, so both are taken out here, for both stores alike.
    """
    if text is None:
        return None
    return text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")[:limit]


class ThreadIndex(Protocol):
    async def setup(self) -> None: ...

    async def get(self, thread_id: str) -> ThreadRecord | None: ...

    async def run_started(self, thread_id: str, owner: str) -> None:
        """A run took the thread's lease: make the row if it is missing, and start it over."""

    async def mark_unattended(self, thread_id: str, first_message: str) -> None:
        """The run in progress goes on without a reader. Called while that run holds the
        thread's lease, so it names no owner."""

    async def note(
        self,
        thread_id: str,
        owner: str,
        noted: Noted,
        error_code: str | None = None,
        error_message: str | None = None,
        *,
        at: datetime | None = None,
    ) -> bool:
        """How the run of ``owner`` ended, as its reader saw it. False when it is not kept.

        ``at`` is when, by this instance's clock, which is the clock the run's checkpoints
        were written by; now when it is not given."""

    async def run_ended(self, thread_id: str, owner: str) -> None:
        """The run of ``owner`` gave its lease back."""

    async def settle(
        self,
        thread_id: str,
        seen_runs: int,
        *,
        settled: Settled,
        changed_at: datetime,
        first_message: str | None,
        agents_pending: bool,
    ) -> bool:
        """Keep what the thread's state said. ``seen_runs`` is the row's ``runs`` as it was
        read before the state was: the write is refused, with False, when a run has started
        since, also one that has ended again. A note older than ``changed_at`` is dropped: a
        later run has recorded a step since."""

    async def open(
        self,
        thread_id: str,
        *,
        settled: Settled | None,
        created_at: datetime,
        changed_at: datetime,
        first_message: str | None,
        agents_pending: bool,
    ) -> ThreadRecord:
        """Make the row of a thread the index does not know; the row as it is otherwise."""

    async def remove(self, thread_id: str, seen_runs: int) -> bool:
        """Forget a thread that has no state, unless a run has started meanwhile."""

    async def request_stop(self, thread_id: str) -> ThreadRecord | None:
        """Mark the run in progress to be stopped. None when the row names no run."""

    async def stop_requested(self, thread_id: str, owner: str) -> bool: ...

    async def unsettled(self) -> list[ThreadRecord]:
        """Every row nobody looked at since its run started: in progress, or ended."""

    async def list(self, limit: int = LIST_LIMIT) -> list[ThreadRecord]:
        """Every paused row, then the others up to ``limit`` rows in all, each group with the
        most recently changed first."""

    async def counts(self) -> tuple[int, int]:
        """How many rows are paused, and how many rows there are."""


class InMemoryThreadIndex:
    """The suite's index: a dict, with the same rules as the table."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._rows: dict[str, ThreadRecord] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    async def setup(self) -> None:
        return None

    async def get(self, thread_id: str) -> ThreadRecord | None:
        return self._rows.get(thread_id)

    async def run_started(self, thread_id: str, owner: str) -> None:
        now = self._clock()
        row = self._rows.get(thread_id) or ThreadRecord(
            thread_id=thread_id, first_message=None, created_at=now, changed_at=now
        )
        self._rows[thread_id] = replace(
            row,
            changed_at=now,
            run_owner=owner,
            runs=row.runs + 1,
            run_unattended=False,
            stop_owner=None,
            settled=None,
        )

    async def mark_unattended(self, thread_id: str, first_message: str) -> None:
        row = self._rows.get(thread_id)
        if row is None:
            return
        kept = row.first_message
        if kept is None:
            kept = _cut(first_message, FIRST_MESSAGE_LIMIT)
        self._rows[thread_id] = replace(row, run_unattended=True, first_message=kept)

    async def note(
        self,
        thread_id: str,
        owner: str,
        noted: Noted,
        error_code: str | None = None,
        error_message: str | None = None,
        *,
        at: datetime | None = None,
    ) -> bool:
        row = self._rows.get(thread_id)
        if row is None or row.run_owner != owner:
            return False
        self._rows[thread_id] = replace(
            row,
            noted=noted,
            error_code=error_code,
            error_message=_cut(error_message, ERROR_MESSAGE_LIMIT),
            noted_at=at or datetime.now(UTC),
        )
        return True

    async def run_ended(self, thread_id: str, owner: str) -> None:
        row = self._rows.get(thread_id)
        if row is None:
            return
        if row.run_owner == owner:
            self._rows[thread_id] = replace(row, run_owner=None)
        elif row.run_owner is None:
            # A run the index did not see start: what the row says is from before it.
            self._rows[thread_id] = replace(row, settled=None)

    async def settle(
        self,
        thread_id: str,
        seen_runs: int,
        *,
        settled: Settled,
        changed_at: datetime,
        first_message: str | None,
        agents_pending: bool,
    ) -> bool:
        row = self._rows.get(thread_id)
        if row is None or row.runs != seen_runs:
            return False
        kept = row.first_message
        if kept is None:
            kept = _cut(first_message, FIRST_MESSAGE_LIMIT)
        if row.noted_at is not None and row.noted_at < changed_at:
            row = replace(row, noted=None, error_code=None, error_message=None, noted_at=None)
        self._rows[thread_id] = replace(
            row,
            settled=settled,
            changed_at=changed_at,
            first_message=kept,
            agents_pending=agents_pending,
        )
        return True

    async def open(
        self,
        thread_id: str,
        *,
        settled: Settled | None,
        created_at: datetime,
        changed_at: datetime,
        first_message: str | None,
        agents_pending: bool,
    ) -> ThreadRecord:
        if thread_id not in self._rows:
            self._rows[thread_id] = ThreadRecord(
                thread_id=thread_id,
                first_message=_cut(first_message, FIRST_MESSAGE_LIMIT),
                created_at=created_at,
                changed_at=changed_at,
                settled=settled,
                agents_pending=agents_pending,
            )
        return self._rows[thread_id]

    async def remove(self, thread_id: str, seen_runs: int) -> bool:
        row = self._rows.get(thread_id)
        if row is None or row.runs != seen_runs:
            return False
        del self._rows[thread_id]
        return True

    async def request_stop(self, thread_id: str) -> ThreadRecord | None:
        row = self._rows.get(thread_id)
        if row is None or row.run_owner is None:
            return None
        self._rows[thread_id] = replace(row, stop_owner=row.run_owner)
        return self._rows[thread_id]

    async def stop_requested(self, thread_id: str, owner: str) -> bool:
        row = self._rows.get(thread_id)
        return row is not None and row.run_owner == owner and row.stop_owner == owner

    async def unsettled(self) -> list[ThreadRecord]:
        return [row for row in self._rows.values() if row.settled is None]

    async def list(self, limit: int = LIST_LIMIT) -> list[ThreadRecord]:
        newest = sorted(self._rows.values(), key=lambda row: row.changed_at, reverse=True)
        paused = [row for row in newest if row.settled == "paused"]
        others = [row for row in newest if row.settled != "paused"]
        return paused + others[: max(0, limit - len(paused))]

    async def counts(self) -> tuple[int, int]:
        paused = sum(1 for row in self._rows.values() if row.settled == "paused")
        return paused, len(self._rows)


# Every statement is one atomic command, so two instances cannot interleave inside an
# operation, and every value is a bound parameter. The condition that ties a write to one
# run is in the statement's WHERE, where the database checks it with the write.
_CREATE = (
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
)

_GET = "SELECT * FROM freyja_threads WHERE thread_id = %(thread_id)s"

_RUN_STARTED = """
INSERT INTO freyja_threads (thread_id, created_at, changed_at, run_owner, runs)
VALUES (%(thread_id)s, now(), now(), %(owner)s, 1)
ON CONFLICT (thread_id) DO UPDATE
    SET changed_at = now(),
        run_owner = EXCLUDED.run_owner,
        runs = freyja_threads.runs + 1,
        run_unattended = false,
        stop_owner = NULL,
        settled = NULL
"""

_MARK_UNATTENDED = """
UPDATE freyja_threads
   SET run_unattended = true,
       first_message = COALESCE(first_message, %(first_message)s)
 WHERE thread_id = %(thread_id)s
"""

_NOTE = """
UPDATE freyja_threads
   SET noted = %(noted)s,
       error_code = %(error_code)s,
       error_message = %(error_message)s,
       noted_at = %(at)s
 WHERE thread_id = %(thread_id)s AND run_owner = %(owner)s
"""

_RUN_ENDED = """
UPDATE freyja_threads
   SET run_owner = NULL,
       settled = CASE WHEN run_owner IS NULL THEN NULL ELSE settled END
 WHERE thread_id = %(thread_id)s AND (run_owner = %(owner)s OR run_owner IS NULL)
"""

_SETTLE = """
UPDATE freyja_threads
   SET settled = %(settled)s,
       changed_at = %(changed_at)s,
       first_message = COALESCE(first_message, %(first_message)s),
       agents_pending = %(agents_pending)s,
       noted = CASE WHEN noted_at < %(changed_at)s THEN NULL ELSE noted END,
       error_code = CASE WHEN noted_at < %(changed_at)s THEN NULL ELSE error_code END,
       error_message = CASE WHEN noted_at < %(changed_at)s THEN NULL ELSE error_message END,
       noted_at = CASE WHEN noted_at < %(changed_at)s THEN NULL ELSE noted_at END
 WHERE thread_id = %(thread_id)s AND runs = %(seen_runs)s
"""

_OPEN = """
INSERT INTO freyja_threads
    (thread_id, first_message, created_at, changed_at, settled, agents_pending)
VALUES
    (%(thread_id)s, %(first_message)s, %(created_at)s, %(changed_at)s, %(settled)s,
     %(agents_pending)s)
ON CONFLICT (thread_id) DO NOTHING
"""

_REMOVE = """
DELETE FROM freyja_threads
 WHERE thread_id = %(thread_id)s AND runs = %(seen_runs)s
"""

_REQUEST_STOP = """
UPDATE freyja_threads
   SET stop_owner = run_owner
 WHERE thread_id = %(thread_id)s AND run_owner IS NOT NULL
RETURNING *
"""

_STOP_REQUESTED = """
SELECT EXISTS (
    SELECT 1 FROM freyja_threads
     WHERE thread_id = %(thread_id)s AND run_owner = %(owner)s AND stop_owner = %(owner)s
) AS asked
"""

_UNSETTLED = "SELECT * FROM freyja_threads WHERE settled IS NULL"

# Every paused row, then the others, as many as bring the list to the limit.
_LIST = """
(SELECT *, 0 AS place FROM freyja_threads WHERE settled = 'paused')
UNION ALL
(SELECT *, 1 AS place FROM freyja_threads
  WHERE settled IS DISTINCT FROM 'paused'
  ORDER BY changed_at DESC
  LIMIT GREATEST(
      0, %(limit)s - (SELECT count(*) FROM freyja_threads WHERE settled = 'paused')
  ))
ORDER BY place, changed_at DESC
"""

_COUNTS = """
SELECT count(*) FILTER (WHERE settled = 'paused') AS paused, count(*) AS total
  FROM freyja_threads
"""


def _record(row: dict[str, Any]) -> ThreadRecord:
    return ThreadRecord(
        thread_id=row["thread_id"],
        first_message=row["first_message"],
        created_at=row["created_at"],
        changed_at=row["changed_at"],
        run_owner=row["run_owner"],
        runs=row["runs"],
        run_unattended=row["run_unattended"],
        stop_owner=row["stop_owner"],
        noted=row["noted"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        noted_at=row["noted_at"],
        settled=row["settled"],
        agents_pending=row["agents_pending"],
    )


class PostgresThreadIndex:
    """The service's index: the table ``freyja_threads`` in the shared pool."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            for statement in _CREATE:
                await conn.execute(statement)

    async def _rows(self, query: str, params: dict[str, Any] | None = None) -> list[Any]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(query, params or {})
            return await cursor.fetchall()

    async def _execute(self, query: str, params: dict[str, Any]) -> int:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(query, params)
            return cursor.rowcount

    async def get(self, thread_id: str) -> ThreadRecord | None:
        rows = await self._rows(_GET, {"thread_id": thread_id})
        return _record(rows[0]) if rows else None

    async def run_started(self, thread_id: str, owner: str) -> None:
        await self._execute(_RUN_STARTED, {"thread_id": thread_id, "owner": owner})

    async def mark_unattended(self, thread_id: str, first_message: str) -> None:
        await self._execute(
            _MARK_UNATTENDED,
            {"thread_id": thread_id, "first_message": _cut(first_message, FIRST_MESSAGE_LIMIT)},
        )

    async def note(
        self,
        thread_id: str,
        owner: str,
        noted: Noted,
        error_code: str | None = None,
        error_message: str | None = None,
        *,
        at: datetime | None = None,
    ) -> bool:
        params = {
            "thread_id": thread_id,
            "owner": owner,
            "noted": noted,
            "error_code": error_code,
            "error_message": _cut(error_message, ERROR_MESSAGE_LIMIT),
            "at": at or datetime.now(UTC),
        }
        return await self._execute(_NOTE, params) == 1

    async def run_ended(self, thread_id: str, owner: str) -> None:
        await self._execute(_RUN_ENDED, {"thread_id": thread_id, "owner": owner})

    async def settle(
        self,
        thread_id: str,
        seen_runs: int,
        *,
        settled: Settled,
        changed_at: datetime,
        first_message: str | None,
        agents_pending: bool,
    ) -> bool:
        params = {
            "thread_id": thread_id,
            "seen_runs": seen_runs,
            "settled": settled,
            "changed_at": changed_at,
            "first_message": _cut(first_message, FIRST_MESSAGE_LIMIT),
            "agents_pending": agents_pending,
        }
        return await self._execute(_SETTLE, params) == 1

    async def open(
        self,
        thread_id: str,
        *,
        settled: Settled | None,
        created_at: datetime,
        changed_at: datetime,
        first_message: str | None,
        agents_pending: bool,
    ) -> ThreadRecord:
        params = {
            "thread_id": thread_id,
            "settled": settled,
            "created_at": created_at,
            "changed_at": changed_at,
            "first_message": _cut(first_message, FIRST_MESSAGE_LIMIT),
            "agents_pending": agents_pending,
        }
        await self._execute(_OPEN, params)
        rows = await self._rows(_GET, {"thread_id": thread_id})
        return _record(rows[0])

    async def remove(self, thread_id: str, seen_runs: int) -> bool:
        params = {"thread_id": thread_id, "seen_runs": seen_runs}
        return await self._execute(_REMOVE, params) == 1

    async def request_stop(self, thread_id: str) -> ThreadRecord | None:
        rows = await self._rows(_REQUEST_STOP, {"thread_id": thread_id})
        return _record(rows[0]) if rows else None

    async def stop_requested(self, thread_id: str, owner: str) -> bool:
        rows = await self._rows(_STOP_REQUESTED, {"thread_id": thread_id, "owner": owner})
        return bool(rows and rows[0]["asked"])

    async def unsettled(self) -> list[ThreadRecord]:
        return [_record(row) for row in await self._rows(_UNSETTLED)]

    async def list(self, limit: int = LIST_LIMIT) -> list[ThreadRecord]:
        return [_record(row) for row in await self._rows(_LIST, {"limit": limit})]

    async def counts(self) -> tuple[int, int]:
        rows = await self._rows(_COUNTS)
        return rows[0]["paused"], rows[0]["total"]


class IndexedRunLocks:
    """The run lease, with the index told of every run that takes it and gives it back.

    Every call is the inner locks' and its answer is returned. The index is written after
    the lease was taken or given back. A write that fails, or that takes longer than
    ``INDEX_WRITE_SECONDS``, is logged and the lease stands as the inner locks left it, so
    keeping the list never fails a run and holds one up by that much at most (spec FR-023).
    """

    def __init__(self, inner: RunLocks, index: ThreadIndex) -> None:
        # A wrapper handed to another runtime, as a test does for a second instance, wraps
        # the locks themselves once and writes to the index of the runtime that holds it.
        self.inner: RunLocks = inner.inner if isinstance(inner, IndexedRunLocks) else inner
        self.index = index

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def setup(self) -> None:
        await self.inner.setup()

    async def acquire(self, thread_id: str, owner: str) -> bool:
        taken = await self.inner.acquire(thread_id, owner)
        if taken:
            try:
                async with asyncio.timeout(INDEX_WRITE_SECONDS):
                    await self.index.run_started(thread_id, owner)
            except Exception as exc:
                self._not_kept("started", thread_id, exc)
            except BaseException:
                # Cancelled while the index was told: the caller never learns that it holds
                # the lease, so it is given back before the cancellation goes on.
                with anyio.CancelScope(shield=True):
                    await self.inner.release(thread_id, owner)
                raise
        return taken

    async def renew(self, thread_id: str, owner: str) -> None:
        await self.inner.renew(thread_id, owner)

    async def release(self, thread_id: str, owner: str) -> None:
        await self.inner.release(thread_id, owner)
        try:
            async with asyncio.timeout(INDEX_WRITE_SECONDS):
                await self.index.run_ended(thread_id, owner)
        except Exception as exc:
            self._not_kept("ended", thread_id, exc)

    async def is_active(self, thread_id: str) -> bool:
        return await self.inner.is_active(thread_id)

    @staticmethod
    def _not_kept(what: str, thread_id: str, exc: Exception) -> None:
        # The thread is in the list again from its next run, or from when it is opened.
        logger.warning(
            "Thread %s: the list of threads was not told that a run %s (%s); the run is "
            "not affected",
            thread_id,
            what,
            type(exc).__name__,
        )


def status_of(record: ThreadRecord, lease_active: bool) -> ThreadStatus:
    """The one status of a thread, first match (data-model.md "Status").

    A proposal comes before a noted failure: a continuation that failed before its action
    completed leaves the proposal pending, and a decision is what the thread needs then.
    """
    if lease_active:
        return "running"
    if record.settled == "paused":
        return "needs_decision"
    if record.noted == "failed":
        return "failed"
    if record.noted == "stopped" or record.settled == "unfinished":
        return "stopped"
    if record.agents_pending:
        return "waiting_for_agent"
    return "done"


def stoppable(record: ThreadRecord, lease_active: bool) -> bool:
    """Whether the run in progress can be asked to stop: only one that goes on by itself."""
    return lease_active and record.run_owner is not None and record.run_unattended


def stopping(record: ThreadRecord, lease_active: bool) -> bool:
    return stoppable(record, lease_active) and record.stop_owner == record.run_owner


@dataclass(frozen=True)
class _Reading:
    """What one read of a thread's state says of it."""

    settled: Settled
    changed_at: datetime
    first_message: str | None
    agents_pending: bool


def _first_message(messages: list[Any]) -> str | None:
    for message in messages:
        if getattr(message, "type", None) == "human":
            content = message.content
            # A first message that is not text, which no client of this feature sends.
            return content if isinstance(content, str) else ""
    return None


async def _read(context: Any, agent_tasks: AgentTaskStore, thread_id: str) -> _Reading | None:
    """Read the thread's state once, as a state read does; None for a thread with none."""
    snapshot = await context.agent.aget_state({"configurable": {"thread_id": thread_id}})
    if snapshot.metadata is None:
        return None
    if snapshot.interrupts:
        settled: Settled = "paused"
    elif snapshot.next:
        settled = "unfinished"
    else:
        settled = "finished"
    try:
        changed_at = datetime.fromisoformat(snapshot.created_at)
    except (TypeError, ValueError):
        changed_at = datetime.now(UTC)
    tasks = await agent_tasks.list_tasks(thread_id)
    return _Reading(
        settled=settled,
        changed_at=changed_at,
        first_message=_first_message(snapshot.values.get("messages", [])),
        agents_pending=any(task.pending for task in tasks),
    )


async def settle_thread(
    index: ThreadIndex,
    context: Any,
    agent_tasks: AgentTaskStore,
    thread_id: str,
    seen_runs: int,
) -> ThreadRecord | None:
    """Look at a thread whose run has ended and keep what its state says; the row afterwards.

    ``seen_runs`` is the row's ``runs`` as the caller read it, before the state is read
    here: what is found is kept only while no run has started since. A thread that has no
    state recorded nothing, so there is no task to list: its row is removed (spec FR-008).
    """
    reading = await _read(context, agent_tasks, thread_id)
    if reading is None:
        await index.remove(thread_id, seen_runs)
        return None
    await index.settle(
        thread_id,
        seen_runs,
        settled=reading.settled,
        changed_at=reading.changed_at,
        first_message=reading.first_message,
        agents_pending=reading.agents_pending,
    )
    return await index.get(thread_id)


async def open_thread(
    index: ThreadIndex,
    context: Any,
    agent_tasks: AgentTaskStore,
    thread_id: str,
    *,
    lease_active: bool,
) -> ThreadRecord | None:
    """Give a thread the index does not know its row, from its state; None when it has none.

    This is how a thread from before the index, or one whose row could not be written, comes
    into the list (spec FR-004, FR-023). While a run holds the thread's lease the row is made
    without ``settled``: the lease says running, and the row is settled once it is gone.
    """
    reading = await _read(context, agent_tasks, thread_id)
    if reading is None:
        return None
    return await index.open(
        thread_id,
        settled=None if lease_active else reading.settled,
        created_at=reading.changed_at,
        changed_at=reading.changed_at,
        first_message=reading.first_message,
        agents_pending=reading.agents_pending,
    )
