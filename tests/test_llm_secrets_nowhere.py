"""004 FR-020, FR-021, SC-004: a key appears nowhere beyond its last four characters.

One recognizable key is saved in a profile and every ``/llm`` route is driven with it,
refusals and all, with tests that pass and fail and runs that complete, pause, and fail. Then
everything the service returned, streamed, kept in a thread, logged, and stored is searched.
"""

import asyncio
import base64
import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.llm.secrets import REDACTED, decrypt
from tests.conftest import LlmApp
from tests.fakes import ScriptedChatModel
from tests.llm_stub import LlmStub

# Nothing else in the suite holds this text, so finding any of it means a key got out.
KEY = "sk-recognizable-key-0001"
NEW_KEY = "sk-recognizable-other-0002"
MARK = "recognizable"
ENV_KEY = "sk-environment-secret-9f2c"
ENV_MARK = "environment-secret"
BASE_URL = "http://127.0.0.1:9997/v1"


@pytest.fixture(autouse=True)
def environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)


class LeakyModel(ScriptedChatModel):
    """A model whose provider repeats the key in every error, until it is ``healed``."""

    healed: bool = False

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        if not self.healed:
            self.calls += 1
            raise RuntimeError(f"401: Incorrect API key provided: {KEY}. Check {KEY}!")
        return super()._generate(messages, stop, run_manager, **kwargs)


class Seen:
    """Everything the service gave back, kept as text to be searched."""

    def __init__(self, client: TestClient, sse_events: Any) -> None:
        self.client = client
        self.sse_events = sse_events
        self.texts: list[str] = []

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        self.texts.append(f"{method} {path} -> {response.status_code} {response.text}")
        return response

    def stream(self, path: str, body: dict[str, Any], **params: Any) -> list[tuple[str, Any]]:
        with self.client.stream("POST", path, json=body, params=params) as response:
            assert response.status_code == 200, response.read()
            lines = list(response.iter_lines())
        # The stream as it went over the wire, every event of it.
        self.texts.append(f"POST {path} stream\\n" + "\\n".join(lines))
        return self.sse_events(iter(lines))

    def state(self, thread: str) -> dict[str, Any]:
        return self.call("GET", f"/threads/{thread}/state").json()


def kept(saver: Any) -> list[str]:
    """Everything the in-memory checkpointer holds, as text: checkpoints, blobs, and the writes
    of each step, a failed step's error among them."""
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, bytes | bytearray):
            texts.append(bytes(value).decode("latin-1"))
        elif isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    for part in (saver.storage, saver.blobs, saver.writes):
        walk(part)
    return texts


def nowhere(texts: list[str], *marks: str) -> None:
    for text in texts:
        for mark in marks:
            assert mark not in text, f"{mark!r} found in: {text[:300]}"


def profile(**values: Any) -> dict[str, Any]:
    return {
        "name": "Stub",
        "provider": "openai",
        "model": "saved-model",
        "base_url": BASE_URL,
        "api_key": KEY,
        "responses_api": False,
        **values,
    }


