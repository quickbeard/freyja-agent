"""005 User Story 3: checking an agent (`POST /agents/{agent_id}/check`).

FR-013 to FR-016, FR-026, FR-027, FR-029, FR-030; SC-011, SC-012; research R6, R15. A check
reads an agent's card again and says whether the agent can be used. It updates this
instance's view of the agent and, when the card can be used, the card details kept or shown
for it, and it changes nothing else: no address, no approval choice, no thread, and no task.
The agent is sent nothing but the one request for its card.

Every agent is the stand-in, inside the test process.
"""

import asyncio
import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from freyja_agent.delegates.agent_cards import SENTENCES
from tests.a2a_stub import StubAgent, make_stub, stub_card
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import A2aApp, holds_port, saved_card
from tests.fakes import ScriptedChatModel
from tests.test_a2a_handoff import DONE, handoff, run, tool_results
from tests.test_agents_api import agents, by_id, refusal
from tests.test_agents_startup import CARD_PATH, seed_task, sent

ROUTES = "freyja_agent.api.agents"
# Ports of this file's own: the scrubber is the process's, and forgets nothing.
SAVED_URL = "http://127.0.0.1:9611"
OTHER_URL = "http://127.0.0.1:9612"
KINDS = ["saved", "environment"]
BUSY = "This agent is already being checked. Wait for that check to end."
FULL = (
    "4 agent cards are already being read on this instance. Try again when one of those reads ends."
)
RESULT = {"usable", "reason", "message", "checked_at", "recorded", "card_changed"}


@dataclasses.dataclass
class Checked:
    """An app with one agent named `helper`, saved or from the environment."""

    made: A2aApp
    agent_id: str
    stub: StubAgent
    transport: Any


def one_agent(
    make_a2a_app: Callable[..., A2aApp],
    kind: str,
    model: ScriptedChatModel | None = None,
    *,
    stub: StubAgent | None = None,
    read_only: bool = False,
    **options: Any,
) -> Checked:
    stub = stub or make_stub("helper")
    if kind == "saved":
        made = make_a2a_app(
            model or ScriptedChatModel(),
            {},
            saved={"helper": {"url": SAVED_URL, "stub": stub, "read_only": read_only}},
            **options,
        )
        return Checked(made, made.saved["helper"].id, stub, made.transport_at(SAVED_URL))
    made = make_a2a_app(
        model or ScriptedChatModel(),
        {"helper": {"read_only": read_only}},
        stubs={"helper": stub},
        **options,
    )
    return Checked(made, "env.helper", stub, made.transports["helper"])


def check(client: TestClient, agent_id: str, **kwargs: Any) -> Any:
    return client.post(f"/agents/{agent_id}/check", **kwargs)


def checked(client: TestClient, agent_id: str) -> dict[str, Any]:
    response = check(client, agent_id)
    assert response.status_code == 200, response.text
    assert set(response.json()) == RESULT
    return response.json()


def requests(transport: Any) -> list[tuple[str, str]]:
    return [(request.method, request.path) for request in transport.requests]


def lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == ROUTES]


def held(made: A2aApp) -> dict[Any, int]:
    registry = made.runtime.registry
    assert registry is not None
    return dict(registry._holders)


# A check that finds the agent usable.


