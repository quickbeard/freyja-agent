"""Conversations and received tasks: what other agents handed Freyja over A2A (feature 009).

Two tables, ``freyja_a2a_conversations`` and ``freyja_a2a_tasks``, with the store of the
service (``PostgresReceivedTaskStore``) and the store of the suite
(``InMemoryReceivedTaskStore``), which keep the same rules (specs/009-a2a-server/
data-model.md).

A conversation's row is what makes a thread a caller's. It is written in the transaction
that accepts the conversation's first message, before any run of the thread starts, and
every question "did a caller start this thread, and which" is one read of it by
``thread_id``. A conversation's ``context_id`` is its caller's own; it is returned to that
caller and appears nowhere else: not in a log line, the console, a thread, or what a model
is told.

A received task is one accepted message. Its state is read from its thread by
``api/received.py`` and written here once it has ended, never to change again apart from
``released_at`` and the two columns that count rejections by rule. Accepting a message is one
transaction under one lock, so that the two limits and "one task in progress per
conversation" hold when two messages arrive together, on one instance or two (research R8).

Nothing here imports the protocol's SDK: the store is tested without it.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, LiteralString, Protocol

from freyja_agent.runtime.persistence import Pool

TaskState = Literal["working", "completed", "failed", "canceled"]
Refusal = Literal["busy", "caller_limit", "total_limit"]

# The caller every agent is that calls on the service's own address without a token.
LOCAL = "local"
# The fixed set a failed or canceled task's reason is one of (data-model.md "Reasons").
REASONS = frozenset(
    {
        "no_usable_model",
        "model_error",
        "step_limit",
        "interrupted",
        "internal",
        "stopped",
        "canceled",
    }
)
ENDED = ("completed", "failed", "canceled")
# The one advisory lock every acceptance takes: a few statements, on any instance.
ACCEPT_LOCK = 9_009_001


def new_id() -> str:
    """An id the service makes, for a thread or a task: 32 hexadecimal characters."""
    return secrets.token_hex(16)


def kept(text: str | None) -> str | None:
    """``text`` as a text column takes it: no NUL character and no lone surrogate."""
    if text is None:
        return None
    return text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


@dataclass(frozen=True)
class Conversation:
    """One conversation id of one caller, kept in one thread."""

    caller: str
    context_id: str
    thread_id: str
    created_at: datetime


@dataclass(frozen=True)
class ReceivedTask:
    """One message a caller sent and the service accepted (data-model.md "Received task")."""

    task_id: str
    caller: str
    context_id: str
    thread_id: str
    # The hand-over count the message carried; 0 when it carried none.
    hops: int
    state: TaskState
    created_at: datetime
    # A code of ``REASONS``, for ``failed`` and ``canceled``.
    reason: str | None = None
    # Freyja's last reply, for ``completed``.
    result: str | None = None
    # When the task's run had taken the thread's lease, by the clock of the instance that
    # started it; None between acceptance and that moment.
    started_at: datetime | None = None
    # The id of the thread's last checkpoint when the run took the lease; None for a thread
    # that had none.
    base_checkpoint: str | None = None
    ended_at: datetime | None = None
    # When the task stopped counting against the limits.
    released_at: datetime | None = None
    reject_attempts: int = 0
    reject_after: datetime | None = None

    @property
    def ended(self) -> bool:
        return self.state != "working"


@dataclass(frozen=True)
class CallerMark:
    """Who started a thread, for its row in the list: the caller, and whether that caller
    canceled the conversation's last task."""

    name: str
    canceled: bool


@dataclass(frozen=True)
class Acceptance:
    """What accepting a message gave: the task, or why none was made."""

    task: ReceivedTask | None
    refused: Refusal | None = None
    # Whether the conversation was made for this message.
    new_conversation: bool = False


