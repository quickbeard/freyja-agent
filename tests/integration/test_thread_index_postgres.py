"""Feature 007, research R6: the thread index's contract against a real database.

The contract's tests are the ones in ``tests/test_thread_index.py``; only the fixture
differs. Times a test does not give come from the database here.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from freyja_agent.api.app import create_app
from freyja_agent.config import Settings
from freyja_agent.runtime.persistence import Pool, open_persistence
from freyja_agent.runtime.runtime import Runtime
from freyja_agent.runtime.thread_index import PostgresThreadIndex, ThreadIndex
from tests.fakes import LabelledChatModel, ScriptedChatModel
from tests.test_thread_index import (
    AT,
    A,
    B,
    row,
    test_a_follow_up_is_never_kept_as_the_first_message,
    test_a_late_mark_never_makes_a_later_run_a_console_run,
    test_a_late_write_for_an_earlier_run_changes_nothing,
    test_a_note_is_kept_only_for_the_run_it_is_for,
    test_a_note_is_kept_until_a_later_run_records_a_step,
    test_a_row_is_made_when_a_run_starts_and_cleared_for_the_next_one,
    test_a_row_is_opened_once_and_removed_only_while_no_run_started,
    test_a_run_that_ends_gives_up_the_row_only_when_it_is_its_own,
    test_a_settle_is_refused_once_a_run_has_started,
    test_a_stop_is_marked_for_the_run_in_progress_only,
    test_text_that_a_column_does_not_take_is_cleaned_before_it_is_kept,
    test_the_first_message_is_kept_once_and_cut,
    test_the_list_puts_paused_rows_first_and_stops_at_the_limit,
    test_unsettled_rows_are_the_ones_nobody_looked_at,
)
from tests.test_thread_index_nowhere import ls_call
from tests.unattended_helpers import Reader, idle

# The contract's tests, collected here against Postgres.
__all__ = [
    "test_a_follow_up_is_never_kept_as_the_first_message",
    "test_a_late_mark_never_makes_a_later_run_a_console_run",
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


@pytest.fixture
async def pool(settings: Settings) -> AsyncIterator[Pool]:
    persistence = await open_persistence(settings)
    try:
        yield persistence.pool
    finally:
        await persistence.close()


@pytest.fixture
async def index(pool: Pool) -> ThreadIndex:
    made = PostgresThreadIndex(pool)
    return made


async def test_two_instances_on_one_database_see_each_others_writes(
    pool: Pool, settings: Settings
) -> None:
    here = PostgresThreadIndex(pool)
    other = await open_persistence(settings)
    try:
        there = PostgresThreadIndex(other.pool)

        # A run on one instance; the other marks it to be stopped and reads the mark back.
        await here.run_started("t-1", A)
        await here.mark_unattended("t-1", A, "Do it.")
        marked = await there.request_stop("t-1", A)
        assert marked is not None and marked.stop_owner == A
        assert await here.stop_requested("t-1", A) is True

        # It ends on the first; the second settles it, and the first lists what it found.
        await here.run_ended("t-1", A)
        assert [record.thread_id for record in await there.unsettled()] == ["t-1"]
        seen = (await row(there, "t-1")).runs
        assert await there.settle(
            "t-1", seen, settled="paused", changed_at=AT, first_message=None, agents_pending=False
        )
        assert [(r.thread_id, r.settled) for r in await here.list()] == [("t-1", "paused")]
        assert await here.counts() == (1, 1)

        # A late write for the run that ended changes nothing of the next one: neither a
        # note for its owner, nor what was read of the thread before the next run started.
        await there.run_started("t-1", B)
        assert await here.note("t-1", A, "failed", "internal", "late") is False
        assert (
            await here.settle(
                "t-1",
                seen,
                settled="finished",
                changed_at=AT,
                first_message=None,
                agents_pending=False,
            )
            is False
        )
        after = await row(here, "t-1")
        assert (after.noted, after.settled, after.runs) == (None, None, seen + 1)
    finally:
        await other.close()


def test_a_console_run_on_one_instance_is_stopped_through_another(settings: Settings) -> None:
    """Phase 3, T025: the stop is a mark in the shared table, read between two steps."""
    model = LabelledChatModel(script=[ls_call("l1")], final_text="Listed.")

    def service(chat: Any, instance_id: str) -> Any:
        return create_app(
            open_runtime=lambda _s: Runtime.open(settings, model=chat, instance_id=instance_id)
        )

    with (
        TestClient(service(model, "instance-a")) as first,
        TestClient(service(ScriptedChatModel(), "instance-b")) as second,
    ):
        model.hold()
        reader = Reader(first, "/threads/pg-stop/runs/unattended", {"message": "List."})
        reader.start()
        with model.held():
            asked = second.post("/threads/pg-stop/stop")
            assert asked.status_code == 202, asked.text
            assert second.get("/threads/pg-stop").json()["stopping"] is True
        events = reader.finish()
        idle(first, "pg-stop")
        after = second.get("/threads/pg-stop").json()

    assert events[-1] == ("end", {"status": "stopped"})
    assert after["status"] == "stopped"
    assert model.calls == 1
