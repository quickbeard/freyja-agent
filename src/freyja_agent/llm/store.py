"""Where LLM profiles and the active choice are kept (feature 004, research R5).

Two things live in the service's database, so every instance sees the same state:

- a profile is one way to reach a model, saved by an operator, with its key as a token only
  the service's secret reads (FR-004, FR-020);
- the state is one row: which profile is active, where none means the environment's, and a
  revision that moves whenever the active model changes, which is how an instance knows the
  orchestrator it built is out of date (FR-011, FR-013, FR-014).

``PostgresLlmProfileStore`` is the service's store and ``InMemoryLlmProfileStore`` the suite's.
Both apply the same rules through ``changed`` and ``check_values``, and both take every time
from their own clock: the database's ``now()`` and the injected one.

"Exactly one active profile" is the database's to guarantee: the choice is one column of one
row, and a foreign key refuses to delete the row it points at (SC-009).
"""

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from psycopg import errors, sql

from freyja_agent.llm.providers import ENVIRONMENT_NAME, PROVIDERS
from freyja_agent.runtime.persistence import Pool, one_row

# The step of a profile's test that a result is about (``check.check_model``).
Step = Literal["first_message", "conversation"]
# The rules of data-model.md, in one place.
PROFILE_LIMIT = 50
NAME_LIMIT = 64
MODEL_LIMIT = 200
BASE_URL_LIMIT = 2000
REASON_LIMIT = 500
# A change to one of these changes how the model is reached: it clears the last test result.
REACH_FIELDS = ("provider", "model", "base_url", "api_key_encrypted", "responses_api")
EDITABLE_FIELDS = ("name", *REACH_FIELDS, "key_hint")
# Taken for the length of the transaction that counts the profiles before adding one.
_LIMIT_LOCK = 0x46524C4C4D  # "FRLLM"


class LlmProfileError(Exception):
    """A request the store refuses. Nothing was changed."""


class InvalidProfileError(LlmProfileError, ValueError):
    """A value breaks a rule of data-model.md. The message never repeats the value."""


class NameTakenError(LlmProfileError):
    """Another profile has the name, or it is the environment profile's (FR-006)."""


class ProfileLimitError(LlmProfileError):
    """The store already holds the most profiles it may (FR-006)."""


class ProfileNotFoundError(LlmProfileError):
    """No saved profile has the id."""


class ProfileActiveError(LlmProfileError):
    """The profile is the active one and cannot be deleted (FR-010)."""


@dataclass(frozen=True)
class LlmProfileRecord:
    """One saved profile (data-model.md "LLM profile, saved")."""

    id: str
    name: str
    provider: str
    model: str
    base_url: str | None
    # The key as a token under the service's secret; never the key itself.
    api_key_encrypted: str
    # The key's last four characters, or nothing for a key shorter than eight.
    key_hint: str
    responses_api: bool
    # Moves when the way the model is reached changes; a test result belongs to one value.
    config_version: int
    last_test_ok: bool | None
    last_test_at: datetime | None
    last_test_step: Step | None
    last_test_reason: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LlmState:
    """Which profile is active, None for the environment's, and the revision of that choice."""

    active_profile_id: str | None
    revision: int


class LlmProfileStore(Protocol):
    async def setup(self) -> None: ...

    async def list_profiles(self) -> list[LlmProfileRecord]: ...

    async def get_profile(self, profile_id: str) -> LlmProfileRecord | None: ...

    async def create_profile(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        api_key_encrypted: str,
        key_hint: str,
        base_url: str | None = None,
        responses_api: bool = True,
    ) -> LlmProfileRecord: ...

    async def update_profile(self, profile_id: str, **changes: Any) -> LlmProfileRecord: ...

    async def delete_profile(self, profile_id: str) -> None: ...

    async def state(self) -> LlmState: ...

    async def activate(self, profile_id: str | None) -> LlmState: ...

    async def record_test(
        self,
        profile_id: str,
        config_version: int,
        *,
        ok: bool,
        step: Step | None,
        reason: str | None,
    ) -> bool: ...


