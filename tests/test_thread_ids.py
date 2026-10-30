"""FR-001a: thread ids are 1 to 128 characters of letters, digits, hyphen, underscore, dot."""

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from freyja_agent.runtime.runtime import Runtime
from tests.fakes import ScriptedChatModel

# (method, path template, json body). US2 and US3 append their routes here.
ROUTES: list[tuple[str, str, dict | None]] = [
    ("POST", "/threads/{thread_id}/runs", {"message": "hello"}),
]

INVALID_IDS = [
    pytest.param("x" * 129, id="129-chars"),
    pytest.param("bad id", id="space"),
    pytest.param("bad!id", id="punctuation"),
    pytest.param("ünïcode", id="non-ascii"),
]

VALID_IDS = ["a", "x" * 128, "case-2026-10-30_0042.v1", "0123456789abcdef"]


@pytest.fixture
def client(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> TestClient:
    runtime = make_runtime(ScriptedChatModel())
    with TestClient(make_app(runtime)) as client:
        yield client


@pytest.mark.parametrize(("method", "template", "body"), ROUTES)
@pytest.mark.parametrize("thread_id", INVALID_IDS)
def test_invalid_thread_id_is_rejected_before_any_work(
    client: TestClient, method: str, template: str, body: dict | None, thread_id: str
) -> None:
    response = client.request(method, template.format(thread_id=thread_id), json=body)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(("method", "template", "body"), ROUTES)
def test_empty_thread_id_does_not_route(
    client: TestClient, method: str, template: str, body: dict | None
) -> None:
    response = client.request(method, template.format(thread_id=""), json=body)
    assert response.status_code in (404, 422)


@pytest.mark.parametrize("thread_id", VALID_IDS)
def test_valid_thread_ids_are_accepted(client: TestClient, thread_id: str) -> None:
    with client.stream("POST", f"/threads/{thread_id}/runs", json={"message": "hi"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        list(r.iter_lines())
