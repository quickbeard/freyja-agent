"""The tasks other agents hand Freyja over A2A: accepting one, and where it stands (feature 009).

``ReceivedTasks`` is the feature's service. It imports nothing of the protocol's SDK: the
routes of ``api/a2a_server.py`` turn its tasks into the protocol's types, so everything here
is tested without them.

A caller's message starts the run the console starts, on a thread the service made, through
``api/runs.py`` (research R10). Everything a caller is told of its task is read from that
thread, so a received task survives a restart and reads the same from every instance; once
it has ended, what it ended as is written once and never read from the thread again
(research R7).

Where a thread stands is one table of rules over four reads, the lease, the thread's state,
its agent tasks, and its row in the index, first match:

- the task's run has not taken the lease yet, or the lease is held: working;
- the state holds an interrupt: working, waiting for a person;
- an agent task is awaited: working, waiting for that agent;
- the index's note of how the last console run ended is this task's: failed with the note's
  reason, or canceled as stopped;
- the thread recorded nothing since the task started, or has a next step: failed, as
  interrupted;
- otherwise: completed, with Freyja's last reply as the result.

Three fences keep the reads, which are not one, from ending a task wrongly: a task is
working until its run is known to have started; an end is written only when the lease and
the checkpoint read again agree with the look; a note counts only when it is not older than
the task's start nor than the thread's last checkpoint.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage

from freyja_agent.api.runs import Events, awaited, start_console_run, state_of
from freyja_agent.api.serialize import interrupt_to_pending
from freyja_agent.runtime.received_tasks import ReceivedTask, Refusal, TaskState, kept
from freyja_agent.runtime.runtime import Runtime
from freyja_agent.runtime.thread_index import ThreadRecord

logger = logging.getLogger(__name__)

# How long a task may be accepted with no run started: past it, the process that accepted
# it died before the run took the thread's lease (research R7).
UNSTARTED_SECONDS = 60
# A failed run's code, as the index notes it, to the fixed reason a caller is given.
_REASONS = {
    "no_usable_model": "no_usable_model",
    "model_error": "model_error",
    "step_limit": "step_limit",
}


@dataclass(frozen=True)
class Standing:
    """Where a caller's thread stands, for its task (research R7)."""

    state: TaskState
    reason: str | None = None
    result: str | None = None
    # What a working task waits for, and the names of the actions or the agents.
    waiting: Literal["person", "agent"] | None = None
    names: tuple[str, ...] = ()
    # The checkpoint the look read; an end is written only while the thread is still there.
    checkpoint: str | None = None


class RefusedError(Exception):
    """A message that was not accepted: its conversation is busy, or a limit was reached."""

    def __init__(self, why: Refusal) -> None:
        super().__init__(why)
        self.why = why


@dataclass(frozen=True)
class Accepted:
    """An accepted message: its task, and the first reader of the run it started."""

    task: ReceivedTask
    events: Events


def _checkpoint(snapshot: Any) -> str | None:
    config = getattr(snapshot, "config", None) or {}
    return config.get("configurable", {}).get("checkpoint_id")


def _checkpointed_at(snapshot: Any) -> datetime | None:
    created = getattr(snapshot, "created_at", None)
    return datetime.fromisoformat(created) if created else None


