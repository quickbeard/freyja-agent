"""004 FR-018, FR-019, FR-021: a profile's test, against the stand-in endpoint (research R7).

The model is the real ``ChatOpenAI`` client, built the way the service builds a saved
profile's, and only its transport is replaced: its requests reach ``tests/llm_stub.py`` in the
test process. Both APIs are covered, because the failure this test exists to catch was seen
over the Responses API.
"""

import asyncio
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from freyja_agent.llm.check import (
    FIRST_MESSAGE,
    SECOND_MESSAGE,
    TESTS_AT_ONCE,
    TOOL_NAME,
    CheckSlots,
    check_model,
)
from freyja_agent.llm.providers import ModelSpec, build_model
from freyja_agent.llm.secrets import REDACTED
from tests.conftest import LlmApp
from tests.fakes import FailingChatModel
from tests.llm_stub import REFUSAL, LlmStub, error

KEY = "sk-recognizable-key-0001"
BASE_URL = "http://127.0.0.1:9997/v1"
APIS = pytest.mark.parametrize("responses_api", [False, True], ids=["chat", "responses"])


def spec(model: str = "ok", *, responses_api: bool = False, **values: str) -> ModelSpec:
    given = ModelSpec(
        provider="openai",
        model=model,
        base_url=BASE_URL,
        api_key=KEY,
        responses_api=responses_api,
        max_retries=0,
    )
    return replace(given, **values)


async def check(stub: LlmStub, model: str, *, responses_api: bool, limit: float = 5) -> Any:
    built = stub.factory(spec(model, responses_api=responses_api))
    return await check_model(built, limit=limit, secrets=[KEY])


@APIS
async def test_an_endpoint_that_handles_a_conversation_passes_after_two_requests(
    responses_api: bool,
) -> None:
    stub = LlmStub()
    result = await check(stub, "ok", responses_api=responses_api)

    assert (result.ok, result.step, result.reason) == (True, None, None)
    first, second = stub.requests
    assert {r.api for r in stub.requests} == {"responses" if responses_api else "chat"}
    assert {r.key for r in stub.requests} == {KEY}
    # The first request is one user message and nothing else.
    assert not first.earlier_turns
    assert [turn.get("role") for turn in first.turns] == ["user"]
    assert FIRST_MESSAGE in str(first.turns[0]["content"])
    assert first.tool_names == []
    # The second carries that message, an assistant turn that calls a tool, the tool's
    # result, and a second user message, with the tool's definition, as a run's request does.
    assert second.earlier_turns
    assert second.tool_names == [TOOL_NAME]
    text = str(second.turns)
    assert FIRST_MESSAGE in text and SECOND_MESSAGE in text
    assert text.index(FIRST_MESSAGE) < text.index(SECOND_MESSAGE)
    if responses_api:
        kinds = [turn.get("type") or turn.get("role") for turn in second.turns]
        assert "function_call" in kinds and "function_call_output" in kinds
        assert kinds.index("function_call") < kinds.index("function_call_output")
    else:
        roles = [turn["role"] for turn in second.turns]
        assert roles == ["user", "assistant", "tool", "user"]
        assert second.turns[1]["tool_calls"][0]["function"]["name"] == TOOL_NAME
        assert second.turns[2]["tool_call_id"] == second.turns[1]["tool_calls"][0]["id"]


@APIS
async def test_an_endpoint_that_refuses_earlier_turns_fails_on_the_conversation(
    responses_api: bool,
) -> None:
    stub = LlmStub()
    result = await check(stub, "refuse-conversation", responses_api=responses_api)

    assert (result.ok, result.step) == (False, "conversation")
    # The endpoint's own reason, so the operator sees what it objected to.
    assert REFUSAL in result.reason
    assert "422" in result.reason
    assert len(stub.requests) == 2


@APIS
async def test_an_endpoint_that_rejects_the_key_fails_on_the_first_message(
    responses_api: bool,
) -> None:
    stub = LlmStub()
    result = await check(stub, "reject-key", responses_api=responses_api)

    assert (result.ok, result.step) == (False, "first_message")
    assert "401" in result.reason
    assert "Incorrect API key" in result.reason
    # No retry: the limit on a test's time is only a limit if a refusal ends it.
    assert len(stub.requests) == 1


