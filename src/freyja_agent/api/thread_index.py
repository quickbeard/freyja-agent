"""The routes that list threads (feature 007, specs/007-web-tasks/contracts/openapi.yaml).

The web console calls a thread a task; the API keeps the word thread. A thread's
conversation, its pending proposal, and its agent tasks are still read from
``GET /threads/{thread_id}/state``. What these routes add is which threads there are and
where each stands, from the index the runtime keeps through the run lease
(``runtime/thread_index.py``).

Both routes contact no model, agent, or tool server and change no thread. They do write to
the index: a row whose run has ended and has not been looked at is settled here, by one read
of the thread's state, and what was found is kept (research R6).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from freyja_agent.api.guards import refuse_other_origins
from freyja_agent.api.thread_index_schemas import RunError, ThreadList, ThreadSummary
from freyja_agent.api.threads import ThreadIdPath
from freyja_agent.runtime.runtime import RunContext, Runtime
from freyja_agent.runtime.thread_index import (
    LIST_LIMIT,
    ThreadRecord,
    open_thread,
    settle_thread,
    status_of,
    stoppable,
    stopping,
)

logger = logging.getLogger(__name__)

# The prefix is the thread routes' own: these are thread routes, in a module of their own.
router = APIRouter(
    prefix="/threads",
    tags=["threads"],
    dependencies=[Depends(refuse_other_origins("Threads"))],
)


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _unreadable(what: str, exc: Exception) -> HTTPException:
    """The list never answers with an earlier one (spec FR-023)."""
    logger.error("The list of threads could not be read (%s): %s", what, type(exc).__name__)
    return _error(500, "internal", "The list of threads could not be read.")


def _summary(record: ThreadRecord, lease_active: bool) -> ThreadSummary:
    status = status_of(record, lease_active)
    return ThreadSummary(
        thread_id=record.thread_id,
        first_message=record.first_message,
        status=status,
        error=(
            RunError(code=record.error_code or "internal", message=record.error_message or "")
            if status == "failed"
            else None
        ),
        created_at=record.created_at,
        changed_at=record.changed_at,
        stoppable=stoppable(record, lease_active),
        stopping=stopping(record, lease_active),
    )


# How often a row is looked at again within one request when a run started or ended while it
# was being read. Past that the row is left for the next request.
_ATTEMPTS = 3


async def _view(
    runtime: Runtime, context: RunContext, record: ThreadRecord, *, again: bool = False
) -> tuple[ThreadRecord, bool] | None:
    """The row as it can be shown, with whether its lease is held; None when it cannot be.

    A row is shown as in progress, by its lease, or as it was settled. One that was not
    looked at since its run ended has no status, and none is made up for it: it is settled
    here first, by one read of the thread's state. A run can start or end while that read is
    made; the write is then refused or undone, and the row is looked at again. ``again``
    reads the state also for a row that was settled, which is how a row that was kept wrong
    is put right once its thread is opened (spec FR-023).

    None for a thread with no state, whose row is removed, and for a row that was still not
    settled after ``_ATTEMPTS`` readings.
    """
    for _ in range(_ATTEMPTS):
        if await runtime.locks.is_active(record.thread_id):
            return record, True
        if record.settled is not None and not again:
            return record, False
        again = False
        looked = await settle_thread(
            runtime.threads, context, runtime.agent_tasks, record.thread_id, record.runs
        )
        if looked is None:
            return None
        record = looked
    return None


async def _view_or_skip(
    runtime: Runtime, context: RunContext, record: ThreadRecord
) -> tuple[ThreadRecord, bool] | None:
    """``_view``, with a row that cannot be read left out and not failing the whole list.

    One thread whose state cannot be read, or whose row cannot be written, must not take the
    list away from every other thread. It is logged, and tried again at the next request.
    """
    try:
        return await _view(runtime, context, record)
    except Exception as exc:
        logger.warning(
            "Thread %s could not be looked at for the list of threads (%s); it is left out "
            "of this answer",
            record.thread_id,
            type(exc).__name__,
        )
        return None


@router.get("")
async def list_threads(request: Request) -> ThreadList:
    """Every thread that needs a decision, then the others up to 100 rows, and the counts.

    Every row whose run ended since the list was last read is settled first, however many
    there are: the number of threads that need a decision counts every thread, listed or
    not. The counts are taken last, after any row was settled while the list was put
    together.
    """
    runtime = _runtime(request)
    context = await runtime.begin_read()
    try:
        try:
            for record in await runtime.threads.unsettled():
                await _view_or_skip(runtime, context, record)
            threads = []
            for record in await runtime.threads.list(LIST_LIMIT):
                view = await _view_or_skip(runtime, context, record)
                if view is not None:
                    threads.append(_summary(*view))
            needs_decision, total = await runtime.threads.counts()
        except Exception as exc:
            raise _unreadable("list", exc) from exc
    finally:
        await context.release()
    return ThreadList(
        threads=threads,
        needs_decision=needs_decision,
        total=total,
        more=total > len(threads),
    )


@router.get("/{thread_id}")
async def read_thread(thread_id: ThreadIdPath, request: Request) -> ThreadSummary:
    """One thread's row, for a page that shows it beside the thread's state.

    A thread the index has no row for, and that has a state, is given its row here: a thread
    from before the index, or one whose row could not be written (spec FR-004). While no run
    is in progress the thread's state is read again each time, so a row that was kept wrong
    is put right by being opened.
    """
    runtime = _runtime(request)
    context = await runtime.begin_read()
    try:
        try:
            record = await runtime.threads.get(thread_id)
            if record is None:
                record = await open_thread(
                    runtime.threads,
                    context,
                    runtime.agent_tasks,
                    thread_id,
                    lease_active=await runtime.locks.is_active(thread_id),
                )
                view = None if record is None else await _view(runtime, context, record)
                missing = record is None
            else:
                view = await _view(runtime, context, record, again=True)
                missing = view is None and await runtime.threads.get(thread_id) is None
        except Exception as exc:
            raise _unreadable(f"thread {thread_id}", exc) from exc
    finally:
        await context.release()
    if view is None:
        if missing:
            raise _error(404, "thread_not_found", "This thread has never been used.")
        # Runs kept starting and ending while the row was read; the next request reads it.
        raise _error(500, "internal", "The list of threads could not be read.")
    return _summary(*view)
