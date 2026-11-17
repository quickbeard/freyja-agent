"""004 research R5: the LLM profile store's contract against a real database.

The tests are the ones in ``tests/test_llm_profile_store.py``; only the fixtures differ. Times
come from the database here, so ``advance`` waits.
"""

import asyncio
from collections.abc import AsyncIterator

import psycopg
import pytest

from freyja_agent.config import Settings
from freyja_agent.llm.store import (
    PROFILE_LIMIT,
    LlmProfileError,
    LlmProfileStore,
    PostgresLlmProfileStore,
    ProfileLimitError,
)
from freyja_agent.runtime.persistence import open_persistence
from tests.test_llm_profile_store import (
    Advance,
    a_profile,
    test_a_change_to_how_the_model_is_reached_clears_the_test_result,
    test_a_created_profile_is_listed_and_read_back,
    test_a_deleted_profile_is_gone,
    test_a_long_reason_is_cut_to_the_limit,
    test_a_rename_keeps_the_test_result,
    test_a_test_result_is_recorded_only_for_the_values_it_tested,
    test_a_value_sent_unchanged_is_not_a_change,
    test_activating_a_profile_makes_it_the_one_active,
    test_activating_an_unknown_profile_reports_not_found,
    test_an_edit_of_the_active_profile_moves_the_revision,
    test_names_are_unique_without_regard_to_case,
    test_profiles_are_listed_by_name,
    test_the_active_profile_cannot_be_deleted,
    test_the_environment_is_active_until_a_profile_is_activated,
    test_the_environments_name_is_reserved,
    test_the_fifty_first_profile_is_refused,
    test_times_come_from_the_stores_own_clock,
    test_values_outside_the_rules_are_refused,
)

# The contract's tests, collected here against Postgres.
__all__ = [
    "test_a_change_to_how_the_model_is_reached_clears_the_test_result",
    "test_a_created_profile_is_listed_and_read_back",
    "test_a_deleted_profile_is_gone",
    "test_a_long_reason_is_cut_to_the_limit",
    "test_a_rename_keeps_the_test_result",
    "test_a_test_result_is_recorded_only_for_the_values_it_tested",
    "test_a_value_sent_unchanged_is_not_a_change",
    "test_activating_a_profile_makes_it_the_one_active",
    "test_activating_an_unknown_profile_reports_not_found",
    "test_an_edit_of_the_active_profile_moves_the_revision",
    "test_names_are_unique_without_regard_to_case",
    "test_profiles_are_listed_by_name",
    "test_the_active_profile_cannot_be_deleted",
    "test_the_environment_is_active_until_a_profile_is_activated",
    "test_the_environments_name_is_reserved",
    "test_the_fifty_first_profile_is_refused",
    "test_times_come_from_the_stores_own_clock",
    "test_values_outside_the_rules_are_refused",
]


@pytest.fixture
async def store(settings: Settings) -> AsyncIterator[LlmProfileStore]:
    persistence = await open_persistence(settings)
    try:
        store = PostgresLlmProfileStore(persistence.pool)
        await store.setup()
        yield store
    finally:
        await persistence.close()


@pytest.fixture
def advance() -> Advance:
    async def _advance(seconds: float) -> None:
        await asyncio.sleep(seconds)

    return _advance


# T017: the tables themselves, and requests that arrive together.


@pytest.fixture
async def other_store(settings: Settings) -> AsyncIterator[LlmProfileStore]:
    """A second instance's store: its own pool on the same database."""
    persistence = await open_persistence(settings)
    try:
        yield PostgresLlmProfileStore(persistence.pool)
    finally:
        await persistence.close()


async def test_setup_twice_changes_nothing(store: LlmProfileStore) -> None:
    profile = await a_profile(store)
    await store.activate(profile.id)
    before = await store.state()
    await store.setup()
    assert await store.state() == before
    assert await store.list_profiles() == [profile]


async def test_the_state_is_one_row_and_refuses_a_second(
    store: LlmProfileStore, settings: Settings
) -> None:
    async with await psycopg.AsyncConnection.connect(
        settings.database_uri, autocommit=True
    ) as conn:
        cursor = await conn.execute("SELECT count(*) FROM freyja_llm_state")
        assert (await cursor.fetchone()) == (1,)
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute("INSERT INTO freyja_llm_state (id) VALUES (2)")


async def test_a_second_instance_sees_the_same_profiles_and_state(
    store: LlmProfileStore, other_store: LlmProfileStore
) -> None:
    profile = await a_profile(store)
    state = await store.activate(profile.id)
    assert await other_store.list_profiles() == [profile]
    assert await other_store.state() == state
    await other_store.update_profile(profile.id, model="another-model")
    assert (await store.state()).revision == state.revision + 1


async def test_an_activation_and_a_deletion_together_leave_one_active_profile_that_exists(
    store: LlmProfileStore, other_store: LlmProfileStore
) -> None:
    """SC-009: whichever wins, the active profile is one that exists."""
    for attempt in range(20):
        profile = await a_profile(store, f"profile {attempt}")
        results = await asyncio.gather(
            store.activate(profile.id),
            other_store.delete_profile(profile.id),
            return_exceptions=True,
        )
        unexpected = [
            r for r in results if isinstance(r, Exception) and not isinstance(r, LlmProfileError)
        ]
        assert not unexpected, unexpected
        state = await store.state()
        if state.active_profile_id is None:
            # The deletion won: the profile is gone and the earlier choice stands.
            assert await store.get_profile(profile.id) is None
        else:
            # The activation won: the profile is active and still exists.
            assert state.active_profile_id == profile.id
            assert await store.get_profile(profile.id) is not None
            await store.activate(None)
            await store.delete_profile(profile.id)


async def test_two_adds_together_at_the_limit_leave_exactly_the_limit(
    store: LlmProfileStore, other_store: LlmProfileStore
) -> None:
    for number in range(PROFILE_LIMIT - 1):
        await a_profile(store, f"profile {number}")
    results = await asyncio.gather(
        a_profile(store, "first of two"),
        a_profile(other_store, "second of two"),
        return_exceptions=True,
    )
    refused = [r for r in results if isinstance(r, Exception)]
    assert len(refused) == 1
    assert isinstance(refused[0], ProfileLimitError)
    assert len(await store.list_profiles()) == PROFILE_LIMIT
