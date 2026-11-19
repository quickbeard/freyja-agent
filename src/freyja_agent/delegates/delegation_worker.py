"""Watching pending agent tasks and delivering their outcomes (feature 003, research R9, R10).

One ``DelegationWorker`` runs per service instance. Each round, at the poll interval:

1. it claims the pending tasks no other instance holds (``watch_until = now + 3 * interval``),
   so each task is watched by exactly one instance, and another instance takes it over once a
   claim lapses (FR-019);
2. it gives up each claimed task past the maximum age, or whose agent is no longer the one
   in use under its name, without sending the agent anything (FR-021, FR-023; 005 FR-025),
   and checks each other one with the agent, recording a change with its outcome ready to
   deliver. A task waiting for input is checked too, because the agent can end it before the
   person answers; while it still waits, nothing about it is recorded. Watching only reads;
   it never sends the agent new work. An agent this instance holds no usable card from is
   asked for its card once in the round; while that fails, its tasks stay pending and
   unchecked (005 FR-026);
3. for each thread with an outcome waiting, it leaves a thread paused on a proposal alone,
   without taking its lease, and otherwise takes the thread's run lease and runs a background
   run whose input is the outcome messages.
   The run goes through ``run_stream`` like any other: the same agent, gate, step limit, and
   lease heartbeat (FR-020, constitution principles I and III). One that fails with a model or
   internal error is tried again after the retry interval, up to the configured attempts,
   continuing from its last checkpoint (FR-027).

Steps 1 and 2 are ``watch()`` and step 3 is ``start_deliveries()``. Each delivery is its own
task, so a background run that takes minutes does not keep the next round from watching the
other tasks or from delivering to other threads (SC-003). At most
``FREYJA_BACKGROUND_RUN_CONCURRENCY`` background runs are under way at once on an instance, and
a thread this instance is already delivering to is not started again.

The service runs ``start()``'s loop, which never waits for a delivery. The suite calls
``tick()``, which is one round that also waits for the deliveries, so a test reads the result
right after it.

Since feature 005 the agents can change while the service runs. A round of watching takes one
context before it claims, so that it never gives up the task of an agent another instance
has just added, and a delivery takes the context its run uses under the thread's lease (005
research R3).
"""

import asyncio
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
from freyja_agent.api.streaming import lease_owner, run_stream, take_context
from freyja_agent.delegates.a2a_agents import ERROR_TEXT_LIMIT, AgentLink, state_name
from freyja_agent.delegates.a2a_tools import a2a_result_text, record_outcome
from freyja_agent.delegates.addresses import SCRUBBER
from freyja_agent.delegates.agent_cards import SENTENCES, CardError
from freyja_agent.runtime.agent_tasks import (
    GIVEN_UP_AGED,
    GIVEN_UP_MOVED,
    GIVEN_UP_REMOVED,
    PENDING_STATES,
    AgentTaskRecord,
    given_up_text,
)

if TYPE_CHECKING:  # the runtime builds the worker
    from freyja_agent.runtime.runtime import RunContext, Runtime

logger = logging.getLogger(__name__)

# A claim lasts this many poll intervals, so one missed tick does not hand a task over.
CLAIM_INTERVALS = 3
NOT_FOUND = "The agent no longer knows this task."
# A background run that ends with one of these is tried again (FR-027); the step limit is not.
RETRIED = frozenset({"model_error", "internal"})
# Why a task is given up, as the log says it.
_WHY = {
    GIVEN_UP_AGED: "it reached the maximum age",
    GIVEN_UP_REMOVED: "the agent is no longer configured",
    GIVEN_UP_MOVED: "the agent was moved",
}


