"""004 FR-011, FR-013, FR-014, FR-024a: the model a run uses follows the active LLM profile.

The apps are assembled the production way. The model factory returns a labelled scripted model
per model id, so a run's reply and the factory's record say which profile it used (R18). Two
apps sharing one profile store stand for two service instances on one database (R6).
"""

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.llm.secrets import encrypt
from freyja_agent.llm.store import InMemoryLlmProfileStore, LlmProfileRecord, LlmState
from tests.conftest import TEST_SECRET, A2aApp, LlmApp, McpApp
from tests.fakes import ProfileModels, ScriptedChatModel
from tests.test_a2a_pending import TASK, go_pending, handoff, pending_app, stub, tick, wait_for

Events = list[tuple[str, Any]]
ENVIRONMENT = {"id": "environment", "name": "Environment", "source": "environment"}
KEY = "sk-recognizable-key-0001"


@pytest.fixture(autouse=True)
def environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment's provider has its key, so the environment's profile is usable."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")


def run(client: TestClient, thread: str, sse_events: Any, message: str = "hello") -> Events:
    with client.stream("POST", f"/threads/{thread}/runs", json={"message": message}) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def profile_of(events: Events) -> dict[str, str]:
    kind, start = events[0]
    assert kind == "start"
    return start["profile"]


def reply(client: TestClient, thread: str) -> str:
    return client.get(f"/threads/{thread}/state").json()["messages"][-1]["content"]


def save(made: LlmApp, name: str, model: str, **values: Any) -> LlmProfileRecord:
    """Save a profile straight into the store, with its key stored as the service stores it."""
    given: dict[str, Any] = {
        "provider": "openai",
        "base_url": "http://127.0.0.1:9997/v1",
        "api_key_encrypted": encrypt(KEY, made.settings.secret_key),
        "key_hint": KEY[-4:],
        "responses_api": False,
    }
    return asyncio.run(made.store.create_profile(name=name, model=model, **{**given, **values}))


def activate(made: LlmApp, profile_id: str | None) -> LlmState:
    return asyncio.run(made.store.activate(profile_id))


def profile_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "uses LLM profile" in r.getMessage()]


# Part 1 (T023): the mechanics, with no route yet.


