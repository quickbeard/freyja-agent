"""Feature 007: runs that go on without a reader (research R1 to R3, R17; FR-021).

``POST /threads/{id}/runs/unattended`` and ``POST /threads/{id}/resume/unattended`` call the
handlers of the routes that exist since feature 001, so they refuse what those refuse and
stream what those stream. What differs: the run goes on whoever reads it, it can be stopped
(tests/test_unattended_stop.py), and the answer is made to pass the console's forwarding.
"""

import asyncio
import inspect
import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sse_starlette import ServerSentEvent

from freyja_agent.api import thread_index as index_api
from freyja_agent.api import threads as threads_api
from freyja_agent.runtime import unattended
from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.thread_index import InMemoryThreadIndex
from freyja_agent.runtime.unattended import UnattendedRuns
from tests.conftest import LlmApp, McpApp
from tests.fakes import FailingChatModel, FakeClock, LabelledChatModel, ScriptedChatModel
from tests.test_llm_no_model import unreadable_and_active
from tests.unattended_helpers import (
    NOTIFY,
    Gate,
    Reader,
    held_server,
    hold_call,
    idle,
    kinds,
    leave,
    ls,
    row,
    shape,
    stream,
)

OLD = {"runs": "/threads/{}/runs", "resume": "/threads/{}/resume"}
NEW = {"runs": "/threads/{}/runs/unattended", "resume": "/threads/{}/resume/unattended"}
BOTH = pytest.mark.parametrize("routes", [OLD, NEW], ids=["existing", "unattended"])


def paused(client: TestClient, routes: dict[str, str], thread: str) -> str:
    events = stream(client, routes["runs"].format(thread), {"message": "Tell #ops."})
    assert events[-1][0] == "interrupt", events
    return events[-1][1]["interrupt_id"]


def decide(interrupt_id: str, *decisions: dict[str, Any]) -> dict[str, Any]:
    return {"interrupt_id": interrupt_id, "decisions": list(decisions)}


# Part 1: the same refusals and the same events as the existing routes.


