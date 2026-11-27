"""The saved-agent store's contract (005 research R1, R2, R10, R14), against the in-memory
store.

``tests/integration/test_saved_agents_postgres.py`` runs the same tests against the Postgres
store. ``store`` is the store under test, ``tasks`` the task store beside it, and
``advance(seconds)`` moves their clock forward: the fake clock here, real waiting there.
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, cast

import pytest

from freyja_agent.runtime.agent_tasks import (
    GIVEN_UP_MOVED,
    GIVEN_UP_REMOVED,
    AgentTaskRecord,
    AgentTaskStore,
    InMemoryAgentTaskStore,
    given_up_text,
)
from freyja_agent.runtime.saved_agents import (
    AGENT_LIMIT,
    AddressTakenError,
    AgentChangedError,
    AgentLimitError,
    AgentNotFoundError,
    InMemorySavedAgentStore,
    InvalidAgentError,
    NameTakenError,
    SavedAgentError,
    SavedAgentRecord,
    SavedAgentStore,
)
from tests.fakes import FakeClock

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

Advance = Callable[[float], Awaitable[None]]

CARD: dict[str, Any] = {
    "name": "Stub Agent stub",
    "description": "A stand-in agent for Freyja's tests.",
    "version": "1.0.0",
    "skills": [{"id": "echo", "name": "Echo", "description": "Answers.", "tags": ["test"]}],
    "cut": False,
}
OTHER_CARD: dict[str, Any] = {**CARD, "name": "Another Agent", "skills": []}


@pytest.fixture
def tasks(clock: FakeClock) -> AgentTaskStore:
    return InMemoryAgentTaskStore(clock=clock.now)


@pytest.fixture
def store(tasks: InMemoryAgentTaskStore, clock: FakeClock) -> SavedAgentStore:
    return InMemorySavedAgentStore(tasks, clock=clock.now)


@pytest.fixture
def advance(clock: FakeClock) -> Advance:
    async def _advance(seconds: float) -> None:
        clock.advance(seconds)

    return _advance


async def an_agent(
    store: SavedAgentStore, name: str = "stub", url: str = "http://127.0.0.1:9998", **values: Any
) -> SavedAgentRecord:
    return await store.add(name=name, url=url, card=values.pop("card", CARD), **values)


async def a_task(
    tasks: AgentTaskStore, task_id: str, ref: str | None, **changes: Any
) -> AgentTaskRecord:
    now = await tasks.now()
    task = AgentTaskRecord(
        agent="stub",
        task_id=task_id,
        thread_id="th-1",
        context_id="ctx-1",
        message="compile the report",
        state="working",
        started_at=now,
        last_checked_at=now,
        agent_ref=ref,
    )
    task = replace(task, **changes)
    await tasks.record_task(task)
    return task


async def state_of(tasks: AgentTaskStore, task_id: str, agent: str = "stub") -> str:
    task = await tasks.get_task(agent, task_id)
    assert task is not None
    return task.state


async def test_an_added_agent_is_listed_and_read_back(store: SavedAgentStore) -> None:
    assert await store.list_agents() == []
    added = await an_agent(store, "stub", "HTTP://LocalHost:9998/")
    assert len(added.id) == 32
    assert set(added.id) <= set("0123456789abcdef")
    # The address is kept in its one form, with where it leads.
    assert (added.name, added.url, added.destination) == ("stub", "http://localhost:9998", "9998")
    assert (added.read_only, added.version, added.address_version) == (False, 1, 1)
    assert added.ref == f"{added.id}:1"
    assert added.card == CARD
    assert added.card_read_at == added.created_at == added.updated_at
    assert await store.get_agent(added.id) == added
    assert await store.get_agent("0" * 32) is None
    without_approval = await an_agent(store, "alpha", "http://127.0.0.1:9997", read_only=True)
    assert without_approval.read_only is True
    # By name, whatever the order they were added in.
    assert [record.name for record in await store.list_agents()] == ["alpha", "stub"]


async def test_two_agents_cannot_share_a_name_or_an_address(store: SavedAgentStore) -> None:
    first = await an_agent(store, "stub", "http://127.0.0.1:9998")
    with pytest.raises(NameTakenError):
        await an_agent(store, "stub", "http://127.0.0.1:9997")
    # The same place, however this machine and the address are written.
    for spelling in (
        "http://localhost:9998",
        "http://127.0.0.1:9998/",
        "https://[::1]:9998",
        "HTTP://LOCALHOST:9998",
    ):
        with pytest.raises(AddressTakenError):
            await an_agent(store, "twin", spelling)
    # Another path on the same port is another place.
    await an_agent(store, "twin", "http://127.0.0.1:9998/other")
    assert [record.name for record in await store.list_agents()] == ["stub", "twin"]
    assert await store.get_agent(first.id) == first


async def test_a_refused_add_leaves_nothing_behind(store: SavedAgentStore) -> None:
    await an_agent(store, "stub", "http://127.0.0.1:9998")
    before = (await store.revision(), await store.list_agents())
    for name, url in (("stub", "http://127.0.0.1:9997"), ("twin", "http://localhost:9998")):
        with pytest.raises(SavedAgentError) as refused:
            await an_agent(store, name, url)
        # The store's own error: nothing of the address or of the database's text, and
        # nothing behind it that holds either.
        assert refused.value.__cause__ is None
        assert refused.value.__context__ is None
        text = f"{refused.value!s} {refused.value!r}"
        for part in ("999", "127.0.0.1", "localhost", "duplicate", "constraint", "Key"):
            assert part not in text
    assert (await store.revision(), await store.list_agents()) == before


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("Stub", "http://127.0.0.1:9998"),
        ("1stub", "http://127.0.0.1:9998"),
        ("", "http://127.0.0.1:9998"),
        ("a" * 33, "http://127.0.0.1:9998"),
        ("with space", "http://127.0.0.1:9998"),
        ("stub", "http://example.com:9998"),
        ("stub", "http://user:secret@127.0.0.1:9998"),
        ("stub", "http://127.0.0.1:9998/?x=1"),
        ("stub", "http://127.0.0.1:9998/a/.."),
        ("stub", "not an address"),
        # 2,000 characters as entered, and longer once its port is written out.
        ("stub", "http://localhost/" + "a" * 1983),
    ],
    ids=[
        "capital",
        "digit-first",
        "empty",
        "too-long",
        "space",
        "not-local",
        "credentials",
        "query",
        "dot-segment",
        "no-address",
        "longer-once-kept",
    ],
)
async def test_values_outside_the_rules_are_refused_without_repeating_them(
    store: SavedAgentStore, name: str, url: str
) -> None:
    with pytest.raises(InvalidAgentError) as refused:
        await an_agent(store, name, url)
    message = str(refused.value)
    for part in ("9998", "example.com", "secret", "x=1", "Stub", "1stub", "with space"):
        assert part not in message
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert await store.list_agents() == []
    assert await store.revision() == 0


async def test_the_twenty_first_agent_is_refused(store: SavedAgentStore) -> None:
    for number in range(AGENT_LIMIT):
        await an_agent(store, f"agent-{number}", f"http://127.0.0.1:{9000 + number}")
    revision = await store.revision()
    with pytest.raises(AgentLimitError):
        await an_agent(store, "one-more", "http://127.0.0.1:8000")
    assert len(await store.list_agents()) == AGENT_LIMIT == 20
    assert await store.revision() == revision


async def test_the_revision_moves_once_for_every_change(store: SavedAgentStore) -> None:
    assert await store.revision() == 0
    added = await an_agent(store)
    assert await store.revision() == 1
    changed, _ = await store.change(added.id, 1, read_only=True)
    assert await store.revision() == 2
    moved, _ = await store.change(changed.id, 2, url="http://127.0.0.1:9997", card=OTHER_CARD)
    assert await store.revision() == 3
    await store.remove(moved.id, 3)
    assert await store.revision() == 4


async def test_the_agents_mode_is_auto_until_set_and_moves_the_revision_when_it_changes(
    store: SavedAgentStore,
) -> None:
    """Feature 010 (FR-001): the mode sits beside the revision, which a change of it moves."""
    assert await store.mode() == "auto"
    revision = await store.revision()
    assert await store.set_mode("auto") == "auto"
    assert await store.revision() == revision
    assert await store.set_mode("manual") == "manual"
    assert await store.revision() == revision + 1
    assert await store.mode() == "manual"
    with pytest.raises(SavedAgentError):
        await store.set_mode(cast(Any, "off"))
    assert await store.mode() == "manual"
    assert await store.set_mode("auto") == "auto"
    assert await store.revision() == revision + 2


async def test_a_change_of_the_approval_choice_moves_the_version_and_not_the_address(
    store: SavedAgentStore, advance: Advance
) -> None:
    added = await an_agent(store)
    await advance(1)
    changed, given_up = await store.change(added.id, added.version, read_only=True)
    assert given_up == 0
    assert (changed.read_only, changed.version, changed.address_version) == (True, 2, 1)
    assert changed.ref == added.ref
    assert (changed.url, changed.card, changed.card_read_at) == (
        added.url,
        added.card,
        added.card_read_at,
    )
    assert changed.updated_at > added.updated_at
    assert changed.created_at == added.created_at
    assert await store.get_agent(added.id) == changed
    back, _ = await store.change(added.id, 2, read_only=False)
    assert (back.read_only, back.version) == (False, 3)


async def test_a_move_replaces_the_address_and_the_card_details(
    store: SavedAgentStore, advance: Advance
) -> None:
    added = await an_agent(store)
    await advance(1)
    moved, given_up = await store.change(
        added.id, added.version, url="HTTP://localhost:9997/", card=OTHER_CARD
    )
    assert given_up == 0
    assert (moved.url, moved.destination) == ("http://localhost:9997", "9997")
    assert (moved.version, moved.address_version) == (2, 2)
    assert moved.ref == f"{added.id}:2"
    assert moved.card == OTHER_CARD
    assert moved.card_read_at > added.card_read_at
    assert moved.read_only is False
    assert await store.get_agent(added.id) == moved
    # The place it left is free again.
    await an_agent(store, "next", "http://127.0.0.1:9998")


async def test_one_change_can_move_an_agent_and_change_its_approval_choice(
    store: SavedAgentStore,
) -> None:
    added = await an_agent(store)
    both, _ = await store.change(
        added.id, 1, url="http://127.0.0.1:9997", card=OTHER_CARD, read_only=True
    )
    assert (both.url, both.read_only) == ("http://127.0.0.1:9997", True)
    # One change of the operator's, so one step of the version.
    assert (both.version, both.address_version) == (2, 2)
    assert await store.revision() == 2


async def test_a_change_that_changes_nothing_moves_nothing(store: SavedAgentStore) -> None:
    added = await an_agent(store, "stub", "http://127.0.0.1:9998")
    unchanged: list[dict[str, Any]] = [
        {"read_only": False},
        # An entry that comes to the saved form is not a move, whatever card comes with it.
        {"url": "HTTP://127.0.0.1:9998/", "card": OTHER_CARD},
        {"url": "http://127.0.0.1:9998"},
        {},
    ]
    for same in unchanged:
        record, given_up = await store.change(added.id, 1, **same)
        assert (record, given_up) == (added, 0)
    assert await store.revision() == 1
    # A move needs the card that was read at the new address.
    with pytest.raises(InvalidAgentError):
        await store.change(added.id, 1, url="http://127.0.0.1:9997")
    assert await store.get_agent(added.id) == added


async def test_a_move_to_an_address_another_agent_has_is_refused(store: SavedAgentStore) -> None:
    first = await an_agent(store, "stub", "http://127.0.0.1:9998")
    await an_agent(store, "other", "http://127.0.0.1:9997")
    revision = await store.revision()
    with pytest.raises(AddressTakenError):
        await store.change(first.id, 1, url="http://localhost:9997", card=OTHER_CARD)
    assert await store.get_agent(first.id) == first
    assert await store.revision() == revision


async def test_a_change_from_a_version_that_is_no_longer_current_changes_nothing(
    store: SavedAgentStore,
) -> None:
    added = await an_agent(store)
    current, _ = await store.change(added.id, 1, read_only=True)
    revision = await store.revision()
    with pytest.raises(AgentChangedError):
        await store.change(added.id, 1, read_only=False)
    with pytest.raises(AgentChangedError):
        await store.change(added.id, 1, url="http://127.0.0.1:9997", card=OTHER_CARD)
    with pytest.raises(AgentChangedError):
        await store.remove(added.id, 1)
    with pytest.raises(AgentChangedError):
        await store.remove(added.id, 3)
    assert await store.get_agent(added.id) == current
    assert await store.revision() == revision
    for missing in ("0" * 32, "not-an-id"):
        with pytest.raises(AgentNotFoundError):
            await store.change(missing, 1, read_only=True)
        with pytest.raises(AgentNotFoundError):
            await store.remove(missing, 1)


async def test_a_removed_agent_is_gone_and_added_again_is_another_agent(
    store: SavedAgentStore,
) -> None:
    added = await an_agent(store)
    assert await store.remove(added.id, 1) == 0
    assert await store.get_agent(added.id) is None
    assert await store.list_agents() == []
    again = await an_agent(store)
    assert again.id != added.id
    assert (again.version, again.address_version) == (1, 1)
    # A request from a page that still showed the earlier one finds nothing.
    with pytest.raises(AgentNotFoundError):
        await store.change(added.id, 1, read_only=True)
    with pytest.raises(AgentNotFoundError):
        await store.remove(added.id, 1)


async def test_a_move_and_a_removal_give_up_the_agents_pending_tasks(
    store: SavedAgentStore, tasks: AgentTaskStore
) -> None:
    agent = await an_agent(store, "stub", "http://127.0.0.1:9998")
    other = await an_agent(store, "other", "http://127.0.0.1:9996")
    await a_task(tasks, "working", agent.ref)
    await a_task(tasks, "waiting", agent.ref, state="input_required")
    await a_task(tasks, "ended", agent.ref, state="completed", outcome="done")
    await a_task(tasks, "others", other.ref, agent="other")
    await a_task(tasks, "environment", None)

    moved, given_up = await store.change(agent.id, 1, url="http://127.0.0.1:9997", card=OTHER_CARD)
    assert given_up == 2
    for task_id in ("working", "waiting"):
        task = await tasks.get_task("stub", task_id)
        assert task is not None
        assert task.state == "given_up"
        assert task.outcome == given_up_text(GIVEN_UP_MOVED, "stub", task_id)
        assert "moved to another address" in (task.outcome or "")
        assert task.outcome_ready and task.delivered_at is None and task.ended_at is not None
    ended = await tasks.get_task("stub", "ended")
    assert ended is not None and (ended.state, ended.outcome) == ("completed", "done")
    assert await state_of(tasks, "others", "other") == "working"
    # A task with no ref under the same name is an environment agent's, and is left alone.
    assert await state_of(tasks, "environment") == "working"

    # A task started at the earlier address after the move, one at the new address, and one
    # under a ref of the agent that no address of it has had: all are this agent's.
    await a_task(tasks, "late", agent.ref)
    await a_task(tasks, "new", moved.ref)
    await a_task(tasks, "odd", f"{agent.id}:7")
    assert await store.remove(agent.id, moved.version) == 3
    for task_id in ("late", "new", "odd"):
        task = await tasks.get_task("stub", task_id)
        assert task is not None and task.state == "given_up"
        assert task.outcome == given_up_text(GIVEN_UP_REMOVED, "stub", task_id)
        assert "no longer configured" in (task.outcome or "")
    # What was given up at the move keeps the report it got then.
    working = await tasks.get_task("stub", "working")
    assert working is not None and "moved to another address" in (working.outcome or "")
    assert await state_of(tasks, "others", "other") == "working"
    assert await state_of(tasks, "environment") == "working"


async def test_card_details_are_replaced_only_for_the_address_they_were_read_at(
    store: SavedAgentStore, advance: Advance
) -> None:
    agent = await an_agent(store)
    await advance(1)
    same = await store.replace_card(agent.id, agent.ref, dict(CARD))
    assert (same.recorded, same.changed) == (True, False)
    assert await store.get_agent(agent.id) == agent
    assert await store.revision() == 1

    replaced = await store.replace_card(agent.id, agent.ref, OTHER_CARD)
    assert (replaced.recorded, replaced.changed) == (True, True)
    kept = await store.get_agent(agent.id)
    assert kept is not None
    assert kept.card == OTHER_CARD
    assert kept.card_read_at > agent.card_read_at
    # A check is not a change of the operator's: the version stays.
    assert (kept.version, kept.address_version) == (1, 1)
    assert await store.revision() == 2

    moved, _ = await store.change(agent.id, 1, url="http://127.0.0.1:9997", card=CARD)
    stale = await store.replace_card(agent.id, agent.ref, OTHER_CARD)
    assert (stale.recorded, stale.changed) == (False, False)
    assert await store.get_agent(agent.id) == moved
    await store.remove(agent.id, moved.version)
    gone = await store.replace_card(agent.id, moved.ref, OTHER_CARD)
    assert gone.recorded is False
    assert await store.revision() == 4


async def test_card_details_are_kept_as_they_read_back(store: SavedAgentStore) -> None:
    given: dict[str, Any] = {**CARD, "skills": ({"id": "echo", "tags": ("a", "b")},)}
    agent = await an_agent(store, card=given)
    # Lists where tuples were given, the same from either store.
    assert agent.card["skills"] == [{"id": "echo", "tags": ["a", "b"]}]
    # The same details again are no change, however they are given.
    same = await store.replace_card(agent.id, agent.ref, given)
    assert (same.recorded, same.changed) == (True, False)
    assert await store.revision() == 1
    # What the caller does with its own mapping afterwards changes nothing kept.
    given["name"] = "changed behind the store"
    kept = await store.get_agent(agent.id)
    assert kept is not None and kept.card["name"] == CARD["name"]
    with pytest.raises(InvalidAgentError):
        await an_agent(store, "other", "http://127.0.0.1:9997", card={"name": object()})
    assert [record.name for record in await store.list_agents()] == ["stub"]


async def test_pending_tasks_are_counted_by_saved_agent(
    store: SavedAgentStore, tasks: AgentTaskStore
) -> None:
    agent = await an_agent(store, "stub", "http://127.0.0.1:9998")
    other = await an_agent(store, "other", "http://127.0.0.1:9996")
    assert await store.pending_counts() == {}
    await a_task(tasks, "a", agent.ref)
    await a_task(tasks, "b", agent.ref, state="input_required", thread_id="th-2")
    await a_task(tasks, "c", agent.ref, state="completed")
    await a_task(tasks, "d", other.ref, agent="other")
    await a_task(tasks, "e", None)
    # A task a run started at an earlier address still counts for its agent.
    await a_task(tasks, "f", f"{agent.id}:7")
    assert await store.pending_counts() == {agent.id: 3, other.id: 1}
