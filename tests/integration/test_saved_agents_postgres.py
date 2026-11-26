"""005 research R1, R2, R10: the saved-agent store's contract against a real database. The
column feature 005 added to a table that existed in deployments is in
``test_migrations_postgres.py``.

The contract's tests are the ones in ``tests/test_saved_agent_store.py``; only the fixtures
differ. Times come from the database here, so ``advance`` waits.
"""

import asyncio
from collections.abc import AsyncIterator

import psycopg
import pytest

from freyja_agent.config import Settings
from freyja_agent.runtime import saved_agents
from freyja_agent.runtime.agent_tasks import (
    AgentTaskStore,
    PostgresAgentTaskStore,
)
from freyja_agent.runtime.persistence import open_persistence
from freyja_agent.runtime.saved_agents import (
    AGENT_LIMIT,
    AddressTakenError,
    AgentLimitError,
    NameTakenError,
    PostgresSavedAgentStore,
    SavedAgentError,
    SavedAgentStore,
)
from tests.test_saved_agent_store import (
    CARD,
    OTHER_CARD,
    Advance,
    a_task,
    an_agent,
    state_of,
    test_a_change_from_a_version_that_is_no_longer_current_changes_nothing,
    test_a_change_of_the_approval_choice_moves_the_version_and_not_the_address,
    test_a_change_that_changes_nothing_moves_nothing,
    test_a_move_and_a_removal_give_up_the_agents_pending_tasks,
    test_a_move_replaces_the_address_and_the_card_details,
    test_a_move_to_an_address_another_agent_has_is_refused,
    test_a_refused_add_leaves_nothing_behind,
    test_a_removed_agent_is_gone_and_added_again_is_another_agent,
    test_an_added_agent_is_listed_and_read_back,
    test_card_details_are_kept_as_they_read_back,
    test_card_details_are_replaced_only_for_the_address_they_were_read_at,
    test_one_change_can_move_an_agent_and_change_its_approval_choice,
    test_pending_tasks_are_counted_by_saved_agent,
    test_the_revision_moves_once_for_every_change,
    test_the_twenty_first_agent_is_refused,
    test_two_agents_cannot_share_a_name_or_an_address,
    test_values_outside_the_rules_are_refused_without_repeating_them,
)

# The contract's tests, collected here against Postgres.
__all__ = [
    "test_a_change_from_a_version_that_is_no_longer_current_changes_nothing",
    "test_a_change_of_the_approval_choice_moves_the_version_and_not_the_address",
    "test_a_change_that_changes_nothing_moves_nothing",
    "test_a_move_and_a_removal_give_up_the_agents_pending_tasks",
    "test_a_move_replaces_the_address_and_the_card_details",
    "test_a_move_to_an_address_another_agent_has_is_refused",
    "test_a_refused_add_leaves_nothing_behind",
    "test_a_removed_agent_is_gone_and_added_again_is_another_agent",
    "test_an_added_agent_is_listed_and_read_back",
    "test_card_details_are_kept_as_they_read_back",
    "test_card_details_are_replaced_only_for_the_address_they_were_read_at",
    "test_one_change_can_move_an_agent_and_change_its_approval_choice",
    "test_pending_tasks_are_counted_by_saved_agent",
    "test_the_revision_moves_once_for_every_change",
    "test_the_twenty_first_agent_is_refused",
    "test_two_agents_cannot_share_a_name_or_an_address",
    "test_values_outside_the_rules_are_refused_without_repeating_them",
]


@pytest.fixture
async def pools(settings: Settings) -> AsyncIterator[list]:
    """Two pools on one database: two instances of the service."""
    opened = [await open_persistence(settings), await open_persistence(settings)]
    try:
        yield [persistence.pool for persistence in opened]
    finally:
        for persistence in opened:
            await persistence.close()


@pytest.fixture
async def tasks(pools: list) -> AgentTaskStore:
    store = PostgresAgentTaskStore(pools[0])
    return store


@pytest.fixture
async def store(pools: list, tasks: AgentTaskStore) -> SavedAgentStore:
    store = PostgresSavedAgentStore(pools[0])
    return store


@pytest.fixture
async def other_store(pools: list, store: SavedAgentStore) -> SavedAgentStore:
    """A second instance's store: its own pool on the same database."""
    return PostgresSavedAgentStore(pools[1])


