"""Thread endpoints: start a run, read state, resume a paused thread (contracts/openapi.yaml).

The bodies of the two routes that run a thread are in ``api/runs.py`` since feature 009, so
that the console's routes and the A2A service start runs through the same code.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sse_starlette import EventSourceResponse

from freyja_agent.api.guards import refuse_other_origins
from freyja_agent.api.runs import refuse_caller_thread, start_message, start_resume, state_of
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


@router.post("/{thread_id}/runs")
async def start_run(
    thread_id: ThreadIdPath,
    body: RunRequest,
    request: Request,
    debug: DebugQuery = False,
) -> EventSourceResponse:
    """Start a run with the user's message and stream it (``runs.start_message``).

    A thread another agent started takes no message here (feature 009, ``caller_thread``).
    """
    runtime = _runtime(request)
    await refuse_caller_thread(runtime, thread_id)
    return EventSourceResponse(await start_message(runtime, thread_id, body.message, debug=debug))


@router.post("/{thread_id}/resume")
async def resume_run(
    thread_id: ThreadIdPath,
    body: ResumeRequest,
    request: Request,
    debug: DebugQuery = False,
) -> EventSourceResponse:
    """Submit one decision per pending action request and stream the continuation
    (``runs.start_resume``)."""
    return EventSourceResponse(await start_resume(_runtime(request), thread_id, body, debug=debug))


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
    context = await runtime.begin_read(thread_id)
    try:
        return await _read_state(runtime, context, thread_id)
    finally:
        await context.release()


async def _read_state(runtime: Runtime, context: RunContext, thread_id: str) -> ThreadState:
    snapshot = await state_of(context, thread_id)
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
