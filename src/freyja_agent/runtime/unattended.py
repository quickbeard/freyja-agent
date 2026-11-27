"""Runs that go on without a reader, and the stop (feature 007, research R2, R4, R5, R17).

A run is the ``run_stream`` generator of ``api/streaming.py``. The routes that exist since
feature 001 hand it to their response, so it ends when the client stops reading. A console
run is driven here instead, in a task of its own, and each event is handed to whoever is
reading: a reader that leaves changes nothing about how the run ends.

Such a run can be stopped, after the step under way and never in the middle of it. Its graph
is run in segments, each one call of the real graph's ``astream`` that ends before the next
model call or the next tool step, by LangGraph's own pause points. Between two segments, and
nowhere else, the run asks whether it is to stop: a person asked for it, or the service is
stopping. An action under way therefore always completes and is recorded, and an action
that had not started is not started (spec FR-022).

- ``SegmentedGraph`` stands for the compiled graph in every ``RunContext``. A thread that is
  not armed, which is every run but a console run, is passed straight to the real graph.
- ``UnattendedRuns`` drives the console runs of this instance, arms their threads, hands
  their events to readers, notes how each ended in the index, and applies a stop.

Feature 009 makes ``SegmentedGraph`` the place a thread a caller started over A2A is held to
the strict build: every run of every kind goes through its ``astream``, which asks first who
started the thread. A caller's thread is refused on the ordinary build, and so is any thread
whose lookup fails; on the strict build the run's config says whose files it works in
(research R6, R16), and the hand-over count of the last message from its caller whose run
started, which the agent tools pass on (FR-027, research R17). Every other way to run a
graph raises on the wrapper, so that none becomes a way around. ``UnattendedRuns.join`` gives one more reader of a console run in
progress (research R10). A run can be started to stop at its first boundary (``stop_first``),
which is how a proposal is rejected by rule, and a console run on a caller's thread also
stops at its next boundary once that caller canceled its task (``canceled``, research R10,
R11).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import anyio
from langgraph.errors import GraphRecursionError
from sse_starlette import ServerSentEvent

from freyja_agent.agent.caller_files import FILES_KEY
from freyja_agent.agent.context import HOPS_KEY
from freyja_agent.delegates.addresses import SCRUBBER
from freyja_agent.runtime.received_tasks import LOCAL
from freyja_agent.runtime.thread_index import INDEX_WRITE_SECONDS

if TYPE_CHECKING:
    from freyja_agent.runtime.received_tasks import Conversation
    from freyja_agent.runtime.thread_index import ThreadIndex

    # Who started a thread: its conversation, or None for a thread no caller started.
    Lookup = Callable[[str], Awaitable[Conversation | None]]
    # The hand-over count of a caller's thread's last message from its caller.
    Count = Callable[[str], Awaitable[int]]

logger = logging.getLogger(__name__)

# The two nodes a segment ends before: a model call and a tool step (research R4). They are
# the library's names; a graph without either is refused, so that an upgrade that renames
# one cannot leave console runs that never reach a boundary.
BOUNDARY_NODES = ("model", "tools")
# How many events wait for a reader before it is disconnected (spec, Assumptions, limits). A
# stalled connection must not make the service hold a run's whole output in memory.
READER_BACKLOG = 1000
# The events that end a run's stream (001 contracts/sse-events.md).
TERMINAL = frozenset({"end", "interrupt", "error"})


class MissingNodeError(RuntimeError):
    """The graph has no node a console run needs to stop at."""


class CallerThreadError(RuntimeError):
    """A thread a caller started would have run on the ordinary build, or whether a caller
    started it could not be read: the run is refused and nothing of it runs."""


class OtherEntryError(RuntimeError):
    """A graph of a run context was run another way than ``astream``."""


class Arming(Protocol):
    """What ``SegmentedGraph`` asks of the console runs of this instance."""

    def armed(self, thread_id: str) -> bool: ...

    async def should_stop(self, thread_id: str) -> bool: ...


def _thread_of(config: Any) -> str | None:
    try:
        return config["configurable"]["thread_id"]
    except (KeyError, TypeError):
        return None


class SegmentedGraph:
    """The compiled graph, with a console run's thread run in segments.

    Everything but ``astream`` is the real graph's. ``astream`` passes a thread that is not
    armed straight through, with the arguments it was given.
    """

    def __init__(
        self,
        graph: Any,
        runs: Arming,
        *,
        strict: bool = False,
        lookup: Lookup | None = None,
        count: Count | None = None,
    ) -> None:
        nodes = getattr(graph, "nodes", {})
        for node in BOUNDARY_NODES:
            if node not in nodes:
                raise MissingNodeError(
                    f"The orchestrator's graph has no node {node!r}; a run that goes on "
                    "without a reader could not be stopped between two steps."
                )
        self.graph = graph
        self.runs = runs
        # Feature 009: whether this wraps the strict build, and how to learn who started a
        # thread. A runtime put together by hand has no lookup, and no caller's thread.
        self.strict = strict
        self.lookup = lookup
        # How to learn the hand-over count a caller's run passes on; none means 0.
        self.count = count

    def __getattr__(self, name: str) -> Any:
        return getattr(self.graph, name)

    async def astream(self, input: Any, config: Any = None, **kwargs: Any) -> AsyncIterator[Any]:
        thread_id = _thread_of(config)
        if thread_id is not None and self.lookup is not None:
            config = await self._held_to_its_build(thread_id, config)
        if thread_id is None or not self.runs.armed(thread_id):
            async for chunk in self.graph.astream(input, config, **kwargs):
                yield chunk
            return
        async for chunk in self._segments(thread_id, input, config, kwargs):
            yield chunk

    async def _held_to_its_build(self, thread_id: str, config: Any) -> Any:
        """``config`` for a run of ``thread_id`` on this build; raises where it may not run.

        Asked before every run, whether a console run or not (research R6).
        """
        if self.lookup is None:  # pragma: no cover - the caller asked first
            return config
        try:
            conversation = await self.lookup(thread_id)
        except Exception as exc:
            raise CallerThreadError(
                f"Thread {thread_id}: whether another agent started it could not be read "
                f"({type(exc).__name__}); the run was refused"
            ) from None
        if conversation is None:
            return config
        if not self.strict:
            raise CallerThreadError(
                f"Thread {thread_id} was started by another agent and runs only on the "
                "orchestrator kept for such threads; the run was refused"
            )
        hops = 0
        if self.count is not None:
            try:
                hops = await self.count(thread_id)
            except Exception as exc:
                raise CallerThreadError(
                    f"Thread {thread_id}: the hand-over count of its caller's last message "
                    f"could not be read ({type(exc).__name__}); the run was refused"
                ) from None
        caller = conversation.caller
        configurable = {
            **config["configurable"],
            FILES_KEY: "" if caller == LOCAL else caller,
            HOPS_KEY: hops,
        }
        return {**config, "configurable": configurable}

    def _other_entry(self, *_: Any, **__: Any) -> Any:
        raise OtherEntryError(
            "A run context's graph is run through astream only, where who started the thread "
            "is asked; this entry is not offered."
        )

    ainvoke = invoke = astream_events = stream = _other_entry
    abatch = batch = abatch_as_completed = batch_as_completed = _other_entry
    astream_log = transform = atransform = _other_entry

    async def _step(self, config: Any) -> int:
        """The step of the thread's last checkpoint; -1 for a thread with none."""
        snapshot = await self.graph.aget_state(config)
        if snapshot.metadata is None:
            return -1
        return int(snapshot.metadata.get("step", -1))

    async def _segments(
        self, thread_id: str, input: Any, config: Any, kwargs: dict[str, Any]
    ) -> AsyncIterator[Any]:
        # LangGraph counts the step limit per call of ``astream``, so each segment is given
        # what is left of the run's limit, read from the thread's last checkpoint.
        limit = int(config.get("recursion_limit", 25))
        first = await self._step(config)
        state_config = {"configurable": config["configurable"]}
        while True:
            left = limit - (await self._step(state_config) - first)
            if left < 1:
                raise GraphRecursionError(
                    f"Recursion limit of {limit} reached without hitting a stop condition."
                )
            segment = {**config, "recursion_limit": left}
            async for chunk in self.graph.astream(
                input, segment, interrupt_before=list(BOUNDARY_NODES), **kwargs
            ):
                if _boundary(chunk):
                    continue
                yield chunk
            input = None
            snapshot = await self.graph.aget_state(state_config)
            # Paused on a proposal, or finished: the run ends as a plain one would.
            if snapshot.interrupts or not snapshot.next:
                return
            # A boundary between two steps, the one place a stop is applied.
            if await self.runs.should_stop(thread_id):
                return


