"""005 User Stories 1, 2, and 4: the list of agents (`GET /agents`), adding one (`POST
/agents`), and changing one's approval choice (`PATCH /agents/{id}`).

Part 1: FR-001, FR-003, FR-004, FR-017, FR-028; contracts/openapi.yaml. The list is read from
the saved-agent store and from what this instance holds in memory. It contacts no agent, shows
an environment agent's address nowhere, and answers with an error, never with an earlier list,
when the store cannot be read.

Part 2: FR-005 to FR-008, FR-014, FR-016, FR-027 to FR-030; SC-007, SC-011, SC-012. Adding
reads the agent's card and nothing more, saves the agent only when it can be used, and
refuses everything else with a reason of the service's own that never holds the address.

Part 3: FR-009, FR-012, FR-017, FR-026, FR-029, FR-030; SC-016. `PATCH /agents/{id}` changes
a saved agent's approval choice and contacts no agent. A change names the version its page
read and is refused when the agent was changed since. Adding now takes the choice too.

Every agent is the stand-in, inside the test process.
"""

import dataclasses
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from freyja_agent.config import AddressError, canonical_address
from freyja_agent.delegates.addresses import SCRUBBER, UNSAVED_LIMIT
from freyja_agent.runtime.saved_agents import (
    AddressTakenError,
    AgentLimitError,
    NameTakenError,
)
from tests.a2a_stub import StubAgent, make_stub, stub_card
from tests.conftest import A2aApp, holds_port, saved_card
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import make_stub as make_mcp_stub
from tests.test_a2a_handoff import run
from tests.test_agents_startup import CARD_PATH, seed_task

NOTHING_ANSWERED = "Nothing answered at the address."
ALPHA_URL = "http://127.0.0.1:9101"
MIKE_URL = "http://127.0.0.1:9102"


def agents(client: TestClient) -> dict[str, Any]:
    response = client.get("/agents")
    assert response.status_code == 200, response.text
    return response.json()


def by_id(listed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {agent["id"]: agent for agent in listed["agents"]}


def test_environment_agents_come_first_in_their_order_then_saved_agents_by_name(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(),
        {"zeta": {"read_only": True}, "beta": {}},
        saved={"mike": {"url": MIKE_URL}, "alpha": {"url": ALPHA_URL, "read_only": True}},
    )
    with TestClient(made.app) as client:
        listed = agents(client)
    assert [(agent["name"], agent["source"]) for agent in listed["agents"]] == [
        ("zeta", "environment"),
        ("beta", "environment"),
        ("alpha", "saved"),
        ("mike", "saved"),
    ]
    assert listed["limit"] == 20
    assert listed["unusable"] == 0
    assert set(listed) == {"agents", "limit", "unusable"}


def test_an_environment_agent_is_listed_without_an_address_or_anything_to_change_it_by(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"zeta": {"read_only": True}, "beta": {}})
    with TestClient(made.app) as client:
        listed = by_id(agents(client))
    zeta, beta = listed["env.zeta"], listed["env.beta"]
    assert (zeta["name"], zeta["source"], zeta["approval"]) == ("zeta", "environment", "none")
    assert beta["approval"] == "required"
    for agent in (zeta, beta):
        assert agent["url"] is None
        assert agent["version"] is None
        assert agent["created_at"] is None
        assert agent["updated_at"] is None
        assert agent["state"] == "reachable"
        assert agent["reason"] is None
        assert datetime.fromisoformat(agent["checked_at"])
        assert agent["pending_tasks"] == 0
    # What its card says, cut and cleaned as it would be kept.
    assert zeta["card"] == saved_card(made.stubs["zeta"])
    assert zeta["card"]["cut"] is False
    # Every field the contract names, and no other.
    assert set(zeta) == {
        "id",
        "name",
        "source",
        "approval",
        "url",
        "version",
        "state",
        "checked_at",
        "reason",
        "card",
        "pending_tasks",
        "created_at",
        "updated_at",
    }


