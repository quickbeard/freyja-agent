"""FR-004a: a run stops at the configured step limit with an error event; the thread stays usable."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from freyja_agent.runtime.runtime import Runtime
from tests.fakes import LoopingChatModel, ScriptedChatModel


def test_step_limit_ends_the_run_with_an_error_naming_the_limit(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    runtime = make_runtime(LoopingChatModel(), max_steps=2)
    with (
        TestClient(make_app(runtime)) as client,
        client.stream("POST", "/threads/loop-1/runs", json={"message": "go"}) as response,
    ):
        events = sse_events(response.iter_lines())
    assert events[0][0] == "start"
    kind, data = events[-1]
    assert kind == "error"
    assert data["code"] == "step_limit"
    assert "2" in data["message"] and "FREYJA_MAX_STEPS" in data["message"]


def test_thread_is_usable_after_hitting_the_limit(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    looping = make_runtime(LoopingChatModel(), max_steps=2)
    with TestClient(make_app(looping)) as client:
        with client.stream("POST", "/threads/loop-2/runs", json={"message": "go"}) as response:
            assert sse_events(response.iter_lines())[-1][0] == "error"
        assert not client.app.state.runtime.locks._rows

    # Same thread, same persistence, a model that finishes: the thread continues.
    healthy = make_runtime(
        ScriptedChatModel(final_text="recovered"),
        saver=looping.agent.checkpointer,
        store=looping.agent.store,
        locks=looping.locks,
    )
    with (
        TestClient(make_app(healthy)) as client,
        client.stream("POST", "/threads/loop-2/runs", json={"message": "again"}) as response,
    ):
        events = sse_events(response.iter_lines())
    assert events[-1] == ("end", {"status": "completed"})