class ReceivedTaskStore(Protocol):
    async def now(self) -> datetime: ...

    async def accept(
        self,
        caller: str,
        context_id: str,
        *,
        hops: int,
        caller_limit: int,
        total_limit: int,
    ) -> Acceptance:
        """Find or make the conversation, refuse a busy one or a limit, write the task."""
        ...

    async def get(self, task_id: str, caller: str | None = None) -> ReceivedTask | None:
        """A task; with ``caller``, only that caller's."""
        ...

    async def mark_started(
        self, task_id: str, started_at: datetime, base_checkpoint: str | None
    ) -> bool: ...

    async def end(
        self,
        task_id: str,
        state: TaskState,
        *,
        reason: str | None = None,
        result: str | None = None,
        released: bool = False,
    ) -> bool:
        """End a task that is working; False when it was not, and nothing was written."""
        ...

    async def release(self, task_id: str) -> bool: ...

    async def remove(self, task_id: str) -> bool:
        """Remove a task whose run never started, as when the run was refused."""
        ...

    async def count_rejection(self, task_id: str, attempts: int, next_at: datetime) -> bool:
        """Count one attempt to reject the thread's proposal by rule, not before ``next_at``
        again, when ``attempts`` were counted so far; False when another counted it first."""
        ...

    async def rejection_done(self, task_id: str) -> None:
        """No proposal of the task's thread is left to reject by rule: it is not tried again."""
        ...

    async def conversation_of(self, thread_id: str) -> Conversation | None: ...

    async def callers_of(self, thread_ids: Iterable[str]) -> dict[str, CallerMark]: ...

    async def last_task(self, caller: str, context_id: str) -> ReceivedTask | None: ...

    async def last_task_of_thread(self, thread_id: str) -> ReceivedTask | None: ...

    async def last_started_task_of_thread(self, thread_id: str) -> ReceivedTask | None:
        """The last task of the thread whose run took the lease: the only one a run on the
        thread, and so a proposal of it, can be of. A message accepted and not yet started
        is not it."""
        ...

    async def unreleased(self, limit: int) -> list[ReceivedTask]: ...

    async def to_reject(self, attempts: int, limit: int) -> list[ReceivedTask]:
        """Tasks that ended and whose proposal is to be rejected by rule again: tried fewer
        than ``attempts`` times, at most ``limit`` of them, the earliest first."""
        ...

    async def working_of_others(self, callers: Iterable[str], limit: int) -> list[ReceivedTask]:
        """Working tasks of callers other than ``callers``, at most ``limit``, the earliest
        first: those of a caller the deployment no longer names (FR-021)."""
        ...


def _checked_end(state: str, reason: str | None, result: str | None) -> None:
    if state not in ENDED:
        raise ValueError(f"a task ends as one of {ENDED}, not {state!r}")
    if state == "completed":
        if reason is not None:
            raise ValueError("a completed task has no reason")
    elif reason not in REASONS:
        raise ValueError(f"a {state} task needs a reason of the fixed set")
    if state != "completed" and result is not None:
        raise ValueError("only a completed task has a result")


