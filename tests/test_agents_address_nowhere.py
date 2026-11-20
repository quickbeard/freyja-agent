"""005 FR-028, SC-008: an agent's address appears nowhere outside the Agents routes (parts 1
to 5).

Built like ``tests/test_llm_secrets_nowhere.py`` and grown in every phase that adds a route.
One agent is saved on a recognizable port, one is named in the environment on another, and
adds are refused at two more. What exists is driven: the list and the add with each of its
refusals, a run that completes, one left paused, and hand-offs that fail with the stand-in
down. Then everything the service returned, streamed, kept in a thread, and logged is
searched for each port.

A saved agent's address is found in ``url`` of an answer of the Agents routes and in nothing
else. An environment agent's, and the address of an add that was refused, are found nowhere.

The stand-ins fail the way the real libraries word a failed connection: the error names the
address that was asked. Their cards name addresses too.

Part 2 does the same for a check: one that succeeds, one that fails for each card problem,
one refused as busy, and one that finds another card, which names its own address and
another agent's.

Part 3 does the same for the approval choice: an add that runs without approval, a change of
the choice with each of its refusals, a run whose message the check before a send keeps back,
and a resume that ends with ``approval_now_required``.

Part 4 does the same for a move with each of its refusals, a removal with its refusals, a
task given up and reported in its thread, and a run kept back because the address it started
with is another agent's.

Part 5 is one journey through every route: an agent saved under ``localhost``, checked, handed
work, switched, moved, removed, and saved again under ``127.0.0.1``, beside a saved agent that
is not in use. Each part notes the routes it drove and the refusals it got, and the check at
the end of the file fails when a route of ``api/agents.py``, or a refusal code that
``contracts/openapi.yaml`` gives a route, is driven by no part.
"""

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from freyja_agent.api import agents as agents_api
from freyja_agent.config import canonical_address, destination
from freyja_agent.delegates.agent_cards import SENTENCES
from tests.a2a_stub import StubAgent, make_stub, stub_card
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import A2aApp
from tests.fakes import ScriptedChatModel
from tests.nowhere import Seen, addresses, found, kept, no_address, strings
from tests.test_a2a_gate import propose
from tests.test_a2a_handoff import DONE, handoff, tool_results
from tests.test_a2a_pending import tick
from tests.test_agents_approval import (
    BLOCKED,
    ELSEWHERE,
    NOW_REQUIRES,
    InTheTool,
    before_the_send,
)

# Ports nothing else in the suite uses. Each is searched for as a number of its own, and
# after a colon, which is how every form of an address holds it (FR-028).
SAVED_PORT = 9731
ENVIRONMENT_PORT = 9732
REFUSED_PORT = 9733
SLOW_PORT = 9734
PORTS = (SAVED_PORT, ENVIRONMENT_PORT, REFUSED_PORT, SLOW_PORT)
SAVED_URL = f"http://127.0.0.1:{SAVED_PORT}"
ENVIRONMENT_URL = f"http://127.0.0.1:{ENVIRONMENT_PORT}"
REFUSED_URL = f"http://127.0.0.1:{REFUSED_PORT}"
SLOW_URL = f"http://127.0.0.1:{SLOW_PORT}"
AGENTS = {"desk": {"url": ENVIRONMENT_URL, "read_only": True}}
# What each agent's card says of itself: its own address and its neighbour's, several ways.
TALKATIVE = (
    f"Reach me at http://127.0.0.1:{SAVED_PORT}/a2a, localhost:{SAVED_PORT}, or "
    f"[::1]:{SAVED_PORT}. My neighbour is at http://localhost:{ENVIRONMENT_PORT} and "
    f"('127.0.0.1', {ENVIRONMENT_PORT})."
)


# What the parts of this proof drove: the parts that ran, each route of the Agents section
# that was asked, and each refusal a route gave, by its code. A 422 is noted as `invalid`.
PARTS_RUN: set[int] = set()
DRIVEN: set[tuple[str, str, str | None]] = set()


def _route(path: str) -> str:
    rest = path.partition("?")[0].removeprefix("/agents")
    if rest == "":
        return "/agents"
    return "/agents/{agent_id}/check" if rest.endswith("/check") else "/agents/{agent_id}"


def watch(client: TestClient, part: int) -> None:
    """Note every request ``client`` makes to the Agents section from here on, for ``part``."""
    PARTS_RUN.add(part)
    request = client.request

    def noting(method: str, url: Any, **kwargs: Any) -> Any:
        response = request(method, url, **kwargs)
        if str(url).startswith("/agents"):
            code: str | None = None
            if not response.is_success:
                detail = response.json()["detail"]
                if isinstance(detail, dict):
                    code = detail["code"]
                elif response.status_code == 422 and isinstance(detail, list):
                    code = "invalid"
                else:
                    # No shape of the contract: the check at the end of the file says so.
                    code = f"status {response.status_code}"
            DRIVEN.add((method.upper(), _route(str(url)), code))
        return response

    client.request = noting  # type: ignore[method-assign]


@pytest.mark.parametrize(
    "text",
    [
        SAVED_URL,
        f"{SAVED_URL}/.well-known/agent-card.json",
        f"127.0.0.1:{SAVED_PORT}",
        f"localhost:{SAVED_PORT}",
        f"[::1]:{SAVED_PORT}",
        f"Connect call failed ('127.0.0.1', {SAVED_PORT})",
        f"('::1', {SAVED_PORT}, 0, 0)",
        f"connect_tcp.started host='127.0.0.1' port={SAVED_PORT} timeout=5",
        f"port {SAVED_PORT}.",
        f"x\x00{SAVED_PORT}\x00",
        f"f:{SAVED_PORT}0",
    ],
)
def test_the_search_finds_the_port_in_every_form_an_address_takes(text: str) -> None:
    assert found(text, SAVED_PORT)


@pytest.mark.parametrize(
    "text",
    [
        f"0af3{SAVED_PORT}c2",
        f"7c9e6679f0a54b3e8d2a1f4b6c{SAVED_PORT}0a",
        # An id written in groups: the port's digits as a whole group, first, inner, or last.
        f"checkpoint 1f0c5a2e-{SAVED_PORT}-6f0a-8001-3b1f2a4c5d6e",
        f"task {SAVED_PORT}-6f0a-8001-3b1f2a4c5d6e",
        f"run 1f0c5a2e-6f0a-8001-{SAVED_PORT}",
        f"2026-11-19T09:04:10.{SAVED_PORT}00+00:00",
        f"2026-11-19T09:04:10.12{SAVED_PORT}+00:00",
        f"1700{SAVED_PORT}.25",
    ],
)
def test_the_search_does_not_take_an_id_or_a_timestamp_for_the_port(text: str) -> None:
    assert not found(text, SAVED_PORT)


