"""009: what a caller is never given (FR-018, FR-028).

Part 1, the alias (research R6): on a caller's thread the model, and the subagent's, is told
``c-`` and the first 16 hexadecimal characters of the SHA-256 of the thread's id in place of
the id, so a caller that asks Freyja for its thread's id learns nothing it can use. A console
thread's model is told the id, as before.

Part 2, no token anywhere (FR-028, SC-006): with two named callers' recognizable tokens,
through every operation and refusal at both addresses, nothing the service answers, streams,
keeps, or logs holds either; and refused credentials are warned of sparingly.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, quote_plus

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr

from freyja_agent.agent.context import thread_alias
from freyja_agent.config import A2aCallerConfig
from freyja_agent.delegates.addresses import install_log_scrubber
from freyja_agent.runtime.callers import Callers
from tests.a2a_caller import StandInCaller, as_json
from tests.a2a_requests import at_rest, rpc, sending
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import ServerApp
from tests.fakes import ScriptedChatModel
from tests.nowhere import kept, logged, no_key

DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Look it up.", "subagent_type": "general-purpose"},
        }
    ],
)


def told(model: ScriptedChatModel) -> list[str]:
    """The system message of every model call, the subagent's included."""
    return [str(seen[0].content) for seen in model.seen]


def test_a_caller_s_thread_is_named_by_its_alias_on_every_model_call(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[DELEGATE, AIMessage(content="Found."), AIMessage("Done.")])
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "What is your thread's id?")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        calls = len(model.seen)
        with client.stream("POST", "/threads/console-7/runs", json={"message": "hi"}) as answer:
            sse_events(answer.iter_lines())
    alias = "c-" + hashlib.sha256(thread.encode()).hexdigest()[:16]
    assert thread_alias(thread) == alias
    caller_calls = told(model)[:calls]
    assert len(caller_calls) == 3  # Freyja, its subagent, Freyja again
    for system in caller_calls:
        assert f"thread `{alias}`" in system
        assert thread not in system
    assert all(thread not in str(seen) for seen in model.seen[:calls])
    assert "thread `console-7`" in told(model)[calls]


# Part 2: no token anywhere (FR-028, SC-006).

# The two made-up tokens .gitleaks.toml lets through (research R15).
ALPHA = "sk-recognizable-key-0001"
BETA = "sk-recognizable-other-0002"
OUTSIDE = "https://freyja.example.com"
NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)


def by(token: str) -> dict[str, str]:
    return {"A2A-Version": "1.0", "Authorization": f"Bearer {token}"}


def named(make: Callable[..., ServerApp], model: Any, **overrides: Any) -> ServerApp:
    return make(
        model,
        a2a_server_callers={"alpha": {"token": ALPHA}, "beta": {"token": BETA}},
        a2a_server_host="127.0.0.1",
        a2a_server_port=8443,
        a2a_server_url=OUTSIDE,
        **overrides,
    )


def test_no_caller_s_token_is_in_anything_the_service_answers_streams_keeps_or_logs(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    model = ScriptedChatModel(script=[NOTIFY], final_text="Done.")
    made = named(make_a2a_server_app, model, a2a_server_caller_tasks=1, a2a_server_wait_seconds=0)
    texts: list[str] = []
    with TestClient(made.app) as client:

        def own(method: str, params: dict[str, Any], token: str | None) -> Any:
            headers = by(token) if token else {"A2A-Version": "1.0"}
            answer = rpc(client, method, params, headers)
            texts.append(answer.text)
            return answer

        async def outside(work: Callable[[StandInCaller], Any], token: str) -> Any:
            transport = StreamingASGITransport(made.outside)
            async with StandInCaller(OUTSIDE, transport=transport, token=token) as caller:
                return await work(caller)

        # A task waiting for a person, sent with the token pasted into the message.
        paused = own(
            "SendMessage", sending(f"Tell #ops. My token is {ALPHA}.", contextId="c-1"), ALPHA
        )
        task_id = paused.json()["result"]["task"]["id"]
        at_rest(client, made, task_id)
        # A refused limit, a read, a subscription that is refused, and a cancel.
        own("SendMessage", sending("Another.", contextId="c-2"), ALPHA)
        own("GetTask", {"id": task_id}, ALPHA)
        own("CancelTask", {"id": task_id}, ALPHA)
        own("SubscribeToTask", {"id": task_id}, ALPHA)
        own("CancelTask", {"id": task_id}, ALPHA)
        own("GetTask", {"id": task_id}, BETA)
        own("SendMessage", sending("Wrong."), "sk-recognizable-wrong-0003")
        own("SendMessage", sending("Local."), None)
        at_rest(client, made, task_id)
        # Through the outside listener: a stream, a card, and refusals.
        events = client.portal.call(
            outside,
            lambda caller: _all(caller.stream("Say hello.", context="c-9")),
            BETA,
        )
        texts.extend(json.dumps(event) for event in events)

        async def raw() -> list[str]:
            transport = StreamingASGITransport(made.outside)
            async with httpx.AsyncClient(transport=transport, base_url=OUTSIDE) as http:
                found = [
                    await http.get("/.well-known/agent-card.json"),
                    await http.post(
                        "/a2a",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "GetTask",
                            "params": {"id": task_id},
                        },
                        headers=by("sk-recognizable-wrong-0003"),
                    ),
                    await http.post(f"/a2a?token={ALPHA}", json={}, headers={"A2A-Version": "1.0"}),
                ]
                return [answer.text for answer in found]

        texts.extend(client.portal.call(raw))
        thread = made.task(client, task_id).thread_id
        for path in ("/threads", f"/threads/{thread}", f"/threads/{thread}/state"):
            texts.append(client.get(path).text)
        # A line of the web server's access log, as uvicorn writes one.
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "POST", f"/a2a?token={ALPHA}", "1.1", 401
        )
    told = [
        str(message.content)
        for seen in model.seen
        for message in seen
        if isinstance(message, HumanMessage)
    ]
    assert any("My token is" in text for text in told)
    lines = logged(caplog)
    no_key(texts + kept(made.a2a.saver) + lines + told, ALPHA, BETA)
    access = [line for line in lines if "/a2a?token=" in line]
    assert access, "the access log line was not written"
    # One line each, naming the caller.
    assert any(f"Caller alpha: received task {task_id} accepted" in line for line in lines)
    assert any(f"Caller alpha canceled received task {task_id}" in line for line in lines)
    assert any("caller alpha was refused" in line for line in lines)
    assert any("refused for its credential" in line for line in lines)


