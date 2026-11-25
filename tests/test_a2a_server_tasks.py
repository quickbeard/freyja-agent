"""009: a received task over time (FR-001, FR-012, FR-016, SC-014, research R6, R7, R9).

Part 1, the feature off while callers' threads exist: such a thread is not run. A message
and every decision are refused with ``a2a_server_off``, the worker leaves it alone, the
graph refuses it, and its state is still read; a console thread runs as before. An instance
that is off asks who started a thread every time, so one started before any conversation
existed refuses it too.

Part 2, where a thread stands and ending a task: one test per row of research R7's table, the
three fences, the minute a task may wait for its run, and the function every release calls.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.api.received import ReceivedTasks
from freyja_agent.runtime.received_tasks import InMemoryReceivedTaskStore
from freyja_agent.runtime.unattended import CallerThreadError
from tests.a2a_stub import make_stub
from tests.conftest import ServerApp
from tests.fakes import FailingChatModel, LabelledChatModel, LoopingChatModel, ScriptedChatModel

NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
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
        made.runtime.locks.on_release = None  # no look by the release: the test looks
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
        made.runtime.locks.on_release = None  # the release function was lost
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

        async def blocks(_thread_id: str) -> None:
            await never.wait()

        async def raises(_thread_id: str) -> None:
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
        made.runtime.locks.on_release = None
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
        made.runtime.locks.on_release = None
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