class Naming(StreamingASGITransport):
    """A stand-in's transport whose failed connection names the address, as httpx's does."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            url = request.url
            raise httpx.ConnectError(
                f"All connection attempts failed: {url}, "
                f"Connect call failed ('{url.host}', {url.port})",
                request=request,
            )
        return await super().handle_async_request(request)


def put(made: A2aApp, url: str, stub: StubAgent) -> Naming:
    """Put ``stub`` at ``url`` behind a transport that names the address when it is down."""
    transport = Naming(stub.app)
    made.at[destination(canonical_address(url))] = (stub, transport)
    return transport


def test_an_address_is_in_nothing_the_service_returns_streams_keeps_or_logs(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedChatModel(
        script=[
            handoff("Greet the desk.", agent="desk"),
            DONE,
            propose(("handoff_to_helper", {"message": "Left waiting."})),
            propose(("handoff_to_helper", {"message": "Sent while it is down."}), step="f"),
            DONE,
            handoff("Greet the desk again.", agent="desk", call_id="h4"),
        ]
    )
    desk = make_stub("desk", card=stub_card("desk", description=TALKATIVE))
    helper = make_stub("helper", card=stub_card("helper", description=TALKATIVE))
    made = make_a2a_app(model, AGENTS, stubs={"desk": desk}, a2a_startup_timeout_seconds=0.4)
    desk_transport = put(made, ENVIRONMENT_URL, desk)
    helper_transport = put(made, SAVED_URL, helper)
    # Where adds are refused: nothing answers, a redirect to the saved agent's own address,
    # and an agent that never answers.
    refused_transport = put(made, REFUSED_URL, make_stub("other"))
    slow = put(made, SLOW_URL, make_stub("slow"))
    slow.hang = True
    # The answers of the Agents routes, which alone may hold a saved agent's address.
    answers: list[Any] = []
    refusals: list[str] = []

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        watch(client, 1)
        seen = Seen(client, sse_events)

        def agents_route(method: str = "GET", **kwargs: Any) -> Any:
            response = client.request(method, "/agents", **kwargs)
            if response.is_success:
                answers.append(response.json())
            else:
                refusals.append(f"{method} /agents -> {response.status_code} {response.text}")
            return response

        def add(name: str, url: str, **kwargs: Any) -> Any:
            return agents_route("POST", json={"name": name, "url": url}, **kwargs)

        # The add, and the list with the saved agent. From here on the address is a saved
        # agent's: before, it was only something the two cards said.
        assert add("helper", f"HTTP://LocalHost:{SAVED_PORT}/").status_code == 201
        listed = agents_route().json()

        # Each refusal of an add.
        assert add("helper", REFUSED_URL).json()["detail"]["code"] == "name_taken"
        assert add("desk", REFUSED_URL).json()["detail"]["code"] == "name_taken"
        taken = [
            add("other", f"https://localhost:{SAVED_PORT}/"),
            add("other", f"http://[::1]:{ENVIRONMENT_PORT}"),
        ]
        assert [r.json()["detail"]["code"] for r in taken] == ["address_taken"] * 2
        refused_transport.down = True
        assert add("other", REFUSED_URL).json()["detail"]["reason"] == "no_answer"
        refused_transport.down = False
        made.at[destination(canonical_address(REFUSED_URL))] = (
            make_stub("other"),
            Naming(make_stub("other", redirect_card_to=f"{SAVED_URL}/elsewhere").app),
        )
        assert add("other", REFUSED_URL).json()["detail"]["reason"] == "redirect"
        assert add("slow", SLOW_URL).json()["detail"]["reason"] == "timeout"
        invalid = [
            add("other", f"http://127.0.0.1:{REFUSED_PORT}/?token=1"),
            add("other", f"http://user:pass@127.0.0.1:{REFUSED_PORT}"),
            add("other", f"ftp://127.0.0.1:{REFUSED_PORT}"),
            add("Not A Name", REFUSED_URL),
            agents_route("POST", json={"name": "other", "url": REFUSED_URL, "approval": "maybe"}),
            agents_route("POST", json={"name": "other", "url": REFUSED_URL, "address": SAVED_URL}),
            agents_route(
                "POST",
                content=f"name=other&url={REFUSED_URL}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ),
        ]
        assert [r.status_code for r in invalid] == [422] * len(invalid)
        elsewhere = add("other", REFUSED_URL, headers={"Sec-Fetch-Site": "cross-site"})
        assert elsewhere.json()["detail"]["code"] == "other_origin"
        slots = made.runtime.card_reads
        for _ in range(4):
            slots.take(None)
        assert add("other", REFUSED_URL).json()["detail"]["code"] == "check_busy"
        for _ in range(4):
            slots.release(None)
        monkeypatch.setattr(agents_api, "AGENT_LIMIT", 1)
        assert add("other", REFUSED_URL).json()["detail"]["code"] == "agent_limit"
        monkeypatch.undo()

        # The catalog, with both agents' tools described from their cards.
        seen.call("GET", "/tools")
        seen.call("GET", "/healthz")

        # A run that completes: a hand-off to the environment agent, which answers.
        completed = seen.stream("/threads/n-1/runs", {"message": "hello"}, debug="true")
        assert completed[-1] == ("end", {"status": "completed"})
        seen.state("n-1")

        # A run left paused on a hand-off to the saved agent.
        paused = seen.stream("/threads/n-2/runs", {"message": "hello"}, debug="true")
        assert paused[-1][0] == "interrupt"
        seen.state("n-2")

        # A hand-off to the saved agent that fails: approved while the stand-in is down.
        waiting = seen.stream("/threads/n-3/runs", {"message": "hello"}, debug="true")
        helper_transport.down = True
        failed = seen.stream(
            "/threads/n-3/resume",
            {"interrupt_id": waiting[-1][1]["interrupt_id"], "decisions": [{"type": "approve"}]},
            debug="true",
        )
        seen.state("n-3")

        # The same for the environment agent, which needs no approval.
        desk_transport.down = True
        unreachable = seen.stream("/threads/n-4/runs", {"message": "hello"}, debug="true")
        seen.state("n-4")

        # What the service says of both agents now that both are down.
        seen.call("GET", "/tools")
        agents_route()
        in_threads = kept(made.runtime.agent.checkpointer)

    # What was driven happened: the agent was saved, both hand-offs failed for an agent that
    # could not be reached, and the failure reached the model as an error result.
    (saved,) = [agent for agent in listed["agents"] if agent["source"] == "saved"]
    kept_url = f"http://localhost:{SAVED_PORT}"
    assert saved["url"] == kept_url
    (helper_result,) = tool_results(failed, "handoff_to_helper")
    (desk_result,) = tool_results(unreachable, "handoff_to_desk")
    for result, agent in ((helper_result, "helper"), (desk_result, "desk")):
        assert result["content"].startswith(f"Agent '{agent}' could not be reached: ")
        assert "<address>" in result["content"]
    assert len(helper.record.messages) == 0 and len(desk.record.messages) == 1
    assert len(refusals) == 17 and len(answers) == 3

    # Outside the Agents routes: every response, every stream with its debug events, and
    # every thread as the checkpointer keeps it.
    assert any("handoff_to_helper" in text for text in seen.texts)
    assert any("<address>" in text for text in seen.texts)
    no_address(seen.texts, *PORTS)
    assert any("could not be reached" in text for text in in_threads)
    no_address(in_threads, *PORTS)

    # Every refusal's reason, the Agents routes' own included.
    no_address(refusals, *PORTS)

    # An answer of the Agents routes holds a saved agent's address in `url`, in the form it is
    # kept in, and in nothing else: not in what a card says, and not in a reason.
    assert any(addresses(answer) for answer in answers)
    for answer in answers:
        assert set(addresses(answer)) <= {kept_url}
        no_address(strings(answer, but="url"), *PORTS)
        # The environment agent's address, and a refused add's, are in no part of it.
        no_address([json.dumps(answer)], ENVIRONMENT_PORT, REFUSED_PORT, SLOW_PORT)
    card_texts = [agent["card"]["description"] for agent in answers[-1]["agents"]]
    assert len(card_texts) == 2 and all("<address>" in text for text in card_texts)

    # Every log record at every level, with its arguments and its traceback.
    logged = [record.getMessage() for record in caplog.records]
    logged += [caplog.handler.format(record) for record in caplog.records]
    assert any("Agent added: helper" in line for line in logged)
    assert any("could not be reached" in line for line in logged)
    # The libraries' own lines about the requests to the agents are among them.
    assert any(
        record.name == "httpx" and "<address>" in line
        for record, line in zip(caplog.records, logged, strict=False)
    )
    no_address(logged, *PORTS)


# Part 2 (T069): checks.

CHECKED_PORT = 9741
DESK_PORT = 9742
CHECK_PORTS = (CHECKED_PORT, DESK_PORT)
CHECKED_URL = f"http://127.0.0.1:{CHECKED_PORT}"
DESK_URL = f"http://127.0.0.1:{DESK_PORT}"


def saying(more: str = "") -> str:
    """What a card says of its own address and of the other agent's, several ways."""
    return (
        f"{more}Reach me at http://127.0.0.1:{CHECKED_PORT}/a2a or localhost:{CHECKED_PORT}. "
        f"My neighbour is at http://localhost:{DESK_PORT}, [::1]:{DESK_PORT}, and "
        f"('127.0.0.1', {DESK_PORT})."
    )


