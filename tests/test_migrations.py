"""The rules of the schema's migrations, as far as they hold without a database.

``tests/integration/test_migrations_postgres.py`` applies the migrations to a real Postgres.
Here: what a database's rows of applied migrations mean for a build (``pending``), what a
migration's checksum counts, that a released migration is the text it was released as, and
that the migrations are the one place the service makes or changes a table.
"""

import re
from pathlib import Path

import pytest

import freyja_agent
from freyja_agent.runtime import migrations
from freyja_agent.runtime.migrations import (
    MIGRATIONS,
    Migration,
    SchemaMismatchError,
    SchemaNewerError,
    checked,
    checksum,
    pending,
)

# The checksum of every migration that was merged, by its version. A database that had a
# migration keeps this checksum and refuses a build whose migration has another, so a test
# that fails here says that a released migration was edited: put the migration back as it
# was, and make the change a new migration. A new migration adds its line.
RELEASED = {
    1: "d4c4cdd32f339730fd430ba4833f602ac3085f372b937a7049ee919bda017dae",
    2: "6c8c6d15aa318c1f98c7d2be258f1728c0c1bb8ad107f9f3fedbfb84e0b855bf",
}

ONE = Migration(1, "one", ("CREATE TABLE a (id integer)",))
TWO = Migration(2, "two", ("CREATE TABLE b (id integer)", "CREATE INDEX b_id ON b (id)"))
THREE = Migration(3, "three", ("ALTER TABLE a ADD COLUMN note text",))
LIST = (ONE, TWO, THREE)


def applied(*migrations: Migration) -> dict[int, str]:
    """A database's rows after these migrations were applied to it."""
    return {migration.version: checksum(migration) for migration in migrations}


# What a database still needs.


def test_a_new_database_needs_every_migration_in_order() -> None:
    assert pending({}, LIST) == [ONE, TWO, THREE]


def test_a_database_needs_the_migrations_after_the_ones_it_had() -> None:
    assert pending(applied(ONE), LIST) == [TWO, THREE]
    assert pending(applied(ONE, TWO), LIST) == [THREE]


def test_a_database_at_the_build_s_version_needs_nothing() -> None:
    assert pending(applied(ONE, TWO, THREE), LIST) == []


def test_a_database_a_newer_build_migrated_is_refused() -> None:
    with pytest.raises(SchemaNewerError) as refused:
        pending(applied(ONE, TWO, THREE), (ONE, TWO))
    message = str(refused.value)
    assert "schema version 3" in message
    assert "this build knows 2" in message


def test_a_newer_database_is_refused_before_anything_else_is_said_of_it() -> None:
    """A database from a newer build may also differ in an earlier migration, which that
    build may have had reason for: what the operator needs to hear is that the build is old."""
    rows = {**applied(ONE, TWO, THREE), 1: "0" * 64}
    with pytest.raises(SchemaNewerError):
        pending(rows, (ONE, TWO))


def test_a_migration_that_was_applied_as_another_text_is_refused() -> None:
    edited = Migration(2, "two", ("CREATE TABLE b (id bigint)",))
    with pytest.raises(SchemaMismatchError) as refused:
        pending(applied(ONE, TWO), (ONE, edited, THREE))
    message = str(refused.value)
    assert "Migration 2 (two)" in message
    assert "never edited" in message


def test_a_database_that_skipped_a_migration_is_refused() -> None:
    """Two changes were each written as migration 2 and 3 on their own branches and merged
    the other way round: a database that had the first to be released lacks the other."""
    with pytest.raises(SchemaMismatchError) as refused:
        pending(applied(ONE, THREE), LIST)
    message = str(refused.value)
    assert "has migration 3 and not migration 2 (two)" in message


# The list itself.