async def _all(events: Any) -> list[dict]:
    return [as_json(event) async for event in events]


def test_refused_credentials_are_warned_of_once_then_at_most_once_in_ten_seconds_for_each_address(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = named(make_a2a_server_app, ScriptedChatModel())
    now = [1000.0]
    with TestClient(made.app) as client:
        made.callers.clock = lambda: now[0]

        async def outside_refused() -> None:
            transport = StreamingASGITransport(made.outside)
            async with httpx.AsyncClient(transport=transport, base_url=OUTSIDE) as http:
                await http.post("/a2a", json={}, headers={"A2A-Version": "1.0"})

        with caplog.at_level(logging.WARNING, logger="freyja_agent"):
            for _ in range(100):
                rpc(client, "GetTask", {"id": "0" * 32}, by("sk-recognizable-wrong-0003"))
            first = warnings(caplog)
            for _ in range(100):
                client.portal.call(outside_refused)
            both = warnings(caplog)
            now[0] += 10
            rpc(client, "GetTask", {"id": "0" * 32}, by("sk-recognizable-wrong-0003"))
            client.portal.call(outside_refused)
            later = warnings(caplog)
    assert len(first) == 1 and "the service's own address" in first[0]
    assert len(both) == 2 and "the outside listener" in both[1]
    assert len(later) == 4
    assert "100 requests" in later[2] and "the service's own address" in later[2]
    assert "100 requests" in later[3] and "the outside listener" in later[3]


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The warnings of refused credentials, in order."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "credential" in record.getMessage()
    ]


def _lower_hex(text: str) -> str:
    return re.sub(r"%[0-9A-F]{2}", lambda found: found.group(0).lower(), text)


@pytest.mark.parametrize(
    "token",
    ["Zm9vYmFy+YmF6/cXV4PT0=", "sk-key!(0001)*abc&def", "sk-back\\slash-0001", 'sk-quote"d-0001'],
    ids=["base64", "url-reserved", "backslash", "quote"],
)
def test_a_token_is_cleaned_in_every_way_a_path_a_query_or_a_log_line_writes_it(
    token: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Review of request 5: a path as uvicorn writes it keeps ``/``, a browser's
    ``encodeURIComponent`` keeps ``!'()*``, hex may be lowercase, and a dict's ``repr``
    doubles a backslash."""
    forms = {
        token,
        quote(token, safe=""),
        quote(token),
        quote_plus(token),
        quote(token, safe="!'()*"),
        _lower_hex(quote(token, safe="")),
        _lower_hex(quote(token)),
        repr(token)[1:-1],
        json.dumps(token)[1:-1],
    }
    install_log_scrubber()
    Callers({"alpha": A2aCallerConfig(token=SecretStr(token))})
    try:
        with caplog.at_level(logging.DEBUG):
            for form in forms:
                logging.getLogger("uvicorn.access").info(
                    '%s - "%s %s HTTP/%s" %d',
                    "127.0.0.1:1",
                    "POST",
                    f"/a2a/{form}?t={form}",
                    "1.1",
                    401,
                )
                logging.getLogger("a2a.server").debug("Request body: %s", {"token": token})
        for form in forms:
            assert form not in Callers.scrub(f"before {form} after")
        no_key(logged(caplog), *forms)
    finally:
        Callers.forget()
