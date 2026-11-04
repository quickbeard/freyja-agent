"""Opt-in tests against a real Postgres (research R11).

Set ``FREYJA_TEST_DATABASE_URI`` to a database that may be wiped; the compose file's
``freyja_test`` is the documented one (.env.example). Without it, every test in this
directory is skipped, so ``uv run pytest`` still needs no database. Each test runs against
the same table layout the service creates at startup and drops every table afterwards.
"""

import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from freyja_agent.config import Settings

TEST_URI_VAR = "FREYJA_TEST_DATABASE_URI"
TEST_URI = os.environ.get(TEST_URI_VAR)
INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_URI:
        return
    skip = pytest.mark.skip(reason=f"{TEST_URI_VAR} is not set; see .env.example")
    for item in items:
        if INTEGRATION_DIR in Path(str(item.path)).resolve().parents:
            item.add_marker(skip)


@pytest.fixture
def settings(workspace: Path) -> Settings:
    """Process settings pointed at the test database and a throwaway workspace."""
    assert TEST_URI is not None
    return Settings(database_uri=TEST_URI, workspace_dir=str(workspace))


@pytest.fixture(autouse=True)
def clean_database() -> None:
    """Drop every table the test created: the LangGraph tables and ``freyja_run_locks``."""
    yield
    if TEST_URI is None:
        return
    with psycopg.connect(TEST_URI, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
        for (name,) in rows:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(name)))
