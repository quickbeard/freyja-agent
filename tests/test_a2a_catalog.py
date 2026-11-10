"""003 User Story 5: the catalog lists the agents Freyja can hand off to.

FR-024, SC-007. ``GET /tools`` gains ``agents``, one entry per configured agent with its
reachability and its card's claims, and every tool names its ``agent``. Everything is read from
memory: reading the catalog contacts no agent and never shows an agent's address.
"""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from langchain_core.messages import AIMessage

from freyja_agent.agent.catalog import OWN_TOOLS
from tests.a2a_stub import make_stub, stub_card
from tests.conftest import A2aApp, McpApp
from tests.fakes import ScriptedChatModel

CONTRACT = (
    Path(__file__).resolve().parent.parent
    / "specs"
    / "003-a2a-agent-delegation"
    / "contracts"
    / "openapi.yaml"
)
# Freyja's own tools as feature 002 listed them: check_agent_tasks comes with agents only.
OWN_BEFORE_003 = [
    "ls",
    "read_file",
    "glob",
    "grep",
    "write_file",
    "edit_file",
    "delete",
    "task",
    "send_notification",
]
TOOL_FIELDS = {
    "name",
    "server",
    "agent",
    "description",
    "input_schema",
    "classification",
    "allowed_decisions",
    "server_hints",
}
AGENT_FIELDS = {"name", "reachable", "checked_at", "error", "card"}
# Not 127.0.0.1:9999, the stub card's own advertised address, so a match can only come from
# the configuration.
ADDRESS = "http://localhost:9877"
AGENTS = {"zeta": {"url": ADDRESS, "read_only": True}, "alpha": {"url": ADDRESS}}


def catalog(client: TestClient) -> dict[str, Any]:
    response = client.get("/tools")
    assert response.status_code == 200, response.text
    return response.json()


def by_name(listing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in listing["tools"]}


def two_agents(
    make_a2a_app: Callable[..., A2aApp], model: ScriptedChatModel | None = None
) -> A2aApp:
    return make_a2a_app(model or ScriptedChatModel(), AGENTS)


