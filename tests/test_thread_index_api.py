"""Feature 007, the index's two routes: ``GET /threads`` and ``GET /threads/{thread_id}``
(specs/007-web-tasks/contracts/openapi.yaml), on apps assembled the production way.

FR-002 to FR-005, FR-023, SC-009, SC-010. The runs here are started through the routes that
exist since feature 001 and by the delegation worker: the index learns of them through the
run lease, whoever starts them.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.api import thread_index as api
from freyja_agent.runtime.thread_index import FIRST_MESSAGE_LIMIT, InMemoryThreadIndex, Settled
from tests.conftest import A2aApp, McpApp, indexed_locks
from tests.fakes import FakeClock, LabelledChatModel, ScriptedChatModel
from tests.test_a2a_pending import TASK, go_pending, handoff, pending_app, stub, tick, wait_for

NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)
LS = AIMessage(content="", tool_calls=[{"id": "l1", "name": "ls", "args": {"path": "/"}}])
AT = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


def run(client: TestClient, thread: str, message: str, sse_events: Any) -> list[tuple[str, Any]]:
    with client.stream("POST", f"/threads/{thread}/runs", json={"message": message}) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def listed(client: TestClient) -> dict[str, Any]:
    response = client.get("/threads")
    assert response.status_code == 200, response.text
    return response.json()


def row(client: TestClient, thread: str) -> dict[str, Any]:
    response = client.get(f"/threads/{thread}")
    assert response.status_code == 200, response.text
    return response.json()


def idle(client: TestClient, thread: str) -> None:
    """Wait until the thread's run has given its lease back."""
    deadline = time.monotonic() + 5
    while client.get(f"/threads/{thread}/state").json().get("run_active"):
        assert time.monotonic() < deadline, f"a run is still active on {thread}"
        time.sleep(0.02)


def index_of(made: McpApp | A2aApp) -> InMemoryThreadIndex:
    index = made.runtime.threads
    assert isinstance(index, InMemoryThreadIndex)
    return index


