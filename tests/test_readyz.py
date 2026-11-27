"""``GET /readyz``: whether the service can do its work now, which is whether it reaches its
database.

``/healthz`` says the process is up. This route asks the database one statement under a time
limit, answers 200 or 503 with nothing of the connection, contacts no model, tool server, or
agent, and is held to the host names the service answers for like every route but
``/healthz``. The statement itself is asked of a real Postgres in
``tests/integration/test_postgres_runtime.py``.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from freyja_agent.api import app as app_module
from freyja_agent.runtime.runtime import Runtime
from tests.conftest import A2aApp
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import make_stub

# What a driver says when the database refuses: an address, a user, and a reason.
REFUSAL = (
    'connection to server at "db.internal.example" (10.1.2.3), port 5439 failed: FATAL: '
    'password authentication failed for user "freyja-zebra"'
)


def test_a_runtime_with_no_database_is_always_ready(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> None:
    runtime = make_runtime(ScriptedChatModel())
    with TestClient(make_app(runtime)) as client:
        response = client.get("/readyz")

    assert runtime.ping is None
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_it_is_ready_when_the_database_answers(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> None:
    asked: list[str] = []

    async def ping() -> None:
        asked.append("asked")

    runtime = make_runtime(ScriptedChatModel())
    runtime.ping = ping
    with TestClient(make_app(runtime)) as client:
        first = client.get("/readyz")
        second = client.get("/readyz")

    assert (first.status_code, first.json()) == (200, {"status": "ok"})
    assert second.status_code == 200
    # Asked each time: an answer is never kept from an earlier one.
    assert asked == ["asked", "asked"]


def test_it_is_not_ready_when_the_database_refuses_and_says_nothing_of_the_connection(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    async def ping() -> None:
        raise ConnectionError(REFUSAL)

    runtime = make_runtime(ScriptedChatModel())
    runtime.ping = ping
    with TestClient(make_app(runtime)) as client:
        response = client.get("/readyz")
        # The process is up all the same.
        alive = client.get("/healthz")

    assert (response.status_code, response.json()) == (503, {"status": "unavailable"})
    assert alive.status_code == 200
    lines = [record.getMessage() for record in caplog.records if record.name == app_module.__name__]
    assert lines == ["Not ready: the database did not answer (ConnectionError)"]
    for text in (response.text, caplog.text):
        for part in ("db.internal", "10.1.2.3", "5439", "zebra", "password"):
            assert part not in text


def test_a_database_that_does_not_answer_in_time_is_not_ready(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(app_module, "READY_SECONDS", 0.05)
    left: list[str] = []

    async def ping() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            left.append("left")

    runtime = make_runtime(ScriptedChatModel())
    runtime.ping = ping
    with TestClient(make_app(runtime)) as client:
        response = client.get("/readyz")

    assert (response.status_code, response.json()) == (503, {"status": "unavailable"})
    # The wait was given up, not left running.
    assert left == ["left"]
    assert "Not ready: the database did not answer (TimeoutError)" in caplog.text


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "10.0.0.7", "[::1]:8000", "localhost"])
def test_a_probe_by_address_is_answered(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI], host: str
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        response = client.get("/readyz", headers={"host": host})

    assert response.status_code == 200


def test_it_is_held_to_the_host_names_the_service_answers_for(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> None:
    """Unlike ``/healthz``: it says something of the deployment, so a page under a name
    pointed at this machine is refused it like any other route."""
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        ready = client.get("/readyz", headers={"host": "freyja.internal:8000"})
        alive = client.get("/healthz", headers={"host": "freyja.internal:8000"})

    assert (ready.status_code, ready.json()["detail"]["code"]) == (403, "unknown_host")
    assert alive.status_code == 200


def test_it_contacts_no_model_tool_server_or_agent(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    model = ScriptedChatModel()
    server = make_stub("work")
    made = make_a2a_app(
        model,
        {"hello": {"read_only": True}},
        servers={"work": {"command": "stub", "read_only": ["lookup"]}},
        server_stubs={"work": server},
    )
    with TestClient(made.app) as client:
        agent = made.stubs["hello"]
        before: tuple[Any, ...] = (model.calls, len(server.calls), len(agent.record.messages))
        response = client.get("/readyz")
        after = (model.calls, len(server.calls), len(agent.record.messages))

    assert response.status_code == 200
    assert before == after == (0, 0, 0)
