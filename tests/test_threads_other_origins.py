"""Feature 007, FR-024, FR-027, SC-007: a page of another origin cannot use the thread routes.

Starting a run and deciding a proposal turn away a request that a browser marks as made by
a page of another origin, by the rule the LLM and Agents routes apply (``api/guards.py``).
A client that is no web page sends none of the headers a browser sends and is served as
before. Reads are not refused: the service gives no other origin leave to read an answer.
"""

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests.conftest import McpApp
from tests.fakes import ScriptedChatModel

NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)
MESSAGE = {"message": "hi"}

# What a browser says of a request made by a page of another origin: the site it came from,
# or an origin that is not the host the browser asked for.
OTHER_ORIGIN = [
    {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"},
    {"origin": "http://evil.example", "x-forwarded-host": "localhost:3000"},
    {"origin": "http://localhost:3001", "host": "localhost:3000"},
]
# What it says of a request the console made to its own origin, or the person made directly.
OWN = [
    {},
    {"sec-fetch-site": "same-origin"},
    {"sec-fetch-site": "none"},
    {"origin": "http://localhost:3000", "x-forwarded-host": "localhost:3000"},
]


def refused(response: Any) -> None:
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "other_origin"
    assert detail["message"].endswith(
        "Threads are changed from the console, or by a client that is not a web page."
    )


def pause(client: TestClient, thread: str, sse_events: Any) -> str:
    with client.stream("POST", f"/threads/{thread}/runs", json=MESSAGE) as response:
        events = sse_events(response.iter_lines())
    assert events[-1][0] == "interrupt"
    return events[-1][1]["interrupt_id"]


@pytest.mark.parametrize("headers", OTHER_ORIGIN)
def test_a_page_of_another_origin_cannot_start_a_run(
    make_mcp_app: Callable[..., McpApp], headers: dict[str, str]
) -> None:
    model = ScriptedChatModel()
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        refused(client.post("/threads/o-1/runs", json=MESSAGE, headers=headers))
        # Refused before anything else was looked at: no lease was taken, so the index was
        # told of no run, and there is no thread and no model call.
        assert client.portal.call(made.runtime.threads.get, "o-1") is None
        assert client.portal.call(made.runtime.locks.is_active, "o-1") is False
        assert client.get("/threads/o-1/state").status_code == 404
        assert client.get("/threads").json()["total"] == 0
    assert model.calls == 0


@pytest.mark.parametrize("headers", OTHER_ORIGIN)
def test_a_page_of_another_origin_cannot_decide_a_proposal(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, headers: dict[str, str]
) -> None:
    model = ScriptedChatModel(script=[NOTIFY])
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        interrupt_id = pause(client, "o-1", sse_events)
        before = client.get("/threads/o-1/state").json()
        body = {"interrupt_id": interrupt_id, "decisions": [{"type": "approve"}]}
        runs = client.portal.call(made.runtime.threads.get, "o-1").runs
        refused(client.post("/threads/o-1/resume", json=body, headers=headers))
        after = client.get("/threads/o-1/state").json()
        # No lease was taken for it: the index counts no further run.
        assert client.portal.call(made.runtime.threads.get, "o-1").runs == runs

    # Nothing ran because of it, and the proposal is still there to be decided.
    assert after == before
    assert after["pending"]["interrupt_id"] == interrupt_id
    assert model.calls == 1


@pytest.mark.parametrize("headers", OWN)
def test_the_console_and_a_client_that_is_no_page_are_served_as_before(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, headers: dict[str, str]
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."), {})
    with TestClient(made.app) as client:
        with client.stream("POST", "/threads/o-1/runs", json=MESSAGE, headers=headers) as response:
            assert response.status_code == 200, response.read()
            events = sse_events(response.iter_lines())
        body = {"interrupt_id": events[-1][1]["interrupt_id"], "decisions": [{"type": "approve"}]}
        with client.stream("POST", "/threads/o-1/resume", json=body, headers=headers) as response:
            assert response.status_code == 200, response.read()
            assert sse_events(response.iter_lines())[-1] == ("end", {"status": "completed"})


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None]
)
def test_a_body_that_is_not_sent_as_json_is_refused(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, content_type: str | None
) -> None:
    model = ScriptedChatModel(script=[NOTIFY])
    made = make_mcp_app(model, {})
    sent = {"content-type": content_type} if content_type else {}
    with TestClient(made.app) as client:
        started = client.post("/threads/o-1/runs", content=json.dumps(MESSAGE), headers=sent)
        assert started.status_code == 422, started.text
        assert client.get("/threads/o-1/state").status_code == 404

        interrupt_id = pause(client, "o-2", sse_events)
        body = {"interrupt_id": interrupt_id, "decisions": [{"type": "approve"}]}
        decided = client.post("/threads/o-2/resume", content=json.dumps(body), headers=sent)
        assert decided.status_code == 422, decided.text
        assert client.get("/threads/o-2/state").json()["pending"]["interrupt_id"] == interrupt_id
    assert model.calls == 1


@pytest.mark.parametrize("headers", OTHER_ORIGIN)
def test_reads_are_served_and_give_no_other_origin_leave_to_read_them(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, headers: dict[str, str]
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        with client.stream("POST", "/threads/o-1/runs", json=MESSAGE) as response:
            sse_events(response.iter_lines())
        for path in ("/threads", "/threads/o-1", "/threads/o-1/state"):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            # A browser keeps an answer from a page of another origin unless the service
            # says otherwise, and it never does.
            assert not any(name.startswith("access-control-") for name in response.headers)
