"""Telemetry of the requests the service answers (feature 011, User Story 4).

One span and one duration for every request, on the service's own address and on the one
other agents call, by ``RequestTelemetry`` (``api/telemetry.py``): the method, the route as
the service declares it, and the status code, and nothing a client wrote. A run stands under
the request that started it.
"""

import asyncio
import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanKind, StatusCode

from freyja_agent.api.telemetry import RequestTelemetry
from freyja_agent.runtime.telemetry import (
    ERROR_TYPE,
    HTTP_LISTENER,
    HTTP_METHOD,
    HTTP_ROUTE,
    HTTP_STATUS,
    MODEL_CALLER,
    MODEL_SPAN,
    REQUEST_DURATION,
    RUN_MODE,
    RUN_SPAN,
    Telemetry,
)
from tests.a2a_requests import VERSION, at_rest, rpc, sending
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import A2aApp, LlmApp, McpApp, ServerApp
from tests.fakes import ScriptedChatModel
from tests.telemetry_capture import Captured
from tests.unattended_helpers import ls
from tests.unattended_helpers import stream as read

# A made-up key for a caller, of the shape .gitleaks.toml lets through.
CALLER_KEY = "sk-recognizable-key-0001"
OUTSIDE = "https://freyja.example.com"
NAMES = {HTTP_METHOD, HTTP_ROUTE, HTTP_STATUS, HTTP_LISTENER}
NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)


def requests(captured: Captured) -> list[ReadableSpan]:
    return [span for span in captured.spans() if span.kind is SpanKind.SERVER]


def said(span: ReadableSpan) -> tuple[str, Any, Any, Any]:
    found = dict(span.attributes or {})
    return span.name, found.get(HTTP_METHOD), found.get(HTTP_ROUTE), found.get(HTTP_STATUS)


def one(spans: list[ReadableSpan]) -> ReadableSpan:
    assert len(spans) == 1, [span.name for span in spans]
    return spans[0]


def parent_of(span: ReadableSpan) -> int | None:
    return span.parent.span_id if span.parent is not None else None


def plain(captured: Captured, make_mcp_app: Callable[..., McpApp], **overrides: Any) -> McpApp:
    return make_mcp_app(
        ScriptedChatModel(script=[ls("l1")]), {}, telemetry=captured.telemetry, **overrides
    )


# What a request's span and duration hold.


