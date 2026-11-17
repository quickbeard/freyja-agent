"""The LLM profile store's contract (004 research R5, data-model.md), against the in-memory store.

``tests/integration/test_llm_profiles_postgres.py`` runs the same tests against the Postgres
store. The ``store`` fixture is the store under test and ``advance(seconds)`` moves its clock
forward: the fake clock here, real waiting there.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from freyja_agent.llm.store import (
    PROFILE_LIMIT,
    InMemoryLlmProfileStore,
    InvalidProfileError,
    LlmProfileRecord,
    LlmProfileStore,
    NameTakenError,
    ProfileActiveError,
    ProfileLimitError,
    ProfileNotFoundError,
)
from tests.fakes import FakeClock

Advance = Callable[[float], Awaitable[None]]

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
def store(clock: FakeClock) -> LlmProfileStore:
    return InMemoryLlmProfileStore(clock=clock.now)


@pytest.fixture
def advance(clock: FakeClock) -> Advance:
    async def _advance(seconds: float) -> None:
        clock.advance(seconds)

    return _advance


async def a_profile(store: LlmProfileStore, name: str = "Local", **values: Any) -> LlmProfileRecord:
    given: dict[str, Any] = {
        "provider": "openai",
        "model": "served-model",
        "base_url": "http://127.0.0.1:9997/v1",
        "api_key_encrypted": "token-1",
        "key_hint": "0001",
        "responses_api": False,
    }
    return await store.create_profile(name=name, **{**given, **values})


async def test_a_created_profile_is_listed_and_read_back(store: LlmProfileStore) -> None:
    assert await store.list_profiles() == []
    created = await a_profile(store)
    assert len(created.id) == 32
    assert int(created.id, 16) >= 0
    assert (created.name, created.provider, created.model) == ("Local", "openai", "served-model")
    assert created.base_url == "http://127.0.0.1:9997/v1"
    assert (created.api_key_encrypted, created.key_hint) == ("token-1", "0001")
    assert created.responses_api is False
    assert created.config_version == 1
    assert created.last_test_ok is None
    assert created.last_test_at is None
    assert await store.get_profile(created.id) == created
    assert await store.list_profiles() == [created]
    assert await store.get_profile("0" * 32) is None


async def test_profiles_are_listed_by_name(store: LlmProfileStore) -> None:
    for name in ("beta", "Alpha", "gamma"):
        await a_profile(store, name)
    assert [p.name for p in await store.list_profiles()] == ["Alpha", "beta", "gamma"]


async def test_names_are_unique_without_regard_to_case(store: LlmProfileStore) -> None:
    first = await a_profile(store, "Local")
    other = await a_profile(store, "Other")
    with pytest.raises(NameTakenError):
        await a_profile(store, "LOCAL")
    with pytest.raises(NameTakenError):
        await store.update_profile(other.id, name="local")
    # A profile may change the case of its own name, and a name is trimmed.
    renamed = await store.update_profile(first.id, name="  LOCAL ")
    assert renamed.name == "LOCAL"
    assert [p.name for p in await store.list_profiles()] == ["LOCAL", "Other"]


async def test_the_environments_name_is_reserved(store: LlmProfileStore) -> None:
    with pytest.raises(NameTakenError):
        await a_profile(store, "environment")
    profile = await a_profile(store)
    with pytest.raises(NameTakenError):
        await store.update_profile(profile.id, name="Environment")


async def test_the_fifty_first_profile_is_refused(store: LlmProfileStore) -> None:
    for number in range(PROFILE_LIMIT):
        await a_profile(store, f"profile {number}")
    with pytest.raises(ProfileLimitError):
        await a_profile(store, "one too many")
    assert len(await store.list_profiles()) == PROFILE_LIMIT == 50


@pytest.mark.parametrize(
    "values",
    [
        {"name": "  "},
        {"name": "n" * 65},
        {"provider": "someprovider"},
        {"model": " "},
        {"model": "m" * 201},
        {"base_url": "ftp://127.0.0.1/v1"},
        {"base_url": "not a web address"},
        {"base_url": "http://"},
        {"base_url": "http://host/" + "p" * 2000},
    ],
)
async def test_values_outside_the_rules_are_refused(
    store: LlmProfileStore, values: dict[str, Any]
) -> None:
    with pytest.raises(InvalidProfileError):
        await a_profile(store, **{"name": "Bad", **values})
    assert await store.list_profiles() == []
    profile = await a_profile(store, "Good")
    with pytest.raises(InvalidProfileError):
        await store.update_profile(profile.id, **values)
    assert await store.get_profile(profile.id) == profile


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "anthropic"},
        {"model": "another-model"},
        {"base_url": None},
        {"api_key_encrypted": "token-2", "key_hint": "0002"},
        {"responses_api": True},
    ],
)
async def test_a_change_to_how_the_model_is_reached_clears_the_test_result(
    store: LlmProfileStore, change: dict[str, Any]
) -> None:
    profile = await a_profile(store)
    assert await store.record_test(profile.id, 1, ok=False, step="conversation", reason="refused")
    changed = await store.update_profile(profile.id, **change)
    assert changed.config_version == 2
    assert changed.last_test_ok is None
    assert changed.last_test_at is None
    assert changed.last_test_step is None
    assert changed.last_test_reason is None
    for field, value in change.items():
        assert getattr(changed, field) == value


async def test_a_rename_keeps_the_test_result(store: LlmProfileStore) -> None:
    profile = await a_profile(store)
    assert await store.record_test(profile.id, 1, ok=True, step=None, reason=None)
    renamed = await store.update_profile(profile.id, name="Renamed")
    assert renamed.config_version == 1
    assert renamed.last_test_ok is True
    assert renamed.last_test_at is not None


async def test_a_value_sent_unchanged_is_not_a_change(store: LlmProfileStore) -> None:
    profile = await a_profile(store)
    assert await store.record_test(profile.id, 1, ok=True, step=None, reason=None)
    same = await store.update_profile(profile.id, model="served-model", responses_api=False)
    assert same.config_version == 1
    assert same.last_test_ok is True


async def test_a_test_result_is_recorded_only_for_the_values_it_tested(
    store: LlmProfileStore,
) -> None:
    profile = await a_profile(store)
    assert await store.record_test(
        profile.id, 1, ok=False, step="first_message", reason="could not connect"
    )
    tested = await store.get_profile(profile.id)
    assert tested is not None
    assert tested.last_test_ok is False
    assert tested.last_test_step == "first_message"
    assert tested.last_test_reason == "could not connect"
    assert tested.last_test_at is not None

    await store.update_profile(profile.id, model="another-model")
    # A result for the values the profile had before the change is dropped.
    assert not await store.record_test(profile.id, 1, ok=True, step=None, reason=None)
    changed = await store.get_profile(profile.id)
    assert changed is not None
    assert changed.last_test_ok is None
    assert await store.record_test(profile.id, 2, ok=True, step=None, reason=None)
    assert not await store.record_test("0" * 32, 1, ok=True, step=None, reason=None)


async def test_a_long_reason_is_cut_to_the_limit(store: LlmProfileStore) -> None:
    profile = await a_profile(store)
    await store.record_test(profile.id, 1, ok=False, step="conversation", reason="r" * 900)
    tested = await store.get_profile(profile.id)
    assert tested is not None
    assert tested.last_test_reason == "r" * 500


async def test_the_environment_is_active_until_a_profile_is_activated(
    store: LlmProfileStore,
) -> None:
    state = await store.state()
    assert state.active_profile_id is None
    assert state.revision == 0
    await a_profile(store)
    assert await store.state() == state


async def test_activating_a_profile_makes_it_the_one_active(store: LlmProfileStore) -> None:
    first = await a_profile(store, "First")
    second = await a_profile(store, "Second")

    state = await store.activate(first.id)
    assert (state.active_profile_id, state.revision) == (first.id, 1)
    assert await store.state() == state
    # Activating the active profile changes nothing.
    assert await store.activate(first.id) == state

    state = await store.activate(second.id)
    assert (state.active_profile_id, state.revision) == (second.id, 2)
    # None is the environment's profile.
    state = await store.activate(None)
    assert (state.active_profile_id, state.revision) == (None, 3)
    assert await store.activate(None) == state


async def test_activating_an_unknown_profile_reports_not_found(store: LlmProfileStore) -> None:
    profile = await a_profile(store)
    before = await store.activate(profile.id)
    with pytest.raises(ProfileNotFoundError):
        await store.activate("0" * 32)
    assert await store.state() == before


async def test_an_edit_of_the_active_profile_moves_the_revision(store: LlmProfileStore) -> None:
    active = await a_profile(store, "Active")
    other = await a_profile(store, "Other")
    await store.activate(active.id)

    await store.update_profile(other.id, model="another-model")
    assert (await store.state()).revision == 1
    await store.update_profile(active.id, model="another-model")
    assert (await store.state()).revision == 2
    await store.update_profile(active.id, name="Renamed")
    assert (await store.state()).revision == 3
    # An edit that changes nothing is not an edit.
    await store.update_profile(active.id, name="Renamed")
    assert (await store.state()).revision == 3
    # A test result is not an edit either.
    await store.record_test(active.id, 2, ok=True, step=None, reason=None)
    assert (await store.state()).revision == 3


async def test_the_active_profile_cannot_be_deleted(store: LlmProfileStore) -> None:
    active = await a_profile(store, "Active")
    other = await a_profile(store, "Other")
    await store.activate(active.id)
    with pytest.raises(ProfileActiveError):
        await store.delete_profile(active.id)
    assert await store.get_profile(active.id) is not None
    assert (await store.state()).active_profile_id == active.id

    await store.activate(other.id)
    await store.delete_profile(active.id)
    assert await store.get_profile(active.id) is None
    assert (await store.state()).active_profile_id == other.id


async def test_a_deleted_profile_is_gone(store: LlmProfileStore) -> None:
    profile = await a_profile(store)
    await store.delete_profile(profile.id)
    assert await store.get_profile(profile.id) is None
    assert await store.list_profiles() == []
    with pytest.raises(ProfileNotFoundError):
        await store.delete_profile(profile.id)
    with pytest.raises(ProfileNotFoundError):
        await store.update_profile(profile.id, name="Back")
    with pytest.raises(ProfileNotFoundError):
        await store.activate(profile.id)
    # Its name is free again.
    await a_profile(store, profile.name)


async def test_times_come_from_the_stores_own_clock(
    store: LlmProfileStore, advance: Advance
) -> None:
    profile = await a_profile(store)
    assert profile.created_at == profile.updated_at
    await advance(0.05)
    changed = await store.update_profile(profile.id, model="another-model")
    assert changed.created_at == profile.created_at
    assert changed.updated_at > profile.updated_at
    await advance(0.05)
    await store.record_test(profile.id, 2, ok=True, step=None, reason=None)
    tested = await store.get_profile(profile.id)
    assert tested is not None
    assert tested.last_test_at is not None
    assert tested.last_test_at > changed.updated_at
    # A test result does not count as a change to the profile.
    assert tested.updated_at == changed.updated_at
