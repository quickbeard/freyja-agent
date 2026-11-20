"""Feature 007: ``POST /threads/{thread_id}/stop`` (research R4, R5; FR-022, FR-022a, SC-012).

A stop is a mark on the thread's row, read by the instance that runs the thread between two
steps. The step under way completes and is recorded, no further step starts, and an action
under way is never cut off. A run that ends by itself first ends as it would have.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.thread_index import InMemoryThreadIndex
from tests.conftest import McpApp
from tests.fakes import FakeClock, LabelledChatModel, LoopingChatModel, ScriptedChatModel
from tests.unattended_helpers import (
    NOTIFY,
    Gate,
    Reader,
    held_server,
    hold_call,
    idle,
    kinds,
    ls,
    row,
    stream,
)

RUN = "/threads/{}/runs/unattended"
RESUME = "/threads/{}/resume/unattended"
ACTIONS = "freyja_agent.actions"


def stop(client: TestClient, thread: str, **headers: str) -> Any:
    return client.post(f"/threads/{thread}/stop", headers=headers)


def accepted(response: Any, thread: str) -> None:
    assert response.status_code == 202, response.text
    assert response.json() == {"thread_id": thread, "stopping": True}


def refused(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


class FailsAfterHold(LabelledChatModel):
    """Holds its next call, then fails it."""

    def next_message(self) -> AIMessage:
        raise RuntimeError("provider unavailable")


def test_a_stop_during_a_model_call_keeps_the_reply_and_runs_none_of_its_tools(
    make_mcp_app: Callable[..., McpApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="freyja_agent")
    model = LabelledChatModel(script=[ls("l1"), ls("l2")], final_text="Done.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        model.hold()
        reader = Reader(client, RUN.format("t-1"), {"message": "List."})
        reader.start()
        with model.held():
            accepted(stop(client, "t-1"), "t-1")
            during = row(client, "t-1")
            # A second stop of the same run changes nothing.
            accepted(stop(client, "t-1"), "t-1")
        events = reader.finish()
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()
        after = row(client, "t-1")
        kept = client.portal.call(made.runtime.threads.get, "t-1")
        calls = model.calls
        again = stream(client, RUN.format("t-1"), {"message": "Go on."})

    assert (during["status"], during["stoppable"], during["stopping"]) == ("running", True, True)
    assert events[-1] == ("end", {"status": "stopped"})
    # The reply under way is recorded; the tool it asked for did not run.
    assert kinds(state) == ["human", "ai"]
    assert state["messages"][-1]["tool_calls"][0]["name"] == "ls"
    assert calls == 1
    assert (after["status"], after["stoppable"], after["stopping"]) == ("stopped", False, False)
    # How the run ended was noted by its reader, not only read from what it left.
    assert kept.noted == "stopped"
    assert again[-1] == ("end", {"status": "completed"})
    assert caplog.text.count("Thread t-1: a stop was asked for the run in progress") == 2
    assert caplog.text.count("Thread t-1: the run ended because a stop was asked") == 1


def test_a_stop_during_a_tool_step_lets_the_tool_finish(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    gate = Gate()
    servers, stub = held_server(gate, gated=False)
    model = ScriptedChatModel(script=[hold_call(), ls("l2")])
    made = make_mcp_app(model, servers, stubs={"work": stub})
    with TestClient(made.app) as client:
        reader = Reader(client, RUN.format("t-1"), {"message": "Work."})
        reader.start()
        gate.wait()
        accepted(stop(client, "t-1"), "t-1")
        gate.open()
        events = reader.finish()
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()

    assert events[-1] == ("end", {"status": "stopped"})
    assert state["messages"][-1]["content"] == "work finished"
    assert gate.calls == 1
    assert model.calls == 1


def test_a_stop_during_an_approved_action_lets_it_run_once_and_records_it(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    gate = Gate()
    servers, stub = held_server(gate, gated=True)
    model = ScriptedChatModel(script=[hold_call()], final_text="Done.")
    made = make_mcp_app(model, servers, stubs={"work": stub})
    with TestClient(made.app) as client:
        pause = stream(client, RUN.format("t-1"), {"message": "Work."})
        assert pause[-1][0] == "interrupt"
        body = {"interrupt_id": pause[-1][1]["interrupt_id"], "decisions": [{"type": "approve"}]}
        reader = Reader(client, RESUME.format("t-1"), body)
        reader.start()
        gate.wait()
        accepted(stop(client, "t-1"), "t-1")
        gate.open()
        events = reader.finish()
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()
        after = row(client, "t-1")

    assert events[-1] == ("end", {"status": "stopped"})
    assert gate.calls == 1
    assert state["pending"] is None
    assert state["messages"][-1]["content"] == "work finished"
    assert model.calls == 1
    assert after["status"] == "stopped"


def test_a_stop_applied_before_an_approved_action_started_leaves_it_unstarted(
    make_mcp_app: Callable[..., McpApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=ACTIONS)
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY], final_text="Answered."), {})
    with TestClient(made.app) as client:
        pause = stream(client, RUN.format("t-1"), {"message": "Tell #ops."})
        index = made.runtime.threads
        real = index.stop_requested

        async def pressed_just_now(thread_id: str, owner: str) -> bool:
            # Stop is pressed right after Approve, before the first boundary is reached.
            await index.request_stop(thread_id, owner)
            return await real(thread_id, owner)

        monkeypatch.setattr(index, "stop_requested", pressed_just_now)
        body = {"interrupt_id": pause[-1][1]["interrupt_id"], "decisions": [{"type": "approve"}]}
        events = stream(client, RESUME.format("t-1"), body)
        monkeypatch.setattr(index, "stop_requested", real)
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()
        again = stream(client, RUN.format("t-1"), {"message": "Go on."})

    assert events[-1] == ("end", {"status": "stopped"})
    # The decision is spent, nothing is pending, and the action never ran.
    assert state["pending"] is None
    assert [r for r in caplog.records if r.name == ACTIONS] == []
    assert again[-1] == ("end", {"status": "completed"})


@pytest.mark.parametrize(
    ("model", "status", "last"),
    [
        (LabelledChatModel(script=[NOTIFY]), "needs_decision", "interrupt"),
        (LabelledChatModel(final_text="Done."), "done", "end"),
        (FailsAfterHold(), "failed", "error"),
    ],
    ids=["pauses", "finishes", "fails"],
)
def test_a_run_that_ends_by_itself_before_the_stop_is_applied_ends_as_it_did(
    make_mcp_app: Callable[..., McpApp], model: LabelledChatModel, status: str, last: str
) -> None:
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        model.hold()
        reader = Reader(client, RUN.format("t-1"), {"message": "Tell #ops."})
        reader.start()
        with model.held():
            accepted(stop(client, "t-1"), "t-1")
        events = reader.finish()
        idle(client, "t-1")
        after = row(client, "t-1")

    assert events[-1][0] == last
    assert events[-1] != ("end", {"status": "stopped"})
    assert after["status"] == status


def test_an_index_whose_stop_cannot_be_read_lets_the_run_go_on(
    make_mcp_app: Callable[..., McpApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[ls("l1")], final_text="Listed."), {})

    async def away(*_args: Any) -> bool:
        raise RuntimeError("the index is away")

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.runtime.threads, "stop_requested", away)
        events = stream(client, RUN.format("t-1"), {"message": "List."})

    assert events[-1] == ("end", {"status": "completed"})
    assert "whether a stop was asked could not be read (RuntimeError); the run goes on" in (
        caplog.text
    )


def test_a_stop_with_no_run_in_progress_is_refused(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY]), {})
    with TestClient(made.app) as client:
        refused(stop(client, "never"), 404, "thread_not_found")
        paused = stream(client, RUN.format("p-1"), {"message": "Tell #ops."})
        assert paused[-1][0] == "interrupt"
        # A paused thread has no run: a proposal is declined by rejecting it.
        refused(stop(client, "p-1"), 409, "no_run")
        assert client.get("/threads/p-1/state").json()["pending"] is not None


def test_a_run_the_console_did_not_start_cannot_be_stopped(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    model = LabelledChatModel(final_text="Done.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        model.hold()
        reader = Reader(client, "/threads/t-1/runs", {"message": "hi"})
        reader.start()
        with model.held():
            refused(stop(client, "t-1"), 409, "not_stoppable")
        assert reader.finish()[-1] == ("end", {"status": "completed"})

        # The delegation worker takes the lease the same way for a background run.
        client.portal.call(made.runtime.locks.acquire, "t-2", "instance-a:background")
        refused(stop(client, "t-2"), 409, "not_stoppable")
        client.portal.call(made.runtime.locks.release, "t-2", "instance-a:background")
        refused(stop(client, "t-1"), 409, "no_run")


def test_a_page_of_another_origin_cannot_stop_a_run(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    model = LabelledChatModel(script=[ls("l1")], final_text="Listed.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        model.hold()
        reader = Reader(client, RUN.format("t-1"), {"message": "List."})
        reader.start()
        with model.held():
            response = stop(client, "t-1", **{"sec-fetch-site": "cross-site"})
            refused(response, 403, "other_origin")
            kept = client.portal.call(made.runtime.threads.get, "t-1")
        events = reader.finish()

    assert kept.stop_owner is None
    assert events[-1] == ("end", {"status": "completed"})
    assert model.calls == 2


def test_a_stop_asked_of_another_instance_ends_the_run(
    make_mcp_app: Callable[..., McpApp], clock: FakeClock
) -> None:
    saver = InMemorySaver()
    locks = InMemoryRunLocks(60, clock=clock.now)
    index = InMemoryThreadIndex()
    model = LabelledChatModel(script=[ls("l1")], final_text="Listed.")
    here = make_mcp_app(model, {}, saver=saver, locks=locks, thread_index=index)
    there = make_mcp_app(
        ScriptedChatModel(),
        {},
        saver=saver,
        locks=locks,
        thread_index=index,
        instance_id="instance-b",
    )
    with TestClient(here.app) as first, TestClient(there.app) as second:
        model.hold()
        reader = Reader(first, RUN.format("t-1"), {"message": "List."})
        reader.start()
        with model.held():
            accepted(stop(second, "t-1"), "t-1")
            assert row(second, "t-1")["stopping"] is True
        events = reader.finish()
        idle(first, "t-1")
        after = row(second, "t-1")

    assert events[-1] == ("end", {"status": "stopped"})
    assert after["status"] == "stopped"
    assert model.calls == 1


def test_a_console_run_ends_at_the_step_limit_without_exceeding_it(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(LoopingChatModel(), {}, max_steps=9)
    with TestClient(made.app) as client:
        events = stream(client, RUN.format("t-1"), {"message": "Loop."})
        idle(client, "t-1")
        one = row(client, "t-1")
        steps = client.portal.call(
            made.runtime.agent.aget_state, {"configurable": {"thread_id": "t-1"}}
        ).metadata["step"]

    assert events[-1][1]["code"] == "step_limit"
    # The thread's first checkpoint, the input's, is step -1: no more than 9 steps ran.
    assert 7 <= steps <= 9
    assert (one["status"], one["error"]["code"]) == ("failed", "step_limit")


def test_a_stop_asked_just_after_a_run_ended_does_not_land_on_the_next_one(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route reads the row, and the run ends and a follow-up starts before the mark."""
    first = LabelledChatModel(final_text="Done.")
    made = make_mcp_app(first, {})
    with TestClient(made.app) as client:
        stream(client, RUN.format("t-1"), {"message": "hi"})
        index = made.runtime.threads
        first.hold()
        reader = Reader(client, RUN.format("t-1"), {"message": "And then."})
        reader.start()
        with first.held():
            current = client.portal.call(index.get, "t-1")
            real = index.get

            async def read_before_the_follow_up(thread_id: str) -> Any:
                # What the route read: the row of the run before this one.
                return replace(current, run_owner="instance-a:the-run-that-ended")

            monkeypatch.setattr(index, "get", read_before_the_follow_up)
            late = stop(client, "t-1")
            monkeypatch.setattr(index, "get", real)
            kept = client.portal.call(index.get, "t-1")
        events = reader.finish()

    refused(late, 409, "no_run")
    assert kept.stop_owner is None
    assert events[-1] == ("end", {"status": "completed"})