def test_with_no_saved_profile_a_run_names_the_environments_profile(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:env-model")
    with (
        caplog.at_level(logging.INFO, logger="freyja_agent.api.streaming"),
        TestClient(made.app) as client,
    ):
        events = run(client, "t-1", sse_events)
        assert reply(client, "t-1") == "Answered by env-model."

    assert profile_of(events) == ENVIRONMENT
    assert events[-1][0] == "end"
    (line,) = profile_lines(caplog)
    assert "run " in line
    assert "thread t-1" in line
    assert "Environment (environment, environment)" in line
    # The environment's model is built from the setting, with no key or address of its own.
    (spec,) = made.models.built
    assert spec.name == "openai:env-model"
    assert spec.api_key is None
    assert spec.base_url is None


def test_the_model_is_built_once_while_the_active_choice_does_not_move(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        for thread in ("t-1", "t-2", "t-1"):
            assert profile_of(run(client, thread, sse_events)) == ENVIRONMENT
    assert len(made.models.built) == 1
    assert made.models.model("env-model").calls == 3


def test_a_profile_activated_in_the_store_is_used_by_the_next_run(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:env-model")
    saved = save(made, "Stub", "saved-model")
    with (
        caplog.at_level(logging.INFO, logger="freyja_agent"),
        TestClient(made.app) as client,
    ):
        assert profile_of(run(client, "t-1", sse_events)) == ENVIRONMENT
        activate(made, saved.id)
        events = run(client, "t-1", sse_events, "and again")
        assert profile_of(events) == {"id": saved.id, "name": "Stub", "source": "saved"}
        assert reply(client, "t-1") == "Answered by saved-model."
        # The thread's earlier messages are still there, and the new model received them.
        seen = made.models.model("saved-model").seen[-1]
        assert [m.content for m in seen if m.type == "human"] == ["hello", "and again"]

        activate(made, None)
        assert profile_of(run(client, "t-1", sse_events)) == ENVIRONMENT

    # The saved profile's model was built from its own values, with its key read back.
    environment, stub, environment_again = made.models.built
    assert (stub.provider, stub.model) == ("openai", "saved-model")
    assert stub.base_url == "http://127.0.0.1:9997/v1"
    assert stub.api_key == KEY
    assert stub.responses_api is False
    assert environment == environment_again
    # No log line holds the key, and each run has one line naming its profile.
    assert all(KEY not in record.getMessage() for record in caplog.records)
    assert [
        line.split("uses LLM profile ")[1].split(" (")[0] for line in profile_lines(caplog)
    ] == [
        "Environment",
        "Stub",
        "Environment",
    ]


def test_a_second_runtime_on_the_same_store_reads_the_same_state(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    first = make_llm_app(model="openai:env-model")
    second = make_llm_app(
        model="openai:env-model",
        store=first.store,
        models=first.models,
        instance_id="instance-b",
    )
    saved = save(first, "Stub", "saved-model")
    with TestClient(first.app) as a, TestClient(second.app) as b:
        assert profile_of(run(a, "t-a", sse_events)) == ENVIRONMENT
        assert profile_of(run(b, "t-b", sse_events)) == ENVIRONMENT

        # A change made on the database reaches each instance at its next run.
        activate(first, saved.id)
        assert profile_of(run(b, "t-b", sse_events))["name"] == "Stub"
        assert profile_of(run(a, "t-a", sse_events))["name"] == "Stub"

        # An edit of the active profile moves the revision, so both rebuild once more.
        asyncio.run(first.store.update_profile(saved.id, name="Renamed", model="newer-model"))
        assert profile_of(run(a, "t-a", sse_events))["name"] == "Renamed"
        assert profile_of(run(b, "t-b", sse_events))["name"] == "Renamed"
        assert reply(b, "t-b") == "Answered by newer-model."


def test_a_model_handed_to_the_runtime_stands_for_the_environments(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(final_text="From the given model."), {})
    with TestClient(made.app) as client:
        events = run(client, "t-1", sse_events)
        assert profile_of(events) == ENVIRONMENT
        assert reply(client, "t-1") == "From the given model."


class FlakyStore(InMemoryLlmProfileStore):
    """A store whose state cannot be read while ``down`` is set."""

    down = False

    async def state(self) -> LlmState:
        if self.down:
            raise ConnectionError("the database is gone")
        return await super().state()


def test_a_run_whose_profile_cannot_be_read_ends_with_an_error_and_frees_the_thread(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    store = FlakyStore()
    made = make_llm_app(model="openai:env-model", store=store)
    with TestClient(made.app) as client:
        store.down = True
        events = run(client, "t-1", sse_events)
        assert [kind for kind, _ in events] == ["start", "error"]
        assert profile_of(events) == ENVIRONMENT
        assert events[1][1]["code"] == "internal"
        assert "database" not in events[1][1]["message"]
        # No model was asked, and nothing was added to the thread.
        assert made.models.model("env-model").calls == 0
        assert client.get("/threads/t-1/state").status_code == 404

        # The lease was released: the same thread runs again once the store answers.
        store.down = False
        assert run(client, "t-1", sse_events)[-1][0] == "end"


# Part 2 (T039): a switch made through the API.

STUB = {
    "name": "Stub",
    "provider": "openai",
    "model": "saved-model",
    "base_url": "http://127.0.0.1:9997/v1",
    "api_key": KEY,
    "responses_api": False,
}


def add(client: TestClient, **values: Any) -> str:
    """Add a profile through the API and return its id."""
    response = client.post("/llm/profiles", json={**STUB, **values})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def switch_to(client: TestClient, profile_id: str) -> None:
    response = client.post(f"/llm/profiles/{profile_id}/activate")
    assert response.status_code == 200, response.text
    assert response.json()["active_id"] == profile_id


def humans(made: LlmApp, label: str) -> list[str]:
    """What the user said, as the model for this label last received it."""
    return [m.content for m in made.models.model(label).seen[-1] if m.type == "human"]


def test_after_an_activation_the_next_run_uses_the_new_profile_with_no_restart(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        assert profile_of(run(client, "t-1", sse_events)) == ENVIRONMENT
        stub_id = add(client)
        # Adding a profile does not change the one in use.
        assert profile_of(run(client, "t-1", sse_events, "still the first")) == ENVIRONMENT

        switch_to(client, stub_id)
        events = run(client, "t-1", sse_events, "now the second")
        assert profile_of(events) == {"id": stub_id, "name": "Stub", "source": "saved"}
        assert events[-1] == ("end", {"status": "completed"})
        assert reply(client, "t-1") == "Answered by saved-model."
        # The thread's history is intact and went to the new model.
        assert humans(made, "saved-model") == ["hello", "still the first", "now the second"]
        # One runtime served all of it.
        assert len(made.runtimes) == 1


def test_activating_the_environment_returns_to_its_model(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:env-model")
    with TestClient(made.app) as client:
        switch_to(client, add(client))
        assert profile_of(run(client, "t-1", sse_events))["name"] == "Stub"
        switch_to(client, "environment")
        assert profile_of(run(client, "t-1", sse_events, "back again")) == ENVIRONMENT
        assert reply(client, "t-1") == "Answered by env-model."
        assert humans(made, "env-model") == ["hello", "back again"]


def test_a_run_in_progress_ends_on_the_model_it_started_with(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:env-model")
    first = made.models.model("env-model")
    streamed: dict[str, Events] = {}

    with TestClient(made.app) as client:
        stub_id = add(client)
        first.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "t-1", sse_events))
        )
        running.start()
        with first.held():
            # The run has reached its model and waits there while the profile changes.
            switch_to(client, stub_id)
            assert made.runtime.applied_model().profile.name == "Stub"
        running.join(10)
        assert not running.is_alive()

        events = streamed["events"]
        assert profile_of(events) == ENVIRONMENT
        assert events[-1] == ("end", {"status": "completed"})
        assert reply(client, "t-1") == "Answered by env-model."
        assert made.models.model("saved-model").calls == 0

        # The next run on the same thread uses the new profile, with the history intact.
        after = run(client, "t-1", sse_events, "and again")
        assert profile_of(after)["name"] == "Stub"
        assert reply(client, "t-1") == "Answered by saved-model."
        assert humans(made, "saved-model") == ["hello", "and again"]


def to_the_subagent(what: str) -> AIMessage:
    """The orchestrator handing ``what`` to its general-purpose subagent."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": "s1",
                "name": "task",
                "args": {"description": what, "subagent_type": "general-purpose"},
            }
        ],
    )


def test_a_subagents_work_stays_on_the_model_its_run_started_with(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    """FR-011, FR-013: the orchestrator and its subagent use the one active profile.

    The profile changes while the run waits in its first step. The subagent is called only
    after that, and is still asked through the model the run started with.
    """
    made = make_llm_app(model="openai:env-model")
    first = made.models.model("env-model")
    first.script = [to_the_subagent("Look into the nightly report.")]
    second = made.models.model("saved-model")
    second.script = [to_the_subagent("Look into the weekly report.")]
    streamed: dict[str, Events] = {}

    with TestClient(made.app) as client:
        stub_id = add(client)
        first.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "t-1", sse_events))
        )
        running.start()
        with first.held():
            switch_to(client, stub_id)
            assert made.runtime.applied_model().profile.name == "Stub"
        running.join(10)
        assert not running.is_alive()

        events = streamed["events"]
        assert profile_of(events) == ENVIRONMENT
        assert events[-1] == ("end", {"status": "completed"})
        # Three calls: the hand-off, the subagent's own, and the reply. All on the first model.
        assert first.calls == 3
        assert second.calls == 0
        asked_of_subagent = [m.content for m in first.seen[1] if m.type == "human"]
        assert asked_of_subagent == ["Look into the nightly report."]
        state = client.get("/threads/t-1/state").json()
        handed_back = [m for m in state["messages"] if m["type"] == "tool" and m["name"] == "task"]
        assert [m["content"] for m in handed_back] == ["Answered by env-model."]

        # The next run's subagent is asked through the newly active profile's model.
        after = run(client, "t-2", sse_events)
        assert profile_of(after)["name"] == "Stub"
        assert after[-1] == ("end", {"status": "completed"})
        assert second.calls == 3
        assert first.calls == 3
        assert [m.content for m in second.seen[1] if m.type == "human"] == [
            "Look into the weekly report."
        ]


def test_a_paused_run_resumed_after_an_activation_continues_on_the_new_profile(
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
    with TestClient(made.app) as client:
        stub_id = add(client)
        paused = run(client, "t-1", sse_events)
        assert profile_of(paused) == ENVIRONMENT
        assert paused[-1][0] == "interrupt"

        switch_to(client, stub_id)
        pending = client.get("/threads/t-1/state").json()["pending"]["interrupt_id"]
        body = {"interrupt_id": pending, "decisions": [{"type": "approve"}]}
        with client.stream("POST", "/threads/t-1/resume", json=body) as response:
            assert response.status_code == 200, response.read()
            resumed = sse_events(response.iter_lines())

        assert profile_of(resumed) == {"id": stub_id, "name": "Stub", "source": "saved"}
        assert resumed[-1] == ("end", {"status": "completed"})
        assert reply(client, "t-1") == "Answered by saved-model."
        # The new model continued the same conversation: it received the action's result.
        seen = made.models.model("saved-model").seen[-1]
        assert [m.name for m in seen if m.type == "tool"] == ["send_notification"]
        assert made.models.model("env-model").calls == 1


def test_a_background_run_started_after_an_activation_uses_the_new_profile(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    agent = stub()
    environment = ScriptedChatModel(script=[handoff("hold: report")], final_text="Handed off.")
    models = ProfileModels()
    made = pending_app(
        make_a2a_app,
        environment,
        agent,
        llm_profiles=InMemoryLlmProfileStore(),
        model_factory=models,
        secret_key=TEST_SECRET,
    )
    with (
        caplog.at_level(logging.INFO, logger="freyja_agent.api.streaming"),
        TestClient(made.app) as client,
    ):
        go_pending(client, "b-1", sse_events)
        switch_to(client, add(client))
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        tick(client, made.runtime)  # the worker delivers the outcome in a background run
        assert reply(client, "b-1") == "Answered by saved-model."

    assert environment.calls == 2
    assert models.model("saved-model").calls == 1
    user, background = profile_lines(caplog)
    assert "(run)" in user and "Environment" in user
    assert "(background)" in background and "Stub" in background


def test_a_restart_keeps_the_saved_active_profile_whatever_the_environment_names(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    before = make_llm_app(model="openai:env-model")
    with TestClient(before.app) as client:
        stub_id = add(client)
        switch_to(client, stub_id)

    # The same database, a new process, and another model in the environment.
    after = make_llm_app(model="openai:another-env-model", store=before.store)
    with TestClient(after.app) as client:
        listed = client.get("/llm/profiles").json()
        assert listed["active_id"] == stub_id
        assert [(p["name"], p["model"], p["active"]) for p in listed["profiles"]] == [
            ("Environment", "another-env-model", False),
            ("Stub", "saved-model", True),
        ]
        events = run(client, "t-1", sse_events)
        assert profile_of(events) == {"id": stub_id, "name": "Stub", "source": "saved"}
        assert reply(client, "t-1") == "Answered by saved-model."
    # The environment's model was never asked anything.
    assert after.models.model("another-env-model").calls == 0


def test_an_activation_through_one_instance_reaches_another_at_its_next_run(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    first = make_llm_app(model="openai:env-model")
    second = make_llm_app(
        model="openai:env-model",
        store=first.store,
        models=first.models,
        instance_id="instance-b",
    )
    with TestClient(first.app) as a, TestClient(second.app) as b:
        assert profile_of(run(b, "t-b", sse_events)) == ENVIRONMENT
        stub_id = add(a)
        switch_to(a, stub_id)
        # The second instance was told nothing: it reads the choice when its run starts.
        assert first.runtime.applied_model().profile.name == "Stub"
        assert second.runtime.applied_model().profile.name == "Environment"
        events = run(b, "t-b", sse_events, "and again")
        assert profile_of(events) == {"id": stub_id, "name": "Stub", "source": "saved"}
        assert reply(b, "t-b") == "Answered by saved-model."
        assert b.get("/llm/profiles").json()["active_id"] == stub_id

        switch_to(b, "environment")
        assert profile_of(run(a, "t-a", sse_events)) == ENVIRONMENT


# Part 3 (T057): editing and deleting while runs go on.


def edit(client: TestClient, profile_id: str, **values: Any) -> None:
    response = client.patch(f"/llm/profiles/{profile_id}", json=values)
    assert response.status_code == 200, response.text


def test_an_edit_of_the_active_profile_is_used_by_the_next_run_everywhere(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    first = make_llm_app(model="openai:env-model")
    second = make_llm_app(
        model="openai:env-model",
        store=first.store,
        models=first.models,
        instance_id="instance-b",
    )
    with TestClient(first.app) as a, TestClient(second.app) as b:
        stub_id = add(a)
        switch_to(a, stub_id)
        assert reply_after(a, "t-a", sse_events) == "Answered by saved-model."
        assert reply_after(b, "t-b", sse_events) == "Answered by saved-model."

        edit(a, stub_id, model="newer-model", name="Renamed")

        # No restart: the instance that took the edit, and the one that was told nothing.
        after = run(a, "t-a", sse_events, "and again")
        assert profile_of(after) == {"id": stub_id, "name": "Renamed", "source": "saved"}
        assert reply(a, "t-a") == "Answered by newer-model."
        assert reply_after(b, "t-b", sse_events, "and again") == "Answered by newer-model."
        assert humans(first, "newer-model") == ["hello", "and again"]
        assert (len(first.runtimes), len(second.runtimes)) == (1, 1)


def reply_after(client: TestClient, thread: str, sse_events: Any, message: str = "hello") -> str:
    assert run(client, thread, sse_events, message)[-1] == ("end", {"status": "completed"})
    return reply(client, thread)


def test_a_run_in_progress_during_an_edit_finishes_on_the_values_it_started_with(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:env-model")
    saved = made.models.model("saved-model")
    streamed: dict[str, Events] = {}
    with TestClient(made.app) as client:
        stub_id = add(client)
        switch_to(client, stub_id)
        saved.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "t-1", sse_events))
        )
        running.start()
        with saved.held():
            edit(client, stub_id, model="newer-model")
        running.join(10)
        assert not running.is_alive()

        assert streamed["events"][-1] == ("end", {"status": "completed"})
        assert reply(client, "t-1") == "Answered by saved-model."
        assert made.models.model("newer-model").calls == 0
        assert reply_after(client, "t-1", sse_events, "and again") == "Answered by newer-model."


def test_a_run_in_progress_on_a_profile_that_is_then_deleted_finishes_normally(
    make_llm_app: Callable[..., LlmApp], sse_events: Any
) -> None:
    made = make_llm_app(model="openai:env-model")
    saved = made.models.model("saved-model")
    streamed: dict[str, Events] = {}
    with TestClient(made.app) as client:
        stub_id = add(client)
        switch_to(client, stub_id)
        saved.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "t-1", sse_events))
        )
        running.start()
        with saved.held():
            # The deletion does not wait for the run.
            switch_to(client, "environment")
            assert client.delete(f"/llm/profiles/{stub_id}").status_code == 204
            assert [p["name"] for p in client.get("/llm/profiles").json()["profiles"]] == [
                "Environment"
            ]
        running.join(10)
        assert not running.is_alive()

        events = streamed["events"]
        assert profile_of(events) == {"id": stub_id, "name": "Stub", "source": "saved"}
        assert events[-1] == ("end", {"status": "completed"})
        assert reply(client, "t-1") == "Answered by saved-model."
        assert profile_of(run(client, "t-1", sse_events, "and again")) == ENVIRONMENT
