"""009: a caller's task and the approval gate (FR-013, FR-013a, research R6).

Part 1, the strict build: the orchestrator a thread a caller started runs on. Every message to
a delegate agent waits for a person there, whatever the agent's approval choice, also from the
subagent and from a run of the worker's; the check at send time agrees with the gate; a
caller's thread runs on no other build, and a graph of a run context runs through ``astream``
only. Runs are started as a caller's message starts them (``ReceivedTasks.accept``) or through
the functions of ``api/runs.py``.
"""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException

from freyja_agent.agent.catalog import Classification
from freyja_agent.delegates import a2a_tools
from freyja_agent.runtime.received_tasks import InMemoryReceivedTaskStore
from freyja_agent.runtime.unattended import CallerThreadError, OtherEntryError
from tests.a2a_stub import make_stub
from tests.conftest import ServerApp, read_events
from tests.fakes import LabelledChatModel, ScriptedChatModel

PROPOSED = {"message": "Summarize the week.", "context": "Ship day is Tuesday.", "task_id": None}


def handoff(step: str = "h", message: str = "Summarize the week.") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"id": f"{step}1", "name": "handoff_to_stub", "args": {**PROPOSED, "message": message}}
        ],
    )


DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Ask the stub agent.", "subagent_type": "general-purpose"},
        }
    ],
)


def approve(client: TestClient, thread: str, sse_events: Any) -> list[tuple[str, Any]]:
    pending = client.get(f"/threads/{thread}/state").json()["pending"]
    body = {
        "interrupt_id": pending["interrupt_id"],
        "decisions": [{"type": "approve"} for _ in pending["action_requests"]],
    }
    with client.stream("POST", f"/threads/{thread}/resume", json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def test_a_hand_off_on_a_caller_s_thread_waits_for_a_person_whatever_the_agent_s_choice(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[handoff()], final_text="Handed over.")
    made = make_a2a_server_app(model)  # the stand-in runs without approval
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Ask the stub.")
        events = made.finish(client, accepted)
        thread = accepted.task.thread_id
        assert events[-1][0] == "interrupt"
        (request,) = events[-1][1]["action_requests"]
        assert (request["name"], request["agent"]) == ("handoff_to_stub", "stub")
        assert made.stub.record.messages == []
        strict = made.runtime.strict_catalog
        assert strict.get("handoff_to_stub").classification is Classification.GATED
        assert made.runtime.catalog.get("handoff_to_stub").classification is (
            Classification.READ_ONLY
        )
        approved = approve(client, thread, sse_events)
    assert approved[-1][0] == "end"
    assert [message.parts[0][1] for message in made.stub.record.messages] == ["Summarize the week."]


def test_the_same_hand_off_on_a_console_thread_is_sent_without_a_pause(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[handoff()]))
    with (
        TestClient(made.app) as client,
        client.stream("POST", "/threads/console-1/runs", json={"message": "go"}) as answer,
    ):
        events = sse_events(answer.iter_lines())
    assert events[-1][0] == "end"
    assert len(made.stub.record.messages) == 1


def test_a_hand_off_the_subagent_proposes_on_a_caller_s_thread_waits_too(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[DELEGATE, handoff("s")]))
    with TestClient(made.app) as client:
        events = made.finish(client, made.accept(client, "Ask through the subagent."))
    assert events[-1][0] == "interrupt"
    assert [r["name"] for r in events[-1][1]["action_requests"]] == ["handoff_to_stub"]
    assert made.stub.record.messages == []


