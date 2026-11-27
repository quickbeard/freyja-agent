"""The index of threads (feature 007, data-model.md): the store's contract against the
in-memory store, the lease wrapper that keeps it, and how a row becomes a status.

``tests/integration/test_thread_index_postgres.py`` runs the store's tests against the
Postgres store. ``index`` is the store under test.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from freyja_agent.runtime import thread_index
from freyja_agent.runtime.agent_tasks import AgentTaskRecord, InMemoryAgentTaskStore
from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.runtime import Runtime
from freyja_agent.runtime.thread_index import (
    ERROR_MESSAGE_LIMIT,
    FIRST_MESSAGE_LIMIT,
    LIST_LIMIT,
    IndexedRunLocks,
    InMemoryThreadIndex,
    Settled,
    ThreadIndex,
    ThreadRecord,
    open_thread,
    settle_thread,
    status_of,
    stoppable,
    stopping,
)
from tests.fakes import FailingChatModel, FakeClock, ScriptedChatModel

__all__ = [
    "test_a_late_write_for_an_earlier_run_changes_nothing",
    "test_a_note_is_kept_only_for_the_run_it_is_for",
    "test_a_note_is_kept_until_a_later_run_records_a_step",
    "test_a_row_is_made_when_a_run_starts_and_cleared_for_the_next_one",
    "test_a_row_is_opened_once_and_removed_only_while_no_run_started",
    "test_a_run_that_ends_gives_up_the_row_only_when_it_is_its_own",
    "test_a_settle_is_refused_once_a_run_has_started",
    "test_a_stop_is_marked_for_the_run_in_progress_only",
    "test_text_that_a_column_does_not_take_is_cleaned_before_it_is_kept",
    "test_the_first_message_is_kept_once_and_cut",
    "test_the_list_puts_paused_rows_first_and_stops_at_the_limit",
    "test_unsettled_rows_are_the_ones_nobody_looked_at",
]

A, B = "instance-a:run-1", "instance-a:run-2"
AT = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


@pytest.fixture
def index() -> ThreadIndex:
    return InMemoryThreadIndex()


async def settled(
    index: ThreadIndex,
    thread: str,
    how: Settled,
    *,
    at: datetime = AT,
    owner: str = A,
    agents_pending: bool = False,
) -> None:
    """A thread whose one run started, ended, and was looked at."""
    await index.run_started(thread, owner)
    await index.run_ended(thread, owner)
    assert await index.settle(
        thread,
        (await row(index, thread)).runs,
        settled=how,
        changed_at=at,
        first_message=f"message of {thread}",
        agents_pending=agents_pending,
    )


async def row(index: ThreadIndex, thread: str) -> ThreadRecord:
    found = await index.get(thread)
    assert found is not None
    return found


# --- the store ---------------------------------------------------------------------------


async def test_a_row_is_made_when_a_run_starts_and_cleared_for_the_next_one(
    index: ThreadIndex,
) -> None:
    assert await index.get("t-1") is None
    await index.run_started("t-1", A)
    first = await row(index, "t-1")
    assert (first.run_owner, first.run_unattended, first.settled) == (A, False, None)
    assert first.first_message is None
    assert first.created_at == first.changed_at

    assert await index.mark_unattended("t-1", A, "Do it.") is True
    assert await index.request_stop("t-1", A) is not None
    await index.run_ended("t-1", A)
    assert first.runs == 1
    assert await index.settle(
        "t-1", 1, settled="unfinished", changed_at=AT, first_message="x", agents_pending=True
    )

    await index.run_started("t-1", B)
    second = await row(index, "t-1")
    assert (second.run_owner, second.runs) == (B, 2)
    assert (second.stop_owner, second.settled, second.run_unattended) == (None, None, False)
    # What the thread is stays: when it was first seen and how it began.
    assert second.created_at == first.created_at
    assert second.first_message == "Do it."
    assert second.changed_at >= first.changed_at


async def test_the_first_message_is_kept_once_and_cut(index: ThreadIndex) -> None:
    await index.run_started("t-1", A)
    await index.mark_unattended("t-1", A, "x" * (FIRST_MESSAGE_LIMIT + 50))
    assert (await row(index, "t-1")).first_message == "x" * FIRST_MESSAGE_LIMIT
    assert (await row(index, "t-1")).run_unattended is True

    await index.mark_unattended("t-1", A, "another")
    assert (await row(index, "t-1")).first_message == "x" * FIRST_MESSAGE_LIMIT

    # A thread nobody ran is not made by the mark.
    assert await index.mark_unattended("never-ran", A, "hello") is False
    assert await index.get("never-ran") is None


async def test_a_follow_up_is_never_kept_as_the_first_message(index: ThreadIndex) -> None:
    # The thread's first run went the earlier way and was not looked at yet: the row does
    # not know how the thread began. A follow-up through the console is not how it began.
    await index.run_started("t-1", A)
    await index.run_ended("t-1", A)
    await index.run_started("t-1", B)
    assert await index.mark_unattended("t-1", B, "And another thing.") is True
    marked = await row(index, "t-1")
    assert (marked.first_message, marked.run_unattended) == (None, True)


async def test_a_late_mark_never_makes_a_later_run_a_console_run(index: ThreadIndex) -> None:
    await index.run_started("t-1", A)
    await index.run_ended("t-1", A)
    # The next run, another client's, holds the row when the first run's mark lands.
    await index.run_started("t-1", B)
    assert await index.mark_unattended("t-1", A, "Do it.") is False
    assert (await row(index, "t-1")).run_unattended is False
    assert await index.request_stop("t-1", B) is None


async def test_a_note_is_kept_only_for_the_run_it_is_for(index: ThreadIndex) -> None:
    await index.run_started("t-1", A)
    assert await index.note("t-1", B, "stopped") is False
    assert (await row(index, "t-1")).noted is None

    assert await index.note("t-1", A, "failed", "model_error", "y" * (ERROR_MESSAGE_LIMIT + 9))
    kept = await row(index, "t-1")
    assert (kept.noted, kept.error_code) == ("failed", "model_error")
    assert kept.error_message == "y" * ERROR_MESSAGE_LIMIT

    assert await index.note("t-1", A, "stopped", at=AT)
    kept = await row(index, "t-1")
    assert (kept.noted, kept.error_code, kept.error_message) == ("stopped", None, None)
    assert kept.noted_at == AT
    assert await index.note("no-such-thread", A, "stopped") is False


async def test_a_note_is_kept_until_a_later_run_records_a_step(index: ThreadIndex) -> None:
    await index.run_started("t-1", A)
    assert await index.note("t-1", A, "failed", "no_usable_model", "No usable model.", at=AT)
    await index.run_ended("t-1", A)
    # The run recorded nothing after the note: the thread's last step is older.
    earlier = AT - timedelta(minutes=5)
    assert await index.settle(
        "t-1", 1, settled="finished", changed_at=earlier, first_message="m", agents_pending=False
    )
    assert (await row(index, "t-1")).noted == "failed"

    # A request that took the lease and was refused is no run: the note stands.
    await index.run_started("t-1", B)
    await index.run_ended("t-1", B)
    assert await index.settle(
        "t-1", 2, settled="finished", changed_at=earlier, first_message="m", agents_pending=False
    )
    kept = await row(index, "t-1")
    assert (kept.noted, kept.error_code, kept.error_message) == (
        "failed",
        "no_usable_model",
        "No usable model.",
    )

    # A run that recorded a step since: what the earlier one left is gone.
    await index.run_started("t-1", A)
    await index.run_ended("t-1", A)
    later = AT + timedelta(minutes=5)
    assert await index.settle(
        "t-1", 3, settled="finished", changed_at=later, first_message="m", agents_pending=False
    )
    gone = await row(index, "t-1")
    assert (gone.noted, gone.error_code, gone.error_message, gone.noted_at) == (
        None,
        None,
        None,
        None,
    )


async def test_a_run_that_ends_gives_up_the_row_only_when_it_is_its_own(
    index: ThreadIndex,
) -> None:
    await index.run_started("t-1", A)
    await index.run_ended("t-1", B)
    assert (await row(index, "t-1")).run_owner == A
    await index.run_ended("t-1", A)
    assert (await row(index, "t-1")).run_owner is None

    # A run the index did not see start: the row was opened, and is looked at again.
    await index.open(
        "t-2",
        settled="finished",
        created_at=AT,
        changed_at=AT,
        first_message="hi",
        agents_pending=False,
    )
    await index.run_ended("t-2", A)
    assert (await row(index, "t-2")).settled is None
    await index.run_ended("no-such-thread", A)


async def test_a_settle_is_refused_once_a_run_has_started(index: ThreadIndex) -> None:
    def found(how: str = "finished", first: str = "m", pending: bool = False) -> dict[str, Any]:
        return {
            "settled": how,
            "changed_at": AT,
            "first_message": first,
            "agents_pending": pending,
        }

    await index.run_started("t-1", A)
    await index.run_ended("t-1", A)
    seen = (await row(index, "t-1")).runs
    # A run starts after the row was read: what the reading found is about an earlier state.
    await index.run_started("t-1", B)
    assert await index.settle("t-1", seen, **found()) is False
    assert (await row(index, "t-1")).settled is None
    # The same once that run has ended again: nothing would ever put a stale settle right.
    await index.run_ended("t-1", B)
    assert await index.settle("t-1", seen, **found()) is False
    assert await index.remove("t-1", seen) is False
    assert (await row(index, "t-1")).settled is None

    assert await index.settle("t-1", seen + 1, **found("paused")) is True
    assert (await row(index, "t-1")).settled == "paused"

    # A run that died with its instance: the row still names it, and keeps naming it. Whether
    # a run is in progress is the lease's to say, and its own late end must still find it.
    await index.run_started("t-1", A)
    dead = await row(index, "t-1")
    assert await index.settle("t-1", dead.runs, **found("unfinished", pending=True)) is True
    after = await row(index, "t-1")
    assert (after.run_owner, after.settled, after.agents_pending) == (A, "unfinished", True)
    assert (after.changed_at, after.first_message) == (AT, "m")
    await index.run_ended("t-1", A)
    assert (await row(index, "t-1")).settled == "unfinished"

    # What a settle read does not replace a first message that is already kept.
    assert await index.settle("t-1", dead.runs, **found("paused", first="other")) is True
    assert (await row(index, "t-1")).first_message == "m"


async def test_text_that_a_column_does_not_take_is_cleaned_before_it_is_kept(
    index: ThreadIndex,
) -> None:
    # The database refuses a NUL character in a text, and a lone surrogate cannot be
    # encoded. A person can send both in a message; the row must still be written.
    nul, surrogate = "a\x00b", "c\ud800d"
    await index.run_started("marked", A)
    await index.mark_unattended("marked", A, nul + surrogate)
    assert (await row(index, "marked")).first_message == "abc?d"
    assert await index.note("marked", A, "failed", "model_error", nul + surrogate, at=AT)
    assert (await row(index, "marked")).error_message == "abc?d"

    await index.run_started("settled", A)
    await index.run_ended("settled", A)
    assert await index.settle(
        "settled", 1, settled="finished", changed_at=AT, first_message=nul, agents_pending=False
    )
    assert (await row(index, "settled")).first_message == "ab"

    opened = await index.open(
        "opened",
        settled="finished",
        created_at=AT,
        changed_at=AT,
        first_message=surrogate,
        agents_pending=False,
    )
    assert opened.first_message == "c?d"


async def test_a_late_write_for_an_earlier_run_changes_nothing(index: ThreadIndex) -> None:
    await index.run_started("t-1", A)
    await index.run_ended("t-1", A)
    await index.run_started("t-1", B)
    before = await row(index, "t-1")

    assert await index.note("t-1", A, "failed", "internal", "late") is False
    await index.run_ended("t-1", A)
    assert await index.stop_requested("t-1", A) is False
    assert await index.remove("t-1", before.runs - 1) is False
    assert await row(index, "t-1") == before


async def test_a_row_is_opened_once_and_removed_only_while_no_run_started(
    index: ThreadIndex,
) -> None:
    opened = await index.open(
        "t-1",
        settled="paused",
        created_at=AT,
        changed_at=AT,
        first_message="z" * (FIRST_MESSAGE_LIMIT + 1),
        agents_pending=True,
    )
    assert (opened.settled, opened.agents_pending, opened.run_owner) == ("paused", True, None)
    assert opened.first_message == "z" * FIRST_MESSAGE_LIMIT
    assert (opened.created_at, opened.changed_at) == (AT, AT)

    again = await index.open(
        "t-1",
        settled=None,
        created_at=AT + timedelta(hours=1),
        changed_at=AT + timedelta(hours=1),
        first_message="other",
        agents_pending=False,
    )
    assert again == opened

    assert opened.runs == 0
    assert await index.remove("t-1", 1) is False
    assert await index.remove("t-1", 0) is True
    assert await index.get("t-1") is None
    assert await index.remove("t-1", 0) is False


async def test_a_stop_is_marked_for_the_run_in_progress_only(index: ThreadIndex) -> None:
    assert await index.request_stop("no-such-thread", A) is None
    await index.run_started("t-1", A)
    # Only a console run can be stopped.
    assert await index.request_stop("t-1", A) is None
    await index.mark_unattended("t-1", A, "Do it.")
    assert await index.stop_requested("t-1", A) is False

    marked = await index.request_stop("t-1", A)
    assert marked is not None and marked.stop_owner == A
    assert await index.stop_requested("t-1", A) is True
    assert await index.stop_requested("t-1", B) is False
    # Asked again, it is the same mark.
    assert (await index.request_stop("t-1", A)) == marked

    await index.run_ended("t-1", A)
    assert await index.request_stop("t-1", A) is None
    await index.run_started("t-1", B)
    await index.mark_unattended("t-1", B, None)
    # A stop asked for the run that ended does not land on the next one.
    assert await index.request_stop("t-1", A) is None
    assert await index.stop_requested("t-1", B) is False
    assert await index.stop_requested("t-1", A) is False


async def test_unsettled_rows_are_the_ones_nobody_looked_at(index: ThreadIndex) -> None:
    await settled(index, "looked-at", "finished")
    await index.run_started("running", A)
    await index.run_started("ended", B)
    await index.run_ended("ended", B)

    found = {record.thread_id: record.run_owner for record in await index.unsettled()}
    assert found == {"running": A, "ended": None}


async def test_the_list_puts_paused_rows_first_and_stops_at_the_limit(
    index: ThreadIndex,
) -> None:
    assert await index.list() == []
    assert await index.counts() == (0, 0)

    for number in range(6):
        await settled(index, f"done-{number}", "finished", at=AT + timedelta(minutes=number))
    await settled(index, "paused-old", "paused", at=AT - timedelta(days=2))
    await settled(index, "paused-new", "paused", at=AT - timedelta(days=1))

    listed = [record.thread_id for record in await index.list(limit=5)]
    assert listed == ["paused-new", "paused-old", "done-5", "done-4", "done-3"]
    assert await index.counts() == (2, 8)
    assert len(await index.list()) == 8
    assert LIST_LIMIT == 100

    # With as many paused rows as the limit, or more, those and no other.
    assert [r.thread_id for r in await index.list(limit=2)] == ["paused-new", "paused-old"]
    assert [r.thread_id for r in await index.list(limit=1)] == ["paused-new", "paused-old"]


# --- the lease wrapper -------------------------------------------------------------------


class FailingIndex(InMemoryThreadIndex):
    """An index whose writes fail, as when the database is away."""

    async def run_started(self, thread_id: str, owner: str) -> None:
        raise RuntimeError("the index is away")

    async def run_ended(self, thread_id: str, owner: str) -> None:
        raise RuntimeError("the index is away")


async def test_the_wrapper_passes_every_call_on_and_keeps_the_index() -> None:
    clock = FakeClock()
    inner = InMemoryRunLocks(60, clock=clock.now)
    index = InMemoryThreadIndex()
    locks = IndexedRunLocks(inner, index)

    assert await locks.acquire("t-1", A) is True
    assert await locks.is_active("t-1") is True
    assert (await row(index, "t-1")).run_owner == A

    # A lease that was not taken starts no run in the index.
    assert await locks.acquire("t-1", B) is False
    assert (await row(index, "t-1")).run_owner == A

    clock.advance(50)
    await locks.renew("t-1", A)
    clock.advance(50)
    assert await inner.is_active("t-1") is True

    await locks.release("t-1", A)
    assert await inner.is_active("t-1") is False
    assert (await row(index, "t-1")).run_owner is None

    # What the wrapper does not define is the inner object's.
    assert locks._rows is inner._rows
    # A wrapper around a wrapper wraps the locks themselves, once.
    assert IndexedRunLocks(locks, InMemoryThreadIndex()).inner is inner


async def test_an_index_that_fails_never_fails_the_lease(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="freyja_agent.runtime.thread_index")
    inner = InMemoryRunLocks(60)
    locks = IndexedRunLocks(inner, FailingIndex())

    assert await locks.acquire("t-1", A) is True
    assert await inner.is_active("t-1") is True
    await locks.release("t-1", A)
    assert await inner.is_active("t-1") is False

    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) == 2
    assert all("t-1" in line and "RuntimeError" in line for line in lines)


class SlowIndex(InMemoryThreadIndex):
    """An index whose writes never come back, as when its connection hangs."""

    def __init__(self) -> None:
        super().__init__()
        self.reached = asyncio.Event()

    async def run_started(self, thread_id: str, owner: str) -> None:
        self.reached.set()
        await asyncio.sleep(3600)

    async def run_ended(self, thread_id: str, owner: str) -> None:
        await asyncio.sleep(3600)


async def test_an_index_that_hangs_holds_the_lease_up_for_a_moment_and_no_longer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="freyja_agent.runtime.thread_index")
    monkeypatch.setattr(thread_index, "INDEX_WRITE_SECONDS", 0.05)
    inner = InMemoryRunLocks(60)
    locks = IndexedRunLocks(inner, SlowIndex())

    async with asyncio.timeout(2):
        assert await locks.acquire("t-1", A) is True
        assert await inner.is_active("t-1") is True
        await locks.release("t-1", A)
    assert await inner.is_active("t-1") is False
    assert [r.getMessage().count("TimeoutError") for r in caplog.records] == [1, 1]


async def test_a_lease_taken_by_a_caller_that_is_cancelled_is_given_back() -> None:
    inner = InMemoryRunLocks(60)
    index = SlowIndex()
    locks = IndexedRunLocks(inner, index)

    taking = asyncio.create_task(locks.acquire("t-1", A))
    await index.reached.wait()
    assert await inner.is_active("t-1") is True
    taking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await taking
    # The caller never learned that it held the lease, so nothing else would give it back.
    assert await inner.is_active("t-1") is False


# --- from a row to a status --------------------------------------------------------------


def a_row(**values: Any) -> ThreadRecord:
    return ThreadRecord(thread_id="t-1", first_message="m", created_at=AT, changed_at=AT, **values)


@pytest.mark.parametrize(
    ("record", "lease", "status"),
    [
        # One case per row of data-model.md "Status".
        (a_row(run_owner=A), True, "running"),
        (a_row(settled="paused"), False, "needs_decision"),
        (a_row(settled="finished", noted="failed", error_code="model_error"), False, "failed"),
        (a_row(settled="unfinished", noted="stopped"), False, "stopped"),
        (a_row(settled="unfinished"), False, "stopped"),
        (a_row(settled="finished", agents_pending=True), False, "waiting_for_agent"),
        (a_row(settled="finished"), False, "done"),
        # The lease alone says running, whatever the row holds.
        (a_row(settled="finished"), True, "running"),
        (a_row(settled="paused"), True, "running"),
        # A proposal over a noted failure; a noted failure over steps left to take.
        (a_row(settled="paused", noted="failed"), False, "needs_decision"),
        (a_row(settled="unfinished", noted="failed"), False, "failed"),
        # Stopped over a pending agent task.
        (a_row(settled="unfinished", agents_pending=True), False, "stopped"),
        (a_row(settled="finished", noted="stopped", agents_pending=True), False, "stopped"),
    ],
)
def test_a_row_has_exactly_one_status(record: ThreadRecord, lease: bool, status: str) -> None:
    assert status_of(record, lease) == status


def test_only_a_console_run_in_progress_can_be_stopped() -> None:
    console = a_row(run_owner=A, run_unattended=True)
    assert stoppable(console, True) is True
    assert stoppable(console, False) is False
    assert stoppable(a_row(run_owner=A), True) is False
    assert stoppable(a_row(run_unattended=True), True) is False

    assert stopping(console, True) is False
    assert stopping(a_row(run_owner=A, run_unattended=True, stop_owner=A), True) is True
    assert stopping(a_row(run_owner=A, run_unattended=True, stop_owner=B), True) is False
    assert stopping(a_row(run_owner=A, run_unattended=True, stop_owner=A), False) is False


NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)


@dataclass
class Reader:
    """What ``settle_thread`` needs of a runtime: a graph to read, and the thread's tasks."""

    runtime: Runtime
    tasks: InMemoryAgentTaskStore = field(default_factory=InMemoryAgentTaskStore)
    index: InMemoryThreadIndex = field(default_factory=InMemoryThreadIndex)

    def config(self, thread: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread}}

    async def run(self, thread: str, message: Any) -> None:
        await self.runtime.agent.ainvoke({"messages": [message]}, self.config(thread))

    async def settle(self, thread: str) -> ThreadRecord | None:
        context = await self.runtime.begin_read()
        seen = await self.index.get(thread)
        assert seen is not None
        return await settle_thread(self.index, context, self.tasks, thread, seen.runs)


