"""Thread endpoints: start a run, read state, resume a paused thread (contracts/openapi.yaml)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request
from langchain_core.messages import HumanMessage
from sse_starlette import EventSourceResponse

from freyja_agent.agent.tools import GATED_TOOLS
from freyja_agent.api.decisions import build_resume_command
from freyja_agent.api.schemas import (
    THREAD_ID_MAX_LENGTH,
    THREAD_ID_PATTERN,
    ResumeRequest,
    RunRequest,
    ThreadState,
)
from freyja_agent.api.serialize import interrupt_to_pending, message_to_dict
from freyja_agent.api.streaming import lease_owner, run_stream
from freyja_agent.runtime.runtime import Runtime

router = APIRouter(prefix="/threads", tags=["threads"])

ThreadIdPath = Annotated[
    str, Path(min_length=1, max_length=THREAD_ID_MAX_LENGTH, pattern=THREAD_ID_PATTERN)
]
DebugQuery = Annotated[bool, Query(description="Add debug events; never changes the run.")]


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _state(runtime: Runtime, thread_id: str) -> Any:
    return await runtime.agent.aget_state({"configurable": {"thread_id": thread_id}})


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
    """
    runtime = _runtime(request)
    run_id = uuid.uuid4().hex
    owner = lease_owner(runtime, run_id)
    if not await runtime.locks.acquire(thread_id, owner):
        raise _error(409, "run_active", "A run is already active on this thread.")
    try:
        snapshot = await _state(runtime, thread_id)
        if snapshot.interrupts:
            raise _error(
                409,
                "decision_pending",
                "This thread is paused on a proposal; decide it through resume first.",
            )
    except BaseException:
        await runtime.locks.release(thread_id, owner)
        raise
    return EventSourceResponse(
        run_stream(
            runtime,
            thread_id,
            input={"messages": [HumanMessage(body.message)]},
            mode="run",
            debug=debug,
            run_id=run_id,
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
    """
    runtime = _runtime(request)
    run_id = uuid.uuid4().hex
    owner = lease_owner(runtime, run_id)
    if not await runtime.locks.acquire(thread_id, owner):
        raise _error(409, "run_active", "A run is already active on this thread.")
    try:
        snapshot = await _state(runtime, thread_id)
        if snapshot.metadata is None:
            raise _error(404, "thread_not_found", "This thread has never been used.")
        if not snapshot.interrupts:
            raise _error(409, "nothing_pending", "This thread has no pending decision.")
        if len(snapshot.interrupts) > 1:
            raise _error(
                500, "internal", "The thread is paused on more than one interrupt at once."
            )
        pending = interrupt_to_pending(snapshot.interrupts[0])
        if body.interrupt_id != pending["interrupt_id"]:
            raise _error(
                409,
                "proposal_mismatch",
                "The interrupt_id is not the pending proposal's; read the thread state and "
                "decide the proposal it shows.",
            )
        command = build_resume_command(pending, body.decisions, GATED_TOOLS)
    except BaseException:
        await runtime.locks.release(thread_id, owner)
        raise
    return EventSourceResponse(
        run_stream(runtime, thread_id, input=command, mode="resume", debug=debug, run_id=run_id)
    )


@router.get("/{thread_id}/state")
async def read_state(thread_id: ThreadIdPath, request: Request) -> ThreadState:
    """Read the transcript, the run marker, and any pending proposal (FR-007, research R8).

    A thread exists once its first checkpoint is written; before that it is not found, so a
    mistyped id is not mistaken for a fresh thread. ``run_active`` comes from the shared
    lease, so it reflects a run on any instance. The read takes no lease: the snapshot is the
    latest durable checkpoint, which already reflects a stream that has closed (SC-005).
    """
    runtime = _runtime(request)
    snapshot = await _state(runtime, thread_id)
    if snapshot.metadata is None:
        raise _error(404, "thread_not_found", "This thread has never been used.")
    if len(snapshot.interrupts) > 1:
        raise _error(500, "internal", "The thread is paused on more than one interrupt at once.")
    return ThreadState(
        thread_id=thread_id,
        run_active=await runtime.locks.is_active(thread_id),
        messages=[message_to_dict(message) for message in snapshot.values.get("messages", [])],
        pending=interrupt_to_pending(snapshot.interrupts[0]) if snapshot.interrupts else None,
        updated_at=snapshot.created_at,
    )
