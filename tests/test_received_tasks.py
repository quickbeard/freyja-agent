"""009: the store of conversations and received tasks, and its rules (data-model.md).

Each case is a function of a store, so that ``tests/integration/test_received_tasks_postgres.py``
runs the same cases on Postgres. Here they run on ``InMemoryReceivedTaskStore``.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from freyja_agent.runtime.received_tasks import (
    InMemoryReceivedTaskStore,
    ReceivedTaskStore,
)

HEX32 = re.compile(r"[0-9a-f]{32}")
LIMITS = {"caller_limit": 5, "total_limit": 20}
Case = Callable[[ReceivedTaskStore], Awaitable[None]]


async def accept(store: ReceivedTaskStore, caller: str = "local", context: str = "c-1", **kw):
    return await store.accept(caller, context, hops=kw.pop("hops", 0), **{**LIMITS, **kw})


async def started(store: ReceivedTaskStore, task_id: str) -> None:
    assert await store.mark_started(task_id, datetime.now(UTC), None)


async def a_new_conversation_gets_a_thread_and_a_working_task(store: ReceivedTaskStore) -> None:
    accepted = await accept(store, hops=2)
    task = accepted.task
    assert accepted.new_conversation and accepted.refused is None
    assert HEX32.fullmatch(task.task_id) and HEX32.fullmatch(task.thread_id)
    assert task.task_id != task.thread_id
    assert (task.caller, task.context_id, task.hops, task.state) == ("local", "c-1", 2, "working")
    assert task.started_at is None and task.ended_at is None and task.released_at is None
    assert task.reject_attempts == 0 and task.reject_after is None
    conversation = await store.conversation_of(task.thread_id)
    assert (conversation.caller, conversation.context_id) == ("local", "c-1")
    assert (await accept(store, context="c-2")).task.hops == 0


async def two_callers_with_one_conversation_id_have_two_threads(store: ReceivedTaskStore) -> None:
    mine = await accept(store, "alpha", "shared")
    theirs = await accept(store, "beta", "shared")
    assert mine.task.thread_id != theirs.task.thread_id
    assert (await store.conversation_of(theirs.task.thread_id)).caller == "beta"


async def a_busy_conversation_takes_no_message(store: ReceivedTaskStore) -> None:
    first = await accept(store)
    refused = await accept(store)
    assert (refused.task, refused.refused) == (None, "busy")
    assert await store.end(first.task.task_id, "completed", result="Hi.", released=True)
    again = await accept(store)
    assert again.task.thread_id == first.task.thread_id and not again.new_conversation
    assert (await store.last_task("local", "c-1")).task_id == again.task.task_id


async def the_two_limits_count_what_is_not_released(store: ReceivedTaskStore) -> None:
    limits = {"caller_limit": 2, "total_limit": 3}
    one = await accept(store, "alpha", "a", **limits)
    await accept(store, "alpha", "b", **limits)
    assert (await accept(store, "alpha", "c", **limits)).refused == "caller_limit"
    await accept(store, "beta", "a", **limits)
    assert (await accept(store, "beta", "b", **limits)).refused == "total_limit"
    # A canceled task counts until it is released; a released one does not.
    assert await store.end(one.task.task_id, "canceled", reason="canceled")
    assert (await accept(store, "beta", "b", **limits)).refused == "total_limit"
    assert await store.release(one.task.task_id)
    assert not await store.release(one.task.task_id)
    assert (await accept(store, "beta", "b", **limits)).task is not None
    # A refusal writes nothing: no conversation is made for it.
    assert await store.callers_of(["nothing"]) == {}


async def two_messages_at_once_make_one_thread(store: ReceivedTaskStore) -> None:
    both = await asyncio.gather(accept(store, context="new"), accept(store, context="new"))
    assert sorted(str(found.refused) for found in both) == ["None", "busy"]
    once = await asyncio.gather(
        *(accept(store, "alpha", f"x{n}", caller_limit=1) for n in range(3))
    )
    assert [found.refused for found in once].count(None) == 1


async def a_start_is_written_once(store: ReceivedTaskStore) -> None:
    task = (await accept(store)).task
    at = datetime.now(UTC)
    assert await store.mark_started(task.task_id, at, "checkpoint-1")
    assert not await store.mark_started(task.task_id, at + timedelta(seconds=1), "other")
    found = await store.get(task.task_id)
    assert (found.started_at, found.base_checkpoint) == (at, "checkpoint-1")


async def an_end_is_written_once_and_never_changes(store: ReceivedTaskStore) -> None:
    task = (await accept(store)).task
    assert await store.end(task.task_id, "completed", result="It\x00 works.", released=True)
    assert not await store.end(task.task_id, "failed", reason="internal")
    ended = await store.get(task.task_id)
    assert (ended.state, ended.result, ended.reason) == ("completed", "It works.", None)
    assert ended.ended_at is not None and ended.released_at is not None
    # Only the two columns of rejections by rule still change.
    assert await store.count_rejection(task.task_id, 0, ended.ended_at + timedelta(seconds=30))
    counted = await store.get(task.task_id)
    assert counted.reject_attempts == 1 and counted.reject_after is not None
    assert counted.state == "completed"


async def reasons_and_results_follow_the_fixed_rules(store: ReceivedTaskStore) -> None:
    task = (await accept(store)).task
    for state, reason, result in [
        ("working", None, None),
        ("failed", "it broke", None),
        ("failed", None, None),
        ("canceled", "canceled", "a result"),
        ("completed", "internal", None),
    ]:
        with pytest.raises(ValueError):
            await store.end(task.task_id, state, reason=reason, result=result)
    for reason in ("no_usable_model", "model_error", "step_limit", "interrupted", "internal"):
        other = (await accept(store, context=reason)).task
        assert await store.end(other.task_id, "failed", reason=reason, released=True)
    assert await store.end(task.task_id, "canceled", reason="stopped")


async def a_refused_run_removes_its_task_and_keeps_the_conversation(
    store: ReceivedTaskStore,
) -> None:
    task = (await accept(store)).task
    assert await store.remove(task.task_id)
    assert await store.get(task.task_id) is None
    assert (await store.conversation_of(task.thread_id)).context_id == "c-1"
    kept = (await accept(store)).task
    await started(store, kept.task_id)
    assert not await store.remove(kept.task_id)


async def who_started_a_thread_is_one_read(store: ReceivedTaskStore) -> None:
    mine = (await accept(store, "alpha", "a")).task
    other = (await accept(store, "beta", "b")).task
    assert await store.conversation_of("not-a-callers-thread") is None
    assert await store.end(other.task_id, "canceled", reason="canceled")
    marks = await store.callers_of([mine.thread_id, other.thread_id, "console-thread"])
    assert set(marks) == {mine.thread_id, other.thread_id}
    assert (marks[mine.thread_id].name, marks[mine.thread_id].canceled) == ("alpha", False)
    assert (marks[other.thread_id].name, marks[other.thread_id].canceled) == ("beta", True)
    assert (await store.last_task_of_thread(mine.thread_id)).task_id == mine.task_id


async def a_task_is_read_by_its_own_caller_only(store: ReceivedTaskStore) -> None:
    task = (await accept(store, "alpha")).task
    assert await store.get(task.task_id, "alpha") == task
    assert await store.get(task.task_id, "beta") is None
    assert await store.get("0" * 32) is None


async def the_rows_that_count_are_listed_up_to_a_number(store: ReceivedTaskStore) -> None:
    tasks = [(await accept(store, context=f"c{n}")).task for n in range(3)]
    assert await store.end(tasks[0].task_id, "completed", released=True)
    listed = await store.unreleased(1)
    assert [found.task_id for found in listed] == [tasks[1].task_id]
    assert len(await store.unreleased(10)) == 2


async def a_context_id_is_kept_without_a_nul(store: ReceivedTaskStore) -> None:
    task = (await accept(store, context="a\x00b")).task
    assert task.context_id == "ab"


async def a_rejection_to_try_again_is_listed_until_it_is_done_or_spent(
    store: ReceivedTaskStore,
) -> None:
    """Request 4 (research R11): the sweep's second list, the tasks that ended and whose
    proposal is to be rejected by rule again."""
    tasks = [(await accept(store, context=f"c{n}")).task for n in range(4)]
    base = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    for task in tasks:
        assert await store.end(task.task_id, "canceled", reason="canceled")
    working = (await accept(store, context="c9")).task
    await store.count_rejection(working.task_id, 0, base)
    assert await store.to_reject(5, 10) == []
    for minutes, task in zip((3, 1, 2), tasks[:3], strict=True):
        await store.count_rejection(task.task_id, 0, base + timedelta(minutes=minutes))
    for done in range(1, 5):
        await store.count_rejection(tasks[2].task_id, done, base + timedelta(minutes=2))
    listed = await store.to_reject(5, 10)
    # The earliest first; one tried five times is spent, one never tried is not listed.
    assert [found.task_id for found in listed] == [tasks[1].task_id, tasks[0].task_id]
    assert [found.task_id for found in await store.to_reject(5, 1)] == [tasks[1].task_id]
    await store.rejection_done(tasks[1].task_id)
    done = await store.get(tasks[1].task_id)
    assert (done.reject_after, done.reject_attempts) == (None, 1)
    assert [found.task_id for found in await store.to_reject(5, 10)] == [tasks[0].task_id]


async def an_attempt_is_counted_once_for_the_count_it_was_read_at(
    store: ReceivedTaskStore,
) -> None:
    """Two instances that read one row try one attempt between them (review of request 4)."""
    task = (await accept(store)).task
    assert await store.end(task.task_id, "canceled", reason="canceled")
    next_at = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    assert await store.count_rejection(task.task_id, 0, next_at) is True
    assert await store.count_rejection(task.task_id, 0, next_at) is False
    assert await store.count_rejection(task.task_id, 1, next_at) is True
    assert (await store.get(task.task_id)).reject_attempts == 2


async def the_last_task_that_started_is_the_one_a_proposal_can_be_of(
    store: ReceivedTaskStore,
) -> None:
    first = (await accept(store)).task
    await started(store, first.task_id)
    assert await store.end(first.task_id, "canceled", reason="canceled")
    second = (await accept(store)).task
    assert (await store.last_task_of_thread(first.thread_id)).task_id == second.task_id
    assert (await store.last_started_task_of_thread(first.thread_id)).task_id == first.task_id
    await started(store, second.task_id)
    assert (await store.last_started_task_of_thread(first.thread_id)).task_id == second.task_id
    assert await store.last_started_task_of_thread("no-such-thread") is None


async def the_working_tasks_of_callers_no_longer_named_are_listed(
    store: ReceivedTaskStore,
) -> None:
    """Request 5 (FR-021): what the sweep's first round cancels."""
    alpha = (await accept(store, "alpha", "a")).task
    beta = (await accept(store, "beta", "b")).task
    local = (await accept(store, "local", "l")).task
    gone = (await accept(store, "gamma", "g")).task
    ended = (await accept(store, "gamma", "h")).task
    assert await store.end(ended.task_id, "completed", result="Hi.", released=True)
    found = await store.working_of_others({"local", "alpha", "beta"}, 10)
    assert [task.task_id for task in found] == [gone.task_id]
    assert {task.task_id for task in await store.working_of_others({"local"}, 10)} == {
        alpha.task_id,
        beta.task_id,
        gone.task_id,
    }
    assert len(await store.working_of_others({"local"}, 2)) == 2
    assert local.caller == "local"


CASES: list[Case] = [
    a_new_conversation_gets_a_thread_and_a_working_task,
    two_callers_with_one_conversation_id_have_two_threads,
    a_busy_conversation_takes_no_message,
    the_two_limits_count_what_is_not_released,
    two_messages_at_once_make_one_thread,
    a_start_is_written_once,
    an_end_is_written_once_and_never_changes,
    reasons_and_results_follow_the_fixed_rules,
    a_refused_run_removes_its_task_and_keeps_the_conversation,
    who_started_a_thread_is_one_read,
    a_task_is_read_by_its_own_caller_only,
    the_rows_that_count_are_listed_up_to_a_number,
    a_context_id_is_kept_without_a_nul,
    a_rejection_to_try_again_is_listed_until_it_is_done_or_spent,
    an_attempt_is_counted_once_for_the_count_it_was_read_at,
    the_last_task_that_started_is_the_one_a_proposal_can_be_of,
    the_working_tasks_of_callers_no_longer_named_are_listed,
]


@pytest.mark.parametrize("case", CASES, ids=[case.__name__ for case in CASES])
async def test_the_in_memory_store(case: Case) -> None:
    await case(InMemoryReceivedTaskStore())
