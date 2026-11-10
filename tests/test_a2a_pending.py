"""003 User Story 4, part 1: a task still working at the call time limit becomes pending.

FR-017, FR-019, SC-003. The stand-in's ``hold`` tasks stay working until the test releases them,
so a short call limit makes them pending. The worker is ticked by hand and the store's clock is
the fake clock, so claims and their expiry take no wall time. A second instance runs in the
first one's event loop, sharing its store, checkpoints, and run leases, as two processes share
one database.
"""

import itertools
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from a2a.types import GetTaskRequest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from freyja_agent.delegates.a2a_agents import state_name
from freyja_agent.runtime.agent_tasks import AgentTaskRecord, InMemoryAgentTaskStore
from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.runtime import Runtime
from tests.a2a_stub import StubAgent, make_stub
from tests.conftest import A2aApp
from tests.fakes import FakeClock, ScriptedChatModel

Events = list[tuple[str, Any]]
LIMIT = 0.3
INTERVAL = 10.0
TASK = "task-1"
PENDING = (
    f"Agent 'stub' is still working on task {TASK}; it is pending, and Freyja will report its "
    "outcome in this thread."
)


def handoff(message: str, *, call_id: str = "h1", task_id: str | None = None) -> AIMessage:
    args: dict[str, Any] = {"message": message}
    if task_id is not None:
        args["task_id"] = task_id
    return AIMessage(
        content="", tool_calls=[{"id": call_id, "name": "handoff_to_stub", "args": args}]
    )


def stub() -> StubAgent:
    """A stand-in whose tasks are numbered ``task-1``, ``task-2``, ..."""
    numbers = itertools.count(1)
    return make_stub("stub", task_ids=lambda: f"task-{next(numbers)}")


def shared(clock: FakeClock) -> dict[str, Any]:
    """One database's worth of state, for two instances or a restart to share."""
    return {
        "store": InMemoryAgentTaskStore(clock=clock.now),
        "saver": InMemorySaver(),
        "locks": InMemoryRunLocks(60, clock=clock.now),
    }


def pending_app(
    make_a2a_app: Callable[..., A2aApp],
    model: ScriptedChatModel,
    agent: StubAgent,
    **options: Any,
) -> A2aApp:
    return make_a2a_app(
        model,
        {"stub": {"read_only": True}},
        stubs={"stub": agent},
        a2a_call_timeout_seconds=LIMIT,
        a2a_poll_interval_seconds=INTERVAL,
        **options,
    )


def run(client: TestClient, thread: str, message: str, sse_events: Any) -> Events:
    with client.stream("POST", f"/threads/{thread}/runs", json={"message": message}) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def state(client: TestClient, thread: str) -> dict[str, Any]:
    response = client.get(f"/threads/{thread}/state")
    assert response.status_code == 200, response.text
    return response.json()


def tasks(client: TestClient, thread: str) -> list[tuple[str, str, bool]]:
    return [
        (task["task_id"], task["status"], task["outcome_delivered"])
        for task in state(client, thread)["agent_tasks"]
    ]


def tick(client: TestClient, runtime: Runtime) -> None:
    assert runtime.worker is not None
    client.portal.call(runtime.worker.tick)


def agent_state(client: TestClient, runtime: Runtime, task_id: str) -> str:
    """The task's state at the stand-in, read through the runtime's own link."""
    (link,) = runtime.agents
    assert link.client is not None
    task = client.portal.call(link.client.get_task, GetTaskRequest(id=task_id))
    return state_name(task.status.state)


def wait_for(client: TestClient, runtime: Runtime, task_id: str, wanted: str) -> None:
    deadline = time.monotonic() + 5
    while (now := agent_state(client, runtime, task_id)) != wanted:
        assert time.monotonic() < deadline, f"task {task_id} is {now}, not {wanted}"
        time.sleep(0.02)


def record(client: TestClient, made: A2aApp, task_id: str) -> AgentTaskRecord:
    found = client.portal.call(made.store.get_task, "stub", task_id)
    assert found is not None
    return found