def test_a_thread_that_ran_is_listed_with_how_it_began_and_where_it_stands(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(final_text="All done.")
    made = make_mcp_app(model, {})
    message = "Summarize the workspace. " + "x" * 300
    with TestClient(made.app) as client:
        assert listed(client) == {"threads": [], "needs_decision": 0, "total": 0, "more": False}

        run(client, "t-1", message, sse_events)
        calls = model.calls
        state = client.get("/threads/t-1/state").json()
        answer = listed(client)
        one = row(client, "t-1")
        # Neither route asked the model anything or changed the thread.
        assert model.calls == calls
        assert client.get("/threads/t-1/state").json() == state

    assert answer["needs_decision"] == 0
    assert (answer["total"], answer["more"]) == (1, False)
    assert answer["threads"] == [one]
    assert one["thread_id"] == "t-1"
    assert one["first_message"] == message[:FIRST_MESSAGE_LIMIT]
    assert (one["status"], one["error"]) == ("done", None)
    assert (one["stoppable"], one["stopping"]) == (False, False)
    # When it last changed is the thread's last recorded step, as its state says.
    assert datetime.fromisoformat(one["changed_at"]) == datetime.fromisoformat(state["updated_at"])
    assert datetime.fromisoformat(one["created_at"]) <= datetime.fromisoformat(one["changed_at"])


def test_reading_the_list_and_a_row_contacts_no_tool_server(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    """FR-023: answering the list or the number contacts no model, no agent, and no tool
    server. The model and the agent are held to it above and below; this is the third."""
    model = ScriptedChatModel(final_text="All done.")
    made = make_mcp_app(model, {"stub": {"command": "stub-command"}})
    with TestClient(made.app) as client:
        run(client, "t-1", "Summarize the workspace.", sse_events)
        link = made.links["stub"]
        # The server was reached at startup, to list its tools; that is what "before" holds.
        before = (link.connections, list(link.stub.calls))
        assert before[0] >= 1
        calls = model.calls

        answer: dict[str, Any] = {}
        for _ in range(3):
            answer = listed(client)
            row(client, "t-1")

        assert answer["total"] == 1
        assert (link.connections, list(link.stub.calls)) == before
        assert model.calls == calls


def test_a_paused_thread_needs_a_decision_until_it_is_decided(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."), {})
    with TestClient(made.app) as client:
        events = run(client, "t-1", "Tell #ops.", sse_events)
        assert events[-1][0] == "interrupt"
        paused = listed(client)
        assert paused["needs_decision"] == 1
        assert [t["status"] for t in paused["threads"]] == ["needs_decision"]

        body = {"interrupt_id": events[-1][1]["interrupt_id"], "decisions": [{"type": "approve"}]}
        with client.stream("POST", "/threads/t-1/resume", json=body) as response:
            assert sse_events(response.iter_lines())[-1] == ("end", {"status": "completed"})
        decided = listed(client)

    assert decided["needs_decision"] == 0
    assert [t["status"] for t in decided["threads"]] == ["done"]


def test_a_run_whose_reader_left_is_stopped(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[LS, LS, LS, LS, LS]), {})
    with TestClient(made.app) as client:
        with client.stream("POST", "/threads/t-1/runs", json={"message": "hi"}) as response:
            for line in response.iter_lines():
                if line == "event: update":
                    break
        idle(client, "t-1")
        one = row(client, "t-1")

    assert (one["status"], one["error"], one["stoppable"]) == ("stopped", None, False)
    assert one["first_message"] == "hi"


def test_a_run_in_progress_is_running_and_cannot_be_stopped(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    model = LabelledChatModel(final_text="done")
    made = make_mcp_app(model, {})
    outcome: dict[str, Any] = {}

    def stream() -> None:
        outcome["events"] = run(client, "t-1", "slow", sse_events)

    with TestClient(made.app) as client:
        model.hold()
        worker = threading.Thread(target=stream)
        worker.start()
        with model.held():
            one = row(client, "t-1")
            during = listed(client)
            kept = client.portal.call(index_of(made).get, "t-1")
        worker.join(timeout=10)
        after = row(client, "t-1")

    # Reading a thread whose run is in progress leaves its row alone: the run is still the
    # row's, and nothing was read of a state that is still changing.
    assert kept.run_owner is not None and kept.settled is None
    assert kept.run_owner.startswith("instance-a:")
    # A run through the route that exists since feature 001 ends when its reader leaves, so
    # it has no stop of its own.
    assert (one["status"], one["stoppable"], one["stopping"]) == ("running", False, False)
    assert [t["status"] for t in during["threads"]] == ["running"]
    assert during["needs_decision"] == 0
    assert outcome["events"][-1] == ("end", {"status": "completed"})
    assert after["status"] == "done"
    assert datetime.fromisoformat(after["changed_at"]) >= datetime.fromisoformat(one["changed_at"])


def test_a_thread_waits_for_an_agent_until_the_outcome_was_delivered(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")], final_text="The report is ready.")
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        go_pending(client, "t-1", sse_events)
        asked = len(made.transports["stub"].requests)
        waiting = listed(client)
        row(client, "t-1")
        # Neither route asked the agent anything.
        assert len(made.transports["stub"].requests) == asked

        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        tick(client, made.runtime)
        delivered = listed(client)

    assert [t["status"] for t in waiting["threads"]] == ["waiting_for_agent"]
    assert waiting["needs_decision"] == 0
    # The outcome was delivered by a run of the service's own, which the index saw as well.
    assert [t["status"] for t in delivered["threads"]] == ["done"]


def test_a_run_that_recorded_nothing_leaves_no_thread(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        # A resume on a thread that was never used takes the lease and is refused.
        body = {"interrupt_id": "x", "decisions": [{"type": "approve"}]}
        assert client.post("/threads/never/resume", json=body).status_code == 404
        answer = listed(client)
        missing = client.get("/threads/never")

    assert (answer["threads"], answer["total"]) == ([], 0)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "thread_not_found"


def test_a_run_that_died_before_it_recorded_a_step_leaves_the_thread_as_it_was(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, clock: FakeClock
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        run(client, "t-1", "hi", sse_events)
        listed(client)
        # Another instance took the lease for a run and died before it recorded a step.
        assert client.portal.call(made.runtime.locks.acquire, "t-1", "instance-b:dead") is True
        assert row(client, "t-1")["status"] == "running"

        clock.advance(made.settings.run_lock_ttl_seconds + 1)
        after = listed(client)
        kept = client.portal.call(index_of(made).get, "t-1")

    # The thread's state has no step left to take, so it is as it was before that run. The
    # row still names the dead run; the lease is what says that no run is in progress.
    assert [t["status"] for t in after["threads"]] == ["done"]
    assert (kept.run_owner, kept.settled) == ("instance-b:dead", "finished")


def test_a_run_that_died_in_the_middle_is_stopped_once_its_lease_expired(
    make_mcp_app: Callable[..., McpApp], clock: FakeClock
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[LS, LS, LS]), {})
    with TestClient(made.app) as client:
        # A run that recorded its input and no more, as one whose reader left.
        with client.stream("POST", "/threads/t-1/runs", json={"message": "hi"}) as response:
            for line in response.iter_lines():
                if line == "event: update":
                    break
        idle(client, "t-1")
        # The next run on it died with its instance: the row still names it.
        assert client.portal.call(made.runtime.locks.acquire, "t-1", "instance-b:dead") is True
        assert listed(client)["threads"][0]["status"] == "running"

        clock.advance(made.settings.run_lock_ttl_seconds + 1)
        after = listed(client)
        kept = client.portal.call(index_of(made).get, "t-1")

    assert [t["status"] for t in after["threads"]] == ["stopped"]
    assert (kept.run_owner, kept.settled) == ("instance-b:dead", "unfinished")


def test_a_thread_the_index_does_not_know_keeps_its_first_message_when_a_console_run_continues_it(
    make_mcp_app: Callable[..., McpApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-002, FR-004: a thread from before the index, or one whose row could not be
    written, that a client continues through the console's route without opening it first.
    The run's text is a follow-up, not how the thread began."""
    made = make_mcp_app(ScriptedChatModel(final_text="Answered."), {})
    with TestClient(made.app) as client:
        run(client, "old", "From before the index.", sse_events)
        index_of(made)._rows.clear()

        with client.stream(
            "POST", "/threads/old/runs/unattended", json={"message": "And one thing more."}
        ) as response:
            events = sse_events(response.iter_lines())
        one = row(client, "old")
        after = listed(client)

        # A thread that is new is listed under the run's text, as before.
        with client.stream(
            "POST", "/threads/new/runs/unattended", json={"message": "A new task."}
        ) as response:
            sse_events(response.iter_lines())
        new = row(client, "new")

    assert events[-1][0] == "end"
    assert (one["first_message"], one["status"], one["runs"]) == (
        "From before the index.",
        "done",
        1,
    )
    assert [t["first_message"] for t in after["threads"]] == ["From before the index."]
    assert new["first_message"] == "A new task."


def test_a_console_run_starts_all_the_same_when_the_thread_cannot_be_looked_up_first(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-023: keeping the list never fails or changes a run."""
    made = make_mcp_app(ScriptedChatModel(final_text="Answered."), {})
    with TestClient(made.app) as client:
        index = index_of(made)
        real = index.get
        failed: list[str] = []

        async def fails_once(thread_id: str) -> Any:
            if not failed:
                failed.append(thread_id)
                raise RuntimeError("the index is away")
            return await real(thread_id)

        monkeypatch.setattr(index, "get", fails_once)
        with (
            caplog.at_level(logging.WARNING, logger="freyja_agent.api.thread_index"),
            client.stream(
                "POST", "/threads/t-1/runs/unattended", json={"message": "Say hello."}
            ) as response,
        ):
            assert response.status_code == 200
            events = sse_events(response.iter_lines())
        one = row(client, "t-1")

    assert failed == ["t-1"]
    assert events[-1] == ("end", {"status": "completed"})
    assert (one["first_message"], one["status"]) == ("Say hello.", "done")
    assert "Thread t-1: its row could not be looked up before its run" in caplog.text


@pytest.mark.parametrize("how", ["fails", "hangs"])
def test_a_thread_whose_lookup_failed_keeps_its_own_first_message(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    """FR-002, T102: when the thread cannot be opened before a console run, the run's text
    is not taken for how a thread began that has begun already."""
    made = make_mcp_app(ScriptedChatModel(final_text="Answered."), {})
    monkeypatch.setattr(api, "INDEX_WRITE_SECONDS", 0.05)
    with TestClient(made.app) as client:
        run(client, "old", "From before the index.", sse_events)
        index = index_of(made)
        index._rows.clear()
        real = index.get
        tried: list[str] = []

        async def fails_once(thread_id: str) -> Any:
            if not tried:
                tried.append(thread_id)
                if how == "hangs":
                    await asyncio.Event().wait()
                raise RuntimeError("the index is away")
            return await real(thread_id)

        monkeypatch.setattr(index, "get", fails_once)
        with client.stream(
            "POST", "/threads/old/runs/unattended", json={"message": "And one thing more."}
        ) as response:
            events = sse_events(response.iter_lines())
        one = row(client, "old")
        state = client.get("/threads/old/state").json()

    assert tried == ["old"]
    assert events[-1] == ("end", {"status": "completed"})
    assert one["first_message"] == "From before the index."
    assert one["status"] == "done"
    # The follow-up is in the conversation, where it belongs.
    assert [m["content"] for m in state["messages"] if m["type"] == "human"] == [
        "From before the index.",
        "And one thing more.",
    ]


def test_a_console_run_starts_when_the_lookup_before_it_never_answers(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-023: a list that hangs holds a run up for a bounded time, and never fails it."""
    made = make_mcp_app(ScriptedChatModel(final_text="Answered."), {})
    monkeypatch.setattr(api, "INDEX_WRITE_SECONDS", 0.05)
    with TestClient(made.app) as client:
        index = index_of(made)
        real = index.get
        hung: list[str] = []

        async def hangs_once(thread_id: str) -> Any:
            if not hung:
                hung.append(thread_id)
                await asyncio.Event().wait()
            return await real(thread_id)

        monkeypatch.setattr(index, "get", hangs_once)
        began = time.monotonic()
        with (
            caplog.at_level(logging.WARNING, logger="freyja_agent.api.thread_index"),
            client.stream(
                "POST", "/threads/t-1/runs/unattended", json={"message": "Say hello."}
            ) as response,
        ):
            assert response.status_code == 200
            events = sse_events(response.iter_lines())
        took = time.monotonic() - began
        held = sum(made.runtime.registry._holders.values())
        one = row(client, "t-1")

    assert hung == ["t-1"]
    assert events[-1] == ("end", {"status": "completed"})
    assert took < 2
    # No context was left taken by the lookup that was given up.
    assert held == 0
    assert (one["first_message"], one["status"]) == ("Say hello.", "done")
    assert "Thread t-1: its row could not be looked up before its run" in caplog.text


def test_a_thread_the_index_does_not_know_is_listed_once_it_was_opened(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY]), {})
    with TestClient(made.app) as client:
        run(client, "old", "From before the index.", sse_events)
        # As if the thread had run before this feature was installed.
        index_of(made)._rows.clear()
        assert listed(client)["threads"] == []

        opened = row(client, "old")
        after = listed(client)

    assert (opened["status"], opened["first_message"]) == (
        "needs_decision",
        "From before the index.",
    )
    assert opened["created_at"] == opened["changed_at"]
    assert after["threads"] == [opened]
    assert after["needs_decision"] == 1


def test_a_thread_opened_during_a_run_the_index_did_not_see_is_running(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        run(client, "t-1", "hi", sse_events)
        index_of(made)._rows.clear()
        # A run holds the lease that the index was not told of.
        inner = indexed_locks(made.runtime).inner
        assert client.portal.call(inner.acquire, "t-1", "instance-b:unseen") is True
        during = row(client, "t-1")
        assert listed(client)["threads"][0]["status"] == "running"

        # It ends; the row says nothing of it and is looked at again.
        client.portal.call(made.runtime.locks.release, "t-1", "instance-b:unseen")
        after = row(client, "t-1")

    assert (during["status"], during["stoppable"]) == ("running", False)
    assert after["status"] == "done"


def test_every_run_that_ended_is_settled_by_one_reading_and_counted(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY]), {})
    with TestClient(made.app) as client:
        with client.stream("POST", "/threads/paused/runs", json={"message": "hi"}) as response:
            response.read()
        index = index_of(made)
        # Sixty more runs ended on that thread's twin rows since the list was last read.
        for number in range(60):
            client.portal.call(index.run_started, f"ghost-{number}", "instance-a:r")
            client.portal.call(index.run_ended, f"ghost-{number}", "instance-a:r")
        assert len(client.portal.call(index.unsettled)) == 61

        answer = listed(client)
        left = client.portal.call(index.unsettled)

    # The ghosts recorded nothing, so they are no threads; the one real thread is counted.
    assert left == []
    assert (answer["needs_decision"], answer["total"]) == (1, 1)


def test_the_list_puts_decisions_first_then_the_newest_up_to_one_hundred_rows(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        index = index_of(made)

        def seed(thread: str, settled: Settled, minutes: int) -> None:
            client.portal.call(
                lambda: index.open(
                    thread,
                    settled=settled,
                    created_at=AT,
                    changed_at=AT + timedelta(minutes=minutes),
                    first_message=thread,
                    agents_pending=False,
                )
            )

        for number in range(120):
            seed(f"done-{number:03d}", "finished", number)
        for number in range(3):
            seed(f"paused-{number}", "paused", -number)

        started = time.monotonic()
        answer = listed(client)
        took = time.monotonic() - started

    ids = [t["thread_id"] for t in answer["threads"]]
    assert len(ids) == 100
    assert ids[:3] == ["paused-0", "paused-1", "paused-2"]
    assert ids[3:6] == ["done-119", "done-118", "done-117"]
    assert ids[-1] == "done-023"
    assert (answer["needs_decision"], answer["total"], answer["more"]) == (3, 123, True)
    # SC-009 asks for two seconds with a hundred threads; in memory it is far below that.
    assert took < 2


def test_more_decisions_than_the_limit_are_all_listed(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        index = index_of(made)
        for number in range(104):
            client.portal.call(
                lambda number=number: index.open(
                    f"paused-{number:03d}",
                    settled="paused",
                    created_at=AT,
                    changed_at=AT + timedelta(minutes=number),
                    first_message="m",
                    agents_pending=False,
                )
            )
        client.portal.call(
            lambda: index.open(
                "done",
                settled="finished",
                created_at=AT,
                changed_at=AT + timedelta(days=1),
                first_message="m",
                agents_pending=False,
            )
        )
        answer = listed(client)

    assert len(answer["threads"]) == 104
    assert {t["status"] for t in answer["threads"]} == {"needs_decision"}
    assert (answer["needs_decision"], answer["total"], answer["more"]) == (104, 105, True)


def test_a_request_that_took_the_lease_and_was_refused_changes_no_status(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY]), {})
    with TestClient(made.app) as client:
        run(client, "t-1", "Tell #ops.", sse_events)
        assert listed(client)["needs_decision"] == 1

        # A new message on a paused thread takes the lease, is refused, and gives it back.
        refused = client.post("/threads/t-1/runs", json={"message": "again"})
        assert (refused.status_code, refused.json()["detail"]["code"]) == (409, "decision_pending")
        after_message = listed(client)

        # So does anything else that takes the lease and finds nothing to do, such as the
        # delegation worker when a thread paused just before it looked.
        assert client.portal.call(made.runtime.locks.acquire, "t-1", "instance-a:w") is True
        client.portal.call(made.runtime.locks.release, "t-1", "instance-a:w")
        after_worker = listed(client)
        one = row(client, "t-1")

    for answer in (after_message, after_worker):
        assert answer["needs_decision"] == 1
        assert [t["status"] for t in answer["threads"]] == ["needs_decision"]
    assert one["status"] == "needs_decision"


def test_a_row_nobody_looked_at_is_never_shown_with_a_status_of_its_own(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    """The moment after a run ends: its lease is gone and the index was not told yet."""
    made = make_mcp_app(ScriptedChatModel(script=[NOTIFY]), {})
    with TestClient(made.app) as client:
        run(client, "t-1", "Tell #ops.", sse_events)
        index = index_of(made)
        ended = client.portal.call(index.get, "t-1")
        assert ended is not None
        # Put the row back to where it was between the lease's release and ``run_ended``.
        owner = "instance-a:just-ended"
        index._rows["t-1"] = replace(ended, run_owner=owner, settled=None)

        between = listed(client)
        one = row(client, "t-1")
        # The run's own end lands afterwards, and must not undo what was found.
        client.portal.call(index.run_ended, "t-1", owner)
        after = listed(client)

    for answer in (between, after):
        assert answer["needs_decision"] == 1
        assert [t["status"] for t in answer["threads"]] == ["needs_decision"]
    assert one["status"] == "needs_decision"


def test_a_row_that_was_kept_wrong_is_put_right_once_its_thread_is_opened(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(final_text="Done."), {})
    with TestClient(made.app) as client:
        run(client, "t-1", "hi", sse_events)
        assert listed(client)["needs_decision"] == 0
        # As when the index was told of neither the start nor the end of a later run.
        index = index_of(made)
        stale = client.portal.call(index.get, "t-1")
        assert stale is not None
        index._rows["t-1"] = replace(stale, settled="paused")
        assert listed(client)["needs_decision"] == 1

        opened = row(client, "t-1")
        after = listed(client)

    assert opened["status"] == "done"
    assert (after["needs_decision"], after["threads"][0]["status"]) == (0, "done")


def test_one_thread_that_cannot_be_looked_at_does_not_take_the_list_away(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = make_mcp_app(ScriptedChatModel(final_text="Done."), {})
    with TestClient(made.app) as client:
        run(client, "good", "hi", sse_events)
        run(client, "bad", "hello", sse_events)
        looked = api.settle_thread

        async def failing(index: Any, context: Any, tasks: Any, thread_id: str, runs: int) -> Any:
            if thread_id == "bad":
                raise ValueError("text fields cannot contain NUL: postgresql://user:secret@db")
            return await looked(index, context, tasks, thread_id, runs)

        monkeypatch.setattr(api, "settle_thread", failing)
        first = listed(client)
        again = listed(client)
        one = client.get("/threads/bad")
        monkeypatch.setattr(api, "settle_thread", looked)
        healed = listed(client)

    # The thread that could be read is listed, every time; the other is left out and said.
    for answer in (first, again):
        assert [t["thread_id"] for t in answer["threads"]] == ["good"]
        assert (answer["total"], answer["more"]) == (2, True)
    assert one.status_code == 500
    assert "Thread bad could not be looked at" in caplog.text
    assert "secret" not in caplog.text
    assert sorted(t["thread_id"] for t in healed["threads"]) == ["bad", "good"]


def test_a_message_with_a_character_the_database_refuses_is_listed(
    make_mcp_app: Callable[..., McpApp], sse_events: Any
) -> None:
    made = make_mcp_app(ScriptedChatModel(final_text="Done."), {})
    with TestClient(made.app) as client:
        run(client, "t-1", "\u0000 hi", sse_events)
        answer = listed(client)

    assert [(t["first_message"], t["status"]) for t in answer["threads"]] == [(" hi", "done")]


@pytest.mark.parametrize("thread", ["...", "a b", "x" * 129])
def test_an_id_the_service_does_not_accept_is_refused(
    make_mcp_app: Callable[..., McpApp], thread: str
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        assert client.get(f"/threads/{thread}").status_code == 422


def test_an_index_that_cannot_be_read_is_an_error_and_never_an_earlier_list(
    make_mcp_app: Callable[..., McpApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(made.app) as client:
        run(client, "t-1", "hi", sse_events)
        assert listed(client)["total"] == 1

        async def away(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("connection to postgresql://user:secret@db is gone")

        index = index_of(made)
        monkeypatch.setattr(index, "list", away)
        monkeypatch.setattr(index, "get", away)
        whole = client.get("/threads")
        one = client.get("/threads/t-1")

    for response in (whole, one):
        assert response.status_code == 500
        assert response.json() == {
            "detail": {"code": "internal", "message": "The list of threads could not be read."}
        }
    # The failure is logged by its kind, without the text an error of the database carries.
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text
