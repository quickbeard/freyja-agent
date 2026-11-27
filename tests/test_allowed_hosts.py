"""A request made for a host name the service does not answer for is refused.

The case this is for: a page under a name its author controls has that name point at this
machine once it is loaded (DNS rebinding). The browser then takes the page and the service,
or the console, for one origin, so the page reads every answer and every header the refusal
of other origins reads says "the console's own". Features 004, 005, and 007 each recorded
that as left open. What the page cannot change is the name the request is made for.

The first part drives the middleware alone, the second the application as it is assembled,
every route of it.
"""

import asyncio
import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.routing import BaseRoute, Route

from freyja_agent.api.hosts import MESSAGE, AllowedHosts, host_of, is_address
from freyja_agent.config import Settings, settings
from tests.conftest import McpApp
from tests.fakes import ScriptedChatModel
from tests.unattended_helpers import NOTIFY

LISTED = "console.example.com"

# Served with nothing listed but ``LISTED``.
SERVED = [
    "localhost",
    "localhost:3000",
    "LocalHost:8000",
    "localhost.",
    "localhost.:8000",
    "127.0.0.1",
    "127.0.0.1:8000",
    "10.20.30.40:8000",
    "[::1]",
    "[::1]:8000",
    "[2001:DB8::1]:8000",
    LISTED,
    f"{LISTED}:443",
    "Console.Example.COM",
    f"{LISTED}.",
    f"  {LISTED}  ",
]

# Refused: another name, however much it looks like one that is served, and anything that is
# no host and port.
REFUSED = [
    "attacker.example",
    "attacker.example:8000",
    "localhost.attacker.example",
    "127.0.0.1.attacker.example",
    "127.0.0.1.nip.io",
    f"{LISTED}.attacker.example",
    f"sub.{LISTED}",
    f"x{LISTED}",
    "example.com",
    "localhost@attacker.example",
    "attacker.example@localhost",
    "localhost:80a",
    "localhost:3000:1",
    "localhost:",
    ":8000",
    "",
    " ",
    "127.1",
    "2130706433",
    "0x7f.0.0.1",
    "::1",
    "[::1",
    "[::1]x",
    "[::1]:x",
    "[localhost]",
    "[attacker.example]:8000",
    "[]",
    "[::1%attacker.example]",
    "[fe80::1%25eth0]:8000",
    "localhost/attacker.example",
    "localhost, attacker.example",
]


def small_app(names: Iterable[str] = (LISTED,)) -> FastAPI:
    app = FastAPI()

    @app.get("/things")
    def read() -> dict[str, Any]:
        return {"things": []}

    @app.post("/things")
    def change() -> dict[str, Any]:
        return {"changed": True}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(AllowedHosts, names=names)
    return app


def refused(response: Any) -> None:
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": {"code": "unknown_host", "message": MESSAGE}}


@pytest.mark.parametrize("host", SERVED)
def test_a_request_for_this_machine_an_address_or_a_listed_name_is_served(host: str) -> None:
    client = TestClient(small_app())
    assert client.get("/things", headers={"host": host}).status_code == 200
    assert client.post("/things", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("host", REFUSED)
def test_a_request_for_any_other_name_is_refused_a_read_included(host: str) -> None:
    client = TestClient(small_app())
    refused(client.get("/things", headers={"host": host}))
    refused(client.post("/things", headers={"host": host}))


@pytest.mark.parametrize(
    "forwarded",
    [
        "attacker.example:3000",
        "localhost:3000, attacker.example",
        "attacker.example, localhost:3000",
        f"{LISTED},attacker.example",
        "localhost:3000:1",
    ],
)
def test_the_name_the_console_was_asked_under_is_held_to_the_list_too(forwarded: str) -> None:
    """The console's server forwards with ``Host`` naming the service."""
    client = TestClient(small_app())
    headers = {"host": "127.0.0.1:8000", "x-forwarded-host": forwarded}
    refused(client.get("/things", headers=headers))
    refused(client.post("/things", headers=headers))


@pytest.mark.parametrize(
    "forwarded", ["localhost:3000", f"{LISTED}", "127.0.0.1:3000, localhost:3000", "", " , "]
)
def test_a_request_the_console_forwards_for_a_served_name_is_served(forwarded: str) -> None:
    client = TestClient(small_app())
    headers = {"host": "127.0.0.1:8000", "x-forwarded-host": forwarded}
    assert client.post("/things", headers=headers).status_code == 200


def test_a_forwarded_name_does_not_stand_in_for_the_host() -> None:
    """A page that reaches the service directly can write ``X-Forwarded-Host`` itself."""
    client = TestClient(small_app())
    headers = {"host": "attacker.example:8000", "x-forwarded-host": "localhost:3000"}
    refused(client.get("/things", headers=headers))


def ask(app: Any, headers: list[tuple[bytes, bytes]], kind: str = "http") -> list[dict[str, Any]]:
    """Hand ``app`` one request with exactly ``headers``, which no HTTP client would send."""
    scope = {
        "type": kind,
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http" if kind == "http" else "ws",
        "path": "/things",
        "raw_path": b"/things",
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
        "extensions": {"websocket.http.response": {}},
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"x-forwarded-host", b"localhost:3000")],
        [(b"host", b"localhost:8000"), (b"host", b"attacker.example")],
        [(b"host", b"attacker.example"), (b"host", b"localhost:8000")],
        [(b"Host", b"attacker.example")],
        [
            (b"host", b"localhost:8000"),
            (b"x-forwarded-host", b"localhost:3000"),
            (b"X-Forwarded-Host", b"attacker.example"),
        ],
    ],
    ids=["no host", "forwarded only", "second host", "first host", "capitals", "second forwarded"],
)
def test_a_request_with_no_host_or_with_one_more_than_it_shows_is_refused(
    headers: list[tuple[bytes, bytes]],
) -> None:
    sent = ask(small_app(), headers)
    assert sent[0]["status"] == 403


