"""Where agent conversations, sends, and tasks are kept (feature 003, research R8).

Three record kinds live in the service's database next to the run lease, so every instance sees
the same state:

- a conversation maps a thread and an agent to the A2A context id (FR-008);
- a send is one message Freyja sent to an agent, keyed by the thread, the step, and the tool
  call that sent it. A step that runs again finds its send and does not send twice (FR-010,
  FR-027);
- a task is one task an agent created for a thread: its state, the outcome waiting to be
  delivered, the instance watching it, and the background run's retries (FR-017 to FR-027).
  Rows are never deleted: the thread state lists every task the thread started.

``PostgresAgentTaskStore`` is the service's store and ``InMemoryAgentTaskStore`` the suite's.
Both take every time they set or compare from their own clock: the database's ``now()`` and
the injected clock, so instances never compare their own clocks (spec Assumptions).
"""

import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from psycopg import sql
from psycopg_pool import AsyncConnectionPool

TaskState = Literal[
    "working", "input_required", "completed", "failed", "rejected", "canceled", "given_up"
]
SendKind = Literal["handoff", "answer", "cancel"]

# A task in one of these states is still pending: the worker claims it (research R9).
PENDING_STATES: tuple[TaskState, ...] = ("working", "input_required")
FINAL_STATES: tuple[TaskState, ...] = ("completed", "failed", "rejected", "canceled", "given_up")


@dataclass(frozen=True)
class AgentSend:
    """One message sent to an agent (data-model.md "Agent send").

    A call is known by its thread, its ``step``, and its tool call id. The step is the graph
    namespace the call ran in: LangGraph gives each tool call its own, and the same one again
    when that call's step runs again. The tool call id alone is the model provider's, and
    nothing makes a provider keep it unique on a thread.
    """

    thread_id: str
    step: str
    tool_call_id: str
    agent: str
    kind: SendKind
    message_id: str
    # A digest of what was sent: the agent, the kind, and the arguments. A step that runs
    # again sends the same; a different digest under the same key is refused, not replayed.
    request: str
    sent_at: datetime
    task_id: str | None = None
    # The text the tool returned; None while the request may still be in flight.
    result: str | None = None


@dataclass(frozen=True)
class AgentTaskRecord:
    """One task an agent created for a thread (data-model.md "Agent task")."""

    agent: str
    task_id: str
    thread_id: str
    context_id: str
    message: str
    state: TaskState
    started_at: datetime
    last_checked_at: datetime
    ended_at: datetime | None = None
    outcome: str | None = None
    outcome_ready: bool = False
    delivered_at: datetime | None = None
    watcher: str | None = None
    watch_until: datetime | None = None
    followup_attempts: int = 0
    followup_next_at: datetime | None = None
    followup_error: str | None = None

    @property
    def pending(self) -> bool:
        return self.state in PENDING_STATES

    @property
    def outcome_delivered(self) -> bool:
        """What the thread state reports: the task's latest outcome is in the thread.

        True once a run recorded it, or when the hand-off returned it itself; false while the
        task is still working, and while an outcome waits to be delivered.
        """
        if self.delivered_at is not None:
            return True
        return not self.outcome_ready and self.state != "working"


# The columns an update may set; the key and the thread never change.
UPDATABLE = frozenset(
    f.name for f in fields(AgentTaskRecord) if f.name not in {"agent", "task_id", "thread_id"}
)


class AgentTaskStore(Protocol):
    async def setup(self) -> None: ...

    async def now(self) -> datetime: ...

    async def get_context(self, thread_id: str, agent: str) -> str | None: ...

    async def set_context(self, thread_id: str, agent: str, context_id: str) -> None: ...

    async def get_send(self, thread_id: str, step: str, tool_call_id: str) -> AgentSend | None: ...

    async def start_send(self, send: AgentSend) -> bool: ...

    async def finish_send(
        self,
        thread_id: str,
        step: str,
        tool_call_id: str,
        *,
        result: str,
        task_id: str | None = None,
    ) -> None: ...

    async def in_flight_sends(self, thread_id: str) -> list[AgentSend]: ...

    async def record_task(self, task: AgentTaskRecord) -> None: ...

    async def get_task(self, agent: str, task_id: str) -> AgentTaskRecord | None: ...

    async def update_task(
        self, agent: str, task_id: str, *, when_state: Collection[str] | None = None, **changes: Any
    ) -> bool: ...

    async def list_tasks(self, thread_id: str) -> list[AgentTaskRecord]: ...

    async def claim_due(self, instance_id: str, lease_seconds: float) -> list[AgentTaskRecord]: ...

    async def ready_outcomes(self, thread_id: str) -> list[AgentTaskRecord]: ...

    async def threads_with_ready_outcomes(self) -> list[str]: ...

    async def threads_with_due_followups(self) -> list[str]: ...

    async def mark_delivered(self, agent: str, task_id: str) -> None: ...

    async def set_followup(
        self,
        keys: Iterable[tuple[str, str]],
        *,
        attempts: int,
        next_at: datetime | None,
        error: str | None,
    ) -> None: ...

    async def clear_followups(self, thread_id: str) -> None: ...


