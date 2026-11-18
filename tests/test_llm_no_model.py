"""004 FR-016, FR-016a, SC-013: the service runs without a usable model (research R8).

It starts, the console can reach it, and a run ends at once with ``no_usable_model``. Nothing
falls back to another profile, and no background run starts while that is so.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from freyja_agent.llm.providers import ModelSpec
from freyja_agent.llm.secrets import encrypt
from freyja_agent.llm.store import InMemoryLlmProfileStore, LlmProfileRecord
from tests.conftest import TEST_SECRET, A2aApp, LlmApp
from tests.fakes import LabelledChatModel, ProfileModels, ScriptedChatModel
from tests.test_a2a_pending import (
    TASK,
    go_pending,
    handoff,
    pending_app,
    record,
    stub,
    tick,
    wait_for,
)

Events = list[tuple[str, Any]]
KEY = "sk-recognizable-key-0001"
ENV_KEY = "sk-environment-key-9f2c"
NO_USABLE_MODEL = {
    "code": "no_usable_model",
    "message": (
        "No usable model is active. Activate a usable LLM profile, then send the message again."
    ),
}
ENVIRONMENT = {"id": "environment", "name": "Environment", "source": "environment"}
OTHER_SECRET = SecretStr(Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment's provider has its key, unless a test takes it away."""
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)


class Unbuildable(ProfileModels):
    """A factory that cannot build the model called ``broken``, and says the key in its error."""

    def __call__(self, spec: ModelSpec) -> LabelledChatModel:
        if spec.model == "broken":
            self.built.append(spec)
            raise ValueError(f"no such model for key {ENV_KEY}")
        return super().__call__(spec)


def run(client: TestClient, thread: str, sse_events: Any, message: str = "hello") -> Events:
    with client.stream("POST", f"/threads/{thread}/runs", json={"message": message}) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def listing(client: TestClient) -> dict[str, Any]:
    response = client.get("/llm/profiles")
    assert response.status_code == 200, response.text
    return response.json()


def unreadable(store: InMemoryLlmProfileStore, name: str = "Unreadable") -> LlmProfileRecord:
    """A saved profile whose key was stored under a secret the service no longer has."""
    return asyncio.run(
        store.create_profile(
            name=name,
            provider="openai",
            model="saved-model",
            base_url="http://127.0.0.1:9997/v1",
            api_key_encrypted=encrypt(KEY, OTHER_SECRET),
            key_hint=KEY[-4:],
            responses_api=False,
        )
    )


def unreadable_and_active(make_llm_app: Callable[..., LlmApp]) -> tuple[LlmApp, LlmProfileRecord]:
    store = InMemoryLlmProfileStore()
    saved = unreadable(store)
    asyncio.run(store.activate(saved.id))
    return make_llm_app(model="openai:env-model", store=store), saved


def refused(events: Events, profile: dict[str, str]) -> None:
    """The whole stream of a run that was turned away: its start, then the error."""
    assert [kind for kind, _ in events] == ["start", "error"]
    assert events[0][1]["profile"] == profile
    assert events[1][1] == NO_USABLE_MODEL


