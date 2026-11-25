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

Feature 005 adds which agent, at which address, a task was started with (``agent_ref``), so
that a saved agent's tasks can be told from the tasks of another agent under its name
(005 research R2, R10). It also adds a fourth record kind:

- a proposed call is one call of an agent's tool that a model has proposed, kept from the
  model's reply until the call's send is finished. A pause holds a tool's name and nothing
  about which agent held the name then; the row does, so a proposal is never sent to
  another agent that has the name by the time it is decided (005 FR-023, research R8).

Feature 008 adds a task's kept progress (008 research R2): the text an agent last attached to a
working status, in its own words, and when the service first learned it (``progress``,
``progress_at``). The hand-off and the worker write it through the statements they already
make, and nothing that ends a task or gives it up names it, so a task keeps the last progress
learned while it was working (008 FR-007).

``PostgresAgentTaskStore`` is the service's store and ``InMemoryAgentTaskStore`` the suite's.
Both take every time they set or compare from their own clock: the database's ``now()`` and
the injected clock, so instances never compare their own clocks (spec Assumptions).
"""

import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from psycopg import errors, sql
from psycopg_pool import AsyncConnectionPool

TaskState = Literal[
    "working", "input_required", "completed", "failed", "rejected", "canceled", "given_up"
]
SendKind = Literal["handoff", "answer", "cancel"]

# A task in one of these states is still pending: the worker claims it (research R9).
PENDING_STATES: tuple[TaskState, ...] = ("working", "input_required")
FINAL_STATES: tuple[TaskState, ...] = ("completed", "failed", "rejected", "canceled", "given_up")

# What a thread is told when Freyja stops waiting for a task. Each takes the task's id and the
# agent's name; ``given_up_text`` fills them in, and the Postgres stores do the same in SQL.
# The first two are feature 003's (FR-021, FR-023); the third is for a saved agent whose
# address was changed (005 FR-024): it offers no cancel, because the task cannot be reached
# through Freyja any more.
GIVEN_UP_REMOVED = (
    "Freyja stopped waiting for task {task_id} of agent '{agent}', which is no longer "
    "configured; the agent may still be working on it."
)
GIVEN_UP_AGED = (
    "Freyja stopped waiting for task {task_id} of agent '{agent}'; the agent may still be "
    "working on it. Ask to cancel it if it should stop."
)
GIVEN_UP_MOVED = (
    "Freyja stopped waiting for task {task_id} of agent '{agent}', which was moved to another "
    "address; the agent may still be working on it where it was."
)


def given_up_text(template: str, agent: str, task_id: str) -> str:
    """``template`` with the task's id and the agent's name filled in."""
    return template.replace("{task_id}", task_id).replace("{agent}", agent)


class AgentTaskSetupError(RuntimeError):
    """The task tables could not be brought up to date at startup. The service does not start."""


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
    # Which agent, at which address, the task was started with: ``<id>:<address_version>``
    # for a saved agent. None for an environment agent's task, which follows the agent's
    # name, and for every task recorded before feature 005 (005 research R2).
    agent_ref: str | None = None
    # The progress text the service last learned for the task: at most 2,000 characters, in
    # the agent's own words. None for a task that has none (008 data-model.md).
    progress: str | None = None
    # When the service first learned that text, on the store's clock. None exactly when
    # ``progress`` is None: the two are always written together.
    progress_at: datetime | None = None

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


@dataclass(frozen=True)
class ProposedCall:
    """One call of an agent's tool that a model proposed (005 data-model.md "Proposed call").

    The key is the thread, the namespace, and the tool call id, as the tool knows them when
    it runs. The tool call id alone is the model provider's, and two subagents of one step
    may be given the same one.
    """

    thread_id: str
    # The graph namespace the model's node and the tool's node share (``call_namespace``).
    namespace: str
    tool_call_id: str
    # The agent's name, and the saved agent's id when the call was proposed; None for an
    # environment agent.
    agent: str
    agent_id: str | None
    # Whether the call needed a person's decision when it was proposed.
    gated: bool
    # Whether a person approved or edited the call, as the approval gate received it in a
    # resume (``AgentTasksMiddleware``). Only this lets a gated tool send it (005 FR-022).
    decided: bool = False
    # When it was proposed, on the store's clock. The store sets it.
    proposed_at: datetime | None = None


def call_namespace(checkpoint_ns: str) -> str:
    """The namespace a proposed call is kept under: ``checkpoint_ns`` without its last segment.

    LangGraph runs the model and each tool call as tasks of their own, each in a namespace
    that ends in the node and the task's id. What the model that proposes a call and the
    tool that carries it out share is what comes before: nothing on the orchestrator, where
    they run in ``model:<id>`` and ``tools:<id>``, and the subagent's own namespace inside a
    subagent, where they run in ``tools:<x>|model:<id>`` and ``tools:<x>|tools:<id>``.
    """
    return checkpoint_ns.rpartition("|")[0]


def _latest(calls: Iterable[ProposedCall]) -> list[ProposedCall]:
    """One call per tool call id: the later one, where a reply uses an id twice."""
    return list({call.tool_call_id: call for call in calls}.values())


# The columns an update may set; the key, the thread, and the agent a task was started with
# never change.
UPDATABLE = frozenset(
    f.name
    for f in fields(AgentTaskRecord)
    if f.name not in {"agent", "task_id", "thread_id", "agent_ref"}
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

    async def mark_delivered(
        self, agent: str, task_id: str, outcome: str | None = None
    ) -> None: ...

    async def set_followup(
        self,
        keys: Iterable[tuple[str, str]],
        *,
        attempts: int,
        next_at: datetime | None,
        error: str | None,
    ) -> None: ...

    async def clear_followups(self, thread_id: str) -> None: ...

    async def give_up_pending(self, refs: Collection[str], text: str) -> int: ...

    async def pending_counts(self) -> dict[tuple[str, str | None], int]: ...

    async def replace_calls(
        self, thread_id: str, namespace: str, calls: Iterable[ProposedCall]
    ) -> None: ...

    async def get_call(
        self, thread_id: str, namespace: str, tool_call_id: str
    ) -> ProposedCall | None: ...

    async def list_calls(self, thread_id: str) -> list[ProposedCall]: ...

    async def mark_decided(
        self, thread_id: str, namespace: str, tool_call_ids: Collection[str]
    ) -> None: ...

    async def delete_call(self, thread_id: str, namespace: str, tool_call_id: str) -> None: ...

    async def clear_calls(self, thread_id: str) -> None: ...


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
        self._calls: dict[tuple[str, str, str], ProposedCall] = {}

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
        task = self._tasks.get(key)
        # As the Postgres store answers: no row, or nothing to set, updates nothing.
        if task is None or not changes:
            return False
        if when_state is not None and task.state not in when_state:
            return False
        self._tasks[key] = replace(task, **changes)
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

    async def mark_delivered(self, agent: str, task_id: str, outcome: str | None = None) -> None:
        """Record that the task's outcome is in its thread.

        ``outcome`` is the text that was delivered. Given it, the task is marked only while
        that is still its outcome: a task given up while another outcome of it was on its
        way to the thread keeps its report to be delivered (005 FR-024, SC-010).
        """
        key = (agent, task_id)
        task = self._tasks.get(key)
        # The first delivery is the one kept, and a task that does not exist is left alone.
        if task is None or task.delivered_at is not None:
            return
        if outcome is not None and task.outcome != outcome:
            return
        self._tasks[key] = replace(task, delivered_at=await self.now())

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
        for key, task in self._tasks.items():
            if task.thread_id == thread_id and task.followup_attempts:
                self._tasks[key] = replace(
                    task, followup_attempts=0, followup_next_at=None, followup_error=None
                )

    async def give_up_pending(self, refs: Collection[str], text: str) -> int:
        """Give up every pending task started with one of ``refs``; return how many.

        ``text`` is one of the ``GIVEN_UP_*`` templates. Each task gets its outcome ready, so
        a background run tells its thread, as for any task given up (005 FR-024).
        """
        wanted = set(refs)
        now = await self.now()
        given_up = 0
        for key, task in self._tasks.items():
            if task.pending and task.agent_ref is not None and task.agent_ref in wanted:
                self._tasks[key] = replace(
                    task,
                    state="given_up",
                    last_checked_at=now,
                    ended_at=now,
                    outcome=given_up_text(text, task.agent, task.task_id),
                    outcome_ready=True,
                    delivered_at=None,
                )
                given_up += 1
        return given_up

    async def pending_counts(self) -> dict[tuple[str, str | None], int]:
        """How many tasks are pending, by the agent's name and the ref they were started with."""
        counts: dict[tuple[str, str | None], int] = {}
        for task in self._tasks.values():
            if task.pending:
                key = (task.agent, task.agent_ref)
                counts[key] = counts.get(key, 0) + 1
        return counts

    async def replace_calls(
        self, thread_id: str, namespace: str, calls: Iterable[ProposedCall]
    ) -> None:
        """Replace the calls kept for one thread and namespace by ``calls`` (005 research R8).

        Each is kept under ``thread_id`` and ``namespace`` and stamped by the store's clock,
        whatever it says of itself.
        """
        now = await self.now()
        for key in [key for key in self._calls if key[:2] == (thread_id, namespace)]:
            del self._calls[key]
        for call in _latest(calls):
            self._calls[(thread_id, namespace, call.tool_call_id)] = replace(
                call, thread_id=thread_id, namespace=namespace, decided=False, proposed_at=now
            )

    async def get_call(
        self, thread_id: str, namespace: str, tool_call_id: str
    ) -> ProposedCall | None:
        return self._calls.get((thread_id, namespace, tool_call_id))

    async def list_calls(self, thread_id: str) -> list[ProposedCall]:
        calls = [call for call in self._calls.values() if call.thread_id == thread_id]
        return sorted(calls, key=lambda call: (call.namespace, call.tool_call_id))

    async def mark_decided(
        self, thread_id: str, namespace: str, tool_call_ids: Collection[str]
    ) -> None:
        """Record that a person approved or edited each of these calls; a call with no row
        stays without one."""
        for tool_call_id in tool_call_ids:
            key = (thread_id, namespace, tool_call_id)
            if key in self._calls:
                self._calls[key] = replace(self._calls[key], decided=True)

    async def delete_call(self, thread_id: str, namespace: str, tool_call_id: str) -> None:
        self._calls.pop((thread_id, namespace, tool_call_id), None)

    async def clear_calls(self, thread_id: str) -> None:
        for key in [key for key in self._calls if key[0] == thread_id]:
            del self._calls[key]


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
        agent_ref         text,
        progress          text,
        progress_at       timestamptz,
        PRIMARY KEY (agent, task_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS freyja_agent_tasks_thread ON freyja_agent_tasks (thread_id)",
    # Feature 005, created with its first user (research R8). Every read and every delete is
    # by the thread, or by the thread and the namespace, which the primary key serves.
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
)

