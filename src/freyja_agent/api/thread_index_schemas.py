"""Response models of the thread index routes (specs/007-web-tasks/contracts/openapi.yaml)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ThreadStatus = Literal[
    "running", "needs_decision", "failed", "stopped", "waiting_for_agent", "done"
]


class RunError(BaseModel):
    """The code and the message of a failed run's ``error`` event, as they were sent."""

    code: str
    message: str


class CallerSummary(BaseModel):
    """Who started a thread over A2A (feature 009, contracts/openapi.yaml)."""

    # The caller's name as the deployment configured it, or ``local`` for an agent that
    # called on the service's own address without a token.
    name: str
    # True while the last task of the conversation is one its caller canceled, or one the
    # service canceled because the deployment no longer names the caller.
    canceled: bool
    # Whether the settings name the caller now. Always true for ``local``.
    configured: bool


class ThreadSummary(BaseModel):
    """What the list holds of one thread."""

    thread_id: str
    # The beginning of the thread's first message from a person; None until it is known.
    first_message: str | None
    # Worked out by the service, so that every client shows the same (research R7).
    status: ThreadStatus
    # With ``failed`` only.
    error: RunError | None
    created_at: datetime
    # While a run is in progress, when it started; otherwise the thread's last recorded step.
    changed_at: datetime
    # True when the run in progress was started through the unattended routes.
    stoppable: bool
    # True when a stop was asked for the run in progress and the run has not ended yet.
    stopping: bool
    # How many runs have started on the thread. The status is of the last of them, so a
    # client that asked to stop run ``n`` knows whether a row still speaks of that run.
    runs: int
    # Feature 009: null for a thread started in the console or through this API; for a
    # thread a caller started over A2A, who, and whether it canceled its last task.
    caller: CallerSummary | None


class ThreadList(BaseModel):
    threads: list[ThreadSummary]
    # How many threads need a decision, among all threads, listed or not.
    needs_decision: int
    # How many threads the index holds.
    total: int
    # True when ``total`` is greater than the number of rows returned.
    more: bool


class StopAnswer(BaseModel):
    thread_id: str
    stopping: Literal[True] = True
    # Which of the thread's runs the stop was asked for: ``runs`` of its row at that time.
    run: int
