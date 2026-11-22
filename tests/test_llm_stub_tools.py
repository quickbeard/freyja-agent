"""The stand-in model endpoint's ``call-tools`` id (feature 007, T097).

The quickstart of the Tasks section needs a model that asks for tools: a proposal to
decide, a hand-off to an agent, a run of several steps to stop. The stand-in answered in
text only, so the quickstart could not be walked with what the repository holds. These run
the real client, the real run loop, and the console's routes against it.
"""

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import LlmApp
from tests.llm_stub import CHAT_ONLY, TOOL_CALLER, LlmStub, Plan, plan
from tests.unattended_helpers import idle, kinds

KEY = "sk-recognizable-key-0001"
BASE_URL = "http://127.0.0.1:9997/v1"
RUN = "/threads/{}/runs/unattended"
RESUME = "/threads/{}/resume/unattended"


def activate(client: TestClient, **values: Any) -> None:
    profile = {
        "name": "Calls tools",
        "provider": "openai",
        "model": TOOL_CALLER,
        "base_url": BASE_URL,
        "api_key": KEY,
        "responses_api": False,
        **values,
    }
    saved = client.post("/llm/profiles", json=profile)
    assert saved.status_code == 201, saved.text
    assert client.post(f"/llm/profiles/{saved.json()['id']}/activate").status_code == 200


def run(client: TestClient, sse_events: Any, path: str, body: dict[str, Any]) -> list[Any]:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


@pytest.fixture
def made(make_llm_app: Callable[..., LlmApp], monkeypatch: pytest.MonkeyPatch) -> LlmApp:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")
    return make_llm_app(model="openai:env-model", models=LlmStub().factory)


def test_notify_proposes_a_notification_and_answers_once_it_was_approved(
    made: LlmApp, sse_events: Any
) -> None:
    with TestClient(made.app) as client:
        activate(client)
        paused = run(
            client, sse_events, RUN.format("t-1"), {"message": "notify The report is ready."}
        )
        assert paused[-1][0] == "interrupt", paused[-1]
        (action,) = paused[-1][1]["action_requests"]
        decision = {
            "interrupt_id": paused[-1][1]["interrupt_id"],
            "decisions": [{"type": "approve"}],
        }
        done = run(client, sse_events, RESUME.format("t-1"), decision)
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()

    assert (action["name"], action["args"]) == (
        "send_notification",
        {"channel": "#ops", "message": "The report is ready."},
    )
    assert done[-1] == ("end", {"status": "completed"})
    # Both replies are in the conversation: each had an id of its own.
    assert kinds(state) == ["human", "ai", "tool", "ai"]
    assert state["messages"][1]["tool_calls"][0]["name"] == "send_notification"
    assert state["messages"][-1]["content"].startswith("Done: ")
    assert "#ops" in state["messages"][-1]["content"]
    assert len({message["id"] for message in state["messages"]}) == 4


def test_steps_takes_as_many_steps_as_it_is_asked_and_then_says_so(
    made: LlmApp, sse_events: Any
) -> None:
    with TestClient(made.app) as client:
        activate(client)
        done = run(client, sse_events, RUN.format("t-1"), {"message": "steps 3"})
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()

    assert done[-1] == ("end", {"status": "completed"})
    assert kinds(state) == ["human", "ai", "tool", "ai", "tool", "ai", "tool", "ai"]
    assert [m["tool_calls"][0]["name"] for m in state["messages"][1:6:2]] == ["ls"] * 3
    assert state["messages"][-1]["content"] == "Took 3 steps."
    assert len({message["id"] for message in state["messages"]}) == 8


def test_two_plain_replies_on_one_task_are_both_kept(made: LlmApp, sse_events: Any) -> None:
    """Every model id gives each reply an id of its own; under one id the second reply
    would take the first one's place in the conversation."""
    with TestClient(made.app) as client:
        activate(client, model="ok")
        for message in ("Say hello.", "And again."):
            done = run(client, sse_events, RUN.format("t-1"), {"message": message})
            assert done[-1] == ("end", {"status": "completed"})
            idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()

    assert kinds(state) == ["human", "ai", "human", "ai"]
    assert [m["content"] for m in state["messages"]] == [
        "Say hello.",
        "ready",
        "And again.",
        "ready",
    ]


def user(text: Any) -> dict[str, Any]:
    return {"role": "user", "content": text}


TOOLS = ["ls", "send_notification", "handoff_to_stub"]
RESULT = {"role": "tool", "content": "Notification to #ops recorded."}
ASKED = {"role": "assistant", "content": None}


