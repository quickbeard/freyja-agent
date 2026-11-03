"""Thread endpoints: start a run, read state, resume a paused thread (contracts/openapi.yaml)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from langchain_core.messages import HumanMessage
from sse_starlette import EventSourceResponse

from freyja_agent.api.schemas import THREAD_ID_PATTERN, RunRequest
from freyja_agent.api.streaming import lease_owner, run_stream
from freyja_agent.runtime.runtime import Runtime

router = APIRouter(prefix="/threads", tags=["threads"])

ThreadIdPath = Annotated[str, Path(pattern=THREAD_ID_PATTERN)]
DebugQuery = Annotated[bool, Query(description="Add debug events; never changes the run.")]


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


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
        raise _conflict("run_active", "A run is already active on this thread.")
    try:
        snapshot = await runtime.agent.aget_state({"configurable": {"thread_id": thread_id}})
        if snapshot.interrupts:
            raise _conflict(
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