class DelegationWorker:
    """Watches this instance's share of pending tasks and delivers ready outcomes."""

    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        self.interval = runtime.settings.a2a_poll_interval_seconds
        self._loop: asyncio.Task[None] | None = None
        # The deliveries under way or waiting for a slot, by thread.
        self._deliveries: dict[str, asyncio.Task[None]] = {}
        self._slots = asyncio.Semaphore(runtime.settings.background_run_concurrency)
        # Whether deliveries are held back for want of a usable model, to say so once.
        self._held = False

    def start(self) -> None:
        """Watch and start deliveries now and then at every poll interval, until ``stop``."""
        if self._loop is None:
            self._loop = asyncio.create_task(self._run(), name="freyja-delegation-worker")

    async def stop(self) -> None:
        """Stop the loop and the deliveries under way; each run releases its lease."""
        loop, self._loop = self._loop, None
        stopping = [*([loop] if loop is not None else []), *self._deliveries.values()]
        self._deliveries.clear()
        for task in stopping:
            task.cancel()
        await asyncio.gather(*stopping, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                await self.watch()
                await self.start_deliveries()
            except Exception:  # one failed round must not stop the watching
                logger.exception("Delegation worker: the round failed; trying again next interval")
            await asyncio.sleep(self.interval)

    async def tick(self) -> None:
        """One round, waited for: claim, check or give up, then deliver and retry (R9, R10)."""
        await self.watch()
        await self.start_deliveries()
        await asyncio.gather(*self._deliveries.values())

    async def watch(self) -> None:
        """Claim this instance's share of pending tasks, and check or give up each (R9).

        The round takes one context before it claims and releases it when its checks are
        done. Without the current set it would give up the task of an agent another instance
        has just added, so a round that cannot take one claims nothing (005 research R3).
        """
        context = await self.runtime.begin_run()
        try:
            await self._watch(context)
        finally:
            await context.release()

    async def _watch(self, context: "RunContext") -> None:
        store = self.runtime.agent_tasks
        claimed = await store.claim_due(self.runtime.instance_id, CLAIM_INTERVALS * self.interval)
        now = await store.now()
        max_age = self.runtime.settings.a2a_task_max_age_seconds
        links = {link.name: link for link in context.links}
        giving_up = []
        to_check: dict[AgentLink, list[AgentTaskRecord]] = {}
        for task in claimed:
            link = links.get(task.agent)
            if link is None or task.agent_ref != link.ref:
                # The task belongs to the agent it was started with: one that left, one that
                # was moved, or one whose name another agent now has (005 FR-025). For an
                # environment agent both refs are null, which keeps the rule of feature 003.
                moved = (
                    link is not None
                    and link.agent_id is not None
                    and (task.agent_ref or "").startswith(f"{link.agent_id}:")
                )
                giving_up.append(self._give_up(task, GIVEN_UP_MOVED if moved else GIVEN_UP_REMOVED))
            elif (now - task.started_at).total_seconds() >= max_age:
                giving_up.append(self._give_up(task, GIVEN_UP_AGED))
            else:
                to_check.setdefault(link, []).append(task)
        await asyncio.gather(
            *giving_up, *(self._check_all(link, tasks) for link, tasks in to_check.items())
        )

    async def _check_all(self, link: AgentLink, tasks: list[AgentTaskRecord]) -> None:
        """Check one agent's claimed tasks, first reading its card if the link needs it.

        A link that is not connected is asked for its card once in the round. When that
        fails, none of its tasks is checked this round and they stay pending; one line says
        so for the agent, not one per task (005 FR-026, research R6).
        """
        if not link.connected:
            try:
                await link.connect(limit=link.call_timeout)
            except CardError as error:
                sentence = error.message
            except Exception:  # anything else is a card that could not be read
                sentence = SENTENCES["unreadable"]
            else:
                sentence = None
            if sentence is not None:
                logger.warning(
                    "Agent %s cannot be used: %s %d pending task(s) stay pending and are "
                    "checked once it answers",
                    link.name,
                    sentence,
                    len(tasks),
                )
                return
        await asyncio.gather(*(self._check(task, link) for task in tasks))

    async def start_deliveries(self) -> None:
        """Start a delivery for each thread with an outcome waiting or a retry due (R10).

        Nothing here waits for a background run. A thread this instance is still delivering to
        is left to that delivery; a later round finds whatever arrived for it meanwhile.
        """
        store = self.runtime.agent_tasks
        threads = set(await store.threads_with_ready_outcomes())
        threads |= set(await store.threads_with_due_followups())
        if not threads:
            return
        # While no usable model is active a background run could only fail, and each failure
        # would use up one of the outcome's attempts. So none starts: what is ready stays
        # ready, untouched, until a usable profile is active (004 FR-016a).
        if not (await self.runtime.model_for_run()).usable:
            if not self._held:
                logger.warning(
                    "Delegation worker: no usable model is active, so no background run "
                    "starts; %d thread(s) wait with an outcome to deliver",
                    len(threads),
                )
            self._held = True
            return
        if self._held:
            logger.info("Delegation worker: a usable model is active again; delivering")
            self._held = False
        for thread_id in sorted(threads - set(self._deliveries)):
            self._deliveries[thread_id] = asyncio.create_task(
                self._deliver_in_turn(thread_id), name=f"freyja-delivery-{thread_id}"
            )

    async def _deliver_in_turn(self, thread_id: str) -> None:
        """Deliver to one thread once a slot is free; a failure is logged, never raised."""
        try:
            async with self._slots:
                await self._deliver(thread_id)
        except Exception:
            logger.exception("Delegation worker: delivery on thread %s failed", thread_id)
        finally:
            self._deliveries.pop(thread_id, None)

    async def _give_up(self, task: AgentTaskRecord, template: str) -> None:
        """Stop watching a task: past the maximum age, or its agent is gone (FR-021, FR-023).

        No cancel is sent: the agent may still be working. ``template`` is the report for
        the thread, which says what the person can still do about it.
        """
        outcome = given_up_text(template, task.agent, task.task_id)
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
            logger.warning(
                "%s",
                self._describe(
                    f"Agent {task.agent}: gave up task {task.task_id} on thread "
                    f"{task.thread_id}: {_WHY[template]}; no cancel was sent"
                ),
            )

    async def _check(self, task: AgentTaskRecord, link: AgentLink | None = None) -> None:
        """Ask the agent about one pending task and record what changed (FR-019).

        ``link`` is the link of the round's context; given none, the one in use under the
        task's agent's name now.
        """
        if link is None:
            link = next(found for found in self.runtime.agents if found.name == task.agent)
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
        if task.state == "input_required" and state in PENDING_STATES:
            # Still waiting, or working on an answer whose hand-off is still following it: the
            # question is recorded already, and that hand-off records what the answer leads
            # to. Nothing is written, so the record keeps the time its question arrived, which
            # names the question's outcome in the thread (``outcome_id``). Only an ending the
            # agent decided on its own is news here.
            return
        if state == "working":
            now = await store.now()
            await store.update_task(
                task.agent, task.task_id, when_state=("working",), last_checked_at=now
            )
            return
        # Recorded only while the task is still pending here: one Freyja canceled meanwhile
        # keeps its state, and its late outcome is ignored (FR-022).
        await record_outcome(store, task, state, text, ready=True)

    @staticmethod
    def _describe(text: str) -> str:
        """Text about an agent that is safe to log: every known address removed (005 FR-028)."""
        return SCRUBBER.scrub(text)[:ERROR_TEXT_LIMIT]

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
        config = {"configurable": {"thread_id": thread_id}}
        # A thread paused on a proposal is left alone without its lease. Taking the lease to
        # find that out would refuse a resume arriving meanwhile as "run_active", every poll
        # interval for as long as the pause lasts. The check under the lease below still
        # decides: this one only keeps the worker off threads it cannot deliver to.
        looking = await runtime.begin_run()
        try:
            if (await looking.agent.aget_state(config)).interrupts:
                return
        finally:
            await looking.release()
        run_id = uuid.uuid4().hex
        owner = lease_owner(runtime, run_id)
        try:
            if not await runtime.locks.acquire(thread_id, owner):
                return  # a run is in progress; it or a later tick delivers
        except Exception:
            logger.exception("Delegation worker: no lease for thread %s", thread_id)
            return
        streaming = False
        # The context the run uses, taken under the lease: the thread's state is read on the
        # orchestrator the run then runs on (005 research R3).
        context = await take_context(runtime, thread_id, run_id)
        try:
            if context is None:
                return  # logged; the next round tries again
            snapshot = await context.agent.aget_state(config)
            if snapshot.interrupts:
                return  # paused since the check above: delivered once it is decided
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
                context=context,
                # A retry continues from the last checkpoint (input None), with any outcome the
                # failed attempt had not recorded yet.
                input={"messages": messages} if messages else None,
                mode="background",
                debug=False,
                run_id=run_id,
                on_end=ended,
                on_recorded=delivered if delivering or cut_off else None,
            )
            # From here the stream owns the lease and the context, and releases both however
            # the run ends.
            streaming = True
            async for _event in stream:
                pass
        except Exception:
            logger.exception("Delegation worker: delivery on thread %s failed", thread_id)
        finally:
            if not streaming:
                if context is not None:
                    await context.release()
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