@pytest.mark.parametrize(
    ("turns", "expected"),
    [
        (
            [user("notify Ready.")],
            Plan(call=("send_notification", {"channel": "#ops", "message": "Ready."})),
        ),
        (
            [user("notify")],
            Plan(call=("send_notification", {"channel": "#ops", "message": "hello"})),
        ),
        (
            [user([{"type": "text", "text": "notify In parts."}])],
            Plan(call=("send_notification", {"channel": "#ops", "message": "In parts."})),
        ),
        (
            [user("handoff stub Compile the report.")],
            Plan(call=("handoff_to_stub", {"message": "Compile the report."})),
        ),
        ([user("handoff stub")], Plan(call=("handoff_to_stub", {"message": "hello"}))),
        ([user("steps 2")], Plan(call=("ls", {"path": "/"}), step=True)),
        ([user("steps 2"), ASKED, RESULT], Plan(call=("ls", {"path": "/"}), step=True)),
        ([user("steps 2"), ASKED, RESULT, ASKED, RESULT], Plan(text="Took 2 steps.", step=True)),
        ([user("steps 0")], Plan(text="Took 0 steps.", step=True)),
        (
            [user("notify Ready."), ASKED, RESULT],
            Plan(text="Done: Notification to #ops recorded."),
        ),
        # A result of an earlier message says nothing of the last one.
        (
            [user("notify One."), ASKED, RESULT, ASKED, user("notify Two.")],
            Plan(call=("send_notification", {"channel": "#ops", "message": "Two."})),
        ),
        # Anything else is answered as every other id answers.
        ([user("Say hello.")], Plan(text="ready")),
        ([user("steps many")], Plan(text="ready")),
        ([user("handoff")], Plan(text="ready")),
        ([user("handoff Stub! now")], Plan(text="ready")),
        ([user("Notify with a capital")], Plan(text="ready")),
        ([], Plan(text="ready")),
        # A tool it was not given is said, not called.
        (
            [user("handoff other Do it.")],
            Plan(text="The tool handoff_to_other is not among the tools I was given."),
        ),
    ],
)
def test_what_it_answers_is_read_from_the_last_message_of_a_person(
    turns: list[dict[str, Any]], expected: Plan
) -> None:
    assert plan(turns, TOOLS) == expected


def test_a_tool_it_was_not_handed_is_never_called() -> None:
    for message in ("notify Ready.", "steps 2", "handoff stub Do it."):
        planned = plan([user(message)], [])
        assert planned.call is None
        assert planned.text.endswith("is not among the tools I was given.")


def ask(stub: LlmStub, path: str, body: dict[str, Any]) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=stub.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://stub") as client:
            return await client.post(path, json=body)

    return asyncio.run(send())


def test_it_says_that_it_answers_chat_completions_only() -> None:
    stub = LlmStub()
    answer = ask(stub, "/v1/responses", {"model": TOOL_CALLER, "input": "notify Ready."})
    assert answer.status_code == 400
    assert answer.json()["error"]["message"] == CHAT_ONLY
    assert "FREYJA_OPENAI_RESPONSES_API=false" in CHAT_ONLY


def test_an_answer_that_is_not_streamed_holds_the_same_call() -> None:
    stub = LlmStub()
    body = {
        "model": TOOL_CALLER,
        "messages": [user("notify Ready.")],
        "tools": [{"type": "function", "function": {"name": "send_notification"}}],
    }
    first = ask(stub, "/v1/chat/completions", body).json()
    second = ask(stub, "/v1/chat/completions", body).json()

    (choice,) = first["choices"]
    assert choice["finish_reason"] == "tool_calls"
    (call,) = choice["message"]["tool_calls"]
    assert call["function"] == {
        "name": "send_notification",
        "arguments": '{"channel": "#ops", "message": "Ready."}',
    }
    # Each reply, and each call, has an id of its own.
    assert first["id"] != second["id"]
    assert call["id"] != second["choices"][0]["message"]["tool_calls"][0]["id"]


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/responses"])
def test_every_reply_of_every_model_id_has_an_id_of_its_own(path: str) -> None:
    stub = LlmStub()
    body = {"model": "ok", "messages": [user("hi")], "input": "hi"}
    ids = {ask(stub, path, body).json()["id"] for _ in range(3)}
    assert len(ids) == 3


def test_a_step_waits_only_when_the_stand_in_is_served_by_hand() -> None:
    """The suite waits for nothing; served as a module it gives a person time to press Stop."""
    assert LlmStub().step_seconds == 0
    stub = LlmStub(step_seconds=0.2)
    body = {
        "model": TOOL_CALLER,
        "messages": [user("steps 1")],
        "tools": [{"type": "function", "function": {"name": "ls"}}],
    }

    async def timed(messages: list[dict[str, Any]]) -> float:
        transport = httpx.ASGITransport(app=stub.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://stub") as client:
            began = asyncio.get_running_loop().time()
            await client.post("/v1/chat/completions", json={**body, "messages": messages})
            return asyncio.get_running_loop().time() - began

    assert asyncio.run(timed([user("steps 1")])) >= 0.2
    assert asyncio.run(timed([user("notify Ready.")])) < 0.2
