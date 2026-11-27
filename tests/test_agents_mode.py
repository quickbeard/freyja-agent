"""010: the agents' approval mode (FR-001 to FR-007).

A message to an agent runs without a person's decision only when the mode is Auto and the
agent's own choice is to run without approval. The mode is kept with the saved agents and is
Auto where nobody set it, so a deployment behaves as it did. Manual holds at once, also for a
run in progress, which the check before a send enforces; Auto holds from the next run. What
reached an agent is read from the stand-in's own record of the messages it received.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.delegates.agent_registry import AgentSet
from tests.conftest import A2aApp
from tests.fakes import ScriptedChatModel
from tests.test_a2a_gate import handoff, propose, run, stream, texts, tool_messages
from tests.test_agents_approval import before_the_send

# `free` runs without approval by its own choice; `gate` requires approval.
AGENTS = {"free": {"read_only": True}, "gate": {}}
DONE = AIMessage(content="Done.")
NOW_REQUIRES = "Agent 'free' now requires approval, so this message was not sent."


def set_mode(client: TestClient, mode: str) -> None:
    response = client.put("/agents/mode", json={"mode": mode})
    assert response.status_code == 200, response.text
    assert response.json() == {"mode": mode}


def classification(client: TestClient, name: str) -> str:
    """How the catalog of the build a request takes classifies the tool ``name``."""
    tools = client.get("/tools").json()["tools"]
    return next(tool["classification"] for tool in tools if tool["name"] == name)


# The set. The store's part is a contract test in tests/test_saved_agent_store.py.


@pytest.mark.parametrize(
    ("auto", "own", "gated"),
    [(True, True, False), (True, False, True), (False, True, True), (False, False, True)],
)
def test_an_agent_runs_without_approval_only_when_the_mode_and_its_own_choice_allow_it(
    auto: bool, own: bool, gated: bool
) -> None:
    agents = AgentSet(read_only={"agent": own}, auto=auto)
    assert agents.gated(cast(Any, SimpleNamespace(name="agent"))) is gated
    # An agent the set does not know is gated, whatever the mode.
    assert agents.gated(cast(Any, SimpleNamespace(name="other"))) is True


# The route and the list.


def test_the_list_says_the_mode_which_is_auto_where_nobody_set_it_and_the_route_sets_it(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_a2a_app(ScriptedChatModel(), AGENTS)
    with TestClient(made.app) as client:
        assert client.get("/agents").json()["mode"] == "auto"
        revision = client.portal.call(made.saved_agents.revision)
        set_mode(client, "manual")
        assert client.get("/agents").json()["mode"] == "manual"
        assert client.portal.call(made.saved_agents.revision) == revision + 1
        # Setting the mode it has changes nothing, and moves nothing.
        set_mode(client, "manual")
        assert client.portal.call(made.saved_agents.revision) == revision + 1
        refused = client.put("/agents/mode", json={"mode": "sometimes"})
        assert refused.status_code == 422
        assert "sometimes" not in refused.text
        assert client.put("/agents/mode", json={}).status_code == 422
        assert client.get("/agents").json()["mode"] == "manual"
    assert sum("Agents' approval mode changed: manual, was auto" in m for m in caplog.messages) == 1


def test_the_catalog_follows_the_mode_and_send_notification_stays_gated(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(ScriptedChatModel(), AGENTS)
    with TestClient(made.app) as client:
        assert classification(client, "handoff_to_free") == "read_only"
        assert classification(client, "cancel_free_task") == "read_only"
        assert classification(client, "handoff_to_gate") == "gated"
        assert classification(client, "send_notification") == "gated"
        set_mode(client, "manual")
        for name in ("handoff_to_free", "cancel_free_task", "handoff_to_gate"):
            assert classification(client, name) == "gated"
        assert classification(client, "send_notification") == "gated"
        set_mode(client, "auto")
        assert classification(client, "handoff_to_free") == "read_only"


# Runs.


def test_a_hand_off_to_an_auto_agent_waits_while_the_mode_is_manual_and_not_while_auto(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            propose(handoff("free", message="first"), step="a"),
            DONE,
            # The second run pauses on its proposal, which is left undecided.
            propose(handoff("free", message="second"), step="b"),
            propose(handoff("free", message="third"), step="c"),
            DONE,
        ]
    )
    made = make_a2a_app(model, AGENTS)
    with TestClient(made.app) as client:
        assert run(client, "m-1", sse_events)[-1] == ("end", {"status": "completed"})
        set_mode(client, "manual")
        paused = run(client, "m-2", sse_events)
        assert paused[-1][0] == "interrupt"
        (request,) = paused[-1][1]["action_requests"]
        assert (request["name"], request["agent"]) == ("handoff_to_free", "free")
        assert texts(made.stubs["free"]) == ["first"]
        set_mode(client, "auto")
        assert run(client, "m-3", sse_events)[-1] == ("end", {"status": "completed"})
    assert texts(made.stubs["free"]) == ["first", "third"]


@pytest.mark.parametrize("source", ["environment", "saved"])
def test_manual_holds_at_once_for_a_run_built_while_the_mode_was_auto(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    model = ScriptedChatModel(script=[propose(handoff("free"), step="a"), DONE])
    if source == "environment":
        made = make_a2a_app(model, AGENTS)
    else:
        made = make_a2a_app(
            model, {}, saved={"free": {"url": "http://127.0.0.1:9822", "read_only": True}}
        )

    async def to_manual() -> None:
        await made.saved_agents.set_mode("manual")

    before_the_send(made, monkeypatch, to_manual)
    with TestClient(made.app) as client:
        events = run(client, "m-4", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    (result,) = tool_messages(model)
    assert result.content == NOW_REQUIRES
    stubs = [made.stubs["free"]] if source == "environment" else [s for s, _ in made.at.values()]
    assert all(stub.record.messages == [] for stub in stubs)


def test_a_paused_step_holding_a_message_proposed_while_auto_is_blocked_once_manual(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[propose(handoff("gate", message="asks"), handoff("free", message="free")), DONE]
    )
    made = make_a2a_app(model, AGENTS)
    with TestClient(made.app) as client:
        paused = run(client, "m-5", sse_events)
        assert [r["name"] for r in paused[-1][1]["action_requests"]] == ["handoff_to_gate"]
        set_mode(client, "manual")
        decision = {
            "interrupt_id": paused[-1][1]["interrupt_id"],
            "decisions": [{"type": "approve"}],
        }
        blocked = stream(client, "/threads/m-5/resume", decision, sse_events)
        assert blocked[-1][0] == "error"
        assert blocked[-1][1]["code"] == "approval_now_required"
        assert "'free'" in blocked[-1][1]["message"]
        assert made.stubs["free"].record.messages == []
        assert made.stubs["gate"].record.messages == []
        # The proposal stays; with the mode back on Auto the step goes on.
        set_mode(client, "auto")
        done = stream(client, "/threads/m-5/resume", decision, sse_events)
    assert done[-1] == ("end", {"status": "completed"})
    assert texts(made.stubs["gate"]) == ["asks"]
    assert texts(made.stubs["free"]) == ["free"]


def test_a_proposal_for_an_auto_agent_can_only_be_approved_once_the_mode_is_auto(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[propose(handoff("free"), step="a"), DONE])
    made = make_a2a_app(model, AGENTS)
    with TestClient(made.app) as client:
        set_mode(client, "manual")
        paused = run(client, "m-6", sse_events)
        (request,) = paused[-1][1]["action_requests"]
        assert request["allowed_decisions"] == ["approve", "edit", "reject"]
        set_mode(client, "auto")
        pending = client.get("/threads/m-6/state").json()["pending"]
        assert pending["action_requests"][0]["allowed_decisions"] == ["approve"]
        rejected = client.post(
            "/threads/m-6/resume",
            json={"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "reject"}]},
        )
        assert rejected.status_code == 422
        decision = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "approve"}]}
        done = stream(client, "/threads/m-6/resume", decision, sse_events)
    assert done[-1] == ("end", {"status": "completed"})
    assert len(made.stubs["free"].record.messages) == 1


def test_a_saved_agent_set_to_auto_waits_while_the_mode_is_manual(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            # The first run pauses on its proposal, which is left undecided.
            propose(handoff("helper", message="one"), step="a"),
            propose(handoff("helper", message="two"), step="b"),
            DONE,
        ]
    )
    made = make_a2a_app(
        model, {}, saved={"helper": {"url": "http://127.0.0.1:9821", "read_only": True}}
    )
    with TestClient(made.app) as client:
        (listed,) = client.get("/agents").json()["agents"]
        assert listed["approval"] == "none"
        set_mode(client, "manual")
        paused = run(client, "m-7", sse_events)
        assert paused[-1][0] == "interrupt"
        set_mode(client, "auto")
        assert run(client, "m-8", sse_events)[-1] == ("end", {"status": "completed"})
    (stub, _transport) = made.at[next(iter(made.at))]
    assert [message.parts[0][1] for message in stub.record.messages] == ["two"]