async def test_settling_reads_the_thread_once_and_keeps_what_it_found(
    make_runtime: Callable[..., Runtime],
) -> None:
    reader = Reader(make_runtime(ScriptedChatModel(script=[NOTIFY], final_text="Done.")))
    index = reader.index

    # A thread whose run paused on a proposal.
    await index.run_started("paused", A)
    await reader.run("paused", HumanMessage("Tell #ops." + "!" * 300))
    await index.run_ended("paused", A)
    found = await reader.settle("paused")
    assert found is not None
    assert (found.settled, found.agents_pending) == ("paused", False)
    assert found.first_message == ("Tell #ops." + "!" * 300)[:FIRST_MESSAGE_LIMIT]
    state = await reader.runtime.agent.aget_state(reader.config("paused"))
    assert found.changed_at == datetime.fromisoformat(state.created_at)

    # One that finished, with an agent task still pending.
    await index.run_started("finished", A)
    await reader.run("finished", HumanMessage("Just answer."))
    await index.run_ended("finished", A)
    now = datetime.now(UTC)
    await reader.tasks.record_task(
        AgentTaskRecord(
            agent="stub",
            task_id="task-1",
            thread_id="finished",
            context_id="c-1",
            message="work",
            state="working",
            started_at=now,
            last_checked_at=now,
        )
    )
    found = await reader.settle("finished")
    assert found is not None
    assert (found.settled, found.agents_pending, found.first_message) == (
        "finished",
        True,
        "Just answer.",
    )
    # A task that waits for an answer is pending too; one that ended is not.
    for state, pending in (("input_required", True), ("completed", False)):
        await reader.tasks.update_task("stub", "task-1", state=state)
        found = await reader.settle("finished")
        assert found is not None and found.agents_pending is pending

    # One whose run left steps to take: the model failed at its first call.
    failing = Reader(make_runtime(FailingChatModel(fail_on_call=1)), index=index)
    await index.run_started("unfinished", A)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await failing.run("unfinished", HumanMessage("Start."))
    await index.run_ended("unfinished", A)
    found = await failing.settle("unfinished")
    assert found is not None and found.settled == "unfinished"

    # One that recorded nothing has no task to list.
    await index.run_started("nothing", A)
    await index.run_ended("nothing", A)
    assert await reader.settle("nothing") is None
    assert await index.get("nothing") is None