@pytest.fixture
def advance() -> Advance:
    async def _advance(seconds: float) -> None:
        await asyncio.sleep(seconds)

    return _advance


async def test_the_two_tables_are_created_and_the_state_is_one_row(
    store: SavedAgentStore, settings: Settings
) -> None:
    async with await psycopg.AsyncConnection.connect(
        settings.database_uri, autocommit=True
    ) as conn:
        cursor = await conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            " AND tablename LIKE 'freyja_agents%'"
        )
        assert {row[0] for row in await cursor.fetchall()} == {
            "freyja_agents",
            "freyja_agents_state",
        }
        cursor = await conn.execute("SELECT count(*), max(revision) FROM freyja_agents_state")
        assert (await cursor.fetchone()) == (1, 0)
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute("INSERT INTO freyja_agents_state (id) VALUES (2)")


async def test_a_second_instance_sees_the_same_agents_and_revision(
    store: SavedAgentStore, other_store: SavedAgentStore
) -> None:
    agent = await an_agent(store)
    assert await other_store.list_agents() == [agent]
    assert await other_store.revision() == 1
    changed, _ = await other_store.change(agent.id, 1, read_only=True)
    assert await store.get_agent(agent.id) == changed
    assert await store.revision() == 2


async def test_two_adds_together_at_the_limit_leave_exactly_the_limit(
    store: SavedAgentStore, other_store: SavedAgentStore
) -> None:
    for number in range(AGENT_LIMIT - 1):
        await an_agent(store, f"agent-{number}", f"http://127.0.0.1:{9000 + number}")
    results = await asyncio.gather(
        an_agent(store, "first-of-two", "http://127.0.0.1:8001"),
        an_agent(other_store, "second-of-two", "http://127.0.0.1:8002"),
        return_exceptions=True,
    )
    refused = [result for result in results if isinstance(result, Exception)]
    assert len(refused) == 1
    assert isinstance(refused[0], AgentLimitError)
    assert len(await store.list_agents()) == AGENT_LIMIT
    assert await store.revision() == AGENT_LIMIT


@pytest.mark.parametrize(
    ("first", "second", "error"),
    [
        (("same", "http://127.0.0.1:8001"), ("same", "http://127.0.0.1:8002"), NameTakenError),
        (("one", "http://127.0.0.1:8001"), ("two", "http://localhost:8001"), AddressTakenError),
    ],
    ids=["one-name", "one-destination"],
)
async def test_two_adds_that_conflict_together_leave_one(
    store: SavedAgentStore,
    other_store: SavedAgentStore,
    first: tuple[str, str],
    second: tuple[str, str],
    error: type[SavedAgentError],
) -> None:
    for _attempt in range(10):
        results = await asyncio.gather(
            an_agent(store, *first), an_agent(other_store, *second), return_exceptions=True
        )
        refused = [result for result in results if isinstance(result, Exception)]
        assert len(refused) == 1
        assert type(refused[0]) is error
        (saved,) = await store.list_agents()
        await store.remove(saved.id, saved.version)


@pytest.mark.parametrize("change", ["move", "remove"])
async def test_a_change_whose_last_statement_fails_leaves_the_agent_and_its_tasks_as_they_were(
    store: SavedAgentStore,
    tasks: AgentTaskStore,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """FR-024: the change and the giving up happen together or not at all."""
    agent = await an_agent(store)
    await a_task(tasks, "pending", agent.ref)
    revision = await store.revision()
    monkeypatch.setattr(saved_agents, "BUMP_REVISION", "SELECT 1 / 0")
    with pytest.raises(psycopg.errors.DivisionByZero):
        if change == "move":
            await store.change(agent.id, 1, url="http://127.0.0.1:9997", card=OTHER_CARD)
        else:
            await store.remove(agent.id, 1)
    monkeypatch.undo()
    assert await store.get_agent(agent.id) == agent
    assert await state_of(tasks, "pending") == "working"
    assert await store.revision() == revision
    # And the same change goes through once nothing fails.
    if change == "move":
        _, given_up = await store.change(agent.id, 1, url="http://127.0.0.1:9997", card=CARD)
    else:
        given_up = await store.remove(agent.id, 1)
    assert given_up == 1
    assert await state_of(tasks, "pending") == "given_up"