@pytest.mark.parametrize(
    "versions",
    [(2,), (0, 1), (1, 3), (1, 2, 2), (2, 1), (1, 2, 4)],
    ids=lambda versions: "-".join(map(str, versions)),
)
def test_a_list_that_is_not_numbered_from_one_without_a_gap_is_refused(
    versions: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="numbered from 1 with no gap") as refused:
        checked([Migration(version, "any", ()) for version in versions])
    assert str(list(versions)) in str(refused.value)


def test_a_list_numbered_from_one_is_taken_as_it_is() -> None:
    assert checked([ONE, TWO, THREE]) == LIST
    assert checked([]) == ()


def test_the_build_s_migrations_are_numbered_from_one_and_each_has_a_name() -> None:
    assert [migration.version for migration in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))
    assert all(migration.name.strip() for migration in MIGRATIONS)
    assert all(migration.statements for migration in MIGRATIONS)
    assert len({migration.name for migration in MIGRATIONS}) == len(MIGRATIONS)


# The checksum.


def test_a_checksum_does_not_count_how_a_statement_is_laid_out() -> None:
    laid_out = Migration(
        2,
        "another name",
        ("\n    CREATE TABLE b\n        (id   integer)\n", "CREATE INDEX b_id\tON b (id)"),
    )
    assert checksum(laid_out) == checksum(TWO)


@pytest.mark.parametrize(
    "statements",
    [
        ("CREATE TABLE b (id bigint)", "CREATE INDEX b_id ON b (id)"),
        ("CREATE INDEX b_id ON b (id)", "CREATE TABLE b (id integer)"),
        ("CREATE TABLE b (id integer)",),
        ("CREATE TABLE b (id integer)", "CREATE INDEX b_id ON b (id)", "SELECT 1"),
        ("CREATE TABLE b (id integer) -- a note", "CREATE INDEX b_id ON b (id)"),
        ("CREATE TABLE b (id integer) CREATE INDEX b_id ON b (id)",),
    ],
    ids=["a type", "the order", "one fewer", "one more", "a comment", "two joined into one"],
)
def test_a_checksum_counts_every_other_change(statements: tuple[str, ...]) -> None:
    assert checksum(Migration(2, "two", statements)) != checksum(TWO)


def test_every_migration_is_the_text_it_was_released_as() -> None:
    """Fails when a released migration is edited, and when a new one has no line in
    ``RELEASED`` yet: the first is undone, the second gets its line."""
    assert {migration.version: checksum(migration) for migration in MIGRATIONS} == RELEASED


# The one place.

SOURCE = Path(freyja_agent.__file__).parent
MODULE = Path(migrations.__file__)
# A statement that makes, changes, or removes a table or an index.
DDL = re.compile(r"\b(CREATE|ALTER|DROP)\s+(UNIQUE\s+)?(TABLE|INDEX)\b", re.IGNORECASE)
# A table a statement reads or writes, by the word before its name.
USED = re.compile(r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+(freyja_[a-z0-9_]+)")
MADE = re.compile(
    r"CREATE TABLE IF NOT EXISTS (freyja_[a-z0-9_]+)|CREATE TABLE (freyja_[a-z0-9_]+)"
)


def test_no_module_but_the_migrations_makes_or_changes_a_table() -> None:
    found = {
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*.py")
        if path != MODULE and DDL.search(path.read_text(encoding="utf-8"))
    }
    assert found == set()


def test_every_table_a_statement_of_the_service_names_is_made_by_a_migration() -> None:
    made = {
        first or second
        for migration in MIGRATIONS
        for statement in migration.statements
        for first, second in MADE.findall(statement)
    }
    made.add("freyja_schema_migrations")  # the migrations' own, made by ``migrate``
    used: dict[str, set[str]] = {}
    for path in SOURCE.rglob("*.py"):
        for table in USED.findall(path.read_text(encoding="utf-8")):
            used.setdefault(table, set()).add(path.relative_to(SOURCE).as_posix())
    assert len(used) > 10  # the search finds the stores' statements
    assert {table: files for table, files in used.items() if table not in made} == {}