def check_values(values: dict[str, Any]) -> dict[str, Any]:
    """The given values with text trimmed, or ``InvalidProfileError`` naming the rule broken."""
    checked = dict(values)
    if "name" in checked:
        name = str(checked["name"]).strip()
        if not 1 <= len(name) <= NAME_LIMIT:
            raise InvalidProfileError(f"'name' must be 1 to {NAME_LIMIT} characters")
        if name.lower() == ENVIRONMENT_NAME.lower():
            raise NameTakenError
        checked["name"] = name
    if "provider" in checked and checked["provider"] not in PROVIDERS:
        raise InvalidProfileError(f"'provider' must be one of {', '.join(PROVIDERS)}")
    if "model" in checked:
        model = str(checked["model"]).strip()
        if not 1 <= len(model) <= MODEL_LIMIT:
            raise InvalidProfileError(f"'model' must be 1 to {MODEL_LIMIT} characters")
        checked["model"] = model
    if checked.get("base_url") is not None:
        base_url = str(checked["base_url"]).strip()
        parts = urlsplit(base_url)
        if len(base_url) > BASE_URL_LIMIT:
            raise InvalidProfileError(f"'base_url' must be at most {BASE_URL_LIMIT} characters")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise InvalidProfileError("'base_url' must be an http or https address with a host")
        checked["base_url"] = base_url
    return checked


