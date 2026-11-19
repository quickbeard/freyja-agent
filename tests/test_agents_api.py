"""005 User Story 1: the list of agents Freyja can hand work to (`GET /agents`).

FR-001, FR-003, FR-004, FR-017, FR-028; contracts/openapi.yaml. The list is read from the
saved-agent store and from what this instance holds in memory. It contacts no agent, shows an
environment agent's address nowhere, and answers with an error, never with an earlier list,
when the store cannot be read.

Every agent is the stand-in, inside the test process.
"""

import dataclasses
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.a2a_stub import make_stub, stub_card
from tests.conftest import A2aApp, saved_card
from tests.fakes import ScriptedChatModel
from tests.test_agents_startup import seed_task

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
        for part in ("127.0.0.1", "localhost", "://", "9200", "9999", "9101"):
            assert part not in text, (part, text)
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