async def test_a_first_message_that_is_not_text_is_kept_empty(
    make_runtime: Callable[..., Runtime],
) -> None:
    reader = Reader(make_runtime(ScriptedChatModel()))
    await reader.index.run_started("t-1", A)
    await reader.run("t-1", HumanMessage([{"type": "text", "text": "in parts"}]))
    await reader.index.run_ended("t-1", A)
    found = await reader.settle("t-1")
    assert found is not None and found.first_message == ""


async def test_a_thread_the_index_does_not_know_is_opened_from_its_state(
    make_runtime: Callable[..., Runtime],
) -> None:
    reader = Reader(make_runtime(ScriptedChatModel()))
    await reader.run("old", HumanMessage("From before."))
    # A second run on it: how the thread began is its first message, not its latest.
    await reader.run("old", HumanMessage("And another thing."))
    context = await reader.runtime.begin_read()

    opened = await open_thread(reader.index, context, reader.tasks, "old", lease_active=False)
    assert opened is not None
    assert (opened.settled, opened.first_message, opened.run_owner) == (
        "finished",
        "From before.",
        None,
    )
    assert opened.created_at == opened.changed_at

    # Opened while a run holds its lease, it is not settled: the lease says running.
    await reader.run("busy", HumanMessage("Still going."))
    busy = await open_thread(reader.index, context, reader.tasks, "busy", lease_active=True)
    assert busy is not None and busy.settled is None

    assert (
        await open_thread(reader.index, context, reader.tasks, "none", lease_active=False) is None
    )
    assert await reader.index.get("none") is None


# Feature 009 (research R9): what follows a release.


async def test_the_lease_tells_who_asked_after_every_release_and_never_waits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner = InMemoryRunLocks(60)
    index = InMemoryThreadIndex()
    locks = IndexedRunLocks(inner, index)
    told: list[tuple[str, bool, str | None]] = []

    def released(thread_id: str) -> None:
        # Called once the lease is back and the index was told.
        told.append(
            (thread_id, inner._rows.get(thread_id) is None, index._rows[thread_id].run_owner)
        )

    locks.on_release = released
    assert await locks.acquire("t-1", A)
    assert not await locks.acquire("t-1", B)
    assert told == []
    await locks.release("t-1", A)
    assert told == [("t-1", True, None)]

    def broken(_thread_id: str) -> None:
        raise RuntimeError("could not start")

    locks.on_release = broken
    caplog.set_level(logging.ERROR, logger="freyja_agent.runtime.thread_index")
    assert await locks.acquire("t-2", A)
    await locks.release("t-2", A)
    assert await inner.is_active("t-2") is False
    assert "t-2" in caplog.text