def test_a_saved_agent_is_listed_with_its_address_its_version_and_its_kept_card_details(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    long_winded = make_stub("alpha", card=stub_card("alpha", text_length=3000))
    made = make_a2a_app(
        ScriptedChatModel(),
        {},
        # Entered another way than it is kept.
        saved={"alpha": {"url": "HTTP://LOCALHOST:9101/", "stub": long_winded}},
    )
    record = made.saved["alpha"]
    with TestClient(made.app) as client:
        (alpha,) = agents(client)["agents"]
    assert alpha["id"] == record.id
    assert (alpha["source"], alpha["approval"]) == ("saved", "required")
    # The address in the one form the service keeps it in.
    assert alpha["url"] == record.url == "http://localhost:9101"
    assert alpha["version"] == 1
    assert alpha["card"] == record.card
    # The kept details were cut, and the list says so.
    assert alpha["card"]["cut"] is True
    assert len(alpha["card"]["description"]) == 2000
    assert datetime.fromisoformat(alpha["created_at"]) == record.created_at
    assert datetime.fromisoformat(alpha["updated_at"]) == record.updated_at
    assert alpha["state"] == "reachable"


def test_each_agent_s_state_is_what_this_instance_last_found(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(),
        {"up": {}, "down": {}},
        # A saved agent with the name of an environment agent: the environment's is in use.
        saved={"up": {"url": ALPHA_URL}},
    )
    made.transports["down"].down = True
    stub = make_stub("late")
    made.add_stub(MIKE_URL, stub)
    with TestClient(made.app) as client:
        # Saved through another instance: this one learns of it and has not tried it yet.
        late = client.portal.call(
            lambda: made.saved_agents.add(name="late", url=MIKE_URL, card=saved_card(stub))
        )
        listed = agents(client)
    found = by_id(listed)

    assert found["env.up"]["state"] == "reachable"
    assert found["env.up"]["reason"] is None

    down = found["env.down"]
    assert down["state"] == "unreachable"
    assert down["reason"] == NOTHING_ANSWERED
    assert datetime.fromisoformat(down["checked_at"])
    # Its card was never read on this instance.
    assert down["card"] is None

    not_tried = found[late.id]
    assert (not_tried["state"], not_tried["checked_at"], not_tried["reason"]) == (
        "not_tried",
        None,
        None,
    )
    # Its card details are the kept ones, read or not.
    assert not_tried["card"] == late.card

    overridden = found[made.saved["up"].id]
    assert overridden["state"] == "not_in_use"
    assert overridden["reason"] == "An agent named in the environment has this name."
    assert overridden["checked_at"] is None
    assert overridden["url"] == ALPHA_URL

    # Unreachable and not in use count; not tried yet does not.
    assert listed["unusable"] == 2
    assert [agent["name"] for agent in listed["agents"]] == ["up", "down", "late", "up"]


def test_pending_tasks_are_counted_for_each_agent_on_every_thread(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(), {"hello": {}, "idle": {}}, saved={"alpha": {"url": ALPHA_URL}}
    )
    ref = made.saved["alpha"].ref
    seed_task(made, "hello", "h-1", thread="t-1")
    seed_task(made, "hello", "h-2", thread="t-2", state="input_required")
    seed_task(made, "hello", "h-3", thread="t-3", state="given_up")
    seed_task(made, "alpha", "a-1", ref=ref, thread="t-1")
    # A task of another agent that once had the name: not this agent's.
    seed_task(made, "alpha", "a-2", ref="0123456789abcdef:1", thread="t-1")
    with TestClient(made.app) as client:
        listed = agents(client)
    assert {agent["name"]: agent["pending_tasks"] for agent in listed["agents"]} == {
        "hello": 2,
        "idle": 0,
        "alpha": 1,
    }


def test_no_field_of_an_environment_agent_and_no_reason_holds_an_address(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    # A card that names its own agent's address and another agent's, in several spellings.
    talkative = stub_card(
        "hello",
        description=(
            "Reach me at http://127.0.0.1:9999/a2a or localhost:9999, and my neighbour at "
            "http://localhost:9101 or ('127.0.0.1', 9101)."
        ),
    )
    made = make_a2a_app(
        ScriptedChatModel(),
        {"hello": {}, "other": {"url": "http://localhost:9200"}},
        stubs={"hello": make_stub("hello", card=talkative)},
        saved={"hello": {"url": ALPHA_URL}, "there": {"url": "http://127.0.0.1:9200"}},
    )
    with TestClient(made.app) as client:
        _hello, other = made.runtime.agents
        # What a library says of a failed request names the address it asked.
        other.mark(
            False,
            "All connection attempts failed: http://localhost:9200/.well-known/agent-card.json, "
            "Connect call failed ('127.0.0.1', 9200)",
        )
        listed = agents(client)
    environment = [agent for agent in listed["agents"] if agent["source"] == "environment"]
    assert [(agent["name"], agent["state"]) for agent in environment] == [
        ("hello", "reachable"),
        ("other", "unreachable"),
    ]
    # The card's text and the reason are there, with every address taken out.
    assert environment[0]["card"]["description"] == (
        "Reach me at <address>/a2a or <address>, and my neighbour at <address> or <address>."
    )
    assert environment[1]["reason"] == (
        "All connection attempts failed: <address>/.well-known/agent-card.json, "
        "Connect call failed <address>"
    )
    for text in (json.dumps(environment), *(agent["reason"] or "" for agent in listed["agents"])):
        for part in ("127.0.0.1", "localhost", "://"):
            assert part not in text, (part, text)
        # As a number of its own: `checked_at` holds digits of its own.
        for port in (9200, 9999, 9101):
            assert not holds_port(text, port), (port, text)
    # Both saved agents are overridden, each by another rule, and each reason says which.
    reasons = {agent["name"]: agent["reason"] for agent in listed["agents"] if agent["url"]}
    assert reasons == {
        "hello": "An agent named in the environment has this name.",
        "there": "An agent named in the environment is at this address.",
    }


def test_reading_the_list_and_the_catalog_contacts_no_agent(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}}, saved={"alpha": {"url": ALPHA_URL}})
    with TestClient(made.app) as client:
        at_startup = (
            len(made.transports["hello"].requests),
            len(made.transport_at(ALPHA_URL).requests),
        )
        for _ in range(2):
            agents(client)
            assert client.get("/tools").status_code == 200
        afterwards = (
            len(made.transports["hello"].requests),
            len(made.transport_at(ALPHA_URL).requests),
        )
    # One card read each at startup, and nothing since.
    assert at_startup == (1, 1)
    assert afterwards == at_startup


def test_the_list_carries_no_cross_origin_header(make_a2a_app: Callable[..., A2aApp]) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}}, saved={"alpha": {"url": ALPHA_URL}})
    with TestClient(made.app) as client:
        preflight = client.options(
            "/agents",
            headers={
                "Origin": "http://elsewhere.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        read = client.get("/agents", headers={"Origin": "http://elsewhere.example"})
    for response in (preflight, read):
        allowed = [name for name in response.headers if name.lower().startswith("access-control-")]
        assert allowed == [], (response.status_code, allowed)
    # A preflight is answered with no permission, and not with the list.
    assert preflight.status_code in (400, 405)
    assert ALPHA_URL not in preflight.text
    # A read is answered, and the browser keeps the answer from the other origin's page.
    assert read.status_code == 200


MARKER = "password=hunter2"


@pytest.mark.parametrize("read", ["revision", "list_agents", "pending_counts"])
def test_the_list_answers_with_an_error_and_no_earlier_list_when_a_store_cannot_be_read(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    read: str,
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}}, saved={"alpha": {"url": ALPHA_URL}})
    with TestClient(made.app) as client:
        assert len(agents(client)["agents"]) == 2

        async def unreadable(*_args: Any) -> Any:
            # What a database's error may hold: where it is and how it was reached.
            raise RuntimeError(f"connection to db.internal failed: {MARKER}")

        store = made.store if read == "pending_counts" else made.saved_agents
        monkeypatch.setattr(store, read, unreadable)
        response = client.get("/agents")
        # The catalog, which shows no address and no version, answers from what it had.
        catalog = client.get("/tools")
        held = dict(made.runtime.registry._holders)  # type: ignore[union-attr]
    assert response.status_code == 500
    assert response.json() == {
        "detail": {"code": "internal", "message": "The saved agents could not be read."}
    }
    # Nothing of the earlier list, and nothing of what the failure said, in the answer or
    # in the log.
    assert "alpha" not in response.text
    for text in (response.text, caplog.text):
        assert MARKER not in text
        assert "db.internal" not in text
    assert "The agents could not be listed: RuntimeError" in caplog.text
    # The set the request took was given back.
    assert held == {}
    assert catalog.status_code == 200
    assert [agent["name"] for agent in catalog.json()["agents"]] == ["hello", "alpha"]


