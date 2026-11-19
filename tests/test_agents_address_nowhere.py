"""005 FR-028, SC-008: an agent's address appears nowhere outside the Agents routes (parts 1
and 2).

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
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from freyja_agent.api import agents as agents_api
from freyja_agent.config import canonical_address, destination
from freyja_agent.delegates.agent_cards import SENTENCES
from tests.a2a_stub import StubAgent, make_stub, stub_card
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import A2aApp, holds_port
from tests.fakes import ScriptedChatModel
from tests.test_a2a_gate import propose
from tests.test_a2a_handoff import DONE, handoff, tool_results
from tests.test_llm_secrets_nowhere import Seen, kept

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


def found(text: str, port: int) -> bool:
    return holds_port(text, port) or f":{port}" in text


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


def strings(value: Any, *, but: str | None = None) -> list[str]:
    """Every text in a JSON value, keys included, leaving out what is under the key ``but``."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            if key != but
            for text in (key, *strings(item, but=but))
        ]
    if isinstance(value, list):
        return [text for item in value for text in strings(item, but=but)]
    return [json.dumps(value)]


def addresses(value: Any) -> list[str]:
    """Every ``url`` of an Agents answer that holds one."""
    if isinstance(value, dict):
        own = [value["url"]] if isinstance(value.get("url"), str) else []
        return own + [text for item in value.values() for text in addresses(item)]
    if isinstance(value, list):
        return [text for item in value for text in addresses(item)]
    return []


def nowhere(texts: list[str], *ports: int) -> None:
    for text in texts:
        for port in ports:
            assert not found(text, port), f"{port} found in: {text[:400]}"


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
            agents_route("POST", json={"name": "other", "url": REFUSED_URL, "approval": "none"}),
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
    nowhere(seen.texts, *PORTS)
    assert any("could not be reached" in text for text in in_threads)
    nowhere(in_threads, *PORTS)

    # Every refusal's reason, the Agents routes' own included.
    nowhere(refusals, *PORTS)

    # An answer of the Agents routes holds a saved agent's address in `url`, in the form it is
    # kept in, and in nothing else: not in what a card says, and not in a reason.
    assert any(addresses(answer) for answer in answers)
    for answer in answers:
        assert set(addresses(answer)) <= {kept_url}
        nowhere(strings(answer, but="url"), *PORTS)
        # The environment agent's address, and a refused add's, are in no part of it.
        nowhere([json.dumps(answer)], ENVIRONMENT_PORT, REFUSED_PORT, SLOW_PORT)
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
    nowhere(logged, *PORTS)


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
    nowhere(results, *CHECK_PORTS)
    nowhere(refusals, *CHECK_PORTS)
    # The catalog describes both agents from the new details, with every address taken out.
    assert any("Now with reports. Reach me at <address>/a2a" in text for text in seen.texts)
    nowhere(seen.texts, *CHECK_PORTS)
    # The list holds the saved agent's address in `url` and in nothing else.
    for answer in answers:
        assert addresses(answer) == [CHECKED_URL]
        nowhere(strings(answer, but="url"), *CHECK_PORTS)
        cards = [agent["card"]["description"] for agent in answer["agents"]]
        assert len(cards) == 2
        assert all(text.startswith("Now with reports. Reach me at <address>") for text in cards)
    # Every log record at every level, with its arguments and its traceback.
    logged = [record.getMessage() for record in caplog.records]
    logged += [caplog.handler.format(record) for record in caplog.records]
    assert sum("Agent checked: helper" in line for line in logged) == 2 * 11
    assert sum("Agent checked: desk" in line for line in logged) == 2 * 3
    nowhere(logged, *CHECK_PORTS)
