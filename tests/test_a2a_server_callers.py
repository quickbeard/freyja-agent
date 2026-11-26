"""009: who may call, and the settings of receiving tasks (contracts/settings.md).

Part 1, the settings the service reads from request 1 on: the switch, the two limits, and the
files directory. Each rule of the contract is one test, and no error repeats a value. With the
feature off the rules are not applied: those settings are then not used (contracts/
settings.md, "Off: every other variable here may be set and is not used").

Part 2, the settings of request 5: named callers and the outside listener. Part 3, the
credential at both addresses (FR-002, FR-005). Part 4, the outside listener (FR-003, FR-006,
FR-029). Part 5, callers that change (FR-021).
"""

import contextlib
import hmac
import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from freyja_agent.config import Settings
from freyja_agent.runtime import callers as callers_module
from tests.a2a_caller import StandInCaller, as_json
from tests.a2a_requests import VERSION, at_rest, rpc, sending
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import ServerApp
from tests.fakes import ScriptedChatModel
from tests.test_threads_other_origins import OTHER_ORIGIN

NAMES = (
    "FREYJA_A2A_SERVER",
    "FREYJA_A2A_SERVER_CALLER_TASKS",
    "FREYJA_A2A_SERVER_TASKS",
    "FREYJA_A2A_SERVER_FILES_DIR",
    "FREYJA_A2A_SERVER_WAIT_SECONDS",
    "FREYJA_A2A_SERVER_CALLERS",
    "FREYJA_A2A_SERVER_HOST",
    "FREYJA_A2A_SERVER_PORT",
    "FREYJA_A2A_SERVER_URL",
)


@pytest.fixture(autouse=True)
def no_feature_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def refused(monkeypatch: pytest.MonkeyPatch, workspace: Path, **values: str) -> str:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as caught:
        Settings(workspace_dir=str(workspace))
    return str(caught.value)


def test_the_feature_is_off_by_default_with_the_contract_s_defaults(workspace: Path) -> None:
    settings = Settings(workspace_dir=str(workspace))
    assert settings.a2a_server is False
    assert settings.a2a_server_caller_tasks == 5
    assert settings.a2a_server_tasks == 20
    assert settings.a2a_server_files_dir == "caller-files"


def test_the_switch_takes_a_boolean_only(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "on-please")
    with pytest.raises(ValidationError, match="a2a_server"):
        Settings(workspace_dir=str(workspace))
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    assert Settings(workspace_dir=str(workspace)).a2a_server is True


@pytest.mark.parametrize("value", ["0", "101", "-3"])
def test_a_caller_s_limit_is_1_to_100(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, value: str
) -> None:
    error = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_CALLER_TASKS=value)
    assert "FREYJA_A2A_SERVER_CALLER_TASKS" in error
    assert f"input_value='{value}'" not in error


@pytest.mark.parametrize(("caller", "total"), [("5", "4"), ("5", "1001")])
def test_the_total_limit_is_at_least_a_caller_s_and_at_most_1000(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, caller: str, total: str
) -> None:
    error = refused(
        monkeypatch,
        workspace,
        FREYJA_A2A_SERVER_CALLER_TASKS=caller,
        FREYJA_A2A_SERVER_TASKS=total,
    )
    assert "FREYJA_A2A_SERVER_TASKS" in error
    assert total not in error.replace("1000", "")


def test_limits_at_their_bounds_are_taken(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLER_TASKS", "100")
    monkeypatch.setenv("FREYJA_A2A_SERVER_TASKS", "1000")
    settings = Settings(workspace_dir=str(workspace))
    assert (settings.a2a_server_caller_tasks, settings.a2a_server_tasks) == (100, 1000)


@pytest.mark.parametrize("value", ["-1", "600.5", "nan"])
def test_a_send_waits_0_to_600_seconds(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, value: str
) -> None:
    error = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_WAIT_SECONDS=value)
    assert "FREYJA_A2A_SERVER_WAIT_SECONDS" in error
    assert f"input_value='{value}'" not in error


