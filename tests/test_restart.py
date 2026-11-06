"""FR-005, FR-006, FR-014, SC-004: a paused thread outlives the service; a dead instance's lease expires.

A restart is simulated by a second app whose runtime shares the first's saver, store, and lock
store (the "database") but has its own agent, model client, and instance id.
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.runtime.runtime import Runtime
from tests.fakes import FakeClock, ScriptedChatModel

ACTIONS = "freyja_agent.actions"
PROPOSED = {"channel": "#ops", "message": "Nightly report is ready."}
NOTIFY = AIMessage(
    content="", tool_calls=[{"id": "n1", "name": "send_notification", "args": PROPOSED}]
)
Events = list[tuple[str, Any]]


def stream(client: TestClient, path: str, body: dict, sse_events: Any) -> Events:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def restarted(make_runtime: Callable[..., Runtime], first: Runtime, model: Any) -> Runtime:
    """The service after a stop and start: same persistence, everything else new."""
    return make_runtime(
        model,
        instance_id="instance-b",
        saver=first.agent.checkpointer,
        store=first.agent.store,
        locks=first.locks,
    )


def test_a_pause_survives_a_restart_and_resumes_on_the_new_instance(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    clock: FakeClock,
) -> None:
    caplog.set_level(logging.WARNING, logger=ACTIONS)
    first = make_runtime(ScriptedChatModel(script=[NOTIFY]), instance_id="instance-a")
    with TestClient(make_app(first)) as a:
        events = stream(a, "/threads/r-1/runs", {"message": "notify"}, sse_events)
    kind, interrupt = events[-1]
    assert kind == "interrupt"

    second = restarted(make_runtime, first, ScriptedChatModel(final_text="Acknowledged."))
    assert second.instance_id != first.instance_id
    clock.advance(7 * 24 * 3600)  # the decision arrives a week later; nothing expires it
    with TestClient(make_app(second)) as b:
        state = b.get("/threads/r-1/state").json()
        assert state["pending"] == interrupt
        assert state["run_active"] is False
        # The proposal keeps its id across the restart, so a decision can still name it.
        decision = {"interrupt_id": interrupt["interrupt_id"], "decisions": [{"type": "approve"}]}
        events = stream(b, "/threads/r-1/resume", decision, sse_events)
        assert b.get("/threads/r-1/state").json()["pending"] is None

    assert events[-1] == ("end", {"status": "completed"})
    records = [r for r in caplog.records if r.name == ACTIONS]
    assert len(records) == 1
    assert "channel=#ops message=Nightly report is ready." in records[0].getMessage()
    assert any(
        kind == "message" and data["message"]["content"] == "Acknowledged." for kind, data in events
    )


def test_a_lease_left_by_a_dead_instance_blocks_until_it_expires(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
    clock: FakeClock,
) -> None:
    first = make_runtime(ScriptedChatModel(), instance_id="instance-a")
    ttl = first.settings.run_lock_ttl_seconds
    second = restarted(make_runtime, first, ScriptedChatModel(final_text="recovered"))
    with TestClient(make_app(second)) as b:
        # Instance A took the lease for a run and died without releasing it.
        assert b.portal.call(first.locks.acquire, "r-2", "instance-a:dead-run") is True

        refused = b.post("/threads/r-2/runs", json={"message": "hello"})
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "run_active"

        clock.advance(ttl - 1)
        assert b.post("/threads/r-2/runs", json={"message": "hello"}).status_code == 409

        clock.advance(2)
        events = stream(b, "/threads/r-2/runs", {"message": "hello"}, sse_events)
        assert b.get("/threads/r-2/state").json()["run_active"] is False

    assert events[-1] == ("end", {"status": "completed"})
