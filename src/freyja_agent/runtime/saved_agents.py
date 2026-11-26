"""Where saved agents are kept (feature 005, research R1, R2, R10, R14).

A saved agent is one an operator added through the console: operator-managed configuration,
kept in the service's database and the same for every instance (constitution principle IV).
Beside the agents sits a one-row revision that moves whenever what a run would be built with
changes; an instance reads it to learn that the set it built for is no longer current.

Two stores meet in one place. Moving or removing a saved agent gives up its pending tasks,
and the change and the giving up happen together or not at all (FR-024):
``PostgresSavedAgentStore`` writes to ``freyja_agent_tasks`` in the same transaction, and
``InMemorySavedAgentStore`` is given the in-memory task store.

Nothing an error of this module says repeats an address or anything the database said.
"""

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from psycopg import errors, sql
from psycopg.types.json import Jsonb

from freyja_agent.config import SERVER_NAME_PATTERN, AddressError, canonical_address, destination
from freyja_agent.runtime.agent_tasks import (
    GIVE_UP,
    GIVEN_UP_MOVED,
    GIVEN_UP_REMOVED,
    InMemoryAgentTaskStore,
)
from freyja_agent.runtime.persistence import Pool, one_row

# Each saved agent adds two tools to every run of the orchestrator and its subagent (FR-006).
AGENT_LIMIT = 20
# The transaction-level advisory lock that makes the count and the insert one step.
_LIMIT_LOCK = 0x4652414754  # "FRAGT"
_NAME = re.compile(SERVER_NAME_PATTERN)


class SavedAgentError(Exception):
    """A change of saved agents that was refused. Nothing was saved."""


class InvalidAgentError(SavedAgentError, ValueError):
    """A value breaks a rule. The message names the rule and never the value."""


class NameTakenError(SavedAgentError):
    """Another saved agent has the name."""


class AddressTakenError(SavedAgentError):
    """Another saved agent's address leads where this one does."""


class AgentLimitError(SavedAgentError):
    """``AGENT_LIMIT`` agents are saved."""


class AgentNotFoundError(SavedAgentError):
    """There is no saved agent with the id."""


class AgentChangedError(SavedAgentError):
    """The agent was changed since the version the request names (FR-012)."""


@dataclass(frozen=True)
class SavedAgentRecord:
    """One agent an operator added (data-model.md "Saved agent")."""

    id: str
    name: str
    # The address in its one kept form, and where it leads however this machine is written.
    url: str
    destination: str
    # The approval choice, named as the environment's entry names it: False requires approval.
    read_only: bool
    # Counts the addresses this agent has had; a move adds one.
    address_version: int
    # Counts the operator's changes; a change or a removal names the one its page read.
    version: int
    # The card details as kept: the cut and cleaned form, as ``AgentCardSummary.to_dict``.
    card: dict[str, Any]
    card_read_at: datetime
    created_at: datetime
    updated_at: datetime

    @property
    def ref(self) -> str:
        """Which agent, at which address: what a task started with this agent carries."""
        return f"{self.id}:{self.address_version}"


@dataclass(frozen=True)
class CardRecorded:
    """What became of card details a check read (FR-014, FR-015)."""

    # False when the agent was moved or removed since the check began: nothing was kept.
    recorded: bool
    # True when the kept details were replaced by different ones.
    changed: bool = False


class SavedAgentStore(Protocol):
    async def setup(self) -> None: ...

    async def revision(self) -> int: ...

    async def list_agents(self) -> list[SavedAgentRecord]: ...

    async def get_agent(self, agent_id: str) -> SavedAgentRecord | None: ...

    async def add(
        self, *, name: str, url: str, card: dict[str, Any], read_only: bool = False
    ) -> SavedAgentRecord: ...

    async def change(
        self,
        agent_id: str,
        version: int,
        *,
        read_only: bool | None = None,
        url: str | None = None,
        card: dict[str, Any] | None = None,
    ) -> tuple[SavedAgentRecord, int]: ...

    async def remove(self, agent_id: str, version: int) -> int: ...

    async def replace_card(self, agent_id: str, ref: str, card: dict[str, Any]) -> CardRecorded: ...

    async def pending_counts(self) -> dict[str, int]: ...


def check_name(name: str) -> str:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise InvalidAgentError(
            "The name must start with a lowercase letter and hold at most 32 lowercase "
            "letters, digits, hyphens, and underscores."
        )
    return name


def check_address(url: str) -> tuple[str, str]:
    """The kept form of ``url`` and where it leads."""
    refused = None
    try:
        kept = canonical_address(url)
        return kept, destination(kept)
    except AddressError as exc:
        refused = str(exc)
    raise InvalidAgentError(refused)


