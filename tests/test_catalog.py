"""FR-014, SC-003: ``GET /tools`` lists every tool the orchestrator can call.

The catalog is built by the factory from the tools it compiled in, so what the service reports
and what the orchestrator was given cannot differ. Reading it contacts no server: a server's
status is the outcome of the most recent startup connection or call.
"""

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from freyja_agent.agent.catalog import OWN_TOOLS
from freyja_agent.runtime.runtime import Runtime
from tests.conftest import McpApp
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import STANDARD_TOOLS, STUB_ENV, make_stub

CONTRACT = (
    Path(__file__).resolve().parent.parent
    / "specs"
    / "002-mcp-tool-delegation"
    / "contracts"
    / "openapi.yaml"
)
STUB = {"stub": {"command": "stub-command", "read_only": ["lookup"]}}
# Freyja's own tools with no agent configured: check_agent_tasks comes with agents (003 FR-024).
OWN = {name: kind for name, kind in OWN_TOOLS.items() if name != "check_agent_tasks"}
TOOL_FIELDS = {
    "name",
    "server",
    # Added by feature 003: null for every tool that is not an agent's.
    "agent",
    "description",
    "input_schema",
    "classification",
    "allowed_decisions",
    "server_hints",
}
SERVER_FIELDS = {"name", "connection", "reachable", "checked_at", "error", "tool_count"}


def catalog_of(made: McpApp) -> dict[str, Any]:
    with TestClient(made.app) as client:
        response = client.get("/tools")
    assert response.status_code == 200, response.text
    return response.json()


def by_name(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in catalog["tools"]}


def test_the_catalog_matches_the_contract(make_mcp_app: Callable[..., McpApp]) -> None:
    contract = yaml.safe_load(CONTRACT.read_text())
    schema = {"$ref": "#/components/schemas/ToolCatalog", "components": contract["components"]}
    servers = {**STUB, "quiet": {"url": "http://127.0.0.1:8765/mcp"}}
    made = make_mcp_app(
        ScriptedChatModel(), servers, stubs={"quiet": make_stub("quiet", empty=True)}
    )
    catalog = catalog_of(made)
    errors = sorted(Draft202012Validator(schema).iter_errors(catalog), key=str)
    assert errors == [], [f"{list(error.absolute_path)}: {error.message}" for error in errors]
    assert all(set(tool) == TOOL_FIELDS for tool in catalog["tools"])
    assert all(set(server) == SERVER_FIELDS for server in catalog["servers"])