def broken(code: str) -> StubAgent:
    """A stand-in whose card is refused as ``code``, for the codes the card decides."""
    return {
        "no_card": lambda: make_stub("helper", card_status=404, card_body=saying().encode()),
        "unreadable": lambda: make_stub("helper", card_body=saying().encode()),
        # A redirect to the other agent's address: neither is to appear.
        "redirect": lambda: make_stub("helper", redirect_card_to=f"{DESK_URL}/elsewhere"),
        "too_large": lambda: make_stub("helper", card_body=saying().encode() * 20000),
        "auth_required": lambda: make_stub(
            "helper", card=stub_card("helper", security=True, description=saying())
        ),
        "no_text": lambda: make_stub(
            "helper",
            card=stub_card("helper", input_modes=("image/png",), description=saying()),
        ),
        "no_interface": lambda: make_stub(
            "helper", card=stub_card("helper", binding="GRPC", description=saying())
        ),
    }.get(code, lambda: make_stub("helper"))()


def test_an_address_is_in_nothing_a_check_returns_or_logs(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    desk = make_stub("desk", card=stub_card("desk", description=saying()))
    helper = make_stub("helper", card=stub_card("helper", description=saying()))
    made = make_a2a_app(
        ScriptedChatModel(),
        {"desk": {"url": DESK_URL, "read_only": True}},
        stubs={"desk": desk},
        a2a_startup_timeout_seconds=0.4,
    )
    desk_transport = put(made, DESK_URL, desk)
    helper_transport = put(made, CHECKED_URL, helper)
    results: list[str] = []
    refusals: list[str] = []
    answers: list[Any] = []

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        watch(client, 2)
        seen = Seen(client, sse_events)
        added = client.post("/agents", json={"name": "helper", "url": CHECKED_URL})
        assert added.status_code == 201, added.text
        saved_id = added.json()["id"]

        def check(agent_id: str, **kwargs: Any) -> Any:
            response = client.post(f"/agents/{agent_id}/check", **kwargs)
            text = f"POST /agents/{agent_id}/check -> {response.status_code} {response.text}"
            (results if response.is_success else refusals).append(text)
            return response

        # A check that succeeds, of each kind of agent.
        assert check(saved_id).json()["usable"] is True
        assert check("env.desk").json()["usable"] is True

        # One that fails, for each card problem, with what the stand-in serves naming both
        # addresses; then the same agent answering again.
        for code in SENTENCES:
            helper_transport.down = code == "no_answer"
            helper_transport.hang = code == "timeout"
            helper_transport.app = broken(code).app
            assert check(saved_id).json()["reason"] == code, code
        helper_transport.down = helper_transport.hang = False
        helper_transport.app = helper.app
        desk_transport.down = True
        assert check("env.desk").json()["reason"] == "no_answer"
        desk_transport.down = False

        # One that finds another card, which names its own address and the other agent's:
        # the details that are kept, and shown, are cleaned of both.
        other_card = stub_card("helper", description=saying("Now with reports. "))
        helper_transport.app = make_stub("helper", card=other_card).app
        assert check(saved_id).json()["card_changed"] is True
        desk_transport.app = make_stub(
            "desk", card=stub_card("desk", description=saying("Now with reports. "))
        ).app
        assert check("env.desk").json()["card_changed"] is True

        # Refused: busy, an agent that does not exist, an id in no form, another origin.
        slots = made.runtime.card_reads
        slots.take(saved_id)
        assert check(saved_id).json()["detail"]["code"] == "check_busy"
        slots.release(saved_id)
        for _ in range(4):
            slots.take(None)
        assert check("env.desk").json()["detail"]["code"] == "check_busy"
        for _ in range(4):
            slots.release(None)
        assert check("0" * 32).status_code == 404
        assert check("Not-An-Id").status_code == 422
        assert check(saved_id, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403

        # What the service says of both agents afterwards.
        seen.call("GET", "/tools")
        listed = client.get("/agents")
        answers.append(listed.json())

    assert len(results) == 14 and len(refusals) == 5
    # A check's answer holds no address at all, and neither does a refusal.
    no_address(results, *CHECK_PORTS)
    no_address(refusals, *CHECK_PORTS)
    # The catalog describes both agents from the new details, with every address taken out.
    assert any("Now with reports. Reach me at <address>/a2a" in text for text in seen.texts)
    no_address(seen.texts, *CHECK_PORTS)
    # The list holds the saved agent's address in `url` and in nothing else.
    for answer in answers:
        assert addresses(answer) == [CHECKED_URL]
        no_address(strings(answer, but="url"), *CHECK_PORTS)
        cards = [agent["card"]["description"] for agent in answer["agents"]]
        assert len(cards) == 2
        assert all(text.startswith("Now with reports. Reach me at <address>") for text in cards)
    # Every log record at every level, with its arguments and its traceback.
    logged = [record.getMessage() for record in caplog.records]
    logged += [caplog.handler.format(record) for record in caplog.records]
    assert sum("Agent checked: helper" in line for line in logged) == 2 * 11
    assert sum("Agent checked: desk" in line for line in logged) == 2 * 3
    no_address(logged, *CHECK_PORTS)


# Part 3 (T087): the approval choice.

FREE_PORT = 9751
GATE_PORT = 9752
COUNTER_PORT = 9753
CHOICE_PORTS = (FREE_PORT, GATE_PORT, COUNTER_PORT)
FREE_URL = f"http://127.0.0.1:{FREE_PORT}"
GATE_URL = f"http://127.0.0.1:{GATE_PORT}"
COUNTER_URL = f"http://127.0.0.1:{COUNTER_PORT}"
# What the cards say: every agent's address. An answer is cleaned of the addresses that are
# an agent's when it is given, so the agent saved first names only what is one by then.
NEIGHBOURLY = (
    f"Reach me at http://127.0.0.1:{FREE_PORT}/a2a or localhost:{GATE_PORT}; the counter is "
    f"at http://localhost:{COUNTER_PORT} and ('127.0.0.1', {COUNTER_PORT})."
)
SAVED_FIRST = (
    f"Reach me at http://127.0.0.1:{GATE_PORT}/a2a; the counter is at localhost:{COUNTER_PORT}."
)


def test_an_address_is_in_nothing_a_change_of_the_approval_choice_returns_or_causes(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedChatModel(
        script=[
            handoff("Kept back by the check before a send.", agent="free"),
            DONE,
            propose(
                ("handoff_to_gate", {"message": "Waits for a decision."}),
                ("handoff_to_free", {"message": "Needed none when it was proposed."}),
            ),
        ]
    )
    counter = make_stub("counter", card=stub_card("counter", description=NEIGHBOURLY))
    made = make_a2a_app(
        model, {"counter": {"url": COUNTER_URL, "read_only": True}}, stubs={"counter": counter}
    )
    put(made, COUNTER_URL, counter)
    free_transport = put(
        made, FREE_URL, make_stub("free", card=stub_card("free", description=NEIGHBOURLY))
    )
    gate_transport = put(
        made, GATE_URL, make_stub("gate", card=stub_card("gate", description=SAVED_FIRST))
    )
    answers: list[Any] = []
    refusals: list[str] = []

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        watch(client, 3)
        seen = Seen(client, sse_events)

        def agents_route(method: str, path: str = "", **kwargs: Any) -> Any:
            response = client.request(method, f"/agents{path}", **kwargs)
            if response.is_success:
                answers.append(response.json())
            else:
                refusals.append(f"{method} {path} -> {response.status_code} {response.text}")
            return response

        def change(agent_id: str, **kwargs: Any) -> Any:
            return agents_route("PATCH", f"/{agent_id}", **kwargs)

        # An add that requires approval, and one that runs without it.
        assert agents_route("POST", json={"name": "gate", "url": GATE_URL}).status_code == 201
        added = agents_route(
            "POST",
            json={"name": "free", "url": f"HTTP://LocalHost:{FREE_PORT}/", "approval": "none"},
        )
        assert added.status_code == 201 and added.json()["approval"] == "none"
        free = added.json()["id"]

        # A change of the choice, there and back, and one that changes nothing.
        tightened = change(free, json={"version": 1, "approval": "required"})
        loosened = change(free, json={"version": 2, "approval": "none"})
        same = change(free, json={"version": 3, "approval": "none"})
        assert [r.json()["agent"]["version"] for r in (tightened, loosened, same)] == [2, 3, 3]

        # Each refusal of a change.
        body = {"version": 3, "approval": "required"}
        assert change(free, json={**body, "version": 1}).json()["detail"]["code"] == "agent_changed"
        assert change("env.counter", json=body).json()["detail"]["code"] == "agent_read_only"
        assert change("0" * 32, json=body).json()["detail"]["code"] == "agent_not_found"
        invalid = [
            change(free, json={**body, "url": f"http://127.0.0.1:{FREE_PORT}/?moved=1"}),
            change(free, json={**body, "address": f"http://127.0.0.1:{GATE_PORT}"}),
            change(free, json={"approval": f"http://127.0.0.1:{COUNTER_PORT}"}),
            change(free, content=f"url={FREE_URL}", headers={"Content-Type": "text/plain"}),
        ]
        assert [r.status_code for r in invalid] == [422] * len(invalid)
        elsewhere = change(free, json=body, headers={"Sec-Fetch-Site": "cross-site"})
        assert elsewhere.json()["detail"]["code"] == "other_origin"

        async def failing(*_args: Any, **_values: Any) -> Any:
            raise RuntimeError(f"connection failed while writing {FREE_URL}")

        monkeypatch.setattr(made.saved_agents, "change", failing)
        assert change(free, json=body).status_code == 500
        monkeypatch.undo()

        # A run whose message the check before a send keeps back: the agent ran without
        # approval when the run started, and requires it by the time its tool would send.
        async def tighten() -> None:
            await made.saved_agents.change(free, 3, read_only=False)

        before_the_send(made, monkeypatch, tighten)
        kept_back = seen.stream("/threads/n-5/runs", {"message": "hello"}, debug="true")
        monkeypatch.undo()
        seen.state("n-5")

        # A resume that a tightened choice blocks: the step waits for a decision about
        # `gate`, and its call to `free` needed none when it was proposed.
        assert change(free, json={"version": 4, "approval": "none"}).status_code == 200
        waiting = seen.stream("/threads/n-6/runs", {"message": "hello"}, debug="true")
        assert [a["name"] for a in waiting[-1][1]["action_requests"]] == ["handoff_to_gate"]
        assert change(free, json={"version": 5, "approval": "required"}).status_code == 200
        blocked = seen.stream(
            "/threads/n-6/resume",
            {"interrupt_id": waiting[-1][1]["interrupt_id"], "decisions": [{"type": "approve"}]},
            debug="true",
        )
        seen.state("n-6")

        seen.call("GET", "/tools")
        listed = agents_route("GET").json()
        in_threads = kept(made.runtime.agent.checkpointer)

    # What was driven happened.
    (result,) = tool_results(kept_back, "handoff_to_free")
    assert result["content"] == NOW_REQUIRES.format(agent="free")
    assert kept_back[-1] == ("end", {"status": "completed"})
    assert blocked[-1] == (
        "error",
        {"code": "approval_now_required", "message": BLOCKED.format(agent="free")},
    )
    # Neither saved agent was sent anything but the request for its card.
    for transport in (free_transport, gate_transport):
        assert [request.method for request in transport.requests] == ["GET"]
    assert len(refusals) == 9 and len(answers) == 8

    # Outside the Agents routes: every response, every stream with its debug events, and
    # every thread as the checkpointer keeps it.
    assert any("handoff_to_free" in text for text in seen.texts)
    assert any("<address>" in text for text in seen.texts)
    no_address(seen.texts, *CHOICE_PORTS)
    assert any("now requires approval" in text for text in in_threads)
    no_address(in_threads, *CHOICE_PORTS)

    # Every refusal of a change, the one for a failing store included.
    no_address(refusals, *CHOICE_PORTS)

    # An answer of the Agents routes holds a saved agent's address in `url` and in nothing
    # else, and the environment agent's in no part of it.
    kept_urls = {f"http://localhost:{FREE_PORT}", f"http://127.0.0.1:{GATE_PORT}"}
    assert any(addresses(answer) for answer in answers)
    for answer in answers:
        assert set(addresses(answer)) <= kept_urls
        no_address(strings(answer, but="url"), *CHOICE_PORTS)
        no_address([json.dumps(answer)], COUNTER_PORT)
    card_texts = [agent["card"]["description"] for agent in listed["agents"]]
    assert len(card_texts) == 3 and all("<address>" in text for text in card_texts)

    # Every log record at every level, with its arguments and its traceback.
    logged = [record.getMessage() for record in caplog.records]
    logged += [caplog.handler.format(record) for record in caplog.records]
    assert any("Agent added: free" in line and "approval none" in line for line in logged)
    assert sum("Agent changed: free" in line for line in set(logged)) >= 4
    assert any("Agent free was not changed: RuntimeError" in line for line in logged)
    assert any(f"Agent free: {NOW_REQUIRES.format(agent='free')}" in line for line in logged)
    assert any("refused" in line and line.endswith("agents: free") for line in logged)
    no_address(logged, *CHOICE_PORTS)


# Part 4 (T103): moving and removing.

ROAM_PORT = 9761
NEXT_PORT = 9762
DOWN_PORT = 9763
FIXED_PORT = 9764
LAST_PORT = 9765
MOVING_PORTS = (ROAM_PORT, NEXT_PORT, DOWN_PORT, FIXED_PORT, LAST_PORT)
ROAM_URL, NEXT_URL, DOWN_URL, FIXED_URL, LAST_URL = (
    f"http://127.0.0.1:{port}" for port in MOVING_PORTS
)


def naming(*ports: int) -> str:
    """What a card says: the addresses at ``ports``, in the forms an address takes. An answer
    is cleaned of the addresses that are an agent's, or were asked for, when it is given, so
    each card names only what is one by the time it is read."""
    forms = ("http://127.0.0.1:{}/a2a", "localhost:{}", "[::1]:{}", "('127.0.0.1', {})")
    return "Reach me at " + ", ".join(
        forms[index % len(forms)].format(port) for index, port in enumerate(ports)
    )


def test_an_address_is_in_nothing_a_move_or_a_removal_returns_or_causes(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedChatModel(
        script=[
            # The background run that tells the thread its task was given up.
            DONE,
            handoff("Kept back: the address is another agent's.", agent="roam"),
        ]
    )
    made = make_a2a_app(model, {})

    def at(url: str, name: str, *ports: int) -> Naming:
        card = stub_card(name, description=naming(*ports))
        return put(made, url, make_stub(name, card=card))

    fixed_transport = at(FIXED_URL, "fixed", FIXED_PORT)
    roam_transport = at(ROAM_URL, "roam", ROAM_PORT, FIXED_PORT)
    down = at(DOWN_URL, "roam", DOWN_PORT)
    next_transport = at(NEXT_URL, "roam", NEXT_PORT, ROAM_PORT, FIXED_PORT, DOWN_PORT)
    last_transport = at(LAST_URL, "roam", LAST_PORT, NEXT_PORT, ROAM_PORT, FIXED_PORT)
    answers: list[Any] = []
    refusals: list[str] = []

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        watch(client, 4)
        seen = Seen(client, sse_events)

        def agents_route(method: str, path: str = "", **kwargs: Any) -> Any:
            response = client.request(method, f"/agents{path}", **kwargs)
            if response.is_success:
                answers.append(response.json())
            else:
                refusals.append(f"{method} {path} -> {response.status_code} {response.text}")
            return response

        def add(name: str, url: str, **more: Any) -> Any:
            return agents_route("POST", json={"name": name, "url": url, **more})

        def move(agent_id: str, version: int, url: str, **kwargs: Any) -> Any:
            return agents_route(
                "PATCH", f"/{agent_id}", json={"version": version, "url": url}, **kwargs
            )

        def remove(agent_id: str, **kwargs: Any) -> Any:
            return agents_route("DELETE", f"/{agent_id}", **kwargs)

        fixed = add("fixed", FIXED_URL).json()["id"]
        roam = add("roam", ROAM_URL, approval="none").json()["id"]
        # A task of `roam` that is pending when it moves.
        now = client.portal.call(made.store.now)
        seed = {"agent": "roam", "task_id": "task-7", "thread_id": "n-7", "ref": f"{roam}:1"}
        client.portal.call(_record_task, made, seed, now)

        # Each refusal of a move.
        down.down = True
        assert move(roam, 1, DOWN_URL).json()["detail"]["reason"] == "no_answer"
        down.down = False
        made.at[destination(canonical_address(DOWN_URL))] = (
            make_stub("roam"),
            Naming(make_stub("roam", redirect_card_to=f"{ROAM_URL}/elsewhere").app),
        )
        assert move(roam, 1, DOWN_URL).json()["detail"]["reason"] == "redirect"
        assert move(roam, 1, f"https://localhost:{FIXED_PORT}/").json()["detail"]["code"] == (
            "address_taken"
        )
        assert move(roam, 9, NEXT_URL).json()["detail"]["code"] == "agent_changed"
        assert move(roam, 1, f"http://user:pass@127.0.0.1:{NEXT_PORT}").status_code == 422
        assert move(roam, 1, f"http://127.0.0.1:{NEXT_PORT}/?x=1").status_code == 422
        elsewhere = move(roam, 1, NEXT_URL, headers={"Sec-Fetch-Site": "cross-site"})
        assert elsewhere.json()["detail"]["code"] == "other_origin"
        slots = made.runtime.card_reads
        for _ in range(4):
            slots.take(None)
        assert move(roam, 1, NEXT_URL).json()["detail"]["code"] == "check_busy"
        for _ in range(4):
            slots.release(None)

        async def failing(*_args: Any, **_values: Any) -> Any:
            raise RuntimeError(f"connection failed while writing {NEXT_URL}")

        monkeypatch.setattr(made.saved_agents, "change", failing)
        assert move(roam, 1, NEXT_URL).status_code == 500
        monkeypatch.undo()

        # The move, which gives the task up; the worker's round tells its thread.
        moved = move(roam, 1, f"HTTP://LocalHost:{NEXT_PORT}/")
        assert moved.status_code == 200 and moved.json()["given_up"] == 1
        assert "<address>" in moved.json()["agent"]["card"]["description"]
        tick(client, made.runtime)
        told = seen.state("n-7")

        # A run kept back because the address it started with is another agent's: while
        # its tool is on its way to sending, `roam` is moved on and another agent is
        # saved where it was.
        def moved_on() -> None:
            assert move(roam, 2, LAST_URL).status_code == 200
            assert add("squatter", NEXT_URL).status_code == 201

        tool = InTheTool(made, monkeypatch)
        kept_back = tool.during(
            lambda: seen.stream("/threads/n-8/runs", {"message": "hello"}, debug="true"),
            moved_on,
        )
        monkeypatch.undo()
        seen.state("n-8")

        # A removal, and each refusal of one.
        assert remove(roam, params={"version": 1}).json()["detail"]["code"] == "agent_changed"
        assert remove(roam).status_code == 422
        other_origin = remove(
            roam, params={"version": 3}, headers={"Origin": "http://elsewhere.example"}
        )
        assert other_origin.json()["detail"]["code"] == "other_origin"

        async def unremovable(*_args: Any) -> Any:
            raise RuntimeError(f"connection failed while removing {LAST_URL}")

        monkeypatch.setattr(made.saved_agents, "remove", unremovable)
        assert remove(roam, params={"version": 3}).status_code == 500
        monkeypatch.undo()
        removed = remove(roam, params={"version": 3})
        assert removed.status_code == 200 and removed.json() == {"given_up": 0}

        seen.call("GET", "/tools")
        listed = agents_route("GET").json()
        in_threads = kept(made.runtime.agent.checkpointer)

    # What was driven happened.
    report = [m for m in told["messages"] if m["name"] == "check_agent_tasks"]
    assert len(report) == 1 and "which was moved to another address" in report[0]["content"]
    (result,) = tool_results(kept_back, "handoff_to_roam")
    assert result["content"] == ELSEWHERE.format(agent="roam")
    assert [agent["name"] for agent in listed["agents"]] == ["fixed", "squatter"]
    assert fixed in {agent["id"] for agent in listed["agents"]}
    # No agent was sent anything but requests for its card.
    for transport in (roam_transport, next_transport, fixed_transport, last_transport):
        assert {request.method for request in transport.requests} <= {"GET"}
    assert len(refusals) == 13 and len(answers) == 7

    # Outside the Agents routes: every response, every stream with its debug events, and
    # every thread as the checkpointer keeps it.
    assert any("handoff_to_roam" in text for text in seen.texts)
    no_address(seen.texts, *MOVING_PORTS)
    assert any("moved to another address" in text for text in in_threads)
    no_address(in_threads, *MOVING_PORTS)

    # Every refusal of a move and of a removal, the ones for a failing store included.
    no_address(refusals, *MOVING_PORTS)

    # An answer of the Agents routes holds a saved agent's address in `url` and nowhere else.
    kept_urls = {
        ROAM_URL,
        FIXED_URL,
        NEXT_URL,
        LAST_URL,
        f"http://localhost:{NEXT_PORT}",
    }
    assert any(addresses(answer) for answer in answers)
    for answer in answers:
        assert set(addresses(answer)) <= kept_urls
        no_address(strings(answer, but="url"), *MOVING_PORTS)

    # Every log record at every level, with its arguments and its traceback.
    logged = [record.getMessage() for record in caplog.records]
    logged += [caplog.handler.format(record) for record in caplog.records]
    assert any("Agent roam was not moved" in line for line in logged)
    assert any("Agent changed: roam" in line and "1 task was given up" in line for line in logged)
    assert any("gave up" not in line and "Agent removed: roam" in line for line in logged)
    assert any(f"Agent roam: {ELSEWHERE.format(agent='roam')}" in line for line in logged)
    no_address(logged, *MOVING_PORTS)


async def _record_task(made: A2aApp, seed: dict[str, Any], now: Any) -> None:
    from freyja_agent.runtime.agent_tasks import AgentTaskRecord

    await made.store.record_task(
        AgentTaskRecord(
            agent=seed["agent"],
            task_id=seed["task_id"],
            thread_id=seed["thread_id"],
            context_id="ctx",
            message="Compile the report.",
            state="working",
            started_at=now,
            last_checked_at=now,
            agent_ref=seed["ref"],
        )
    )


# Part 5 (T111): one journey through every route, and the check that nothing is left out.

ROVER_PORT = 9771
ONWARD_PORT = 9772
STAND_PORT = 9773
SHADOW_PORT = 9774
JOURNEY_PORTS = (ROVER_PORT, ONWARD_PORT, STAND_PORT, SHADOW_PORT)
# The same place, spelled both ways an operator may enter it: each is kept as it was entered.
ROVER_AS_LOCALHOST = f"http://localhost:{ROVER_PORT}"
ROVER_AS_LOOPBACK = f"http://127.0.0.1:{ROVER_PORT}"
# An address with a path, which is part of the address: it goes with its host and port.
ONWARD_URL = f"http://127.0.0.1:{ONWARD_PORT}/agents/onward-path"
STAND_URL = f"http://127.0.0.1:{STAND_PORT}"
SHADOW_URL = f"http://127.0.0.1:{SHADOW_PORT}"
TO_ROVER = ("handoff_to_rover", {"message": "For the rover."})


def under(app: Any, prefix: str) -> Any:
    """``app`` as it answers when it is served under ``prefix``, as behind a gateway."""

    async def mounted(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].startswith(prefix):
            scope = {**scope, "path": scope["path"][len(prefix) :] or "/"}
        await app(scope, receive, send)

    return mounted


def every_way(port: int) -> str:
    """An agent's own address as a card may write it: after any scheme or none, with
    `localhost` in any case and with a full stop at its end. The independent review of
    Phase 9 found that the first seven of these were left in card text."""
    forms = (
        "ws://127.0.0.1:{}/a2a",
        "grpc://localhost:{}",
        "//127.0.0.1:{}/a2a",
        "tcp://[::1]:{}",
        "http://LOCALHOST:{}/a2a",
        "HTTP://LocalHost:{}/",
        "http://localhost.:{}",
        "http://127.0.0.1:{}/a2a",
        "localhost:{}",
        "[::1]:{}",
        "('127.0.0.1', {})",
    )
    return "Reach me at " + ", or ".join(form.format(port) for form in forms) + " today."


def test_an_address_is_in_nothing_one_journey_through_every_route_returns_or_causes(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedChatModel(
        script=[
            handoff("Greet the stand.", agent="stand"),
            DONE,
            propose(TO_ROVER),
            DONE,
            handoff("Sent with no pause.", agent="rover", call_id="h3"),
            DONE,
            # The background run that tells a thread its task was given up.
            DONE,
            propose(TO_ROVER, step="g"),
            DONE,
            # A hand-off and a cancel that fail on a link that holds a card.
            propose(TO_ROVER, ("cancel_rover_task", {"task_id": "task-10"}), step="k"),
            DONE,
        ]
    )
    stand = make_stub("stand", card=stub_card("stand", description=naming(STAND_PORT, SHADOW_PORT)))
    # A saved agent the environment overrides: `stand` is an environment agent's name.
    shadow = make_stub("stand", card=stub_card("stand", description=naming(SHADOW_PORT)))
    made = make_a2a_app(
        model,
        {"stand": {"url": STAND_URL, "read_only": True}},
        stubs={"stand": stand},
        saved={"stand": {"url": SHADOW_URL, "stub": shadow}},
        a2a_startup_timeout_seconds=0.4,
    )
    put(made, STAND_URL, stand)
    shadow_transport = put(made, SHADOW_URL, shadow)
    rover = make_stub(
        "rover",
        card=stub_card(
            "rover", description=f"{every_way(ROVER_PORT)} {naming(STAND_PORT, SHADOW_PORT)}"
        ),
    )
    rover_transport = put(made, ROVER_AS_LOCALHOST, rover)
    # Its card names its whole address, path included, as a sentence does: before a full
    # stop, before a query, and in brackets.
    onward = make_stub(
        "rover",
        card=stub_card(
            "rover",
            description=(
                f"Reach me at {ONWARD_URL}. Or at {ONWARD_URL}?x=1, or ({ONWARD_URL}), or "
                f"ws://localhost:{ONWARD_PORT}/agents/onward-path; {every_way(ROVER_PORT)} "
                f"{naming(STAND_PORT, SHADOW_PORT)}"
            ),
        ),
    )
    onward_transport = Naming(under(onward.app, "/agents/onward-path"))
    made.at[destination(canonical_address(ONWARD_URL))] = (onward, onward_transport)
    shadow_id = made.saved["stand"].id
    answers: list[Any] = []
    refusals: list[str] = []

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        watch(client, 5)
        seen = Seen(client, sse_events)

        def route(method: str, path: str = "", **kwargs: Any) -> Any:
            response = client.request(method, f"/agents{path}", **kwargs)
            if response.is_success:
                answers.append(response.json())
            else:
                refusals.append(f"{method} {path} -> {response.status_code} {response.text}")
            return response

        def code(response: Any) -> str:
            return response.json()["detail"]["code"]

        def decide(thread: str, proposal: Any) -> Any:
            approve = [{"type": "approve"} for _action in proposal["action_requests"]]
            body = {"interrupt_id": proposal["interrupt_id"], "decisions": approve}
            return seen.stream(f"/threads/{thread}/resume", body, debug="true")

        async def failing(*_args: Any, **_values: Any) -> Any:
            raise RuntimeError(f"connection failed for {ROVER_AS_LOCALHOST} and {SHADOW_URL}")

        # The list, and the list when the store cannot be read.
        route("GET")
        monkeypatch.setattr(made.saved_agents, "list_agents", failing)
        assert code(route("GET")) == "internal"
        monkeypatch.undo()

        # The agent that is not in use: nothing but a removal is done with it.
        assert code(route("POST", f"/{shadow_id}/check")) == "agent_read_only"
        assert code(route("PATCH", f"/{shadow_id}", json={"version": 1, "approval": "none"})) == (
            "agent_read_only"
        )
        # An agent of the environment's form that the environment does not name.
        nobody = {"version": 1, "approval": "none"}
        assert code(route("PATCH", "/env.nobody", json=nobody)) == "agent_not_found"
        assert code(route("DELETE", "/env.nobody", params={"version": 1})) == "agent_not_found"
        assert code(route("POST", "/env.nobody/check")) == "agent_not_found"

        # Saved under `localhost`; first an add the store cannot write.
        new = {"name": "rover", "url": f"HTTP://LocalHost:{ROVER_PORT}/"}
        monkeypatch.setattr(made.saved_agents, "add", failing)
        assert code(route("POST", json=new)) == "internal"
        monkeypatch.undo()
        monkeypatch.setattr(made.saved_agents, "list_agents", failing)
        assert code(route("POST", json=new)) == "internal"
        monkeypatch.undo()
        added = route("POST", json=new)
        assert added.status_code == 201 and added.json()["url"] == ROVER_AS_LOCALHOST
        first = added.json()["id"]

        # A check that succeeds, one whose result cannot be recorded, a change and a
        # removal the store cannot read for, and a move to the environment agent's address.
        assert route("POST", f"/{first}/check").json()["usable"] is True
        monkeypatch.setattr(made.saved_agents, "get_agent", failing)
        assert code(route("POST", f"/{first}/check")) == "internal"
        assert code(route("DELETE", f"/{first}", params={"version": 1})) == "internal"
        monkeypatch.undo()
        monkeypatch.setattr(made.saved_agents, "list_agents", failing)
        assert code(route("PATCH", f"/{first}", json=nobody)) == "internal"
        monkeypatch.undo()
        there = {"version": 1, "url": f"http://localhost:{STAND_PORT}/"}
        assert code(route("PATCH", f"/{first}", json=there)) == "address_taken"
        # A check that fails with the stand-in down.
        rover_transport.down = True
        assert route("POST", f"/{first}/check").json()["reason"] == "no_answer"

        # A run that completes, one left paused, and a hand-off that fails once approved:
        # the last check found the agent down, so its link holds no usable card.
        completed = seen.stream("/threads/j-1/runs", {"message": "hello"}, debug="true")
        assert completed[-1] == ("end", {"status": "completed"})
        paused = seen.stream("/threads/j-2/runs", {"message": "hello"}, debug="true")
        failed = decide("j-2", paused[-1][1])
        rover_transport.down = False
        seen.state("j-2")

        # The approval choice, and a hand-off that waits for no one.
        assert (
            route("PATCH", f"/{first}", json={"version": 1, "approval": "none"}).status_code == 200
        )
        unpaused = seen.stream("/threads/j-3/runs", {"message": "hello"}, debug="true")
        assert unpaused[-1] == ("end", {"status": "completed"})

        # A task pending for the agent, given up by a move and told to its thread.
        now = client.portal.call(made.store.now)
        seed = {"agent": "rover", "task_id": "task-9", "thread_id": "j-4", "ref": f"{first}:1"}
        client.portal.call(_record_task, made, seed, now)
        moved = route("PATCH", f"/{first}", json={"version": 2, "url": ONWARD_URL})
        assert moved.status_code == 200 and moved.json()["given_up"] == 1
        tick(client, made.runtime)
        told = seen.state("j-4")

        # A removal with each refusal the other parts do not drive, then the removal.
        assert code(route("DELETE", "/env.stand", params={"version": 1})) == "agent_read_only"
        assert code(route("DELETE", f"/{'0' * 32}", params={"version": 1})) == "agent_not_found"
        assert route("DELETE", f"/{first}", params={"version": 3}).json() == {"given_up": 0}
        assert route("DELETE", f"/{shadow_id}", params={"version": 1}).status_code == 200

        # Saved again, under `127.0.0.1` this time: another agent at the same place.
        again = route("POST", json={"name": "rover", "url": ROVER_AS_LOOPBACK})
        assert again.status_code == 201 and again.json()["url"] == ROVER_AS_LOOPBACK
        assert again.json()["id"] != first
        second = again.json()["id"]
        waiting = seen.stream("/threads/j-5/runs", {"message": "hello"}, debug="true")
        sent = decide("j-5", waiting[-1][1])
        assert sent[-1] == ("end", {"status": "completed"})

        # With the agent down, on a link that holds its card: a round of watching that
        # cannot check its pending task, and a hand-off and a cancel that fail once
        # approved. The stand-in's refused connection names the address as the HTTP
        # library does.
        now = client.portal.call(made.store.now)
        seed = {"agent": "rover", "task_id": "task-10", "thread_id": "j-7", "ref": f"{second}:1"}
        client.portal.call(_record_task, made, seed, now)
        rover_transport.down = True
        tick(client, made.runtime)
        both = seen.stream("/threads/j-7/runs", {"message": "hello"}, debug="true")
        down = decide("j-7", both[-1][1])
        rover_transport.down = False
        seen.state("j-7")

        seen.call("GET", "/tools")
        seen.call("GET", "/healthz")
        listed = route("GET").json()
        in_threads = kept(made.runtime.agent.checkpointer)

    # What was driven happened.
    (unreached,) = tool_results(failed, "handoff_to_rover")
    assert unreached["content"] == (
        "Agent 'rover' cannot be used: Nothing answered at the address."
    )
    report = [m for m in told["messages"] if m["name"] == "check_agent_tasks"]
    assert len(report) == 1 and "moved to another address" in report[0]["content"]
    assert [message.text for message in rover.record.messages] == [
        "Sent with no pause.",
        "For the rover.",
    ]
    # The failures on a connected link are worded by the library, with the address taken out.
    (unsent,) = tool_results(down, "handoff_to_rover")
    (uncanceled,) = tool_results(down, "cancel_rover_task")
    for result in (unsent, uncanceled):
        assert result["content"].startswith("Agent 'rover' could not be reached: ")
        assert "<address>" in result["content"]
    assert onward.record.messages == [] and shadow.record.messages == []
    assert [request.method for request in onward_transport.requests] == ["GET"]
    assert {request.method for request in shadow_transport.requests} <= {"GET"}
    assert [agent["name"] for agent in listed["agents"]] == ["stand", "rover"]
    assert (len(refusals), len(answers)) == (14, 10)

    # Outside the Agents routes: every response, every stream with its debug events, and
    # every thread as the checkpointer keeps it. Each spelling of an address holds its port.
    assert any("handoff_to_rover" in text for text in seen.texts)
    assert any("<address>" in text for text in seen.texts)
    no_address(seen.texts, *JOURNEY_PORTS)
    assert any("Agent 'rover' cannot be used" in text for text in in_threads)
    no_address(in_threads, *JOURNEY_PORTS)
    for text in (*seen.texts, *in_threads):
        assert "localhost:" not in text.lower() and "127.0.0.1:" not in text, text[:300]
        # The path of a saved address is part of it.
        assert "onward-path" not in text, text[:300]

    # Every refusal, the ones for a store that fails naming two addresses included.
    no_address(refusals, *JOURNEY_PORTS)

    # An answer of the Agents routes holds a saved agent's address in `url`, as it was
    # entered, and in nothing else. The environment agent's is in no part of any.
    kept_urls = {ROVER_AS_LOCALHOST, ROVER_AS_LOOPBACK, ONWARD_URL, SHADOW_URL}
    assert {url for answer in answers for url in addresses(answer)} == kept_urls
    for answer in answers:
        no_address(strings(answer, but="url"), *JOURNEY_PORTS)
        no_address([json.dumps(answer)], STAND_PORT)
        assert all("onward-path" not in text for text in strings(answer, but="url"))
    # The cards' own words about their addresses came back with every form taken out.
    described = [
        agent["card"]["description"]
        for answer in answers
        for agent in (answer.get("agents") or [answer.get("agent") or answer])
        if isinstance(agent, dict) and agent.get("card")
    ]
    assert any(text.count("<address>") >= 11 for text in described)

    # Every log record at every level, with its arguments and its traceback.
    logged = [record.getMessage() for record in caplog.records]
    logged += [caplog.handler.format(record) for record in caplog.records]
    for line in ("Agent added: rover", "Agent changed: rover", "Agent removed: rover"):
        assert any(line in entry for entry in logged), line
    assert any("could not check task task-10" in line and "<address>" in line for line in logged)
    assert all("onward-path" not in line for line in logged)
    assert any("Agent checked: rover" in line for line in logged)
    assert any("The agents could not be listed: RuntimeError" in line for line in logged)
    no_address(logged, *JOURNEY_PORTS)


# What each route refuses with, by contracts/openapi.yaml: `invalid` stands for a 422, and
# `internal` for the store failing. The contract gives `internal` to the list alone; every
# route reads or writes the store, answers 500 when it fails, and is driven that way here.
CONTRACT = {
    ("GET", "/agents"): {"internal"},
    ("POST", "/agents"): {
        "name_taken",
        "address_taken",
        "agent_limit",
        "check_busy",
        "agent_unusable",
        "invalid",
        "other_origin",
        "internal",
    },
    ("PATCH", "/agents/{agent_id}"): {
        "agent_changed",
        "agent_read_only",
        "agent_not_found",
        "address_taken",
        "check_busy",
        "agent_unusable",
        "invalid",
        "other_origin",
        "internal",
    },
    ("DELETE", "/agents/{agent_id}"): {
        "agent_changed",
        "agent_read_only",
        "agent_not_found",
        "invalid",
        "other_origin",
        "internal",
    },
    ("POST", "/agents/{agent_id}/check"): {
        "check_busy",
        "agent_read_only",
        "agent_not_found",
        "invalid",
        "other_origin",
        "internal",
    },
}
CONTRACT_FILE = (
    Path(__file__).parent.parent / "specs/005-web-delegate-agents/contracts/openapi.yaml"
)


def test_the_table_of_refusals_is_the_contract_s() -> None:
    """The table above names every route of the router and every code of the contract, so
    a route or a code added to either without a line here fails."""
    routes = {
        (method, f"/agents{route.path.removeprefix('/agents')}")
        for route in agents_api.router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }
    assert routes == set(CONTRACT)
    contract = yaml.safe_load(CONTRACT_FILE.read_text())
    schemas = contract["components"]["schemas"]
    codes = set(schemas["Error"]["properties"]["detail"]["properties"]["code"]["enum"])
    codes.add(schemas["UnusableError"]["properties"]["detail"]["properties"]["code"]["const"])
    named = set().union(*CONTRACT.values()) - {"invalid"}
    assert named == codes
    paths = {
        (method.upper(), path)
        for path, operations in contract["paths"].items()
        for method in operations
        if method in ("get", "post", "patch", "delete")
    }
    assert paths == set(CONTRACT)
    # Route by route: what the contract's own answers for the route name.
    by_reference = {"NotFound": "agent_not_found", "OtherOrigin": "other_origin"}
    for (method, path), listed in CONTRACT.items():
        given: set[str] = set()
        for status, response in contract["paths"][path][method.lower()]["responses"].items():
            if str(status).startswith("2"):
                continue
            if str(status) == "422":
                given.add("invalid")
            reference = response.get("$ref", "").rpartition("/")[2]
            if reference in by_reference:
                given.add(by_reference[reference])
            words = re.findall(r"`([a-z_]+)`", response.get("description", ""))
            given |= {word for word in words if word in codes}
        assert listed - {"internal"} == given - {"internal"}, (method, path)
        assert given <= listed, (method, path)


def test_every_route_and_every_refusal_of_the_agents_section_is_driven_by_a_part() -> None:
    """The proof is complete only when each route was asked, and each refusal the contract
    gives a route was got, by one of the parts above, whose answers they then search."""
    if {1, 2, 3, 4, 5} != PARTS_RUN:
        pytest.skip("the parts of the proof did not all run in this session")
    asked = {(method, route) for method, route, _code in DRIVEN}
    assert asked == set(CONTRACT)
    answered = {(method, route) for method, route, code in DRIVEN if code is None}
    assert answered == set(CONTRACT), "every route also answers once with what it returns"
    missing = sorted(
        (method, route, code)
        for (method, route), codes in CONTRACT.items()
        for code in codes
        if (method, route, code) not in DRIVEN
    )
    assert missing == []
    beyond = sorted(
        entry
        for entry in DRIVEN
        if entry[2] is not None and entry[2] not in CONTRACT[(entry[0], entry[1])]
    )
    assert beyond == [], "a route gave a refusal the contract does not give it"