class InMemoryReceivedTaskStore:
    """The suite's store: two dicts, with the same rules as the tables."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._conversations: dict[tuple[str, str], Conversation] = {}
        # In the order they were accepted, which breaks ties between equal times.
        self._tasks: dict[str, ReceivedTask] = {}
        self._lock = asyncio.Lock()

    async def now(self) -> datetime:
        return self._clock()

    async def accept(
        self,
        caller: str,
        context_id: str,
        *,
        hops: int,
        caller_limit: int,
        total_limit: int,
    ) -> Acceptance:
        async with self._lock:
            now = self._clock()
            key = (caller, kept(context_id) or "")
            conversation = self._conversations.get(key)
            new = conversation is None
            if conversation is None:
                conversation = Conversation(caller, key[1], new_id(), now)
            else:
                last = self._last(lambda task: (task.caller, task.context_id) == key)
                if last is not None and last.state == "working":
                    return Acceptance(None, "busy")
            counting = [task for task in self._tasks.values() if task.released_at is None]
            if sum(task.caller == caller for task in counting) >= caller_limit:
                return Acceptance(None, "caller_limit")
            if len(counting) >= total_limit:
                return Acceptance(None, "total_limit")
            self._conversations[key] = conversation
            task = ReceivedTask(
                task_id=new_id(),
                caller=caller,
                context_id=key[1],
                thread_id=conversation.thread_id,
                hops=hops,
                state="working",
                created_at=now,
            )
            self._tasks[task.task_id] = task
            return Acceptance(task, None, new)

    def _last(self, matches: Callable[[ReceivedTask], bool]) -> ReceivedTask | None:
        found = [task for task in self._tasks.values() if matches(task)]
        return found[-1] if found else None

    async def get(self, task_id: str, caller: str | None = None) -> ReceivedTask | None:
        task = self._tasks.get(task_id)
        if task is None or (caller is not None and task.caller != caller):
            return None
        return task

    async def mark_started(
        self, task_id: str, started_at: datetime, base_checkpoint: str | None
    ) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.started_at is not None or task.ended:
            return False
        self._tasks[task_id] = replace(task, started_at=started_at, base_checkpoint=base_checkpoint)
        return True

    async def end(
        self,
        task_id: str,
        state: TaskState,
        *,
        reason: str | None = None,
        result: str | None = None,
        released: bool = False,
    ) -> bool:
        _checked_end(state, reason, result)
        task = self._tasks.get(task_id)
        if task is None or task.ended:
            return False
        now = self._clock()
        self._tasks[task_id] = replace(
            task,
            state=state,
            reason=reason,
            result=kept(result),
            ended_at=now,
            released_at=now if released else task.released_at,
        )
        return True

    async def release(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.released_at is not None:
            return False
        self._tasks[task_id] = replace(task, released_at=self._clock())
        return True

    async def remove(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.ended or task.started_at is not None:
            return False
        del self._tasks[task_id]
        return True

    async def count_rejection(self, task_id: str, attempts: int, next_at: datetime) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.reject_attempts != attempts:
            return False
        self._tasks[task_id] = replace(task, reject_attempts=attempts + 1, reject_after=next_at)
        return True

    async def rejection_done(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is not None and task.reject_after is not None:
            self._tasks[task_id] = replace(task, reject_after=None)

    async def conversation_of(self, thread_id: str) -> Conversation | None:
        return next(
            (found for found in self._conversations.values() if found.thread_id == thread_id),
            None,
        )

    async def callers_of(self, thread_ids: Iterable[str]) -> dict[str, CallerMark]:
        wanted = set(thread_ids)
        marks: dict[str, CallerMark] = {}
        for conversation in self._conversations.values():
            if conversation.thread_id not in wanted:
                continue
            last = await self.last_task_of_thread(conversation.thread_id)
            marks[conversation.thread_id] = CallerMark(
                conversation.caller,
                last is not None and last.state == "canceled" and last.reason == "canceled",
            )
        return marks

    async def last_task(self, caller: str, context_id: str) -> ReceivedTask | None:
        key = (caller, kept(context_id) or "")
        return self._last(lambda task: (task.caller, task.context_id) == key)

    async def last_task_of_thread(self, thread_id: str) -> ReceivedTask | None:
        return self._last(lambda task: task.thread_id == thread_id)

    async def last_started_task_of_thread(self, thread_id: str) -> ReceivedTask | None:
        return self._last(lambda task: task.thread_id == thread_id and task.started_at is not None)

    async def unreleased(self, limit: int) -> list[ReceivedTask]:
        return [task for task in self._tasks.values() if task.released_at is None][:limit]

    async def working_of_others(self, callers: Iterable[str], limit: int) -> list[ReceivedTask]:
        named = set(callers)
        return [
            task
            for task in self._tasks.values()
            if task.state == "working" and task.caller not in named
        ][:limit]

    async def to_reject(self, attempts: int, limit: int) -> list[ReceivedTask]:
        found = [
            task
            for task in self._tasks.values()
            if task.ended and task.reject_after is not None and task.reject_attempts < attempts
        ]
        return sorted(found, key=lambda task: task.reject_after or task.created_at)[:limit]


# Fixed statements with bound parameters only.
_LOCK = "SELECT pg_advisory_xact_lock(%(key)s)"
_CONVERSATION = """
SELECT * FROM freyja_a2a_conversations WHERE caller = %(caller)s AND context_id = %(context_id)s
"""
_MAKE_CONVERSATION = """
INSERT INTO freyja_a2a_conversations (caller, context_id, thread_id, created_at)
VALUES (%(caller)s, %(context_id)s, %(thread_id)s, now())
"""
_LAST = """
SELECT * FROM freyja_a2a_tasks
 WHERE caller = %(caller)s AND context_id = %(context_id)s
 ORDER BY created_at DESC LIMIT 1
"""
_LAST_OF_THREAD = """
SELECT * FROM freyja_a2a_tasks WHERE thread_id = %(thread_id)s ORDER BY created_at DESC LIMIT 1
"""
_LAST_STARTED_OF_THREAD = """
SELECT * FROM freyja_a2a_tasks
 WHERE thread_id = %(thread_id)s AND started_at IS NOT NULL
 ORDER BY created_at DESC LIMIT 1