def plain(card: dict[str, Any]) -> dict[str, Any]:
    """``card`` as it reads back from storage: a copy, with lists where it had tuples.

    Both stores keep and compare this form, so the same details are the same in each.
    """
    refused = False
    kept: object = None
    try:
        kept = json.loads(json.dumps(card))
    except (TypeError, ValueError):
        refused = True
    if refused or not isinstance(kept, dict):
        raise InvalidAgentError("The card details must be a mapping that can be stored.")
    return kept


def _moved(record: SavedAgentRecord, url: str | None, card: dict[str, Any] | None) -> bool:
    """Whether a change gives the agent another address. It then needs the card read there."""
    if url is None or check_address(url)[0] == record.url:
        return False
    if card is None:
        raise InvalidAgentError("A change of address needs the card read at the new address.")
    return True


class InMemorySavedAgentStore:
    """The store for the suite. ``clock`` returns seconds; tests inject one they can advance."""

    def __init__(
        self,
        tasks: InMemoryAgentTaskStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._tasks = tasks if tasks is not None else InMemoryAgentTaskStore(clock=clock)
        self._clock = clock
        self._agents: dict[str, SavedAgentRecord] = {}
        self._revision = 0

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), UTC)

    async def setup(self) -> None:
        return None

    async def revision(self) -> int:
        return self._revision

    async def list_agents(self) -> list[SavedAgentRecord]:
        return sorted(self._agents.values(), key=lambda record: record.name)

    async def get_agent(self, agent_id: str) -> SavedAgentRecord | None:
        return self._agents.get(agent_id)

    def _taken(self, destination: str, *, but: str | None = None) -> bool:
        return any(
            record.destination == destination and record.id != but
            for record in self._agents.values()
        )

    async def add(
        self, *, name: str, url: str, card: dict[str, Any], read_only: bool = False
    ) -> SavedAgentRecord:
        check_name(name)
        kept, leads_to = check_address(url)
        if len(self._agents) >= AGENT_LIMIT:
            raise AgentLimitError
        if any(record.name == name for record in self._agents.values()):
            raise NameTakenError
        if self._taken(leads_to):
            raise AddressTakenError
        now = self._now()
        record = SavedAgentRecord(
            id=uuid.uuid4().hex,
            name=name,
            url=kept,
            destination=leads_to,
            read_only=bool(read_only),
            address_version=1,
            version=1,
            card=plain(card),
            card_read_at=now,
            created_at=now,
            updated_at=now,
        )
        self._agents[record.id] = record
        self._revision += 1
        return record

    def _current(self, agent_id: str, version: int) -> SavedAgentRecord:
        record = self._agents.get(agent_id)
        if record is None:
            raise AgentNotFoundError
        if record.version != version:
            raise AgentChangedError
        return record

    async def _give_up(self, record: SavedAgentRecord, text: str) -> int:
        # Every address the agent has had: a ref is ``<id>:<address_version>``.
        refs = [
            ref
            for _agent, ref in await self._tasks.pending_counts()
            if ref is not None and ref.partition(":")[0] == record.id
        ]
        return await self._tasks.give_up_pending(refs, text)

    async def change(
        self,
        agent_id: str,
        version: int,
        *,
        read_only: bool | None = None,
        url: str | None = None,
        card: dict[str, Any] | None = None,
    ) -> tuple[SavedAgentRecord, int]:
        record = self._current(agent_id, version)
        moved = _moved(record, url, card)
        changes: dict[str, Any] = {}
        if moved and url is not None and card is not None:
            kept, leads_to = check_address(url)
            if self._taken(leads_to, but=record.id):
                raise AddressTakenError
            changes |= {
                "url": kept,
                "destination": leads_to,
                "address_version": record.address_version + 1,
                "card": plain(card),
                "card_read_at": self._now(),
            }
        if read_only is not None and bool(read_only) != record.read_only:
            changes["read_only"] = bool(read_only)
        if not changes:
            return record, 0
        # The tasks of every address the agent has had so far, the one it is leaving included.
        given_up = await self._give_up(record, GIVEN_UP_MOVED) if moved else 0
        changed = replace(record, **changes, version=record.version + 1, updated_at=self._now())
        self._agents[agent_id] = changed
        self._revision += 1
        return changed, given_up

    async def remove(self, agent_id: str, version: int) -> int:
        record = self._current(agent_id, version)
        given_up = await self._give_up(record, GIVEN_UP_REMOVED)
        del self._agents[agent_id]
        self._revision += 1
        return given_up

    async def replace_card(self, agent_id: str, ref: str, card: dict[str, Any]) -> CardRecorded:
        record = self._agents.get(agent_id)
        if record is None or record.ref != ref:
            return CardRecorded(recorded=False)
        card = plain(card)
        if record.card == card:
            return CardRecorded(recorded=True)
        self._agents[agent_id] = replace(record, card=card, card_read_at=self._now())
        self._revision += 1
        return CardRecorded(recorded=True, changed=True)

    async def pending_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for (_agent, ref), count in (await self._tasks.pending_counts()).items():
            if ref is not None:
                agent_id = ref.partition(":")[0]
                counts[agent_id] = counts.get(agent_id, 0) + count
        return counts


