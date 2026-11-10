"""Watching pending agent tasks and delivering their outcomes (feature 003, research R9, R10).

One ``DelegationWorker`` runs per service instance. Each tick, at the poll interval:

1. it claims the pending tasks no other instance holds (``watch_until = now + 3 * interval``),
   so each task is watched by exactly one instance, and another instance takes it over once a
   claim lapses (FR-019);
2. it gives up each claimed task past the maximum age, or whose agent is no longer configured,
   without sending the agent anything (FR-021, FR-023), and checks each other ``working`` one
   with the agent, recording a change with its outcome ready to deliver. A task waiting for
   input is not checked: nothing changes at the agent until the person answers. Watching only
   reads; it never sends the agent new work;
3. for each thread with an outcome waiting, it takes the thread's run lease, leaves a thread
   paused on a proposal alone, and runs a background run whose input is the outcome messages.
   The run goes through ``run_stream`` like any other: the same agent, gate, step limit, and
   lease heartbeat (FR-020, constitution principles I and III). One that fails with a model or
   internal error is tried again after the retry interval, up to the configured attempts,
   continuing from its last checkpoint (FR-027).

The suite calls ``tick()`` itself; the service runs ``start()``'s loop.
"""

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from a2a.client.errors import A2AClientTimeoutError
from a2a.utils.errors import TaskNotFoundError

from freyja_agent.api.outcomes import (
    answer_in_flight,
    mark_delivered,
    record_answers,
    take_undelivered,
)
from freyja_agent.api.streaming import lease_owner, run_stream
from freyja_agent.delegates.a2a_agents import AgentLink, state_name
from freyja_agent.delegates.a2a_tools import a2a_result_text, record_outcome
from freyja_agent.runtime.agent_tasks import PENDING_STATES, AgentTaskRecord

if TYPE_CHECKING:  # the runtime builds the worker
    from freyja_agent.runtime.runtime import Runtime

logger = logging.getLogger(__name__)