def test_a_saved_agent_changed_since_the_set_was_read_is_listed_as_not_tried(
    make_a2a_app: Callable[..., A2aApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {}, saved={"alpha": {"url": ALPHA_URL}})
    record = made.saved["alpha"]
    seed_task(made, "alpha", "a-1", ref=record.ref)
    with TestClient(made.app) as client:
        assert agents(client)["agents"][0]["state"] == "reachable"
        # Moved by another instance between this request's two reads: the store has the
        # later address, and the set this request took still has the earlier link.
        moved = dataclasses.replace(
            record, url=MIKE_URL, destination="9102", address_version=2, version=2
        )

        async def later() -> list[Any]:
            return [moved]

        monkeypatch.setattr(made.saved_agents, "list_agents", later)
        (alpha,) = agents(client)["agents"]
    # What is listed is the agent as the store has it, which nothing here has tried yet.
    assert (alpha["url"], alpha["version"]) == (MIKE_URL, 2)
    assert (alpha["state"], alpha["checked_at"], alpha["reason"]) == ("not_tried", None, None)
    # The task belongs to the agent at its earlier address.
    assert alpha["pending_tasks"] == 0


# Part 2 (T050): `POST /agents`.

ROUTES = "freyja_agent.api.agents"
# Ports nothing else in the suite uses, so that finding one anywhere means an address got out.
# The scrubber is the process's own and forgets nothing a test told it, so a check that an
# address is not logged can only fail for an address no earlier test gave an agent: an add
# that must be refused is made at a port of its own, which nothing ever saves.
NEW_URL = "http://127.0.0.1:9311"
NEW_PORT = "9311"
OTHER_PORT = "9312"
# Where the adds that break a rule point: never requested, never told to the scrubber.
RULE_PORT = "9314"
# One port for each way a card can be refused, from 9330.
UNUSABLE_PORT = 9330
PORTS = (NEW_PORT, OTHER_PORT, RULE_PORT, *(str(UNUSABLE_PORT + n) for n in range(9)))
FIELDS = {
    "id",
    "name",
    "source",
    "approval",
    "url",
    "version",
    "state",
    "checked_at",
    "reason",
    "card",
    "pending_tasks",
    "created_at",
    "updated_at",
}


def add(client: TestClient, name: str = "helper", url: str = NEW_URL, **more: Any) -> Any:
    return client.post("/agents", json={"name": name, "url": url, **more})


def refusal(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code, detail
    return detail


def what_exists(client: TestClient) -> tuple[Any, Any]:
    """The list and the catalog: what a refused add must leave as it was (SC-007)."""
    return agents(client), client.get("/tools").json()


def no_address(*texts: str) -> None:
    """Nothing of an address is in an answer: no scheme, no host, and no port."""
    for text in texts:
        for part in ("127.0.0.1", "localhost", "://"):
            assert part not in text, (part, text[:400])
    no_port(*texts)


def no_port(*texts: str) -> None:
    """No port of an address is in a log, which also holds the test client's own lines."""
    for text in texts:
        for port in PORTS:
            assert not holds_port(text, port), (port, text[:400])


def route_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == ROUTES]


@pytest.mark.parametrize(
    ("approval", "choice"),
    [({}, "required"), ({"approval": "required"}, "required"), ({"approval": "none"}, "none")],
)
def test_adding_an_agent_reads_its_card_and_answers_with_the_agent_as_it_is_saved(
    make_a2a_app: Callable[..., A2aApp],
    caplog: pytest.LogCaptureFixture,
    approval: dict[str, str],
    choice: str,
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}})
    stub = make_stub("helper")
    transport = made.add_stub(NEW_URL, stub)
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        # Entered another way than it is kept.
        response = add(client, url="HTTP://LocalHost:9311/", **approval)
        listed = by_id(agents(client))
        catalog = client.get("/tools").json()
        (record,) = client.portal.call(made.saved_agents.list_agents)

    assert response.status_code == 201, response.text
    agent = response.json()
    assert set(agent) == FIELDS
    assert agent["id"] == record.id and len(agent["id"]) == 32
    assert (agent["name"], agent["source"]) == ("helper", "saved")
    # The address in the form the service keeps it in, and approval required unless the
    # request chose otherwise.
    assert agent["url"] == record.url == "http://localhost:9311"
    assert agent["approval"] == choice and record.read_only is (choice == "none")
    assert agent["version"] == 1
    # Its card was just read: the agent answers, and its details are the kept ones.
    assert (agent["state"], agent["reason"]) == ("reachable", None)
    assert datetime.fromisoformat(agent["checked_at"])
    assert agent["card"] == saved_card(stub) == record.card
    assert agent["pending_tasks"] == 0
    assert datetime.fromisoformat(agent["created_at"]) == record.created_at
    # The list has it as the answer had it, and the catalog has its two tools, classified
    # by the choice.
    assert listed[record.id] == agent
    tools = {tool["name"]: tool for tool in catalog["tools"]}
    classification = "gated" if choice == "required" else "read_only"
    assert tools["handoff_to_helper"]["classification"] == classification
    assert tools["cancel_helper_task"]["classification"] == classification
    # One request reached the agent, for its card: no message and no task (SC-011).
    assert [(request.method, request.path) for request in transport.requests] == [
        ("GET", CARD_PATH)
    ]
    assert stub.record.messages == [] and stub.record.cancels == []
    # The log names the agent and its approval choice, and never the address (FR-030).
    assert f"Agent added: helper ({record.id}), approval {choice}" in route_lines(caplog)
    no_port(caplog.text)
    # Neither does the catalog, in the agent's entry or in either of its tools.
    no_address(
        json.dumps([catalog["agents"], tools["handoff_to_helper"], tools["cancel_helper_task"]])
    )


def stand_in(code: str) -> StubAgent:
    """A stand-in whose card is refused as ``code``, for the codes its card decides."""
    return {
        "no_card": lambda: make_stub("helper", card_status=404, card_body=b""),
        "unreadable": lambda: make_stub("helper", card_body=b"this is no card"),
        "redirect": lambda: make_stub("helper", redirect_card_to="http://127.0.0.1:9312/a"),
        "too_large": lambda: make_stub("helper", card_body=b"x" * (1024 * 1024 + 1)),
        "auth_required": lambda: make_stub("helper", card=stub_card("helper", security=True)),
        "no_text": lambda: make_stub(
            "helper", card=stub_card("helper", input_modes=("image/png",))
        ),
        "no_interface": lambda: make_stub("helper", card=stub_card("helper", binding="GRPC")),
    }.get(code, lambda: make_stub("helper"))()


UNUSABLE = {
    "no_answer": "Nothing answered at the address.",
    "timeout": "The agent did not answer within 0.4 seconds.",
    "no_card": "Something answered at the address, but it served no agent card.",
    "unreadable": "The agent's card could not be read.",
    "redirect": "The address answered with a redirect, which is not followed.",
    "too_large": "The agent's card is larger than one megabyte.",
    "auth_required": "The agent's card requires authentication, which is out of scope.",
    "no_text": "The agent's card does not accept text messages.",
    "no_interface": "The agent's card offers no JSON-RPC interface.",
}


