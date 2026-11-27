"""Telemetry of the work that waits (feature 011, User Story 3).

Two values, read from what every instance shares and not from a run: how many threads have
a proposal nobody decided, and how old the oldest outcome is that is ready and not
delivered. A reading is made on a timer (``runtime/waiting.py``) and sent by two gauges
(``runtime/telemetry.py``), which send nothing when there is no reading to stand on.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from freyja_agent.runtime.agent_tasks import AgentTaskRecord
from freyja_agent.runtime.runtime import Runtime
from freyja_agent.runtime.telemetry import (
    OLDEST_UNDELIVERED_AGE,
    THREADS_AWAITING_DECISION,
    WAITING_INTERVAL_SECONDS,
)
from freyja_agent.runtime.thread_index import InMemoryThreadIndex
from freyja_agent.runtime.waiting import WaitingWork, read_waiting
from tests.conftest import McpApp
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import make_stub
from tests.telemetry_capture import Captured
from tests.unattended_helpers import stream as read

BOTH = (THREADS_AWAITING_DECISION, OLDEST_UNDELIVERED_AGE)


def notify(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": call_id,
                "name": "send_notification",
                "args": {"channel": "#ops", "message": "x"},
            }
        ],
    )


def values(captured: Captured) -> list[float | None]:
    """What the two gauges send now; None for one that sends nothing."""
    found: list[float | None] = []
    for metric in BOTH:
        points = captured.points(metric)
        assert len(points) <= 1 and all(point.attributes == {} for point in points)
        found.append(points[0].value if points else None)
    return found


def pausing(captured: Captured, make_mcp_app: Callable[..., McpApp], runs: int = 3) -> McpApp:
    model = ScriptedChatModel(script=[notify(f"n{number}") for number in range(runs)])
    return make_mcp_app(
        model,
        {"work": {"command": "stub", "read_only": ["lookup"]}},
        stubs={"work": make_stub("work")},
        telemetry=captured.telemetry,
    )


def pause(client: TestClient, thread: str) -> None:
    assert read(client, f"/threads/{thread}/runs", {"message": "go"})[-1][0] == "interrupt"


def decide(client: TestClient, thread: str) -> None:
    pending = client.get(f"/threads/{thread}/state").json()["pending"]
    body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "reject"}]}
    assert read(client, f"/threads/{thread}/resume", body)[-1][0] == "end"


# The two gauges.


def test_the_gauges_send_what_the_last_reading_found() -> None:
    captured = Captured()
    assert values(captured) == [None, None]

    captured.telemetry.waiting(2, 37.5)
    assert values(captured) == [2, 37.5]

    captured.telemetry.waiting(0, 0.0)
    assert values(captured) == [0, 0.0]


def test_a_reading_that_is_too_old_or_was_given_up_is_not_sent() -> None:
    captured = Captured()
    now = [1000.0]
    captured.telemetry.clock = lambda: now[0]

    captured.telemetry.waiting(2, 37.5)
    now[0] += 2 * WAITING_INTERVAL_SECONDS
    assert values(captured) == [2, 37.5]
    # Nobody read for more than two intervals: the number would be made up.
    now[0] += 1
    assert values(captured) == [None, None]

    captured.telemetry.waiting(1, 5.0)
    captured.telemetry.waiting_unknown()
    assert values(captured) == [None, None]


def test_telemetry_that_is_off_keeps_no_reading() -> None:
    from freyja_agent.runtime.telemetry import Telemetry

    off = Telemetry.off()
    off.waiting(2, 37.5)
    off.waiting_unknown()


# A reading.


def test_a_reading_counts_the_threads_whose_proposal_nobody_decided(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = pausing(captured, make_mcp_app)
    with TestClient(made.app) as client:
        for thread in ("t-1", "t-2", "t-3"):
            pause(client, thread)
        decide(client, "t-2")
        states = [client.get(f"/threads/{thread}/state").json() for thread in ("t-1", "t-3")]
        model = made.runtime.models
        assert model is not None
        # Nobody has read the list of threads: no row was settled since its run ended.
        unsettled = client.portal.call(made.runtime.threads.unsettled)
        assert {row.thread_id for row in unsettled} == {"t-1", "t-2", "t-3"}

        client.portal.call(read_waiting, made.runtime)

        assert values(captured) == [2, 0.0]
        # It settled the rows, as a reading of the list does, and changed no thread.
        assert client.portal.call(made.runtime.threads.unsettled) == []
        after = [client.get(f"/threads/{thread}/state").json() for thread in ("t-1", "t-3")]
        assert after == states
        assert all(state["run_active"] is False for state in after)
        assert client.get("/threads").json()["needs_decision"] == 2

        decide(client, "t-1")
        decide(client, "t-3")
        client.portal.call(read_waiting, made.runtime)
        assert values(captured) == [0, 0.0]
    # No model and no tool server was asked anything for a reading.
    assert made.links["work"].stub.calls == []


def test_a_reading_leaves_a_thread_whose_run_is_in_progress_alone(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = Captured()
    made = pausing(captured, make_mcp_app)
    with TestClient(made.app) as client:
        pause(client, "t-1")
        pause(client, "t-2")
        held = made.runtime.locks.is_active

        async def is_active(thread_id: str) -> bool:
            return thread_id == "t-2" or await held(thread_id)

        monkeypatch.setattr(made.runtime.locks, "is_active", is_active)
        client.portal.call(read_waiting, made.runtime)
        unsettled = client.portal.call(made.runtime.threads.unsettled)

    # Whether a run is in progress is the lease's to say: its row is not written.
    assert [row.thread_id for row in unsettled] == ["t-2"]
    assert values(captured) == [1, 0.0]


def test_a_reading_says_how_old_the_oldest_outcome_is_that_waits(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = pausing(captured, make_mcp_app, runs=0)

    async def an_outcome_waits(runtime: Runtime) -> None:
        store = runtime.agent_tasks
        ended = await store.now() - timedelta(seconds=90)
        await store.record_task(
            AgentTaskRecord(
                agent="stub",
                task_id="task-1",
                thread_id="t-1",
                context_id="ctx-1",
                message="compile the report",
                state="completed",
                started_at=ended,
                last_checked_at=ended,
                ended_at=ended,
                outcome="done",
                outcome_ready=True,
            )
        )

    with TestClient(made.app) as client:
        client.portal.call(an_outcome_waits, made.runtime)
        client.portal.call(read_waiting, made.runtime)
        awaiting, oldest = values(captured)
        client.portal.call(made.runtime.agent_tasks.mark_delivered, "stub", "task-1")
        client.portal.call(read_waiting, made.runtime)

    assert awaiting == 0 and oldest is not None and 90 <= oldest < 95
    assert values(captured) == [0, 0.0]


def test_two_instances_that_share_the_stores_read_the_same(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    one, other = Captured(), Captured()
    saver, index = InMemorySaver(), InMemoryThreadIndex()
    first = make_mcp_app(
        ScriptedChatModel(script=[notify("n0")]),
        {},
        saver=saver,
        thread_index=index,
        telemetry=one.telemetry,
    )
    with TestClient(first.app) as client:
        pause(client, "t-1")
        second = make_mcp_app(
            ScriptedChatModel(),
            {},
            instance_id="instance-b",
            saver=saver,
            thread_index=index,
            telemetry=other.telemetry,
        )
        with TestClient(second.app) as other_client:
            other_client.portal.call(read_waiting, second.runtime)
        client.portal.call(read_waiting, first.runtime)

    # Each sends what the shared stores say: a collector takes one and does not add them.
    assert values(one)[0] == values(other)[0] == 1


# The timer.


def test_a_reading_that_fails_leaves_both_values_out_and_is_said_once_until_one_succeeds(
    make_mcp_app: Callable[..., McpApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    captured = Captured()
    made = pausing(captured, make_mcp_app)
    said = "the work that waits could not be read"

    def lines() -> int:
        return sum(1 for record in caplog.records if said in record.getMessage())

    with TestClient(made.app) as client:
        pause(client, "t-1")
        work = WaitingWork(made.runtime, interval=60)
        client.portal.call(work.read)
        assert values(captured) == [1, 0.0]
        counts = made.runtime.threads.counts

        async def down() -> Any:
            raise ConnectionError("the zebra database at 10.1.2.3 is away")

        monkeypatch.setattr(made.runtime.threads, "counts", down)
        client.portal.call(work.read)
        client.portal.call(work.read)
        # Left out at once, and said once however long it lasts.
        assert values(captured) == [None, None]
        assert lines() == 1
        # A run goes on as it would: nothing but the two values depends on a reading.
        pause(client, "t-2")

        monkeypatch.setattr(made.runtime.threads, "counts", counts)
        client.portal.call(work.read)
        assert values(captured) == [2, 0.0]
        monkeypatch.setattr(made.runtime.threads, "counts", down)
        client.portal.call(work.read)
        assert lines() == 2

    text = caplog.text
    assert "(ConnectionError)" in text
    assert "zebra" not in text and "10.1.2.3" not in text


def test_one_thread_that_cannot_be_looked_at_does_not_cost_the_reading(
    make_mcp_app: Callable[..., McpApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    from freyja_agent.runtime import waiting

    captured = Captured()
    made = pausing(captured, make_mcp_app)
    settle = waiting.settle_thread

    async def settle_but_one(index: Any, context: Any, tasks: Any, thread: str, runs: int) -> Any:
        if thread == "t-1":
            raise RuntimeError("this thread's state cannot be read")
        return await settle(index, context, tasks, thread, runs)

    monkeypatch.setattr(waiting, "settle_thread", settle_but_one)
    with TestClient(made.app) as client:
        pause(client, "t-1")
        pause(client, "t-2")
        client.portal.call(read_waiting, made.runtime)

    assert values(captured) == [1, 0.0]


def test_the_timer_reads_at_its_interval_and_stops_when_told(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = pausing(captured, make_mcp_app)

    async def a_while(work: WaitingWork) -> None:
        work.start()
        work.start()  # started once, however often it is asked
        await asyncio.sleep(0.2)
        await work.stop()
        await work.stop()

    with TestClient(made.app) as client:
        pause(client, "t-1")
        client.portal.call(a_while, WaitingWork(made.runtime, interval=0.02))

    assert values(captured) == [1, 0.0]


def test_the_reading_is_started_only_where_telemetry_is_on(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    on = make_mcp_app(ScriptedChatModel(), {}, telemetry=captured.telemetry)
    off = make_mcp_app(ScriptedChatModel(), {})
    with TestClient(on.app), TestClient(off.app):
        # The suite's applications are built without the service's loops; the service
        # starts this one with its worker's, and never with telemetry off.
        assert on.runtime.waiting is not None and not on.runtime.waiting.running
        assert on.runtime.waiting.wanted(run_loops=True) is True
        assert on.runtime.waiting.wanted(run_loops=False) is False
        assert off.runtime.waiting is not None
        assert off.runtime.waiting.wanted(run_loops=True) is False
    assert values(captured) == [None, None]
