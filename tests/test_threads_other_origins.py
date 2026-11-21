"""Feature 007, FR-024, FR-027, SC-007: a page of another origin cannot use the thread routes.

Starting a run and deciding a proposal turn away a request that a browser marks as made by
a page of another origin, by the rule the LLM and Agents routes apply (``api/guards.py``).
A client that is no web page sends none of the headers a browser sends and is served as
before. Reads are not refused: the service gives no other origin leave to read an answer.

Part 2 (user story 6) holds the whole table: every thread route that changes something,
the three of the console's runs included, each refused before a lease is taken or a stop is
marked. The table is checked against the routes the application has and against the
feature's contract, so a route that is added without the refusal, or without a test here,
fails this file.

Not covered, here or by the rule: a request that reaches the service under a name an
attacker controls (DNS rebinding). The browser then calls the page's origin and the host it
asked for by the same name, and the rule has nothing to tell them apart by. Feature 004
left that open on purpose, and features 005 and 007 recorded that they inherit it. Such a
request is refused by the name it is made for, before any route (``api/hosts.py``), which
``tests/test_allowed_hosts.py`` covers.
"""

import json
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.routing import BaseRoute, Route

from tests.conftest import McpApp, parse_sse
from tests.fakes import LabelledChatModel, ScriptedChatModel
from tests.unattended_helpers import Reader, ls, stream

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
    # A sandboxed frame, or a page that is no address at all, names no origin.
    {"origin": "null"},
    # The service's own address is not the host the browser asked the console for.
    {
        "origin": "http://127.0.0.1:8000",
        "host": "127.0.0.1:8000",
        "x-forwarded-host": "localhost:3000",
    },
]
# What it says of a request the console made to its own origin, or the person made directly.
OWN = [
    {},
    {"sec-fetch-site": "same-origin"},
    {"sec-fetch-site": "none"},
    {"origin": "http://localhost:3000", "x-forwarded-host": "localhost:3000"},
    # What the console's own page sends, as the web app's server passes it on.
    {
        "sec-fetch-site": "same-origin",
        "origin": "http://localhost:3000",
        "host": "127.0.0.1:8000",
        "x-forwarded-host": "localhost:3000",
    },
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


# Part 2 (user story 6): every route that changes something, the console's runs included.

RUN = "/threads/{}/runs/unattended"
RESUME = "/threads/{}/resume/unattended"
STOP = "/threads/{}/stop"

# Every thread route that changes something. `SHOWN_BY`, at the end of the file, names for
# each the test that shows what a refusal of it leaves untouched.
CHANGING = {
    ("POST", "/threads/{thread_id}/runs"),
    ("POST", "/threads/{thread_id}/resume"),
    ("POST", "/threads/{thread_id}/runs/unattended"),
    ("POST", "/threads/{thread_id}/resume/unattended"),
    ("POST", "/threads/{thread_id}/stop"),
}
CONTRACT = Path(__file__).parents[1] / "specs" / "007-web-tasks" / "contracts" / "openapi.yaml"


READS = {"GET", "HEAD", "OPTIONS"}


def _routes(routes: Iterable[BaseRoute]) -> Iterator[tuple[frozenset[str], str]]:
    """Every route the application serves, with its methods, through the routers it includes.

    A kind of route this does not know fails the test that asks: a WebSocket route, which a
    browser lets any page open and which names no method, or a mounted application, whose
    routes would be served without being seen here.
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _routes(included.routes)
        elif isinstance(route, Route):
            yield frozenset(route.methods or ()), route.path
        else:
            raise AssertionError(
                f"{type(route).__name__} is a kind of route this file does not know: say "
                "here how a page of another origin is kept from it"
            )


def test_every_thread_route_that_changes_something_is_refused_in_this_file(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    served = {
        (method, path) for methods, path in _routes(made.app.routes) for method in methods - READS
    }
    # The walk above found what the application says of itself, and nothing less.
    described = {
        (method.upper(), path)
        for path, operations in made.app.openapi()["paths"].items()
        for method in operations
        if method.upper() not in READS
    }
    assert described <= served
    # A route added to the application is added to the table, with a test that refuses it.
    assert {route for route in served if route[1].startswith("/threads")} == CHANGING


def test_no_route_of_the_application_that_changes_something_serves_another_origin(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    """Also a route under another prefix, which could change a thread all the same."""
    made = make_mcp_app(ScriptedChatModel(), {})
    changing = sorted(
        (method, path) for methods, path in _routes(made.app.routes) for method in methods - READS
    )
    assert len(changing) > len(CHANGING)
    with TestClient(made.app) as client:
        for method, path in changing:
            address = re.sub(r"\{[^}]+\}", "x1", path)
            response = client.request(method, address, headers={"sec-fetch-site": "cross-site"})
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["detail"]["code"] == "other_origin", (method, path)


def _changing(paths: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    """The thread routes of an OpenAPI ``paths`` object that are not reads."""
    return {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/threads")
        for method in operations
        if method.upper() not in READS and method in {"post", "put", "patch", "delete"}
    }


def test_every_route_of_the_contract_that_changes_something_is_in_the_table() -> None:
    paths = yaml.safe_load(CONTRACT.read_text())["paths"]
    changing = _changing(paths)
    assert changing == {
        ("POST", "/threads/{thread_id}/runs/unattended"),
        ("POST", "/threads/{thread_id}/resume/unattended"),
        ("POST", "/threads/{thread_id}/stop"),
    }
    assert changing <= CHANGING
    # The contract promises the refusal for each of them.
    for method, path in changing:
        assert "403" in paths[path][method.lower()]["responses"], path


@pytest.mark.parametrize(("method", "path"), sorted(CHANGING))
def test_a_page_that_asks_first_is_given_no_permission(
    make_mcp_app: Callable[..., McpApp], method: str, path: str
) -> None:
    """A browser asks before it sends JSON across origins; the answer permits nothing."""
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        asked = client.options(
            path.replace("{thread_id}", "o-1"),
            headers={
                "origin": "http://evil.example",
                "access-control-request-method": method,
                "access-control-request-headers": "content-type",
            },
        )
        assert asked.status_code == 405, asked.text
        assert not any(name.startswith("access-control-") for name in asked.headers)
        assert client.portal.call(made.runtime.threads.get, "o-1") is None


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None]
)
def test_a_body_that_is_not_sent_as_json_is_refused_by_the_console_s_routes(
    make_mcp_app: Callable[..., McpApp], content_type: str | None
) -> None:
    """What a page can send without asking first, when a browser names neither header."""
    model = ScriptedChatModel(script=[NOTIFY])
    made = make_mcp_app(model, {})
    sent = {"content-type": content_type} if content_type else {}
    with TestClient(made.app) as client:
        started = client.post(RUN.format("o-1"), content=json.dumps(MESSAGE), headers=sent)
        assert started.status_code == 422, started.text
        assert client.portal.call(made.runtime.threads.get, "o-1") is None
        assert made.runtime.unattended.armed("o-1") is False
        assert client.get("/threads/o-1/state").status_code == 404

        paused = stream(client, RUN.format("o-2"), {"message": "Tell #ops."})
        interrupt_id = paused[-1][1]["interrupt_id"]
        body = {"interrupt_id": interrupt_id, "decisions": [{"type": "approve"}]}
        decided = client.post(RESUME.format("o-2"), content=json.dumps(body), headers=sent)
        assert decided.status_code == 422, decided.text
        assert client.get("/threads/o-2/state").json()["pending"]["interrupt_id"] == interrupt_id
    assert model.calls == 1


@pytest.mark.parametrize("headers", OWN)
def test_the_console_s_own_requests_to_its_routes_are_served(
    make_mcp_app: Callable[..., McpApp], headers: dict[str, str]
) -> None:
    model = LabelledChatModel(script=[NOTIFY, ls("l1")], final_text="Listed.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        with client.stream("POST", RUN.format("o-1"), json=MESSAGE, headers=headers) as response:
            assert response.status_code == 200, response.read()
            paused = list(parse_sse(response.iter_lines()))
        assert paused[-1][0] == "interrupt"
        body = {"interrupt_id": paused[-1][1]["interrupt_id"], "decisions": [{"type": "approve"}]}

        # The continuation is held in its next model call, and a stop is asked meanwhile.
        model.hold()
        reader = Reader(client, RESUME.format("o-1"), body, headers)
        reader.start()
        with model.held():
            stopped = client.post(STOP.format("o-1"), headers=headers)
            assert stopped.status_code == 202, stopped.text
        assert reader.finish()[-1] == ("end", {"status": "stopped"})


@pytest.mark.parametrize("headers", OTHER_ORIGIN)
def test_a_page_of_another_origin_cannot_start_a_console_run(
    make_mcp_app: Callable[..., McpApp], headers: dict[str, str]
) -> None:
    model = ScriptedChatModel()
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        refused(client.post(RUN.format("o-1"), json=MESSAGE, headers=headers))
        # Refused before anything else: no lease, no row, no run that goes on by itself.
        assert client.portal.call(made.runtime.threads.get, "o-1") is None
        assert client.portal.call(made.runtime.locks.is_active, "o-1") is False
        assert made.runtime.unattended.armed("o-1") is False
        assert client.get("/threads/o-1/state").status_code == 404
        assert client.get("/threads").json()["total"] == 0
    assert model.calls == 0


@pytest.mark.parametrize("headers", OTHER_ORIGIN)
def test_a_page_of_another_origin_cannot_decide_through_the_console_s_route(
    make_mcp_app: Callable[..., McpApp], headers: dict[str, str]
) -> None:
    model = ScriptedChatModel(script=[NOTIFY])
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        paused = stream(client, RUN.format("o-1"), {"message": "Tell #ops."})
        assert paused[-1][0] == "interrupt"
        interrupt_id = paused[-1][1]["interrupt_id"]
        before = client.get("/threads/o-1/state").json()
        runs = client.portal.call(made.runtime.threads.get, "o-1").runs
        body = {"interrupt_id": interrupt_id, "decisions": [{"type": "approve"}]}
        refused(client.post(RESUME.format("o-1"), json=body, headers=headers))
        after = client.get("/threads/o-1/state").json()
        # No lease was taken for it, and no run goes on by itself.
        assert client.portal.call(made.runtime.threads.get, "o-1").runs == runs
        assert made.runtime.unattended.armed("o-1") is False
        assert client.get("/threads").json()["needs_decision"] == 1

    # The action never ran, and the proposal is still there to be decided by a person.
    assert after == before
    assert after["pending"]["interrupt_id"] == interrupt_id
    assert model.calls == 1


@pytest.mark.parametrize("headers", OTHER_ORIGIN)
def test_a_page_of_another_origin_cannot_stop_a_run(
    make_mcp_app: Callable[..., McpApp], headers: dict[str, str]
) -> None:
    model = LabelledChatModel(script=[ls("l1")], final_text="Listed.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        model.hold()
        reader = Reader(client, RUN.format("o-1"), {"message": "List."})
        reader.start()
        with model.held():
            # A stop takes no body, so a form on any page could send it: what the browser
            # says of the request is all that turns it away.
            refused(client.post(STOP.format("o-1"), headers=headers))
            marked = client.portal.call(made.runtime.threads.get, "o-1")
            row = client.get("/threads/o-1").json()
        events = reader.finish()

    # No stop was marked: the run went on to its end, through the step the stop would
    # have kept from starting.
    assert marked.stop_owner is None
    assert (row["status"], row["stoppable"], row["stopping"]) == ("running", True, False)
    assert events[-1] == ("end", {"status": "completed"})
    assert model.calls == 2


def test_the_refusal_names_threads_and_repeats_nothing_that_was_sent(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    headers = {
        "sec-fetch-site": "cross-site",
        "origin": "http://recognizable-origin.example",
        "referer": "http://recognizable-origin.example/recognizable-page",
    }
    sent = ["recognizable-thread", "recognizable-message", "recognizable-origin", "int-recog"]
    bodies = {
        "runs": {"message": "recognizable-message"},
        "resume": {"interrupt_id": "int-recog", "decisions": [{"type": "approve"}]},
        "runs/unattended": {"message": "recognizable-message"},
        "resume/unattended": {"interrupt_id": "int-recog", "decisions": [{"type": "approve"}]},
        "stop": None,
    }
    assert {f"/threads/{{thread_id}}/{tail}" for tail in bodies} == {path for _, path in CHANGING}
    with TestClient(made.app) as client:
        for tail, body in bodies.items():
            response = client.post(
                f"/threads/recognizable-thread/{tail}", json=body, headers=headers
            )
            refused(response)
            # The same fixed sentence whatever was sent: nothing of the request is in it.
            assert response.json() == {
                "detail": {
                    "code": "other_origin",
                    "message": (
                        "This request was made by a page of another origin and was refused. "
                        "Threads are changed from the console, or by a client that is not a "
                        "web page."
                    ),
                }
            }
            assert not any(value in response.text for value in sent), tail
            assert not any(name.startswith("access-control-") for name in response.headers)


def test_a_body_that_cannot_be_read_as_json_is_answered_before_the_refusal(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    """The one answer that comes before the refusal, and it changes nothing either."""
    model = ScriptedChatModel()
    made = make_mcp_app(model, {})
    headers = {"content-type": "application/json", "sec-fetch-site": "cross-site"}
    with TestClient(made.app) as client:
        for path in (RUN, "/threads/{}/runs"):
            response = client.post(path.format("o-1"), content="{not json", headers=headers)
            assert response.status_code == 422, response.text
        assert client.portal.call(made.runtime.threads.get, "o-1") is None
        assert client.portal.call(made.runtime.locks.is_active, "o-1") is False
    assert model.calls == 0


# Each route of the table, with the test that shows what a refusal of it leaves untouched.
SHOWN_BY = {
    ("POST", "/threads/{thread_id}/runs"): test_a_page_of_another_origin_cannot_start_a_run,
    ("POST", "/threads/{thread_id}/resume"): (
        test_a_page_of_another_origin_cannot_decide_a_proposal
    ),
    ("POST", "/threads/{thread_id}/runs/unattended"): (
        test_a_page_of_another_origin_cannot_start_a_console_run
    ),
    ("POST", "/threads/{thread_id}/resume/unattended"): (
        test_a_page_of_another_origin_cannot_decide_through_the_console_s_route
    ),
    ("POST", "/threads/{thread_id}/stop"): test_a_page_of_another_origin_cannot_stop_a_run,
}


def test_every_route_of_the_table_has_a_test_of_what_its_refusal_leaves_untouched() -> None:
    assert set(SHOWN_BY) == CHANGING