# Feature 005 is the first to add a column to a table that exists in deployments. Adding a
# nullable column rewrites nothing, but it takes a lock that waits behind any long reader of
# the table, and every query on the table then waits behind it. So the column is looked for
# first, and added only when it is missing, under a lock timeout (005 research R2).
_HAS_COLUMN = (
    "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()"
    " AND table_name = %(table)s AND column_name = %(column)s"
)
# The columns added to tables that exist in deployments, with the statement that adds each:
# a task's ``agent_ref`` (005 research R2), a proposed call's ``decided``, which the table
# created by 005's fifth phase lacks, and a task's kept progress and its time (008 research
# R2). A boolean with a constant default, and a nullable column with none, are added without
# rewriting the table.
_ADDED_COLUMNS = (
    (
        "freyja_agent_tasks",
        "agent_ref",
        "ALTER TABLE freyja_agent_tasks ADD COLUMN IF NOT EXISTS agent_ref text",
    ),
    (
        "freyja_agent_calls",
        "decided",
        "ALTER TABLE freyja_agent_calls ADD COLUMN IF NOT EXISTS decided boolean NOT NULL"
        " DEFAULT false",
    ),
    (
        "freyja_agent_tasks",
        "progress",
        "ALTER TABLE freyja_agent_tasks ADD COLUMN IF NOT EXISTS progress text",
    ),
    (
        "freyja_agent_tasks",
        "progress_at",
        "ALTER TABLE freyja_agent_tasks ADD COLUMN IF NOT EXISTS progress_at timestamptz",
    ),
)
LOCK_TIMEOUT = "5s"

