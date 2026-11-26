"""Starting a run, a resume, and a console run, and asking a stop (feature 009, research R10).

The bodies of the two handlers of ``api/threads.py`` that run a thread live here, moved out
of the route module without a change of behavior, so that the thread routes, the console's
routes of ``api/thread_index.py``, and the A2A service (``api/received.py``) all start runs
through one piece of code and no module imports a route module. Each function takes the
runtime and the request's values, raises the ``HTTPException`` the route answers with, and
returns the run's events; the routes put them in their response.

Feature 009 adds two rules here, and nothing else:

- With the feature off, a thread a caller started while it was on is not run: a message and
  every decision are refused with 409 ``a2a_server_off`` before the lease is taken. Its state
  is read as before (FR-001, research R6).
- Under the lease, a caller's thread with a delegate agent's task still awaited takes no
  message from its caller (research R8). Only ``ReceivedTasks`` asks for that rule, so the
  routes answer with no code the contracts do not define.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from sse_starlette import ServerSentEvent

from freyja_agent.api.decisions import build_resume_command, decided_calls, decisions_taken
from freyja_agent.api.outcomes import (
    answer_in_flight,
    mark_delivered,
    record_answers,
    take_undelivered,
)
from freyja_agent.api.schemas import ResumeRequest
from freyja_agent.api.serialize import interrupt_to_pending
from freyja_agent.api.streaming import lease_owner, no_context_stream, run_stream, take_context
from freyja_agent.runtime.agent_tasks import AgentTaskRecord
from freyja_agent.runtime.runtime import RunContext, Runtime
from freyja_agent.runtime.thread_index import INDEX_WRITE_SECONDS, ThreadRecord, open_thread
from freyja_agent.runtime.unattended import UnattendedRuns

logger = logging.getLogger(__name__)

Events = AsyncIterator[ServerSentEvent]
# A reader of a console run, as ``UnattendedRuns`` hands one out: its events, and a way to
# leave it (``aclose``), which a reader that stops early uses.
Reader = AsyncGenerator[ServerSentEvent, None]
# Called under the lease, once a message's run is accepted and before it starts, with the
# thread's state as the run found it (feature 009: a received task's start, research R7).
OnStarted = Callable[[Any], Awaitable[None]]

A2A_SERVER_OFF = "a2a_server_off"
# A message to a caller's thread through the service's own routes (contracts/openapi.yaml).
CALLER_THREAD = "caller_thread"
# The code a caller's message is refused with when its thread is not at rest under the lease;
# ``ReceivedTasks`` turns it into the protocol's "not now".
AGENT_AWAITED = "agent_awaited"


def error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def state_of(context: RunContext, thread_id: str) -> Any:
    return await context.agent.aget_state({"configurable": {"thread_id": thread_id}})


def awaited(task: AgentTaskRecord) -> bool:
    """Whether a received task still waits for this agent task (research R7).

    It does while the task works, while its outcome is not in the thread, and while a run to
    report it is to be tried again. One that waits for input and whose question was reported
    is not awaited: Freyja's reply carries the question, and the caller answers.
    """
    return (
        task.state == "working" or not task.outcome_delivered or task.followup_next_at is not None
    )


async def refuse_while_off(runtime: Runtime, thread_id: str) -> None:
    """Refuse to run a thread a caller started, while receiving tasks from agents is off."""
    if runtime.settings.a2a_server:
        return
    try:
        conversation = await runtime.caller_of(thread_id)
    except Exception as exc:
        logger.warning(
            "Thread %s: whether another agent started it could not be read (%s); nothing ran",
            thread_id,
            type(exc).__name__,
        )
        raise error(500, "internal", "This thread could not be read; nothing ran.") from None
    if conversation is not None:
        raise error(
            409,
            A2A_SERVER_OFF,
            "This task was started by another agent, and is continued only while receiving "
            "tasks from agents is on. Its state can still be read.",
        )


async def refuse_caller_thread(runtime: Runtime, thread_id: str) -> None:
    """Refuse a message to a thread a caller started, through the service's own routes.

    Only its caller adds messages to its conversation (FR-024, research R20); a person still
    decides its proposals and stops its run. Asked before the lease, so the answer is the same
    while a run holds it. With the feature off the run's own refusal, ``a2a_server_off``,
    answers instead, and nothing more is looked up here. Not asked by the function the A2A
    service starts a caller's run with.
    """
    if not runtime.settings.a2a_server:
        return
    try:
        conversation = await runtime.caller_of(thread_id)
    except Exception as exc:
        logger.warning(
            "Thread %s: whether another agent started it could not be read (%s); nothing ran",
            thread_id,
            type(exc).__name__,
        )
        raise error(500, "internal", "This thread could not be read; nothing ran.") from None
    if conversation is not None:
        raise error(
            409,
            CALLER_THREAD,
            "This task was started by another agent, and only that agent sends messages on "
            "it. Its proposals are decided and its run is stopped here as for any task.",
        )


async def _paused_calls(runtime: Runtime, thread_id: str, namespace: str) -> list[str]:
    """The ids of the calls of the reply a gate paused on: the rows of its namespace, which
    each reply replaces, so a later reply of the same resume is not that reply (005 FR-022).

    When they cannot be read the resume is refused with 500 and nothing runs: the proposal
    stays pending, to be decided again, rather than run with no decision recorded.
    """
    try:
        calls = await runtime.agent_tasks.list_calls(thread_id)
    except Exception as exc:
        logger.warning(
            "The proposed calls of thread %s could not be read for its resume (%s); the "
            "resume was refused",
            thread_id,
            type(exc).__name__,
        )
        raise error(
            500, "internal", "The proposed calls could not be read; nothing was resumed."
        ) from None
    return [call.tool_call_id for call in calls if call.namespace == namespace]


async def _not_decided_otherwise(
    context: RunContext,
    thread_id: str,
    decided: dict[str, Any] | None,
    interrupt_id: str,
    command: Any,
) -> None:
    """Refuse decisions other than those the proposal's gate already took.

    A resume whose run failed after the gate's step completed, inside a subagent, leaves the
    proposal shown although it was decided: the next resume continues past the gate with the
    earlier decisions, whatever it sends (``decisions_taken``). The same decisions continue
    the thread; any others are refused, and nothing runs.
    """
    if decided is None:
        return
    earlier = await decisions_taken(
        context.agent.checkpointer, thread_id, decided["namespace"], interrupt_id
    )
    if earlier is None or earlier == [command.resume[interrupt_id]]:
        return
    taken = [
        str(decision.get("type"))
        for value in earlier
        if isinstance(value, dict)
        for decision in value.get("decisions") or []
        if isinstance(decision, dict)
    ]
    raise error(
        409,
        "already_decided",
        "This proposal was already decided by an earlier resume, whose run stopped after the "
        f"decision was taken ({', '.join(taken) or 'no decision'}). That decision stands: "
        "send the same decisions to continue the thread.",
    )


async def start_message(
    runtime: Runtime,
    thread_id: str,
    message: str,
    *,
    debug: bool = False,
    refuse_awaited: bool = False,
    on_started: OnStarted | None = None,
) -> Events:
    """Start a run with ``message`` and return its events: the body of
    ``POST /threads/{thread_id}/runs``.

    The lease is taken first so two racing requests resolve to exactly one stream; state is
    read under the lease, and a thread paused on a proposal is refused rather than having its
    proposal silently discarded (research R9, constitution principle I).

    Outcomes of agent tasks that arrived and were not delivered go into the input ahead of the
    user's message, and count as delivered once the input is in a checkpoint (feature 003,
    FR-018, research R10). Ahead of them go the results of messages to agents that a stop left
    in flight (FR-017). A resume records neither: it finishes a step already under way.

    The run's context is taken right after the lease, and the thread's state is read on it,
    so the run sees the thread through the orchestrator it then runs on (005 research R3).
    When none can be taken, the stream says so and nothing runs.

    ``refuse_awaited`` and ``on_started`` are the A2A service's (feature 009): the first
    refuses a thread with an agent task awaited, the second is told, under the lease, that
    the run is accepted.
    """
    await refuse_while_off(runtime, thread_id)
    run_id = uuid.uuid4().hex
    owner = lease_owner(runtime, run_id)
    if not await runtime.locks.acquire(thread_id, owner):
        raise error(409, "run_active", "A run is already active on this thread.")
    context = await take_context(runtime, thread_id, run_id)
    if context is None:
        return no_context_stream(runtime, thread_id, mode="run", debug=debug, run_id=run_id)
    try:
        snapshot = await state_of(context, thread_id)
        if snapshot.interrupts:
            raise error(
                409,
                "decision_pending",
                "This thread is paused on a proposal; decide it through resume first.",
            )
        if refuse_awaited and any(
            awaited(task) for task in await runtime.agent_tasks.list_tasks(thread_id)
        ):
            raise error(
                409, AGENT_AWAITED, "Work this thread handed to an agent is still going on."
            )
        answers, cut_off = await answer_in_flight(runtime.agent_tasks, thread_id, snapshot)
        outcomes, delivering = await take_undelivered(runtime.agent_tasks, thread_id, snapshot)
        if on_started is not None:
            await on_started(snapshot)
    except BaseException:
        await context.release()
        await runtime.locks.release(thread_id, owner)
        raise

    async def delivered() -> None:
        await record_answers(runtime.agent_tasks, cut_off)
        await mark_delivered(runtime.agent_tasks, delivering)

    return run_stream(
        runtime,
        thread_id,
        context=context,
        input={"messages": [*answers, *outcomes, HumanMessage(message)]},
        mode="run",
        debug=debug,
        run_id=run_id,
        on_recorded=delivered if delivering or cut_off else None,
    )


async def start_resume(
    runtime: Runtime, thread_id: str, body: ResumeRequest, *, debug: bool = False
) -> Events:
    """Submit one decision per pending action request and return the continuation's events:
    the body of ``POST /threads/{thread_id}/resume``.

    Same lease-first order as ``start_message``. The request names the proposal it decides; a
    request naming any other id is refused before its decisions are looked at, so a decision
    sent twice cannot approve a later proposal. Decisions are then validated against the
    pending proposal before anything resumes; every refusal leaves the proposal untouched
    (FR-013).

    One context serves the whole request: the pending proposal is worded, the decisions are
    validated, and the continuation runs on the same orchestrator. Validating against one
    catalog and running on another would let a decision meant for one call be applied to
    another when an approval choice changed in between (005 research R3).
    """
    await refuse_while_off(runtime, thread_id)
    run_id = uuid.uuid4().hex
    owner = lease_owner(runtime, run_id)
    if not await runtime.locks.acquire(thread_id, owner):
        raise error(409, "run_active", "A run is already active on this thread.")
    context = await take_context(runtime, thread_id, run_id)
    if context is None:
        return no_context_stream(runtime, thread_id, mode="resume", debug=debug, run_id=run_id)
    try:
        snapshot = await state_of(context, thread_id)
        if snapshot.metadata is None:
            raise error(404, "thread_not_found", "This thread has never been used.")
        if not snapshot.interrupts:
            raise error(409, "nothing_pending", "This thread has no pending decision.")
        if len(snapshot.interrupts) > 1:
            raise error(500, "internal", "The thread is paused on more than one interrupt at once.")
        pending = interrupt_to_pending(snapshot.interrupts[0], context.catalog)
        if body.interrupt_id != pending["interrupt_id"]:
            raise error(
                409,
                "proposal_mismatch",
                "The interrupt_id is not the pending proposal's; read the thread state and "
                "decide the proposal it shows.",
            )
        command = build_resume_command(pending, body.decisions, context.catalog)
        decided = decided_calls(snapshot.tasks, snapshot.interrupts[0], body.decisions)
        await _not_decided_otherwise(context, thread_id, decided, pending["interrupt_id"], command)
        if decided is not None:
            decided["calls"] = await _paused_calls(runtime, thread_id, decided["namespace"])
            decided["seen"] = []
    except BaseException:
        await context.release()
        await runtime.locks.release(thread_id, owner)
        raise
    return run_stream(
        runtime,
        thread_id,
        context=context,
        input=command,
        mode="resume",
        debug=debug,
        run_id=run_id,
        decided=decided,
    )


def console_runs(runtime: Runtime) -> UnattendedRuns:
    """The console runs of this instance; asked before a lease is taken."""
    if runtime.unattended is None:  # a runtime put together by hand, never the service's
        raise error(500, "internal", "This instance cannot run a task without a reader.")
    return runtime.unattended


async def open_first(
    runtime: Runtime, thread_id: str, *, seconds: float = INDEX_WRITE_SECONDS
) -> bool:
    """Give a thread that has a state and no row its row before a console run starts on it.

    True once the row is known to be in order: there was one, or one was made, or the
    thread is new. False when the lookup failed or was given up: the run's text may then be
    a follow-up on a thread that has begun, and is not to be kept as its first message.

    A console run tells the index the text it was started with, which a row that has no
    first message takes for how the thread began. For a thread from before the index, or
    one whose row could not be written, that text is a follow-up: so such a thread is opened
    first, as reading it would, and its row has the first message its state holds (007
    FR-002, FR-004). A thread that is new has no state, and gets no row here.

    Keeping the list never fails or holds up a run for long (007 FR-023): whatever goes wrong
    here is logged, and the run starts.
    """
    try:
        async with asyncio.timeout(seconds):
            if await runtime.threads.get(thread_id) is not None:
                return True
            context = await runtime.begin_read(thread_id)
            try:
                await open_thread(
                    runtime.threads,
                    context,
                    runtime.agent_tasks,
                    thread_id,
                    lease_active=await runtime.locks.is_active(thread_id),
                )
            finally:
                await context.release()
    except Exception:
        logger.warning(
            "Thread %s: its row could not be looked up before its run; the run starts",
            thread_id,
            exc_info=True,
        )
        return False
    return True


def go_on(
    runs: UnattendedRuns,
    thread_id: str,
    events: AsyncIterable[ServerSentEvent],
    first_message: str | None,
) -> Reader:
    """Drive the run a handler accepted without its reader, and return its first reader.

    The handler took the thread's lease and made the run's events; nothing of the run has
    started. It is handed over at once, with nothing awaited before, so that nothing can
    leave the lease taken. The row is marked as a console run's at the run's first event.
    """
    reader = runs.start(thread_id, events, first_message)
    logger.info("Thread %s: a run started that goes on without a reader", thread_id)
    return reader


async def start_console_run(
    runtime: Runtime,
    thread_id: str,
    message: str,
    *,
    debug: bool = False,
    refuse_awaited: bool = False,
    on_started: OnStarted | None = None,
) -> Reader:
    """Start a console run with ``message``: the body of
    ``POST /threads/{thread_id}/runs/unattended``, with the same refusals and events as
    ``start_message``, from the same code (007 research R2, R3)."""
    runs = console_runs(runtime)
    known = await open_first(runtime, thread_id)
    events = await start_message(
        runtime,
        thread_id,
        message,
        debug=debug,
        refuse_awaited=refuse_awaited,
        on_started=on_started,
    )
    # Without the lookup, the row takes its first message from the thread's state once the
    # run is settled, which is the run's text when the thread is new (007 FR-002).
    return go_on(runs, thread_id, events, message if known else None)


def _unreadable(thread_id: str, exc: Exception) -> HTTPException:
    """The list never answers with an earlier one (007 FR-023)."""
    logger.error(
        "The list of threads could not be read (thread %s): %s", thread_id, type(exc).__name__
    )
    return error(500, "internal", "The list of threads could not be read.")


async def ask_stop(runtime: Runtime, thread_id: str) -> ThreadRecord:
    """Ask the console run in progress to stop after the step under way: the body of
    ``POST /threads/{thread_id}/stop``, which answers with the row the stop marked (007
    research R5). The mark is read by the instance that runs the thread, between two steps,
    so this works from any instance."""
    try:
        record = await runtime.threads.get(thread_id)
        running = await runtime.locks.is_active(thread_id)
    except Exception as exc:
        raise _unreadable(thread_id, exc) from exc
    if record is None:
        raise error(404, "thread_not_found", "No run on this thread is known.")
    if not running or record.run_owner is None:
        raise error(409, "no_run", "No run is in progress on this thread.")
    if not record.run_unattended:
        raise error(
            409,
            "not_stoppable",
            "This run was not started by the console, so it cannot be stopped from here: a "
            "client that reads its own stream stops it by closing the stream, and a run the "
            "service started ends by itself.",
        )
    try:
        # For the run that was read: one that has just ended is not stopped in its place.
        marked = await runtime.threads.request_stop(thread_id, record.run_owner)
    except Exception as exc:
        raise _unreadable(thread_id, exc) from exc
    if marked is None:
        raise error(409, "no_run", "No run is in progress on this thread.")
    logger.info("Thread %s: a stop was asked for the run in progress", thread_id)
    return marked
