"""Feature 007, FR-025, SC-008: the thread index returns no key and no agent's address.

The list returns text that comes from threads: how a first message begins and, for a run
that goes on without a reader, the reason it failed. A stored API key appears nowhere beyond
its last four characters (feature 004), and an agent's address nowhere outside the Agents
routes (feature 005). This file drives the routes of feature 007 with a recognizable key and
a recognizable address, through threads that finish, pause, and fail, and searches what they
returned and what the service logged.

It shares its tools with the proofs of features 004 and 005, in ``tests/nowhere.py``.
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests.conftest import A2aApp, LlmApp
from tests.fakes import FailingChatModel
from tests.nowhere import MARK, LeakyModel, Seen, logged, no_address, no_key, profile
from tests.unattended_helpers import Reader, idle

# A port nothing else in the suite uses, so finding it means an address got out.
PORT = 9781
URL = f"http://127.0.0.1:{PORT}"
# What a model's endpoint might answer: an error that repeats an address it was sent.
LEAKY = f"The provider could not take a request that names {URL}/ and gave up."

NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)


def handoff(message: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "handoff_to_helper", "args": {"message": message}}],
    )


def index_routes(seen: Seen, *threads: str) -> dict[str, str]:
    """Read the list and each thread's row; the status of each thread by its id."""
    listed = seen.call("GET", "/threads")
    assert listed.status_code == 200, listed.text
    for thread in threads:
        assert seen.call("GET", f"/threads/{thread}").status_code == 200
    return {row["thread_id"]: row["status"] for row in listed.json()["threads"]}