@pytest.mark.parametrize("code", list(UNUSABLE))
def test_an_agent_that_cannot_be_used_is_refused_with_the_reason_and_leaves_nothing(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    code: str,
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_app(model, {"hello": {}}, a2a_startup_timeout_seconds=0.4)
    stub = stand_in(code)
    # An address nothing else was ever told: what keeps it out of the log is this request.
    url = f"http://127.0.0.1:{UNUSABLE_PORT + list(UNUSABLE).index(code)}"
    transport = made.add_stub(url, stub)
    transport.down = code == "no_answer"
    transport.hang = code == "timeout"
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        run(client, "p-1", sse_events)
        before, bound = what_exists(client), list(model.bound_tool_names)
        began = time.monotonic()
        response = add(client, url=url)
        took = time.monotonic() - began
        after = what_exists(client)
        run(client, "p-1", sse_events, "again")
        saved = client.portal.call(made.saved_agents.list_agents)

    assert response.status_code == 409, response.text
    # The card problem's code and the service's own sentence for it (FR-016).
    assert response.json() == {
        "detail": {"code": "agent_unusable", "reason": code, "message": UNUSABLE[code]}
    }
    # Nothing was saved: the list, the catalog, and the next run's tools are what they were.
    assert saved == []
    assert after == before
    assert model.bound_tool_names == bound
    # The card was asked for once, and nothing else was (SC-011).
    assert [(request.method, request.path) for request in transport.requests] == [
        ("GET", CARD_PATH)
    ]
    assert stub.record.messages == []
    # One that never answers is given up at the time limit (SC-012).
    assert took < 3
    no_address(response.text)
    no_port(caplog.text)
    if code not in ("no_answer", "timeout"):
        # The request was answered, so the HTTP library logged it, with the address taken out.
        requested = [
            record.getMessage()
            for record in caplog.records
            if record.name == "httpx" and CARD_PATH in record.getMessage()
        ]
        # Two card reads: the environment agent's at startup, and this add's.
        assert len(requested) == 2, requested
        assert all("GET <address>/.well-known" in line for line in requested), requested
    assert f"Agent helper was not added: {UNUSABLE[code]}" in route_lines(caplog)


@pytest.mark.parametrize(
    ("configured", "limit"),
    [
        # The service's limit for reading a card, when it is the shorter.
        (0.5, 0.5),
        # Twenty seconds whatever the limit is set to: the console's server waits thirty.
        (300.0, 20.0),
    ],
)
def test_the_card_is_read_within_the_lesser_of_the_startup_limit_and_twenty_seconds(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    configured: float,
    limit: float,
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {}, a2a_startup_timeout_seconds=configured)
    made.add_stub(NEW_URL, make_stub("helper"))
    limits: list[float] = []
    with TestClient(made.app) as client:
        registry = made.runtime.registry
        assert registry is not None
        read = registry.read_card

        async def reading(*args: Any, limit: float) -> Any:
            limits.append(limit)
            return await read(*args, limit=limit)

        monkeypatch.setattr(registry, "read_card", reading)
        assert add(client).status_code == 201
    assert limits == [limit]


NAME_TAKEN = {
    "a-saved-agent": ("alpha", "Another saved agent has this name."),
    "an-environment-agent": ("hello", "An agent named in the environment has this name."),
    "a-tool-server": ("notes", "A tool server has this name."),
    "its-hand-off-tool": ("ops", "Another tool has the name of one of this agent's tools."),
    "its-cancel-tool": ("desk", "Another tool has the name of one of this agent's tools."),
}


@pytest.mark.parametrize("case", list(NAME_TAKEN))
def test_a_name_that_is_taken_is_refused_before_any_card_is_read(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture, case: str
) -> None:
    name, message = NAME_TAKEN[case]
    server = make_mcp_stub(
        "notes",
        extra={"handoff_to_ops": lambda: "taken", "cancel_desk_task": lambda: "taken"},
    )
    made = make_a2a_app(
        ScriptedChatModel(),
        {"hello": {}},
        servers={"notes": {"command": "stub-command"}},
        server_stubs={"notes": server},
        saved={"alpha": {"url": ALPHA_URL}},
    )
    transport = made.add_stub(NEW_URL, make_stub(name))
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        before = what_exists(client)
        response = add(client, name=name)
        after = what_exists(client)
    assert refusal(response, 409, "name_taken") == {"code": "name_taken", "message": message}
    assert transport.requests == []
    assert after == before
    no_address(response.text)
    no_port(caplog.text)


ADDRESS_TAKEN = {
    # A saved agent is at http://127.0.0.1:9101, an environment agent at http://127.0.0.1:9999.
    "a-saved-agent-s": ("http://127.0.0.1:9101", "Another saved agent is at this address."),
    "a-saved-agent-s-written-another-way": (
        "HTTPS://LocalHost:9101/",
        "Another saved agent is at this address.",
    ),
    "a-saved-agent-s-over-ipv6": ("http://[::1]:9101", "Another saved agent is at this address."),
    "an-environment-agent-s": (
        "http://localhost:9999",
        "An agent named in the environment is at this address.",
    ),
}


@pytest.mark.parametrize("case", list(ADDRESS_TAKEN))
def test_an_address_that_leads_where_another_agent_s_does_is_refused_before_any_card_is_read(
    make_a2a_app: Callable[..., A2aApp], case: str
) -> None:
    url, message = ADDRESS_TAKEN[case]
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}}, saved={"alpha": {"url": ALPHA_URL}})
    with TestClient(made.app) as client:
        read_before = (
            len(made.transport_at(ALPHA_URL).requests),
            len(made.transports["hello"].requests),
        )
        before = what_exists(client)
        response = add(client, url=url)
        after = what_exists(client)
        read_after = (
            len(made.transport_at(ALPHA_URL).requests),
            len(made.transports["hello"].requests),
        )
    assert refusal(response, 409, "address_taken") == {"code": "address_taken", "message": message}
    # The agent that is there was not asked for its card again.
    assert read_after == read_before
    assert after == before
    for part in ("127.0.0.1", "localhost", "://"):
        assert part not in response.text
    assert not holds_port(response.text, 9101) and not holds_port(response.text, 9999)