@BOTH
@pytest.mark.parametrize("message", ["", "x" * 32001])
def test_a_message_the_service_refuses_is_refused_the_same(
    make_mcp_app: Callable[..., McpApp], routes: dict[str, str], message: str
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        response = client.post(routes["runs"].format("t-1"), json={"message": message})
        assert response.status_code == 422, response.text
        assert client.get("/threads/t-1/state").status_code == 404
        assert client.get("/threads").json()["total"] == 0


@BOTH
def test_the_refusals_of_a_run_and_a_resume_are_the_same(
    make_mcp_app: Callable[..., McpApp], routes: dict[str, str]
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."), {})
    with TestClient(made.app) as client:
        interrupt_id = paused(client, routes, "p-1")
        before = client.get("/threads/p-1/state").json()

        def code(path: str, body: dict[str, Any]) -> tuple[int, str]:
            response = client.post(path.format("p-1"), json=body)
            return response.status_code, response.json()["detail"]["code"]

        assert code(routes["runs"], {"message": "again"}) == (409, "decision_pending")
        assert code(routes["resume"], decide("other", {"type": "approve"})) == (
            409,
            "proposal_mismatch",
        )
        missing = client.post(
            routes["resume"].format("never"), json=decide("x", {"type": "approve"})
        )
        assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "thread_not_found")
        edit = client.post(
            routes["resume"].format("p-1"),
            json=decide(interrupt_id, {"type": "edit", "args": {"channel": "#ops"}}),
        )
        assert edit.status_code == 422, edit.text

        # Another run holds the thread.
        client.portal.call(made.runtime.locks.acquire, "p-1", "instance-b:busy")
        assert code(routes["runs"], {"message": "hi"}) == (409, "run_active")
        assert code(routes["resume"], decide(interrupt_id, {"type": "approve"})) == (
            409,
            "run_active",
        )
        client.portal.call(made.runtime.locks.release, "p-1", "instance-b:busy")

        # Nothing ran because of any of it, and the proposal is still there.
        assert client.get("/threads/p-1/state").json() == before
        done = stream(
            client, routes["resume"].format("p-1"), decide(interrupt_id, {"type": "approve"})
        )
        assert done[-1] == ("end", {"status": "completed"})
        assert code(routes["resume"], decide(interrupt_id, {"type": "approve"})) == (
            409,
            "nothing_pending",
        )


def test_a_run_streams_what_the_existing_route_streams(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    seen = {}
    for name, routes in (("existing", OLD), ("unattended", NEW)):
        made = make_mcp_app(ScriptedChatModel(script=[ls("l1")], final_text="Listed."), {})
        with TestClient(made.app) as client:
            seen[name] = shape(stream(client, routes["runs"].format("t-1"), {"message": "List."}))
    assert seen["unattended"] == seen["existing"]
    assert seen["unattended"][-1] == ("end", {"status": "completed"})


@pytest.mark.parametrize(
    "decision",
    [
        {"type": "approve"},
        {"type": "edit", "args": {"channel": "#ops-staging", "message": "hi"}},
        {"type": "reject", "message": "Not now."},
    ],
    ids=["approve", "edit", "reject"],
)
def test_a_pause_and_each_decision_stream_what_the_existing_routes_stream(
    make_mcp_app: Callable[..., McpApp], decision: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="freyja_agent.actions")
    seen = {}
    for name, routes in (("existing", OLD), ("unattended", NEW)):
        caplog.clear()
        made = make_mcp_app(ScriptedChatModel(script=[NOTIFY], final_text="Handled."), {})
        with TestClient(made.app) as client:
            first = stream(client, routes["runs"].format("t-1"), {"message": "Tell #ops."})
            body = decide(first[-1][1]["interrupt_id"], decision)
            then = stream(client, routes["resume"].format("t-1"), body)
            actions = [r.getMessage() for r in caplog.records if r.name == "freyja_agent.actions"]
            seen[name] = (
                shape(first),
                shape(then),
                client.get("/threads/t-1/state").json(),
                actions,
            )
    existing, console = seen["existing"], seen["unattended"]
    assert console[:2] == existing[:2]
    assert kinds(console[2]) == kinds(existing[2])
    assert [m["content"] for m in console[2]["messages"]] == [
        m["content"] for m in existing[2]["messages"]
    ]
    # The action ran as decided, once, or not at all.
    assert console[3] == existing[3]
    assert len(console[3]) == (0 if decision["type"] == "reject" else 1)


def test_the_answer_passes_the_consoles_forwarding_as_it_is_produced(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header Next.js leaves alone, and a sign of life well inside its 30 seconds."""
    assert index_api.PING_SECONDS == 10
    monkeypatch.setattr(index_api, "PING_SECONDS", 0.05)
    model = LabelledChatModel(final_text="Done.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        model.hold()
        with client.stream("POST", NEW["runs"].format("t-1"), json={"message": "hi"}) as response:
            headers = response.headers
            lines = []
            read = response.iter_lines()
            with model.held():
                # Silence while the model works: a ping arrives all the same.
                for line in read:
                    lines.append(line)
                    if line.startswith(": ping"):
                        break
            lines += list(read)
        old = client.post(OLD["runs"].format("t-2"), json={"message": "hi"})

    assert headers["cache-control"] == "no-store, no-transform"
    assert any(line.startswith(": ping") for line in lines)
    # The existing route keeps its own answer (FR-027).
    assert old.headers["cache-control"] == "no-store"


def test_a_console_run_is_stoppable_while_it_runs_and_keeps_its_first_message(
    make_mcp_app: Callable[..., McpApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="freyja_agent")
    model = LabelledChatModel(final_text="Done.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        model.hold()
        reader = Reader(client, NEW["runs"].format("t-1"), {"message": "Summarize the week."})
        reader.start()
        with model.held():
            during = row(client, "t-1")
            kept = client.portal.call(made.runtime.threads.get, "t-1")
        assert reader.finish()[-1] == ("end", {"status": "completed"})
        stream(client, NEW["runs"].format("t-1"), {"message": "And the month."})
        after = row(client, "t-1")

    assert kept.run_unattended is True
    assert (during["status"], during["stoppable"], during["stopping"]) == ("running", True, False)
    # A follow-up is not the thread's first message.
    assert during["first_message"] == after["first_message"] == "Summarize the week."
    assert (after["status"], after["stoppable"]) == ("done", False)
    assert "Thread t-1: a run started that goes on without a reader" in caplog.text


def test_the_handlers_answer_with_the_runs_events_not_yet_started(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one attribute the routes rely on, pinned so that an upgrade fails loudly (R3)."""
    answered: list[Any] = []
    states: list[str] = []
    real = threads_api.start_run

    async def watching(*args: Any, **kwargs: Any) -> Any:
        response = await real(*args, **kwargs)
        answered.append(response.body_iterator)
        states.append(inspect.getasyncgenstate(response.body_iterator))
        return response

    monkeypatch.setattr(threads_api, "start_run", watching)
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        events = stream(client, NEW["runs"].format("t-1"), {"message": "hi"})
    assert len(answered) == 1
    assert isinstance(answered[0], AsyncIterator)
    # Not started when the handler answered: nothing of the run ran before it was handed on.
    assert states == [inspect.AGEN_CREATED]
    assert events[-1] == ("end", {"status": "completed"})


@pytest.mark.parametrize("route", ["runs", "resume"])
def test_a_page_of_another_origin_cannot_start_or_continue_a_console_run(
    make_mcp_app: Callable[..., McpApp], route: str
) -> None:
    model = ScriptedChatModel(script=[NOTIFY])
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        interrupt_id = paused(client, NEW, "p-1")
        runs = client.portal.call(made.runtime.threads.get, "p-1").runs
        body = {"message": "hi"} if route == "runs" else decide(interrupt_id, {"type": "approve"})
        response = client.post(
            NEW[route].format("p-1"), json=body, headers={"sec-fetch-site": "cross-site"}
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "other_origin"
        # Refused before a lease was taken.
        assert client.portal.call(made.runtime.threads.get, "p-1").runs == runs
        assert client.get("/threads/p-1/state").json()["pending"]["interrupt_id"] == interrupt_id
    assert model.calls == 1


# Part 2: readers.


def test_a_run_goes_on_after_its_reader_leaves(make_mcp_app: Callable[..., McpApp]) -> None:
    script = [ls(f"l{n}") for n in range(5)]
    model = ScriptedChatModel(script=list(script), final_text="All five listed.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        leave(
            client, NEW["runs"].format("t-1"), {"message": "List five times."}, at="event: update"
        )
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()
        held = sum(made.runtime.registry._holders.values())
        one = row(client, "t-1")

    assert state["messages"][-1]["content"] == "All five listed."
    assert model.calls == 6
    assert state["run_active"] is False
    # The run's context was given back.
    assert held == 0
    assert one["status"] == "done"


def said(state: dict[str, Any]) -> list[Any]:
    """A conversation without what differs between two runs of one script: the ids."""
    return [
        (
            message["type"],
            message["content"],
            message.get("name"),
            [(call["name"], call["args"]) for call in message.get("tool_calls") or []],
        )
        for message in state["messages"]
    ]


def test_a_run_whose_reader_left_holds_the_same_conversation_as_one_that_was_read(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    """SC-011: the same end, with the same conversation. Each action runs alone here, so
    the order of results is the script's."""
    states = {}
    for how in ("read", "left"):
        script = [ls(f"l{n}") for n in range(3)]
        made = make_mcp_app(ScriptedChatModel(script=script, final_text="All three listed."), {})
        with TestClient(made.app) as client:
            path, body = NEW["runs"].format("t-1"), {"message": "List three times."}
            if how == "read":
                assert stream(client, path, body)[-1] == ("end", {"status": "completed"})
            else:
                leave(client, path, body, at="event: update")
            idle(client, "t-1")
            states[how] = client.get("/threads/t-1/state").json()

    assert said(states["left"]) == said(states["read"])
    assert kinds(states["left"]) == ["human", "ai", "tool", "ai", "tool", "ai", "tool", "ai"]
    assert states["left"]["pending"] is None


@pytest.mark.parametrize("at", [None, "event: update"], ids=["at once", "at a step"])
def test_a_decision_whose_reader_left_is_carried_out_as_one_that_was_read(
    make_mcp_app: Callable[..., McpApp], caplog: pytest.LogCaptureFixture, at: str | None
) -> None:
    """US5/AC1, SC-011: a run the console started with a decision goes on too."""
    caplog.set_level(logging.WARNING, logger="freyja_agent.actions")
    states, rows, sent = {}, {}, {}
    for how in ("read", "left"):
        caplog.clear()
        made = make_mcp_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."), {})
        with TestClient(made.app) as client:
            body = decide(paused(client, NEW, "t-1"), {"type": "approve"})
            path = NEW["resume"].format("t-1")
            if how == "read":
                assert stream(client, path, body)[-1] == ("end", {"status": "completed"})
            else:
                leave(client, path, body, at=at)
            idle(client, "t-1")
            states[how] = client.get("/threads/t-1/state").json()
            rows[how] = row(client, "t-1")
        sent[how] = [r.getMessage() for r in caplog.records if r.name == "freyja_agent.actions"]

    assert said(states["left"]) == said(states["read"])
    assert kinds(states["left"]) == ["human", "ai", "tool", "ai"]
    assert states["left"]["pending"] is None
    assert (rows["left"]["status"], rows["left"]["runs"]) == ("done", 2)
    # The approved action ran once, whoever read the run.
    assert len(sent["left"]) == 1
    assert sent["left"] == sent["read"]


def test_a_second_message_is_refused_while_a_run_nobody_reads_is_in_progress(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    """FR-021: one run per task for as long as the run lasts, also without a reader."""
    model = LabelledChatModel(script=[], final_text="Answered.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        model.hold()
        leave(client, NEW["runs"].format("t-1"), {"message": "The first."})
        with model.held():
            refusals = [
                (response.status_code, response.json()["detail"]["code"])
                for response in (
                    client.post(NEW["runs"].format("t-1"), json={"message": "The second."}),
                    client.post(OLD["runs"].format("t-1"), json={"message": "The second."}),
                    client.post(NEW["resume"].format("t-1"), json=decide("x", {"type": "approve"})),
                )
            ]
            during = row(client, "t-1")
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()
        after = row(client, "t-1")

    assert refusals == [(409, "run_active")] * 3
    assert (during["status"], during["runs"]) == ("running", 1)
    # Nothing of the refused messages reached the thread, and no run was counted for them.
    assert [(kind, text) for kind, text, _, _ in said(state)] == [
        ("human", "The first."),
        ("ai", "Answered."),
    ]
    assert model.calls == 1
    assert (after["status"], after["runs"]) == ("done", 1)


def test_an_edit_shows_in_the_conversation_as_proposed_and_as_changed_by_a_person(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    """US2/AC3: the conversation shows both what was proposed and that a person changed
    it. The second is the approval library's notice, which nothing here writes: this is
    what fails if a later version drops or rewords it."""
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."), {})
    with TestClient(made.app) as client:
        interrupt_id = paused(client, NEW, "t-1")
        edited = {"channel": "#ops-staging", "message": "hi"}
        done = stream(
            client,
            NEW["resume"].format("t-1"),
            decide(interrupt_id, {"type": "edit", "args": edited}),
        )
        state = client.get("/threads/t-1/state").json()

    assert done[-1] == ("end", {"status": "completed"})
    proposed, result = state["messages"][1], state["messages"][2]
    # What Freyja proposed is kept as it was proposed.
    assert proposed["tool_calls"][0]["args"] == {"channel": "#ops", "message": "hi"}
    # The result says what was run in its place, and that a person changed it.
    assert result["type"] == "tool"
    assert "#ops-staging" in result["content"]
    assert "a human reviewer replaced this tool call before it ran" in result["content"]


def test_a_run_nobody_reads_at_all_goes_on(make_mcp_app: Callable[..., McpApp]) -> None:
    model = ScriptedChatModel(script=[ls("l1"), ls("l2")], final_text="Listed.")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        leave(client, NEW["runs"].format("t-1"), {"message": "List."})
        idle(client, "t-1")
        state = client.get("/threads/t-1/state").json()
    assert state["messages"][-1]["content"] == "Listed."
    assert model.calls == 3


def test_the_existing_route_still_ends_a_run_its_reader_left(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    model = ScriptedChatModel(script=[ls(f"l{n}") for n in range(5)])
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        leave(client, OLD["runs"].format("t-1"), {"message": "List."}, at="event: update")
        idle(client, "t-1")
        # A model call under way when the reader left may still complete in its thread.
        time.sleep(0.3)
        state = client.get("/threads/t-1/state").json()
    assert model.calls < 6
    assert state["messages"][-1]["content"] != "Done."


def test_a_run_that_fails_is_listed_as_failed_with_its_reason(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    model = FailingChatModel(script=[ls("l1")], fail_on_call=2, error="provider unavailable")
    made = make_mcp_app(model, {})
    with TestClient(made.app) as client:
        leave(client, NEW["runs"].format("t-1"), {"message": "List."})
        idle(client, "t-1")
        one = row(client, "t-1")
        listed = client.get("/threads").json()

    assert one["status"] == "failed"
    assert one["error"]["code"] == "model_error"
    assert "provider unavailable" in one["error"]["message"]
    assert listed["threads"] == [one]


def test_no_usable_model_fails_a_thread_that_has_a_state_and_lists_no_new_one(
    make_mcp_app: Callable[..., McpApp],
    make_llm_app: Callable[..., LlmApp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")
    saver = InMemorySaver()
    first = make_mcp_app(ScriptedChatModel(final_text="Hello."), {}, saver=saver)
    with TestClient(first.app) as client:
        stream(client, NEW["runs"].format("old"), {"message": "Hi."})

    made, _ = unreadable_and_active(make_llm_app)
    made = make_llm_app(model="openai:env-model", store=made.store, saver=saver)
    with TestClient(made.app) as client:
        old = stream(client, NEW["runs"].format("old"), {"message": "Again."})
        new = stream(client, NEW["runs"].format("new"), {"message": "First."})
        listed = client.get("/threads").json()
        missing = client.get("/threads/new")

    for events in (old, new):
        assert events[-1][1]["code"] == "no_usable_model"
    assert [(t["thread_id"], t["status"]) for t in listed["threads"]] == [("old", "failed")]
    assert listed["threads"][0]["error"]["code"] == "no_usable_model"
    assert missing.status_code == 404


async def test_a_reader_that_does_not_keep_up_is_disconnected_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(unattended, "READER_BACKLOG", 5)
    index = InMemoryThreadIndex()
    runs = UnattendedRuns(index, "instance-a")
    sent: list[int] = []

    async def events() -> AsyncIterator[ServerSentEvent]:
        yield ServerSentEvent(event="start", data=json.dumps({"run_id": "r1"}))
        for number in range(20):
            sent.append(number)
            yield ServerSentEvent(event="update", data=json.dumps({"n": number}))
            await asyncio.sleep(0)
        yield ServerSentEvent(event="end", data=json.dumps({"status": "completed"}))

    reader = runs.start("t-1", events())
    await runs.close()
    received = [event async for event in reader]

    # The run went on to its end; the reader was cut off and got nothing more.
    assert len(sent) == 20
    assert received == []
    assert "a reader fell 5 events behind and was disconnected" in caplog.text
    assert unattended.READER_BACKLOG == 5


def test_a_pause_nobody_read_is_counted_and_decided_after_a_restart(
    make_mcp_app: Callable[..., McpApp], clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="freyja_agent.actions")
    saver = InMemorySaver()
    locks = InMemoryRunLocks(60, clock=clock.now)
    index = InMemoryThreadIndex()
    first = make_mcp_app(
        ScriptedChatModel(script=[NOTIFY]), {}, saver=saver, locks=locks, thread_index=index
    )
    with TestClient(first.app) as client:
        leave(client, NEW["runs"].format("t-1"), {"message": "Tell #ops."})
        idle(client, "t-1")
        assert client.get("/threads").json()["needs_decision"] == 1

    second = make_mcp_app(
        ScriptedChatModel(final_text="Sent."),
        {},
        saver=saver,
        locks=locks,
        thread_index=index,
        instance_id="instance-b",
    )
    with TestClient(second.app) as client:
        listed = client.get("/threads").json()
        pending = client.get("/threads/t-1/state").json()["pending"]
        done = stream(
            client,
            NEW["resume"].format("t-1"),
            decide(pending["interrupt_id"], {"type": "approve"}),
        )
        after = client.get("/threads").json()

    assert (listed["needs_decision"], listed["threads"][0]["status"]) == (1, "needs_decision")
    assert done[-1] == ("end", {"status": "completed"})
    assert (after["needs_decision"], after["threads"][0]["status"]) == (0, "done")
    assert len([r for r in caplog.records if r.name == "freyja_agent.actions"]) == 1


def test_an_index_that_cannot_mark_or_note_a_run_does_not_change_it(
    make_mcp_app: Callable[..., McpApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = LabelledChatModel(final_text="Done.")
    made = make_mcp_app(model, {})

    async def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the index is away")

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.runtime.threads, "mark_unattended", broken)
        model.hold()
        reader = Reader(client, NEW["runs"].format("t-1"), {"message": "hi"})
        reader.start()
        with model.held():
            during = row(client, "t-1")
        assert reader.finish()[-1] == ("end", {"status": "completed"})

    # A run whose end cannot be noted ends as it would have.
    failing_app = make_mcp_app(FailingChatModel(fail_on_call=1), {})
    with TestClient(failing_app.app) as other:
        monkeypatch.setattr(failing_app.runtime.threads, "note", broken)
        events = stream(other, NEW["runs"].format("t-2"), {"message": "hi"})

    assert (during["status"], during["stoppable"]) == ("running", False)
    assert events[-1][1]["code"] == "model_error"
    assert "could not be marked as one that goes on without a reader (RuntimeError)" in caplog.text
    assert "how its run ended could not be kept for the list (RuntimeError)" in caplog.text


# Part 3: the service stopping (research R17).


def test_stopping_the_service_waits_for_the_step_under_way(
    make_mcp_app: Callable[..., McpApp], clock: FakeClock
) -> None:
    gate = Gate()
    servers, stub = held_server(gate, gated=False)
    model = ScriptedChatModel(script=[hold_call(), ls("l1"), ls("l2")])
    saver = InMemorySaver()
    locks = InMemoryRunLocks(60, clock=clock.now)
    index = InMemoryThreadIndex()
    made = make_mcp_app(
        model, servers, stubs={"work": stub}, saver=saver, locks=locks, thread_index=index
    )
    with TestClient(made.app) as client:
        # The reader's stream is gone before the service stops, as the server cancels it.
        leave(client, NEW["runs"].format("t-1"), {"message": "Work."})
        gate.wait()
        calls = model.calls
        opening = threading.Timer(0.3, gate.open)
        opening.start()
        started = time.monotonic()
    waited = time.monotonic() - started

    assert waited >= 0.25
    # The step under way completed; no further step started.
    assert gate.calls == 1
    assert model.calls == calls
    again = make_mcp_app(ScriptedChatModel(), {}, saver=saver, locks=locks, thread_index=index)
    with TestClient(again.app) as client:
        state = client.get("/threads/t-1/state").json()
        one = row(client, "t-1")
    assert state["run_active"] is False
    assert state["messages"][-1]["type"] == "tool"
    assert state["messages"][-1]["content"] == "work finished"
    assert one["status"] == "stopped"


def test_a_follow_up_through_the_console_is_not_the_threads_first_message(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(final_text="Done."), {})
    with TestClient(made.app) as client:
        # The thread began the earlier way, and nobody read the list since.
        stream(client, OLD["runs"].format("t-1"), {"message": "How it began."})
        stream(client, NEW["runs"].format("t-1"), {"message": "A follow-up."})
        one = row(client, "t-1")
    assert one["first_message"] == "How it began."


async def test_a_drive_that_is_cancelled_still_gives_the_run_back() -> None:
    """The run's own ``finally`` gives its context and lease back; it must always run."""
    index = InMemoryThreadIndex()
    runs = UnattendedRuns(index, "instance-a")
    given_back = asyncio.Event()
    noting = asyncio.Event()

    async def slow_note(*_args: Any, **_kwargs: Any) -> bool:
        noting.set()
        await asyncio.sleep(30)
        return True

    index.note = slow_note  # type: ignore[method-assign]

    async def events() -> AsyncIterator[ServerSentEvent]:
        try:
            yield ServerSentEvent(event="start", data=json.dumps({"run_id": "r1"}))
            yield ServerSentEvent(event="error", data=json.dumps({"code": "internal"}))
        finally:
            given_back.set()

    runs.start("t-1", events())
    await asyncio.wait_for(noting.wait(), 5)
    for task in list(runs._tasks):
        task.cancel()
    await asyncio.wait_for(given_back.wait(), 5)


async def test_an_index_that_does_not_answer_holds_a_run_up_for_a_bounded_time(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(unattended, "INDEX_WRITE_SECONDS", 0.05)
    index = InMemoryThreadIndex()
    runs = UnattendedRuns(index, "instance-a")

    async def never(*_args: Any, **_kwargs: Any) -> Any:
        await asyncio.sleep(30)

    for name in ("mark_unattended", "note", "stop_requested"):
        monkeypatch.setattr(index, name, never)

    async def events() -> AsyncIterator[ServerSentEvent]:
        yield ServerSentEvent(event="start", data=json.dumps({"run_id": "r1"}))
        assert await runs.should_stop("t-1") is False
        yield ServerSentEvent(event="error", data=json.dumps({"code": "internal"}))

    reader = runs.start("t-1", events())
    received = await asyncio.wait_for(_all(reader), 5)
    assert [event.event for event in received] == ["start", "error"]
    for what in ("marked", "a stop was asked", "how its run ended"):
        assert what in caplog.text
    assert caplog.text.count("TimeoutError") == 3


async def _all(reader: AsyncIterator[ServerSentEvent]) -> list[ServerSentEvent]:
    return [event async for event in reader]


def test_a_graph_that_cannot_be_wrapped_leaves_no_agents_held(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    from freyja_agent.runtime import runtime as runtime_module

    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        runtime = made.runtime

        def refusing(*_args: Any) -> Any:
            raise unattended.MissingNodeError("no node 'model'")

        monkeypatch.setattr(runtime_module, "SegmentedGraph", refusing)
        runtime.segmented = None
        with pytest.raises(unattended.MissingNodeError):
            client.portal.call(runtime.begin_run)
        held = sum(runtime.registry._holders.values())
    assert held == 0


# Feature 009 (research R10): one more reader of a console run in progress.


async def test_a_reader_that_joins_gets_the_events_that_follow_to_the_run_s_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = UnattendedRuns(InMemoryThreadIndex(), "instance-a")
    assert runs.join("t-1") is None
    step = asyncio.Event()

    async def events() -> AsyncIterator[ServerSentEvent]:
        yield ServerSentEvent(event="start", data=json.dumps({"run_id": "r1"}))
        await step.wait()
        yield ServerSentEvent(event="update", data=json.dumps({"n": 1}))
        yield ServerSentEvent(event="end", data=json.dumps({"status": "completed"}))

    first = runs.start("t-1", events())
    await asyncio.sleep(0.01)
    joined = runs.join("t-1")
    assert joined is not None
    step.set()
    got = [event.event async for event in joined]
    assert got == ["update", "end"]
    assert [event.event async for event in first] == ["start", "update", "end"]
    assert runs.join("t-1") is None

    # A joined reader that falls behind is disconnected, as any other is.
    monkeypatch.setattr(unattended, "READER_BACKLOG", 2)
    flood = asyncio.Event()

    async def many() -> AsyncIterator[ServerSentEvent]:
        yield ServerSentEvent(event="start", data=json.dumps({"run_id": "r2"}))
        await flood.wait()
        for number in range(5):
            yield ServerSentEvent(event="update", data=json.dumps({"n": number}))
        yield ServerSentEvent(event="end", data=json.dumps({"status": "completed"}))

    runs.start("t-2", many())
    await asyncio.sleep(0.01)
    slow = runs.join("t-2")
    flood.set()
    await runs.close()
    assert [event async for event in slow] == []


# Feature 009, request 4 (research R10, R11): a run that stops at its first boundary, and a
# caller's thread whose last task its caller canceled.


async def _held(step: asyncio.Event) -> AsyncIterator[ServerSentEvent]:
    yield ServerSentEvent(event="start", data=json.dumps({"run_id": "r1"}))
    await step.wait()
    yield ServerSentEvent(event="end", data=json.dumps({"status": "completed"}))


async def test_a_run_started_to_stop_first_stops_at_its_first_boundary_and_says_so() -> None:
    index = InMemoryThreadIndex()
    runs = UnattendedRuns(index, "instance-a")
    step = asyncio.Event()
    reader = runs.start("t-1", _held(step), stop_first=True)
    other = runs.start("t-2", _held(step))
    assert await runs.should_stop("t-1") is True
    assert await runs.should_stop("t-2") is False
    step.set()
    got = [event async for event in reader]
    assert (got[-1].event, json.loads(got[-1].data)) == ("end", {"status": "stopped"})
    await runs.close()
    assert json.loads([event async for event in other][-1].data) == {"status": "completed"}


async def test_a_console_run_on_a_thread_whose_caller_canceled_stops_marked_or_not(
    caplog: pytest.LogCaptureFixture,
) -> None:
    index = InMemoryThreadIndex()
    runs = UnattendedRuns(index, "instance-a")
    asked: list[str] = []
    canceled = {"caller-thread"}

    async def by_its_caller(thread_id: str) -> bool:
        asked.append(thread_id)
        if thread_id == "broken":
            raise RuntimeError("the store is down")
        return thread_id in canceled

    held = asyncio.Event()
    for thread in ("caller-thread", "person-thread", "broken"):
        runs.start(thread, _held(held))
    # Before any event: no owner yet, so the index was never marked for these runs.
    assert await runs.should_stop("caller-thread") is False
    runs.canceled = by_its_caller
    assert await runs.should_stop("caller-thread") is True
    assert await runs.should_stop("person-thread") is False
    assert await runs.should_stop("broken") is False
    assert await runs.should_stop("no-run") is False
    assert asked == ["caller-thread", "person-thread", "broken"]
    assert "whether its caller canceled could not be read" in caplog.text
    held.set()
    await runs.close()