def test_a_key_is_in_nothing_the_service_returns_streams_keeps_or_logs(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:env-model")
    saved_model = made.models.model("saved-model")
    saved_model.script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "n1",
                    "name": "send_notification",
                    "args": {"channel": "#ops", "message": "Nightly report is ready."},
                }
            ],
        )
    ]
    made.models.models["leaky-model"] = LeakyModel()

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = Seen(client, sse_events)

        # Every route, as it answers when all is well.
        added = seen.call("POST", "/llm/profiles", json=profile())
        assert added.status_code == 201
        stub_id = added.json()["id"]
        seen.call("GET", "/llm/profiles")
        assert (
            seen.call("PATCH", f"/llm/profiles/{stub_id}", json={"name": "Renamed"}).status_code
            == 200
        )
        assert (
            seen.call("PATCH", f"/llm/profiles/{stub_id}", json={"api_key": NEW_KEY}).status_code
            == 200
        )
        assert (
            seen.call("PATCH", f"/llm/profiles/{stub_id}", json={"api_key": KEY}).status_code == 200
        )
        assert seen.call("POST", f"/llm/profiles/{stub_id}/activate").status_code == 200

        # Every refusal and every 422, each sent with the key in it where a key can go.
        refusals = [
            seen.call("POST", "/llm/profiles", json=profile(name="renamed")),  # name_taken
            seen.call("POST", "/llm/profiles", json=profile(name="Environment")),
            seen.call("POST", "/llm/profiles", json=profile(name="n" * 65)),
            seen.call("POST", "/llm/profiles", json=profile(name="X", api_key=KEY + "k" * 4000)),
            seen.call("POST", "/llm/profiles", json=profile(name="X", base_url=f"not-a-url-{KEY}")),
            seen.call("POST", "/llm/profiles", json=profile(name="X", surprise=KEY)),
            seen.call("POST", "/llm/profiles", json={"api_key": KEY}),
            seen.call(
                "POST",
                "/llm/profiles",
                content=f'{{"api_key": "{KEY}"',
                headers={"content-type": "application/json"},
            ),
            seen.call(
                "POST",
                "/llm/profiles",
                content=f"api_key={KEY}",
                headers={"content-type": "text/plain"},
            ),
            seen.call("PATCH", f"/llm/profiles/{stub_id}", json={"api_key": KEY + "k" * 4000}),
            seen.call("PATCH", f"/llm/profiles/{stub_id}", json={"api_key": None}),
            seen.call(
                "PATCH", f"/llm/profiles/{stub_id}", json={"model": "m" * 201, "api_key": KEY}
            ),
            seen.call("PATCH", "/llm/profiles/environment", json={"api_key": KEY}),
            seen.call(  # other_origin, which is also logged
                "POST",
                "/llm/profiles",
                json=profile(name="X"),
                headers={"Origin": "http://other.example"},
            ),
            seen.call("PATCH", f"/llm/profiles/{'0' * 32}", json={"api_key": KEY}),
            seen.call("DELETE", f"/llm/profiles/{stub_id}"),  # profile_active
            seen.call("DELETE", "/llm/profiles/environment"),
            seen.call("POST", f"/llm/profiles/{'0' * 32}/activate"),
            seen.call("POST", f"/llm/profiles/{'0' * 32}/test"),
            seen.call("POST", "/llm/test", json={"provider": "openai", "model": "m"}),
            seen.call("POST", "/llm/test", json=profile(name=None, base_url=f"bad {KEY}")),
            seen.call(
                "POST",
                "/llm/test",
                json={"provider": "openai", "model": "m", "profile_id": "0" * 32},
            ),
        ]
        assert all(400 <= response.status_code < 500 for response in refusals)

        # A test that passes and tests that fail, of a listed profile and of unsaved values.
        leaky = seen.call("POST", "/llm/profiles", json=profile(name="Leaky", model="leaky-model"))
        leaky_id = leaky.json()["id"]
        failed = seen.call("POST", f"/llm/profiles/{leaky_id}/test").json()
        unsaved = seen.call(
            "POST",
            "/llm/test",
            json={k: v for k, v in profile(model="leaky-model").items() if k != "name"},
        ).json()
        stored = seen.call(
            "POST",
            "/llm/test",
            json={"provider": "openai", "model": "leaky-model", "profile_id": leaky_id},
        ).json()
        assert seen.call("POST", "/llm/profiles/environment/test").json()["ok"] is True

        # A run that pauses, its resume, and a run that fails, all on profiles with the key.
        paused = seen.stream("/threads/t-1/runs", {"message": "hello"}, debug="true")
        assert paused[-1][0] == "interrupt"
        pending = seen.state("t-1")["pending"]["interrupt_id"]
        resumed = seen.stream(
            "/threads/t-1/resume",
            {"interrupt_id": pending, "decisions": [{"type": "approve"}]},
        )
        assert resumed[-1] == ("end", {"status": "completed"})
        seen.state("t-1")
        assert seen.call("POST", f"/llm/profiles/{leaky_id}/activate").status_code == 200
        # The failing run is watched in full: its debug events carry the failed step.
        broken = seen.stream("/threads/t-2/runs", {"message": "hello"}, debug="true")
        seen.state("t-2")
        in_threads = kept(made.runtime.agent.checkpointer)
        seen.call("GET", "/llm/profiles")
        seen.call("GET", "/tools")
        assert seen.call("POST", "/llm/profiles/environment/activate").status_code == 200
        assert seen.call("DELETE", f"/llm/profiles/{leaky_id}").status_code == 204
        records = client.portal.call(made.store.list_profiles)

    # The provider repeated the key, and what the operator is told has it removed.
    for result in (failed, unsaved, stored):
        assert result["ok"] is False
        assert result["reason"] == f"401: Incorrect API key provided: {REDACTED}. Check {REDACTED}!"
    assert broken[-1][0] == "error"
    assert broken[-1][1]["code"] == "model_error"
    assert REDACTED in broken[-1][1]["message"]
    assert "Incorrect API key provided" in broken[-1][1]["message"]
    # The failed step is in the debug events and in what the checkpointer kept of the thread,
    # in the provider's words with the key taken out.
    debugged = [str(data) for kind, data in broken if kind == "debug"]
    assert any("Incorrect API key provided" in text for text in debugged)
    assert any("Incorrect API key provided" in text for text in in_threads)
    nowhere(in_threads, MARK, ENV_MARK)

    # Nothing returned or streamed, and no thread, holds any part of either key.
    assert len(seen.texts) >= 40
    nowhere(seen.texts, MARK, ENV_MARK)
    # Nor does any log record, with its arguments and its traceback.
    logged = [record.getMessage() for record in caplog.records]
    logged += [caplog.handler.format(record) for record in caplog.records]
    assert any("model error" in line for line in logged)
    nowhere(logged, MARK, ENV_MARK)
    # What is stored is a token that does not hold the key, readable only under the secret.
    (record,) = records
    assert MARK not in record.api_key_encrypted
    assert MARK not in base64.urlsafe_b64decode(record.api_key_encrypted).decode("latin-1")
    assert record.key_hint == "0001"
    assert decrypt(record.api_key_encrypted, made.settings.secret_key) == KEY


