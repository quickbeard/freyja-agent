"""The work that waits, read for telemetry (feature 011, research R10).

Two numbers an operator can act on, which the service already keeps: how many threads have
a proposal that no person has decided, and how old the oldest agent task's outcome is that
is ready and has not been delivered to its thread. Both are facts of the stores every
instance shares, so every instance reads the same and a collector does not add them up.

They are read on a timer, at the interval metrics are sent, and never on the path of a run.
A reading takes no thread's lease, changes no thread, and contacts no model, tool server,
or agent. It does write one thing: the index of threads settles a row at the first reading
after its run ended (feature 007), so a count taken without that would stay too low where
nobody opens the console. The reading settles those rows as the list of threads does.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from freyja_agent.runtime.thread_index import settle_thread

if TYPE_CHECKING:  # the runtime holds the reading it starts
    from freyja_agent.runtime.runtime import Runtime

logger = logging.getLogger(__name__)


async def read_waiting(runtime: "Runtime") -> None:
    """Read the two values once and hand them to telemetry. Raises when a store cannot be
    read; one thread that cannot be looked at is left for the next reading."""
    # What this instance last built, which reads nothing and holds nothing: a thread's
    # state is read through it, and no agent or profile is looked up for a reading.
    context = runtime.last_built()
    for record in await runtime.threads.unsettled():
        # Whether a run is in progress is the lease's to say: such a row is left as it is.
        if await runtime.locks.is_active(record.thread_id):
            continue
        try:
            await settle_thread(
                runtime.threads, context, runtime.agent_tasks, record.thread_id, record.runs
            )
        except Exception as exc:
            logger.debug(
                "Thread %s could not be looked at for the work that waits (%s)",
                record.thread_id,
                type(exc).__name__,
            )
    awaiting_decision, _total = await runtime.threads.counts()
    oldest_undelivered = await runtime.agent_tasks.oldest_ready_outcome_age()
    runtime.telemetry.waiting(awaiting_decision, oldest_undelivered)


class WaitingWork:
    """The timer that reads the work that waits, for as long as the runtime is open."""

    def __init__(self, runtime: "Runtime", interval: float) -> None:
        self.runtime = runtime
        self.interval = interval
        self._loop: asyncio.Task[None] | None = None
        # Whether the last reading failed, so that a store that is away is said once.
        self._failed = False

    def wanted(self, *, run_loops: bool) -> bool:
        """Whether the timer is to run: only with telemetry on, and only where the runtime
        runs its loops at all, which the suite's applications do not."""
        return run_loops and self.runtime.telemetry.on

    @property
    def running(self) -> bool:
        return self._loop is not None

    def start(self) -> None:
        if self._loop is None:
            self._loop = asyncio.create_task(self._run(), name="freyja-waiting-work")

    async def stop(self) -> None:
        loop, self._loop = self._loop, None
        if loop is not None:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await self.read()
            await asyncio.sleep(self.interval)

    async def read(self) -> None:
        """One reading, which never raises. One that fails leaves both values out, and is
        said in the log once: the next line comes only after a reading succeeded."""
        try:
            await read_waiting(self.runtime)
        except Exception as exc:
            self.runtime.telemetry.waiting_unknown()
            if not self._failed:
                self._failed = True
                logger.warning(
                    "Telemetry: the work that waits could not be read (%s). Its two values "
                    "are left out until a reading succeeds; further failures are not logged.",
                    type(exc).__name__,
                )
        else:
            self._failed = False
