"""009: a received task over time (FR-001, FR-012, FR-016, SC-014, research R6, R7, R9).

Part 1, the feature off while callers' threads exist: such a thread is not run. A message
and every decision are refused with ``a2a_server_off``, the worker leaves it alone, the
graph refuses it, and its state is still read; a console thread runs as before. An instance
that is off asks who started a thread every time, so one started before any conversation
existed refuses it too.

Part 2, where a thread stands and ending a task: one test per row of research R7's table, the
three fences, the minute a task may wait for its run, and the function every release calls.

Part 3, conversations. Part 4, following a task (FR-017, research R13). Part 5, a task after
a restart (FR-012, SC-010). Part 6, a cancel of a run (FR-021, SC-011), as
``tests/test_unattended_stop.py`` stops runs. Part 7, a cancel and a proposal (FR-021,
FR-022, FR-022a, research R11). Part 8, releases and the sweep (research R9).
"""

import asyncio
import functools
import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.api import received as received_module
from freyja_agent.api import runs as runs_module
from freyja_agent.api.received import Ended, ReceivedTasks, RefusedError
from freyja_agent.api.runs import start_resume
from freyja_agent.api.schemas import ResumeRequest
from freyja_agent.runtime import unattended as unattended_module
from freyja_agent.runtime.received_tasks import InMemoryReceivedTaskStore
from freyja_agent.runtime.unattended import CallerThreadError
from tests.a2a_caller import StandInCaller, as_json
from tests.a2a_requests import ADDRESS, at_rest, ended, rpc, sending, text_of
from tests.a2a_stub import make_stub
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import ServerApp, detail_of, indexed_locks, read_events
from tests.fakes import (
    FailingChatModel,
    LabelledChatModel,
    LoopingChatModel,
    ScriptedChatModel,
    StreamingChatModel,
)
from tests.unattended_helpers import Gate, Reader, held_server, hold_call, ls, stream

NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)
NOTIFY_AGAIN = AIMessage(
    content="",
    tool_calls=[
        {"id": "n2", "name": "send_notification", "args": {"channel": "#ops", "message": "again"}}
    ],
)
DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Tell #ops.", "subagent_type": "general-purpose"},
        }
    ],
)
HAND_OFF = AIMessage(
    content="",
    tool_calls=[
        {"id": "h1", "name": "handoff_to_stub", "args": {"message": "hold on", "task_id": None}}
    ],
)


def pending_of(client: TestClient, thread: str) -> dict[str, Any]:
    pending = client.get(f"/threads/{thread}/state").json()["pending"]
    assert pending is not None
    return pending


def decision(client: TestClient, thread: str, kind: str) -> dict[str, Any]:
    pending = pending_of(client, thread)
    return {
        "interrupt_id": pending["interrupt_id"],
        "decisions": [{"type": kind} for _ in pending["action_requests"]],
    }


# Part 1: the feature off.


def test_with_the_feature_off_a_caller_s_thread_is_read_and_not_run(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[NOTIFY], final_text="Answered.")
    on = make_a2a_server_app(model)
    # Started before any conversation exists, as during a rolling restart that turns the
    # feature on: it asks every time all the same.
    off = on.again(a2a_server=False)
    with TestClient(off.app) as off_client, TestClient(on.app) as on_client:
        accepted = on.accept(on_client, "Tell #ops.")
        on.finish(on_client, accepted)
        thread = accepted.task.thread_id
        pending = pending_of(off_client, thread)
        calls = model.calls
        refusals = [
            off_client.post(f"/threads/{thread}/runs", json={"message": "more"}),
            off_client.post(f"/threads/{thread}/runs/unattended", json={"message": "more"}),
            *(
                off_client.post(
                    f"/threads/{thread}/{path}", json=decision(off_client, thread, kind)
                )
                for path in ("resume", "resume/unattended")
                for kind in ("approve", "reject")
            ),
        ]
        for answer in refusals:
            assert answer.status_code == 409, answer.text
            assert answer.json()["detail"]["code"] == "a2a_server_off"
        assert pending_of(off_client, thread) == pending
        assert off_client.get("/threads").status_code == 200

        acquired: list[str] = []
        locks = off.runtime.locks
        taking = locks.acquire

        async def recorded(thread_id: str, owner: str) -> bool:
            acquired.append(thread_id)
            return await taking(thread_id, owner)

        locks.acquire = recorded
        try:
            off_client.portal.call(off.runtime.worker._deliver, thread)
        finally:
            locks.acquire = taking
        assert acquired == []

        async def run_on_off() -> None:
            context = await off.runtime.begin_run(thread)
            try:
                config = {"configurable": {"thread_id": thread}}
                async for _chunk in context.agent.astream(None, config):
                    pass
            finally:
                await context.release()

        with pytest.raises(CallerThreadError):
            off_client.portal.call(run_on_off)
        assert model.calls == calls
        with off_client.stream("POST", "/threads/k-1/runs", json={"message": "hi"}) as answer:
            events = sse_events(answer.iter_lines())
    assert events[-1][0] == "end"


# Part 2: where a thread stands.


def standing(client: TestClient, made: ServerApp, task: Any) -> Any:
    return client.portal.call(made.service().standing, task)


def look(client: TestClient, made: ServerApp, task: Any) -> Any:
    return client.portal.call(made.service().look, task)


def test_a_task_whose_run_has_not_started_is_working_whatever_the_thread_says(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="An earlier reply."))
    with TestClient(made.app) as client:
        first = made.accept(client, "hello")
        made.finish(client, first)
        assert made.task(client, first.task.task_id).state == "completed"
        # A second message accepted, whose run has not taken the lease yet: the thread's
        # last reply is the earlier one, and the task is still working (fence 1).
        second = client.portal.call(
            lambda: made.received.accept("local", "c-1", hops=0, caller_limit=5, total_limit=20)
        ).task
        assert standing(client, made, second).state == "working"
        assert look(client, made, second).state == "working"


def test_a_held_lease_and_a_proposal_are_working(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY]))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task = made.task(client, accepted.task.task_id)
        found = standing(client, made, task)
        assert (found.state, found.waiting, found.names) == (
            "working",
            "person",
            ("send_notification",),
        )
        assert client.portal.call(made.runtime.locks.acquire, task.thread_id, "someone")
        assert standing(client, made, task) == standing(client, made, task)
        assert standing(client, made, task).waiting is None
        client.portal.call(made.runtime.locks.release, task.thread_id, "someone")
        assert look(client, made, task).state == "working"