def _boundary(chunk: Any) -> bool:
    """The root ``updates`` chunk a pause before a node leaves, holding no interrupt.

    With ``subgraphs=True`` and several stream modes a chunk is ``(namespace, mode, data)``.
    A pause raised by the gate holds its interrupts and is passed on.
    """
    if not (isinstance(chunk, tuple) and len(chunk) == 3):
        return isinstance(chunk, dict) and chunk.get("__interrupt__") == ()
    namespace, mode, data = chunk
    return (
        not namespace
        and mode == "updates"
        and isinstance(data, dict)
        and "__interrupt__" in data
        and not data["__interrupt__"]
    )


_CLOSED = object()


@dataclass(eq=False)
class _Reader:
    queue: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    dropped: bool = False


@dataclass(eq=False)
class _Run:
    thread_id: str
    first_message: str | None = None
    owner: str | None = None
    readers: list[_Reader] = field(default_factory=list)
    # Whether ``should_stop`` said yes for this run: the run then ended because of a stop.
    stopped: bool = False
    # Whether the run stops at its first boundary, whatever else is asked (feature 009).
    stop_first: bool = False
    task: asyncio.Task[None] | None = None


class UnattendedRuns:
    """The console runs of this instance: each driven to its end, whoever reads it."""

    def __init__(self, index: ThreadIndex, instance_id: str) -> None:
        self.index = index
        self.instance_id = instance_id
        self._runs: dict[str, _Run] = {}
        # Every drive in progress, also one whose run has ended and gives its lease back.
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        # Told of each run as it starts, before its first event (feature 009): a follower of
        # a caller's task joins the run a person's decision started.
        self.on_start: Callable[[str], None] | None = None
        # Asked at each boundary (feature 009, research R10): whether the last task of a
        # caller's thread was canceled by its caller; False for any other thread.
        self.canceled: Callable[[str], Awaitable[bool]] | None = None

    def armed(self, thread_id: str) -> bool:
        return thread_id in self._runs

    async def should_stop(self, thread_id: str) -> bool:
        """Whether the run on ``thread_id`` is to end at this boundary; remembered when yes.

        A stop counts when a person asked for it for this very run, on any instance, or when
        this instance is stopping. An index that cannot be read is no stop asked.

        Feature 009 adds two: a run started to stop at its first boundary, and a run on a
        caller's thread whose last task that caller canceled, whether or not the index was
        marked, since a cancel can arrive before the run's first event marks it (R10). A
        cancel that cannot be read is none asked.
        """
        run = self._runs.get(thread_id)
        if run is None:
            return False
        asked = self._closing or run.stop_first
        if not asked and self.canceled is not None:
            try:
                async with asyncio.timeout(INDEX_WRITE_SECONDS):
                    asked = await self.canceled(thread_id)
            except Exception as exc:
                logger.warning(
                    "Thread %s: whether its caller canceled could not be read (%s); the run "
                    "goes on",
                    thread_id,
                    type(exc).__name__,
                )
                asked = False
        if not asked and run.owner is not None:
            try:
                async with asyncio.timeout(INDEX_WRITE_SECONDS):
                    asked = await self.index.stop_requested(thread_id, run.owner)
            except Exception as exc:
                logger.warning(
                    "Thread %s: whether a stop was asked could not be read (%s); the run goes on",
                    thread_id,
                    type(exc).__name__,
                )
                asked = False
        if asked:
            run.stopped = True
        return asked

    def start(
        self,
        thread_id: str,
        events: AsyncIterable[ServerSentEvent],
        first_message: str | None = None,
        *,
        stop_first: bool = False,
    ) -> AsyncGenerator[ServerSentEvent, None]:
        """Arm the thread, drive ``events`` in a task, and give the first reader its events.

        The caller holds the thread's lease, taken by the handler that made ``events``, so
        no other run of this instance is armed for the thread. The row is marked as a console
        run's at the run's first event, which names the run's owner; ``first_message`` is
        the message the run was started with, None for a resume. ``stop_first`` makes the
        run stop at its first boundary (feature 009, research R11).
        """
        run = _Run(thread_id=thread_id, first_message=first_message, stop_first=stop_first)
        reader = _Reader()
        run.readers.append(reader)
        self._runs[thread_id] = run
        if self.on_start is not None:
            try:
                self.on_start(thread_id)
            except Exception:
                logger.exception("Thread %s: telling of the run's start failed", thread_id)
        run.task = asyncio.create_task(self._drive(run, events), name=f"freyja-run-{thread_id}")
        self._tasks.add(run.task)
        run.task.add_done_callback(self._tasks.discard)
        return self._read(run, reader)

    def join(self, thread_id: str) -> AsyncGenerator[ServerSentEvent, None] | None:
        """One more reader of the console run in progress on ``thread_id`` on this instance,
        from the events that follow; None when no such run is in progress here (feature 009,
        research R10). A reader that falls behind is disconnected as any other is."""
        run = self._runs.get(thread_id)
        if run is None:
            return None
        reader = _Reader()
        run.readers.append(reader)
        return self._read(run, reader)

    async def _read(self, run: _Run, reader: _Reader) -> AsyncGenerator[ServerSentEvent, None]:
        try:
            while True:
                event = await reader.queue.get()
                if event is _CLOSED:
                    return
                yield event
        finally:
            if reader in run.readers:
                run.readers.remove(reader)

    def _publish(self, run: _Run, event: Any) -> None:
        for reader in list(run.readers):
            if event is not _CLOSED and reader.queue.qsize() >= READER_BACKLOG:
                # A reader that does not keep up is disconnected; its page reads the thread.
                logger.warning(
                    "Thread %s: a reader fell %d events behind and was disconnected; the "
                    "run goes on",
                    run.thread_id,
                    READER_BACKLOG,
                )
                run.readers.remove(reader)
                reader.dropped = True
                while not reader.queue.empty():
                    reader.queue.get_nowait()
                reader.queue.put_nowait(_CLOSED)
                continue
            reader.queue.put_nowait(event)

    async def _drive(self, run: _Run, events: AsyncIterable[ServerSentEvent]) -> None:
        try:
            async for given in events:
                event = _scrubbed(given)
                kind = event.event
                if kind == "start" and run.owner is None:
                    run.owner = f"{self.instance_id}:{_data(event).get('run_id')}"
                    await self._mark(run, run.owner)
                if kind in TERMINAL:
                    # Disarmed before the generator goes on to give its lease back, so a
                    # run that takes the lease next is never taken for this one.
                    self._runs.pop(run.thread_id, None)
                    event = await self._ended(run, event)
                self._publish(run, event)
        except Exception:
            logger.exception(
                "Thread %s: the run that goes on without a reader failed", run.thread_id
            )
        finally:
            # However the drive ends, the run's own ``finally`` runs: it gives the context
            # and the lease back, also when this task is cancelled.
            with anyio.CancelScope(shield=True):
                closing = getattr(events, "aclose", None)
                if closing is not None:
                    await closing()
            if self._runs.get(run.thread_id) is run:
                del self._runs[run.thread_id]
            self._publish(run, _CLOSED)

    async def _mark(self, run: _Run, owner: str) -> None:
        """Mark the row as this console run's, which is what makes it stoppable.

        Written for the run's owner only, so a late mark never lands on a later run. A
        failure is logged, and the run goes on as one that cannot be stopped.
        """
        try:
            async with asyncio.timeout(INDEX_WRITE_SECONDS):
                await self.index.mark_unattended(run.thread_id, owner, run.first_message)
        except Exception as exc:
            logger.warning(
                "Thread %s: the run could not be marked as one that goes on without a reader "
                "(%s); it goes on, and cannot be stopped",
                run.thread_id,
                type(exc).__name__,
            )

    async def _ended(self, run: _Run, event: ServerSentEvent) -> ServerSentEvent:
        """Note how the run ended, and say ``stopped`` for a run a stop ended."""
        kind = event.event
        if kind == "end" and run.stopped:
            logger.info("Thread %s: the run ended because a stop was asked", run.thread_id)
            await self._note(run, "stopped")
            return ServerSentEvent(data=json.dumps({"status": "stopped"}), event="end")
        if kind == "error":
            # Cleaned of agents' addresses already (`_scrubbed`), so the reason kept for
            # the list holds none either.
            data = _data(event)
            await self._note(run, "failed", data.get("code"), data.get("message"))
        return event

    async def _note(
        self, run: _Run, noted: Any, code: str | None = None, message: str | None = None
    ) -> None:
        if run.owner is None:
            return
        try:
            async with asyncio.timeout(INDEX_WRITE_SECONDS):
                await self.index.note(run.thread_id, run.owner, noted, code, message)
        except Exception as exc:
            logger.warning(
                "Thread %s: how its run ended could not be kept for the list (%s); the run "
                "is not affected",
                run.thread_id,
                type(exc).__name__,
            )

    async def close(self) -> None:
        """Ask every run to stop at its next boundary, and wait for them (research R17).

        The step under way of each completes and is recorded; nothing is cancelled.
        """
        self._closing = True
        tasks = list(self._tasks)
        if tasks:
            logger.info("Waiting for %d run(s) without a reader to reach a step's end", len(tasks))
            await asyncio.gather(*tasks, return_exceptions=True)