# Every statement is one parameterized command; identifiers come from a fixed set.
_CREATE = (
    """
    CREATE TABLE IF NOT EXISTS freyja_agents (
        id              text PRIMARY KEY,
        name            text NOT NULL,
        url             text NOT NULL,
        destination     text NOT NULL,
        read_only       boolean NOT NULL DEFAULT false,
        address_version integer NOT NULL DEFAULT 1,
        version         integer NOT NULL DEFAULT 1,
        card            jsonb NOT NULL,
        card_read_at    timestamptz NOT NULL DEFAULT now(),
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT freyja_agents_name UNIQUE (name),
        CONSTRAINT freyja_agents_destination UNIQUE (destination)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS freyja_agents_state (
        id         smallint PRIMARY KEY CHECK (id = 1),
        revision   bigint NOT NULL DEFAULT 0,
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "INSERT INTO freyja_agents_state (id) VALUES (1) ON CONFLICT DO NOTHING",
)

_COLUMNS = [f.name for f in fields(SavedAgentRecord)]
_NAMES = sql.SQL(", ").join(sql.Identifier(name) for name in _COLUMNS)
_SELECT = sql.SQL("SELECT {} FROM freyja_agents").format(_NAMES)
_RETURNING = sql.SQL(" RETURNING {}").format(_NAMES)
# Moves the revision. A statement of its own, so that every change ends with the same one.
BUMP_REVISION = (
    "UPDATE freyja_agents_state SET revision = revision + 1, updated_at = now() WHERE id = 1"
)
# The pending tasks of every address a saved agent has had: a ref is ``<id>:<address_version>``.
_OF_AGENT = " AND split_part(agent_ref, ':', 1) = %(id)s"


def _record(row: dict[str, Any]) -> SavedAgentRecord:
    return SavedAgentRecord(**{name: row[name] for name in _COLUMNS})


def _taken(exc: errors.UniqueViolation) -> SavedAgentError:
    """The store's own error for a unique violation: the database's text repeats the value."""
    if exc.diag.constraint_name == "freyja_agents_destination":
        return AddressTakenError()
    return NameTakenError()


# Semgrep's rule for SQLAlchemy takes a statement composed with psycopg's ``sql`` module for
# one built from strings, so each such statement below is marked ``nosemgrep`` for that rule.
# Their parts are constants and identifiers of this file, and every value is a parameter.
class PostgresSavedAgentStore:
    """The service's store: two tables in the shared pool (research R1).

    It needs ``freyja_agent_tasks`` with its ``agent_ref`` column, which
    ``PostgresAgentTaskStore.setup`` provides: set that store up first.
    """

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            for statement in _CREATE:
                await conn.execute(statement)

    async def revision(self) -> int:
        async with self._pool.connection() as conn:
            cursor = await conn.execute("SELECT revision FROM freyja_agents_state WHERE id = 1")
            return (await one_row(cursor))["revision"]

    async def list_agents(self) -> list[SavedAgentRecord]:
        async with self._pool.connection() as conn:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            cursor = await conn.execute(_SELECT + sql.SQL(" ORDER BY name"))
            return [_record(row) for row in await cursor.fetchall()]

    async def get_agent(self, agent_id: str) -> SavedAgentRecord | None:
        async with self._pool.connection() as conn:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            cursor = await conn.execute(_SELECT + sql.SQL(" WHERE id = %(id)s"), {"id": agent_id})
            row = await cursor.fetchone()
            return _record(row) if row else None

    async def add(
        self, *, name: str, url: str, card: dict[str, Any], read_only: bool = False
    ) -> SavedAgentRecord:
        check_name(name)
        kept, leads_to = check_address(url)
        values = {
            "id": uuid.uuid4().hex,
            "name": name,
            "url": kept,
            "destination": leads_to,
            "read_only": bool(read_only),
            "card": Jsonb(plain(card)),
        }
        insert = sql.SQL("INSERT INTO freyja_agents ({}) VALUES ({})").format(
            sql.SQL(", ").join(sql.Identifier(key) for key in values),
            sql.SQL(", ").join(sql.Placeholder(key) for key in values),
        )
        try:
            async with self._pool.connection() as conn, conn.transaction():
                # Two adds at once cannot both pass the count: the second waits for the first.
                await conn.execute("SELECT pg_advisory_xact_lock(%(lock)s)", {"lock": _LIMIT_LOCK})
                cursor = await conn.execute("SELECT count(*) AS count FROM freyja_agents")
                if (await one_row(cursor))["count"] >= AGENT_LIMIT:
                    raise AgentLimitError
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                cursor = await conn.execute(insert + _RETURNING, values)
                record = _record(await one_row(cursor))
                await conn.execute(BUMP_REVISION)
                return record
        except errors.UniqueViolation as exc:
            taken = _taken(exc)
        # Raised here, not in the handler: the database's error names the value that was taken.
        raise taken

    @staticmethod
    async def _locked(conn: Any, agent_id: str, version: int) -> SavedAgentRecord:
        """The agent as it is, held for this transaction, when it is still at ``version``."""
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        cursor = await conn.execute(
            _SELECT + sql.SQL(" WHERE id = %(id)s FOR UPDATE"), {"id": agent_id}
        )
        row = await cursor.fetchone()
        if row is None:
            raise AgentNotFoundError
        record = _record(row)
        if record.version != version:
            raise AgentChangedError
        return record

    async def change(
        self,
        agent_id: str,
        version: int,
        *,
        read_only: bool | None = None,
        url: str | None = None,
        card: dict[str, Any] | None = None,
    ) -> tuple[SavedAgentRecord, int]:
        try:
            async with self._pool.connection() as conn, conn.transaction():
                record = await self._locked(conn, agent_id, version)
                moved = _moved(record, url, card)
                changes: dict[str, Any] = {}
                extra = sql.SQL("")
                if moved and url is not None and card is not None:
                    kept, leads_to = check_address(url)
                    changes |= {"url": kept, "destination": leads_to, "card": Jsonb(plain(card))}
                    extra = sql.SQL(", address_version = address_version + 1, card_read_at = now()")
                if read_only is not None and bool(read_only) != record.read_only:
                    changes["read_only"] = bool(read_only)
                if not changes:
                    return record, 0
                assignments = sql.SQL(", ").join(
                    sql.SQL("{} = {}").format(sql.Identifier(key), sql.Placeholder(key))
                    for key in changes
                )
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                cursor = await conn.execute(
                    sql.SQL(
                        "UPDATE freyja_agents SET {}{}, version = version + 1, updated_at = now()"
                        " WHERE id = %(key_id)s AND version = %(key_version)s"
                    ).format(assignments, extra)
                    + _RETURNING,
                    {**changes, "key_id": agent_id, "key_version": version},
                )
                changed = _record(await one_row(cursor))
                given_up = 0
                if moved:
                    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                    cursor = await conn.execute(
                        GIVE_UP + _OF_AGENT, {"text": GIVEN_UP_MOVED, "id": agent_id}
                    )
                    given_up = cursor.rowcount
                await conn.execute(BUMP_REVISION)
                return changed, given_up
        except errors.UniqueViolation as exc:
            taken = _taken(exc)
        raise taken

    async def remove(self, agent_id: str, version: int) -> int:
        async with self._pool.connection() as conn, conn.transaction():
            await self._locked(conn, agent_id, version)
            await conn.execute(
                "DELETE FROM freyja_agents WHERE id = %(id)s AND version = %(version)s",
                {"id": agent_id, "version": version},
            )
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            cursor = await conn.execute(
                GIVE_UP + _OF_AGENT, {"text": GIVEN_UP_REMOVED, "id": agent_id}
            )
            given_up = cursor.rowcount
            await conn.execute(BUMP_REVISION)
            return given_up

    async def replace_card(self, agent_id: str, ref: str, card: dict[str, Any]) -> CardRecorded:
        async with self._pool.connection() as conn, conn.transaction():
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            cursor = await conn.execute(
                _SELECT + sql.SQL(" WHERE id = %(id)s FOR UPDATE"), {"id": agent_id}
            )
            row = await cursor.fetchone()
            if row is None or _record(row).ref != ref:
                return CardRecorded(recorded=False)
            card = plain(card)
            if row["card"] == card:
                return CardRecorded(recorded=True)
            await conn.execute(
                "UPDATE freyja_agents SET card = %(card)s, card_read_at = now() WHERE id = %(id)s",
                {"id": agent_id, "card": Jsonb(card)},
            )
            await conn.execute(BUMP_REVISION)
            return CardRecorded(recorded=True, changed=True)

    async def pending_counts(self) -> dict[str, int]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT split_part(agent_ref, ':', 1) AS id, count(*) AS count"
                " FROM freyja_agent_tasks"
                " WHERE state IN ('working', 'input_required') AND agent_ref IS NOT NULL"
                " GROUP BY 1"
            )
            return {row["id"]: row["count"] for row in await cursor.fetchall()}