def test_the_real_clients_error_reaches_a_run_and_the_log_without_the_key(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The same, through the real client and an endpoint that repeats the key it was sent."""
    stub = LlmStub()
    made = make_llm_app(model="openai:env-model", models=stub.factory)
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = Seen(client, sse_events)
        added = seen.call("POST", "/llm/profiles", json=profile(model="repeat-key")).json()
        seen.call("POST", f"/llm/profiles/{added['id']}/activate")
        tested = seen.call("POST", f"/llm/profiles/{added['id']}/test").json()
        events = seen.stream("/threads/t-1/runs", {"message": "hello"}, debug="true")
        seen.state("t-1")
        in_threads = kept(made.runtime.agent.checkpointer)

    # The stand-in was sent the key and said it back.
    assert {request.key for request in stub.requests} == {KEY}
    assert (tested["ok"], tested["step"]) == (False, "first_message")
    assert f"The key {REDACTED} is not allowed" in tested["reason"]
    kind, error = events[-1]
    assert (kind, error["code"]) == ("error", "model_error")
    assert f"The key {REDACTED} is not allowed" in error["message"]
    nowhere(seen.texts, MARK)
    assert any("is not allowed" in text for text in in_threads)
    nowhere(in_threads, MARK)
    logged = [caplog.handler.format(record) for record in caplog.records]
    assert any("model error" in line and REDACTED in line for line in logged)
    nowhere(logged, MARK)


def test_the_environments_key_is_removed_from_a_failed_run_too(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:env-model")

    class Leaky(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(f"401: Incorrect API key provided: {ENV_KEY}")

    made.models.models["env-model"] = Leaky()
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = Seen(client, sse_events)
        # Watched in full, as a saved profile's failing run is: the environment's model is
        # guarded too, and nothing else in the default suite would notice if it were not.
        events = seen.stream("/threads/t-1/runs", {"message": "hello"}, debug="true")
        tested = seen.call("POST", "/llm/profiles/environment/test").json()
        in_threads = kept(made.runtime.agent.checkpointer)

    assert events[-1][1] == {
        "code": "model_error",
        "message": f"The model request failed: 401: Incorrect API key provided: {REDACTED}",
    }
    assert tested["reason"] == f"401: Incorrect API key provided: {REDACTED}"
    nowhere(seen.texts, ENV_MARK)
    debugged = [str(data) for kind, data in events if kind == "debug"]
    assert any("Incorrect API key provided" in text for text in debugged)
    assert any("Incorrect API key provided" in text for text in in_threads)
    nowhere(in_threads, ENV_MARK)
    nowhere([caplog.handler.format(record) for record in caplog.records], ENV_MARK)


GATEWAY_KEY = "ls-gateway-secret-7777"
GATEWAY_MARK = "gateway-secret"


def test_the_gateways_key_is_removed_from_a_failed_run_and_a_failed_test(
    make_llm_app: Callable[..., LlmApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An environment that reaches its model through the LangSmith gateway, with no provider
    key: the key that travels is the gateway's, so that is the one an endpoint can say back.

    The real client is built for the gateway and sent to the stand-in, which repeats the key
    it was given in its error.
    """
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("LANGSMITH_GATEWAY", "true")
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", GATEWAY_KEY)
    stub = LlmStub()
    made = make_llm_app(model="openai:repeat-key", models=stub.factory)
    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = Seen(client, sse_events)
        listed = seen.call("GET", "/llm/profiles").json()
        events = seen.stream("/threads/t-1/runs", {"message": "hello"}, debug="true")
        tested = seen.call("POST", "/llm/profiles/environment/test").json()
        seen.state("t-1")
        in_threads = kept(made.runtime.agent.checkpointer)

    # The environment's profile is usable, and it was the gateway's key that was sent.
    environment = listed["profiles"][0]
    assert (environment["usable"], environment["key_hint"]) == (True, "7777")
    assert environment["base_url"] == "https://gateway.smith.langchain.com/openai/v1"
    assert {request.key for request in stub.requests} == {GATEWAY_KEY}
    # The endpoint said it back, and everywhere it is taken out.
    kind, error = events[-1]
    assert (kind, error["code"]) == ("error", "model_error")
    assert f"The key {REDACTED} is not allowed" in error["message"]
    assert (tested["ok"], tested["step"]) == (False, "first_message")
    assert f"The key {REDACTED} is not allowed" in tested["reason"]
    debugged = [str(data) for kind, data in events if kind == "debug"]
    assert any("is not allowed" in text for text in debugged)
    assert any("is not allowed" in text for text in in_threads)
    nowhere(seen.texts, GATEWAY_MARK)
    nowhere(in_threads, GATEWAY_MARK)
    logged = [caplog.handler.format(record) for record in caplog.records]
    assert any("model error" in line and REDACTED in line for line in logged)
    nowhere(logged, GATEWAY_MARK)


@pytest.mark.parametrize(
    ("key", "hint"), [("1234567", ""), ("7654321", ""), ("12345678", "5678"), (KEY, "0001")]
)
def test_a_key_shorter_than_eight_characters_shows_no_character(
    make_llm_app: Callable[..., LlmApp], key: str, hint: str
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        added = client.post("/llm/profiles", json=profile(api_key=key))
        listed = client.get("/llm/profiles")
        changed = client.patch(f"/llm/profiles/{added.json()['id']}", json={"api_key": key})
    for response in (added, listed, changed):
        assert key not in response.text
    assert added.json()["key_hint"] == hint
    assert listed.json()["profiles"][1]["key_hint"] == hint
    assert changed.json()["key_hint"] == hint
    (record,) = asyncio.run(made.store.list_profiles())
    assert key not in record.api_key_encrypted