@pytest.mark.parametrize("kind", KINDS)
def test_a_check_of_an_agent_that_answers_says_so_and_changes_nothing_else(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture, kind: str
) -> None:
    app = one_agent(make_a2a_app, kind)
    made = app.made
    ref = made.saved["helper"].ref if kind == "saved" else None
    seed_task(made, "helper", "task-1", ref=ref, thread="c-1")
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        before = by_id(agents(client))[app.agent_id]
        at_startup = requests(app.transport)
        revision = client.portal.call(made.saved_agents.revision)
        task = client.portal.call(made.store.get_task, "helper", "task-1")
        result = checked(client, app.agent_id)
        after = by_id(agents(client))[app.agent_id]
        holders = held(made)
        task_after = client.portal.call(made.store.get_task, "helper", "task-1")
        revision_after = client.portal.call(made.saved_agents.revision)
        threads = dict(made.runtime.agent.checkpointer.storage)

    assert result["usable"] is True
    assert (result["reason"], result["message"]) == (None, None)
    assert (result["recorded"], result["card_changed"]) == (True, False)
    # The list shows the agent reachable at the time of this check, with the same details.
    assert after["state"] == "reachable"
    assert after["checked_at"] == result["checked_at"]
    assert datetime.fromisoformat(after["checked_at"]) >= datetime.fromisoformat(
        before["checked_at"]
    )
    assert after["card"] == before["card"] == saved_card(app.stub)
    # Nothing else of the agent changed, and neither did its task, any thread, or the
    # revision the other instances watch.
    for field in ("id", "name", "source", "approval", "url", "version", "pending_tasks"):
        assert after[field] == before[field], field
    assert after["pending_tasks"] == 1
    assert task_after == task
    assert revision_after == revision
    assert threads == {}
    # One request reached the agent, for its card: no message and no task (SC-011).
    assert at_startup == [("GET", CARD_PATH)]
    assert requests(app.transport) == [("GET", CARD_PATH)] * 2
    assert app.stub.record.messages == [] and app.stub.record.cancels == []
    # The set the request took was given back.
    assert holders == {}
    # The log names the agent, the result, and that its card details were not replaced.
    assert (
        f"Agent checked: helper ({app.agent_id}): it can be used; "
        "its card details were not replaced" in lines(caplog)
    )
    for port in (9611, 9999):
        assert not holds_port(caplog.text, port)
        assert not holds_port(str(result), port)


# A check that finds the agent cannot be used.


def break_it(app: Checked, code: str) -> None:
    """Make the stand-in's card unusable as ``code``."""
    replaced = {
        "no_card": lambda: make_stub("helper", card_status=404, card_body=b""),
        "unreadable": lambda: make_stub("helper", card_body=b"this is no card"),
        "redirect": lambda: make_stub("helper", redirect_card_to="http://127.0.0.1:9612/a"),
        "too_large": lambda: make_stub("helper", card_body=b"x" * (1024 * 1024 + 1)),
        "auth_required": lambda: make_stub("helper", card=stub_card("helper", security=True)),
        "no_text": lambda: make_stub(
            "helper", card=stub_card("helper", input_modes=("image/png",))
        ),
        "no_interface": lambda: make_stub("helper", card=stub_card("helper", binding="GRPC")),
    }
    app.transport.down = code == "no_answer"
    app.transport.hang = code == "timeout"
    if code in replaced:
        app.transport.app = replaced[code]().app


def mend_it(app: Checked) -> None:
    app.transport.down = app.transport.hang = False
    app.transport.app = app.stub.app


CODES = list(SENTENCES)


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("kind", KINDS)
def test_a_check_of_an_agent_that_cannot_be_used_says_why_and_the_agent_stays_configured(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    kind: str,
    code: str,
) -> None:
    model = ScriptedChatModel(
        script=[
            handoff("While it cannot be used.", agent="helper"),
            DONE,
            handoff("Once it answers again.", agent="helper", call_id="h2"),
        ]
    )
    # The check has the limit for reading a card; a hand-off reads within its own.
    app = one_agent(
        make_a2a_app,
        kind,
        model,
        read_only=True,
        a2a_startup_timeout_seconds=0.4,
        a2a_call_timeout_seconds=0.6,
    )
    made = app.made
    sentence = SENTENCES[code].format(seconds="0.4")
    in_a_hand_off = SENTENCES[code].format(seconds="0.6")
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        before = by_id(agents(client))[app.agent_id]
        break_it(app, code)
        began = time.monotonic()
        result = checked(client, app.agent_id)
        took = time.monotonic() - began
        after = by_id(agents(client))[app.agent_id]
        seen = requests(app.transport)
        # The link holds no usable card any more: a hand-off fails with the reason.
        failing = run(client, "c-2", sse_events)
        mend_it(app)
        # A later read succeeds without another check, inside the hand-off's own limit.
        working = run(client, "c-2", sse_events, "again")
        recovered = by_id(agents(client))[app.agent_id]
        holders = held(made)

    assert result["usable"] is False
    # The card problem's code and the service's own sentence for it (FR-016).
    assert (result["reason"], result["message"]) == (code, sentence)
    assert result["card_changed"] is False
    assert result["recorded"] is True
    # One that never answers is given up at the time limit (SC-012).
    assert took < 3
    # The list shows it unreachable, with that reason and the time of the check.
    assert (after["state"], after["reason"]) == ("unreachable", sentence)
    assert after["checked_at"] == result["checked_at"]
    # It stays configured as it was, with the details kept or shown before.
    for field in ("id", "name", "source", "approval", "url", "version", "card"):
        assert after[field] == before[field], field
    # One request for the card, and nothing else.
    assert seen == [("GET", CARD_PATH)] * 2
    (kept_back,) = tool_results(failing, "handoff_to_helper")
    assert kept_back["content"] == f"Agent 'helper' cannot be used: {in_a_hand_off}"
    assert failing[-1] == ("end", {"status": "completed"})
    (result_after,) = tool_results(working, "handoff_to_helper")
    assert "completed task" in result_after["content"], result_after
    assert recovered["state"] == "reachable"
    assert [m.text for m in app.stub.record.messages] == ["Once it answers again."]
    assert holders == {}
    assert (
        f"Agent checked: helper ({app.agent_id}): it cannot be used: {sentence.rstrip('.')}; "
        "its card details were not replaced" in lines(caplog)
    )
    # The same warning as at startup, since a card read for the agent failed (FR-030).
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == ROUTES and record.levelno == logging.WARNING
    ] == [f"Agent helper cannot be used: {sentence}"]
    for port in (9611, 9612, 9999):
        assert not holds_port(str(result), port)
        assert not holds_port("\n".join(lines(caplog)), port)