def test_a_wait_at_its_bounds_is_taken_and_30_by_default(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    assert Settings(workspace_dir=str(workspace)).a2a_server_wait_seconds == 30
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    for value in ("0", "600"):
        monkeypatch.setenv("FREYJA_A2A_SERVER_WAIT_SECONDS", value)
        assert Settings(workspace_dir=str(workspace)).a2a_server_wait_seconds == float(value)


@pytest.mark.parametrize("where", ["the workspace", "inside it", "around it"])
def test_the_files_directory_is_never_the_workspace_inside_it_or_around_it(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, where: str
) -> None:
    path = {
        "the workspace": workspace,
        "inside it": workspace / "callers",
        "around it": workspace.parent,
    }[where]
    error = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_FILES_DIR=str(path))
    assert "FREYJA_A2A_SERVER_FILES_DIR" in error
    assert str(path) not in error


def test_a_relative_files_directory_is_taken_from_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.chdir(workspace)
    # "." is the workspace itself once it is the working directory.
    assert "FREYJA_A2A_SERVER_FILES_DIR" in refused(
        monkeypatch, workspace, FREYJA_A2A_SERVER_FILES_DIR="."
    )
    # Beside the workspace, which is what the default gives in the service's own directory.
    monkeypatch.chdir(workspace.parent)
    monkeypatch.setenv("FREYJA_A2A_SERVER_FILES_DIR", "caller-files")
    settings = Settings(workspace_dir=str(workspace))
    assert settings.a2a_server and settings.a2a_server_files_dir == "caller-files"


