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
