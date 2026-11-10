"""Watching pending agent tasks and delivering their outcomes (feature 003, research R9, R10).

One ``DelegationWorker`` runs per service instance. Each tick, at the poll interval:

1. it claims the pending tasks no other instance holds (``watch_until = now + 3 * interval``),
   so each task is watched by exactly one instance, and another instance takes it over once a
   claim lapses (FR-019);
2. it checks each claimed ``working`` task with the agent and records a change with its outcome
   ready to deliver. A task waiting for input is not checked: nothing changes at the agent until
   the person answers. Watching only reads; it never sends the agent new work;
3. for each thread with an outcome waiting, it takes the thread's run lease, leaves a thread
   paused on a proposal alone, and runs a background run whose input is the outcome messages.
   The run goes through ``run_stream`` like any other: the same agent, gate, step limit, and
   lease heartbeat (FR-020, constitution principles I and III).

The suite calls ``tick()`` itself; the service runs ``start()``'s loop.
"""

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

from a2a.types import GetTaskRequest
from a2a.utils.errors import TaskNotFoundError

from freyja_agent.api.outcomes import mark_delivered, take_undelivered
from freyja_agent.api.streaming import lease_owner, run_stream
from freyja_agent.delegates.a2a_agents import AgentLink, state_name
from freyja_agent.delegates.a2a_tools import a2a_result_text, outcome_text
from freyja_agent.runtime.agent_tasks import AgentTaskRecord

if TYPE_CHECKING:  # the runtime builds the worker
    from freyja_agent.runtime.runtime import Runtime

logger = logging.getLogger(__name__)

# A claim lasts this many poll intervals, so one missed tick does not hand a task over.
CLAIM_INTERVALS = 3
NOT_FOUND = "The agent no longer knows this task."


class DelegationWorker:
    """Watches this instance's share of pending tasks and delivers ready outcomes."""

    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        self.interval = runtime.settings.a2a_poll_interval_seconds
        self._loop: asyncio.Task[None] | None = None

    @property
    def _links(self) -> dict[str, AgentLink]:
        return {link.name: link for link in self.runtime.agents}

    def start(self) -> None:
        """Tick now and then at every poll interval, until ``stop``."""
        if self._loop is None:
            self._loop = asyncio.create_task(self._run(), name="freyja-delegation-worker")

    async def stop(self) -> None:
        loop, self._loop = self._loop, None
        if loop is not None:
            loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # one failed tick must not stop the watching
                logger.exception("Delegation worker: the tick failed; trying again next interval")
            await asyncio.sleep(self.interval)

    async def tick(self) -> None:
        """Claim, check, and deliver once (research R9, R10)."""
        store = self.runtime.agent_tasks
        claimed = await store.claim_due(self.runtime.instance_id, CLAIM_INTERVALS * self.interval)
        await asyncio.gather(*(self._check(task) for task in claimed if task.state == "working"))
        threads = await store.threads_with_ready_outcomes()
        await asyncio.gather(*(self._deliver(thread_id) for thread_id in threads))

    async def _check(self, task: AgentTaskRecord) -> None:
        """Ask the agent about one working task and record what changed (FR-019)."""
        link = self._links.get(task.agent)
        if link is None or link.client is None:
            return
        try:
            async with asyncio.timeout(link.call_timeout):
                found = await link.client.get_task(GetTaskRequest(id=task.task_id))
        except TaskNotFoundError:
            link.mark(True)
            state, text = "failed", NOT_FOUND
        except TimeoutError:
            link.mark(False, f"it did not answer within {link.call_timeout:g} seconds")
            return
        except Exception as exc:  # unreachable or a protocol error: checked again next tick
            link.mark(False, str(exc) or type(exc).__name__)
            return
        else:
            link.mark(True)
            state, text = state_name(found.status.state), a2a_result_text(found)
        store = self.runtime.agent_tasks
        now = await store.now()
        if state == "working":
            await store.update_task(task.agent, task.task_id, last_checked_at=now)
            return
        outcome, _ok = outcome_text(task.agent, task.task_id, state, text)
        stored = "failed" if state == "auth_required" else state
        await store.update_task(
            task.agent,
            task.task_id,
            state=stored,
            last_checked_at=now,
            ended_at=None if stored == "input_required" else now,
            outcome=outcome,
            outcome_ready=True,
            delivered_at=None,
        )

    async def _deliver(self, thread_id: str) -> None:
        """Run one background run that records the thread's waiting outcomes, if it is free."""
        runtime = self.runtime
        run_id = uuid.uuid4().hex
        owner = lease_owner(runtime, run_id)
        try:
            if not await runtime.locks.acquire(thread_id, owner):
                return  # a run is in progress; it or a later tick delivers
        except Exception:
            logger.exception("Delegation worker: no lease for thread %s", thread_id)
            return
        streaming = False
        try:
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await runtime.agent.aget_state(config)
            if snapshot.interrupts:
                return  # paused on a proposal: delivered once it is decided
            messages, delivering = await take_undelivered(runtime.agent_tasks, thread_id, snapshot)
            if not messages:
                return

            async def delivered() -> None:
                await mark_delivered(runtime.agent_tasks, delivering)

            async def ended(kind: str, code: str | None) -> None:
                tasks = ", ".join(f"{task.agent}/{task.task_id}" for task in delivering)
                if kind == "error":
                    logger.warning(
                        "Background run %s on thread %s for %s ended with %s",
                        run_id,
                        thread_id,
                        tasks,
                        code,
                    )
                else:
                    logger.info(
                        "Background run %s on thread %s for %s ended with %s",
                        run_id,
                        thread_id,
                        tasks,
                        kind,
                    )

            stream = run_stream(
                runtime,
                thread_id,
                input={"messages": messages},
                mode="background",
                debug=False,
                run_id=run_id,
                on_end=ended,
                on_recorded=delivered,
            )
            # From here the stream owns the lease and releases it however the run ends.
            streaming = True
            async for _event in stream:
                pass
        except Exception:
            logger.exception("Delegation worker: delivery on thread %s failed", thread_id)
        finally:
            if not streaming:
                await runtime.locks.release(thread_id, owner)