_TASK_COLUMNS = [f.name for f in fields(AgentTaskRecord)]
_SEND_COLUMNS = [f.name for f in fields(AgentSend)]
_CALL_COLUMNS = [f.name for f in fields(ProposedCall)]
_INSERT_CALL = (
    "INSERT INTO freyja_agent_calls (thread_id, namespace, tool_call_id, agent, agent_id, gated)"
    " VALUES (%(thread_id)s, %(namespace)s, %(tool_call_id)s, %(agent)s, %(agent_id)s,"
    " %(gated)s)"
)
_DELETE_CALLS = (
    "DELETE FROM freyja_agent_calls WHERE thread_id = %(thread_id)s AND namespace = %(namespace)s"
)
_MARK_DECIDED = (
    "UPDATE freyja_agent_calls SET decided = true WHERE thread_id = %(thread_id)s"
    " AND namespace = %(namespace)s AND tool_call_id = ANY(%(tool_call_ids)s)"
)


# Gives pending tasks up with one of the ``GIVEN_UP_*`` templates as ``%(text)s``; the caller
# adds which tasks. The saved-agent store runs it inside the transaction that moves or
# removes an agent, so the change and the giving up happen together or not at all (FR-024).
GIVE_UP = (
    "UPDATE freyja_agent_tasks"
    " SET state = 'given_up', last_checked_at = now(), ended_at = now(),"
    "     outcome = replace(replace(%(text)s, '{task_id}', task_id), '{agent}', agent),"
    "     outcome_ready = true, delivered_at = NULL"
    " WHERE state IN ('working', 'input_required')"
)


