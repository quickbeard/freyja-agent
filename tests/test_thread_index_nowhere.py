"""Feature 007, FR-025, SC-008: the thread index returns no key and no agent's address.

The list returns text that comes from threads: how a first message begins and, for a run
that goes on without a reader, the reason it failed. A stored API key appears nowhere beyond
its last four characters (feature 004), and an agent's address nowhere outside the Agents
routes (feature 005). This file drives the routes of feature 007 with a recognizable key and
a recognizable address, through threads that finish, pause, and fail, and searches what they
returned and what the service logged.

It reuses the helpers of ``tests/test_llm_secrets_nowhere.py`` and
``tests/test_agents_address_nowhere.py`` and changes neither. The three have to be joined
once feature 005 is done: its Phase 9 rewrites the second.
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests import test_agents_address_nowhere as addresses
from tests import test_llm_secrets_nowhere as keys
from tests.conftest import A2aApp, LlmApp
from tests.fakes import ScriptedChatModel

# A port nothing else in the suite uses, so finding it means an address got out.
PORT = 9781
URL = f"http://127.0.0.1:{PORT}"

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


def index_routes(seen: keys.Seen, *threads: str) -> dict[str, str]:
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
    made.models.models["leaky-model"] = keys.LeakyModel()

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = keys.Seen(client, sse_events)
        saved = seen.call("POST", "/llm/profiles", json=keys.profile()).json()["id"]
        assert seen.call("POST", f"/llm/profiles/{saved}/activate").status_code == 200

        # A thread that pauses, and one that finishes; the person's own text names the key's
        # mark nowhere, so what is searched for can only have come from the service.
        paused = seen.stream("/threads/k-paused/runs", {"message": "Tell #ops."})
        assert paused[-1][0] == "interrupt"
        seen.stream("/threads/k-done/runs", {"message": "Just answer."})

        # One that fails with a provider's error that repeats the key.
        leaky = seen.call(
            "POST", "/llm/profiles", json=keys.profile(name="Leaky", model="leaky-model")
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
    keys.nowhere(seen.texts, keys.MARK)
    keys.nowhere([caplog.handler.format(record) for record in caplog.records], keys.MARK)


def test_an_address_is_in_nothing_the_thread_index_returns_or_logs(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    model = ScriptedChatModel(
        script=[handoff("Left waiting.", "h1"), handoff("Sent once approved.", "h2")],
        final_text="Handed over.",
    )
    # A saved agent on a recognizable port; a message to it needs a person's approval.
    made = make_a2a_app(model, {}, saved={"helper": {"url": URL}})

    with caplog.at_level(logging.DEBUG), TestClient(made.app) as client:
        seen = keys.Seen(client, sse_events)
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

        statuses = index_routes(seen, "a-paused", "a-done")

    assert statuses == {"a-paused": "needs_decision", "a-done": "done"}
    # The helper did receive the approved message, so the address was in use.
    assert len(made.stub_at(URL).record.messages) == 1
    addresses.nowhere(seen.texts, PORT)
    addresses.nowhere([caplog.handler.format(record) for record in caplog.records], PORT)
