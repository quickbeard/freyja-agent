"""003 FR-002, FR-003, FR-009, SC-005: agents are read at startup or startup fails naming them.

Every case runs against the stand-in agent inside the test process, through the streaming
transport. A failure is raised before the app serves anything, names the agent, and never
contains the configured address.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from freyja_agent.agent.catalog import Classification
from freyja_agent.delegates.a2a_agents import AgentStartupError, pinned_card
from tests.a2a_stub import ELSEWHERE, make_stub, stub_card
from tests.conftest import A2aApp
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import make_stub as make_mcp_stub

AGENTS_LOGGER = "freyja_agent.delegates.a2a_agents"
URL = "http://127.0.0.1:9999"


def start(made: A2aApp) -> None:
    with TestClient(made.app):
        pass


def refused(made: A2aApp) -> str:
    with pytest.raises(AgentStartupError) as raised:
        start(made)
    text = str(raised.value)
    assert "127.0.0.1:9999" not in text
    return text


def test_cards_are_read_and_both_tools_reach_the_model(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=AGENTS_LOGGER)
    model = ScriptedChatModel(final_text="hello")
    made = make_a2a_app(model, {"hello": {"read_only": True}, "stub": {}})
    with (
        TestClient(made.app) as client,
        client.stream("POST", "/threads/a-1/runs", json={"message": "hi"}) as response,
    ):
        events = sse_events(response.iter_lines())
        runtime = made.runtime

    assert events[-1] == ("end", {"status": "completed"})
    expected = {"handoff_to_hello", "cancel_hello_task", "handoff_to_stub", "cancel_stub_task"}
    assert expected <= set(model.bound_tool_names)
    assert [link.name for link in runtime.agents] == ["hello", "stub"]
    assert all(link.reachable and link.error is None for link in runtime.agents)
    classes = {tool.name: tool.classification for tool in runtime.catalog if tool.agent}
    assert classes == {
        "handoff_to_hello": Classification.READ_ONLY,
        "cancel_hello_task": Classification.READ_ONLY,
        "handoff_to_stub": Classification.GATED,
        "cancel_stub_task": Classification.GATED,
    }
    lines = [r.getMessage() for r in caplog.records if r.name == AGENTS_LOGGER]
    assert "Agent hello: read-only, 2 skills" in lines
    assert "Agent stub: gated, 2 skills" in lines
    # Reading the card is all startup does: nothing was sent to either agent.
    assert all(stub.record.messages == [] for stub in made.stubs.values())
    assert all(
        {r.path for r in transport.requests} == {"/.well-known/agent-card.json"}
        for transport in made.transports.values()
    )


def test_an_agent_with_no_skills_starts(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=AGENTS_LOGGER)
    made = make_a2a_app(
        ScriptedChatModel(),
        {"bare": {}},
        stubs={"bare": make_stub("bare", card=stub_card("bare", skills=False))},
    )
    start(made)
    assert "Agent bare: gated, 0 skills" in [r.getMessage() for r in caplog.records]


def test_an_agent_that_is_not_running_stops_startup(make_a2a_app: Callable[..., A2aApp]) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}})
    made.transports["hello"].down = True
    text = refused(made)
    assert text.startswith("Agent 'hello': its card could not be read")


def test_an_agent_that_never_answers_stops_startup_within_the_limit(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}}, a2a_startup_timeout_seconds=0.3)
    made.transports["hello"].hang = True
    began = time.monotonic()
    text = refused(made)
    assert time.monotonic() - began < 5
    assert text == "Agent 'hello': its card was not read within 0.3 seconds."


def test_a_redirected_card_is_not_followed(make_a2a_app: Callable[..., A2aApp]) -> None:
    stub = make_stub(
        "hello", redirect_card_to="http://elsewhere.example/.well-known/agent-card.json"
    )
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}}, stubs={"hello": stub})
    text = refused(made)
    assert text.startswith("Agent 'hello': its card could not be read")
    assert "307" in text
    assert [r.host for r in made.transports["hello"].requests] == ["127.0.0.1"]


@pytest.mark.parametrize(
    ("card", "reason"),
    [
        (stub_card("hello", security=True), "its card requires authentication"),
        (stub_card("hello", input_modes=("image/png",)), "its card does not accept text messages"),
        (stub_card("hello", binding="HTTP+JSON"), "its card offers no JSON-RPC interface"),
    ],
    ids=["authentication", "no-text", "no-json-rpc"],
)
def test_a_card_freyja_cannot_use_stops_startup(
    make_a2a_app: Callable[..., A2aApp], card: Any, reason: str
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(), {"hello": {}}, stubs={"hello": make_stub("hello", card=card)}
    )
    assert refused(made).startswith(f"Agent 'hello': {reason}")


def test_an_agent_named_like_a_tool_server_stops_startup(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(), {"git": {}}, servers={"git": {"command": "stub-command"}}
    )
    text = refused(made)
    assert text == "Agent 'git': the name is also a tool server's name."
    assert made.transports["git"].requests == []


def test_an_agent_tool_name_already_taken_stops_startup(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    server = make_mcp_stub("notes", extra={"handoff_to_hello": lambda: "taken"})
    made = make_a2a_app(
        ScriptedChatModel(),
        {"hello": {}},
        servers={"notes": {"command": "stub-command"}},
        server_stubs={"notes": server},
    )
    text = refused(made)
    assert (
        text == "Agent 'hello': the tool name 'handoff_to_hello' is already used by another tool."
    )


def test_every_failing_agent_is_named(make_a2a_app: Callable[..., A2aApp]) -> None:
    made = make_a2a_app(
        ScriptedChatModel(),
        {"one": {}, "two": {}},
        stubs={"two": make_stub("two", card=stub_card("two", security=True))},
    )
    made.transports["one"].down = True
    text = refused(made)
    assert "Agent 'one': its card could not be read" in text
    assert "Agent 'two': its card requires authentication" in text


def test_the_card_address_is_replaced_by_the_configured_one(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    card = stub_card("far", url=ELSEWHERE)
    made = make_a2a_app(
        ScriptedChatModel(), {"far": {}}, stubs={"far": make_stub("far", card=card)}
    )
    with TestClient(made.app):
        (link,) = made.runtime.agents
        # The client the link built speaks to the configured host and port, at the card's path.
        assert link.client is not None
        # The SDK has no public accessor for the URL its transport was built with.
        assert link.client._transport.url == f"{URL}/a2a"
    assert {r.host for r in made.transports["far"].requests} == {"127.0.0.1"}
    pinned = pinned_card(card, "https://localhost:8443/base")
    assert [i.url for i in pinned.supported_interfaces] == ["https://localhost:8443/a2a"]
    assert [i.url for i in card.supported_interfaces] == [ELSEWHERE]