def changed(record: LlmProfileRecord, changes: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """What an edit really changes, and whether it changes how the model is reached.

    A value sent unchanged is not a change. A key is always one: two tokens of the same key
    differ, so they cannot be compared.
    """
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise InvalidProfileError(f"cannot change {', '.join(sorted(unknown))}")
    if ("api_key_encrypted" in changes) != ("key_hint" in changes):
        raise InvalidProfileError("a key and its hint are changed together")
    checked = check_values(changes)
    real = {
        name: value
        for name, value in checked.items()
        if name == "api_key_encrypted" or getattr(record, name) != value
    }
    if "api_key_encrypted" not in real:
        real.pop("key_hint", None)
    return real, any(name in REACH_FIELDS for name in real)


def _cut(reason: str | None) -> str | None:
    return reason[:REASON_LIMIT] if reason is not None else None


class InMemoryLlmProfileStore:
    """The store for the suite. ``clock`` returns seconds; tests inject one they can advance."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._profiles: dict[str, LlmProfileRecord] = {}
        self._state = LlmState(active_profile_id=None, revision=0)

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), UTC)

    def _name_taken(self, name: str, *, by_other_than: str | None = None) -> bool:
        return any(
            profile.name.lower() == name.lower() and profile.id != by_other_than
            for profile in self._profiles.values()
        )

    async def setup(self) -> None:
        return None

    async def list_profiles(self) -> list[LlmProfileRecord]:
        return sorted(self._profiles.values(), key=lambda profile: profile.name.lower())

    async def get_profile(self, profile_id: str) -> LlmProfileRecord | None:
        return self._profiles.get(profile_id)

    async def create_profile(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        api_key_encrypted: str,
        key_hint: str,
        base_url: str | None = None,
        responses_api: bool = True,
    ) -> LlmProfileRecord:
        values = check_values(
            {"name": name, "provider": provider, "model": model, "base_url": base_url}
        )
        if len(self._profiles) >= PROFILE_LIMIT:
            raise ProfileLimitError
        if self._name_taken(values["name"]):
            raise NameTakenError
        now = self._now()
        record = LlmProfileRecord(
            id=uuid.uuid4().hex,
            api_key_encrypted=api_key_encrypted,
            key_hint=key_hint,
            responses_api=responses_api,
            config_version=1,
            last_test_ok=None,
            last_test_at=None,
            last_test_step=None,
            last_test_reason=None,
            created_at=now,
            updated_at=now,
            **values,
        )
        self._profiles[record.id] = record
        return record

    async def update_profile(self, profile_id: str, **changes: Any) -> LlmProfileRecord:
        record = self._profiles.get(profile_id)
        if record is None:
            raise ProfileNotFoundError
        real, reach = changed(record, changes)
        if not real:
            return record
        if "name" in real and self._name_taken(real["name"], by_other_than=profile_id):
            raise NameTakenError
        if reach:
            real |= {
                "config_version": record.config_version + 1,
                "last_test_ok": None,
                "last_test_at": None,
                "last_test_step": None,
                "last_test_reason": None,
            }
        record = replace(record, updated_at=self._now(), **real)
        self._profiles[profile_id] = record
        if self._state.active_profile_id == profile_id:
            self._state = replace(self._state, revision=self._state.revision + 1)
        return record

    async def delete_profile(self, profile_id: str) -> None:
        if profile_id not in self._profiles:
            raise ProfileNotFoundError
        if self._state.active_profile_id == profile_id:
            raise ProfileActiveError
        del self._profiles[profile_id]

    async def state(self) -> LlmState:
        return self._state

    async def activate(self, profile_id: str | None) -> LlmState:
        if profile_id is not None and profile_id not in self._profiles:
            raise ProfileNotFoundError
        if self._state.active_profile_id != profile_id:
            self._state = LlmState(profile_id, self._state.revision + 1)
        return self._state

    async def record_test(
        self,
        profile_id: str,
        config_version: int,
        *,
        ok: bool,
        step: Step | None,
        reason: str | None,
    ) -> bool:
        record = self._profiles.get(profile_id)
        if record is None or record.config_version != config_version:
            return False
        self._profiles[profile_id] = replace(
            record,
            last_test_ok=ok,
            last_test_at=self._now(),
            last_test_step=step,
            last_test_reason=_cut(reason),
        )
        return True


# Every statement is one parameterized command; identifiers come from a fixed set.
_CREATE = (
    """
    CREATE TABLE IF NOT EXISTS freyja_llm_profiles (
        id                text PRIMARY KEY,
        name              text NOT NULL,
        provider          text NOT NULL,
        model             text NOT NULL,
        base_url          text,
        api_key_encrypted text NOT NULL,
        key_hint          text NOT NULL,
        responses_api     boolean NOT NULL DEFAULT true,
        config_version    integer NOT NULL DEFAULT 1,
        last_test_ok      boolean,
        last_test_at      timestamptz,
        last_test_step    text,
        last_test_reason  text,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS freyja_llm_profiles_name"
    " ON freyja_llm_profiles (lower(name))",
    """
    CREATE TABLE IF NOT EXISTS freyja_llm_state (
        id                smallint PRIMARY KEY CHECK (id = 1),
        active_profile_id text REFERENCES freyja_llm_profiles (id) ON DELETE RESTRICT,
        revision          bigint NOT NULL DEFAULT 0,
        updated_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
    "INSERT INTO freyja_llm_state (id) VALUES (1) ON CONFLICT DO NOTHING",
)

_COLUMNS = [f.name for f in fields(LlmProfileRecord)]
_SELECT = sql.SQL("SELECT {} FROM freyja_llm_profiles").format(
    sql.SQL(", ").join(sql.Identifier(name) for name in _COLUMNS)
)
_RETURNING = sql.SQL(" RETURNING {}").format(
    sql.SQL(", ").join(sql.Identifier(name) for name in _COLUMNS)
)


def _record(row: dict[str, Any]) -> LlmProfileRecord:
    return LlmProfileRecord(**{name: row[name] for name in _COLUMNS})


# Semgrep's rule for SQLAlchemy takes a statement composed with psycopg's ``sql`` module for
# one built from strings, so each such statement below is marked ``nosemgrep`` for that rule.
# Their parts are constants and identifiers of this file, and every value is a parameter.
class PostgresLlmProfileStore:
    """The service's store: two tables in the shared pool (research R5)."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            for statement in _CREATE:
                await conn.execute(statement)

    async def list_profiles(self) -> list[LlmProfileRecord]:
        async with self._pool.connection() as conn:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            cursor = await conn.execute(_SELECT + sql.SQL(" ORDER BY lower(name)"))
            return [_record(row) for row in await cursor.fetchall()]

    async def get_profile(self, profile_id: str) -> LlmProfileRecord | None:
        async with self._pool.connection() as conn:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            cursor = await conn.execute(_SELECT + sql.SQL(" WHERE id = %(id)s"), {"id": profile_id})
            row = await cursor.fetchone()
            return _record(row) if row else None

    async def create_profile(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        api_key_encrypted: str,
        key_hint: str,
        base_url: str | None = None,
        responses_api: bool = True,
    ) -> LlmProfileRecord:
        values = check_values(
            {"name": name, "provider": provider, "model": model, "base_url": base_url}
        ) | {
            "id": uuid.uuid4().hex,
            "api_key_encrypted": api_key_encrypted,
            "key_hint": key_hint,
            "responses_api": responses_api,
        }
        names = list(values)
        insert = sql.SQL("INSERT INTO freyja_llm_profiles ({}) VALUES ({})").format(
            sql.SQL(", ").join(sql.Identifier(name) for name in names),
            sql.SQL(", ").join(sql.Placeholder(name) for name in names),
        )
        try:
            async with self._pool.connection() as conn, conn.transaction():
                # Two adds at once cannot both pass the count: the second waits for the first.
                await conn.execute("SELECT pg_advisory_xact_lock(%(lock)s)", {"lock": _LIMIT_LOCK})
                cursor = await conn.execute("SELECT count(*) AS count FROM freyja_llm_profiles")
                if (await one_row(cursor))["count"] >= PROFILE_LIMIT:
                    raise ProfileLimitError
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                cursor = await conn.execute(insert + _RETURNING, values)
                return _record(await one_row(cursor))
        except errors.UniqueViolation:
            raise NameTakenError from None

    async def update_profile(self, profile_id: str, **changes: Any) -> LlmProfileRecord:
        try:
            async with self._pool.connection() as conn, conn.transaction():
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                cursor = await conn.execute(
                    _SELECT + sql.SQL(" WHERE id = %(id)s FOR UPDATE"), {"id": profile_id}
                )
                row = await cursor.fetchone()
                if row is None:
                    raise ProfileNotFoundError
                record = _record(row)
                real, reach = changed(record, changes)
                if not real:
                    return record
                assignments: list[sql.Composable] = [
                    sql.SQL("{} = {}").format(sql.Identifier(name), sql.Placeholder(name))
                    for name in real
                ]
                assignments.append(sql.SQL("updated_at = now()"))
                if reach:
                    assignments.append(
                        sql.SQL(
                            "config_version = config_version + 1, last_test_ok = NULL,"
                            " last_test_at = NULL, last_test_step = NULL,"
                            " last_test_reason = NULL"
                        )
                    )
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                cursor = await conn.execute(
                    sql.SQL("UPDATE freyja_llm_profiles SET {} WHERE id = %(id)s").format(
                        sql.SQL(", ").join(assignments)
                    )
                    + _RETURNING,
                    {**real, "id": profile_id},
                )
                updated = _record(await one_row(cursor))
                # An edit of the active profile changes the model in use (FR-013).
                await conn.execute(
                    "UPDATE freyja_llm_state SET revision = revision + 1, updated_at = now()"
                    " WHERE id = 1 AND active_profile_id = %(id)s",
                    {"id": profile_id},
                )
                return updated
        except errors.UniqueViolation:
            raise NameTakenError from None

    async def delete_profile(self, profile_id: str) -> None:
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(
                    "DELETE FROM freyja_llm_profiles WHERE id = %(id)s", {"id": profile_id}
                )
                deleted = cursor.rowcount
        except errors.ForeignKeyViolation:
            # The state row points at it: it is the active profile (SC-009).
            raise ProfileActiveError from None
        if not deleted:
            raise ProfileNotFoundError

    async def state(self) -> LlmState:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT active_profile_id, revision FROM freyja_llm_state WHERE id = 1"
            )
            row = await one_row(cursor)
            return LlmState(row["active_profile_id"], row["revision"])

    async def activate(self, profile_id: str | None) -> LlmState:
        try:
            async with self._pool.connection() as conn:
                # One statement: either the choice changes and the revision moves, or neither.
                await conn.execute(
                    "UPDATE freyja_llm_state SET active_profile_id = %(id)s,"
                    " revision = revision + 1, updated_at = now()"
                    " WHERE id = 1 AND active_profile_id IS DISTINCT FROM %(id)s",
                    {"id": profile_id},
                )
        except errors.ForeignKeyViolation:
            raise ProfileNotFoundError from None
        return await self.state()

    async def record_test(
        self,
        profile_id: str,
        config_version: int,
        *,
        ok: bool,
        step: Step | None,
        reason: str | None,
    ) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "UPDATE freyja_llm_profiles SET last_test_ok = %(ok)s, last_test_at = now(),"
                " last_test_step = %(step)s, last_test_reason = %(reason)s"
                " WHERE id = %(id)s AND config_version = %(version)s",
                {
                    "ok": ok,
                    "step": step,
                    "reason": _cut(reason),
                    "id": profile_id,
                    "version": config_version,
                },
            )
            return cursor.rowcount == 1