"""
_COUNTING = """
SELECT count(*) FILTER (WHERE caller = %(caller)s) AS mine, count(*) AS total
  FROM freyja_a2a_tasks WHERE released_at IS NULL
"""
_MAKE_TASK = """
INSERT INTO freyja_a2a_tasks (task_id, caller, context_id, thread_id, hops, state, created_at)
VALUES (%(task_id)s, %(caller)s, %(context_id)s, %(thread_id)s, %(hops)s, 'working', now())
RETURNING *
"""
_GET = "SELECT * FROM freyja_a2a_tasks WHERE task_id = %(task_id)s"
_MARK_STARTED = """
UPDATE freyja_a2a_tasks
   SET started_at = %(started_at)s, base_checkpoint = %(base_checkpoint)s
 WHERE task_id = %(task_id)s AND started_at IS NULL AND state = 'working'
"""
_END = """
UPDATE freyja_a2a_tasks
   SET state = %(state)s, reason = %(reason)s, result = %(result)s, ended_at = now(),
       released_at = CASE WHEN %(released)s THEN now() ELSE released_at END
 WHERE task_id = %(task_id)s AND state = 'working'
"""
_RELEASE = """
UPDATE freyja_a2a_tasks SET released_at = now()
 WHERE task_id = %(task_id)s AND released_at IS NULL
"""
_REMOVE = """
DELETE FROM freyja_a2a_tasks
 WHERE task_id = %(task_id)s AND state = 'working' AND started_at IS NULL
"""
_COUNT_REJECTION = """
UPDATE freyja_a2a_tasks
   SET reject_attempts = reject_attempts + 1, reject_after = %(next_at)s
 WHERE task_id = %(task_id)s AND reject_attempts = %(attempts)s
"""
_REJECTION_DONE = """
UPDATE freyja_a2a_tasks SET reject_after = NULL
 WHERE task_id = %(task_id)s AND reject_after IS NOT NULL
"""
_CONVERSATION_OF = "SELECT * FROM freyja_a2a_conversations WHERE thread_id = %(thread_id)s"
_CALLERS_OF = """
SELECT c.thread_id, c.caller,
       COALESCE(last.state = 'canceled' AND last.reason = 'canceled', false) AS canceled
  FROM freyja_a2a_conversations c
  LEFT JOIN LATERAL (
      SELECT state, reason FROM freyja_a2a_tasks t
       WHERE t.thread_id = c.thread_id ORDER BY created_at DESC LIMIT 1
  ) last ON true
 WHERE c.thread_id = ANY(%(thread_ids)s)
"""
_UNRELEASED = """
SELECT * FROM freyja_a2a_tasks WHERE released_at IS NULL ORDER BY created_at LIMIT %(limit)s
"""
_TO_REJECT = """
SELECT * FROM freyja_a2a_tasks
 WHERE state <> 'working' AND reject_after IS NOT NULL AND reject_attempts < %(attempts)s
 ORDER BY reject_after LIMIT %(limit)s
"""
_WORKING_OF_OTHERS = """
SELECT * FROM freyja_a2a_tasks
 WHERE state = 'working' AND NOT (caller = ANY(%(callers)s))
 ORDER BY created_at LIMIT %(limit)s