def test_each_agent_is_listed_in_configuration_order_with_its_cards_claims(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = two_agents(make_a2a_app)
    with TestClient(made.app) as client:
        listing = catalog(client)

    assert [agent["name"] for agent in listing["agents"]] == ["zeta", "alpha"]
    assert all(set(agent) == AGENT_FIELDS for agent in listing["agents"])
    zeta = listing["agents"][0]
    assert (zeta["reachable"], zeta["error"]) == (True, None)
    assert zeta["checked_at"]
    assert zeta["card"] == {
        "name": "Stub Agent zeta",
        "description": "A stand-in agent for Freyja's tests.",
        "version": "1.0.0",
        "skills": [
            {
                "id": "echo",
                "name": "Echo",
                "description": "Answers with what it was sent.",
                "tags": ["test"],
            },
            {
                "id": "report",
                "name": "Report",
                "description": "Writes a short report.",
                "tags": ["test"],
            },
        ],
    }


def test_agent_tools_name_their_agent_and_carry_the_entrys_classification(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = two_agents(make_a2a_app)
    with TestClient(made.app) as client:
        listing = catalog(client)

    tools = by_name(listing)
    assert all(set(tool) == TOOL_FIELDS for tool in listing["tools"])
    expected = {
        "handoff_to_zeta": ("zeta", "read_only", []),
        "cancel_zeta_task": ("zeta", "read_only", []),
        "handoff_to_alpha": ("alpha", "gated", ["approve", "edit", "reject"]),
        "cancel_alpha_task": ("alpha", "gated", ["approve", "reject"]),
    }
    for name, (agent, classification, decisions) in expected.items():
        tool = tools[name]
        assert (tool["agent"], tool["classification"], tool["allowed_decisions"]) == (
            agent,
            classification,
            decisions,
        ), name
        assert (tool["server"], tool["server_hints"]) == (None, None)
    # Freyja's own tools first, check_agent_tasks among them, then each agent's two tools.
    names = [tool["name"] for tool in listing["tools"]]
    assert names == [*OWN_TOOLS, *expected]
    assert all(tools[name]["agent"] is None for name in OWN_TOOLS)
    assert tools["check_agent_tasks"]["classification"] == "read_only"


def test_a_cards_read_only_claim_is_shown_as_its_own_and_changes_nothing(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    claim = "Read-only: this agent never changes anything."
    made = make_a2a_app(
        ScriptedChatModel(),
        {"boast": {"url": ADDRESS}},
        stubs={"boast": make_stub("boast", card=stub_card("boast", description=claim))},
    )
    with TestClient(made.app) as client:
        listing = catalog(client)

    (agent,) = listing["agents"]
    assert agent["card"]["description"] == claim
    tools = by_name(listing)
    assert tools["handoff_to_boast"]["classification"] == "gated"
    assert tools["cancel_boast_task"]["classification"] == "gated"


def test_an_agent_that_could_not_be_reached_is_shown_unavailable_with_its_tools_listed(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    handoff = AIMessage(
        content="",
        tool_calls=[{"id": "h1", "name": "handoff_to_zeta", "args": {"message": "hello"}}],
    )
    made = two_agents(make_a2a_app, ScriptedChatModel(script=[handoff]))
    with TestClient(made.app) as client:
        made.transports["zeta"].down = True
        with client.stream("POST", "/threads/cat-1/runs", json={"message": "go"}) as response:
            sse_events(response.iter_lines())
        listing = catalog(client)

    zeta, alpha = listing["agents"]
    assert zeta["reachable"] is False
    assert zeta["error"] and len(zeta["error"]) <= 500
    assert (alpha["reachable"], alpha["error"]) == (True, None)
    tools = by_name(listing)
    assert tools["handoff_to_zeta"]["classification"] == "read_only"
    assert tools["cancel_zeta_task"]["agent"] == "zeta"


def test_no_agents_address_appears_in_the_catalog(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    handoff = AIMessage(
        content="",
        tool_calls=[{"id": "h1", "name": "handoff_to_zeta", "args": {"message": "hello"}}],
    )
    made = two_agents(make_a2a_app, ScriptedChatModel(script=[handoff]))
    with TestClient(made.app) as client:
        before = client.get("/tools").text
        made.transports["zeta"].down = True
        with client.stream("POST", "/threads/cat-2/runs", json={"message": "go"}) as response:
            sse_events(response.iter_lines())
        after = client.get("/tools").text

    # The failure is shown, without the address it happened at.
    assert json.loads(after)["agents"][0]["reachable"] is False

    for text in (before, after):
        assert "localhost" not in text
        assert "9877" not in text


def test_reading_the_catalog_contacts_no_agent(make_a2a_app: Callable[..., A2aApp]) -> None:
    made = two_agents(make_a2a_app)
    with TestClient(made.app) as client:
        seen = {name: len(t.requests) for name, t in made.transports.items()}
        for _ in range(3):
            catalog(client)
        after = {name: len(t.requests) for name, t in made.transports.items()}
    assert after == seen


def test_without_agents_the_catalog_lists_the_tools_of_feature_002(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {"stub": {"command": "stub-command"}})
    with TestClient(made.app) as client:
        listing = catalog(client)

    assert listing["agents"] == []
    names = [tool["name"] for tool in listing["tools"]]
    assert names[: len(OWN_BEFORE_003)] == OWN_BEFORE_003
    assert "check_agent_tasks" not in names
    assert all(tool["server"] == "stub" for tool in listing["tools"][len(OWN_BEFORE_003) :])
    assert all(tool["agent"] is None for tool in listing["tools"])


def test_the_catalog_matches_the_contract(make_a2a_app: Callable[..., A2aApp]) -> None:
    contract = yaml.safe_load(CONTRACT.read_text())
    schema = {"$ref": "#/components/schemas/ToolCatalog", "components": contract["components"]}
    made = two_agents(make_a2a_app)
    with TestClient(made.app) as client:
        listing = catalog(client)
    errors = sorted(Draft202012Validator(schema).iter_errors(listing), key=str)
    assert errors == [], [f"{list(error.absolute_path)}: {error.message}" for error in errors]


def test_the_catalog_answers_within_a_second(make_a2a_app: Callable[..., A2aApp]) -> None:
    made = two_agents(make_a2a_app)
    with TestClient(made.app) as client:
        catalog(client)  # warm
        started = time.perf_counter()
        listing = catalog(client)
        elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    assert json.dumps(listing)