@contextmanager
def busy(client: TestClient, runtime: Runtime, thread: str) -> Iterator[None]:
    """Hold the thread's run lease, as a run in progress does."""
    assert client.portal.call(runtime.locks.acquire, thread, "a-run-in-progress")
    try:
        yield
    finally:
        client.portal.call(runtime.locks.release, thread, "a-run-in-progress")


@contextmanager
def second_instance(client: TestClient, made: A2aApp) -> Iterator[Runtime]:
    """Open ``made``'s runtime in ``client``'s event loop: another instance beside it."""
    with client.portal.wrap_async_context_manager(made.open_runtime(made.settings)) as runtime:
        yield runtime


def go_pending(client: TestClient, thread: str, sse_events: Any) -> Events:
    events = run(client, thread, "Compile the report with the stub.", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    return events


def tool_results(events: Events, name: str) -> list[dict[str, Any]]:
    return [
        message
        for kind, data in events
        if kind == "update" and data["node"] == "tools"
        for message in data["data"].get("messages", [])
        if message["type"] == "tool" and message["name"] == name
    ]


def test_a_task_still_working_at_the_limit_is_pending(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")])
    made = pending_app(make_a2a_app, model, agent)
    with TestClient(made.app) as client:
        events = go_pending(client, "p-1", sse_events)
        (task,) = state(client, "p-1")["agent_tasks"]
        agent.release()

    (result,) = tool_results(events, "handoff_to_stub")
    assert result["content"] == PENDING
    # Pending is not a failure: the orchestrator receives the note as a result, not an error.
    (seen,) = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert (seen.content, seen.status) == (PENDING, "success")
    assert (task["task_id"], task["status"]) == (TASK, "working")
    assert task["outcome_delivered"] is False
    assert task["ended_at"] is None
    assert task["message"] == "hold: report"


def test_a_tick_claims_a_task_for_one_instance_only(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    agent = stub()
    database = shared(clock)
    model = ScriptedChatModel(script=[handoff("hold: report")])
    made = pending_app(make_a2a_app, model, agent, **database)
    other = pending_app(
        make_a2a_app, ScriptedChatModel(), agent, instance_id="instance-b", **database
    )
    with TestClient(made.app) as client:
        go_pending(client, "p-2", sse_events)
        tick(client, made.runtime)
        assert record(client, made, TASK).watcher == "instance-a"
        with second_instance(client, other) as runtime_b:
            seen = len(other.transports["stub"].requests)
            tick(client, runtime_b)
            # The claim is held: the other instance neither takes it nor checks the task.
            assert len(other.transports["stub"].requests) == seen
            assert record(client, made, TASK).watcher == "instance-a"
            # The holder renews its own claim.
            tick(client, made.runtime)
            assert record(client, made, TASK).watcher == "instance-a"
        agent.release()


def test_a_restarted_service_watches_the_task_again(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    agent = stub()
    database = shared(clock)
    made = pending_app(
        make_a2a_app, ScriptedChatModel(script=[handoff("hold: report")]), agent, **database
    )
    with TestClient(made.app) as client:
        go_pending(client, "p-3", sse_events)
        tick(client, made.runtime)
        # The agent finishes while this instance is going away; it never sees the change.
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")

    restarted = pending_app(
        make_a2a_app,
        ScriptedChatModel(final_text="The report is ready."),
        agent,
        instance_id="instance-b",
        **database,
    )
    with TestClient(restarted.app) as client:
        assert tasks(client, "p-3") == [(TASK, "working", False)]
        # The stopped instance's claim holds for three intervals from its last tick.
        clock.advance(3 * INTERVAL - 1)
        tick(client, restarted.runtime)
        assert record(client, restarted, TASK).watcher == "instance-a"
        clock.advance(2)
        tick(client, restarted.runtime)
        assert tasks(client, "p-3") == [(TASK, "completed", True)]
        assert record(client, restarted, TASK).watcher == "instance-b"


ENDINGS = [
    ("hold: report", "completed", "Agent 'stub' completed task task-1:"),
    ("hold fail: report", "failed", "Agent 'stub' task task-1 failed."),
    ("hold reject: report", "rejected", "Agent 'stub' task task-1 rejected the task."),
    ("hold cancel: report", "canceled", "Agent 'stub' task task-1 was canceled."),
    ("hold ask: report", "input_required", "Agent 'stub' needs input for task task-1:"),
    (
        "hold auth: report",
        "failed",
        "Agent 'stub' task task-1 asks for authentication, which is out of scope.",
    ),
]


@pytest.mark.parametrize(("message", "status", "heading"), ENDINGS)
def test_a_watched_task_that_changes_is_recorded_with_an_outcome_ready(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    message: str,
    status: str,
    heading: str,
) -> None:
    agent = stub()
    made = pending_app(make_a2a_app, ScriptedChatModel(script=[handoff(message)]), agent)
    with TestClient(made.app) as client:
        go_pending(client, "p-4", sse_events)
        tick(client, made.runtime)
        assert record(client, made, TASK).state == "working"
        agent.release()
        wait_for(client, made.runtime, TASK, "auth_required" if "auth" in message else status)
        # The thread is busy, so the change is recorded and nothing is delivered yet.
        with busy(client, made.runtime, "p-4"):
            tick(client, made.runtime)
        task = record(client, made, TASK)
        listed = tasks(client, "p-4")

    assert task.state == status
    assert task.outcome is not None and task.outcome.startswith(heading)
    assert (task.outcome_ready, task.delivered_at) == (True, None)
    assert (task.ended_at is not None) is (status != "input_required")
    assert listed == [(TASK, status, False)]


def test_a_task_the_agent_no_longer_knows_ends_failed(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    made = pending_app(make_a2a_app, ScriptedChatModel(), stub())
    with TestClient(made.app) as client:
        run(client, "p-5", "hello", sse_events)
        now = client.portal.call(made.store.now)
        client.portal.call(
            made.store.record_task,
            AgentTaskRecord(
                agent="stub",
                task_id="forgotten",
                thread_id="p-5",
                context_id="ctx",
                message="compile the report",
                state="working",
                started_at=now,
                last_checked_at=now,
            ),
        )
        with busy(client, made.runtime, "p-5"):
            tick(client, made.runtime)
        task = record(client, made, "forgotten")

    assert task.state == "failed"
    assert task.outcome == (
        "Agent 'stub' task forgotten failed.\n\nThe agent no longer knows this task."
    )
    assert task.outcome_ready is True


def test_a_task_waiting_for_input_is_not_checked(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    agent = stub()
    made = pending_app(make_a2a_app, ScriptedChatModel(script=[handoff("hold ask: report")]), agent)
    with TestClient(made.app) as client:
        go_pending(client, "p-6", sse_events)
        agent.release()
        wait_for(client, made.runtime, TASK, "input_required")
        with busy(client, made.runtime, "p-6"):
            tick(client, made.runtime)
        seen = len(made.transports["stub"].requests)
        with busy(client, made.runtime, "p-6"):
            tick(client, made.runtime)
        # Nothing changes at the agent until the person answers; it is not asked again.
        assert len(made.transports["stub"].requests) == seen


def test_a_task_whose_watcher_stops_is_delivered_within_a_minute(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    agent = stub()
    database = shared(clock)
    made = pending_app(
        make_a2a_app, ScriptedChatModel(script=[handoff("hold: report")]), agent, **database
    )
    other = pending_app(
        make_a2a_app,
        ScriptedChatModel(final_text="The report is ready."),
        agent,
        instance_id="instance-b",
        **database,
    )
    with TestClient(made.app) as client:
        go_pending(client, "p-7", sse_events)
        tick(client, made.runtime)
        # The task changes now, and the instance watching it stops ticking.
        changed = clock.now()
        agent.release()
        wait_for(client, made.runtime, TASK, "completed")
        with second_instance(client, other) as runtime_b:
            # Instance b ticks at its interval until the outcome is in the thread.
            while tasks(client, "p-7") != [(TASK, "completed", True)]:
                assert clock.now() - changed <= 60, "not delivered within a minute"
                clock.advance(INTERVAL)
                tick(client, runtime_b)
        delivered_after = clock.now() - changed
        messages = state(client, "p-7")["messages"]

    assert delivered_after <= 60
    assert record_count(messages, "tool", "check_agent_tasks") == 1


def record_count(messages: list[dict[str, Any]], kind: str, name: str) -> int:
    return sum(1 for m in messages if m["type"] == kind and m["name"] == name)
