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
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage

from freyja_agent.api.runs import Reader, awaited, start_console_run, state_of
from freyja_agent.api.serialize import interrupt_to_pending
from freyja_agent.delegates.addresses import SCRUBBER
from freyja_agent.llm.secrets import redact
from freyja_agent.runtime.received_tasks import ReceivedTask, Refusal, TaskState, kept
from freyja_agent.runtime.runtime import Runtime
from freyja_agent.runtime.thread_index import ThreadRecord

logger = logging.getLogger(__name__)

# How long a task may be accepted with no run started: past it, the process that accepted
# it died before the run took the thread's lease (research R7).
UNSTARTED_SECONDS = 60
# How long a follower waits to be woken before it looks at its task's thread (research R13).
FOLLOW_SECONDS = 4
# A failed run's code, as the index notes it, to the fixed reason a caller is given.
_REASONS = {
    "no_usable_model": "no_usable_model",
    "model_error": "model_error",
    "step_limit": "step_limit",
}

# What a caller is told, each a fixed text of contracts/a2a-server.md: the reason of a task
# that failed or was canceled, by its code (data-model.md, "Reasons").
REASON_SENTENCES = {
    "no_usable_model": "No usable model is active.",
    "model_error": "The model could not be used.",
    "step_limit": "The run reached its step limit.",
    "interrupted": "The run was interrupted.",
    "internal": "An internal error.",
    "stopped": "The run was stopped before it finished.",
    "canceled": "The caller canceled the task.",
}
WORKING = "Working."
# The name a status gives work Freyja handed its subagent.
SUBAGENT_TOOL = "task"
SUBAGENT = "a subagent"
UNKNOWN_TOOL = "a tool Freyja does not have"


def _listed(names: Iterable[str]) -> str:
    return ", ".join(dict.fromkeys(names))


def using(names: Iterable[str]) -> str:
    """A step of Freyja's own asked for actions."""
    return f"Freyja is using: {_listed(names)}."


def completed(names: Iterable[str]) -> str:
    """A step that ran them completed."""
    return f"Completed: {_listed(names)}."


def waiting_for_person(names: Iterable[str]) -> str:
    """The run paused on a proposal."""
    return f"Waiting for a person's approval of: {_listed(names)}."


def waiting_for_agent(names: Iterable[str]) -> str:
    """The run ended and an agent task is awaited."""
    return f"Waiting for agent {_listed(names)}."


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
    events: Reader


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


def _action_name(action: dict[str, Any]) -> str:
    """An action of a proposal as a status names it: its agent, the subagent, or its tool."""
    agent = action.get("agent")
    if agent:
        return f"agent {agent}"
    name = str(action.get("name"))
    return SUBAGENT if name == SUBAGENT_TOOL else name


@dataclass(frozen=True)
class View:
    """A received task as its caller is told it: the state, the status's sentence, and the
    result, every text of which was read from the thread and cleaned (research R13)."""

    task: ReceivedTask
    state: TaskState
    # A working task's sentence, or a failed or canceled task's reason; None when completed.
    sentence: str | None
    result: str | None


@dataclass(frozen=True)
class Status:
    """A working task's status changed: its sentence."""

    sentence: str


@dataclass(frozen=True)
class Piece:
    """A piece of the text of Freyja's own reply, as it is written."""

    text: str


@dataclass(frozen=True)
class Ended:
    """The task has ended, as ``view`` says."""

    view: View


Change = Status | Piece | Ended