@pytest.mark.parametrize("name", [b"host", b"Host", b"HOST"])
def test_a_request_with_one_served_host_passes_untouched(name: bytes) -> None:
    """A server hands header names over in lower case; one that did not is read the same."""
    sent = ask(small_app(), [(name, b"localhost:8000")])
    assert sent[0]["status"] == 200


def test_a_websocket_asked_under_another_name_is_refused_before_any_route() -> None:
    """The service has none today; one that is added is covered from its first day."""
    reached: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["type"])

    guarded = AllowedHosts(inner, names=[LISTED])
    sent = ask(guarded, [(b"host", b"attacker.example")], kind="websocket")
    assert reached == []
    assert sent[0]["status"] == 403
    ask(guarded, [(b"host", b"localhost:8000")], kind="websocket")
    assert reached == ["websocket"]


def test_what_is_not_a_request_passes() -> None:
    reached: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["type"])

    unused: Any = None
    asyncio.run(AllowedHosts(inner)({"type": "lifespan"}, unused, unused))
    assert reached == ["lifespan"]


def test_healthz_answers_under_any_name() -> None:
    client = TestClient(small_app())
    response = client.get("/healthz", headers={"host": "freyja.internal:8000"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    # Only that path: one that begins like it, or goes on from it, is held to the list.
    refused(client.get("/healthz/", headers={"host": "freyja.internal:8000"}))
    refused(client.get("/healthzz", headers={"host": "freyja.internal:8000"}))


def test_the_refusal_does_not_repeat_the_name_and_the_log_names_it_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(small_app())
    name = "a" * 200 + ".attacker.example"
    with caplog.at_level(logging.WARNING, logger="freyja_agent.api.hosts"):
        response = client.post("/things", headers={"host": name})
    refused(response)
    assert "attacker" not in response.text
    assert "FREYJA_ALLOWED_HOSTS" in response.json()["detail"]["message"]
    (record,) = [r for r in caplog.records if r.name == "freyja_agent.api.hosts"]
    line = record.getMessage()
    assert line.startswith("Refused POST '/things': ")
    # As much of the name as tells an operator what to list, and no more.
    assert "a" * 80 in line
    assert "a" * 81 not in line


def test_the_log_names_the_forwarded_name_when_that_is_what_was_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(small_app())
    headers = {"host": "127.0.0.1:8000", "x-forwarded-host": "localhost:3000, attacker.example"}
    with caplog.at_level(logging.WARNING, logger="freyja_agent.api.hosts"):
        refused(client.get("/things", headers=headers))
    (record,) = [r for r in caplog.records if r.name == "freyja_agent.api.hosts"]
    assert record.getMessage().endswith("does not list: 'attacker.example'")


def test_a_path_cannot_write_a_log_line_of_its_own(caplog: pytest.LogCaptureFixture) -> None:
    """The path reaches the middleware with its escapes undone, a line break included."""
    client = TestClient(small_app())
    forged = "/a%0AWARNING:%20freyja_agent.api.hosts:%20Refused%20GET%20/threads:%20forged"
    with caplog.at_level(logging.WARNING, logger="freyja_agent.api.hosts"):
        refused(client.get(forged, headers={"host": "attacker.example"}))
        refused(client.get("/" + "b" * 3000, headers={"host": "attacker.example"}))
    first, second = (r.getMessage() for r in caplog.records if r.name == "freyja_agent.api.hosts")
    assert "\n" not in first
    assert first.startswith("Refused GET '/a\\nWARNING")
    assert "b" * 79 in second
    assert "b" * 80 not in second


def test_nothing_is_logged_for_a_request_that_is_served(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(small_app())
    with caplog.at_level(logging.DEBUG, logger="freyja_agent.api.hosts"):
        assert client.get("/things", headers={"host": "localhost:8000"}).status_code == 200
    assert [r for r in caplog.records if r.name == "freyja_agent.api.hosts"] == []


@pytest.mark.parametrize(
    ("value", "host"),
    [
        ("localhost", "localhost"),
        ("LocalHost:8000", "localhost"),
        ("example.com.", "example.com"),
        ("example.com.:80", "example.com"),
        ("[::1]:8000", "[::1]"),
        ("[2001:DB8::1]", "[2001:db8::1]"),
        ("127.0.0.1:8000", "127.0.0.1"),
        ("", None),
        (".", None),
        (":80", None),
        ("a:b", None),
        ("a:1:2", None),
        ("a:", None),
        ("a:١٢", None),
        ("[::1", None),
        ("[::1]8000", None),
        ("[::1]:", None),
    ],
)
def test_the_host_a_value_names(value: str, host: str | None) -> None:
    assert host_of(value) == host


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("192.168.1.10", True),
        ("[::1]", True),
        ("[fe80::1]", True),
        ("[::ffff:127.0.0.1]", True),
        ("[fe80::1%eth0]", False),
        ("[::1%attacker.example]", False),
        ("localhost", False),
        ("127.1", False),
        ("127.0.0.1.example", False),
        ("256.0.0.1", False),
        ("1.2.3.4.5", False),
        ("[localhost]", False),
        ("[127.0.0.1]", False),
        ("[]", False),
        ("::1", False),
    ],
)
def test_an_address_is_told_from_a_name(host: str, expected: bool) -> None:
    assert is_address(host) is expected


# The setting.


def test_no_name_is_listed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FREYJA_ALLOWED_HOSTS")
    assert Settings().allowed_hosts == []


def test_the_names_are_read_as_one_json_list_and_kept_as_a_host_is_compared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREYJA_ALLOWED_HOSTS", '["Console.Example.com.", " freyja-agent ", "a_b"]')
    assert Settings().allowed_hosts == ["console.example.com", "freyja-agent", "a_b"]


@pytest.mark.parametrize(
    "entry",
    [
        "https://console.example.com",
        "console.example.com:3000",
        "console.example.com/tasks",
        "*.example.com",
        "*",
        "",
        " ",
        ".",
        "a..b",
        ".internal",
        "console example",
        "[::1]",
        "münchen.example",
    ],
)
def test_an_entry_that_is_no_host_name_stops_startup_without_being_repeated(entry: str) -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(allowed_hosts=["console.example.com", entry])
    said = str(raised.value)
    assert "each entry must be a host name alone" in said
    if len(entry.strip()) > 1:
        assert entry not in said


# The application as it is assembled.

MESSAGE_BODY = {"message": "Tell ops."}
# What a browser sends for a page that was loaded under the attacker's name, once that name
# points here: the request is the page's own origin's in every header.
REBOUND_DIRECT = {
    "host": "attacker.example:8000",
    "origin": "http://attacker.example:8000",
    "sec-fetch-site": "same-origin",
}
# The same page, loaded from the console: the console's server forwards its request.
REBOUND_CONSOLE = {
    "host": "127.0.0.1:8000",
    "x-forwarded-host": "attacker.example:3000",
    "origin": "http://attacker.example:3000",
    "sec-fetch-site": "same-origin",
}
REBOUND = [REBOUND_DIRECT, REBOUND_CONSOLE]


def _routes(routes: Iterable[BaseRoute]) -> Iterator[tuple[str, str]]:
    """Every method and path the application serves, through the routers it includes."""
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _routes(included.routes)
        elif isinstance(route, Route):
            for method in route.methods or ():
                yield method, route.path
        else:
            pytest.fail(f"a kind of route this walk does not know: {route!r}")


@pytest.mark.parametrize("headers", REBOUND)
def test_every_route_of_the_service_refuses_a_page_whose_name_was_pointed_here(
    make_mcp_app: Callable[..., McpApp], headers: dict[str, str]
) -> None:
    model = ScriptedChatModel()
    made = make_mcp_app(model, {})
    routes = sorted(set(_routes(made.app.routes)))
    paths = {path for _, path in routes}
    # The walk found the routes of every section, and the documentation FastAPI serves.
    assert {"/threads", "/tools", "/llm/profiles", "/agents", "/openapi.json", "/docs"} <= paths
    assert len(routes) > 20
    with TestClient(made.app) as client:
        for method, path in routes:
            response = client.request(
                method, path.replace("{", "").replace("}", ""), headers=headers
            )
            if path == "/healthz":
                assert response.status_code == 200
            elif method == "HEAD":
                assert response.status_code == 403, (method, path)
            else:
                assert response.status_code == 403, (method, path, response.text)
                assert response.json()["detail"]["code"] == "unknown_host", (method, path)
        # A path no route has is refused like the rest: nothing is learnt by asking.
        refused(client.get("/nothing-here", headers=headers))
    assert model.calls == 0


@pytest.mark.parametrize("headers", REBOUND)
def test_such_a_page_can_neither_hand_freyja_a_task_nor_read_one_nor_approve(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, headers: dict[str, str]
) -> None:
    model = ScriptedChatModel(script=[NOTIFY])
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        # A task of the console's own, paused on a proposal.
        with client.stream("POST", "/threads/h-1/runs/unattended", json=MESSAGE_BODY) as response:
            events = sse_events(response.iter_lines())
        assert events[-1][0] == "interrupt"
        before = client.get("/threads/h-1/state").json()
        runs = client.portal.call(made.runtime.threads.get, "h-1").runs
        approve = {
            "interrupt_id": events[-1][1]["interrupt_id"],
            "decisions": [{"type": "approve"}],
        }

        for path in ("/threads", "/threads/h-1", "/threads/h-1/state", "/agents", "/tools"):
            answer = client.get(path, headers=headers)
            refused(answer)
            assert "Tell ops." not in answer.text
        for path in ("/threads/h-1/resume", "/threads/h-1/resume/unattended"):
            refused(client.post(path, json=approve, headers=headers))
        for path in ("/threads/h-2/runs", "/threads/h-2/runs/unattended"):
            refused(client.post(path, json=MESSAGE_BODY, headers=headers))
        refused(client.post("/threads/h-1/stop", headers=headers))

        # Nothing ran because of it: the proposal still waits, no run was counted, and
        # there is no second thread.
        assert client.get("/threads/h-1/state").json() == before
        assert client.portal.call(made.runtime.threads.get, "h-1").runs == runs
        assert client.portal.call(made.runtime.threads.get, "h-2") is None
        assert client.portal.call(made.runtime.locks.is_active, "h-2") is False
        assert client.get("/threads").json()["total"] == 1
    assert model.calls == 1


@pytest.mark.parametrize("headers", REBOUND)
def test_the_same_request_is_served_once_its_name_is_listed(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    """Why the name is what decides: every other header of the request says "the console's
    own", so the refusal of other origins serves it. It is also how a deployment that is
    reached under a name is served."""
    monkeypatch.setattr(settings, "allowed_hosts", ["attacker.example"])
    made = make_mcp_app(ScriptedChatModel(final_text="Done."), {})
    with TestClient(made.app) as client:
        with client.stream(
            "POST", "/threads/h-1/runs/unattended", json=MESSAGE_BODY, headers=headers
        ) as response:
            assert response.status_code == 200, response.read()
            events = sse_events(response.iter_lines())
        assert events[-1][0] == "end"
        assert client.get("/threads", headers=headers).json()["total"] == 1


def test_with_nothing_listed_only_this_machine_and_addresses_are_served(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setting's default. The suite itself lists ``testserver`` (tests/conftest.py)."""
    monkeypatch.setattr(settings, "allowed_hosts", [])
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        refused(client.get("/tools"))
        assert client.get("/tools", headers={"host": "localhost:8000"}).status_code == 200
        assert client.get("/tools", headers={"host": "127.0.0.1:8000"}).status_code == 200
        assert client.get("/tools", headers={"host": "[::1]:8000"}).status_code == 200
        own = {"host": "127.0.0.1:8000", "x-forwarded-host": "localhost:3000"}
        assert client.get("/tools", headers=own).status_code == 200
