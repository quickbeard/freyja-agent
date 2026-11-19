"""003 FR-002, FR-003, FR-009, SC-005: agents are read at startup; an invalid entry stops it.

Every case runs against the stand-in agent inside the test process, through the streaming
transport. An entry that is invalid in itself is refused before the app serves anything, in
an error that names the agent and never contains the configured address.

Since feature 005 an agent whose card cannot be read or used no longer stops startup (005
FR-020, research R22): the service starts, warns naming the agent and the reason, and lists
the agent as not reachable with that reason, still with both of its tools.
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
REGISTRY_LOGGER = "freyja_agent.delegates.agent_registry"
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


def started_without(
    made: A2aApp,
    caplog: pytest.LogCaptureFixture,
    sentences: dict[str, str],
    classification: Classification = Classification.GATED,
) -> None:
    """The service starts; each agent in ``sentences`` is warned about and listed with it.

    ``sentences`` gives, per agent, the card problem's sentence (005 FR-020, FR-021).
    """
    caplog.set_level(logging.WARNING)
    with TestClient(made.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        catalog = client.get("/tools")
    listed = {agent["name"]: agent for agent in catalog.json()["agents"]}
    tools = {tool["name"]: tool["classification"] for tool in catalog.json()["tools"]}
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == REGISTRY_LOGGER and r.levelno == logging.WARNING
    ]
    for name, sentence in sentences.items():
        # One warning for the agent, naming it and giving the reason.
        assert [line for line in warnings if f"Agent {name} " in line] == [
            f"Agent {name} cannot be used: {sentence}"
        ]
        assert listed[name]["reachable"] is False
        assert listed[name]["error"] == sentence
        # Which tools an agent has, and their gate, never depend on whether it answers.
        assert tools[f"handoff_to_{name}"] == classification.value
        assert tools[f"cancel_{name}_task"] == classification.value
    assert len(warnings) == len(sentences)
    said = "\n".join([catalog.text, *(r.getMessage() for r in caplog.records)])
    assert "127.0.0.1:9999" not in said


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


def test_an_agent_that_is_not_running_does_not_stop_startup(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}})
    made.transports["hello"].down = True
    started_without(made, caplog, {"hello": "Nothing answered at the address."})


def test_an_agent_that_never_answers_delays_startup_by_the_limit_and_no_more(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}}, a2a_startup_timeout_seconds=0.3)
    made.transports["hello"].hang = True
    began = time.monotonic()
    started_without(made, caplog, {"hello": "The agent did not answer within 0.3 seconds."})
    assert time.monotonic() - began < 5


def test_a_redirected_card_is_not_followed(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture
) -> None:
    stub = make_stub(
        "hello", redirect_card_to="http://elsewhere.example/.well-known/agent-card.json"
    )
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}}, stubs={"hello": stub})
    started_without(
        made, caplog, {"hello": "The address answered with a redirect, which is not followed."}
    )
    assert [r.host for r in made.transports["hello"].requests] == ["127.0.0.1"]


@pytest.mark.parametrize(
    ("card", "sentence"),
    [
        (
            stub_card("hello", security=True),
            "The agent's card requires authentication, which is out of scope.",
        ),
        (
            stub_card("hello", input_modes=("image/png",)),
            "The agent's card does not accept text messages.",
        ),
        (
            stub_card("hello", binding="HTTP+JSON"),
            "The agent's card offers no JSON-RPC interface.",
        ),
    ],
    ids=["authentication", "no-text", "no-json-rpc"],
)
def test_a_card_freyja_cannot_use_does_not_stop_startup(
    make_a2a_app: Callable[..., A2aApp],
    caplog: pytest.LogCaptureFixture,
    card: Any,
    sentence: str,
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(),
        {"hello": {"read_only": True}},
        stubs={"hello": make_stub("hello", card=card)},
    )
    started_without(made, caplog, {"hello": sentence}, Classification.READ_ONLY)


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


def test_every_agent_that_cannot_be_used_is_named(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(),
        {"one": {}, "two": {}},
        stubs={"two": make_stub("two", card=stub_card("two", security=True))},
    )
    made.transports["one"].down = True
    started_without(
        made,
        caplog,
        {
            "one": "Nothing answered at the address.",
            "two": "The agent's card requires authentication, which is out of scope.",
        },
    )


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
