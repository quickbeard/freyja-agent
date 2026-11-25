"""Thread endpoints: start a run, read state, resume a paused thread (contracts/openapi.yaml)."""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from langchain_core.messages import HumanMessage
from sse_starlette import EventSourceResponse

from freyja_agent.api.decisions import build_resume_command, decided_calls, decisions_taken
from freyja_agent.api.guards import refuse_other_origins
from freyja_agent.api.outcomes import (
    answer_in_flight,
    mark_delivered,
    record_answers,
    take_undelivered,
)
from freyja_agent.api.schemas import (
    THREAD_ID_MAX_LENGTH,
    THREAD_ID_PATTERN,
    AgentTask,
    AgentTaskProgress,
    ResumeRequest,
    RunRequest,
    ThreadState,
)
from freyja_agent.api.serialize import interrupt_to_pending, message_to_dict
from freyja_agent.api.streaming import lease_owner, no_context_stream, run_stream, take_context
from freyja_agent.runtime.runtime import RunContext, Runtime

logger = logging.getLogger(__name__)

# Feature 007: a request that changes something and that a browser marks as made by a page of
# another origin is refused, as the LLM and Agents routes refuse it (007 research R9).
router = APIRouter(
    prefix="/threads",
    tags=["threads"],
    dependencies=[Depends(refuse_other_origins("Threads"))],
)