def test_a_request_is_one_span_and_one_duration_by_method_route_and_status(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = plain(captured, make_mcp_app)
    with TestClient(made.app) as client:
        assert client.get("/threads").status_code == 200
        assert client.get("/threads/t-9/state").status_code == 404

    listed, read_state = requests(captured)
    assert said(listed) == ("GET /threads", "GET", "/threads", 200)
    # The route as the service declares it, never the path as it was written.
    assert said(read_state) == (
        "GET /threads/{thread_id}/state",
        "GET",
        "/threads/{thread_id}/state",
        404,
    )
    for span in (listed, read_state):
        assert set(span.attributes or {}) == NAMES
        assert (span.attributes or {})[HTTP_LISTENER] == "service"
        assert span.parent is None and span.status.status_code is StatusCode.UNSET
    points = captured.points(REQUEST_DURATION)
    assert sorted((point.attributes[HTTP_ROUTE], point.value) for point in points) == [
        ("/threads", 1),
        ("/threads/{thread_id}/state", 1),
    ]
    assert all(set(point.attributes) == NAMES for point in points)
    assert not any("t-9" in text for text in captured.texts())


def test_a_request_that_reaches_no_route_is_unmatched_whatever_it_asked_for(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = plain(captured, make_mcp_app)
    with TestClient(made.app) as client:
        nowhere = client.get("/no/such/zebra-path?secret=zebra-query")
        wrong_method = client.delete("/threads")
        other_host = client.get("/threads", headers={"host": "zebra.internal:8000"})
        assert client.request("ZEBRA", "/threads").status_code == 405

    assert [answer.status_code for answer in (nowhere, wrong_method, other_host)] == [
        404,
        405,
        403,
    ]
    assert [said(span) for span in requests(captured)] == [
        ("GET unmatched", "GET", "unmatched", 404),
        # The path has a route, which does not take the method: it is named by that route.
        ("DELETE /threads", "DELETE", "/threads", 405),
        # Refused for its host name before any route was looked for.
        ("GET unmatched", "GET", "unmatched", 403),
        # A method is a word a client writes: one that is no method is not repeated.
        ("_OTHER /threads", "_OTHER", "/threads", 405),
    ]
    assert not any("zebra" in text.lower() for text in captured.texts())


def test_the_routes_that_say_the_service_is_up_are_not_recorded(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = plain(captured, make_mcp_app)
    with TestClient(made.app) as client:
        assert client.get("/healthz").status_code == 200
        # Left out by its path, whether or not this build serves it.
        client.get("/readyz")

    assert requests(captured) == [] and captured.points(REQUEST_DURATION) == []


def test_a_request_the_application_fails_to_answer_is_a_500_by_the_error_s_class(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = Captured()
    made = plain(captured, make_mcp_app)

    async def broken() -> Any:
        raise RuntimeError("the zebra database said no")

    with TestClient(made.app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(made.runtime, "begin_run", broken)
        monkeypatch.setattr(made.runtime, "last_built", broken)
        answer = client.get("/tools")

    assert answer.status_code == 500
    span = one(requests(captured))
    assert said(span) == ("GET /tools", "GET", "/tools", 500)
    assert span.status.status_code is StatusCode.ERROR
    assert (span.attributes or {}).get(ERROR_TYPE) in {"RuntimeError", "TypeError", None}
    assert not any("zebra" in text for text in captured.texts())


def test_nothing_a_client_wrote_is_sent_and_nothing_it_receives_changes(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    on = plain(captured, make_mcp_app)
    off = make_mcp_app(ScriptedChatModel(script=[ls("l1")]), {})
    headers = {
        "x-zebra-header": "zebra-header-value",
        "authorization": f"Bearer {CALLER_KEY}",
        "user-agent": "zebra-agent",
        # A trace of the client's own: its id is not used (FR-028).
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    }
    answers = []
    for made in (on, off):
        with TestClient(made.app) as client:
            answers.append(client.get("/threads?zebra=1", headers=headers))

    with_telemetry, without = answers
    assert with_telemetry.status_code == without.status_code == 200
    assert with_telemetry.content == without.content
    assert sorted(with_telemetry.headers.items()) == sorted(without.headers.items())
    span = one(requests(captured))
    assert format(span.context.trace_id, "032x") != "0af7651916cd43dd8448eb211c80319c"
    assert span.parent is None
    texts = captured.texts()
    for part in ("zebra", "recognizable", "testserver", "testclient", "0af7651916cd43dd"):
        assert not any(part in text for text in texts), part


def test_a_stream_s_request_ends_when_the_stream_does(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = plain(captured, make_mcp_app)
    with TestClient(made.app) as client:
        events = read(client, "/threads/t-1/runs", {"message": "go"})

    assert events[-1] == ("end", {"status": "completed"})
    span = one(requests(captured))
    assert said(span) == (
        "POST /threads/{thread_id}/runs",
        "POST",
        "/threads/{thread_id}/runs",
        200,
    )
    run_span = one(captured.spans(RUN_SPAN))
    # The request lasted as long as its stream, which is as long as the run.
    assert span.start_time is not None and run_span.start_time is not None
    assert span.end_time is not None and run_span.end_time is not None
    assert span.start_time <= run_span.start_time and run_span.end_time <= span.end_time


def test_with_telemetry_off_a_request_is_served_and_nothing_is_kept(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        assert client.get("/threads").status_code == 200
        assert made.runtime.telemetry.on is False


async def test_an_application_with_no_runtime_yet_and_what_is_no_request_are_passed_on() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    async def receive() -> Any:
        return {}

    async def send(_message: Any) -> None:
        return None

    app = FastAPI()
    middleware = RequestTelemetry(inner)
    await middleware({"type": "lifespan", "app": app}, receive, send)
    # Before the lifespan has opened a runtime there is no telemetry to record with.
    request = {"type": "http", "path": "/x", "method": "GET", "app": app}
    await middleware(request, receive, send)

    assert seen == ["lifespan", "http"]


@pytest.mark.parametrize("begun", [True, False], ids=["a stream that had begun", "no answer yet"])
async def test_a_request_whose_client_leaves_is_ended_then(begun: bool) -> None:
    """FR-029: a stream's request ends when its reader leaves. One left before any answer
    began has no status code to name."""
    captured = Captured()

    async def leaving(scope: Any, receive: Any, send: Any) -> None:
        if begun:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"event: start", "more_body": True})
        # What the server does to the application when the client is gone.
        raise asyncio.CancelledError

    sent: list[Any] = []

    async def receive() -> Any:
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        sent.append(message)

    middleware = RequestTelemetry(leaving, telemetry=captured.telemetry)
    request = {"type": "http", "path": "/threads/t-1/runs", "method": "POST"}
    with pytest.raises(asyncio.CancelledError):
        await middleware(request, receive, send)

    span = one(requests(captured))
    found = dict(span.attributes or {})
    assert span.end_time is not None and span.status.status_code is StatusCode.UNSET
    assert ERROR_TYPE not in found
    assert found.get(HTTP_STATUS) == (200 if begun else None)
    # What was sent is what the application sent, untouched.
    assert len(sent) == (2 if begun else 0)
    point = one_point(captured)
    assert point.attributes.get(HTTP_STATUS) == (200 if begun else None)


def one_point(captured: Captured) -> Any:
    points = captured.points(REQUEST_DURATION)
    assert len(points) == 1, points
    return points[0]


def test_a_route_is_named_by_its_declared_path_whoever_matched_it() -> None:
    from starlette.routing import Route

    from freyja_agent.api.telemetry import declared_route

    async def card(_request: Any) -> Any:
        return None

    async def other(_request: Any) -> Any:
        return None

    app = FastAPI()
    app.router.routes.append(Route("/.well-known/{name}.json", card))

    # A route that is not FastAPI's is found by the endpoint the router wrote down.
    assert declared_route({"app": app, "endpoint": card}) == "/.well-known/{name}.json"
    # An endpoint of no route of the application, and no match at all, have no name.
    assert declared_route({"app": app, "endpoint": other}) is None
    assert declared_route({"app": app}) is None


# A run under the request that started it.


def test_a_run_stands_under_the_request_that_started_it(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[NOTIFY])
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        paused = read(client, "/threads/t-1/runs", {"message": "go"})
        pending = client.get("/threads/t-1/state").json()["pending"]
        body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "approve"}]}
        resumed = read(client, "/threads/t-1/resume", body)
        console = read(client, "/threads/t-2/runs/unattended", {"message": "go"})

    assert (paused[-1][0], resumed[-1][0], console[-1][0]) == ("interrupt", "end", "end")
    by_route = {(span.attributes or {})[HTTP_ROUTE]: span for span in requests(captured)}
    runs = captured.spans(RUN_SPAN)
    assert [(span.attributes or {})[RUN_MODE] for span in runs] == ["run", "resume", "run"]
    for run_span, route in zip(
        runs,
        (
            "/threads/{thread_id}/runs",
            "/threads/{thread_id}/resume",
            "/threads/{thread_id}/runs/unattended",
        ),
        strict=True,
    ):
        request = by_route[route]
        assert parent_of(run_span) == request.context.span_id
        assert run_span.context.trace_id == request.context.trace_id
    # Three requests started a run, and each is a trace of its own.
    assert len({span.context.trace_id for span in runs}) == 3


def test_a_caller_s_message_over_a2a_starts_a_run_under_its_request(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    captured = Captured()
    made = make_a2a_server_app(
        ScriptedChatModel(final_text="Hello, caller."), telemetry=captured.telemetry
    )
    with TestClient(made.app) as client:
        sent = rpc(client, "SendMessage", sending("Say hello."))
        assert sent.status_code == 200, sent.text
        at_rest(client, made, sent.json()["result"]["task"]["id"])

    request = next(
        span for span in requests(captured) if (span.attributes or {})[HTTP_ROUTE] == "/a2a"
    )
    run_span = one(captured.spans(RUN_SPAN))
    assert parent_of(run_span) == request.context.span_id


def test_a_background_run_is_a_trace_of_its_own(make_a2a_app: Callable[..., A2aApp]) -> None:
    from tests.test_a2a_pending import TASK as PENDING_TASK
    from tests.test_a2a_pending import handoff, pending_app, stub, tick, wait_for

    captured = Captured()
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")], final_text="The report is ready.")
    made = pending_app(make_a2a_app, model, agent, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        read(client, "/threads/b-1/runs", {"message": "go"})
        agent.release()
        wait_for(client, made.runtime, PENDING_TASK, "completed")
        tick(client, made.runtime)
        tick(client, made.runtime)

    first, background = captured.spans(RUN_SPAN)
    assert (background.attributes or {})[RUN_MODE] == "background"
    # No request started it: the worker did.
    assert background.parent is None
    assert background.context.trace_id != first.context.trace_id
    assert first.parent is not None


def test_a_profile_s_test_s_calls_stand_under_its_request(
    make_llm_app: Callable[..., LlmApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")
    captured = Captured()
    made = make_llm_app(model="openai:env-model", telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        assert client.post("/llm/profiles/environment/test").json()["ok"]

    request = one(requests(captured))
    assert said(request)[2] == "/llm/profiles/{profile_id}/test"
    calls = captured.spans(MODEL_SPAN)
    assert [(span.attributes or {})[MODEL_CALLER] for span in calls] == ["test", "test"]
    assert {parent_of(span) for span in calls} == {request.context.span_id}


# The address other agents call.


def test_the_outside_listener_s_requests_are_recorded_as_its_own(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    captured = Captured()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    made = make_a2a_server_app(
        ScriptedChatModel(final_text="Hi."),
        telemetry=captured.telemetry,
        a2a_server_callers={"alpha": {"token": CALLER_KEY}},
        a2a_server_host="127.0.0.1",
        a2a_server_port=port,
        a2a_server_url=OUTSIDE,
    )

    async def ask(method: str, path: str, **kwargs: Any) -> httpx.Response:
        transport = StreamingASGITransport(made.outside)
        async with httpx.AsyncClient(transport=transport, base_url=OUTSIDE) as http:
            answer = await http.request(method, path, **kwargs)
            await answer.aread()
            return answer

    with TestClient(made.app) as client:
        card = client.portal.call(lambda: ask("GET", "/.well-known/agent-card.json"))
        refused = client.portal.call(
            lambda: ask("POST", "/a2a", json={"jsonrpc": "2.0"}, headers=VERSION)
        )
        elsewhere = client.portal.call(lambda: ask("GET", "/threads"))

    assert (card.status_code, refused.status_code, elsewhere.status_code) == (200, 401, 404)
    found = [(said(span), (span.attributes or {})[HTTP_LISTENER]) for span in requests(captured)]
    assert found == [
        (
            ("GET /.well-known/agent-card.json", "GET", "/.well-known/agent-card.json", 200),
            "outside",
        ),
        (("POST /a2a", "POST", "/a2a", 401), "outside"),
        (("GET unmatched", "GET", "unmatched", 404), "outside"),
    ]
    assert not any("recognizable" in text or "alpha" in text for text in captured.texts())


def test_telemetry_that_fails_changes_no_answer(make_mcp_app: Callable[..., McpApp]) -> None:
    from tests.test_telemetry import _Broken

    for at in ("start", "end", "count"):
        broken: Any = _Broken(at)
        made = make_mcp_app(
            ScriptedChatModel(), {}, telemetry=Telemetry(broken, broken, providers=("on",))
        )
        with TestClient(made.app) as client:
            assert client.get("/threads").status_code == 200
            events = read(client, "/threads/t-1/runs", {"message": "go"})
        assert events[-1] == ("end", {"status": "completed"})