def _columns(names: list[str]) -> sql.Composed:
    return sql.SQL(", ").join(sql.Identifier(name) for name in names)


def _task(row: dict[str, Any]) -> AgentTaskRecord:
    return AgentTaskRecord(**{name: row[name] for name in _TASK_COLUMNS})


def _send(row: dict[str, Any]) -> AgentSend:
    return AgentSend(**{name: row[name] for name in _SEND_COLUMNS})


def _call(row: dict[str, Any]) -> ProposedCall:
    return ProposedCall(**{name: row[name] for name in _CALL_COLUMNS})


# Semgrep's rule for SQLAlchemy takes a statement composed with psycopg's ``sql`` module for
# one built from strings, so each such statement below is marked ``nosemgrep`` for that rule.
# Their parts are constants and identifiers of this file, and every value is a parameter.
class PostgresAgentTaskStore:
    """The service's store: four tables in the shared pool (003 research R8, 005 research R8)."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            for statement in _CREATE:
                await conn.execute(statement)
            for table, column, add in _ADDED_COLUMNS:
                cursor = await conn.execute(_HAS_COLUMN, {"table": table, "column": column})
                if await cursor.fetchone() is not None:
                    continue
                try:
                    async with conn.transaction():
                        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                        await conn.execute(
                            sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(LOCK_TIMEOUT))
                        )
                        await conn.execute(add)
                except errors.LockNotAvailable:
                    raise AgentTaskSetupError(
                        f"The table {table} needs a new column, {column}, and could not be "
                        f"changed within {LOCK_TIMEOUT}: another session is holding the table. "
                        "Start the service again once that session has ended."
                    ) from None

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

    async def mark_delivered(self, agent: str, task_id: str, outcome: str | None = None) -> None:
        # Only while ``outcome`` is still the task's, when it is given (005 FR-024).
        await self._execute(
            "UPDATE freyja_agent_tasks SET delivered_at = now()"
            " WHERE agent = %(agent)s AND task_id = %(task_id)s AND delivered_at IS NULL"
            " AND (%(outcome)s::text IS NULL OR outcome = %(outcome)s::text)",
            {"agent": agent, "task_id": task_id, "outcome": outcome},
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

    async def give_up_pending(self, refs: Collection[str], text: str) -> int:
        if not refs:
            return 0
        return await self._execute(
            GIVE_UP + " AND agent_ref = ANY(%(refs)s)", {"refs": list(refs), "text": text}
        )

    async def pending_counts(self) -> dict[tuple[str, str | None], int]:
        rows = await self._rows(
            "SELECT agent, agent_ref, count(*) AS count FROM freyja_agent_tasks"
            " WHERE state IN ('working', 'input_required') GROUP BY agent, agent_ref",
            {},
        )
        return {(row["agent"], row["agent_ref"]): row["count"] for row in rows}

    async def replace_calls(
        self, thread_id: str, namespace: str, calls: Iterable[ProposedCall]
    ) -> None:
        """Replace the calls kept for one thread and namespace by ``calls`` (005 research R8).

        One transaction: a replacement that fails leaves the earlier rows, and the model call
        it was made for fails with it.
        """
        place = {"thread_id": thread_id, "namespace": namespace}
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_DELETE_CALLS, place)
            for call in _latest(calls):
                await conn.execute(
                    _INSERT_CALL,
                    {
                        **place,
                        "tool_call_id": call.tool_call_id,
                        "agent": call.agent,
                        "agent_id": call.agent_id,
                        "gated": call.gated,
                    },
                )

    async def get_call(
        self, thread_id: str, namespace: str, tool_call_id: str
    ) -> ProposedCall | None:
        rows = await self._rows(
            sql.SQL(
                "SELECT {} FROM freyja_agent_calls WHERE thread_id = %(thread_id)s"
                " AND namespace = %(namespace)s AND tool_call_id = %(tool_call_id)s"
            ).format(_columns(_CALL_COLUMNS)),
            {"thread_id": thread_id, "namespace": namespace, "tool_call_id": tool_call_id},
        )
        return _call(rows[0]) if rows else None

    async def list_calls(self, thread_id: str) -> list[ProposedCall]:
        rows = await self._rows(
            sql.SQL(
                "SELECT {} FROM freyja_agent_calls WHERE thread_id = %(thread_id)s"
                " ORDER BY namespace, tool_call_id"
            ).format(_columns(_CALL_COLUMNS)),
            {"thread_id": thread_id},
        )
        return [_call(row) for row in rows]

    async def mark_decided(
        self, thread_id: str, namespace: str, tool_call_ids: Collection[str]
    ) -> None:
        if not tool_call_ids:
            return
        await self._execute(
            _MARK_DECIDED,
            {
                "thread_id": thread_id,
                "namespace": namespace,
                "tool_call_ids": list(tool_call_ids),
            },
        )

    async def delete_call(self, thread_id: str, namespace: str, tool_call_id: str) -> None:
        await self._execute(
            _DELETE_CALLS + " AND tool_call_id = %(tool_call_id)s",
            {"thread_id": thread_id, "namespace": namespace, "tool_call_id": tool_call_id},
        )

    async def clear_calls(self, thread_id: str) -> None:
        await self._execute(
            "DELETE FROM freyja_agent_calls WHERE thread_id = %(thread_id)s",
            {"thread_id": thread_id},
        )