# A claim lasts this many poll intervals, so one missed tick does not hand a task over.
CLAIM_INTERVALS = 3
NOT_FOUND = "The agent no longer knows this task."
# A background run that ends with one of these is tried again (FR-027); the step limit is not.
RETRIED = frozenset({"model_error", "internal"})


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
        """Claim, check or give up, then deliver and retry, once (research R9, R10)."""
        store = self.runtime.agent_tasks
        claimed = await store.claim_due(self.runtime.instance_id, CLAIM_INTERVALS * self.interval)
        now = await store.now()
        max_age = self.runtime.settings.a2a_task_max_age_seconds
        links = self._links
        watching = []
        for task in claimed:
            if task.agent not in links:
                watching.append(self._give_up(task, removed=True))
            elif (now - task.started_at).total_seconds() >= max_age:
                watching.append(self._give_up(task, removed=False))
            elif task.state == "working":
                watching.append(self._check(task))
        await asyncio.gather(*watching)
        threads = set(await store.threads_with_ready_outcomes())
        threads |= set(await store.threads_with_due_followups())
        await asyncio.gather(*(self._deliver(thread_id) for thread_id in sorted(threads)))

    async def _give_up(self, task: AgentTaskRecord, *, removed: bool) -> None:
        """Stop watching a task: past the maximum age, or its agent is gone (FR-021, FR-023).

        No cancel is sent: the agent may still be working, and the report says the person can
        ask for one, which goes through the gate like any message to the agent (FR-022).
        """
        if removed:
            outcome = (
                f"Freyja stopped waiting for task {task.task_id} of agent '{task.agent}', which "
                "is no longer configured; the agent may still be working on it."
            )
        else:
            outcome = (
                f"Freyja stopped waiting for task {task.task_id} of agent '{task.agent}'; the "
                "agent may still be working on it. Ask to cancel it if it should stop."
            )
        store = self.runtime.agent_tasks
        now = await store.now()
        if await store.update_task(
            task.agent,
            task.task_id,
            when_state=PENDING_STATES,
            state="given_up",
            last_checked_at=now,
            ended_at=now,
            outcome=outcome,
            outcome_ready=True,
            delivered_at=None,
        ):
            reason = (
                "the agent is no longer configured" if removed else "it reached the maximum age"
            )
            logger.warning(
                "%s",
                self._describe(
                    task.agent,
                    f"Agent {task.agent}: gave up task {task.task_id} on thread "
                    f"{task.thread_id}: {reason}; no cancel was sent",
                ),
            )

    async def _check(self, task: AgentTaskRecord) -> None:
        """Ask the agent about one working task and record what changed (FR-019)."""
        link = self._links[task.agent]
        try:
            found = await link.get_task(task.task_id)
        except TaskNotFoundError:
            link.mark(True)
            state, text = "failed", NOT_FOUND
        except (TimeoutError, A2AClientTimeoutError):
            self._unchecked(link, task, f"it did not answer within {link.call_timeout:g} seconds")
            return
        except Exception as exc:  # unreachable or a protocol error: checked again next tick
            self._unchecked(link, task, str(exc) or type(exc).__name__)
            return
        else:
            link.mark(True)
            state, text = state_name(found.status.state), a2a_result_text(found)
        store = self.runtime.agent_tasks
        if state == "working":
            now = await store.now()
            await store.update_task(
                task.agent, task.task_id, when_state=("working",), last_checked_at=now
            )
            return
        # Recorded only while the task is still pending here: one Freyja canceled meanwhile
        # keeps its state, and its late outcome is ignored (FR-022).
        await record_outcome(store, task, state, text, ready=True)

    def _describe(self, agent: str, text: str) -> str:
        """Text about an agent that is safe to log: its address removed (FR-009, research R14)."""
        link = self._links.get(agent)
        return link.describe(text) if link is not None else text

    @staticmethod
    def _unchecked(link: AgentLink, task: AgentTaskRecord, why: str) -> None:
        """A status check that failed: the agent is marked unavailable, and the task stays."""
        link.mark(False, why)
        logger.warning(
            "%s",
            link.describe(
                f"Agent {link.name}: could not check task {task.task_id} on thread "
                f"{task.thread_id}: {why}; checking again next interval"
            ),
        )

    async def _deliver(self, thread_id: str) -> None:
        """Run one background run on the thread, if it is free: a first delivery or a retry.

        A thread whose last background run failed waits for its retry time, then runs again
        from its last checkpoint, with any outcome that run never recorded (FR-027).
        """
        runtime = self.runtime
        store = runtime.agent_tasks
        retrying = [task for task in await store.list_tasks(thread_id) if task.followup_next_at]
        now = await store.now()
        if any(task.followup_next_at and task.followup_next_at > now for task in retrying):
            return  # a failed attempt waits for its retry interval
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
            messages, delivering = await take_undelivered(store, thread_id, snapshot)
            if not messages and not retrying:
                return
            # Messages a stop left in flight are answered first, as at the start of a user's
            # run (FR-017).
            answers, cut_off = await answer_in_flight(store, thread_id, snapshot)
            messages = [*answers, *messages]
            attempted = max((task.followup_attempts for task in retrying), default=0)
            keys = sorted({(t.agent, t.task_id) for t in [*retrying, *delivering]})

            async def delivered() -> None:
                await record_answers(store, cut_off)
                await mark_delivered(store, delivering)

            async def ended(kind: str, code: str | None) -> None:
                await self._ended(thread_id, run_id, keys, attempted + 1, kind, code)

            logger.info(
                "Background run %s on thread %s for %s: attempt %d of %d",
                run_id,
                thread_id,
                ", ".join(f"{agent}/{task_id}" for agent, task_id in keys),
                attempted + 1,
                runtime.settings.background_run_attempts,
            )
            stream = run_stream(
                runtime,
                thread_id,
                # A retry continues from the last checkpoint (input None), with any outcome the
                # failed attempt had not recorded yet.
                input={"messages": messages} if messages else None,
                mode="background",
                debug=False,
                run_id=run_id,
                on_end=ended,
                on_recorded=delivered if delivering or cut_off else None,
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

    async def _ended(
        self,
        thread_id: str,
        run_id: str,
        keys: list[tuple[str, str]],
        attempt: int,
        kind: str,
        code: str | None,
    ) -> None:
        """Schedule the next attempt of a background run that failed, or give up (FR-027).

        A run that ends normally, pauses, or reaches the step limit is not retried; the stream
        clears the thread's retries then (``run_stream``).
        """
        tasks = ", ".join(f"{agent}/{task_id}" for agent, task_id in keys)
        if kind != "error" or code not in RETRIED:
            logger.info(
                "Background run %s on thread %s for %s ended with %s",
                run_id,
                thread_id,
                tasks,
                code or kind,
            )
            return
        store = self.runtime.agent_tasks
        settings = self.runtime.settings
        if attempt >= settings.background_run_attempts:
            logger.warning(
                "Background run %s on thread %s for %s ended with %s; gave up after %d "
                "attempts, and the outcome stays in the thread for its next run",
                run_id,
                thread_id,
                tasks,
                code,
                attempt,
            )
            await store.set_followup(keys, attempts=attempt, next_at=None, error=code)
            return
        next_at = await store.now() + timedelta(seconds=settings.background_retry_seconds)
        logger.warning(
            "Background run %s on thread %s for %s ended with %s (attempt %d of %d); trying "
            "again at %s",
            run_id,
            thread_id,
            tasks,
            code,
            attempt,
            settings.background_run_attempts,
            next_at.isoformat(),
        )
        await store.set_followup(keys, attempts=attempt, next_at=next_at, error=code)