def test_a_run_of_the_worker_on_a_caller_s_thread_waits_for_a_person_too(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    """The path ``SegmentedGraph`` does not run in segments: a run the service starts by
    itself to report an agent's outcome (research R6)."""
    agent = make_stub("stub")
    model = ScriptedChatModel(
        script=[handoff("a", "hold on"), AIMessage(content="Waiting."), handoff("b", "again")],
        final_text="Done.",
    )
    made = make_a2a_server_app(
        model,
        stubs={"stub": agent},
        a2a_call_timeout_seconds=0.3,
        a2a_poll_interval_seconds=10.0,
    )
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Ask the stub to hold.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        approve(client, thread, sse_events)  # the first hand-off, by a person
        assert len(agent.record.messages) == 1
        agent.release()
        client.portal.call(made.runtime.worker.tick)
        client.portal.call(made.runtime.worker.tick)
        state = client.get(f"/threads/{thread}/state").json()
    assert state["pending"] is not None
    assert [r["name"] for r in state["pending"]["action_requests"]] == ["handoff_to_stub"]
    assert len(agent.record.messages) == 1


def test_on_the_strict_build_the_check_at_send_time_wants_a_person_s_decision(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app):
        (link,) = made.runtime.agents
        strict = a2a_tools._Send(link, made.runtime.agent_tasks, True, None, strict=True)
        plain = a2a_tools._Send(link, made.runtime.agent_tasks, False, None)

        async def check(send: a2a_tools._Send, call: Any) -> None:
            await a2a_tools._still_allowed(send, call)

        # No row for the call, and a row no person decided: both refused on the strict build.
        for call in (None, _Call(decided=False)):
            with pytest.raises(ToolException, match="only once a person approved"):
                asyncio.run(check(strict, call))
        asyncio.run(check(strict, _Call(decided=True)))
        asyncio.run(check(plain, None))


def wrappers(client: TestClient, made: ServerApp) -> tuple[Any, Any, str]:
    """The ordinary and the strict build's graphs as run contexts hold them, and the thread
    of a caller's message that completed."""
    accepted = made.accept(client, "hello")
    made.finish(client, accepted)
    thread = accepted.task.thread_id

    async def take(thread_id: str) -> Any:
        context = await made.runtime.begin_run(thread_id)
        await context.release()
        return context.agent

    return client.portal.call(take, "console-thread"), client.portal.call(take, thread), thread


class _Call:
    def __init__(self, decided: bool) -> None:
        self.decided = decided


def test_a_context_for_a_caller_s_thread_is_the_strict_build_s(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app) as client:
        runtime = made.runtime
        accepted = made.accept(client, "hello")
        made.finish(client, accepted)
        thread = accepted.task.thread_id

        async def contexts() -> list[Any]:
            found = []
            for take in (runtime.begin_run, runtime.begin_read):
                for which in (thread, "console-thread", None):
                    context = await take(which)
                    found.append((context.agent, context.catalog))
                    await context.release()
            return found

        found = client.portal.call(contexts)
        fallback = runtime.last_built(strict=True)
    strict_graph, strict_catalog = found[0]
    assert strict_graph.strict and strict_catalog is runtime.strict_catalog
    for graph, catalog in (found[1], found[2], found[4], found[5]):
        assert not graph.strict and catalog is runtime.catalog
    assert found[3][0].strict
    assert fallback.catalog is runtime.strict_catalog
    assert runtime.last_built().catalog is runtime.catalog


def test_a_caller_s_thread_never_runs_on_the_ordinary_build(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        ordinary, strict, thread = wrappers(client, made)
        calls = model.calls

        async def run_on(graph: Any, thread_id: str) -> None:
            config = {"configurable": {"thread_id": thread_id}}
            async for _chunk in graph.astream({"messages": [("user", "again")]}, config):
                pass

        with pytest.raises(CallerThreadError, match="started by another agent"):
            client.portal.call(run_on, ordinary, thread)

        async def broken(_thread_id: str) -> None:
            raise RuntimeError("the store is down")

        for graph in (ordinary, strict):
            looking = graph.lookup
            graph.lookup = broken
            try:
                with pytest.raises(CallerThreadError, match="could not be read"):
                    client.portal.call(run_on, graph, "console-thread")
            finally:
                graph.lookup = looking
    assert model.calls == calls


@pytest.mark.parametrize(
    "entry", ["ainvoke", "invoke", "astream_events", "stream", "batch", "abatch"]
)
def test_a_run_context_s_graph_runs_through_astream_only(
    make_a2a_server_app: Callable[..., ServerApp], entry: str
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app) as client:
        ordinary, strict, _thread = wrappers(client, made)
        for graph in (ordinary, strict):
            with pytest.raises(OtherEntryError):
                getattr(graph, entry)({"messages": []}, {"configurable": {"thread_id": "t"}})


def test_with_the_feature_on_both_builds_are_made_together_and_with_it_off_one(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    on = make_a2a_server_app(ScriptedChatModel())
    with TestClient(on.app) as client:
        manager = on.runtime.models
        ordinary, strict, _active = client.portal.call(manager.builds, on.runtime.built_for)
        assert strict is manager.strict and ordinary is manager.orchestrator
        assert strict.agent is not ordinary.agent
    off = make_a2a_server_app(ScriptedChatModel(), a2a_server=False)
    with TestClient(off.app):
        assert off.runtime.models.strict is None and off.runtime.strict_catalog is None
        assert off.app.state.received is None


async def test_read_events_reads_a_stream_to_its_end() -> None:
    async def two() -> Any:
        from sse_starlette import ServerSentEvent

        yield ServerSentEvent(data='{"a": 1}', event="start")
        yield ServerSentEvent(data="", event="end")

    assert await read_events(two()) == [("start", {"a": 1}), ("end", None)]


# Part 2: a message to a caller's thread through the service's own routes (FR-024).

CALLER_THREAD = {
    "detail": {
        "code": "caller_thread",
        "message": (
            "This task was started by another agent, and only that agent sends messages on it. "
            "Its proposals are decided and its run is stopped here as for any task."
        ),
    }
}
NOTIFY = AIMessage(
    content="",
    tool_calls=[{"id": "n1", "name": "send_notification", "args": {"channel": "#ops"}}],
)


def messages_of(client: TestClient, thread: str) -> list[Any]:
    return client.get(f"/threads/{thread}/state").json()["messages"]


@pytest.mark.parametrize("route", ["runs", "runs/unattended"])
def test_a_message_to_a_caller_s_thread_is_refused_before_the_lease(
    make_a2a_server_app: Callable[..., ServerApp], route: str
) -> None:
    model = LabelledChatModel(final_text="Done.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "First.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        before = messages_of(client, thread)
        calls = model.calls
        answer = client.post(f"/threads/{thread}/{route}", json={"message": "From a person."})
        # The same answer while another run holds the thread.
        model.hold()
        second = made.accept(client, "Second.", context="c-1")
        held = client.portal.start_task_soon(read_events, second.events)
        with model.held():
            busy = client.post(f"/threads/{thread}/{route}", json={"message": "Again."})
        held.result(timeout=5)
        client.portal.call(made.service().close)
        after = messages_of(client, thread)
    assert answer.status_code == busy.status_code == 409
    assert answer.json() == busy.json() == CALLER_THREAD
    assert model.calls == calls + 1
    assert "From a person." not in str(after) and "Again." not in str(after)
    assert len(after) == len(before) + 2


class Unreadable(InMemoryReceivedTaskStore):
    """A store whose lookups fail once ``broken`` is set."""

    broken = False

    async def conversation_of(self, thread_id: str) -> Any:
        if self.broken:
            raise RuntimeError("the table is gone")
        return await super().conversation_of(thread_id)


@pytest.mark.parametrize("route", ["runs", "runs/unattended"])
def test_a_lookup_that_fails_answers_500_and_starts_nothing(
    make_a2a_server_app: Callable[..., ServerApp], route: str
) -> None:
    model = ScriptedChatModel()
    store = Unreadable()
    made = make_a2a_server_app(model, received=store)
    with TestClient(made.app) as client:
        store.broken = True
        answer = client.post(f"/threads/k-1/{route}", json={"message": "hi"})
        store.broken = False
        state = client.get("/threads/k-1/state")
    assert answer.status_code == 500
    assert answer.json()["detail"]["code"] == "internal"
    assert model.calls == 0
    assert state.status_code == 404


def test_its_state_its_proposal_and_its_stop_are_the_service_s_as_before(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[NOTIFY], final_text="Sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell ops.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        state = client.get(f"/threads/{thread}/state")
        pending = state.json()["pending"]
        body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "approve"}]}
        with client.stream("POST", f"/threads/{thread}/resume", json=body) as answer:
            assert answer.status_code == 200
            events = sse_events(answer.iter_lines())
        stop = client.post(f"/threads/{thread}/stop")
    assert state.status_code == 200
    assert events[-1][0] == "end"
    # No run is in progress: the stop is answered as for any thread.
    assert stop.status_code == 409
    assert stop.json()["detail"]["code"] == "no_run"