# Card details that changed.


def test_a_saved_agent_s_changed_details_are_kept_and_reach_every_instance_s_next_run(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture
) -> None:
    app = one_agent(make_a2a_app, "saved")
    first = app.made
    second = make_a2a_app(
        ScriptedChatModel(),
        {},
        instance_id="instance-b",
        store=first.store,
        saved_agents=first.saved_agents,
        at=first.at,
    )
    record = first.saved["helper"]
    later = make_stub("helper", card=stub_card("helper", description="Now it writes reports."))

    def described(client: TestClient) -> str:
        tools = {tool["name"]: tool for tool in client.get("/tools").json()["tools"]}
        return tools["handoff_to_helper"]["description"]

    with caplog.at_level(logging.INFO), TestClient(first.app) as one, TestClient(second.app) as two:
        assert "Now it writes reports." not in described(one) + described(two)
        revision = one.portal.call(first.saved_agents.revision)
        app.transport.app = later.app
        result = checked(one, app.agent_id)
        kept = one.portal.call(first.saved_agents.get_agent, record.id)
        moved_to = one.portal.call(first.saved_agents.revision)
        here, there = described(one), described(two)
        listed = by_id(agents(two))[record.id]
        # The same card again: nothing is replaced, and nothing moves.
        again = checked(one, app.agent_id)
        still = one.portal.call(first.saved_agents.revision)

    assert (result["usable"], result["recorded"], result["card_changed"]) == (True, True, True)
    # The details just read are the kept ones, and nothing else of the agent changed.
    assert kept.card == saved_card(later) != record.card
    assert dataclasses.replace(kept, card=record.card, card_read_at=record.card_read_at) == record
    # The revision moved, so every instance describes the tools from them in its next run.
    assert moved_to == revision + 1
    assert "Now it writes reports." in here and "Now it writes reports." in there
    assert listed["card"] == saved_card(later)
    assert (again["usable"], again["recorded"], again["card_changed"]) == (True, True, False)
    assert still == moved_to
    assert (
        f"Agent checked: helper ({record.id}): it can be used; its card details were replaced"
        in lines(caplog)
    )