def _check_columns(changes: dict[str, Any]) -> None:
    unknown = sorted(set(changes) - UPDATABLE)
    if unknown:
        raise ValueError(f"Not an updatable agent task column: {unknown}")


class InMemoryAgentTaskStore:
    """The store for the suite. ``clock`` returns seconds; tests inject one they can advance."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._contexts: dict[tuple[str, str], str] = {}
        self._sends: dict[tuple[str, str, str], AgentSend] = {}
        self._tasks: dict[tuple[str, str], AgentTaskRecord] = {}

    async def setup(self) -> None:
        return None

    async def now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), UTC)

    async def get_context(self, thread_id: str, agent: str) -> str | None:
        return self._contexts.get((thread_id, agent))

    async def set_context(self, thread_id: str, agent: str, context_id: str) -> None:
        # The first acknowledged reply decides the conversation (FR-008).
        self._contexts.setdefault((thread_id, agent), context_id)

    async def get_send(self, thread_id: str, step: str, tool_call_id: str) -> AgentSend | None:
        return self._sends.get((thread_id, step, tool_call_id))

    async def start_send(self, send: AgentSend) -> bool:
        key = (send.thread_id, send.step, send.tool_call_id)
        if key in self._sends:
            return False
        self._sends[key] = send
        return True

    async def finish_send(
        self,
        thread_id: str,
        step: str,
        tool_call_id: str,
        *,
        result: str,
        task_id: str | None = None,
    ) -> None:
        key = (thread_id, step, tool_call_id)
        send = self._sends[key]
        self._sends[key] = replace(send, result=result, task_id=task_id or send.task_id)

    async def in_flight_sends(self, thread_id: str) -> list[AgentSend]:
        sends = [s for s in self._sends.values() if s.thread_id == thread_id and s.result is None]
        return sorted(sends, key=lambda s: s.sent_at)

    async def record_task(self, task: AgentTaskRecord) -> None:
        self._tasks.setdefault((task.agent, task.task_id), task)

    async def get_task(self, agent: str, task_id: str) -> AgentTaskRecord | None:
        return self._tasks.get((agent, task_id))

    async def update_task(
        self, agent: str, task_id: str, *, when_state: Collection[str] | None = None, **changes: Any
    ) -> bool:
        _check_columns(changes)
        key = (agent, task_id)
        if when_state is not None and self._tasks[key].state not in when_state:
            return False
        self._tasks[key] = replace(self._tasks[key], **changes)
        return True

    async def list_tasks(self, thread_id: str) -> list[AgentTaskRecord]:
        tasks = [task for task in self._tasks.values() if task.thread_id == thread_id]
        return sorted(tasks, key=lambda task: task.started_at)

    async def claim_due(self, instance_id: str, lease_seconds: float) -> list[AgentTaskRecord]:
        now = await self.now()
        until = now + timedelta(seconds=lease_seconds)
        claimed = []
        for key, task in self._tasks.items():
            if not task.pending:
                continue
            free = task.watch_until is None or task.watch_until < now
            if free or task.watcher == instance_id:
                self._tasks[key] = replace(task, watcher=instance_id, watch_until=until)
                claimed.append(self._tasks[key])
        return claimed

    async def ready_outcomes(self, thread_id: str) -> list[AgentTaskRecord]:
        ready = [
            task
            for task in self._tasks.values()
            if task.thread_id == thread_id and task.outcome_ready and task.delivered_at is None
        ]
        return sorted(ready, key=lambda task: task.last_checked_at)

    async def threads_with_ready_outcomes(self) -> list[str]:
        threads = {
            task.thread_id
            for task in self._tasks.values()
            if task.outcome_ready and task.delivered_at is None
        }
        return sorted(threads)

    async def threads_with_due_followups(self) -> list[str]:
        now = await self.now()
        threads = {
            task.thread_id
            for task in self._tasks.values()
            if task.followup_next_at is not None and task.followup_next_at <= now
        }
        return sorted(threads)

    async def mark_delivered(self, agent: str, task_id: str) -> None:
        key = (agent, task_id)
        self._tasks[key] = replace(self._tasks[key], delivered_at=await self.now())

    async def set_followup(
        self,
        keys: Iterable[tuple[str, str]],
        *,
        attempts: int,
        next_at: datetime | None,
        error: str | None,
    ) -> None:
        for key in keys:
            self._tasks[key] = replace(
                self._tasks[key],
                followup_attempts=attempts,
                followup_next_at=next_at,
                followup_error=error,
            )

    async def clear_followups(self, thread_id: str) -> None:
        for key, task in self._tasks.items():
            if task.thread_id == thread_id and task.followup_attempts:
                self._tasks[key] = replace(
                    task, followup_attempts=0, followup_next_at=None, followup_error=None
                )


# Every statement is one parameterized command; identifiers come from a fixed set.
_CREATE = (
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
        PRIMARY KEY (agent, task_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS freyja_agent_tasks_thread ON freyja_agent_tasks (thread_id)",
)

_TASK_COLUMNS = [f.name for f in fields(AgentTaskRecord)]
_SEND_COLUMNS = [f.name for f in fields(AgentSend)]


def _columns(names: list[str]) -> sql.Composed:
    return sql.SQL(", ").join(sql.Identifier(name) for name in names)


def _task(row: dict[str, Any]) -> AgentTaskRecord:
    return AgentTaskRecord(**{name: row[name] for name in _TASK_COLUMNS})


def _send(row: dict[str, Any]) -> AgentSend:
    return AgentSend(**{name: row[name] for name in _SEND_COLUMNS})


class PostgresAgentTaskStore:
    """The service's store: three tables in the shared pool (research R8)."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            for statement in _CREATE:
                await conn.execute(statement)

    async def _rows(self, query: sql.Composable | str, params: dict[str, Any]) -> list[Any]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(query, params)
            return await cursor.fetchall()

    async def _execute(self, query: sql.Composable | str, params: dict[str, Any]) -> int:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(query, params)
            return cursor.rowcount

    async def now(self) -> datetime:
        rows = await self._rows("SELECT now() AS now", {})
        return rows[0]["now"]

    async def get_context(self, thread_id: str, agent: str) -> str | None:
        rows = await self._rows(
            "SELECT context_id FROM freyja_agent_conversations"
            " WHERE thread_id = %(thread_id)s AND agent = %(agent)s",
            {"thread_id": thread_id, "agent": agent},
        )
        return rows[0]["context_id"] if rows else None

    async def set_context(self, thread_id: str, agent: str, context_id: str) -> None:
        await self._execute(
            "INSERT INTO freyja_agent_conversations (thread_id, agent, context_id)"
            " VALUES (%(thread_id)s, %(agent)s, %(context_id)s) ON CONFLICT DO NOTHING",
            {"thread_id": thread_id, "agent": agent, "context_id": context_id},
        )

    async def get_send(self, thread_id: str, step: str, tool_call_id: str) -> AgentSend | None:
        rows = await self._rows(
            sql.SQL(
                "SELECT {} FROM freyja_agent_sends WHERE thread_id = %(thread_id)s"
                " AND step = %(step)s AND tool_call_id = %(tool_call_id)s"
            ).format(_columns(_SEND_COLUMNS)),
            {"thread_id": thread_id, "step": step, "tool_call_id": tool_call_id},
        )
        return _send(rows[0]) if rows else None

    async def start_send(self, send: AgentSend) -> bool:
        values = {name: getattr(send, name) for name in _SEND_COLUMNS}
        inserted = await self._execute(
            sql.SQL(
                "INSERT INTO freyja_agent_sends ({}) VALUES ({}) ON CONFLICT DO NOTHING"
            ).format(
                _columns(_SEND_COLUMNS),
                sql.SQL(", ").join(sql.Placeholder(name) for name in _SEND_COLUMNS),
            ),
            values,
        )
        return inserted == 1

    async def finish_send(
        self,
        thread_id: str,
        step: str,
        tool_call_id: str,
        *,
        result: str,
        task_id: str | None = None,
    ) -> None:
        await self._execute(
            "UPDATE freyja_agent_sends SET result = %(result)s,"
            " task_id = COALESCE(%(task_id)s, task_id)"
            " WHERE thread_id = %(thread_id)s AND step = %(step)s"
            " AND tool_call_id = %(tool_call_id)s",
            {
                "thread_id": thread_id,
                "step": step,
                "tool_call_id": tool_call_id,
                "result": result,
                "task_id": task_id,
            },
        )

    async def in_flight_sends(self, thread_id: str) -> list[AgentSend]:
        rows = await self._rows(
            sql.SQL(
                "SELECT {} FROM freyja_agent_sends"
                " WHERE thread_id = %(thread_id)s AND result IS NULL ORDER BY sent_at"
            ).format(_columns(_SEND_COLUMNS)),
            {"thread_id": thread_id},
        )
        return [_send(row) for row in rows]

    async def record_task(self, task: AgentTaskRecord) -> None:
        values = {name: getattr(task, name) for name in _TASK_COLUMNS}
        await self._execute(
            sql.SQL(
                "INSERT INTO freyja_agent_tasks ({}) VALUES ({}) ON CONFLICT DO NOTHING"
            ).format(
                _columns(_TASK_COLUMNS),
                sql.SQL(", ").join(sql.Placeholder(name) for name in _TASK_COLUMNS),
            ),
            values,
        )

    async def get_task(self, agent: str, task_id: str) -> AgentTaskRecord | None:
        rows = await self._rows(
            sql.SQL(
                "SELECT {} FROM freyja_agent_tasks WHERE agent = %(agent)s AND task_id = %(task_id)s"
            ).format(_columns(_TASK_COLUMNS)),
            {"agent": agent, "task_id": task_id},
        )
        return _task(rows[0]) if rows else None

    async def update_task(
        self, agent: str, task_id: str, *, when_state: Collection[str] | None = None, **changes: Any
    ) -> bool:
        _check_columns(changes)
        if not changes:
            return False
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(name), sql.Placeholder(name))
            for name in changes
        )
        # One statement, so a change another instance made in between is never overwritten.
        condition = sql.SQL(" AND state = ANY(%(key_states)s)" if when_state is not None else "")
        updated = await self._execute(
            sql.SQL(
                "UPDATE freyja_agent_tasks SET {}"
                " WHERE agent = %(key_agent)s AND task_id = %(key_task_id)s{}"
            ).format(assignments, condition),
            {
                **changes,
                "key_agent": agent,
                "key_task_id": task_id,
                "key_states": list(when_state or ()),
            },
        )
        return updated == 1

    async def list_tasks(self, thread_id: str) -> list[AgentTaskRecord]:
        rows = await self._rows(
            sql.SQL(
                "SELECT {} FROM freyja_agent_tasks WHERE thread_id = %(thread_id)s"
                " ORDER BY started_at, task_id"
            ).format(_columns(_TASK_COLUMNS)),
            {"thread_id": thread_id},
        )
        return [_task(row) for row in rows]

    async def claim_due(self, instance_id: str, lease_seconds: float) -> list[AgentTaskRecord]:
        rows = await self._rows(
            sql.SQL(
                "UPDATE freyja_agent_tasks"
                " SET watcher = %(watcher)s,"
                "     watch_until = now() + %(lease)s * interval '1 second'"
                " WHERE (agent, task_id) IN ("
                "     SELECT agent, task_id FROM freyja_agent_tasks"
                "      WHERE state IN ('working', 'input_required')"
                "        AND (watch_until IS NULL OR watch_until < now()"
                "             OR watcher = %(watcher)s)"
                "      FOR UPDATE SKIP LOCKED)"
                " RETURNING {}"
            ).format(_columns(_TASK_COLUMNS)),
            {"watcher": instance_id, "lease": lease_seconds},
        )
        return [_task(row) for row in rows]

    async def ready_outcomes(self, thread_id: str) -> list[AgentTaskRecord]:
        rows = await self._rows(
            sql.SQL(
                "SELECT {} FROM freyja_agent_tasks"
                " WHERE thread_id = %(thread_id)s AND outcome_ready AND delivered_at IS NULL"
                " ORDER BY last_checked_at, task_id"
            ).format(_columns(_TASK_COLUMNS)),
            {"thread_id": thread_id},
        )
        return [_task(row) for row in rows]

    async def threads_with_ready_outcomes(self) -> list[str]:
        rows = await self._rows(
            "SELECT DISTINCT thread_id FROM freyja_agent_tasks"
            " WHERE outcome_ready AND delivered_at IS NULL ORDER BY thread_id",
            {},
        )
        return [row["thread_id"] for row in rows]

    async def threads_with_due_followups(self) -> list[str]:
        rows = await self._rows(
            "SELECT DISTINCT thread_id FROM freyja_agent_tasks"
            " WHERE followup_next_at IS NOT NULL AND followup_next_at <= now() ORDER BY thread_id",
            {},
        )
        return [row["thread_id"] for row in rows]

    async def mark_delivered(self, agent: str, task_id: str) -> None:
        await self._execute(
            "UPDATE freyja_agent_tasks SET delivered_at = now()"
            " WHERE agent = %(agent)s AND task_id = %(task_id)s AND delivered_at IS NULL",
            {"agent": agent, "task_id": task_id},
        )

    async def set_followup(
        self,
        keys: Iterable[tuple[str, str]],
        *,
        attempts: int,
        next_at: datetime | None,
        error: str | None,
    ) -> None:
        for agent, task_id in keys:
            await self.update_task(
                agent,
                task_id,
                followup_attempts=attempts,
                followup_next_at=next_at,
                followup_error=error,
            )

    async def clear_followups(self, thread_id: str) -> None:
        await self._execute(
            "UPDATE freyja_agent_tasks"
            " SET followup_attempts = 0, followup_next_at = NULL, followup_error = NULL"
            " WHERE thread_id = %(thread_id)s AND followup_attempts > 0",
            {"thread_id": thread_id},
        )