ThreadIdPath = Annotated[
    str, Path(min_length=1, max_length=THREAD_ID_MAX_LENGTH, pattern=THREAD_ID_PATTERN)
]
DebugQuery = Annotated[bool, Query(description="Add debug events; never changes the run.")]


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


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
        raise _error(
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
    raise _error(
        409,
        "already_decided",
        "This proposal was already decided by an earlier resume, whose run stopped after the "
        f"decision was taken ({', '.join(taken) or 'no decision'}). That decision stands: "
        "send the same decisions to continue the thread.",
    )


async def _state(context: RunContext, thread_id: str) -> Any:
    return await context.agent.aget_state({"configurable": {"thread_id": thread_id}})


@router.post("/{thread_id}/runs")
async def start_run(
    thread_id: ThreadIdPath,
    body: RunRequest,
    request: Request,
    debug: DebugQuery = False,
) -> EventSourceResponse:
    """Start a run with the user's message and stream it.

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
    """
    runtime = _runtime(request)
    run_id = uuid.uuid4().hex
    owner = lease_owner(runtime, run_id)
    if not await runtime.locks.acquire(thread_id, owner):
        raise _error(409, "run_active", "A run is already active on this thread.")
    context = await take_context(runtime, thread_id, run_id)
    if context is None:
        return EventSourceResponse(
            no_context_stream(runtime, thread_id, mode="run", debug=debug, run_id=run_id)
        )
    try:
        snapshot = await _state(context, thread_id)
        if snapshot.interrupts:
            raise _error(
                409,
                "decision_pending",
                "This thread is paused on a proposal; decide it through resume first.",
            )
        answers, cut_off = await answer_in_flight(runtime.agent_tasks, thread_id, snapshot)
        outcomes, delivering = await take_undelivered(runtime.agent_tasks, thread_id, snapshot)
    except BaseException:
        await context.release()
        await runtime.locks.release(thread_id, owner)
        raise

    async def delivered() -> None:
        await record_answers(runtime.agent_tasks, cut_off)
        await mark_delivered(runtime.agent_tasks, delivering)

    return EventSourceResponse(
        run_stream(
            runtime,
            thread_id,
            context=context,
            input={"messages": [*answers, *outcomes, HumanMessage(body.message)]},
            mode="run",
            debug=debug,
            run_id=run_id,
            on_recorded=delivered if delivering or cut_off else None,
        )
    )


@router.post("/{thread_id}/resume")
async def resume_run(
    thread_id: ThreadIdPath,
    body: ResumeRequest,
    request: Request,
    debug: DebugQuery = False,
) -> EventSourceResponse:
    """Submit one decision per pending action request and stream the continuation.

    Same lease-first order as ``start_run``. The request names the proposal it decides; a
    request naming any other id is refused before its decisions are looked at, so a decision
    sent twice cannot approve a later proposal. Decisions are then validated against the
    pending proposal before anything resumes; every refusal leaves the proposal untouched
    (FR-013).

    One context serves the whole request: the pending proposal is worded, the decisions are
    validated, and the continuation runs on the same orchestrator. Validating against one
    catalog and running on another would let a decision meant for one call be applied to
    another when an approval choice changed in between (005 research R3).
    """
    runtime = _runtime(request)
    run_id = uuid.uuid4().hex
    owner = lease_owner(runtime, run_id)
    if not await runtime.locks.acquire(thread_id, owner):
        raise _error(409, "run_active", "A run is already active on this thread.")
    context = await take_context(runtime, thread_id, run_id)
    if context is None:
        return EventSourceResponse(
            no_context_stream(runtime, thread_id, mode="resume", debug=debug, run_id=run_id)
        )
    try:
        snapshot = await _state(context, thread_id)
        if snapshot.metadata is None:
            raise _error(404, "thread_not_found", "This thread has never been used.")
        if not snapshot.interrupts:
            raise _error(409, "nothing_pending", "This thread has no pending decision.")
        if len(snapshot.interrupts) > 1:
            raise _error(
                500, "internal", "The thread is paused on more than one interrupt at once."
            )
        pending = interrupt_to_pending(snapshot.interrupts[0], context.catalog)
        if body.interrupt_id != pending["interrupt_id"]:
            raise _error(
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
    return EventSourceResponse(
        run_stream(
            runtime,
            thread_id,
            context=context,
            input=command,
            mode="resume",
            debug=debug,
            run_id=run_id,
            decided=decided,
        )
    )


@router.get("/{thread_id}/state")
async def read_state(thread_id: ThreadIdPath, request: Request) -> ThreadState:
    """Read the transcript, the run marker, any pending proposal (FR-007, research R8), and
    the thread's agent tasks (feature 003, FR-017).

    A thread exists once its first checkpoint is written; before that it is not found, so a
    mistyped id is not mistaken for a fresh thread. ``run_active`` comes from the shared
    lease, so it reflects a run on any instance. The read takes no lease: the snapshot is the
    latest durable checkpoint, which already reflects a stream that has closed (SC-005).

    A pending proposal is worded with the catalog of the agents as they are now; when they
    cannot be read, with what this instance last built (005 research R3).
    """
    runtime = _runtime(request)
    context = await runtime.begin_read()
    try:
        return await _read_state(runtime, context, thread_id)
    finally:
        await context.release()


async def _read_state(runtime: Runtime, context: RunContext, thread_id: str) -> ThreadState:
    snapshot = await _state(context, thread_id)
    if snapshot.metadata is None:
        raise _error(404, "thread_not_found", "This thread has never been used.")
    if len(snapshot.interrupts) > 1:
        raise _error(500, "internal", "The thread is paused on more than one interrupt at once.")
    return ThreadState(
        thread_id=thread_id,
        run_active=await runtime.locks.is_active(thread_id),
        messages=[message_to_dict(message) for message in snapshot.values.get("messages", [])],
        pending=(
            interrupt_to_pending(snapshot.interrupts[0], context.catalog)
            if snapshot.interrupts
            else None
        ),
        updated_at=snapshot.created_at,
        agent_tasks=[
            AgentTask(
                agent=task.agent,
                task_id=task.task_id,
                message=task.message,
                status=task.state,
                started_at=task.started_at,
                last_checked_at=task.last_checked_at,
                ended_at=task.ended_at,
                outcome_delivered=task.outcome_delivered,
                progress=(
                    AgentTaskProgress(text=task.progress, learned_at=task.progress_at)
                    if task.progress is not None and task.progress_at is not None
                    else None
                ),
            )
            for task in await runtime.agent_tasks.list_tasks(thread_id)
        ],
    )