def test_the_twenty_first_agent_is_refused_before_any_card_is_read(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    twenty = {
        f"agent{number:02d}": {"url": f"http://127.0.0.1:{9400 + number}"} for number in range(20)
    }
    made = make_a2a_app(ScriptedChatModel(), {}, saved=twenty)
    transport = made.add_stub(NEW_URL, make_stub("helper"))
    with TestClient(made.app) as client:
        assert agents(client)["limit"] == 20
        response = add(client)
        count = len(agents(client)["agents"])
    detail = refusal(response, 409, "agent_limit")
    assert detail["message"] == "20 agents are saved, which is the most there can be."
    assert transport.requests == []
    assert count == 20


def test_a_fifth_card_read_at_once_is_refused_and_a_slot_is_given_back_when_a_read_ends(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    # The four reads hang until this limit, and the fifth request has to arrive before the
    # first of them ends: three seconds is room for a slow machine.
    made = make_a2a_app(ScriptedChatModel(), {}, a2a_startup_timeout_seconds=3.0)
    hanging = []
    for number in range(4):
        transport = made.add_stub(f"http://127.0.0.1:{9500 + number}", make_stub(f"slow{number}"))
        transport.hang = True
        hanging.append(transport)
    fifth = made.add_stub(NEW_URL, make_stub("helper"))
    answers: dict[int, Any] = {}
    with TestClient(made.app) as client:
        slots = made.runtime.card_reads

        def slow(number: int) -> None:
            answers[number] = add(
                client, name=f"slow{number}", url=f"http://127.0.0.1:{9500 + number}"
            )

        threads = [threading.Thread(target=slow, args=(number,)) for number in range(4)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        while not all(transport.requests for transport in hanging):
            assert time.monotonic() < deadline, "the four card reads never started"
            time.sleep(0.01)
        # Four card reads are under way: the fifth is refused at once, and reads nothing.
        began = time.monotonic()
        response = add(client)
        took = time.monotonic() - began
        for thread in threads:
            thread.join(10)
        # Each of the four ended at its time limit and gave its slot back.
        free = slots.refusal(None) is None
        added = add(client)

    detail = refusal(response, 409, "check_busy")
    assert detail["message"] == (
        "4 agent cards are already being read on this instance. Try again when one of those "
        "reads ends."
    )
    assert took < 1
    assert [answers[number].json()["detail"]["reason"] for number in range(4)] == ["timeout"] * 4
    assert free
    assert added.status_code == 201
    assert [(request.method, request.path) for request in fifth.requests] == [("GET", CARD_PATH)]


def test_a_slot_is_given_back_when_the_card_read_ends_in_a_way_no_rule_expects(
    make_a2a_app: Callable[..., A2aApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    made.add_stub(NEW_URL, make_stub("helper"))
    with TestClient(made.app) as client:
        registry = made.runtime.registry
        assert registry is not None

        async def stopped(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("the read failed")

        monkeypatch.setattr(registry, "read_card", stopped)
        with pytest.raises(RuntimeError):
            add(client)
        free = made.runtime.card_reads.refusal(None) is None
        running = made.runtime.card_reads._running
    assert free and running == 0


def _rule(url: str) -> str:
    """The sentence the address rule gives for ``url``."""
    with pytest.raises(AddressError) as refused:
        canonical_address(url)
    return str(refused.value)


NAME_RULE = (
    "The name must start with a lowercase letter and hold at most 32 lowercase letters, "
    "digits, hyphens, and underscores."
)
BAD_ADDRESSES = [
    "ftp://127.0.0.1:9314",
    "127.0.0.1:9314",
    "http://example.com:9314",
    "http://192.168.1.5:9314",
    "http://user:secret@127.0.0.1:9314",
    "http://127.0.0.1:9314/?x=1",
    "http://127.0.0.1:9314/#top",
    "http://127.0.0.1:9314//a",
    "http://127.0.0.1:9314/a/../b",
    "http://127.0.0.1:9314/a%20b",
    "http://127.0.0.1:9314/a\\b",
    "http://127.0.0.1:9314/a b",
    "http://127.0.0.1:0",
    "http://127.0.0.1:9314/a{b}",
    "http://127.0.0.1:9314/" + "a" * 2000,
]
BAD_NAMES = ["Helper", "1helper", "", "a" * 33, "has space", "naïve", "helper!"]


@pytest.mark.parametrize("url", BAD_ADDRESSES, ids=lambda url: url[:40])
def test_an_address_that_breaks_a_rule_is_refused_in_the_service_s_words_without_the_address(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture, url: str
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    transport = made.add_stub(f"http://127.0.0.1:{RULE_PORT}", make_stub("helper"))
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        response = add(client, url=url)
        saved = client.portal.call(made.saved_agents.list_agents)
    assert response.status_code == 422, response.text
    # One field error, at the address, worded by the rule and holding nothing that was sent.
    assert response.json() == {
        "detail": [{"type": "agent_address", "loc": ["body", "url"], "msg": _rule(url)}]
    }
    assert saved == [] and transport.requests == []
    for text in (response.text, caplog.text):
        for part in ("example.com", "192.168", "secret", "x=1"):
            assert part not in text, (part, text[:300])
    no_port(response.text, caplog.text)


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_name_that_is_not_in_the_form_is_refused_at_the_name(
    make_a2a_app: Callable[..., A2aApp], name: str
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    transport = made.add_stub(NEW_URL, make_stub("helper"))
    with TestClient(made.app) as client:
        response = add(client, name=name)
    assert response.status_code == 422, response.text
    assert response.json() == {
        "detail": [{"type": "agent_name", "loc": ["body", "name"], "msg": NAME_RULE}]
    }
    assert transport.requests == []


@pytest.mark.parametrize(
    ("body", "loc", "kind"),
    [
        # `none` is a choice since the routes that can take it back exist (Phase 7).
        ({"name": "helper", "url": NEW_URL, "approval": "maybe"}, "approval", "literal_error"),
        ({"name": "helper", "url": NEW_URL, "approval": None}, "approval", "literal_error"),
        ({"name": "helper", "url": NEW_URL, "read_only": True}, "read_only", "extra_forbidden"),
        ({"name": "helper", "url": NEW_URL, "version": 1}, "version", "extra_forbidden"),
        ({"url": NEW_URL}, "name", "missing"),
        ({"name": "helper"}, "url", "missing"),
        ({"name": "helper", "url": 9311}, "url", "string_type"),
        ({"name": ["helper"], "url": NEW_URL}, "name", "string_type"),
    ],
)
def test_a_body_that_is_not_the_shape_is_refused_as_field_errors_without_what_was_sent(
    make_a2a_app: Callable[..., A2aApp], body: dict[str, Any], loc: str, kind: str
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    transport = made.add_stub(NEW_URL, make_stub("helper"))
    with TestClient(made.app) as client:
        response = client.post("/agents", json=body)
        saved = client.portal.call(made.saved_agents.list_agents)
    assert response.status_code == 422, response.text
    (error,) = response.json()["detail"]
    assert (error["type"], error["loc"]) == (kind, ["body", loc])
    assert set(error) == {"type", "loc", "msg"}
    no_port(response.text)
    assert saved == [] and transport.requests == []


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        ("name=helper&url=http%3A%2F%2F127.0.0.1%3A9311", "application/x-www-form-urlencoded"),
        ('{"name": "helper", "url": "http://127.0.0.1:9311"}', "text/plain"),
        ("name=helper url=http://127.0.0.1:9311", "application/json"),
    ],
)
def test_a_body_that_is_not_sent_as_json_is_refused_without_repeating_it(
    make_a2a_app: Callable[..., A2aApp], content: str, content_type: str
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    transport = made.add_stub(NEW_URL, make_stub("helper"))
    with TestClient(made.app) as client:
        response = client.post("/agents", content=content, headers={"Content-Type": content_type})
        saved = client.portal.call(made.saved_agents.list_agents)
    assert response.status_code == 422, response.text
    for error in response.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}
    no_port(response.text)
    assert "helper" not in response.text
    assert saved == [] and transport.requests == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://elsewhere.example"},
        {"Origin": "http://elsewhere.example", "Sec-Fetch-Site": "same-origin"},
    ],
)
def test_an_add_made_by_a_page_of_another_origin_is_refused_before_anything_is_looked_at(
    make_a2a_app: Callable[..., A2aApp], headers: dict[str, str]
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    transport = made.add_stub(NEW_URL, make_stub("helper"))
    with TestClient(made.app) as client:
        response = client.post("/agents", json={"name": "helper", "url": NEW_URL}, headers=headers)
        # Refused also when the body would have been refused for itself.
        invalid = client.post("/agents", json={"name": "Not A Name"}, headers=headers)
        saved = client.portal.call(made.saved_agents.list_agents)
    for refused in (response, invalid):
        detail = refusal(refused, 403, "other_origin")
        assert detail["message"].endswith(
            "Agents are changed from the console, or by a client that is not a web page."
        )
    assert saved == [] and transport.requests == []


def test_an_add_made_by_the_console_s_own_page_is_served(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    made.add_stub(NEW_URL, make_stub("helper"))
    own = {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}
    with TestClient(made.app) as client:
        response = client.post("/agents", json={"name": "helper", "url": NEW_URL}, headers=own)
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (NameTakenError, "name_taken"),
        (AddressTakenError, "address_taken"),
        (AgentLimitError, "agent_limit"),
    ],
)
def test_the_store_decides_again_when_another_instance_saved_in_between(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
    code: str,
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    transport = made.add_stub(NEW_URL, make_stub("helper"))

    async def taken_meanwhile(**_values: Any) -> Any:
        # What the route read before the card was read is no longer so.
        raise error

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.saved_agents, "add", taken_meanwhile)
        response = add(client)
        monkeypatch.undo()
        listed = agents(client)["agents"]
    detail = refusal(response, 409, code)
    no_address(detail["message"])
    assert listed == []
    # The card had been read by then, and nothing else was sent.
    assert [request.method for request in transport.requests] == ["GET"]


def test_an_add_stands_when_this_instance_cannot_take_it_up_at_once(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    # An address of this test's own: the scrubber has it from this add and nothing else.
    kept_url = "http://127.0.0.1:9316"
    transport = made.add_stub(kept_url, make_stub("helper"))
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        registry = made.runtime.registry
        assert registry is not None

        async def failing(*_args: Any) -> Any:
            raise RuntimeError(f"could not connect to {kept_url}")

        monkeypatch.setattr(registry, "adopt", failing)
        response = add(client, url=kept_url)
        monkeypatch.undo()
        # No link exists for the agent yet, and its address is already kept for as long as
        # the process runs: it is not forgotten with the addresses of adds that were refused.
        for number in range(UNSAVED_LIMIT + 1):
            SCRUBBER.register(f"http://127.0.0.1:{20000 + number}")
        still_cleaned = SCRUBBER.scrub(f"asked {kept_url} for its card")
        (listed,) = agents(client)["agents"]
    assert still_cleaned == "asked <address> for its card"
    # Saved all the same: every instance takes it up at its next context.
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "not_tried"
    assert response.json()["checked_at"] is None
    assert listed["id"] == response.json()["id"]
    assert (
        "Agent helper was added, and this instance could not take it up at once: RuntimeError"
        in route_lines(caplog)
    )
    assert f"Agent added: helper ({listed['id']}), approval required" in route_lines(caplog)
    no_port(caplog.text)
    assert [request.method for request in transport.requests] == ["GET"]


def test_an_add_is_answered_with_an_error_when_the_saved_agents_cannot_be_read(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    transport = made.add_stub(NEW_URL, make_stub("helper"))

    async def unreadable() -> Any:
        raise RuntimeError(f"connection to db.internal failed: {MARKER}")

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.saved_agents, "list_agents", unreadable)
        response = add(client)
    assert refusal(response, 500, "internal") == {
        "code": "internal",
        "message": "The saved agents could not be read.",
    }
    for text in (response.text, caplog.text):
        assert MARKER not in text and "db.internal" not in text
    assert transport.requests == []


def test_an_add_is_answered_with_an_error_when_the_agent_cannot_be_saved(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    made.add_stub(NEW_URL, make_stub("helper"))

    async def unwritable(**_values: Any) -> Any:
        raise RuntimeError(f"connection to db.internal failed: {MARKER} {NEW_URL}")

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        monkeypatch.setattr(made.saved_agents, "add", unwritable)
        response = add(client)
        monkeypatch.undo()
        listed = agents(client)["agents"]
        free = made.runtime.card_reads.refusal(None) is None
    assert refusal(response, 500, "internal") == {
        "code": "internal",
        "message": "The agent could not be saved.",
    }
    for text in (response.text, caplog.text):
        assert MARKER not in text and "db.internal" not in text
    no_address(response.text)
    no_port(caplog.text)
    assert listed == [] and free


def test_a_service_put_together_without_agents_cannot_add_one(
    make_runtime: Callable[..., Any], make_app: Callable[..., Any]
) -> None:
    # A runtime built by hand has no registry: nothing could read a card or use the agent.
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        response = add(client)
    assert refusal(response, 500, "internal")["message"] == "This instance cannot add agents."


def test_an_address_that_becomes_a_saved_agent_s_is_removed_from_what_other_cards_said(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    # Two agents whose cards name an address before any agent is saved there: an environment
    # agent's, read at startup, and a saved agent's, kept in the database. The scrubber is
    # the process's own, so the address is one no other test gives an agent.
    later, port = "http://127.0.0.1:9318", "9318"
    naming = f"My neighbour is at {later}/a2a, localhost:{port}, and ('127.0.0.1', {port})."
    cleaned = "My neighbour is at <address>/a2a, <address>, and <address>."
    stubs = {"hello": make_stub("hello", card=stub_card("hello", description=naming))}
    saved = {
        "alpha": {
            "url": ALPHA_URL,
            "stub": make_stub("alpha", card=stub_card("alpha", description=naming)),
        }
    }
    first = make_a2a_app(ScriptedChatModel(), {"hello": {}}, stubs=stubs, saved=saved)
    second = make_a2a_app(
        ScriptedChatModel(),
        {"hello": {}},
        stubs=stubs,
        instance_id="instance-b",
        store=first.store,
        saved_agents=first.saved_agents,
        at=first.at,
    )
    first.add_stub(later, make_stub("helper"))

    def descriptions(client: TestClient) -> dict[str, str]:
        catalog = client.get("/tools").json()
        found = {
            f"/tools {a['name']}": a["card"]["description"]
            for a in catalog["agents"]
            if a["name"] != "helper"
        }
        found |= {
            f"tool {t['name']}": t["description"]
            for t in catalog["tools"]
            if t["name"] in ("handoff_to_hello", "handoff_to_alpha")
        }
        found |= {
            f"/agents {a['name']}": a["card"]["description"]
            for a in agents(client)["agents"]
            if a["name"] != "helper"
        }
        return found

    with TestClient(first.app) as one, TestClient(second.app) as two:
        before = descriptions(one)
        added = add(one, url=later)
        # On the instance that added the agent, and on one that only learns of it.
        here, there = descriptions(one), descriptions(two)
        answered = by_id(agents(one))[added.json()["id"]]

    assert added.status_code == 201
    # Until an agent was saved there, it was only something a card said.
    assert len(before) == 6
    assert all(naming in text for text in before.values()), before
    # From then on it is a saved agent's address, and is in no card's text and in no tool's
    # description, on either instance.
    for found in (here, there):
        assert set(found) == set(before)
        for where, text in found.items():
            assert cleaned in text, (where, text)
            assert not holds_port(text, port), (where, text)
    # It is where it belongs: in `url` of the agent's own entry.
    assert answered["url"] == later


def test_the_address_of_a_saved_agent_that_is_not_in_use_is_removed_from_what_cards_say(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    # A saved agent the environment overrides has no link, so nothing but its record says
    # what its address is. The address is this test's own: nothing told the scrubber of it.
    unused, port = "http://127.0.0.1:9319", "9319"
    naming = f"My neighbour is at {unused}/a2a and localhost:{port}."
    made = make_a2a_app(
        ScriptedChatModel(),
        {"desk": {}},
        stubs={"desk": make_stub("desk", card=stub_card("desk", description=naming))},
        saved={"desk": {"url": unused}},
    )
    with TestClient(made.app) as client:
        catalog = client.get("/tools").json()
        listed = agents(client)["agents"]
    environment, saved = listed
    assert (saved["state"], saved["url"]) == ("not_in_use", unused)
    tools = {tool["name"]: tool for tool in catalog["tools"]}
    for where, text in {
        "/tools": catalog["agents"][0]["card"]["description"],
        "tool": tools["handoff_to_desk"]["description"],
        "/agents": environment["card"]["description"],
    }.items():
        assert "My neighbour is at <address>/a2a and <address>." in text, (where, text)
        assert not holds_port(text, port), (where, text)


# Part 3: changing a saved agent's approval choice.

# A port of this part's own, for the reason given at the top of part 2.
CHANGED_URL = "http://127.0.0.1:9351"
CHANGED_PORT = "9351"
UNKNOWN_ID = "0" * 32
CHANGED = "The agent was changed since this page read it. Nothing was changed."
ENVIRONMENT_READ_ONLY = "An agent named in the environment cannot be changed here."
ONLY_REMOVED = "This saved agent is not in use, so it can only be removed."
NOT_FOUND = "There is no agent with this id."


def change(client: TestClient, agent_id: str, **kwargs: Any) -> Any:
    return client.patch(f"/agents/{agent_id}", **kwargs)


def helper_app(
    make_a2a_app: Callable[..., A2aApp], *, read_only: bool = False, **more: Any
) -> A2aApp:
    saved = {"helper": {"url": CHANGED_URL, "read_only": read_only}}
    return make_a2a_app(ScriptedChatModel(), more.pop("agents", {}), saved=saved, **more)


def changed_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [line for line in route_lines(caplog) if line.startswith("Agent changed")]


@pytest.mark.parametrize("down", [False, True])
@pytest.mark.parametrize(("before", "after"), [("required", "none"), ("none", "required")])
def test_a_change_of_the_approval_choice_contacts_no_agent_and_answers_with_the_agent(
    make_a2a_app: Callable[..., A2aApp],
    caplog: pytest.LogCaptureFixture,
    before: str,
    after: str,
    down: bool,
) -> None:
    made = helper_app(make_a2a_app, read_only=before == "none")
    record = made.saved["helper"]
    transport = made.transport_at(CHANGED_URL)
    # Not running when the service starts, and still not when the choice is changed.
    transport.down = down
    seed_task(made, "helper", "task-1", ref=record.ref, thread="g-1")
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        at_startup = list(transport.requests)
        was = by_id(agents(client))[record.id]
        response = change(client, record.id, json={"version": 1, "approval": after})
        requests = list(transport.requests)
        now = by_id(agents(client))[record.id]
        catalog = {tool["name"]: tool for tool in client.get("/tools").json()["tools"]}
        kept = client.portal.call(made.saved_agents.get_agent, record.id)
        task = client.portal.call(made.store.get_task, "helper", "task-1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"agent", "given_up"} and body["given_up"] == 0
    agent = body["agent"]
    assert set(agent) == FIELDS
    # The choice and the version moved, and nothing else of the agent did.
    assert (was["approval"], was["version"]) == (before, 1)
    assert (agent["approval"], agent["version"]) == (after, 2)
    assert kept.read_only is (after == "none") and kept.version == 2
    for field in FIELDS - {"approval", "version", "updated_at"}:
        assert agent[field] == was[field], field
    assert agent["state"] == ("unreachable" if down else "reachable")
    assert now == agent
    # The agent was sent nothing, in either direction and whether or not it answers.
    assert requests == at_startup
    # Its pending task is as it was (FR-026).
    assert agent["pending_tasks"] == 1 and task.state == "working"
    # The catalog classifies its two tools by the new choice.
    classification = "gated" if after == "required" else "read_only"
    assert catalog["handoff_to_helper"]["classification"] == classification
    assert catalog["cancel_helper_task"]["classification"] == classification
    # The log names the agent and what changed, and never the address (FR-030).
    assert changed_lines(caplog) == [
        f"Agent changed: helper ({record.id}): its approval choice is now {after}, was {before}"
    ]
    assert not holds_port(caplog.text, CHANGED_PORT)


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("named", [True, False])
def test_a_change_that_changes_nothing_answers_with_the_agent_as_it_is(
    make_a2a_app: Callable[..., A2aApp],
    caplog: pytest.LogCaptureFixture,
    read_only: bool,
    named: bool,
) -> None:
    made = helper_app(make_a2a_app, read_only=read_only)
    record = made.saved["helper"]
    choice = "none" if read_only else "required"
    # The choice the agent has, or no value at all: a field left out is kept.
    body = {"version": 1, "approval": choice} if named else {"version": 1}
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        was = by_id(agents(client))[record.id]
        revision = client.portal.call(made.saved_agents.revision)
        response = change(client, record.id, json=body)
        after = client.portal.call(made.saved_agents.revision)
        kept = client.portal.call(made.saved_agents.get_agent, record.id)

    assert response.status_code == 200, response.text
    assert response.json() == {"agent": was, "given_up": 0}
    assert (was["approval"], was["version"]) == (choice, 1)
    assert kept == record and after == revision
    assert changed_lines(caplog) == []


@pytest.mark.parametrize("version", [1, 3, 99])
@pytest.mark.parametrize("approval", ["none", "required"])
def test_a_change_at_a_version_that_is_not_current_is_refused_and_changes_nothing(
    make_a2a_app: Callable[..., A2aApp],
    caplog: pytest.LogCaptureFixture,
    version: int,
    approval: str,
) -> None:
    made = helper_app(make_a2a_app)
    record = made.saved["helper"]
    transport = made.transport_at(CHANGED_URL)
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        # Changed elsewhere since the page read it: the agent is at version 2 now.
        current, _ = client.portal.call(
            lambda: made.saved_agents.change(record.id, 1, read_only=True)
        )
        before = what_exists(client)
        requests = list(transport.requests)
        response = change(client, record.id, json={"version": version, "approval": approval})
        after = what_exists(client)
        kept = client.portal.call(made.saved_agents.get_agent, record.id)

    assert current.version == 2
    assert refusal(response, 409, "agent_changed") == {"code": "agent_changed", "message": CHANGED}
    # Nothing changed, whatever the request asked for (SC-016).
    assert kept == current and after == before
    assert transport.requests == requests
    assert changed_lines(caplog) == []


def test_an_environment_agent_cannot_be_changed(make_a2a_app: Callable[..., A2aApp]) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}})
    with TestClient(made.app) as client:
        before = what_exists(client)
        responses = [
            change(client, "env.hello", json={"version": 1, "approval": approval})
            for approval in ("none", "required")
        ]
        unknown = change(client, "env.nobody", json={"version": 1, "approval": "none"})
        after = what_exists(client)
    for response in responses:
        assert refusal(response, 409, "agent_read_only")["message"] == ENVIRONMENT_READ_ONLY
    assert refusal(unknown, 404, "agent_not_found")["message"] == NOT_FOUND
    assert after == before
    assert before[0]["agents"][0]["approval"] == "required"


def test_a_saved_agent_that_is_not_in_use_cannot_be_changed(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    # The environment has an agent of the saved agent's name: the saved one is not in use.
    made = helper_app(make_a2a_app, agents={"helper": {}})
    record = made.saved["helper"]
    with TestClient(made.app) as client:
        listed = by_id(agents(client))[record.id]
        response = change(client, record.id, json={"version": 1, "approval": "none"})
        kept = client.portal.call(made.saved_agents.get_agent, record.id)
    assert listed["state"] == "not_in_use"
    assert refusal(response, 409, "agent_read_only")["message"] == ONLY_REMOVED
    assert kept == record


def test_a_change_of_an_agent_that_does_not_exist_is_not_found(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = helper_app(make_a2a_app)
    record = made.saved["helper"]
    with TestClient(made.app) as client:
        unknown = change(client, UNKNOWN_ID, json={"version": 1, "approval": "none"})
        client.portal.call(made.saved_agents.remove, record.id, 1)
        removed = change(client, record.id, json={"version": 1, "approval": "none"})
    for response in (unknown, removed):
        assert refusal(response, 404, "agent_not_found")["message"] == NOT_FOUND


@pytest.mark.parametrize(
    ("body", "loc", "kind"),
    [
        # A saved agent's name never changes (FR-009), and its address not by this phase's route.
        ({"version": 1, "approval": "none", "name": "other"}, "name", "extra_forbidden"),
        ({"version": 1, "name": "other"}, "name", "extra_forbidden"),
        ({"version": 1, "approval": "none", "url": CHANGED_URL}, "url", "extra_forbidden"),
        ({"version": 1, "approval": "none", "read_only": True}, "read_only", "extra_forbidden"),
        ({"approval": "none"}, "version", "missing"),
        ({"version": 0, "approval": "none"}, "version", "greater_than_equal"),
        ({"version": "1", "approval": "none"}, "version", "int_type"),
        ({"version": 1.5, "approval": "none"}, "version", "int_type"),
        ({"version": None, "approval": "none"}, "version", "int_type"),
        ({"version": 1, "approval": "maybe"}, "approval", "literal_error"),
        ({"version": 1, "approval": True}, "approval", "literal_error"),
        ({"version": 1, "approval": None}, "approval", "literal_error"),
    ],
)
def test_a_change_whose_body_is_not_the_shape_is_refused_and_changes_nothing(
    make_a2a_app: Callable[..., A2aApp], body: dict[str, Any], loc: str, kind: str
) -> None:
    made = helper_app(make_a2a_app)
    record = made.saved["helper"]
    with TestClient(made.app) as client:
        response = change(client, record.id, json=body)
        kept = client.portal.call(made.saved_agents.get_agent, record.id)
    assert response.status_code == 422, response.text
    (error,) = response.json()["detail"]
    assert (error["type"], error["loc"]) == (kind, ["body", loc])
    assert set(error) == {"type", "loc", "msg"}
    assert not holds_port(response.text, CHANGED_PORT)
    assert kept == record


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        ("version=1&approval=none", "application/x-www-form-urlencoded"),
        ('{"version": 1, "approval": "none"}', "text/plain"),
        ("version=1 approval=none", "application/json"),
        ("", "application/json"),
    ],
)
def test_a_change_whose_body_is_not_sent_as_json_is_refused(
    make_a2a_app: Callable[..., A2aApp], content: str, content_type: str
) -> None:
    made = helper_app(make_a2a_app)
    record = made.saved["helper"]
    with TestClient(made.app) as client:
        response = change(
            client, record.id, content=content, headers={"Content-Type": content_type}
        )
        kept = client.portal.call(made.saved_agents.get_agent, record.id)
    assert response.status_code == 422, response.text
    for error in response.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}
    assert kept == record


@pytest.mark.parametrize("agent_id", ["Helper", "env.", "env.Hello", "0" * 31, "0" * 33, "x" * 32])
def test_a_change_of_an_id_in_neither_form_is_refused(
    make_a2a_app: Callable[..., A2aApp], agent_id: str
) -> None:
    made = helper_app(make_a2a_app)
    with TestClient(made.app) as client:
        response = change(client, agent_id, json={"version": 1, "approval": "none"})
    assert response.status_code == 422, response.text
    (error,) = response.json()["detail"]
    assert error["loc"] == ["path", "agent_id"] and set(error) == {"type", "loc", "msg"}


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://elsewhere.example"},
        {"Origin": "http://elsewhere.example", "Sec-Fetch-Site": "same-origin"},
    ],
)
def test_a_change_made_by_a_page_of_another_origin_is_refused_before_anything_is_looked_at(
    make_a2a_app: Callable[..., A2aApp], headers: dict[str, str]
) -> None:
    made = helper_app(make_a2a_app)
    record = made.saved["helper"]
    body = {"version": 1, "approval": "none"}
    with TestClient(made.app) as client:
        responses = [
            change(client, record.id, json=body, headers=headers),
            # Refused also when the request would have been refused for itself.
            change(client, UNKNOWN_ID, json=body, headers=headers),
            change(client, record.id, json={"name": "other"}, headers=headers),
        ]
        kept = client.portal.call(made.saved_agents.get_agent, record.id)
        own = {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}
        served = change(client, record.id, json=body, headers=own)
    for refused in responses:
        detail = refusal(refused, 403, "other_origin")
        assert detail["message"].endswith(
            "Agents are changed from the console, or by a client that is not a web page."
        )
    assert kept == record
    # The console's own page is served.
    assert served.status_code == 200, served.text


def test_a_change_stands_when_this_instance_cannot_take_it_up_at_once(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = helper_app(make_a2a_app)
    record = made.saved["helper"]
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        registry = made.runtime.registry
        assert registry is not None

        async def failing() -> Any:
            raise RuntimeError(f"could not connect to {CHANGED_URL}")

        monkeypatch.setattr(registry, "refresh", failing)
        response = change(client, record.id, json={"version": 1, "approval": "none"})
        monkeypatch.undo()
        listed = by_id(agents(client))[record.id]

    assert response.status_code == 200, response.text
    agent = response.json()["agent"]
    assert (agent["approval"], agent["version"]) == ("none", 2)
    # Changed all the same: this instance, like every other, takes it up at its next context.
    assert (listed["approval"], listed["version"], listed["state"]) == ("none", 2, "reachable")
    lines = route_lines(caplog)
    assert (
        "Agent helper was changed, and this instance could not take it up at once: RuntimeError"
        in lines
    )
    assert len(changed_lines(caplog)) == 1
    assert not holds_port(caplog.text, CHANGED_PORT)


@pytest.mark.parametrize("read", ["get_agent", "pending_counts", "change"])
def test_a_change_is_answered_with_an_error_when_the_store_fails(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    read: str,
) -> None:
    made = helper_app(make_a2a_app)
    record = made.saved["helper"]

    async def failing(*_args: Any, **_values: Any) -> Any:
        raise RuntimeError(f"connection to db.internal failed: {MARKER} {CHANGED_URL}")

    store = made.store if read == "pending_counts" else made.saved_agents
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        monkeypatch.setattr(store, read, failing)
        response = change(client, record.id, json={"version": 1, "approval": "none"})
        monkeypatch.undo()
        kept = client.portal.call(made.saved_agents.get_agent, record.id)

    message = (
        "The agent could not be changed."
        if read == "change"
        else "The saved agents could not be read."
    )
    assert refusal(response, 500, "internal") == {"code": "internal", "message": message}
    for text in (response.text, caplog.text):
        assert MARKER not in text and "db.internal" not in text
    assert not holds_port(response.text, CHANGED_PORT)
    assert not holds_port(caplog.text, CHANGED_PORT)
    assert kept == record


def test_a_service_put_together_without_agents_cannot_change_one(
    make_runtime: Callable[..., Any], make_app: Callable[..., Any]
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        response = change(client, UNKNOWN_ID, json={"version": 1, "approval": "none"})
    assert refusal(response, 500, "internal")["message"] == "This instance cannot change agents."