def test_with_the_feature_off_the_rules_are_not_applied(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLER_TASKS", "0")
    monkeypatch.setenv("FREYJA_A2A_SERVER_TASKS", "-1")
    monkeypatch.setenv("FREYJA_A2A_SERVER_FILES_DIR", str(workspace))
    settings = Settings(workspace_dir=str(workspace))
    assert settings.a2a_server is False


# Part 2: the settings of named callers and of the outside listener (contracts/settings.md).

# The address that listens on every interface, as a deployment may set FREYJA_HOST.
EVERY_ADDRESS = ".".join(["0"] * 4)
# The two made-up tokens .gitleaks.toml lets through (research R15).
ALPHA = "sk-recognizable-key-0001"
OTHER = "sk-recognizable-other-0002"
LISTENER = {
    "FREYJA_A2A_SERVER_HOST": "127.0.0.1",
    "FREYJA_A2A_SERVER_PORT": "8443",
    "FREYJA_A2A_SERVER_URL": "https://freyja.example.com",
}


def named(**tokens: str) -> str:
    return json.dumps({name: {"token": token} for name, token in tokens.items()})


def clean_of(error: str, *values: str) -> None:
    """An error names settings and callers, never a token or a value."""
    for value in (ALPHA, OTHER, *values):
        assert value not in error, error


def test_callers_are_named_with_a_token_that_neither_prints_nor_serializes(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLERS", named(artex=ALPHA, heimdall=OTHER))
    settings = Settings(workspace_dir=str(workspace))
    assert sorted(settings.a2a_server_callers) == ["artex", "heimdall"]
    token = settings.a2a_server_callers["artex"].token
    assert token.get_secret_value() == ALPHA
    for shown in (repr(settings), str(settings), settings.model_dump_json(), repr(token)):
        clean_of(shown)
    assert Settings(workspace_dir=str(workspace)).a2a_server_host is None


def test_a_caller_holds_a_token_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    error = refused(
        monkeypatch,
        workspace,
        FREYJA_A2A_SERVER_CALLERS=json.dumps({"artex": {"token": ALPHA, "url": "x"}}),
    )
    # A shape error names the setting only: a key may be a token put in the wrong place.
    assert "FREYJA_A2A_SERVER_CALLERS" in error and "artex" not in error
    clean_of(error)


@pytest.mark.parametrize("name", ["Artex", "1artex", "a" * 33, "local", "art ex", "art.ex", ""])
def test_a_caller_s_name_is_an_agent_s_name_and_not_local(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, name: str
) -> None:
    error = refused(
        monkeypatch, workspace, FREYJA_A2A_SERVER_CALLERS=json.dumps({name: {"token": ALPHA}})
    )
    assert "FREYJA_A2A_SERVER_CALLERS" in error
    clean_of(error)


def test_a_caller_s_name_of_32_characters_is_taken(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLERS", named(**{"a" + "b-_9" * 7 + "cde": ALPHA}))
    assert len(next(iter(Settings(workspace_dir=str(workspace)).a2a_server_callers))) == 32


@pytest.mark.parametrize(
    "token",
    [
        "sk-only-15-chars",
        "x" * 4097,
        "sk-with a-space-0001",
        "sk-with-\x01-control",
        "sk-ünïcode-token-01",
    ],
    ids=["short", "long", "space", "control", "not-ascii"],
)
def test_a_token_is_16_to_4096_visible_ascii_characters(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, token: str
) -> None:
    given = token[:15] if token.startswith("sk-only-15") else token
    error = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_CALLERS=named(artex=given))
    assert "FREYJA_A2A_SERVER_CALLERS" in error and "artex" in error
    clean_of(error, given)


def test_tokens_at_their_bounds_are_taken(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLERS", named(a="!" * 16, b="~" * 4096))
    assert len(Settings(workspace_dir=str(workspace)).a2a_server_callers) == 2


def test_two_callers_with_one_token_and_more_than_20_are_refused(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    twice = refused(
        monkeypatch, workspace, FREYJA_A2A_SERVER_CALLERS=named(artex=ALPHA, heimdall=ALPHA)
    )
    assert "FREYJA_A2A_SERVER_CALLERS" in twice and "heimdall" in twice
    clean_of(twice)
    many = {f"caller-{n}": f"sk-recognizable-many-{n:04d}" for n in range(21)}
    too_many = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_CALLERS=named(**many))
    assert "FREYJA_A2A_SERVER_CALLERS" in too_many and "20" in too_many
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLERS", named(**dict(list(many.items())[:20])))
    assert len(Settings(workspace_dir=str(workspace)).a2a_server_callers) == 20


def test_a_caller_may_share_a_delegate_agent_s_name(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    """A caller and an agent are two namespaces: neither name gives anything of the other."""
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLERS", named(stub=ALPHA))
    monkeypatch.setenv("FREYJA_A2A_AGENTS", json.dumps({"stub": {"url": "http://127.0.0.1:9999"}}))
    settings = Settings(workspace_dir=str(workspace))
    assert "stub" in settings.a2a_server_callers and "stub" in settings.a2a_agents


@pytest.mark.parametrize("given", ["FREYJA_A2A_SERVER_HOST", "FREYJA_A2A_SERVER_PORT"])
def test_the_listener_s_host_and_port_go_together_also_with_the_feature_off(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, given: str
) -> None:
    monkeypatch.setenv(given, LISTENER[given])
    with pytest.raises(ValidationError) as caught:
        Settings(workspace_dir=str(workspace))
    error = str(caught.value)
    assert "FREYJA_A2A_SERVER_HOST" in error and "FREYJA_A2A_SERVER_PORT" in error
    clean_of(error, "8443")


def test_the_listener_needs_the_feature_a_caller_and_an_address_for_callers(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    for name, value in LISTENER.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as caught:
        Settings(workspace_dir=str(workspace))
    assert "FREYJA_A2A_SERVER" in str(caught.value) and "off" in str(caught.value)
    no_caller = refused(monkeypatch, workspace)
    assert "FREYJA_A2A_SERVER_CALLERS" in no_caller
    monkeypatch.delenv("FREYJA_A2A_SERVER_URL")
    no_url = refused(monkeypatch, workspace, FREYJA_A2A_SERVER_CALLERS=named(artex=ALPHA))
    assert "FREYJA_A2A_SERVER_URL" in no_url
    clean_of(no_url)
    monkeypatch.setenv("FREYJA_A2A_SERVER_URL", "https://freyja.example.com")
    settings = Settings(workspace_dir=str(workspace))
    assert (settings.a2a_server_host, settings.a2a_server_port) == ("127.0.0.1", 8443)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://freyja.example.com",
        "https://",
        "freyja.example.com",
        "https://freyja.example.com/a2a",
        "https://freyja.example.com/?x=1",
        "https://freyja.example.com#top",
        "https://user:secret-pass@freyja.example.com",
        "https://freyja.example.com:99999",
    ],
)
def test_the_address_for_callers_is_a_scheme_a_host_and_a_port_at_most(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, url: str
) -> None:
    error = refused(
        monkeypatch,
        workspace,
        **{**LISTENER, "FREYJA_A2A_SERVER_URL": url},
        FREYJA_A2A_SERVER_CALLERS=named(artex=ALPHA),
    )
    assert "FREYJA_A2A_SERVER_URL" in error
    clean_of(error, "secret-pass")


@pytest.mark.parametrize(
    "url", ["http://10.0.0.5:8443", "https://freyja.example.com", "https://[::1]:8443/"]
)
def test_an_address_for_callers_in_the_rule_is_taken(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, url: str
) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER", "true")
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLERS", named(artex=ALPHA))
    for name, value in {**LISTENER, "FREYJA_A2A_SERVER_URL": url}.items():
        monkeypatch.setenv(name, value)
    assert Settings(workspace_dir=str(workspace)).a2a_server_url == url


@pytest.mark.parametrize(
    ("host", "port"),
    [("127.0.0.1", "0"), ("127.0.0.1", "65536"), ("127.0.0.1", "8000"), (EVERY_ADDRESS, "8000")],
)
def test_the_listener_s_port_is_1_to_65535_and_not_the_service_s_own(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, host: str, port: str
) -> None:
    monkeypatch.setenv("FREYJA_HOST", "127.0.0.1")
    monkeypatch.setenv("FREYJA_PORT", "8000")
    error = refused(
        monkeypatch,
        workspace,
        **{**LISTENER, "FREYJA_A2A_SERVER_HOST": host, "FREYJA_A2A_SERVER_PORT": port},
        FREYJA_A2A_SERVER_CALLERS=named(artex=ALPHA),
    )
    assert "FREYJA_A2A_SERVER_PORT" in error
    clean_of(error)


def test_with_the_feature_off_callers_are_not_checked(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.setenv("FREYJA_A2A_SERVER_CALLERS", named(Bad=ALPHA, local=ALPHA))
    monkeypatch.setenv("FREYJA_A2A_SERVER_URL", "ftp://x")
    assert Settings(workspace_dir=str(workspace)).a2a_server is False


# Part 3: the credential (FR-002, FR-005), at both addresses.

UNAUTHORIZED = {
    "detail": {
        "code": "unauthorized",
        "message": (
            "This address takes a request only with a caller's bearer token in the "
            "Authorization header."
        ),
    }
}
OUTSIDE = "https://freyja.example.com"


def two_callers(make: Callable[..., ServerApp], model: Any = None, **overrides: Any) -> ServerApp:
    """An app with two named callers, ``alpha`` and ``beta``, and the outside listener set."""
    return make(
        model if model is not None else ScriptedChatModel(final_text="Hi."),
        a2a_server_callers={"alpha": {"token": ALPHA}, "beta": {"token": OTHER}},
        a2a_server_host="127.0.0.1",
        a2a_server_port=free_port(),
        a2a_server_url=OUTSIDE,
        **overrides,
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def bearer(token: str) -> dict[str, str]:
    return {**VERSION, "Authorization": f"Bearer {token}"}


def outside(
    client: TestClient, made: ServerApp, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    """A request to the outside listener's application, in the app's event loop."""

    async def go() -> httpx.Response:
        transport = StreamingASGITransport(made.outside)
        async with httpx.AsyncClient(transport=transport, base_url=OUTSIDE) as http:
            answer = await http.request(method, path, **kwargs)
            await answer.aread()
            return answer

    return client.portal.call(go)


def body(method: str = "SendMessage", params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 7, "method": method, "params": params or sending()}


def test_at_the_outside_listener_every_request_without_a_named_caller_s_token_is_refused_alike(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel(final_text="Hi.")
    made = two_callers(make_a2a_server_app, model)
    with TestClient(made.app) as client:
        tries = [
            outside(client, made, "POST", "/a2a", json=body(), headers=VERSION),
            outside(
                client, made, "POST", "/a2a", json=body(), headers=bearer("sk-no-such-caller-0000")
            ),
            outside(
                client,
                made,
                "POST",
                "/a2a",
                json=body(),
                headers={**VERSION, "Authorization": f"Basic {ALPHA}"},
            ),
            outside(client, made, "POST", "/a2a", json={**body(), "token": ALPHA}, headers=VERSION),
            outside(client, made, "POST", f"/a2a?token={ALPHA}", json=body(), headers=VERSION),
            outside(
                client, made, "POST", "/a2a", json=body(), headers={**VERSION, "X-Token": ALPHA}
            ),
            # Refused before its body is read: an oversized body is not answered 413.
            outside(client, made, "POST", "/a2a", content=b"x" * (300 * 1024), headers=VERSION),
        ]
        made_nothing = client.portal.call(made.received.unreleased, 100)
    for answer in tries:
        assert answer.status_code == 401, answer.text
        assert answer.headers["www-authenticate"] == "Bearer"
        assert answer.json() == UNAUTHORIZED
        assert ALPHA not in answer.text
    assert len({answer.content for answer in tries}) == 1
    assert made_nothing == []
    assert model.calls == 0


def test_at_the_service_s_own_address_no_header_is_local_a_token_is_its_caller_and_another_is_refused(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = two_callers(make_a2a_server_app)
    with TestClient(made.app) as client:
        local = rpc(client, "SendMessage", sending()).json()["result"]["task"]["id"]
        alpha = rpc(client, "SendMessage", sending(), bearer(ALPHA)).json()["result"]["task"]["id"]
        beta = rpc(client, "SendMessage", sending(), bearer(OTHER)).json()["result"]["task"]["id"]
        wrong = rpc(client, "SendMessage", sending(), bearer("sk-no-such-caller-0000"))
        scheme = rpc(
            client, "SendMessage", sending(), {**VERSION, "Authorization": f"Basic {ALPHA}"}
        )
        # A tenant naming another caller is not who calls.
        tenant = rpc(client, "SendMessage", {**sending(), "tenant": "beta"}, bearer(ALPHA)).json()
        owners = [made.task(client, task).caller for task in (local, alpha, beta)]
        tenant_owner = made.task(client, tenant["result"]["task"]["id"]).caller
    assert owners == ["local", "alpha", "beta"]
    assert tenant_owner == "alpha"
    for answer in (wrong, scheme):
        assert answer.status_code == 401
        assert answer.headers["www-authenticate"] == "Bearer"
        assert answer.json() == UNAUTHORIZED


def test_a_token_is_compared_with_every_caller_s_without_stopping_at_a_match(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    compared: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def counted(a: Any, b: Any) -> bool:
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(callers_module.hmac, "compare_digest", counted)
    made = two_callers(make_a2a_server_app)
    with TestClient(made.app) as client:
        rpc(client, "SendMessage", sending(), bearer(ALPHA))
    assert len(compared) == 2
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in compared)


@pytest.mark.parametrize("where", ["own", "outside"])
def test_the_order_of_the_checks_with_a_named_caller_s_token(
    make_a2a_server_app: Callable[..., ServerApp], where: str
) -> None:
    made = two_callers(make_a2a_server_app)
    big = json.dumps(body(params=sending("x" * (300 * 1024)))).encode()
    with TestClient(made.app) as client:

        def ask(headers: dict[str, str], content: bytes) -> Any:
            if where == "own":
                return client.post("/a2a", content=content, headers=headers)
            return outside(client, made, "POST", "/a2a", content=content, headers=headers)

        small = json.dumps(body()).encode()
        wrong_from_a_page = ask({**bearer("sk-no-such-caller-0000"), **OTHER_ORIGIN[0]}, small)
        wrong_and_big = ask(bearer("sk-no-such-caller-0000"), big)
        right_and_big = ask(bearer(ALPHA), big)
    assert wrong_from_a_page.status_code == 403
    assert wrong_from_a_page.json()["detail"]["code"] == "other_origin"
    assert wrong_and_big.status_code == 401
    assert right_and_big.status_code == 413


# Part 4: the outside listener (FR-003, FR-006, FR-029, SC-005).


def _paths(app: Any) -> list[tuple[str, str]]:
    found = [("GET", "/healthz")]
    for path, operations in app.openapi()["paths"].items():
        concrete = (
            path.replace("{thread_id}", "t-1")
            .replace("{profile_id}", "p")
            .replace("{agent_id}", "a")
            .replace("{id}", "x")
        )
        found += [(method.upper(), concrete) for method in operations]
    return found


def test_the_outside_listener_serves_the_card_and_the_operations_and_nothing_else(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = two_callers(make_a2a_server_app)
    with TestClient(made.app) as client:
        others = [
            (method, path, outside(client, made, method, path, headers=bearer(ALPHA)))
            for method, path in _paths(made.app)
        ]
        own_card = client.get("/.well-known/agent-card.json").json()
        card = outside(client, made, "GET", "/.well-known/agent-card.json").json()
        refused_page = [
            outside(client, made, "POST", "/a2a", json=body(), headers={**bearer(ALPHA), **headers})
            for headers in OTHER_ORIGIN
        ]
    for method, path, answer in others:
        assert answer.status_code in (404, 405), (method, path, answer.status_code)
        assert answer.json() == {"detail": "Not Found"} or answer.status_code == 405
    assert card["supportedInterfaces"][0]["url"] == f"{OUTSIDE}/a2a"
    assert card["securitySchemes"] == {"bearer": {"httpAuthSecurityScheme": {"scheme": "bearer"}}}
    assert card["securityRequirements"] == [{"schemes": {"bearer": {}}}]
    assert "securitySchemes" not in own_card and "securityRequirements" not in own_card
    assert {
        key: value
        for key, value in card.items()
        if not key.startswith("security") and key != "supportedInterfaces"
    } == {key: value for key, value in own_card.items() if key != "supportedInterfaces"}
    for answer in refused_page:
        assert answer.status_code == 403
        assert answer.json()["detail"]["code"] == "other_origin"


def test_a_named_caller_sends_through_the_outside_listener_and_its_task_is_its_own(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = two_callers(make_a2a_server_app)
    with TestClient(made.app) as client:

        async def work() -> Any:
            transport = StreamingASGITransport(made.outside)
            async with StandInCaller(OUTSIDE, transport=transport, token=ALPHA) as caller:
                return as_json(await caller.send("hello"))

        task = client.portal.call(work)
        owner = made.task(client, task["id"]).caller
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert owner == "alpha"


def test_the_outside_listener_binds_its_port_serves_and_stops_with_the_service(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    # The main server's access log, as uvicorn configures it, is left as it is.
    access = logging.getLogger("uvicorn.access")
    handler = logging.NullHandler()
    access.addHandler(handler)
    propagates = access.propagate
    model = ScriptedChatModel(
        script=[
            # The send completes; the stream then waits for a person.
            AIMessage(content="Hi."),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "n1",
                        "name": "send_notification",
                        "args": {"channel": "#ops", "message": "hi"},
                    }
                ],
            ),
        ],
        final_text="Hi.",
    )
    made = two_callers(make_a2a_server_app, model, listen=True)
    port = made.a2a.settings.a2a_server_port
    address = f"http://127.0.0.1:{port}"
    streaming: list[dict] = []
    opened = threading.Event()
    with TestClient(made.app):
        card = httpx.get(
            f"{address}/.well-known/agent-card.json", timeout=5, trust_env=False
        ).json()
        answer = httpx.post(
            f"{address}/a2a", json=body(), headers=bearer(ALPHA), timeout=30, trust_env=False
        ).json()
        refused = httpx.post(
            f"{address}/a2a", json=body(), headers=VERSION, timeout=5, trust_env=False
        )

        def follow() -> None:
            # A stream left open while the service stops: the task waits for a person. The
            # listener closes it once its grace period is over.
            with (
                contextlib.suppress(httpx.RemoteProtocolError),
                httpx.stream(
                    "POST",
                    f"{address}/a2a",
                    json=body("SendStreamingMessage", sending("Tell #ops.")),
                    headers=bearer(ALPHA),
                    timeout=30,
                    trust_env=False,
                ) as stream,
            ):
                for line in stream.iter_lines():
                    if line.startswith("data:"):
                        streaming.append(json.loads(line[5:]))
                        opened.set()

        reader = threading.Thread(target=follow, daemon=True)
        reader.start()
        assert opened.wait(10)
        # The stream is open when the service begins to stop.
        assert reader.is_alive()
        started = time.monotonic()
    stopped_in = time.monotonic() - started
    reader.join(10)
    assert card["supportedInterfaces"][0]["url"] == f"{OUTSIDE}/a2a"
    assert answer["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert refused.status_code == 401
    assert stopped_in < 15
    # Nothing listens there any more.
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"{address}/.well-known/agent-card.json", timeout=2, trust_env=False)
    assert any(
        f"127.0.0.1:{port}" in line and "outside listener is on" in line for line in caplog.messages
    )
    assert [r for r in caplog.records if r.name == "uvicorn.access"] == []
    assert handler in access.handlers
    assert access.propagate is propagates
    access.removeHandler(handler)


def test_a_port_that_cannot_be_bound_stops_startup_naming_the_setting(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        made = make_a2a_server_app(
            ScriptedChatModel(),
            a2a_server_callers={"alpha": {"token": ALPHA}},
            a2a_server_host="127.0.0.1",
            a2a_server_port=taken.getsockname()[1],
            a2a_server_url=OUTSIDE,
            listen=True,
        )
        with (
            pytest.raises(Exception, match="FREYJA_A2A_SERVER_PORT") as caught,
            TestClient(made.app),
        ):
            pass
    assert ALPHA not in str(caught.value)


@pytest.mark.parametrize(
    ("url", "warned"), [("http://10.0.0.5:8443", True), ("https://freyja.example.com", False)]
)
def test_startup_says_the_listener_is_on_and_warns_for_an_address_that_does_not_encrypt(
    make_a2a_server_app: Callable[..., ServerApp],
    caplog: pytest.LogCaptureFixture,
    url: str,
    warned: bool,
) -> None:
    caplog.set_level(logging.INFO)
    made = make_a2a_server_app(
        ScriptedChatModel(),
        a2a_server_callers={"alpha": {"token": ALPHA}, "beta": {"token": OTHER}},
        a2a_server_host="127.0.0.1",
        a2a_server_port=8443,
        a2a_server_url=url,
    )
    with TestClient(made.app):
        pass
    on = [
        r
        for r in caplog.records
        if "Receiving tasks from other agents over A2A is on" in r.getMessage()
    ]
    assert len(on) == 1 and on[0].levelno == logging.INFO
    assert "2 callers are named" in on[0].getMessage()
    assert "outside listener is on at 127.0.0.1:8443" in on[0].getMessage()
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "can be read on the network path" in r.getMessage()
    ]
    assert bool(warnings) is warned
    assert all(ALPHA not in line and OTHER not in line for line in caplog.messages)


# Part 5: callers that change (FR-021, Edge Cases).

NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)


def test_a_caller_no_longer_named_has_its_tasks_canceled_and_named_again_has_them_back(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    model = ScriptedChatModel(script=[NOTIFY, NOTIFY, NOTIFY], final_text="Noted.")
    made = make_a2a_server_app(
        model, a2a_server_callers={"alpha": {"token": ALPHA}, "beta": {"token": OTHER}}
    )
    waiting = {"configuration": {"returnImmediately": True}}
    with TestClient(made.app) as client:
        first = rpc(
            client,
            "SendMessage",
            {**sending("Tell #ops.", contextId="c-1"), **waiting},
            bearer(ALPHA),
        ).json()["result"]["task"]
        # The local caller's task and a still named caller's wait too, and are left alone.
        kept_ids = [
            rpc(
                client, "SendMessage", {**sending("Tell #ops.", contextId="c-2"), **waiting}, h
            ).json()["result"]["task"]["id"]
            for h in (VERSION, bearer(OTHER))
        ]
        at_rest(client, made, first["id"], *kept_ids)
        thread = made.task(client, first["id"]).thread_id
    removed = made.again(a2a_server_callers={"beta": {"token": OTHER}})
    with TestClient(removed.app) as client:
        task = until_ended(client, removed, first["id"])
        others = [removed.task(client, task_id).state for task_id in kept_ids]
        state = client.get(f"/threads/{thread}/state").json()
        row = client.get(f"/threads/{thread}").json()
        refused = rpc(client, "GetTask", {"id": first["id"]}, bearer(ALPHA))
    assert (task.state, task.reason) == ("canceled", "canceled")
    assert others == ["working", "working"]
    assert state["pending"] is None
    assert row["caller"] == {"name": "alpha", "canceled": True, "configured": False}
    assert refused.status_code == 401
    assert any("alpha" in line and "no longer named" in line for line in caplog.messages)
    again = made.again(a2a_server_callers={"alpha": {"token": ALPHA}})
    with TestClient(again.app) as client:
        read = rpc(client, "GetTask", {"id": first["id"]}, bearer(ALPHA)).json()["result"]
        follow_up = rpc(
            client, "SendMessage", sending("Again.", contextId="c-1"), bearer(ALPHA)
        ).json()
        row = client.get(f"/threads/{thread}").json()
        continued = again.task(client, follow_up["result"]["task"]["id"]).thread_id
    assert read["status"]["state"] == "TASK_STATE_CANCELED"
    # Its conversation, on its thread, again.
    assert continued == thread
    assert row["caller"]["configured"] is True
    replaced = made.again(a2a_server_callers={"alpha": {"token": OTHER}})
    with TestClient(replaced.app) as client:
        old = rpc(client, "GetTask", {"id": first["id"]}, bearer(ALPHA))
        new = rpc(client, "GetTask", {"id": first["id"]}, bearer(OTHER)).json()
    assert old.status_code == 401
    assert new["result"]["id"] == first["id"]
    assert follow_up["result"]["task"]["contextId"] == "c-1"


def until_ended(client: TestClient, made: ServerApp, task_id: str, seconds: float = 5) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = made.task(client, task_id)
        if found.ended and not client.portal.call(made.runtime.locks.is_active, found.thread_id):
            client.portal.call(made.service().close)
            return made.task(client, task_id)
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not end")


# What the review of request 5 found.


def test_callers_in_the_wrong_shape_are_refused_without_their_keys_and_ignored_when_off(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    twice = (
        '{"artex": {"token": "sk-recognizable-many-0001"}, '
        '"artex": {"token": "sk-recognizable-many-0002"}}'
    )
    for wrong in (json.dumps({ALPHA: "artex"}), "[1, 2]", "not json", twice):
        monkeypatch.setenv("FREYJA_A2A_SERVER_CALLERS", wrong)
        monkeypatch.delenv("FREYJA_A2A_SERVER", raising=False)
        assert Settings(workspace_dir=str(workspace)).a2a_server_callers == {}
        error = refused(monkeypatch, workspace)
        assert "FREYJA_A2A_SERVER_CALLERS" in error
        clean_of(error, "sk-recognizable-many-0001", "sk-recognizable-many-0002")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_the_listener_on_the_service_s_own_loopback_port_is_refused(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, host: str
) -> None:
    monkeypatch.setenv("FREYJA_HOST", "localhost")
    monkeypatch.setenv("FREYJA_PORT", "8000")
    error = refused(
        monkeypatch,
        workspace,
        **{**LISTENER, "FREYJA_A2A_SERVER_HOST": host, "FREYJA_A2A_SERVER_PORT": "8000"},
        FREYJA_A2A_SERVER_CALLERS=named(artex=ALPHA),
    )
    assert "FREYJA_A2A_SERVER_PORT" in error


def test_a_blank_listener_host_is_refused(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    error = refused(
        monkeypatch,
        workspace,
        **{**LISTENER, "FREYJA_A2A_SERVER_HOST": " "},
        FREYJA_A2A_SERVER_CALLERS=named(artex=ALPHA),
    )
    assert "FREYJA_A2A_SERVER_HOST" in error


def test_pages_of_another_origin_at_the_outside_listener_are_warned_of_sparingly(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = two_callers(make_a2a_server_app)
    page = {**VERSION, **OTHER_ORIGIN[0]}
    with TestClient(made.app) as client, caplog.at_level(logging.WARNING):
        answers = [
            outside(client, made, "POST", "/a2a", json=body(), headers=page) for _ in range(50)
        ]
    assert {answer.status_code for answer in answers} == {403}
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len([line for line in lines if "another origin" in line]) == 1


def test_a_removed_caller_s_tasks_are_canceled_once_the_store_answers(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_server_app(
        ScriptedChatModel(script=[NOTIFY]), a2a_server_callers={"alpha": {"token": ALPHA}}
    )
    with TestClient(made.app) as client:
        task_id = rpc(
            client,
            "SendMessage",
            {**sending("Tell #ops."), "configuration": {"returnImmediately": True}},
            bearer(ALPHA),
        ).json()["result"]["task"]["id"]
        at_rest(client, made, task_id)
    removed = made.again(a2a_server_callers={})
    real = removed.received.working_of_others
    failing = [True]

    async def once_down(callers: Any, limit: int) -> Any:
        if failing:
            failing.clear()
            raise RuntimeError("the store is down")
        return await real(callers, limit)

    monkeypatch.setattr(removed.received, "working_of_others", once_down)
    with TestClient(removed.app) as client:
        until(lambda: not failing)
        assert removed.task(client, task_id).state == "working"
        client.portal.call(removed.service().sweep)
        task = until_ended(client, removed, task_id)
    assert (task.state, task.reason) == ("canceled", "canceled")


def until(found: Callable[[], Any], seconds: float = 5) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = found()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("what the test waited for did not happen")