def test_the_catalog_lists_exactly_the_tools_the_model_was_offered(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(final_text="hello")
    made = make_mcp_app(model, STUB)
    with TestClient(made.app) as client:
        with client.stream("POST", "/threads/k-1/runs", json={"message": "hi"}) as response:
            sse_events(response.iter_lines())
        catalog = client.get("/tools").json()
    names = [tool["name"] for tool in catalog["tools"]]
    assert len(names) == len(set(names))
    assert sorted(names) == sorted(model.bound_tool_names)


def test_freyjas_own_tools_come_first_then_each_server_in_configuration_order(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    # "zulu" is configured before "alpha": the order is the configuration's, not the alphabet's.
    servers = {"zulu": {"command": "stub-command"}, "alpha": {"url": "http://localhost:9/mcp"}}
    stubs = {
        "zulu": make_stub("zulu", only=["ping", "lookup"]),
        "alpha": make_stub("alpha", only=["stats"]),
    }
    catalog = catalog_of(make_mcp_app(ScriptedChatModel(), servers, stubs=stubs))
    assert [(tool["name"], tool["server"]) for tool in catalog["tools"]] == [
        *((name, None) for name in OWN),
        # In the order the server lists them.
        ("lookup", "zulu"),
        ("ping", "zulu"),
        ("stats", "alpha"),
    ]
    assert [server["name"] for server in catalog["servers"]] == ["zulu", "alpha"]


def test_each_tool_has_the_classification_the_configuration_implies(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    tools = by_name(catalog_of(make_mcp_app(ScriptedChatModel(), STUB)))
    assert {name: tools[name]["classification"] for name in OWN} == {
        name: classification.value for name, classification in OWN.items()
    }
    assert tools["lookup"]["classification"] == "read_only"
    assert {tools[name]["classification"] for name in STANDARD_TOOLS if name != "lookup"} == {
        "gated"
    }
    # Decisions are listed only for a gated tool, and a tool with no arguments has no edit.
    for tool in tools.values():
        gated = tool["classification"] == "gated"
        assert bool(tool["allowed_decisions"]) is gated, tool["name"]
    assert tools["record_note"]["allowed_decisions"] == ["approve", "edit", "reject"]
    assert tools["ping"]["allowed_decisions"] == ["approve", "reject"]
    assert tools["send_notification"]["allowed_decisions"] == ["approve", "edit", "reject"]


def test_every_tool_shows_its_description_and_declared_input(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    tools = by_name(catalog_of(make_mcp_app(ScriptedChatModel(), STUB)))
    assert all(isinstance(tool["description"], str) for tool in tools.values())
    assert all(tool["input_schema"]["type"] == "object" for tool in tools.values())
    assert tools["lookup"]["description"].startswith("Look up the value stored under a key.")
    assert tools["lookup"]["input_schema"]["required"] == ["key"]
    assert set(tools["record_note"]["input_schema"]["properties"]) == {"note", "priority"}
    assert tools["ping"]["input_schema"]["properties"] == {}
    # Freyja's own tools show the arguments the model supplies, nothing injected.
    assert set(tools["send_notification"]["input_schema"]["properties"]) == {"channel", "message"}
    assert "path" in tools["ls"]["input_schema"]["properties"]


def test_a_servers_own_label_is_shown_and_never_used(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    tools = by_name(catalog_of(make_mcp_app(ScriptedChatModel(), STUB)))
    # The server calls this tool read-only; the operator did not list it, so it is gated.
    assert tools["record_note"]["classification"] == "gated"
    assert tools["record_note"]["server_hints"] == {"read_only": True}
    # Only the hints the server set are present.
    assert tools["lookup"]["server_hints"] == {"read_only": True, "idempotent": True}
    # A tool whose server publishes none shows none, and so do Freyja's own.
    assert tools["ping"]["server_hints"] is None
    assert all(tools[name]["server_hints"] is None for name in OWN)


def test_with_no_server_configured_only_freyjas_own_tools_appear(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        catalog = client.get("/tools").json()
    assert catalog["servers"] == []
    assert [tool["name"] for tool in catalog["tools"]] == list(OWN)
    assert all(tool["server"] is None for tool in catalog["tools"])
    notification = by_name(catalog)["send_notification"]
    assert notification["classification"] == "gated"


def test_each_configured_server_has_a_status(make_mcp_app: Callable[..., McpApp]) -> None:
    servers = {**STUB, "quiet": {"url": "http://127.0.0.1:8765/mcp"}}
    before = datetime.now().astimezone()
    made = make_mcp_app(
        ScriptedChatModel(), servers, stubs={"quiet": make_stub("quiet", empty=True)}
    )
    catalog = catalog_of(made)
    stub, quiet = catalog["servers"]
    assert stub == {
        "name": "stub",
        "connection": "command",
        "reachable": True,
        "checked_at": stub["checked_at"],
        "error": None,
        "tool_count": len(STANDARD_TOOLS),
    }
    # A server with no tools is still listed.
    assert (quiet["name"], quiet["connection"], quiet["tool_count"]) == ("quiet", "address", 0)
    assert quiet["reachable"] is True and quiet["error"] is None
    for server in (stub, quiet):
        checked_at = datetime.fromisoformat(server["checked_at"])
        assert checked_at.tzinfo is not None
        assert checked_at >= before


def test_the_catalog_shows_nothing_of_how_a_server_is_configured(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STUB_ENV, "zz-value-given-to-the-server")
    servers = {
        "launched": {
            "command": "zz-launch-command",
            "args": ["--zz-argument", "zz-argument-value"],
            "pass_env": [STUB_ENV],
            "read_only": ["lookup"],
        },
        "remote": {"url": "http://127.0.0.1:8765/zz-address-path"},
    }
    stubs = {
        "launched": make_stub("launched", only=["lookup"]),
        "remote": make_stub("remote", only=["ping"]),
    }
    with TestClient(make_mcp_app(ScriptedChatModel(), servers, stubs=stubs).app) as client:
        text = client.get("/tools").text
    # Which of the two forms a server uses is shown; nothing else about its entry is.
    assert '"connection":"command"' in text and '"connection":"address"' in text
    for hidden in (
        "zz-launch-command",
        "--zz-argument",
        "zz-argument-value",
        STUB_ENV,
        "zz-value-given-to-the-server",
        "127.0.0.1",
        "8765",
        "zz-address-path",
        "pass_env",
    ):
        assert hidden not in text, hidden


def test_reading_the_catalog_contacts_no_server(make_mcp_app: Callable[..., McpApp]) -> None:
    servers = {**STUB, "remote": {"url": "http://127.0.0.1:8765/mcp"}}
    stubs = {
        "stub": make_stub(only=["lookup", "record_note"]),
        "remote": make_stub("remote", only=["ping"]),
    }
    made = make_mcp_app(ScriptedChatModel(), servers, stubs=stubs)
    with TestClient(made.app) as client:
        connections = {name: link.connections for name, link in made.links.items()}
        first = client.get("/tools").json()
        started = time.monotonic()
        for _ in range(5):
            assert client.get("/tools").json() == first
        elapsed = time.monotonic() - started
        # A server at an address is connected per use, so a read that contacted it would show.
        assert {name: link.connections for name, link in made.links.items()} == connections
    assert all(stub.calls == [] for stub in made.stubs.values())
    # Five reads, each well inside the one second a single read is allowed (SC-003).
    assert elapsed < 1