async def test_an_address_that_cannot_be_reached_fails_on_the_first_message() -> None:
    # Nothing listens on the discard port; this is the real client and the real transport.
    unreachable = build_model(spec(base_url="http://127.0.0.1:9/v1"))
    started = time.monotonic()
    result = await check_model(unreachable, limit=5, secrets=[KEY])

    assert (result.ok, result.step) == (False, "first_message")
    assert result.reason
    assert KEY not in result.reason
    assert time.monotonic() - started < 4


@APIS
async def test_an_endpoint_that_does_not_answer_ends_at_the_time_limit(
    responses_api: bool,
) -> None:
    stub = LlmStub()
    started = time.monotonic()
    result = await check(stub, "never-answer", responses_api=responses_api, limit=0.3)
    took = time.monotonic() - started

    assert (result.ok, result.step) == (False, "first_message")
    assert "did not answer" in result.reason
    assert "0.3 seconds" in result.reason
    assert 0.25 < took < 2
    assert len(stub.requests) == 1


async def test_the_time_limit_covers_both_steps_together() -> None:
    """An endpoint that answers the first message and then goes quiet fails on the second."""

    class QuietSecond(LlmStub):
        async def _answer(self, api: str, request: Any) -> Any:
            if self.requests:
                await asyncio.Event().wait()
            return await super()._answer(api, request)

    stub = QuietSecond()
    result = await check(stub, "ok", responses_api=False, limit=0.5)
    assert (result.ok, result.step) == (False, "conversation")
    assert "did not answer" in result.reason


@APIS
async def test_a_reason_never_holds_the_key(responses_api: bool) -> None:
    stub = LlmStub()
    result = await check(stub, "repeat-key", responses_api=responses_api)

    # The endpoint repeated the key in its error, and the reason has it removed.
    assert (result.ok, result.step) == (False, "first_message")
    assert KEY not in result.reason
    assert REDACTED in result.reason
    assert "is not allowed to use this model" in result.reason


async def test_a_long_reason_is_cut_to_five_hundred_characters() -> None:
    class Wordy(LlmStub):
        async def _answer(self, api: str, request: Any) -> Any:
            return error(400, "no " * 400, "wordy")

    result = await check(Wordy(), "ok", responses_api=False)
    assert result.ok is False
    assert len(result.reason) == 500


async def test_a_model_that_cannot_be_asked_reports_why_without_the_key() -> None:
    """Whatever the client raises is a reason, not an error of the test's own."""
    model = FailingChatModel(fail_on_call=1, error=f"cannot use {KEY} here")
    result = await check_model(model, limit=5, secrets=[KEY])
    assert (result.ok, result.step) == (False, "first_message")
    assert result.reason == f"cannot use {REDACTED} here"

    later = FailingChatModel(fail_on_call=2, error="no earlier turns")
    result = await check_model(later, limit=5, secrets=[KEY])
    assert (result.ok, result.step, result.reason) == (False, "conversation", "no earlier turns")
    assert later.calls == 2


@APIS
async def test_a_request_that_asks_for_a_stream_is_answered_as_one(responses_api: bool) -> None:
    """A run streams its requests, so a profile pointing at the stand-in can be run on."""
    stub = LlmStub()
    model = stub.factory(spec("ok", responses_api=responses_api))
    text = ""
    async for chunk in model.astream("Say hello."):
        text += chunk.text
    assert text == "ready"
    (request,) = stub.requests
    assert request.body["stream"] is True
    assert request.api == ("responses" if responses_api else "chat")


