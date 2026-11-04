"""FR-008, FR-014, SC-004: pause, stop, start as another instance, resume, all through Postgres."""

import logging
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.api.app import create_app
from freyja_agent.config import Settings
from freyja_agent.runtime import persistence
from freyja_agent.runtime.persistence import DatabaseUnavailableError
from freyja_agent.runtime.runtime import Runtime
from tests.fakes import ScriptedChatModel

ACTIONS = "freyja_agent.actions"
PROPOSED = {"channel": "#ops", "message": "Nightly report is ready."}
NOTIFY = AIMessage(
    content="", tool_calls=[{"id": "n1", "name": "send_notification", "args": PROPOSED}]
)
Events = list[tuple[str, Any]]


def service(settings: Settings, model: ScriptedChatModel, instance_id: str) -> FastAPI:
    """One service process: the production app factory opening ``Runtime`` against Postgres."""
    return create_app(
        open_runtime=lambda _settings: Runtime.open(settings, model=model, instance_id=instance_id)
    )


def stream(client: TestClient, path: str, body: dict, sse_events: Any) -> Events:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def lock_rows(settings: Settings) -> int:
    with psycopg.connect(settings.database_uri) as conn:
        row = conn.execute("SELECT count(*) FROM freyja_run_locks").fetchone()
    assert row is not None
    return int(row[0])


def test_a_pause_survives_a_restart_through_postgres(
    settings: Settings, sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=ACTIONS)
    with TestClient(service(settings, ScriptedChatModel(script=[NOTIFY]), "instance-a")) as a:
        events = stream(a, "/threads/pg-1/runs", {"message": "notify"}, sse_events)
        assert lock_rows(settings) == 0
    kind, interrupt = events[-1]
    assert kind == "interrupt"
    assert [r for r in caplog.records if r.name == ACTIONS] == []

    # The first process is gone: its pool is closed. A new one opens against the same database.
    second = service(settings, ScriptedChatModel(final_text="Acknowledged."), "instance-b")
    with TestClient(second) as b:
        state = b.get("/threads/pg-1/state").json()
        assert state["pending"] == interrupt
        assert state["run_active"] is False
        assert [m["type"] for m in state["messages"]] == ["human", "ai"]
        events = stream(b, "/threads/pg-1/resume", {"decisions": [{"type": "approve"}]}, sse_events)
        after = b.get("/threads/pg-1/state").json()
        assert lock_rows(settings) == 0

    assert events[0][1]["mode"] == "resume"
    assert events[-1] == ("end", {"status": "completed"})
    assert after["pending"] is None
    assert [m["type"] for m in after["messages"]] == ["human", "ai", "tool", "ai"]
    records = [r for r in caplog.records if r.name == ACTIONS]
    assert len(records) == 1
    assert "channel=#ops message=Nightly report is ready." in records[0].getMessage()


async def test_an_unreachable_database_fails_startup_naming_the_connection(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(persistence, "CONNECT_TIMEOUT_SECONDS", 1.0)
    unreachable = Settings(
        database_uri="postgresql://freyja:hunter2@127.0.0.1:1/freyja", workspace_dir=str(workspace)
    )
    with pytest.raises(DatabaseUnavailableError) as raised:
        async with Runtime.open(unreachable, model=ScriptedChatModel()):
            pass
    message = str(raised.value)
    assert "127.0.0.1:1/freyja" in message
    assert "hunter2" not in message