"""
_NOW = "SELECT now() AS now"


def _task(row: dict[str, Any]) -> ReceivedTask:
    return ReceivedTask(**row)


def _conversation(row: dict[str, Any]) -> Conversation:
    return Conversation(**row)


class PostgresReceivedTaskStore:
    """The service's store: the two tables in the shared pool."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def _rows(self, query: LiteralString, params: dict[str, Any] | None = None) -> list[Any]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(query, params or {})
            return await cursor.fetchall()

    async def _execute(self, query: LiteralString, params: dict[str, Any]) -> int:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(query, params)
            return cursor.rowcount

    async def now(self) -> datetime:
        return (await self._rows(_NOW))[0]["now"]

    async def accept(
        self,
        caller: str,
        context_id: str,
        *,
        hops: int,
        caller_limit: int,
        total_limit: int,
    ) -> Acceptance:
        names = {"caller": caller, "context_id": kept(context_id) or ""}
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_LOCK, {"key": ACCEPT_LOCK})
            found = await (await conn.execute(_CONVERSATION, names)).fetchall()
            new = not found
            thread_id = new_id() if new else found[0]["thread_id"]
            if not new:
                last = await (await conn.execute(_LAST, names)).fetchall()
                if last and last[0]["state"] == "working":
                    return Acceptance(None, "busy")
            counts = (await (await conn.execute(_COUNTING, names)).fetchall())[0]
            refused: Refusal | None = None
            if counts["mine"] >= caller_limit:
                refused = "caller_limit"
            elif counts["total"] >= total_limit:
                refused = "total_limit"
            if refused is not None:
                return Acceptance(None, refused)
            if new:
                await conn.execute(_MAKE_CONVERSATION, {**names, "thread_id": thread_id})
            params = {**names, "task_id": new_id(), "thread_id": thread_id, "hops": hops}
            row = (await (await conn.execute(_MAKE_TASK, params)).fetchall())[0]
            return Acceptance(_task(row), None, new)

    async def get(self, task_id: str, caller: str | None = None) -> ReceivedTask | None:
        rows = await self._rows(_GET, {"task_id": task_id})
        if not rows or (caller is not None and rows[0]["caller"] != caller):
            return None
        return _task(rows[0])

    async def mark_started(
        self, task_id: str, started_at: datetime, base_checkpoint: str | None
    ) -> bool:
        params = {"task_id": task_id, "started_at": started_at, "base_checkpoint": base_checkpoint}
        return await self._execute(_MARK_STARTED, params) == 1

    async def end(
        self,
        task_id: str,
        state: TaskState,
        *,
        reason: str | None = None,
        result: str | None = None,
        released: bool = False,
    ) -> bool:
        _checked_end(state, reason, result)
        params = {
            "task_id": task_id,
            "state": state,
            "reason": reason,
            "result": kept(result),
            "released": released,
        }
        return await self._execute(_END, params) == 1

    async def release(self, task_id: str) -> bool:
        return await self._execute(_RELEASE, {"task_id": task_id}) == 1

    async def remove(self, task_id: str) -> bool:
        return await self._execute(_REMOVE, {"task_id": task_id}) == 1

    async def count_rejection(self, task_id: str, attempts: int, next_at: datetime) -> bool:
        params = {"task_id": task_id, "attempts": attempts, "next_at": next_at}
        return await self._execute(_COUNT_REJECTION, params) == 1

    async def rejection_done(self, task_id: str) -> None:
        await self._execute(_REJECTION_DONE, {"task_id": task_id})

    async def conversation_of(self, thread_id: str) -> Conversation | None:
        rows = await self._rows(_CONVERSATION_OF, {"thread_id": thread_id})
        return _conversation(rows[0]) if rows else None

    async def callers_of(self, thread_ids: Iterable[str]) -> dict[str, CallerMark]:
        rows = await self._rows(_CALLERS_OF, {"thread_ids": list(thread_ids)})
        return {row["thread_id"]: CallerMark(row["caller"], row["canceled"]) for row in rows}

    async def last_task(self, caller: str, context_id: str) -> ReceivedTask | None:
        rows = await self._rows(_LAST, {"caller": caller, "context_id": kept(context_id) or ""})
        return _task(rows[0]) if rows else None

    async def last_task_of_thread(self, thread_id: str) -> ReceivedTask | None:
        rows = await self._rows(_LAST_OF_THREAD, {"thread_id": thread_id})
        return _task(rows[0]) if rows else None

    async def last_started_task_of_thread(self, thread_id: str) -> ReceivedTask | None:
        rows = await self._rows(_LAST_STARTED_OF_THREAD, {"thread_id": thread_id})
        return _task(rows[0]) if rows else None

    async def unreleased(self, limit: int) -> list[ReceivedTask]:
        return [_task(row) for row in await self._rows(_UNRELEASED, {"limit": limit})]

    async def to_reject(self, attempts: int, limit: int) -> list[ReceivedTask]:
        params = {"attempts": attempts, "limit": limit}
        return [_task(row) for row in await self._rows(_TO_REJECT, params)]

    async def working_of_others(self, callers: Iterable[str], limit: int) -> list[ReceivedTask]:
        params = {"callers": list(callers), "limit": limit}
        return [_task(row) for row in await self._rows(_WORKING_OF_OTHERS, params)]