def _scrubbed(event: ServerSentEvent) -> ServerSentEvent:
    """The event with every agent's address taken out of its data (FR-025).

    What a console run sends its reader can hold text a model's endpoint wrote: a failed
    step's error, in the ``error`` event and in the debug stream's events. An agent's
    address in it is taken out here, before the event is told to the index or handed to the
    reader. Each text of the data is cleaned as it reads, its keys included: in the JSON
    text a line break or a letter of another script is an escape sequence written against
    the address, which the scrubber would not take for one. Data that is no JSON is cleaned
    as it stands. What the checkpointer keeps of a failed step is not cleaned here, and
    neither are the earlier routes' streams: that is the model's guard's (``llm/guard.py``),
    as it is for a profile's key.
    """
    data = event.data
    if not isinstance(data, str):
        return event
    try:
        value = json.loads(data)
    except ValueError:
        cleaned = SCRUBBER.scrub(data)
    else:
        scrubbed = _scrub_texts(value)
        cleaned = data if scrubbed == value else json.dumps(scrubbed)
    if cleaned == data:
        return event
    return ServerSentEvent(
        data=cleaned, event=event.event, id=event.id, retry=event.retry, comment=event.comment
    )


def _scrub_texts(value: Any) -> Any:
    """``value`` with every text in it, keys included, cleaned of agents' addresses."""
    if isinstance(value, str):
        return SCRUBBER.scrub(value)
    if isinstance(value, list):
        return [_scrub_texts(item) for item in value]
    if isinstance(value, dict):
        return {SCRUBBER.scrub(key): _scrub_texts(item) for key, item in value.items()}
    return value


def _data(event: ServerSentEvent) -> dict[str, Any]:
    try:
        data = json.loads(event.data) if isinstance(event.data, str) else event.data
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