def _last_reply(snapshot: Any) -> str | None:
    """Freyja's last reply since the thread's last message from its caller; None when it
    wrote none. An earlier task's reply is never taken for this one's."""
    messages = (getattr(snapshot, "values", None) or {}).get("messages", [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return None
        if isinstance(message, AIMessage) and message.text:
            return message.text
    return None


def _action_name(request: dict[str, Any]) -> str:
    agent = request.get("agent")
    return f"agent {agent}" if agent else str(request.get("name"))


class ReceivedTasks:
    """Accepts callers' messages and reads where each task stands, for one runtime."""

    def __init__(self, runtime: Runtime, *, clock: Callable[[], datetime] | None = None) -> None:
        self.runtime = runtime
        self.store = runtime.received_tasks
        # The clock a task's start is written by: this instance's, the one a run's note in
        # the index is written by (research R7).
        self._clock = clock or (lambda: datetime.now(UTC))
        self._looks: set[asyncio.Task[None]] = set()

    async def accept(self, caller: str, context_id: str, text: str, *, hops: int = 0) -> Accepted:
        """Accept a message of ``caller`` in its conversation ``context_id`` and start its run.

        The tasks that still count against the limits are looked at first, so that one
        whose end was not written yet does not count (data-model.md, "Accepting a message").
        The conversation and the task are written before the run starts; the run is started
        as the console starts one, and refuses under the lease a thread that is not at rest.
        Raises ``RefusedError`` for a busy conversation or a limit, and nothing is started.
        """
        settings = self.runtime.settings
        for counted in await self.store.unreleased(settings.a2a_server_tasks):
            try:
                await self.look(counted)
            except Exception as exc:
                # A thread that cannot be read now keeps its task counted; it does not stop
                # every caller's next message.
                logger.warning(
                    "Received task %s could not be looked at (%s); it still counts",
                    counted.task_id,
                    type(exc).__name__,
                )
        acceptance = await self.store.accept(
            caller,
            context_id,
            hops=hops,
            caller_limit=settings.a2a_server_caller_tasks,
            total_limit=settings.a2a_server_tasks,
        )
        task = acceptance.task
        if task is None:
            why = acceptance.refused or "busy"
            if why != "busy":
                logger.warning(
                    "A message of caller %s was refused: the %s on tasks in progress was reached",
                    caller,
                    "caller's limit" if why == "caller_limit" else "total limit",
                )
            raise RefusedError(why)

        async def started(snapshot: Any) -> None:
            if not await self.store.mark_started(
                task.task_id, self._clock(), _checkpoint(snapshot)
            ):
                raise RuntimeError(f"received task {task.task_id} could not be marked started")

        try:
            events = await start_console_run(
                self.runtime,
                task.thread_id,
                text,
                refuse_awaited=True,
                on_started=started,
            )
        except HTTPException as exc:
            await self.store.remove(task.task_id)
            if exc.status_code == 409:
                raise RefusedError("busy") from None
            raise
        except BaseException:
            await self.store.remove(task.task_id)
            raise
        found = await self.store.get(task.task_id) or task
        if found.started_at is None:
            # The run's context could not be taken, and its stream reports an internal
            # error: the task has ended, failed.
            await self.store.end(task.task_id, "failed", reason="internal", released=True)
            found = await self.store.get(task.task_id) or found
        logger.info(
            "Caller %s: received task %s accepted, on thread %s",
            caller,
            task.task_id,
            task.thread_id,
        )
        return Accepted(found, events)

    async def standing(self, task: ReceivedTask) -> Standing:
        """Where ``task``'s thread stands, by the table of research R7; reads, writes nothing."""
        if task.ended:
            return Standing(task.state, task.reason, task.result)
        if task.started_at is None:
            return Standing("working")
        runtime, thread_id = self.runtime, task.thread_id
        if await runtime.locks.is_active(thread_id):
            return Standing("working")
        context = await runtime.begin_read(thread_id)
        try:
            snapshot = await state_of(context, thread_id)
            catalog = context.catalog
        finally:
            await context.release()
        checkpoint = _checkpoint(snapshot)
        if snapshot.interrupts:
            pending = interrupt_to_pending(snapshot.interrupts[0], catalog)
            names = tuple(_action_name(request) for request in pending["action_requests"])
            return Standing("working", waiting="person", names=names, checkpoint=checkpoint)
        agents = sorted(
            {
                found.agent
                for found in await runtime.agent_tasks.list_tasks(thread_id)
                if awaited(found)
            }
        )
        if agents:
            return Standing("working", waiting="agent", names=tuple(agents), checkpoint=checkpoint)
        row = await runtime.threads.get(thread_id)
        if row is not None and self._its_own(row, task, snapshot):
            if row.noted == "failed":
                reason = _REASONS.get(row.error_code or "", "internal")
                return Standing("failed", reason=reason, checkpoint=checkpoint)
            return Standing("canceled", reason="stopped", checkpoint=checkpoint)
        if checkpoint == task.base_checkpoint or snapshot.next:
            return Standing("failed", reason="interrupted", checkpoint=checkpoint)
        return Standing("completed", result=kept(_last_reply(snapshot)), checkpoint=checkpoint)

    @staticmethod
    def _its_own(row: ThreadRecord, task: ReceivedTask, snapshot: Any) -> bool:
        """Whether the index's note is of this task's runs: not older than the task's start
        and not older than the thread's last checkpoint (research R7, "Whose note")."""
        if row.noted is None or row.noted_at is None or task.started_at is None:
            return False
        if row.noted_at < task.started_at:
            return False
        checkpointed = _checkpointed_at(snapshot)
        return checkpointed is None or row.noted_at >= checkpointed

    async def look(self, task: ReceivedTask) -> ReceivedTask:
        """Look at ``task``'s thread and write its end when it has ended; the task as it is.

        An end is written only from a consistent look: the lease is read again and the
        thread's checkpoint compared with the one the look read, and the write names the
        state it replaces, so two instances that look together write one end. A task that
        ends is released in the same write: a look ends a task only when nothing of its
        thread is in progress, pending, or awaited.
        """
        if task.ended:
            return task
        store = self.store
        if task.started_at is None:
            if await store.now() - task.created_at > timedelta(seconds=UNSTARTED_SECONDS):
                await store.end(task.task_id, "failed", reason="interrupted", released=True)
                logger.warning(
                    "Received task %s of caller %s never started its run; it failed",
                    task.task_id,
                    task.caller,
                )
                return await store.get(task.task_id) or task
            return task
        standing = await self.standing(task)
        if standing.state == "working":
            return task
        runtime, thread_id = self.runtime, task.thread_id
        if await runtime.locks.is_active(thread_id):
            return task
        context = await runtime.begin_read(thread_id)
        try:
            again = _checkpoint(await state_of(context, thread_id))
        finally:
            await context.release()
        if again != standing.checkpoint:
            return task
        if await store.end(
            task.task_id,
            standing.state,
            reason=standing.reason,
            result=standing.result if standing.state == "completed" else None,
            released=True,
        ):
            logger.info(
                "Caller %s: received task %s ended, %s",
                task.caller,
                task.task_id,
                standing.state
                if standing.reason is None
                else f"{standing.state} ({standing.reason})",
            )
        return await store.get(task.task_id) or task

    async def released(self, thread_id: str) -> None:
        """What follows the end of any run on a thread: its last task is looked at (R9)."""
        task = await self.store.last_task_of_thread(thread_id)
        if task is not None and not task.ended:
            await self.look(task)

    def on_release(self, thread_id: str) -> None:
        """Given to the lease: start ``released`` in a task of its own, never waiting."""
        look = asyncio.create_task(self._released(thread_id), name=f"freyja-look-{thread_id}")
        self._looks.add(look)
        look.add_done_callback(self._looks.discard)

    async def _released(self, thread_id: str) -> None:
        try:
            await self.released(thread_id)
        except Exception as exc:
            logger.warning(
                "Thread %s: its received task could not be looked at after a run ended (%s)",
                thread_id,
                type(exc).__name__,
            )

    async def close(self) -> None:
        """Wait for the looks under way; the service is stopping."""
        if self._looks:
            await asyncio.gather(*self._looks, return_exceptions=True)