def test_an_environment_agent_s_changed_details_are_shown_from_the_next_run_on_this_instance(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    app = one_agent(make_a2a_app, "environment")
    made = app.made
    later = make_stub("helper", card=stub_card("helper", description="Now it writes reports."))
    with TestClient(made.app) as client:
        revision = client.portal.call(made.saved_agents.revision)
        app.transport.app = later.app
        result = checked(client, app.agent_id)
        tools = {tool["name"]: tool for tool in client.get("/tools").json()["tools"]}
        listed = by_id(agents(client))[app.agent_id]
        again = checked(client, app.agent_id)
        revision_after = client.portal.call(made.saved_agents.revision)
    assert (result["usable"], result["recorded"], result["card_changed"]) == (True, True, True)
    assert "Now it writes reports." in tools["handoff_to_helper"]["description"]
    assert listed["card"] == saved_card(later)
    assert again["card_changed"] is False
    # Nothing is stored for an environment agent: the other instances have their own reading.
    assert revision_after == revision


def test_the_first_read_of_an_environment_agent_s_card_is_not_a_change(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    app = one_agent(make_a2a_app, "environment")
    app.transport.down = True
    with TestClient(app.made.app) as client:
        before = by_id(agents(client))[app.agent_id]
        app.transport.down = False
        result = checked(client, app.agent_id)
        after = by_id(agents(client))[app.agent_id]
    assert (before["state"], before["card"]) == ("unreachable", None)
    # No details were shown before, so none were replaced by different ones.
    assert (result["usable"], result["recorded"], result["card_changed"]) == (True, True, False)
    assert (after["state"], after["card"]) == ("reachable", saved_card(app.stub))


# The limits.


@pytest.mark.parametrize("kind", KINDS)
def test_a_second_check_of_an_agent_that_is_being_checked_is_refused_and_contacts_nothing(
    make_a2a_app: Callable[..., A2aApp], kind: str
) -> None:
    app = one_agent(make_a2a_app, kind, a2a_startup_timeout_seconds=3.0)
    made = app.made
    answers: dict[str, Any] = {}
    with TestClient(made.app) as client:
        app.transport.hang = True
        first = threading.Thread(target=lambda: answers.update(first=check(client, app.agent_id)))
        first.start()
        deadline = time.monotonic() + 5
        while len(app.transport.requests) < 2:
            assert time.monotonic() < deadline, "the first check never read the card"
            time.sleep(0.01)
        began = time.monotonic()
        second = check(client, app.agent_id)
        took = time.monotonic() - began
        seen = len(app.transport.requests)
        first.join(10)
        app.transport.hang = False
        afterwards = checked(client, app.agent_id)

    assert refusal(second, 409, "check_busy")["message"] == BUSY
    assert took < 1
    # The refused check asked the agent for nothing.
    assert seen == 2
    assert answers["first"].json()["reason"] == "timeout"
    # The agent's slot was given back when its check ended.
    assert afterwards["usable"] is True


def test_a_fifth_card_read_at_once_is_refused_and_contacts_nothing(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    app = one_agent(make_a2a_app, "saved")
    with TestClient(app.made.app) as client:
        slots = app.made.runtime.card_reads
        for _ in range(4):
            slots.take(None)
        response = check(client, app.agent_id)
        seen = len(app.transport.requests)
        for _ in range(4):
            slots.release(None)
        afterwards = checked(client, app.agent_id)
    assert refusal(response, 409, "check_busy")["message"] == FULL
    assert seen == 1
    assert afterwards["usable"] is True


@pytest.mark.parametrize(("configured", "limit"), [(0.5, 0.5), (300.0, 20.0)])
def test_the_card_is_read_within_the_lesser_of_the_startup_limit_and_twenty_seconds(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    configured: float,
    limit: float,
) -> None:
    app = one_agent(make_a2a_app, "saved", a2a_startup_timeout_seconds=configured)
    limits: list[float] = []
    with TestClient(app.made.app) as client:
        (link,) = app.made.runtime.agents
        connect = link.connect

        async def connecting(*args: Any, limit: float) -> Any:
            limits.append(limit)
            return await connect(*args, limit=limit)

        monkeypatch.setattr(link, "connect", connecting)
        checked(client, app.agent_id)
    assert limits == [limit]


def test_a_slot_is_given_back_when_the_check_ends_in_a_way_no_rule_expects(
    make_a2a_app: Callable[..., A2aApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    app = one_agent(make_a2a_app, "saved")
    with TestClient(app.made.app) as client:
        (link,) = app.made.runtime.agents

        async def failing(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("the read failed")

        monkeypatch.setattr(link, "connect", failing)
        with pytest.raises(RuntimeError):
            check(client, app.agent_id)
        monkeypatch.undo()
        holders = held(app.made)
        afterwards = checked(client, app.agent_id)
    assert holders == {}
    assert afterwards["usable"] is True


# An agent that changed while it was checked.


@pytest.mark.parametrize("change", ["removed", "moved"])
@pytest.mark.parametrize("usable", [True, False])
def test_a_check_of_an_agent_that_was_moved_or_removed_meanwhile_is_not_recorded(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    change: str,
    usable: bool,
) -> None:
    app = one_agent(make_a2a_app, "saved")
    made = app.made
    record = made.saved["helper"]
    later = make_stub("helper", card=stub_card("helper", description="Read during the check."))
    elsewhere = make_stub("helper")
    made.add_stub(OTHER_URL, elsewhere)
    with caplog.at_level(logging.INFO), TestClient(made.app) as client:
        (link,) = made.runtime.agents
        connect = link.connect

        async def changed_meanwhile(*args: Any, **kwargs: Any) -> Any:
            # Another instance changes the agent while this one reads its card.
            if change == "removed":
                await made.saved_agents.remove(record.id, record.version)
            else:
                await made.saved_agents.change(
                    record.id, record.version, url=OTHER_URL, card=saved_card(elsewhere)
                )
            return await connect(*args, **kwargs)

        monkeypatch.setattr(link, "connect", changed_meanwhile)
        if usable:
            app.transport.app = later.app
        else:
            app.transport.down = True
        result = checked(client, app.agent_id)
        monkeypatch.undo()
        kept = client.portal.call(made.saved_agents.get_agent, record.id)

    assert result["usable"] is usable
    # The result is of an agent that is no longer what was checked: nothing was kept.
    assert (result["recorded"], result["card_changed"]) == (False, False)
    if change == "removed":
        assert kept is None
    else:
        assert kept.card == saved_card(elsewhere)
        assert kept.url == OTHER_URL
    assert any(
        line.startswith(f"Agent checked: helper ({record.id}): ")
        and line.endswith("the agent was moved or removed meanwhile, so nothing was recorded")
        for line in lines(caplog)
    )


# What cannot be checked.


def test_an_agent_that_does_not_exist_is_not_found(make_a2a_app: Callable[..., A2aApp]) -> None:
    app = one_agent(make_a2a_app, "saved")
    environment = make_a2a_app(ScriptedChatModel(), {"hello": {}})
    with TestClient(app.made.app) as client:
        unknown = [check(client, "0" * 32), check(client, "env.helper"), check(client, "env.x")]
        seen = len(app.transport.requests)
    with TestClient(environment.app) as client:
        # A saved agent's name is not an environment agent's id, and the other way round.
        unknown.append(check(client, "env.helper"))
        unknown.append(check(client, "f" * 32))
    for response in unknown:
        assert refusal(response, 404, "agent_not_found") == {
            "code": "agent_not_found",
            "message": "There is no agent with this id.",
        }
    assert seen == 1


def test_a_saved_agent_that_is_not_in_use_cannot_be_checked(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"helper": {}}, saved={"helper": {"url": SAVED_URL}})
    record = made.saved["helper"]
    with TestClient(made.app) as client:
        response = check(client, record.id)
        # The environment's agent of that name is the one in use, and can be checked.
        environment = checked(client, "env.helper")
    assert refusal(response, 409, "agent_read_only") == {
        "code": "agent_read_only",
        "message": "This saved agent is not in use, so it cannot be checked.",
    }
    assert made.transport_at(SAVED_URL).requests == []
    assert environment["usable"] is True


@pytest.mark.parametrize(
    "agent_id",
    ["helper", "ENV.helper", "env.", "env.Helper", "0" * 31, "0" * 33, "g" * 32, "env.a b"],
)
def test_an_id_in_neither_form_is_refused(
    make_a2a_app: Callable[..., A2aApp], agent_id: str
) -> None:
    app = one_agent(make_a2a_app, "saved")
    with TestClient(app.made.app) as client:
        response = check(client, agent_id)
    assert response.status_code == 422, response.text
    (error,) = response.json()["detail"]
    assert error["loc"] == ["path", "agent_id"]
    assert set(error) == {"type", "loc", "msg"}
    assert len(app.transport.requests) == 1


@pytest.mark.parametrize(
    "headers", [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://elsewhere.example"}]
)
@pytest.mark.parametrize("kind", KINDS)
def test_a_check_asked_by_a_page_of_another_origin_is_refused_and_contacts_nothing(
    make_a2a_app: Callable[..., A2aApp], kind: str, headers: dict[str, str]
) -> None:
    app = one_agent(make_a2a_app, kind)
    with TestClient(app.made.app) as client:
        response = check(client, app.agent_id, headers=headers)
        # A route with no body is what a form on any page can reach: refused all the same.
        form = check(
            client,
            app.agent_id,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            content="",
        )
    for refused in (response, form):
        assert refusal(refused, 403, "other_origin")["message"].endswith(
            "Agents are changed from the console, or by a client that is not a web page."
        )
    assert len(app.transport.requests) == 1


def test_a_check_is_answered_with_an_error_when_the_agents_cannot_be_read(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = one_agent(make_a2a_app, "saved")

    async def unreadable() -> int:
        raise RuntimeError("connection to db.internal failed: password=hunter2")

    with TestClient(app.made.app) as client:
        monkeypatch.setattr(app.made.saved_agents, "revision", unreadable)
        response = check(client, app.agent_id)
    assert refusal(response, 500, "internal")["message"] == "The saved agents could not be read."
    for text in (response.text, caplog.text):
        assert "hunter2" not in text and "db.internal" not in text
    assert len(app.transport.requests) == 1


def test_a_check_whose_result_cannot_be_kept_is_answered_with_an_error(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = one_agent(make_a2a_app, "saved")

    async def unwritable(*_args: Any) -> Any:
        raise RuntimeError("connection to db.internal failed: password=hunter2")

    later = make_stub("helper", card=stub_card("helper", description="Now it writes reports."))
    with TestClient(app.made.app) as client:
        # Other details than the kept ones, which the check then has to write.
        app.transport.app = later.app
        monkeypatch.setattr(app.made.saved_agents, "replace_card", unwritable)
        response = check(client, app.agent_id)
        monkeypatch.undo()
        holders = held(app.made)
        afterwards = checked(client, app.agent_id)
    assert refusal(response, 500, "internal")["message"] == (
        "The check's result could not be recorded."
    )
    for text in (response.text, caplog.text):
        assert "hunter2" not in text and "db.internal" not in text
    assert holders == {}
    # The next check finds the same card and keeps it.
    assert (afterwards["usable"], afterwards["card_changed"]) == (True, True)


def test_a_check_sends_the_agent_nothing_but_the_request_for_its_card(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    app = one_agent(make_a2a_app, "saved")
    with TestClient(app.made.app) as client:
        for _ in range(3):
            checked(client, app.agent_id)
    assert requests(app.transport) == [("GET", CARD_PATH)] * 4
    assert sent(app.transport) == 0


# A check beside another user of the same link: a hand-off in flight, or another reading.


class CardOnly:
    """An ASGI app that serves no card while ``broken``, and the stand-in for everything else."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.broken = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self.broken and scope["path"].endswith(CARD_PATH):
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await self.app(scope, receive, send)


class Slow(StreamingASGITransport):
    """A stand-in's transport that answers after ``delay`` seconds."""

    delay = 0.0

    async def handle_async_request(self, request: Any) -> Any:
        self.requests.append(None)  # counted when asked, not when answered
        await asyncio.sleep(self.delay)
        self.requests.pop()
        return await super().handle_async_request(request)


NO_CARD = SENTENCES["no_card"]


@pytest.mark.parametrize("streaming", [True, False])
def test_a_check_that_fails_under_a_hand_off_in_flight_breaks_neither(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, streaming: bool
) -> None:
    # The agent follows its task by a stream, or is asked about it every second.
    stub = make_stub("helper", card=stub_card("helper", streaming=streaming))
    model = ScriptedChatModel(script=[handoff("slow 1.5 report", agent="helper"), DONE])
    made = make_a2a_app(
        model,
        {"helper": {"read_only": True}},
        stubs={"helper": stub},
        a2a_startup_timeout_seconds=1.0,
        a2a_call_timeout_seconds=10.0,
    )
    wrapped = CardOnly(stub.app)
    made.transports["helper"].app = wrapped
    events: dict[str, Any] = {}
    with TestClient(made.app) as client:
        running = threading.Thread(target=lambda: events.update(run=run(client, "c-3", sse_events)))
        running.start()
        deadline = time.monotonic() + 5
        while not stub.record.messages:
            assert time.monotonic() < deadline, "the hand-off never reached the agent"
            time.sleep(0.01)
        # Only the card is gone: the agent goes on answering the task it was handed.
        wrapped.broken = True
        result = checked(client, "env.helper")
        right_after = by_id(agents(client))["env.helper"]
        running.join(20)
        assert not running.is_alive()
        listed = by_id(agents(client))["env.helper"]
        (link,) = made.runtime.agents
        connected = link.connected

    assert (result["usable"], result["reason"]) == (False, "no_card")
    assert (right_after["state"], right_after["reason"]) == ("unreachable", NO_CARD)
    # The hand-off that was under way finishes on the connection it began with.
    (handed,) = tool_results(events["run"], "handoff_to_helper")
    assert handed["content"].startswith("Agent 'helper' completed task "), handed
    # Its ending says nothing of the card: the link holds none, nothing more is sent over it,
    # and the agent stays listed as it was found, with the service's own sentence.
    assert connected is False
    assert (listed["state"], listed["reason"]) == ("unreachable", NO_CARD)
    assert listed["checked_at"] == result["checked_at"]


def test_a_check_takes_the_reading_another_caller_is_making_and_requests_nothing_itself(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    stub = make_stub("helper")
    made = make_a2a_app(
        ScriptedChatModel(),
        {"helper": {}},
        stubs={"helper": stub},
        a2a_startup_timeout_seconds=3.0,
        a2a_call_timeout_seconds=10.0,
    )
    transport = Slow(stub.app)
    made.transports["helper"] = transport
    transport.down = True
    with TestClient(made.app) as client:
        (link,) = made.runtime.agents
        assert link.connected is False
        asked_at_startup = len(transport.requests)
        transport.down, transport.delay = False, 0.6
        # As a hand-off does for a link that is not connected, inside its own limit.
        reader = client.portal.start_task_soon(lambda: link.connect(limit=link.call_timeout))
        deadline = time.monotonic() + 5
        while len(transport.requests) == asked_at_startup:
            assert time.monotonic() < deadline, "the other reading never started"
            time.sleep(0.01)
        result = checked(client, "env.helper")
        reader.result(5)
        listed = by_id(agents(client))["env.helper"]
    # One reading served both: the agent was asked for its card once since startup.
    assert len(transport.requests) == asked_at_startup + 1
    assert (result["usable"], result["recorded"], result["card_changed"]) == (True, True, False)
    assert (listed["state"], listed["card"]) == ("reachable", saved_card(stub))
    assert listed["checked_at"] == result["checked_at"]


def test_a_check_that_waited_for_another_reading_still_ends_within_its_own_limit(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    stub = make_stub("helper")
    made = make_a2a_app(
        ScriptedChatModel(),
        {"helper": {}},
        stubs={"helper": stub},
        a2a_startup_timeout_seconds=1.0,
        a2a_call_timeout_seconds=10.0,
    )
    transport = made.transports["helper"]
    transport.down = True
    answers: dict[str, Any] = {}
    with TestClient(made.app) as client:
        (link,) = made.runtime.agents
        transport.down, transport.hang = False, True
        reader = client.portal.start_task_soon(lambda: link.connect(limit=link.call_timeout))
        deadline = time.monotonic() + 5
        while len(transport.requests) < 2:
            assert time.monotonic() < deadline, "the other reading never started"
            time.sleep(0.01)
        began = time.monotonic()
        checking = threading.Thread(
            target=lambda: answers.update(result=check(client, "env.helper"))
        )
        checking.start()
        time.sleep(0.7)
        # The run whose hand-off was reading the card is stopped: its reading ends with no
        # result, and the check reads for itself in what is left of its limit.
        reader.cancel()
        checking.join(10)
        took = time.monotonic() - began
    result = answers["result"].json()
    assert (result["usable"], result["reason"]) == (False, "timeout")
    # The sentence names the check's limit, and the check kept to it (SC-012).
    assert result["message"] == "The agent did not answer within 1 seconds."
    assert took < 1.5, took


def test_a_check_that_could_not_wait_for_another_reading_says_so_at_its_own_time(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    stub = make_stub("helper")
    made = make_a2a_app(
        ScriptedChatModel(),
        {"helper": {}},
        stubs={"helper": stub},
        a2a_startup_timeout_seconds=0.5,
        a2a_call_timeout_seconds=10.0,
    )
    transport = made.transports["helper"]
    transport.down = True
    with TestClient(made.app) as client:
        (link,) = made.runtime.agents
        at_startup = by_id(agents(client))["env.helper"]
        time.sleep(0.3)
        transport.down, transport.hang = False, True
        reader = client.portal.start_task_soon(lambda: link.connect(limit=link.call_timeout))
        deadline = time.monotonic() + 5
        while len(transport.requests) < 2:
            assert time.monotonic() < deadline, "the other reading never started"
            time.sleep(0.01)
        began = datetime.now(datetime.fromisoformat(at_startup["checked_at"]).tzinfo)
        result = checked(client, "env.helper")
        listed = by_id(agents(client))["env.helper"]
        asked = len(transport.requests)
        reader.cancel()
    sentence = "The agent did not answer within 0.5 seconds."
    assert (result["usable"], result["reason"], result["message"]) == (False, "timeout", sentence)
    # The time is this check's, not the startup attempt's, and the list says the same.
    assert datetime.fromisoformat(result["checked_at"]) >= began
    assert (listed["state"], listed["reason"]) == ("unreachable", sentence)
    assert listed["checked_at"] == result["checked_at"]
    # It asked the agent for nothing itself: another reading was under way.
    assert asked == 2


def test_a_card_that_did_not_change_is_not_a_change_once_an_address_it_names_is_an_agent_s(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    # Ports of this test's own: the scrubber is the process's, and forgets nothing.
    helper_url, neighbour_url, refused_url = (
        "http://127.0.0.1:9621",
        "http://127.0.0.1:9622",
        "http://127.0.0.1:9623",
    )
    says = (
        "I work with the agent at http://127.0.0.1:9622/a2a and the one at http://127.0.0.1:9623."
    )
    helper = make_stub("helper", card=stub_card("helper", description=says))
    desk = make_stub("desk", card=stub_card("desk", description=says))
    made = make_a2a_app(ScriptedChatModel(), {"desk": {}}, stubs={"desk": desk})
    made.add_stub(helper_url, helper)
    made.add_stub(neighbour_url, make_stub("neighbour"))
    made.add_stub(refused_url, make_stub("refused")).down = True
    with TestClient(made.app) as client:
        added = client.post("/agents", json={"name": "helper", "url": helper_url})
        assert added.status_code == 201, added.text
        helper_id = added.json()["id"]
        first = checked(client, helper_id), checked(client, "env.desk")
        # Two addresses the cards name become known: one an agent's, one of a refused add.
        assert client.post("/agents", json={"name": "neighbour", "url": neighbour_url}).is_success
        assert (
            client.post("/agents", json={"name": "refused", "url": refused_url}).status_code == 409
        )
        shown = by_id(agents(client))[helper_id]["card"]["description"]
        revision = client.portal.call(made.saved_agents.revision)
        second = checked(client, helper_id), checked(client, "env.desk")
        revision_after = client.portal.call(made.saved_agents.revision)
        shown_after = by_id(agents(client))[helper_id]["card"]["description"]
    assert [result["card_changed"] for result in first] == [False, False]
    # The cards say what they said. That an address in them is now taken out is not a change
    # of the card: nothing is replaced, and no instance rebuilds for it.
    assert [result["card_changed"] for result in second] == [False, False]
    assert revision_after == revision
    assert (
        shown_after == shown == "I work with the agent at <address>/a2a and the one at <address>."
    )


def test_a_failed_check_whose_agent_cannot_be_looked_up_is_answered_with_an_error(
    make_a2a_app: Callable[..., A2aApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = one_agent(make_a2a_app, "saved")

    async def unreadable(_agent_id: str) -> Any:
        raise RuntimeError("connection to db.internal failed: password=hunter2")

    with TestClient(app.made.app) as client:
        app.transport.down = True
        monkeypatch.setattr(app.made.saved_agents, "get_agent", unreadable)
        response = check(client, app.agent_id)
        monkeypatch.undo()
        holders = held(app.made)
        free = app.made.runtime.card_reads.refusal(app.agent_id) is None
    assert refusal(response, 500, "internal")["message"] == (
        "The check's result could not be recorded."
    )
    for text in (response.text, caplog.text):
        assert "hunter2" not in text and "db.internal" not in text
    assert holders == {} and free