def test_a_key_is_in_nothing_the_thread_index_returns_or_logs(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:env-model")
    made.models.model("saved-model").script = [NOTIFY]
    made.models.models["leaky-model"] = LeakyModel()

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = Seen(client, sse_events)
        saved = seen.call("POST", "/llm/profiles", json=profile()).json()["id"]
        assert seen.call("POST", f"/llm/profiles/{saved}/activate").status_code == 200

        # A thread that pauses, and one that finishes; the person's own text names the key's
        # mark nowhere, so what is searched for can only have come from the service.
        paused = seen.stream("/threads/k-paused/runs", {"message": "Tell #ops."})
        assert paused[-1][0] == "interrupt"
        seen.stream("/threads/k-done/runs", {"message": "Just answer."})

        # One that fails with a provider's error that repeats the key.
        leaky = seen.call(
            "POST", "/llm/profiles", json=profile(name="Leaky", model="leaky-model")
        ).json()["id"]
        assert seen.call("POST", f"/llm/profiles/{leaky}/activate").status_code == 200
        failed = seen.stream("/threads/k-failed/runs", {"message": "This one fails."})
        assert failed[-1][0] == "error"

        statuses = index_routes(seen, "k-paused", "k-done", "k-failed")
        # A row is read once more after the list settled it, and a thread nobody ran.
        index_routes(seen, "k-failed")
        assert seen.call("GET", "/threads/k-never").status_code == 404

    assert statuses == {
        "k-paused": "needs_decision",
        "k-done": "done",
        # A run started the earlier way: its error went to its own reader.
        "k-failed": "stopped",
    }
    no_key(seen.texts, MARK)
    no_key(logged(caplog), MARK)


def test_an_address_is_in_nothing_the_thread_index_returns_or_logs(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    model = FailingChatModel(
        script=[handoff("Left waiting.", "h1"), handoff("Sent once approved.", "h2")],
        final_text="Handed over.",
        # Its fourth call fails, as a provider might that repeats what it was sent.
        fail_on_call=4,
        error=LEAKY,
    )
    # A saved agent on a recognizable port; a message to it needs a person's approval.
    made = make_a2a_app(model, {}, saved={"helper": {"url": URL}})

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = Seen(client, sse_events)
        waiting = seen.stream("/threads/a-paused/runs", {"message": "Ask the helper."})
        assert waiting[-1][0] == "interrupt"

        proposed = seen.stream("/threads/a-done/runs", {"message": "Ask the helper again."})
        assert proposed[-1][0] == "interrupt"
        body = {
            "interrupt_id": proposed[-1][1]["interrupt_id"],
            "decisions": [{"type": "approve"}],
        }
        approved = seen.stream("/threads/a-done/resume", body)
        assert approved[-1] == ("end", {"status": "completed"})

        # One that fails, with an error that names the agent's address. Its stream is the
        # earlier route's own answer and is not searched here: this file is about what the
        # index returns, and the earlier route sends a model's error with the address in
        # it, which is for ``api/streaming.py`` to take out
        # (``tests/test_agents_address_nowhere.py`` has no such run yet).
        with client.stream(
            "POST", "/threads/a-failed/runs", json={"message": "This one fails."}
        ) as response:
            failed = sse_events(response.iter_lines())
        assert failed[-1][0] == "error"

        statuses = index_routes(seen, "a-paused", "a-done", "a-failed")

    # A run started the earlier way: its error went to its own reader.
    assert statuses == {"a-paused": "needs_decision", "a-done": "done", "a-failed": "stopped"}
    # The helper did receive the approved message, so the address was in use.
    assert len(made.stub_at(URL).record.messages) == 1
    no_address(seen.texts, PORT)
    no_address(logged(caplog), PORT)


# Part 2 (Phase 3): console runs, through the three routes that start, continue, and stop
# them. A failed console run is listed with its reason, the one text of a run the list holds.


def ls_call(call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": "ls", "args": {"path": "/"}}])


def test_a_key_is_in_nothing_a_console_run_returns_or_logs(
    make_llm_app: Callable[..., LlmApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    made = make_llm_app(model="openai:env-model")
    saved_model = made.models.model("saved-model")
    saved_model.script = [NOTIFY, AIMessage(content="Not sent."), ls_call("l1"), ls_call("l2")]
    made.models.models["leaky-model"] = LeakyModel()

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = Seen(client, sse_events)
        saved = seen.call("POST", "/llm/profiles", json=profile()).json()["id"]
        assert seen.call("POST", f"/llm/profiles/{saved}/activate").status_code == 200

        paused = seen.stream("/threads/c-paused/runs/unattended", {"message": "Tell #ops."})
        assert paused[-1][0] == "interrupt"
        body = {"interrupt_id": paused[-1][1]["interrupt_id"], "decisions": [{"type": "reject"}]}
        decided = seen.stream("/threads/c-paused/resume/unattended", body)
        assert decided[-1] == ("end", {"status": "completed"})

        # One that is stopped while the model works.
        saved_model.hold()
        reader = Reader(client, "/threads/c-stopped/runs/unattended", {"message": "List."})
        reader.start()
        with saved_model.held():
            assert seen.call("POST", "/threads/c-stopped/stop").status_code == 202
            seen.call("GET", "/threads/c-stopped")
        stopped = reader.finish()
        seen.texts.append(repr(stopped))
        assert stopped[-1] == ("end", {"status": "stopped"})
        idle(client, "c-stopped")
        assert seen.call("POST", "/threads/c-stopped/stop").status_code == 409

        # One that fails with a provider's error that repeats the key.
        leaky = seen.call(
            "POST", "/llm/profiles", json=profile(name="Leaky", model="leaky-model")
        ).json()["id"]
        assert seen.call("POST", f"/llm/profiles/{leaky}/activate").status_code == 200
        failed = seen.stream("/threads/c-failed/runs/unattended", {"message": "This one fails."})
        assert failed[-1][0] == "error"

        statuses = index_routes(seen, "c-paused", "c-stopped", "c-failed")
        failed_row = seen.call("GET", "/threads/c-failed").json()

    assert statuses == {"c-paused": "done", "c-stopped": "stopped", "c-failed": "failed"}
    # The reason is there, with the key taken out of it.
    assert "Incorrect API key provided" in failed_row["error"]["message"]
    no_key(seen.texts, MARK)
    no_key(logged(caplog), MARK)


def test_an_address_is_in_nothing_a_console_run_returns_or_logs(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    model = FailingChatModel(
        script=[
            handoff("Sent once approved.", "h1"),
            AIMessage(content="Handed over."),
            handoff("Left waiting.", "h2"),
        ],
        final_text="Handed over.",
        fail_on_call=4,
        error=LEAKY,
    )
    made = make_a2a_app(model, {}, saved={"helper": {"url": URL}})

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = Seen(client, sse_events)
        proposed = seen.stream("/threads/c-done/runs/unattended", {"message": "Ask the helper."})
        assert proposed[-1][0] == "interrupt"
        body = {
            "interrupt_id": proposed[-1][1]["interrupt_id"],
            "decisions": [{"type": "approve"}],
        }
        approved = seen.stream("/threads/c-done/resume/unattended", body)
        assert approved[-1] == ("end", {"status": "completed"})
        waiting = seen.stream("/threads/c-paused/runs/unattended", {"message": "Again."})
        assert waiting[-1][0] == "interrupt"
        assert seen.call("POST", "/threads/c-paused/stop").status_code == 409

        # One that fails, with an error that names the agent's address: the list gives a
        # failed console run's reason, the one text of a run it holds.
        failed = seen.stream("/threads/c-failed/runs/unattended", {"message": "This one fails."})
        assert failed[-1][0] == "error"

        statuses = index_routes(seen, "c-done", "c-paused", "c-failed")
        failed_row = seen.call("GET", "/threads/c-failed").json()

    assert statuses == {"c-done": "done", "c-paused": "needs_decision", "c-failed": "failed"}
    # The reason is given, to the run's reader and in the list, and it is the one that named
    # the address, without the address: a console run's error is cleaned where it passes.
    assert failed[-1][1]["code"] == "model_error"
    assert "could not take a request that names" in failed[-1][1]["message"]
    assert failed_row["error"]["message"] == failed[-1][1]["message"]
    assert len(made.stub_at(URL).record.messages) == 1
    no_address(seen.texts, PORT)
    no_address(logged(caplog), PORT)