def test_the_service_starts_when_the_environment_names_no_key(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        body = listing(client)
        events = run(client, "t-1", sse_events)
        # Nothing was added to the thread: it does not exist.
        assert client.get("/threads/t-1/state").status_code == 404

    (environment,) = body["profiles"]
    assert (environment["active"], environment["usable"]) == (True, False)
    assert environment["unusable_reason"] == "not_configured"
    assert body["usable_model"] is False
    refused(events, ENVIRONMENT)
    # No model was asked anything.
    assert all(model.calls == 0 for model in made.models.models.values())


def test_the_service_starts_when_the_environments_model_cannot_be_built(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:broken", models=Unbuildable())
    with TestClient(made.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        (environment,) = listing(client)["profiles"]
        events = run(client, "t-1", sse_events)

    assert (environment["usable"], environment["unusable_reason"]) == (False, "cannot_build")
    refused(events, ENVIRONMENT)
    assert ENV_KEY not in str(events)


def test_a_saved_active_profile_that_cannot_be_built_is_listed_as_not_usable(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    """FR-016: the list says no usable model is active for as long as runs are turned away.

    No values of the three providers are known to fail here; the client stands for one that
    would.
    """
    made = make_llm_app(model="openai:env-model", models=Unbuildable())
    with TestClient(made.app) as client:
        added = client.post(
            "/llm/profiles",
            json={"name": "Broken", "provider": "openai", "model": "broken", "api_key": KEY},
        ).json()
        other = client.post(
            "/llm/profiles",
            json={"name": "Other", "provider": "openai", "model": "broken", "api_key": KEY},
        ).json()
        # A key that can be read is all that is known before a model is built from it.
        assert (added["usable"], added["unusable_reason"]) == (True, None)
        assert client.post(f"/llm/profiles/{added['id']}/activate").status_code == 200
        body = listing(client)
        events = run(client, "t-1", sse_events)
        # Back on a model that can be built, nothing is held against the profile.
        assert client.post("/llm/profiles/environment/activate").status_code == 200
        back = listing(client)

    by_name = {profile["name"]: profile for profile in body["profiles"]}
    assert (by_name["Broken"]["active"], by_name["Broken"]["usable"]) == (True, False)
    assert by_name["Broken"]["unusable_reason"] == "cannot_build"
    assert body["usable_model"] is False
    # Only the profile this instance tried to build is known not to build.
    assert (by_name["Other"]["usable"], by_name["Other"]["unusable_reason"]) == (True, None)
    refused(events, {"id": added["id"], "name": "Broken", "source": "saved"})
    assert back["usable_model"] is True
    assert {p["name"]: p["usable"] for p in back["profiles"]}["Broken"] is True
    assert other["id"] != added["id"]


def test_the_service_starts_when_the_active_profiles_key_cannot_be_read(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made, saved = unreadable_and_active(make_llm_app)
    with TestClient(made.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        body = listing(client)
        events = run(client, "t-1", sse_events)
        assert client.get("/threads/t-1/state").status_code == 404
        applied = made.runtime.applied_model()

    environment, profile = body["profiles"]
    assert (profile["active"], profile["usable"], profile["unusable_reason"]) == (
        True,
        False,
        "key_unreadable",
    )
    assert body["usable_model"] is False
    refused(events, {"id": saved.id, "name": "Unreadable", "source": "saved"})
    # The service did not switch to the environment's profile, usable as it is.
    assert (environment["active"], environment["usable"]) == (False, True)
    assert body["active_id"] == saved.id
    assert asyncio.run(made.store.state()).active_profile_id == saved.id
    assert (applied.profile.name, applied.usable) == ("Unreadable", False)
    assert all(model.calls == 0 for model in made.models.models.values())


def test_a_profile_saved_under_a_secret_the_service_no_longer_has_starts_it_too(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    store = InMemoryLlmProfileStore()
    saved = asyncio.run(
        store.create_profile(
            name="Stub",
            provider="openai",
            model="saved-model",
            api_key_encrypted=encrypt(KEY, SecretStr(TEST_SECRET)),
            key_hint=KEY[-4:],
        )
    )
    asyncio.run(store.activate(saved.id))
    made = make_llm_app(model="openai:env-model", store=store, secret=None)
    with TestClient(made.app) as client:
        (_, profile) = listing(client)["profiles"]
        events = run(client, "t-1", sse_events)
    assert profile["unusable_reason"] == "secret_not_configured"
    refused(events, {"id": saved.id, "name": "Stub", "source": "saved"})


def test_a_refused_run_frees_the_thread_and_the_message_can_be_sent_again(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made, saved = unreadable_and_active(make_llm_app)
    with TestClient(made.app) as client:
        refused(
            run(client, "t-1", sse_events),
            {"id": saved.id, "name": "Unreadable", "source": "saved"},
        )
        # The lease was released: the same thread is turned away again, not reported busy.
        refused(
            run(client, "t-1", sse_events),
            {"id": saved.id, "name": "Unreadable", "source": "saved"},
        )

        # A usable profile is activated, and the same message goes through with no restart.
        activated = client.post("/llm/profiles/environment/activate")
        assert activated.status_code == 200
        assert activated.json()["usable_model"] is True
        events = run(client, "t-1", sse_events)
        messages = client.get("/threads/t-1/state").json()["messages"]

    assert events[0][1]["profile"] == ENVIRONMENT
    assert events[-1] == ("end", {"status": "completed"})
    # The thread holds the message once: the refused attempts added nothing.
    assert [(m["type"], m["content"]) for m in messages] == [
        ("human", "hello"),
        ("ai", "Answered by env-model."),
    ]
    assert len(made.runtimes) == 1


def test_a_resume_that_is_refused_leaves_the_proposal_to_be_decided_later(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:env-model")
    made.models.model("env-model").script = [
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

    def resume(client: TestClient) -> Events:
        pending = client.get("/threads/t-1/state").json()["pending"]["interrupt_id"]
        body = {"interrupt_id": pending, "decisions": [{"type": "approve"}]}
        with client.stream("POST", "/threads/t-1/resume", json=body) as response:
            assert response.status_code == 200, response.read()
            return sse_events(response.iter_lines())

    with TestClient(made.app) as client:
        assert run(client, "t-1", sse_events)[-1][0] == "interrupt"
        before = client.get("/threads/t-1/state").json()

        # The active profile becomes one whose key cannot be read.
        saved = unreadable(made.store)
        client.portal.call(made.store.activate, saved.id)
        events = resume(client)
        refused(events, {"id": saved.id, "name": "Unreadable", "source": "saved"})
        after = client.get("/threads/t-1/state").json()
        assert after["pending"] == before["pending"]
        assert after["messages"] == before["messages"]

        # Once a usable profile is active the same proposal is decided.
        assert client.post("/llm/profiles/environment/activate").status_code == 200
        decided = resume(client)

    assert decided[0][1]["profile"] == ENVIRONMENT
    assert decided[-1] == ("end", {"status": "completed"})


def test_no_background_run_starts_and_no_attempt_is_used_while_no_model_is_usable(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    agent = stub()
    environment = ScriptedChatModel(script=[handoff("hold: report")], final_text="Delivered.")
    profiles = InMemoryLlmProfileStore()
    made = pending_app(
        make_a2a_app,
        environment,
        agent,
        llm_profiles=profiles,
        model_factory=ProfileModels(),
        secret_key=TEST_SECRET,
    )
    with TestClient(made.app) as client:
        go_pending(client, "b-1", sse_events)
        saved = unreadable(profiles)
        client.portal.call(profiles.activate, saved.id)
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        calls = environment.calls
        before = client.get("/threads/b-1/state").json()["messages"]

        # The outcome is ready, round after round, and nothing is done with it.
        with caplog.at_level(logging.INFO, logger="freyja_agent"):
            caplog.clear()
            for _ in range(3):
                tick(client, made.runtime)
            held = [r.getMessage() for r in caplog.records]
        # No background run was started, so none was refused either; the worker says once why.
        assert not any("Background run" in line or "refused" in line for line in held)
        assert len([line for line in held if "no background run starts" in line]) == 1
        waiting = record(client, made, TASK)
        assert waiting.outcome_ready is True
        assert waiting.delivered_at is None
        assert (waiting.followup_attempts, waiting.followup_next_at) == (0, None)
        assert environment.calls == calls
        assert client.get("/threads/b-1/state").json()["messages"] == before

        # A usable profile is activated, and the next round delivers it.
        assert client.post("/llm/profiles/environment/activate").status_code == 200
        tick(client, made.runtime)
        delivered = record(client, made, TASK)
        after = client.get("/threads/b-1/state").json()

    assert delivered.delivered_at is not None
    assert delivered.followup_attempts == 0
    assert environment.calls == calls + 1
    assert after["messages"][-1]["content"] == "Delivered."
    assert after["agent_tasks"][0]["outcome_delivered"] is True


def test_what_is_not_usable_is_logged_at_startup_and_each_refused_run_is_logged(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    store = InMemoryLlmProfileStore()
    active = unreadable(store)
    unreadable(store, "Spare")
    asyncio.run(
        store.create_profile(
            name="Fine",
            provider="openai",
            model="other-model",
            api_key_encrypted=encrypt(KEY, SecretStr(TEST_SECRET)),
            key_hint=KEY[-4:],
        )
    )
    asyncio.run(store.activate(active.id))
    made = make_llm_app(model="openai:env-model", store=store)
    with caplog.at_level(logging.INFO, logger="freyja_agent"), TestClient(made.app) as client:
        startup = [r.getMessage() for r in caplog.records if "not usable" in r.getMessage()]
        caplog.clear()
        run(client, "t-1", sse_events)
        during = [r.getMessage() for r in caplog.records]

    # Each profile that is not usable, by name, with the reason; the usable ones are not named.
    assert len(startup) == 2
    assert any("Unreadable" in line and active.id in line for line in startup)
    assert any("Spare" in line for line in startup)
    assert all("cannot be read" in line for line in startup)
    assert not any("Fine" in line or "Environment" in line for line in startup)
    (refusal,) = [line for line in during if "no usable model" in line]
    assert "t-1" in refusal and "Unreadable" in refusal
    # A refused run is not said to use a profile, and no line holds a key.
    assert not any("uses LLM profile" in line for line in during)
    for line in [*startup, *during]:
        assert KEY not in line and ENV_KEY not in line


def test_an_environment_that_is_not_usable_is_logged_at_startup_without_its_key(
    make_llm_app: Callable[..., LlmApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:broken", models=Unbuildable())
    with caplog.at_level(logging.INFO, logger="freyja_agent"), TestClient(made.app):
        logged = [r.getMessage() for r in caplog.records]
    (line,) = [line for line in logged if "not usable" in line]
    assert "Environment" in line and "no model can be built" in line
    assert all(ENV_KEY not in line for line in logged)