def test_an_agent_task_awaited_is_working_and_names_the_agent(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    agent = make_stub("stub")
    made = make_a2a_server_app(
        ScriptedChatModel(script=[HAND_OFF, AIMessage(content="Handed over.")]),
        stubs={"stub": agent},
        # An agent that requires approval: the hand-off waits for a person (010 FR-007).
        read_only=False,
        a2a_call_timeout_seconds=0.3,
    )
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Ask the stub.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        with client.stream(
            "POST", f"/threads/{thread}/resume", json=decision(client, thread, "approve")
        ) as answer:
            sse_events(answer.iter_lines())
        client.portal.call(made.service().close)
        task = made.task(client, accepted.task.task_id)
        found = standing(client, made, task)
        assert (found.state, found.waiting, found.names) == ("working", "agent", ("stub",))
        assert task.state == "working"
        agent.release()


@pytest.mark.parametrize(
    ("model", "reason", "steps"),
    [
        (FailingChatModel(fail_on_call=1, error="the provider said no"), "model_error", 50),
        (LoopingChatModel(), "step_limit", 4),
    ],
    ids=["model_error", "step_limit"],
)
def test_a_run_that_failed_ends_its_task_as_failed_with_a_fixed_reason(
    make_a2a_server_app: Callable[..., ServerApp], model: Any, reason: str, steps: int
) -> None:
    made = make_a2a_server_app(model, max_steps=steps)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Try.")
        events = made.finish(client, accepted)
        task = made.task(client, accepted.task.task_id)
    assert events[-1][0] == "error"
    assert (task.state, task.reason, task.result) == ("failed", reason, None)
    assert task.released_at is not None
    assert "the provider said no" not in repr(task)


def test_a_console_run_stopped_between_two_steps_ends_its_task_as_canceled(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = LabelledChatModel(
        script=[AIMessage(content="", tool_calls=[{"id": "l1", "name": "ls", "args": {}}])]
    )
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        model.hold()
        accepted = made.accept(client, "List.")
        with model.held():
            asked = client.post(f"/threads/{accepted.task.thread_id}/stop")
            assert asked.status_code == 202, asked.text
        made.finish(client, accepted)
        task = made.task(client, accepted.task.task_id)
    assert (task.state, task.reason) == ("canceled", "stopped")
    assert task.released_at is not None


def test_a_thread_that_recorded_nothing_since_its_task_started_ends_it_as_interrupted(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="First."))
    with TestClient(made.app) as client:
        first = made.accept(client, "hello")
        made.finish(client, first)

        async def started_and_died() -> Any:
            # As when the process died after the run took the lease and before its step.
            context = await made.runtime.begin_read(first.task.thread_id)
            snapshot = await context.agent.aget_state(
                {"configurable": {"thread_id": first.task.thread_id}}
            )
            await context.release()
            task = (
                await made.received.accept("local", "c-1", hops=0, caller_limit=5, total_limit=20)
            ).task
            checkpoint = snapshot.config["configurable"]["checkpoint_id"]
            await made.received.mark_started(task.task_id, datetime.now(UTC), checkpoint)
            return await made.received.get(task.task_id)

        task = client.portal.call(started_and_died)
        assert (standing(client, made, task).state, standing(client, made, task).reason) == (
            "failed",
            "interrupted",
        )
        ended = look(client, made, task)
    assert (ended.state, ended.reason) == ("failed", "interrupted")


def test_an_earlier_run_s_note_is_not_this_task_s(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = FailingChatModel(fail_on_call=1, error="once")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        first = made.accept(client, "Fails.")
        made.finish(client, first)
        assert made.task(client, first.task.task_id).reason == "model_error"
        second = made.accept(client, "Works now.")
        made.finish(client, second)
        task = made.task(client, second.task.task_id)
    assert (task.state, task.result) == ("completed", "Done.")


def test_two_looks_at_once_write_one_end_and_a_moved_thread_writes_none(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    with TestClient(made.app) as client:
        service = made.service()
        indexed_locks(made.runtime).on_release = None  # no look by the release: the test looks
        accepted = made.accept(client, "hello")
        made.finish(client, accepted)
        task = made.task(client, accepted.task.task_id)
        assert task.state == "working"

        # A run takes the lease between the look and the write: nothing is written.
        real = service.standing

        async def then_a_run_starts(found: Any) -> Any:
            result = await real(found)
            await made.runtime.locks.acquire(found.thread_id, "a-run")
            return result

        service.standing = then_a_run_starts
        try:
            assert look(client, made, task).state == "working"
        finally:
            service.standing = real
            client.portal.call(made.runtime.locks.release, task.thread_id, "a-run")

        async def twice() -> list[Any]:
            return list(await asyncio.gather(service.look(task), service.look(task)))

        with caplog.at_level("INFO", logger="freyja_agent.api.received"):
            both = client.portal.call(twice)
    assert [found.state for found in both] == ["completed", "completed"]
    assert sum("ended, completed" in line for line in caplog.messages) == 1


def test_a_task_whose_run_never_started_fails_after_a_minute(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    now = [datetime(2026, 11, 25, 10, 0, tzinfo=UTC)]
    store = InMemoryReceivedTaskStore(clock=lambda: now[0])
    made = make_a2a_server_app(ScriptedChatModel(), received=store)
    with TestClient(made.app) as client:
        task = client.portal.call(
            lambda: store.accept("local", "c-9", hops=0, caller_limit=5, total_limit=20)
        ).task
        now[0] += timedelta(seconds=59)
        assert look(client, made, task).state == "working"
        now[0] += timedelta(seconds=2)
        ended = look(client, made, task)
    assert (ended.state, ended.reason) == ("failed", "interrupted")
    assert ended.released_at is not None


def test_accepting_looks_first_at_the_tasks_that_still_count(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(
        ScriptedChatModel(final_text="Hi."), a2a_server_caller_tasks=1, a2a_server_tasks=1
    )
    with TestClient(made.app) as client:
        indexed_locks(made.runtime).on_release = None  # the release function was lost
        first = made.accept(client, "hello", context="a")
        made.finish(client, first)
        assert made.task(client, first.task.task_id).state == "working"
        second = made.accept(client, "hello", context="b")
        made.finish(client, second)
    assert second.task.task_id != first.task.task_id


def test_the_release_function_runs_for_every_kind_of_run_and_holds_nothing_up(
    make_a2a_server_app: Callable[..., ServerApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    with TestClient(made.app) as client:
        service: ReceivedTasks = made.service()
        accepted = made.accept(client, "hello")
        made.finish(client, accepted)  # a console run
        looked: list[str] = []
        real = service.released

        async def recorded(thread_id: str) -> None:
            looked.append(thread_id)
            await real(thread_id)

        service.released = recorded
        with client.stream("POST", "/threads/plain-1/runs", json={"message": "hi"}) as answer:
            sse_events(answer.iter_lines())  # a run of the service's own route
        client.portal.call(service.close)
        assert looked == ["plain-1"]

        never = asyncio.Event()

        async def blocks(thread_id: str) -> None:
            await never.wait()

        async def raises(thread_id: str) -> None:
            raise RuntimeError("the store is down")

        service.released = blocks
        locks = made.runtime.locks

        async def take_and_give_back() -> bool:
            assert await locks.acquire("t-9", "me")
            await asyncio.wait_for(locks.release("t-9", "me"), 1)
            return await locks.is_active("t-9")

        assert client.portal.call(take_and_give_back) is False
        for look_task in list(service._looks):
            client.portal.call(lambda task=look_task: _cancel(task))
        service.released = raises
        with caplog.at_level("WARNING", logger="freyja_agent.api.received"):
            assert client.portal.call(take_and_give_back) is False
            client.portal.call(service.close)
    assert any("could not be looked at" in line for line in caplog.messages)


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_events_are_json(make_a2a_server_app: Callable[..., ServerApp]) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    with TestClient(made.app) as client:
        events = made.finish(client, made.accept(client, "hello"))
    assert events[0][0] == "start"
    assert json.dumps(events)


def test_which_agent_tasks_a_received_task_waits_for() -> None:
    """Research R7: working, an outcome not in the thread, or a run still to be tried; a
    question already reported is not waited for."""
    from freyja_agent.api.runs import awaited
    from freyja_agent.runtime.agent_tasks import AgentTaskRecord

    now = datetime(2026, 11, 25, tzinfo=UTC)
    base = AgentTaskRecord(
        agent="stub",
        task_id="t",
        thread_id="th",
        context_id="c",
        message="m",
        state="working",
        started_at=now,
        last_checked_at=now,
    )
    assert awaited(base)
    ready = base.__class__(**{**base.__dict__, "state": "completed", "outcome_ready": True})
    assert awaited(ready)
    delivered = ready.__class__(**{**ready.__dict__, "delivered_at": now})
    assert not awaited(delivered)
    retrying = delivered.__class__(**{**delivered.__dict__, "followup_next_at": now})
    assert awaited(retrying)
    asked = base.__class__(
        **{**base.__dict__, "state": "input_required", "outcome_ready": True, "delivered_at": now}
    )
    assert not awaited(asked)


def test_a_thread_left_with_a_next_step_ends_its_task_as_interrupted(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run cut off in the middle, as a run of the worker's is when the service stops: the
    thread has a next step and nothing in progress, so the task does not stay working."""
    from freyja_agent.api import received

    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    with TestClient(made.app) as client:
        indexed_locks(made.runtime).on_release = None
        accepted = made.accept(client, "hello")
        made.finish(client, accepted)
        task = made.task(client, accepted.task.task_id)

        cut = SimpleNamespace(
            interrupts=(),
            next=("tools",),
            values={"messages": [AIMessage(content="Half way.")]},
            config={"configurable": {"checkpoint_id": "moved-on"}},
            created_at=None,
        )

        async def cut_off(_context: Any, _thread_id: str) -> Any:
            return cut

        monkeypatch.setattr(received, "state_of", cut_off)
        found = standing(client, made, task)
        assert (found.state, found.reason) == ("failed", "interrupted")


def test_a_task_whose_run_wrote_no_reply_is_never_given_an_earlier_one(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel(script=[AIMessage(content="The first reply."), AIMessage("")])
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        first = made.accept(client, "hello")
        made.finish(client, first)
        second = made.accept(client, "again")
        made.finish(client, second)
        tasks = [made.task(client, found.task.task_id) for found in (first, second)]
    assert [(task.state, task.result) for task in tasks] == [
        ("completed", "The first reply."),
        ("completed", None),
    ]


def test_a_look_that_fails_keeps_its_task_counted_and_blocks_no_message(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    with TestClient(made.app) as client:
        service = made.service()
        indexed_locks(made.runtime).on_release = None
        first = made.accept(client, "hello", context="a")
        made.finish(client, first)

        async def broken(_task: Any) -> Any:
            raise RuntimeError("the thread cannot be read")

        service.look = broken
        with caplog.at_level("WARNING", logger="freyja_agent.api.received"):
            second = made.accept(client, "hello", context="b")
        made.finish(client, second)
    assert second.task.task_id != first.task.task_id
    assert any(first.task.task_id in line and "still counts" in line for line in caplog.messages)


# Part 3: conversations (FR-009, FR-010, FR-012).


def test_a_message_in_a_conversation_at_rest_is_a_new_task_on_the_same_thread(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel(final_text="Noted.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        first = rpc(client, "SendMessage", sending("My name is Ada.", contextId="c-1")).json()
        second = rpc(client, "SendMessage", sending("What is my name?", contextId="c-1")).json()
        one = made.task(client, first["result"]["task"]["id"])
        two = made.task(client, second["result"]["task"]["id"])
    assert one.task_id != two.task_id
    assert one.thread_id == two.thread_id
    assert second["result"]["task"]["contextId"] == "c-1"
    told = [str(message.content) for message in model.seen[-1]]
    assert "My name is Ada." in told and "What is my name?" in told


def busy_answer(client: TestClient, thread: str) -> dict[str, Any]:
    before = len(client.get(f"/threads/{thread}/state").json()["messages"])
    answer = rpc(client, "SendMessage", sending("Anything new?", contextId="c-1")).json()
    assert len(client.get(f"/threads/{thread}/state").json()["messages"]) == before
    return answer["error"]


BUSY = (
    "This conversation takes no message now: its last task has not ended, or work it started "
    "is still going on."
)


def test_a_message_is_refused_while_the_last_task_works(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = LabelledChatModel(final_text="Done.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        model.hold()
        accepted = made.accept(client, "Take your time.", context="c-1")
        held = client.portal.start_task_soon(read_events, accepted.events)
        with model.held():
            error = busy_answer(client, accepted.task.thread_id)
        held.result(timeout=5)
    assert (error["code"], error["message"]) == (-32004, BUSY)
    assert error["data"][0]["metadata"] == {"reason": "conversation_busy"}


def test_a_message_is_refused_while_a_proposal_is_pending_and_while_a_decision_runs(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    model = LabelledChatModel(script=[NOTIFY], final_text="Sent.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell ops.", context="c-1")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        pending = busy_answer(client, thread)
        # A person's decision runs: the lease is held.
        model.hold()
        body = decision(client, thread, "approve")
        held = client.portal.start_task_soon(
            functools.partial(read_events, _resume(client, thread, body))
        )
        with model.held():
            running = busy_answer(client, thread)
        held.result(timeout=5)
        client.portal.call(made.service().close)
        # Another proposal, whose task was ended by hand and not yet rejected by rule (part
        # 7): the conversation is at rest for the store, and the thread holds a proposal.
        model.script = [*model.script, NOTIFY_AGAIN]
        second = made.accept(client, "Tell ops again.", context="c-1")
        made.finish(client, second)

        async def not_yet(task: Any) -> Any:
            return task

        monkeypatch.setattr(made.service(), "_tidy", not_yet)
        client.portal.call(
            functools.partial(made.received.end, second.task.task_id, "canceled", reason="canceled")
        )
        on_the_thread = busy_answer(client, thread)
    for error in (pending, on_the_thread, running):
        assert error["data"][0]["metadata"] == {"reason": "conversation_busy"}


def _resume(client: TestClient, thread: str, body: dict[str, Any]) -> Any:
    runtime = cast(FastAPI, client.app).state.runtime

    async def start() -> Any:
        return await start_resume(runtime, thread, ResumeRequest.model_validate(body))

    return client.portal.call(start)


def test_two_messages_at_once_in_a_new_conversation_make_one_thread_and_one_refusal(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = LabelledChatModel(final_text="Done.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        model.hold()

        async def both() -> list[Any]:
            service = made.service()
            return await asyncio.gather(
                service.accept("local", "c-new", "one"),
                service.accept("local", "c-new", "two"),
                return_exceptions=True,
            )

        found = client.portal.call(both)
        accepted = [item for item in found if not isinstance(item, BaseException)]
        refused = [item for item in found if isinstance(item, BaseException)]
        held = client.portal.start_task_soon(read_events, accepted[0].events)
        with model.held():
            pass
        held.result(timeout=5)
        conversation = client.portal.call(made.received.conversation_of, accepted[0].task.thread_id)
    assert len(accepted) == len(refused) == 1
    assert isinstance(refused[0], RefusedError) and refused[0].why == "busy"
    assert conversation is not None and conversation.context_id == "c-new"


def test_a_conversation_id_never_used_starts_a_conversation(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    with TestClient(made.app) as client:
        one = rpc(client, "SendMessage", sending("a", contextId="first-id")).json()
        two = rpc(client, "SendMessage", sending("b", contextId="second-id")).json()
        threads = {
            made.task(client, found["result"]["task"]["id"]).thread_id for found in (one, two)
        }
    assert len(threads) == 2
    assert [found["result"]["task"]["contextId"] for found in (one, two)] == [
        "first-id",
        "second-id",
    ]


# Part 4: following (FR-017, research R13).

STREAM_LIMIT = "This task has as many streams as it takes. Read it with GetTask."
CANCELED = "The caller canceled the task."
STOPPED = "The run was stopped before it finished."
REJECTED = "The requesting agent canceled the task."
REQUESTER_GONE = (
    "The agent that asked for this canceled its task, so the action is not carried out. The "
    "proposal can only be rejected."
)
WAITING = "Waiting for a person's approval of: send_notification."


def until(found: Callable[[], Any], seconds: float = 5) -> Any:
    """``found()`` once it is true, waiting for it a bounded time."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = found()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("what the test waited for did not happen")


def readers(made: ServerApp, thread: str) -> int:
    """How many readers the console run in progress on ``thread`` has on this instance."""
    run = made.runtime.unattended._runs.get(thread)
    return len(run.readers) if run is not None else -1


def subscribed(client: TestClient, made: ServerApp, task_id: str) -> Future[list[dict]]:
    """A caller's subscription to ``task_id``, read to its end in the app's event loop."""

    async def work() -> list[dict]:
        async with StandInCaller(ADDRESS, transport=StreamingASGITransport(made.app)) as caller:
            return [as_json(event) async for event in caller.follow(task_id)]

    return client.portal.start_task_soon(work)


def first_of(client: TestClient, made: ServerApp, task_id: str) -> dict:
    """The first event of a subscription to ``task_id``; the stream is then left."""

    async def work() -> dict:
        async with StandInCaller(ADDRESS, transport=StreamingASGITransport(made.app)) as caller:
            async for event in caller.follow(task_id):
                return as_json(event)
        raise AssertionError("the subscription sent nothing")

    return client.portal.call(work)


def streams(made: ServerApp, task_id: str) -> int:
    return made.service()._streams.get(task_id, 0)


def statuses(events: list[dict]) -> list[str | None]:
    return [text_of(event) for event in events if "statusUpdate" in event]


def appended(events: list[dict]) -> list[str]:
    return [
        event["artifactUpdate"]["artifact"]["parts"][0]["text"]
        for event in events
        if event.get("artifactUpdate", {}).get("append")
    ]


def test_subscribing_gives_the_task_as_it_stands_and_every_change_to_its_end(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = LabelledChatModel(script=[ls("l1")], final_text="Listed.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        model.hold()
        accepted = made.accept(client, "List.")
        client.portal.call(made.service().leave, accepted.events)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        with model.held():
            following = subscribed(client, made, task_id)
            until(lambda: readers(made, thread) == 2)
        events = following.result(timeout=10)
        at_rest(client, made, task_id)
        after_end = rpc(client, "SubscribeToTask", {"id": task_id}).json()
        unknown = rpc(client, "SubscribeToTask", {"id": "0" * 32}).json()
        malformed = rpc(client, "SubscribeToTask", {"id": "t"}).json()
    assert events[0]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert text_of(events[0]) == "Working."
    assert statuses(events) == ["Freyja is using: ls.", "Completed: ls.", None]
    assert events[-2]["artifactUpdate"]["artifact"]["parts"][0]["text"] == "Listed."
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert after_end["error"]["code"] == -32004
    assert unknown["error"]["code"] == malformed["error"]["code"] == -32001
    assert streams(made, task_id) == 0


def test_a_fifth_stream_of_one_task_is_refused_and_a_fourth_is_not(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Not sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        four = [subscribed(client, made, task_id) for _ in range(4)]
        until(lambda: streams(made, task_id) == 4)
        fifth = rpc(client, "SubscribeToTask", {"id": task_id}).json()
        # A person rejects in the console; every stream follows the task to its end.
        decided = client.post(
            f"/threads/{thread}/resume/unattended", json=decision(client, thread, "reject")
        )
        followed = [stream.result(timeout=10) for stream in four]
    error = fifth["error"]
    assert (error["code"], error["message"]) == (-32004, STREAM_LIMIT)
    assert error["data"][0]["metadata"] == {"reason": "stream_limit"}
    assert decided.status_code == 200
    for events in followed:
        assert text_of(events[0]) == WAITING
        assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert streams(made, task_id) == 0


def test_a_follower_through_another_instance_gets_each_change_and_no_pieces(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(received_module, "FOLLOW_SECONDS", 0.05)
    model = StreamingChatModel(
        script=[NOTIFY], final_text="It is not sent.", pieces=["It is ", "not ", "sent."]
    )
    made = make_a2a_server_app(model)
    other = made.again()
    with TestClient(made.app) as client, TestClient(other.app) as elsewhere:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        following = subscribed(elsewhere, other, task_id)
        until(lambda: streams(other, task_id) == 1)
        decided = client.post(
            f"/threads/{thread}/resume/unattended", json=decision(client, thread, "reject")
        )
        events = following.result(timeout=10)
    assert decided.status_code == 200
    assert text_of(events[0]) == WAITING
    # The status the subscription began with is not sent again.
    assert WAITING not in statuses(events)
    assert appended(events) == []
    assert events[-2]["artifactUpdate"]["artifact"]["parts"][0]["text"] == "It is not sent."
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_a_follower_that_falls_behind_is_disconnected_and_the_run_goes_on(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(unattended_module, "READER_BACKLOG", 2)
    model = StreamingChatModel(
        script=[ls("l1")], final_text="one two three", pieces=["one ", "two ", "three"]
    )
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        model.hold()
        accepted = made.accept(client, "List.")
        changes = made.service().follow(accepted.task, accepted.events)

        async def read() -> list[Any]:
            async with asyncio.timeout(5):
                return [change async for change in changes]

        with model.held():
            until(lambda: readers(made, accepted.task.thread_id) == 0)
            got = client.portal.call(read)
        task = ended(client, made, accepted.task.task_id)
    assert not any(isinstance(change, Ended) for change in got)
    assert (task.state, task.result) == ("completed", "one two three")


# Part 5: restarts (FR-012, SC-010).


def test_a_task_reads_the_same_after_a_restart_and_is_followed_and_canceled_there(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    model = ScriptedChatModel(final_text="Hi.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        done = rpc(client, "SendMessage", sending("hello", contextId="c-1")).json()
        model.script = [NOTIFY]
        model._cursor = 0
        waiting = rpc(
            client,
            "SendMessage",
            {
                **sending("Tell #ops.", contextId="c-2"),
                "configuration": {"returnImmediately": True},
            },
        ).json()
        waiting_id = waiting["result"]["task"]["id"]
        at_rest(client, made, waiting_id)
    again = made.again()
    with caplog.at_level(logging.WARNING, logger=ACTIONS), TestClient(again.app) as client:
        read = rpc(client, "GetTask", {"id": done["result"]["task"]["id"]}).json()["result"]
        first = first_of(client, again, waiting_id)
        canceled = rpc(client, "CancelTask", {"id": waiting_id}).json()["result"]
        task = quiet(client, again, waiting_id)
        state = client.get(f"/threads/{task.thread_id}/state").json()
    assert read == done["result"]["task"] | {"status": read["status"]}
    assert read["status"]["state"] == "TASK_STATE_COMPLETED"
    assert read["artifacts"][0]["parts"][0]["text"] == "Hi."
    assert (first["task"]["id"], text_of(first)) == (waiting_id, WAITING)
    assert (canceled["status"]["state"], text_of(canceled)) == ("TASK_STATE_CANCELED", CANCELED)
    assert state["pending"] is None
    assert actions(caplog) == []


def test_a_caller_s_run_in_progress_at_shutdown_ends_stopped_and_a_follow_up_goes_on(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = LabelledChatModel(script=[ls("l1"), ls("l2")], final_text="Listed again.")
    made = make_a2a_server_app(model)
    model.hold()
    with TestClient(made.app) as client:
        answer = rpc(
            client,
            "SendMessage",
            {**sending("List.", contextId="c-1"), "configuration": {"returnImmediately": True}},
        ).json()
        task_id = answer["result"]["task"]["id"]
        assert model._reached.wait(5)
        # The service stops while the model is called; the call returns once shutdown began.
        threading.Timer(0.3, model._release.set).start()
    assert model.calls == 1
    again = made.again()
    with TestClient(again.app) as client:
        read = rpc(client, "GetTask", {"id": task_id}).json()["result"]
        time.sleep(0.2)
        idle_calls = model.calls
        model.script = []
        follow_up = rpc(client, "SendMessage", sending("Go on.", contextId="c-1")).json()
    assert read["status"]["state"] == "TASK_STATE_CANCELED"
    assert text_of(read) == STOPPED
    # Nothing ran by itself after the restart.
    assert idle_calls == 1
    assert follow_up["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    told = model.seen[-1]
    assert any(getattr(message, "tool_calls", None) for message in told)
    assert "List." in [str(message.content) for message in told]


# Part 6: a cancel of a run (FR-021, SC-011).

ACTIONS = "freyja_agent.actions"


def actions(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What the placeholder action did: each run of it logs one line."""
    return [record.getMessage() for record in caplog.records if record.name == ACTIONS]


def quiet(client: TestClient, made: ServerApp, task_id: str, seconds: float = 5) -> Any:
    """The task once it is released and nothing runs on its thread, the looks done."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        task = made.task(client, task_id)
        busy = client.portal.call(made.runtime.locks.is_active, task.thread_id)
        if task.released_at is not None and not busy and not made.service()._looks:
            client.portal.call(made.service().close)
            return made.task(client, task_id)
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} was not released")


def cancel(client: TestClient, task_id: str) -> dict[str, Any]:
    return rpc(client, "CancelTask", {"id": task_id}).json()


def test_a_cancel_during_a_model_call_keeps_the_reply_and_runs_none_of_its_tools(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="freyja_agent")
    model = LabelledChatModel(script=[ls("l1"), ls("l2")], final_text="Done.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        model.hold()
        accepted = made.accept(client, "List.")
        client.portal.call(made.service().leave, accepted.events)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        with model.held():
            answer = cancel(client, task_id)
        task = quiet(client, made, task_id)
        state = client.get(f"/threads/{thread}/state").json()
        row = client.get(f"/threads/{thread}").json()
    assert answer["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert text_of(answer["result"]) == CANCELED
    assert [message["type"] for message in state["messages"]] == ["human", "ai"]
    assert state["messages"][-1]["tool_calls"][0]["name"] == "ls"
    assert model.calls == 1
    assert (task.state, task.reason) == ("canceled", "canceled")
    assert row["status"] == "stopped"
    assert row["caller"]["canceled"] is True
    assert f"Caller local canceled received task {task_id}" in caplog.text


def test_a_cancel_during_a_tool_step_lets_the_tool_finish(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    gate = Gate()
    servers, stub = held_server(gate, gated=False)
    model = ScriptedChatModel(script=[hold_call(), ls("l2")])
    made = make_a2a_server_app(model, servers=servers, server_stubs={"work": stub})
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Work.")
        client.portal.call(made.service().leave, accepted.events)
        gate.wait()
        answer = cancel(client, accepted.task.task_id)
        gate.open()
        quiet(client, made, accepted.task.task_id)
        state = client.get(f"/threads/{accepted.task.thread_id}/state").json()
    assert text_of(answer["result"]) == CANCELED
    assert state["messages"][-1]["content"] == "work finished"
    assert gate.calls == 1
    assert model.calls == 1


def test_a_cancel_during_an_approved_action_lets_it_run_once_and_records_it(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    gate = Gate()
    servers, stub = held_server(gate, gated=True)
    model = ScriptedChatModel(script=[hold_call()], final_text="Done.")
    made = make_a2a_server_app(model, servers=servers, server_stubs={"work": stub})
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Work.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        reader = Reader(client, f"/threads/{thread}/resume/unattended", decision_of(client, thread))
        reader.start()
        gate.wait()
        answer = cancel(client, task_id)
        gate.open()
        events = reader.finish()
        quiet(client, made, task_id)
        state = client.get(f"/threads/{thread}/state").json()
    assert text_of(answer["result"]) == CANCELED
    assert events[-1] == ("end", {"status": "stopped"})
    assert gate.calls == 1
    assert state["pending"] is None
    assert state["messages"][-1]["content"] == "work finished"
    assert model.calls == 1


def decision_of(client: TestClient, thread: str) -> dict[str, Any]:
    return decision(client, thread, "approve")


def test_a_task_stays_canceled_when_its_run_ends_by_itself_before_the_stop(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = LabelledChatModel(final_text="All done.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        model.hold()
        accepted = made.accept(client, "Say it.")
        client.portal.call(made.service().leave, accepted.events)
        with model.held():
            answer = cancel(client, accepted.task.task_id)
        task = quiet(client, made, accepted.task.task_id)
        state = client.get(f"/threads/{accepted.task.thread_id}/state").json()
        read = rpc(client, "GetTask", {"id": task.task_id}).json()["result"]
    assert text_of(answer["result"]) == CANCELED
    assert state["messages"][-1]["content"] == "All done."
    assert (task.state, task.reason, task.result) == ("canceled", "canceled", None)
    assert (read["status"]["state"], text_of(read)) == ("TASK_STATE_CANCELED", CANCELED)


def test_a_cancel_of_a_task_that_ended_or_was_never_made_is_refused(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="freyja_agent")
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    with TestClient(made.app) as client:
        done = rpc(client, "SendMessage", sending("hello")).json()["result"]["task"]["id"]
        refused = [cancel(client, done), cancel(client, "0" * 32), cancel(client, "t")]
        task = made.task(client, done)
    assert [answer["error"]["code"] for answer in refused] == [-32002, -32001, -32001]
    assert task.state == "completed"
    assert "canceled received task" not in caplog.text


# Part 7: a cancel and a proposal (FR-021, FR-022, FR-022a, research R11).


def test_a_cancel_rejects_a_pending_proposal_by_rule_and_nothing_of_it_runs(
    make_a2a_server_app: Callable[..., ServerApp],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.INFO)
    model = ScriptedChatModel(script=[NOTIFY], final_text="Not sent.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        counted = client.get("/threads").json()["needs_decision"]
        late = decision(client, thread, "approve")
        calls = model.calls
        runs = made.runtime.unattended
        real_stop = runs.should_stop
        at_once: list[str] = []

        async def asked(thread_id: str) -> bool:
            # A person's approval while the rejection holds the lease is refused.
            try:
                await start_resume(made.runtime, thread, ResumeRequest.model_validate(late))
            except HTTPException as exc:
                at_once.append(detail_of(exc)["code"])
            return await real_stop(thread_id)

        monkeypatch.setattr(runs, "should_stop", asked)
        # Nothing in a cancel but the task's id is read: its metadata decides nothing.
        approving = {"decision": "approve", "interrupt_id": late["interrupt_id"]}
        answer = rpc(client, "CancelTask", {"id": task_id, "metadata": approving}).json()
        task = quiet(client, made, task_id)
        state = client.get(f"/threads/{thread}/state").json()
        listed = client.get("/threads").json()
        refused = client.post(f"/threads/{thread}/resume", json=late)
    assert counted == 1
    assert text_of(answer["result"]) == CANCELED
    assert state["pending"] is None
    assert state["messages"][-1]["type"] == "tool"
    assert state["messages"][-1]["content"].endswith(f"with reason: {REJECTED}")
    # The rejecting run stopped at its first boundary, before the model was asked.
    assert model.calls == calls
    assert listed["needs_decision"] == 0
    assert listed["threads"][0]["status"] == "stopped"
    assert listed["threads"][0]["caller"]["canceled"] is True
    assert refused.status_code == 409
    assert at_once == ["run_active"]
    assert actions(caplog) == []
    assert (task.state, task.reason, task.reject_attempts) == ("canceled", "canceled", 1)


def test_a_subagent_s_proposals_are_rejected_and_the_run_stops_at_freyja_s_next_boundary(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    now = [datetime(2026, 11, 26, 9, 0, tzinfo=UTC)]
    store = InMemoryReceivedTaskStore(clock=lambda: now[0])
    model = ScriptedChatModel(
        script=[DELEGATE, NOTIFY, NOTIFY_AGAIN, AIMessage(content="The subagent stopped.")],
        final_text="Unused.",
    )
    made = make_a2a_server_app(model, received=store)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Ask the subagent to tell #ops.")
        events = made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        cancel(client, task_id)
        # The subagent's step goes on after the rejection and proposes again: the run pauses
        # again, and the next rejection waits its 30 seconds.
        first = quiet(client, made, task_id)
        again = client.get(f"/threads/{thread}/state").json()["pending"]
        now[0] += timedelta(seconds=31)
        client.portal.call(made.service().sweep)
        until(lambda: client.get(f"/threads/{thread}/state").json()["pending"] is None)
        until(lambda: not client.portal.call(made.runtime.locks.is_active, thread))
        client.portal.call(made.service().close)
        state = client.get(f"/threads/{thread}/state").json()
        row = client.get(f"/threads/{thread}").json()
        attempts = made.task(client, task_id).reject_attempts
    assert events[-1][0] == "interrupt"
    assert first.reject_attempts == 1
    assert again is not None
    assert [action["args"]["message"] for action in again["action_requests"]] == ["again"]
    assert model.calls == 4
    assert actions(caplog) == []
    assert row["status"] == "stopped"
    assert attempts == 2
    assert state["messages"][-1]["type"] == "tool"


def test_a_cancel_during_a_step_whose_reply_proposes_leaves_that_proposal_rejected(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    model = LabelledChatModel(script=[NOTIFY], final_text="Not sent.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        model.hold()
        accepted = made.accept(client, "Tell #ops.")
        client.portal.call(made.service().leave, accepted.events)
        with model.held():
            cancel(client, accepted.task.task_id)
        until(lambda: made.task(client, accepted.task.task_id).reject_attempts == 1)
        quiet(client, made, accepted.task.task_id)
        state = client.get(f"/threads/{accepted.task.thread_id}/state").json()
    assert state["pending"] is None
    assert state["messages"][-1]["content"].endswith(REJECTED)
    assert model.calls == 1
    assert actions(caplog) == []


def test_a_cancel_while_an_agent_works_leaves_the_agent_task_and_rejects_its_report_s_proposal(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    agent = make_stub("stub")
    model = ScriptedChatModel(
        script=[HAND_OFF, AIMessage(content="Handed over."), NOTIFY], final_text="Unused."
    )
    made = make_a2a_server_app(model, stubs={"stub": agent}, a2a_call_timeout_seconds=0.3)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Ask the stub.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        answer = cancel(client, task_id)
        busy = rpc(client, "SendMessage", sending("Anything new?", contextId="c-1")).json()
        counting = made.task(client, task_id)
        agent.release()
        # The first round learns the outcome, the second delivers it.
        client.portal.call(made.runtime.worker.tick)
        client.portal.call(made.runtime.worker.tick)
        task = quiet(client, made, task_id)
        state = client.get(f"/threads/{thread}/state").json()
    assert text_of(answer["result"]) == CANCELED
    assert busy["error"]["data"][0]["metadata"] == {"reason": "conversation_busy"}
    # Awaited: the task counts. Reported: released, and what the report led to was rejected.
    assert counting.released_at is None
    assert [found["status"] for found in state["agent_tasks"]] == ["completed"]
    assert state["agent_tasks"][0]["outcome_delivered"] is True
    assert state["pending"] is None
    assert state["messages"][-1]["content"].endswith(REJECTED)
    assert task.released_at is not None
    assert actions(caplog) == []


def test_a_proposal_a_person_already_decided_is_neither_rejected_nor_resumed(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        before = pending_of(client, thread)

        async def taken(*_: Any) -> list[Any]:
            return [{"decisions": [{"type": "approve"}]}]

        monkeypatch.setattr(received_module, "decisions_taken", taken)
        cancel(client, accepted.task.task_id)
        task = quiet(client, made, accepted.task.task_id)
        after = pending_of(client, thread)
        monkeypatch.setattr(runs_module, "decisions_taken", taken)
        again = client.post(
            f"/threads/{thread}/resume/unattended", json=decision_of(client, thread)
        )
    assert after == before
    assert task.reject_attempts == 0
    # The same decisions, sent again, are taken; the run then stops at its first boundary.
    assert again.status_code == 200
    assert actions(caplog) == []


def test_an_approval_or_an_edit_is_refused_once_the_requester_is_gone_and_a_rejection_is_not(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Not sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id

        async def not_made(*_: Any) -> bool:
            return False

        # As when the rejection by rule failed every time it was tried.
        monkeypatch.setattr(made.service(), "_reject", not_made)
        cancel(client, accepted.task.task_id)
        quiet(client, made, accepted.task.task_id)
        pending = pending_of(client, thread)
        edit = {
            "interrupt_id": pending["interrupt_id"],
            "decisions": [{"type": "edit", "args": {"channel": "#ops", "message": "changed"}}],
        }
        refusals = [
            client.post(f"/threads/{thread}/{path}", json=body)
            for path in ("resume", "resume/unattended")
            for body in (decision(client, thread, "approve"), edit)
        ]
        still = pending_of(client, thread)
        rejected = client.post(
            f"/threads/{thread}/resume/unattended", json=decision(client, thread, "reject")
        )
        until(lambda: not client.portal.call(made.runtime.locks.is_active, thread))
        state = client.get(f"/threads/{thread}/state").json()
    for answer in refusals:
        assert answer.status_code == 409, answer.text
        assert answer.json()["detail"] == {"code": "requester_gone", "message": REQUESTER_GONE}
    assert still == pending
    assert rejected.status_code == 200
    assert state["pending"] is None
    assert actions(caplog) == []


def test_an_approval_is_refused_when_whether_the_requester_waits_cannot_be_read(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        pending = pending_of(client, thread)

        async def down(*_: Any) -> Any:
            raise RuntimeError("the store is down")

        monkeypatch.setattr(made.received, "last_started_task_of_thread", down)
        answer = client.post(f"/threads/{thread}/resume", json=decision_of(client, thread))
        busy = client.portal.call(made.runtime.locks.is_active, thread)
        monkeypatch.undo()
        still = pending_of(client, thread)
    assert still == pending
    assert answer.status_code == 500
    assert answer.json()["detail"]["code"] == "internal"
    assert not busy
    assert actions(caplog) == []
    assert "whether its caller still waits could not be read" in caplog.text


@pytest.mark.parametrize("when", ["before the run is marked", "after the run is marked"])
def test_a_cancel_around_a_person_s_continuation_stops_it_at_its_first_boundary(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    when: str,
) -> None:
    caplog.set_level(logging.INFO)
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        service = made.service()

        async def cancel_now() -> None:
            await service.cancel(await made.received.get(task_id))

        if when == "before the run is marked":
            real_mark = made.index.mark_unattended

            async def marked(*args: Any) -> Any:
                await cancel_now()
                return await real_mark(*args)

            monkeypatch.setattr(made.index, "mark_unattended", marked)
        else:
            runs = made.runtime.unattended
            real_stop = runs.should_stop

            async def asked(thread_id: str) -> bool:
                if not (await made.received.get(task_id)).ended:
                    await cancel_now()
                return await real_stop(thread_id)

            monkeypatch.setattr(runs, "should_stop", asked)
        events = stream(client, f"/threads/{thread}/resume/unattended", decision_of(client, thread))
        task = quiet(client, made, task_id)
        state = client.get(f"/threads/{thread}/state").json()
    assert events[-1] == ("end", {"status": "stopped"})
    assert (task.state, task.reason) == ("canceled", "canceled")
    # The decision is spent and its action never started.
    assert state["pending"] is None
    assert actions(caplog) == []


def test_a_rejection_by_rule_that_fails_is_tried_again_30_seconds_apart_five_times_at_most(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    now = [datetime(2026, 11, 26, 9, 0, tzinfo=UTC)]
    store = InMemoryReceivedTaskStore(clock=lambda: now[0])
    made = make_a2a_server_app(
        ScriptedChatModel(script=[NOTIFY], final_text="Not sent."), received=store
    )
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        runtime = made.runtime
        real = runtime.begin_run

        async def no_context(*_: Any, **__: Any) -> Any:
            raise RuntimeError("no context can be taken")

        monkeypatch.setattr(runtime, "begin_run", no_context)
        cancel(client, task_id)
        attempts = [quiet(client, made, task_id).reject_attempts]
        for seconds in (29, 2) * 6:
            now[0] += timedelta(seconds=seconds)
            client.portal.call(made.service().sweep)
            until(lambda: not client.portal.call(runtime.locks.is_active, thread))
            client.portal.call(made.service().close)
            attempts.append(made.task(client, task_id).reject_attempts)
        still = pending_of(client, thread)
        monkeypatch.setattr(runtime, "begin_run", real)
        approved = client.post(
            f"/threads/{thread}/resume/unattended", json=decision(client, thread, "approve")
        )
        rejected = client.post(
            f"/threads/{thread}/resume/unattended", json=decision(client, thread, "reject")
        )
        until(lambda: not client.portal.call(runtime.locks.is_active, thread))
        state = client.get(f"/threads/{thread}/state").json()
        task = made.task(client, task_id)
    assert attempts == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 5, 5, 5]
    assert still is not None
    # The pending proposal did not keep the task counting.
    assert task.released_at is not None
    assert approved.json()["detail"]["code"] == "requester_gone"
    assert rejected.status_code == 200
    assert state["pending"] is None
    assert actions(caplog) == []


def test_a_decision_through_the_route_that_ends_with_its_reader_is_not_stopped_by_a_cancel(
    make_a2a_server_app: Callable[..., ServerApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = LabelledChatModel(script=[NOTIFY], final_text="It was sent.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        body = decision_of(client, thread)
        model.hold()
        reading = client.portal.start_task_soon(
            functools.partial(read_events, _resume(client, thread, body))
        )
        with model.held():
            answer = cancel(client, task_id)
        events = reading.result(timeout=10)
        task = quiet(client, made, task_id)
        state = client.get(f"/threads/{thread}/state").json()
    assert text_of(answer["result"]) == CANCELED
    assert events[-1] == ("end", {"status": "completed"})
    assert state["messages"][-1]["content"] == "It was sent."
    assert len(actions(caplog)) == 1
    assert (task.state, task.reason) == ("canceled", "canceled")


# Part 8: releases and the sweep (research R9).


def test_the_sweep_finds_a_canceled_task_whose_release_was_lost_and_rejects_its_proposal(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    made = make_a2a_server_app(
        ScriptedChatModel(script=[NOTIFY], final_text="Not sent."), a2a_server_tasks=7
    )
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id
        # The cancel was written, and what follows it was lost with the process.
        client.portal.call(
            functools.partial(made.received.end, task_id, "canceled", reason="canceled")
        )
        indexed_locks(made.runtime).on_release = None
        asked: list[int] = []
        unreleased = made.received.unreleased
        to_reject = made.received.to_reject

        async def counted(limit: int) -> Any:
            asked.append(limit)
            return await unreleased(limit)

        async def counted_too(attempts: int, limit: int) -> Any:
            asked.append(limit)
            return await to_reject(attempts, limit)

        monkeypatch.setattr(made.received, "unreleased", counted)
        monkeypatch.setattr(made.received, "to_reject", counted_too)
        client.portal.call(made.service().sweep)
        until(lambda: not client.portal.call(made.runtime.locks.is_active, thread))
        between = made.task(client, task_id)
        client.portal.call(made.service().sweep)
        task = made.task(client, task_id)
        state = client.get(f"/threads/{thread}/state").json()
    assert between.reject_attempts == 1 and between.released_at is None
    assert task.released_at is not None
    assert state["pending"] is None
    # Each of the sweep's two lists is read up to the total limit.
    assert len(asked) == 4 and set(asked) == {7}
    assert actions(caplog) == []


def test_the_sweep_runs_every_30_seconds_while_the_service_runs(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    rounds: list[float] = []
    real = ReceivedTasks.sweep

    async def counted(self: ReceivedTasks) -> None:
        rounds.append(time.monotonic())
        await real(self)

    monkeypatch.setattr(ReceivedTasks, "sweep", counted)
    monkeypatch.setattr(received_module, "SWEEP_SECONDS", 0.05)
    assert received_module.SWEEP_SECONDS == 0.05
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app):
        until(lambda: len(rounds) >= 3)
    count = len(rounds)
    time.sleep(0.2)
    # Stopped with the service.
    assert len(rounds) == count
    off = make_a2a_server_app(ScriptedChatModel(), a2a_server=False)
    before = len(rounds)
    with TestClient(off.app):
        time.sleep(0.2)
    assert len(rounds) == before


# What the review of request 4 found.


def test_a_caller_s_next_message_does_not_let_a_person_approve_the_canceled_task_s_proposal(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The next message's task is written before its run is refused: in between, the
    conversation's last task works, and it is not the one the proposal belongs to."""
    caplog.set_level(logging.INFO)
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.", context="c-1")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        service = made.service()

        async def not_made(*_: Any) -> bool:
            return False

        # As while the rejection by rule waits for its next attempt.
        monkeypatch.setattr(service, "_reject", not_made)
        cancel(client, accepted.task.task_id)
        quiet(client, made, accepted.task.task_id)
        body = ResumeRequest.model_validate(decision(client, thread, "approve"))
        real_accept = made.received.accept
        found: list[Any] = []

        async def accepting(*args: Any, **kwargs: Any) -> Any:
            written = await real_accept(*args, **kwargs)
            found.append(await service.canceled(thread))
            try:
                events = await start_resume(made.runtime, thread, body)
            except HTTPException as exc:
                found.append(detail_of(exc)["code"])
            else:
                found.append([kind for kind, _ in await read_events(events)][-1])
            return written

        monkeypatch.setattr(made.received, "accept", accepting)
        answer = rpc(client, "SendMessage", sending("And now?", contextId="c-1")).json()
    assert found == [True, "requester_gone"]
    assert answer["error"]["data"][0]["metadata"] == {"reason": "conversation_busy"}
    assert actions(caplog) == []


def test_a_task_that_ended_is_answered_from_its_row_when_its_thread_cannot_be_read(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id = accepted.task.task_id
        # Canceled, its rejection and its release still to come.
        client.portal.call(
            functools.partial(made.received.end, task_id, "canceled", reason="canceled")
        )

        async def unreadable(*_: Any, **__: Any) -> Any:
            raise RuntimeError("the store is down")

        monkeypatch.setattr(made.runtime, "begin_read", unreadable)
        read = rpc(client, "GetTask", {"id": task_id}).json()
    assert read["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert text_of(read["result"]) == CANCELED


def test_a_proposal_whose_action_no_longer_needs_approval_is_not_run_by_a_cancel(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    """The agents' mode turned Auto after the proposal: the gate would no longer ask about
    the hand-off, so no decision but approve is taken for it, and a cancel runs nothing."""
    caplog.set_level(logging.INFO)
    model = ScriptedChatModel(script=[HAND_OFF], final_text="Handed over.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        assert client.put("/agents/mode", json={"mode": "manual"}).status_code == 200
        accepted = made.accept(client, "Ask the stub.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        assert client.put("/agents/mode", json={"mode": "auto"}).status_code == 200
        cancel(client, accepted.task.task_id)
        task = quiet(client, made, accepted.task.task_id)
        pending = client.get(f"/threads/{thread}/state").json()["pending"]
    assert pending is not None
    assert pending["action_requests"][0]["allowed_decisions"] == ["approve"]
    assert made.stub.record.messages == []
    assert task.reject_attempts == 1
    assert "set the agents' approval mode to Manual to reject it" in caplog.text


def test_the_rule_repeats_its_own_rejection_that_a_gate_already_took(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A subagent's gate took the rule's rejection and the run then failed: the proposal is
    still shown, and only those same decisions continue the thread (005)."""
    caplog.set_level(logging.INFO)
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Not sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        own = [{"decisions": [{"type": "reject", "message": received_module.REJECTED}]}]

        async def taken(*_: Any) -> list[Any]:
            return own

        monkeypatch.setattr(received_module, "decisions_taken", taken)
        monkeypatch.setattr(runs_module, "decisions_taken", taken)
        cancel(client, accepted.task.task_id)
        task = quiet(client, made, accepted.task.task_id)
        state = client.get(f"/threads/{thread}/state").json()
    assert task.reject_attempts == 1
    assert state["pending"] is None
    assert state["messages"][-1]["content"].endswith(REJECTED)
    assert actions(caplog) == []


def test_a_cancel_whose_index_cannot_be_written_still_rejects_the_proposal_at_once(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Not sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)

        async def down(*_: Any) -> Any:
            raise RuntimeError("the index is down")

        monkeypatch.setattr(made.index, "get", down)
        answer = cancel(client, accepted.task.task_id)
        until(lambda: made.task(client, accepted.task.task_id).reject_attempts == 1)
    assert text_of(answer["result"]) == CANCELED


def test_two_instances_sweeping_at_once_make_one_attempt(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [datetime(2026, 11, 26, 9, 0, tzinfo=UTC)]
    store = InMemoryReceivedTaskStore(clock=lambda: now[0])
    made = make_a2a_server_app(
        ScriptedChatModel(script=[NOTIFY], final_text="Not sent."), received=store
    )
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell #ops.")
        made.finish(client, accepted)
        task_id, thread = accepted.task.task_id, accepted.task.thread_id

        async def no_context(*_: Any, **__: Any) -> Any:
            raise RuntimeError("no context can be taken")

        monkeypatch.setattr(made.runtime, "begin_run", no_context)
        cancel(client, task_id)
        quiet(client, made, task_id)
        now[0] += timedelta(seconds=31)
        service = made.service()

        real_now = store.now

        async def yielding() -> datetime:
            # As two instances would: each reads the time before the other counts.
            await asyncio.sleep(0)
            return await real_now()

        monkeypatch.setattr(store, "now", yielding)

        async def both() -> None:
            # Two rounds that read the same rows before either counts an attempt.
            listed = await store.to_reject(5, 20)
            await asyncio.gather(*(service.look(found) for found in listed + listed))

        client.portal.call(both)
        until(lambda: not client.portal.call(made.runtime.locks.is_active, thread))
        attempts = made.task(client, task_id).reject_attempts
    assert attempts == 2