def test_a_run_on_a_profile_that_points_at_the_stand_in_completes(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The quickstart's runs: the real client, the real run loop, and the stand-in."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")
    stub = LlmStub()
    made = make_llm_app(model="openai:env-model", models=stub.factory)
    values = {
        "name": "Stub",
        "provider": "openai",
        "model": "ok",
        "base_url": BASE_URL,
        "api_key": KEY,
        "responses_api": False,
    }
    with TestClient(made.app) as client:
        stub_id = client.post("/llm/profiles", json=values).json()["id"]
        client.post(f"/llm/profiles/{stub_id}/activate")
        with client.stream("POST", "/threads/t-1/runs", json={"message": "Say hello."}) as stream:
            events = sse_events(stream.iter_lines())
        reply = client.get("/threads/t-1/state").json()["messages"][-1]

    assert events[0][1]["profile"]["name"] == "Stub"
    assert events[-1] == ("end", {"status": "completed"})
    assert (reply["type"], reply["content"]) == ("ai", "ready")
    assert [r.model for r in stub.requests] == ["ok"]


@APIS
def test_the_routes_test_a_profile_through_the_real_client(
    make_llm_app: Callable[..., LlmApp], responses_api: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path: the route, the model built for a test, the client, and an endpoint."""
    # The environment's model is a real client here too, and is built at startup.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")
    stub = LlmStub()
    made = make_llm_app(model="openai:env-model", models=stub.factory)
    values = {
        "provider": "openai",
        "base_url": BASE_URL,
        "api_key": KEY,
        "responses_api": responses_api,
    }
    with TestClient(made.app) as client:
        works = client.post("/llm/profiles", json={**values, "name": "Works", "model": "ok"})
        refuses = client.post(
            "/llm/profiles", json={**values, "name": "Refuses", "model": "refuse-conversation"}
        )
        passed = client.post(f"/llm/profiles/{works.json()['id']}/test").json()
        failed = client.post(f"/llm/profiles/{refuses.json()['id']}/test").json()
        unsaved = client.post("/llm/test", json={**values, "model": "repeat-key"}).json()
        listed = {p["name"]: p["last_test"] for p in client.get("/llm/profiles").json()["profiles"]}

    assert (passed["ok"], passed["step"], passed["recorded"]) == (True, None, True)
    assert (failed["ok"], failed["step"], failed["recorded"]) == (False, "conversation", True)
    assert REFUSAL in failed["reason"]
    assert (unsaved["ok"], unsaved["step"], unsaved["recorded"]) == (False, "first_message", False)
    assert KEY not in unsaved["reason"] and REDACTED in unsaved["reason"]
    assert listed["Works"]["ok"] is True
    assert (listed["Refuses"]["ok"], listed["Refuses"]["step"]) == (False, "conversation")
    # Two requests for each of the two profiles and one for the values that failed at once,
    # every one with the key and none tried twice.
    assert [r.model for r in stub.requests] == [
        "ok",
        "ok",
        "refuse-conversation",
        "refuse-conversation",
        "repeat-key",
    ]
    assert {r.key for r in stub.requests} == {KEY}
    assert {r.api for r in stub.requests} == {"responses" if responses_api else "chat"}


def test_slots_refuse_in_the_words_they_were_made_with() -> None:
    """Feature 005, research R15: card reads are counted as tests are, in words of their own."""
    slots = CheckSlots()
    slots.take("p1")
    assert slots.refusal("p1") == "This profile is already being tested. Wait for that test to end."
    for _ in range(TESTS_AT_ONCE - 1):
        slots.take(None)
    assert slots.refusal("p2") == (
        f"{TESTS_AT_ONCE} tests are already running on this instance. "
        "Try again when one of them ends."
    )

    reads = CheckSlots(
        busy="This agent is already being checked.",
        full="{at_once} card reads are already under way.",
        at_once=2,
    )
    assert reads.refusal("a1") is None
    reads.take("a1")
    assert reads.refusal("a1") == "This agent is already being checked."
    assert reads.refusal("a2") is None
    reads.take(None)
    assert reads.refusal("a2") == "2 card reads are already under way."
    reads.release(None)
    reads.release("a1")
    assert reads.refusal("a1") is None


async def test_what_a_test_is_recorded_with_is_entered_around_each_of_its_two_requests() -> None:
    """Feature 011: telemetry records a test's calls through this, and changes no result."""
    entered: list[str] = []

    @contextmanager
    def recorded() -> Iterator[None]:
        entered.append("in")
        yield
        entered.append("out")

    stub = LlmStub()
    built = stub.factory(spec("ok"))
    result = await check_model(built, limit=5, secrets=[KEY], recorded=recorded)

    assert result.ok and entered == ["in", "out", "in", "out"]
    assert len(stub.requests) == 2