def _event_data(event: Any) -> dict[str, Any]:
    try:
        data = json.loads(event.data) if isinstance(event.data, str) else event.data
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _text_of(content: Any) -> str:
    """The text of a message's content, as the run's stream carries it: a string, or blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block
            if isinstance(block, str)
            else str(block.get("text") or "")
            if isinstance(block, dict) and block.get("type") == "text"
            else ""
            for block in content
        )
    return ""


def _ready(text: str) -> tuple[str, str]:
    """``text`` split after its last whitespace: what can be cleaned and sent now, and what
    is held for the pieces that follow. An address or a key holds no whitespace, so one
    that a model writes over several pieces is whole in what is sent, and cleaned there;
    what follows the last whitespace may be the start of one (research R13)."""
    for index in range(len(text) - 1, -1, -1):
        if text[index].isspace():
            return text[: index + 1], text[index + 1 :]
    return "", text


def _messages_of(update: Any) -> list[dict[str, Any]]:
    messages = update.get("messages") if isinstance(update, dict) else None
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]


class ReceivedTasks:
    """Accepts callers' messages and reads where each task stands, for one runtime."""

    def __init__(self, runtime: Runtime, *, clock: Callable[[], datetime] | None = None) -> None:
        self.runtime = runtime
        self.store = runtime.received_tasks
        # The clock a task's start is written by: this instance's, the one a run's note in
        # the index is written by (research R7).
        self._clock = clock or (lambda: datetime.now(UTC))
        self._looks: set[asyncio.Task[None]] = set()
        # The readers of runs nobody follows, read to their end; cancelled when the service
        # stops, which leaves each run to stop at its next boundary as any console run.
        self._drains: set[asyncio.Task[None]] = set()
        # The followers of a thread's task on this instance, woken when a run on it starts
        # or ends.
        self._wakers: dict[str, set[asyncio.Event]] = {}
        # Readers of the runs that started on a follower's thread, joined for the follower
        # before each run's first event, in the order the runs started, by its wake.
        self._joined: dict[asyncio.Event, list[Reader]] = {}

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
        return (await self._looked(task))[0]

    async def _looked(self, task: ReceivedTask) -> tuple[ReceivedTask, Standing]:
        """``look``, with where the thread stood for a task that is still working."""
        if task.ended:
            return task, Standing(task.state, task.reason, task.result)
        store = self.store
        if task.started_at is None:
            if await store.now() - task.created_at > timedelta(seconds=UNSTARTED_SECONDS):
                await store.end(task.task_id, "failed", reason="interrupted", released=True)
                logger.warning(
                    "Received task %s of caller %s never started its run; it failed",
                    task.task_id,
                    task.caller,
                )
                ended = await store.get(task.task_id) or task
                return ended, Standing(ended.state, ended.reason, ended.result)
            return task, Standing("working")
        standing = await self.standing(task)
        if standing.state == "working":
            return task, standing
        runtime, thread_id = self.runtime, task.thread_id
        # A run started meanwhile: the task works, and is looked at again later.
        if await runtime.locks.is_active(thread_id):
            return task, Standing("working")
        context = await runtime.begin_read(thread_id)
        try:
            again = _checkpoint(await state_of(context, thread_id))
        finally:
            await context.release()
        if again != standing.checkpoint:
            return task, Standing("working")
        if await store.end(
            task.task_id,
            standing.state,
            reason=standing.reason,
            # Cleaned when kept, with the keys of the model in use now, as well as when sent:
            # another profile may be active when it is read again.
            result=(
                self.clean(standing.result)
                if standing.state == "completed" and standing.result is not None
                else None
            ),
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
        ended = await store.get(task.task_id) or task
        return ended, Standing(ended.state, ended.reason, ended.result)

    def clean(self, text: str) -> str:
        """``text`` as a caller may be sent it: without any agent's address and without the
        keys of the models this instance uses (research R13). A text read from a thread can
        hold either: an agent's reply quoted by Freyja, or a model that repeats its key."""
        runtime = self.runtime
        secrets = (*runtime.applied_model().secrets, *runtime.environment_secrets)
        return redact(SCRUBBER.scrub(text), secrets)

    def _view(self, task: ReceivedTask, standing: Standing) -> View:
        if task.ended:
            result = self.clean(task.result or "") if task.state == "completed" else None
            sentence = (
                None
                if task.state == "completed"
                else REASON_SENTENCES.get(task.reason or "", REASON_SENTENCES["internal"])
            )
            return View(task, task.state, sentence, result)
        if standing.waiting == "person":
            sentence = waiting_for_person(standing.names)
        elif standing.waiting == "agent":
            sentence = waiting_for_agent(standing.names)
        else:
            sentence = WORKING
        return View(task, "working", sentence, None)

    def as_accepted(self, task: ReceivedTask) -> View:
        """``task`` as it is told when its message was just accepted: working, or as it
        ended when its run could not start."""
        return self._view(task, Standing("working"))

    async def view(self, task: ReceivedTask) -> View:
        """``task`` as its caller is told it now, after a look at its thread."""
        return self._view(*await self._looked(task))

    def _label(self, name: str) -> str:
        """A tool as a status names it: by the catalog's name, an agent's tool by its agent,
        and the subagent as a subagent; nothing of its arguments (FR-018)."""
        if name == SUBAGENT_TOOL:
            return SUBAGENT
        catalog = self.runtime.strict_catalog or self.runtime.catalog
        tool = catalog.get(name) if catalog is not None else None
        if tool is None:
            # A name the model wrote and the catalog does not have is not repeated.
            return UNKNOWN_TOOL
        if tool.agent is not None:
            return f"agent {tool.agent}"
        return name

    def _change(self, event: Any) -> Change | None:
        """What a run's event changes for a caller, by the table of research R13; names
        only, and the text of Freyja's own reply."""
        kind, data = event.event, _event_data(event)
        if kind == "update":
            messages = _messages_of(data.get("data"))
            if data.get("node") == "model":
                names = [
                    self._label(str(call.get("name")))
                    for message in messages
                    if message.get("type") == "ai"
                    for call in message.get("tool_calls") or []
                    if isinstance(call, dict)
                ]
                return Status(using(names)) if names else None
            if data.get("node") == "tools":
                names = [
                    self._label(str(message.get("name")))
                    for message in messages
                    if message.get("type") == "tool" and message.get("name")
                ]
                return Status(completed(names)) if names else None
            return None
        if kind == "message":
            if (data.get("metadata") or {}).get("reply") is not True:
                return None
            text = _text_of((data.get("message") or {}).get("content"))
            # Raw: the follower cleans the reply's text across pieces (``_pieces``).
            return Piece(text) if text else None
        if kind == "interrupt":
            requests = data.get("action_requests") or []
            names = [_action_name(request) for request in requests if isinstance(request, dict)]
            return Status(waiting_for_person(names)) if names else None
        return None

    def _join(self, thread_id: str) -> Reader | None:
        runs = self.runtime.unattended
        return runs.join(thread_id) if runs is not None else None

    async def follow(
        self, task: ReceivedTask, events: Reader | None = None
    ) -> AsyncGenerator[Change, None]:
        """What changes for ``task``, as it changes, until it has ended (research R13).

        While a console run of its thread is in progress on this instance, the run's events
        are read and turned into changes: ``events``, the first reader of the run a message
        started, or one more reader of a run in progress. Otherwise the follower waits to be
        woken by the end of a run on the thread here, or for ``FOLLOW_SECONDS``, and looks at
        the thread. The last change is ``Ended``. Close it to stop following; the run goes on.
        """
        wake = asyncio.Event()
        woken = self._wakers.setdefault(task.thread_id, set())
        woken.add(wake)
        sentence: str | None = WORKING
        try:
            while True:
                first, events = events, None
                handed = self._joined.get(wake)
                joined = handed.pop(0) if handed else None
                if handed is not None and not handed:
                    del self._joined[wake]
                reader = first or joined or self._join(task.thread_id)
                if reader is not None:
                    if first is None and sentence != WORKING:
                        # Another run on the thread, such as the one a person's decision
                        # started: what the task waited for is behind it.
                        sentence = WORKING
                        yield Status(WORKING)
                    held = ""
                    async with contextlib.aclosing(reader) as run:
                        async for event in run:
                            change = self._change(event)
                            if isinstance(change, Piece):
                                ready, held = _ready(held + change.text)
                                if ready:
                                    yield Piece(self.clean(ready))
                                continue
                            if isinstance(change, Status):
                                if change.sentence == sentence:
                                    continue
                                sentence = change.sentence
                            if change is not None:
                                yield change
                task, standing = await self._looked(await self.store.get(task.task_id) or task)
                view = self._view(task, standing)
                if task.ended:
                    yield Ended(view)
                    return
                if view.sentence is not None and view.sentence != sentence:
                    sentence = view.sentence
                    yield Status(sentence)
                # A run that started meanwhile is read at once; otherwise wait to be woken.
                if not self._joined.get(wake):
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(FOLLOW_SECONDS):
                            await wake.wait()
                wake.clear()
        finally:
            woken.discard(wake)
            if not woken and self._wakers.get(task.thread_id) is woken:
                del self._wakers[task.thread_id]
            for left in self._joined.pop(wake, []):
                self.leave(left)

    async def settle(self, accepted: Accepted, seconds: float) -> View:
        """The task of a send that asked for no stream, once it has ended or ``seconds``
        have passed, whichever is first (research R14). The run goes on either way."""
        if seconds <= 0:
            self.leave(accepted.events)
            return await self.view(accepted.task)
        with contextlib.suppress(TimeoutError):
            async with (
                asyncio.timeout(seconds),
                contextlib.aclosing(self.follow(accepted.task, accepted.events)) as changes,
            ):
                async for change in changes:
                    if isinstance(change, Ended):
                        return change.view
        found = await self.store.get(accepted.task.task_id) or accepted.task
        return await self.view(found)

    def leave(self, events: Reader) -> None:
        """Read a run's events nobody follows to their end, so the run never waits on them."""
        drain = asyncio.create_task(self._drain(events), name="freyja-a2a-drain")
        self._drains.add(drain)
        drain.add_done_callback(self._drains.discard)

    @staticmethod
    async def _drain(events: Reader) -> None:
        async with contextlib.aclosing(events) as run:
            async for _ in run:
                pass

    async def released(self, thread_id: str) -> None:
        """What follows the end of any run on a thread: its last task is looked at (R9)."""
        task = await self.store.last_task_of_thread(thread_id)
        if task is not None and not task.ended:
            await self.look(task)

    def on_run_started(self, thread_id: str) -> None:
        """Given to the console's runs: hand each follower of ``thread_id`` on this instance
        a reader of the run that starts there, before its first event, and wake it. This is
        how a caller's stream follows the continuation a person's decision starts (FR-017).
        """
        runs = self.runtime.unattended
        if runs is None:
            return
        for wake in list(self._wakers.get(thread_id, ())):
            reader = runs.join(thread_id)
            if reader is None:
                return
            # Kept in order: a follower still reading an earlier run reads this one next.
            self._joined.setdefault(wake, []).append(reader)
            wake.set()

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
        finally:
            # Its followers on this instance look now, not at their next interval.
            for wake in self._wakers.get(thread_id, ()):
                wake.set()

    async def close(self) -> None:
        """Stop reading the runs nobody follows, and wait for the looks under way; the
        service is stopping. A run whose reader is let go goes on, and is stopped at its
        next boundary by the console's runs, as every console run is."""
        for drain in list(self._drains):
            drain.cancel()
        waited = [*self._looks, *self._drains]
        if waited:
            await asyncio.gather(*waited, return_exceptions=True)
