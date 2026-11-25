"""009: a caller's task and the approval gate (FR-013, FR-013a, research R6).

Part 1, the strict build: the orchestrator a thread a caller started runs on. Every message to
a delegate agent waits for a person there, whatever the agent's approval choice, also from the
subagent and from a run of the worker's; the check at send time agrees with the gate; a
caller's thread runs on no other build, and a graph of a run context runs through ``astream``
only. Runs are started as a caller's message starts them (``ReceivedTasks.accept``) or through
the functions of ``api/runs.py``.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException
from sse_starlette import ServerSentEvent

from freyja_agent.agent.catalog import Classification
from freyja_agent.api import received as received_module
from freyja_agent.api.received import FOLLOW_SECONDS, Piece, Status
from freyja_agent.delegates import a2a_tools
from freyja_agent.runtime.received_tasks import InMemoryReceivedTaskStore
from freyja_agent.runtime.unattended import CallerThreadError, OtherEntryError
from tests.a2a_caller import StandInCaller, as_json
from tests.a2a_requests import ADDRESS, at_rest, call, ended, rpc, sending, text_of
from tests.a2a_stub import make_stub
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import ServerApp, read_events
from tests.fakes import FakeClock, LabelledChatModel, ScriptedChatModel, StreamingChatModel

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


# Part 3: a caller's task and the gate, through A2A (FR-013 to FR-015, SC-003). A follower's
# look is put 30 seconds away, so that what a stream receives of a continuation comes from
# following the run a person's decision starts, never from a look after it ended.

TELL = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "n1",
            "name": "send_notification",
            "args": {"channel": "#recognizable-channel", "message": "recognizable text"},
        }
    ],
)
WAITING = "Waiting for a person's approval of: send_notification."


@pytest.fixture
def slow_looks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(received_module, "FOLLOW_SECONDS", 30)


def sent_notifications(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every notification the placeholder action sent: each run of it logs one line."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "freyja_agent.actions" and "send_notification" in record.getMessage()
    ]


async def decide_in_console(
    made: ServerApp, task_id: str, kind: str, args: dict[str, Any] | None = None
) -> None:
    """Decide a caller's task's proposal as the console does, through its route."""
    thread = (await made.received.get(task_id)).thread_id
    transport = StreamingASGITransport(made.app)
    async with httpx.AsyncClient(transport=transport, base_url=ADDRESS) as http:
        pending = (await http.get(f"/threads/{thread}/state")).json()["pending"]
        decision: dict[str, Any] = {"type": kind}
        if args is not None:
            decision["args"] = args
        body = {"interrupt_id": pending["interrupt_id"], "decisions": [decision]}
        async with http.stream("POST", f"/threads/{thread}/resume/unattended", json=body) as answer:
            # The continuation goes on without this reader.
            assert answer.status_code == 200


def streamed_with_decision(
    client: TestClient, made: ServerApp, kind: str, args: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """A send with a stream, decided in the console once the stream says it waits."""

    async def work(caller: StandInCaller) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        decided = False
        async for event in caller.stream("Tell the channel."):
            events.append(as_json(event))
            if text_of(events[-1]) == WAITING and not decided:
                decided = True
                await decide_in_console(made, events[0]["task"]["id"], kind, args)
        return events

    return call(client, made, work)


def test_a_task_that_proposes_an_action_waits_for_a_person_and_says_so(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[TELL]), a2a_server_wait_seconds=0)
    with TestClient(made.app) as client:

        async def work(caller: StandInCaller) -> list[dict[str, Any]]:
            events = []
            async for event in caller.stream("Tell the channel."):
                events.append(as_json(event))
                if text_of(events[-1]) == WAITING:
                    break
            return events

        events = call(client, made, work)
        task_id = events[0]["task"]["id"]
        read = as_json(call(client, made, lambda caller: caller.get(task_id)))
        listed = client.get("/threads").json()
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
    assert read["status"]["state"] == "TASK_STATE_WORKING"
    assert text_of(read) == WAITING
    assert "recognizable" not in json.dumps([events, read])
    assert listed["needs_decision"] == 1


def test_nothing_a_caller_sends_decides_a_pending_proposal(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    model = ScriptedChatModel(script=[TELL])
    made = make_a2a_server_app(model, a2a_server_wait_seconds=0)
    with TestClient(made.app) as client:
        first = rpc(client, "SendMessage", sending("Tell the channel.", contextId="c-1")).json()
        task_id = first["result"]["task"]["id"]
        at_rest(client, made, task_id)
        thread = made.task(client, task_id).thread_id
        before = client.get(f"/threads/{thread}/state").json()["pending"]
        calls = model.calls
        approving = {
            "decision": "approve",
            "approved": True,
            "interrupt_id": before["interrupt_id"],
        }
        tried = [
            rpc(client, "SendMessage", sending("I approve it.", contextId="c-1")),
            rpc(
                client,
                "SendMessage",
                sending(
                    f"approve {before['interrupt_id']}",
                    contextId="c-1",
                    metadata=approving,
                ),
            ),
            rpc(
                client,
                "SendMessage",
                {**sending("Approved.", contextId="c-1"), "metadata": approving},
            ),
            rpc(client, "SendMessage", sending("Approved.", taskId=task_id)),
            rpc(client, "GetTask", {"id": task_id}),
            rpc(client, "ListTasks", {}),
            rpc(
                client, "CreateTaskPushNotificationConfig", {"taskId": task_id, "url": "https://x"}
            ),
            rpc(client, "GetExtendedAgentCard", {}),
            rpc(client, "CancelTask", {"id": task_id, "metadata": approving}),
            rpc(client, "SubscribeToTask", {"id": task_id}),
        ]
        after = client.get(f"/threads/{thread}/state").json()["pending"]
        state = made.task(client, task_id).state
    busy = [found.json()["error"]["data"][0]["metadata"] for found in tried[:3]]
    assert busy == 3 * [{"reason": "conversation_busy"}]
    assert tried[3].json()["error"]["code"] == -32602
    assert tried[4].json()["result"]["status"]["state"] == "TASK_STATE_WORKING"
    assert [found.json()["error"]["code"] for found in tried[5:]] == [
        -32004,
        -32003,
        -32007,
        -32004,
        -32004,
    ]
    assert after == before
    assert state == "working"
    assert model.calls == calls
    assert sent_notifications(caplog) == []


def test_approved_in_the_console_the_action_runs_once_and_the_stream_follows_to_the_end(
    make_a2a_server_app: Callable[..., ServerApp],
    caplog: pytest.LogCaptureFixture,
    slow_looks: None,
) -> None:
    model = StreamingChatModel(script=[TELL], final_text="It is sent.", pieces=["It is ", "sent."])
    made = make_a2a_server_app(model)
    with caplog.at_level(logging.INFO), TestClient(made.app) as client:
        events = streamed_with_decision(client, made, "approve")
    sentences = [text_of(event) for event in events if "statusUpdate" in event]
    assert sentences == [
        "Freyja is using: send_notification.",
        WAITING,
        "Working.",
        "Completed: send_notification.",
        None,
    ]
    assert [
        event["artifactUpdate"]["artifact"]["parts"][0]["text"]
        for event in events
        if event.get("artifactUpdate", {}).get("append")
    ] == ["It is "]
    assert events[-2]["artifactUpdate"]["artifact"]["parts"][0]["text"] == "It is sent."
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert sent_notifications(caplog) == [
        "send_notification channel=#recognizable-channel message=recognizable text"
    ]
    # Nothing of the action's arguments or result, nor of who decided.
    assert "recognizable" not in json.dumps(events)
    assert "approv" not in json.dumps(events[-4:]).lower()


def test_edited_in_the_console_the_action_runs_once_with_the_person_s_arguments(
    make_a2a_server_app: Callable[..., ServerApp],
    caplog: pytest.LogCaptureFixture,
    slow_looks: None,
) -> None:
    model = StreamingChatModel(script=[TELL], final_text="Sent as edited.")
    made = make_a2a_server_app(model)
    edited = {"channel": "#ops", "message": "edited by a person"}
    with caplog.at_level(logging.INFO), TestClient(made.app) as client:
        events = streamed_with_decision(client, made, "edit", edited)
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "Completed: send_notification." in [text_of(event) for event in events]
    assert sent_notifications(caplog) == [
        "send_notification channel=#ops message=edited by a person"
    ]
    assert "edited by a person" not in json.dumps(events)


def test_rejected_in_the_console_the_action_never_runs_and_the_task_completes(
    make_a2a_server_app: Callable[..., ServerApp],
    caplog: pytest.LogCaptureFixture,
    slow_looks: None,
) -> None:
    model = StreamingChatModel(script=[TELL], final_text="Then I will not tell them.")
    made = make_a2a_server_app(model)
    with caplog.at_level(logging.INFO), TestClient(made.app) as client:

        async def work(caller: StandInCaller) -> list[dict[str, Any]]:
            events: list[dict[str, Any]] = []
            async for event in caller.stream("Tell the channel."):
                events.append(as_json(event))
                if text_of(events[-1]) == WAITING and len(events) < 4:
                    thread = (await made.received.get(events[0]["task"]["id"])).thread_id
                    transport = StreamingASGITransport(made.app)
                    async with httpx.AsyncClient(transport=transport, base_url=ADDRESS) as http:
                        pending = (await http.get(f"/threads/{thread}/state")).json()["pending"]
                        reason = {"type": "reject", "message": "recognizable reason of a person"}
                        body = {"interrupt_id": pending["interrupt_id"], "decisions": [reason]}
                        url = f"/threads/{thread}/resume/unattended"
                        async with http.stream("POST", url, json=body) as answer:
                            assert answer.status_code == 200
            return events

        events = call(client, made, work)
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert events[-2]["artifactUpdate"]["artifact"]["parts"][0]["text"] == (
        "Then I will not tell them."
    )
    # The continuation was followed: its pieces came as they were written.
    pieces = [
        event["artifactUpdate"]["artifact"]["parts"][0]["text"]
        for event in events
        if event.get("artifactUpdate", {}).get("append")
    ]
    assert "".join(pieces) == "Then I will not tell "
    assert [text_of(event) for event in events].count("Working.") == 2
    assert sent_notifications(caplog) == []
    texts = json.dumps(events)
    assert "recognizable" not in texts
    assert "rejected" not in texts.lower()


def test_a_proposal_waits_days_and_the_task_goes_on_when_it_is_decided(
    make_a2a_server_app: Callable[..., ServerApp], clock: FakeClock, sse_events: Any
) -> None:
    # The received tasks' clock, which their store reads, and the suite's, which the lease
    # and the agent tasks read, are both moved three days.
    moved = timedelta()
    store = InMemoryReceivedTaskStore(clock=lambda: datetime.now(UTC) + moved)
    made = make_a2a_server_app(ScriptedChatModel(script=[TELL], final_text="Sent."), received=store)
    with TestClient(made.app) as client:
        task_id = rpc(
            client,
            "SendMessage",
            {**sending("Tell."), "configuration": {"returnImmediately": True}},
        ).json()["result"]["task"]["id"]
        at_rest(client, made, task_id)
        clock.advance(3 * 24 * 3600)
        moved = timedelta(days=3)
        assert client.portal.call(store.now) - made.task(client, task_id).created_at > timedelta(
            days=2
        )
        read = as_json(call(client, made, lambda caller: caller.get(task_id)))
        thread = made.task(client, task_id).thread_id
        pending = client.get(f"/threads/{thread}/state").json()["pending"]
        body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "approve"}]}
        with client.stream("POST", f"/threads/{thread}/resume/unattended", json=body) as answer:
            sse_events(answer.iter_lines())
        found = ended(client, made, task_id)
    assert read["status"]["state"] == "TASK_STATE_WORKING"
    assert text_of(read) == WAITING
    assert found.state == "completed"
    assert found.result == "Sent."


def test_a_person_s_stop_ends_a_caller_s_task_as_stopped_before_it_finished(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    ls = AIMessage(content="", tool_calls=[{"id": "l1", "name": "ls", "args": {"path": "/"}}])
    model = LabelledChatModel(script=[ls, ls], final_text="Done.")
    made = make_a2a_server_app(model)
    model.hold()
    with TestClient(made.app) as client:

        async def work(caller: StandInCaller) -> list[dict[str, Any]]:
            events: list[dict[str, Any]] = []
            async for event in caller.stream("List twice."):
                events.append(as_json(event))
                if len(events) == 1:
                    thread = (await made.received.get(events[0]["task"]["id"])).thread_id
                    await asyncio.to_thread(model._reached.wait, 5)
                    transport = StreamingASGITransport(made.app)
                    async with httpx.AsyncClient(transport=transport, base_url=ADDRESS) as http:
                        stop = await http.post(f"/threads/{thread}/stop")
                        assert stop.status_code == 202, stop.text
                    model._release.set()
            return events

        events = call(client, made, work)
    last = events[-1]["statusUpdate"]["status"]
    assert last["state"] == "TASK_STATE_CANCELED"
    assert last["message"]["parts"][0]["text"] == "The run was stopped before it finished."


def test_a_hand_off_to_an_agent_that_runs_without_approval_waits_for_a_person(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[handoff()], final_text="Handed on.")
    made = make_a2a_server_app(model, read_only=True)
    with TestClient(made.app) as client:
        answered = as_json(
            call(client, made, lambda caller: caller.send("Ask the stub.", return_immediately=True))
        )
        at_rest(client, made, answered["id"])
        read = as_json(call(client, made, lambda caller: caller.get(answered["id"])))
        received_before = list(made.stub.record.messages)
        thread = made.task(client, answered["id"]).thread_id
        pending = client.get(f"/threads/{thread}/state").json()["pending"]
        body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "approve"}]}
        with client.stream("POST", f"/threads/{thread}/resume/unattended", json=body) as answer:
            sse_events(answer.iter_lines())
        received_after = list(made.stub.record.messages)
    assert text_of(read) == "Waiting for a person's approval of: agent stub."
    assert received_before == []
    assert len(received_after) == 1


def test_a_follower_closed_with_a_reader_it_never_read_leaves_nothing_behind(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[TELL], final_text="Sent."))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        received = made.service()
        runs = made.runtime.unattended

        async def scenario() -> tuple[int, int, int, int]:
            follower = received.follow(await made.received.get(accepted.task.task_id))
            assert isinstance(await anext(follower), Status)
            await decide_in_console(made, accepted.task.task_id, "approve")
            # The decision's run handed the follower a reader, which it never read.
            handed = len(received._joined)
            await follower.aclose()
            for _ in range(200):
                if not runs.armed(thread) and not received._drains:
                    break
                await asyncio.sleep(0.01)
            return handed, len(received._joined), len(received._wakers), len(received._drains)

        handed, joined, wakers, drains = client.portal.call(scenario)
        found = ended(client, made, accepted.task.task_id)
    assert handed == 1
    assert (joined, wakers, drains) == (0, 0, 0)
    assert found.state == "completed"


def test_runs_that_start_one_after_another_are_read_in_order(
    make_a2a_server_app: Callable[..., ServerApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(script=[TELL]))
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell.")
        made.finish(client, accepted)
        received = made.service()

        def piece(text: str) -> Any:
            data = {"message": {"type": "ai", "content": text}, "metadata": {"reply": True}}
            return ServerSentEvent(data=json.dumps(data), event="message")

        async def run_of(text: str) -> Any:
            yield piece(text)

        handed = iter([run_of("first "), run_of("second ")])
        starting = False

        def join(_thread: str) -> Any:
            # A run is read whole only by a reader joined as it starts; one joined later
            # misses what it already did, which these runs, of one event, stand for by
            # giving nothing.
            return next(handed) if starting else None

        async def scenario() -> list[Any]:
            nonlocal starting
            follower = received.follow(await made.received.get(accepted.task.task_id))
            # The task waits for a person; then two runs start before it reads either.
            changes = [await anext(follower)]
            monkeypatch.setattr(made.runtime.unattended, "join", join)
            starting = True
            received.on_run_started(accepted.task.thread_id)
            received.on_run_started(accepted.task.thread_id)
            starting = False
            try:
                async with asyncio.timeout(3):
                    while sum(isinstance(change, Piece) for change in changes) < 2:
                        changes.append(await anext(follower))
            except TimeoutError:
                pass
            await follower.aclose()
            return changes

        started = time.monotonic()
        changes = client.portal.call(scenario)
        took = time.monotonic() - started
    # Each run read in full, in the order they started, the second at once after the first;
    # the thread, still paused, is looked at between them.
    assert took < FOLLOW_SECONDS
    assert [change for change in changes if isinstance(change, Piece)] == [
        Piece("first "),
        Piece("second "),
    ]
    assert changes[:3] == [Status(WAITING), Status("Working."), Piece("first ")]


def test_a_failure_told_of_a_run_s_start_leaves_the_run_alone(
    make_a2a_server_app: Callable[..., ServerApp],
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Done."))
    with TestClient(made.app) as client:

        def broken(_thread: str) -> None:
            raise RuntimeError("telling failed")

        made.runtime.unattended.on_start = broken
        accepted = made.accept(client, "Go.")
        events = made.finish(client, accepted)
    assert events[-1][0] == "end"
    assert any("telling of the run's start failed" in r.getMessage() for r in caplog.records)
