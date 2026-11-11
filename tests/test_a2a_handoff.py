"""003 User Story 1: hand work to an agent and get its answer.

FR-005 to FR-008, FR-010, SC-002, SC-004. Every hand-off goes to the stand-in agent inside the
test process through the streaming transport, so the message, the parts, the progress, and the
conversation are the ones the agent really received.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from freyja_agent.delegates import a2a_agents, a2a_tools
from freyja_agent.runtime.agent_tasks import AgentSend, InMemoryAgentTaskStore
from tests.a2a_stub import make_stub, stub_card
from tests.conftest import A2aApp
from tests.fakes import ScriptedChatModel

Events = list[tuple[str, Any]]


def handoff(
    message: str, *, context: str | None = None, call_id: str = "h1", agent: str = "hello"
) -> AIMessage:
    args: dict[str, Any] = {"message": message}
    if context is not None:
        args["context"] = context
    return AIMessage(
        content="", tool_calls=[{"id": call_id, "name": f"handoff_to_{agent}", "args": args}]
    )


DONE = AIMessage(content="Done.")

DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Ask the hello agent.", "subagent_type": "general-purpose"},
        }
    ],
)


def run(client: TestClient, thread_id: str, sse_events: Any, message: str = "go") -> Events:
    with client.stream("POST", f"/threads/{thread_id}/runs", json={"message": message}) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def tool_results(events: Events, name: str) -> list[dict[str, Any]]:
    """The tool messages of ``name`` in the run's ``tools`` updates, in order."""
    return [
        message
        for kind, data in events
        if kind == "update" and data["node"] == "tools"
        for message in data["data"].get("messages", [])
        if message["type"] == "tool" and message["name"] == name
    ]


def progress(events: Events) -> list[dict[str, Any]]:
    return [data["data"] for kind, data in events if kind == "update" and data["node"] == "agent"]


def thread_messages(made: A2aApp, thread_id: str) -> list[Any]:
    state = made.runtime.agent.get_state({"configurable": {"thread_id": thread_id}})
    return list(state.values["messages"])


# Part 1 (T020): what reaches the agent, what comes back, and progress.


def test_a_read_only_hand_off_completes_without_a_pause(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            handoff("Say hello to the ops team.", context="The ops team is on call this week.")
        ],
        final_text="The agent said hello.",
    )
    made = make_a2a_app(model, {"hello": {"read_only": True}})
    with TestClient(made.app) as client:
        events = run(client, "h-1", sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert "interrupt" not in [kind for kind, _ in events]
    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"].startswith("Agent 'hello' completed task ")
    assert "Say hello to the ops team." in result["content"]
    # Exactly the task message and the chosen context reached the agent, nothing else (FR-005).
    (received,) = made.stubs["hello"].record.messages
    assert received.parts == [
        ("text", "Say hello to the ops team.", {}),
        ("text", "The ops team is on call this week.", {"freyja": "context"}),
    ]
    assert "go" not in received.text


def test_a_hand_off_without_context_sends_the_message_alone(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[handoff("Hi.")]), {"hello": {"read_only": True}})
    with TestClient(made.app) as client:
        run(client, "h-2", sse_events)
    (received,) = made.stubs["hello"].record.messages
    assert received.parts == [("text", "Hi.", {})]


def test_a_hand_off_through_the_subagent_completes_the_same_way(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    # Call 1 delegates, call 2 (inside the subagent) hands off.
    model = ScriptedChatModel(script=[DELEGATE, handoff("From the subagent.")], final_text="Done.")
    made = make_a2a_app(model, {"hello": {"read_only": True}})
    with TestClient(made.app) as client:
        events = run(client, "h-3", sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert [m.text for m in made.stubs["hello"].record.messages] == ["From the subagent."]
    # The subagent received the agent's reply as the hand-off's result.
    subagent_call = model.seen[2]
    (reply,) = [
        m for m in subagent_call if isinstance(m, ToolMessage) and m.name == "handoff_to_hello"
    ]
    assert "From the subagent." in reply.content
    # Its progress reached the run's stream from inside the subagent (research R7).
    assert progress(events)


def test_progress_streams_in_order_before_the_result(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("progress 3")]), {"hello": {"read_only": True}}
    )
    with TestClient(made.app) as client:
        events = run(client, "h-4", sse_events)

    updates = progress(events)
    texts = [update["text"] for update in updates]
    assert texts[:4] == ["Working on it.", "Step 1", "Step 2", "Step 3"]
    assert {update["agent"] for update in updates} == {"hello"}
    task_ids = {update["task_id"] for update in updates}
    assert len(task_ids) == 1 and "" not in task_ids
    assert all(update["state"] in ("working", "completed") for update in updates)
    nodes = [data["node"] for kind, data in events if kind == "update"]
    assert nodes.index("tools") > max(i for i, node in enumerate(nodes) if node == "agent")


def test_an_agent_that_does_not_stream_reports_each_observed_change(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(a2a_agents, "POLL_SECONDS", 0.05)
    stub = make_stub("hello", card=stub_card("hello", streaming=False))
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("slow 0.3")]),
        {"hello": {"read_only": True}},
        stubs={"hello": stub},
    )
    with TestClient(made.app) as client:
        events = run(client, "h-5", sse_events)

    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"].startswith("Agent 'hello' completed task ")
    # One event per observed change, not one per check: submitted to working, then completed.
    assert [update["state"] for update in progress(events)] == ["working", "completed"]


def test_parts_of_other_kinds_become_notes(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("parts")]), {"hello": {"read_only": True}}
    )
    with TestClient(made.app) as client:
        events = run(client, "h-6", sse_events)

    (result,) = tool_results(events, "handoff_to_hello")
    content = result["content"]
    assert "Here is the report." in content
    assert '{"ok":true,"total":3}' in content
    assert "[file omitted: image/png]" in content
    assert "[file link omitted: application/pdf]" in content
    # Nothing binary and no link reaches the thread (FR-006).
    stored = " ".join(str(m.content) for m in thread_messages(made, "h-6"))
    assert "PNG" not in stored and "files.example" not in stored


# Part 2 (T021): one conversation per thread and agent.


def test_a_thread_keeps_one_conversation_with_an_agent_across_a_restart(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    store = InMemoryAgentTaskStore()
    stub = make_stub("hello")
    # The script is consumed across model calls: a reply ends each run before the next hand-off.
    script = [handoff("first", call_id="a"), DONE, handoff("second", call_id="b")]
    first = make_a2a_app(
        ScriptedChatModel(script=script),
        {"hello": {"read_only": True}},
        stubs={"hello": stub},
        store=store,
    )
    with TestClient(first.app) as client:
        run(client, "conv-1", sse_events)
        run(client, "conv-1", sse_events)
    # A new runtime on the same store is the service after a restart.
    later = make_a2a_app(
        ScriptedChatModel(
            script=[handoff("third", call_id="c"), DONE, handoff("other", call_id="d")]
        ),
        {"hello": {"read_only": True}},
        stubs={"hello": stub},
        store=store,
    )
    with TestClient(later.app) as client:
        run(client, "conv-1", sse_events)
        run(client, "conv-2", sse_events)

    contexts = {m.text: m.context_id for m in stub.record.messages}
    assert contexts["first"] == contexts["second"] == contexts["third"]
    assert contexts["other"] != contexts["first"]
    assert all(contexts.values())


async def test_a_direct_message_reply_continues_the_conversation_and_lists_no_task(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    script = [handoff("message hi", call_id="a"), DONE, handoff("again", call_id="b")]
    made = make_a2a_app(ScriptedChatModel(script=script), {"hello": {"read_only": True}})
    with TestClient(made.app) as client:
        events = run(client, "msg-1", sse_events)
        run(client, "msg-1", sse_events)

    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"] == "Agent 'hello' replied:\n\nDirect reply: hi"
    first, second = made.stubs["hello"].record.messages
    assert first.context_id and second.context_id == first.context_id
    assert [task.message for task in await made.store.list_tasks("msg-1")] == ["again"]


# Part 3 (T022, T072): the send ledger keeps a step that runs again from sending twice, and
# tells apart the calls a provider gave the same id.

# The pre-recorded sends below are filed under a step these tests pin, as a call in a step
# that runs again would find them.
PINNED_STEP = "tools:pinned"


@pytest.fixture
def pinned_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a2a_tools, "_step", lambda: PINNED_STEP)


async def _ledger(
    store: InMemoryAgentTaskStore, thread_id: str, result: str | None, message: str = "again"
) -> None:
    await store.start_send(
        AgentSend(
            thread_id=thread_id,
            step=PINNED_STEP,
            tool_call_id="h1",
            agent="hello",
            kind="handoff",
            message_id="m-1",
            request=a2a_tools._digest("hello", "handoff", message, None, None),
            sent_at=datetime.now(UTC),
            result=result,
        )
    )


@pytest.mark.usefixtures("pinned_step")
async def test_a_send_already_recorded_returns_its_result_and_sends_nothing(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    store = InMemoryAgentTaskStore()
    await _ledger(store, "led-1", "Agent 'hello' completed task t0:\n\nearlier answer")
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("again")]), {"hello": {"read_only": True}}, store=store
    )
    with TestClient(made.app) as client:
        events = run(client, "led-1", sse_events)
    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"].endswith("earlier answer")
    assert made.stubs["hello"].record.messages == []


@pytest.mark.usefixtures("pinned_step")
async def test_a_send_still_in_flight_is_not_sent_again(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    store = InMemoryAgentTaskStore()
    await _ledger(store, "led-2", None)
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("again")]), {"hello": {"read_only": True}}, store=store
    )
    with TestClient(made.app) as client:
        events = run(client, "led-2", sse_events)
    (result,) = tool_results(events, "handoff_to_hello")
    assert "may or may not have received" in result["content"]
    assert made.stubs["hello"].record.messages == []
    assert events[-1] == ("end", {"status": "completed"})


@pytest.mark.usefixtures("pinned_step")
async def test_a_recorded_send_of_another_message_is_refused_and_nothing_is_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    # The same key for a different message is not a step running again (research R5).
    store = InMemoryAgentTaskStore()
    await _ledger(store, "led-3", "Agent 'hello' replied:\n\nsomething else", message="earlier")
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("again")]), {"hello": {"read_only": True}}, store=store
    )
    with TestClient(made.app) as client:
        events = run(client, "led-3", sse_events)
    (result,) = tool_results(events, "handoff_to_hello")
    assert "already recorded a different message" in result["content"]
    assert "something else" not in result["content"]
    assert made.stubs["hello"].record.messages == []


def test_a_step_that_runs_again_returns_the_recorded_result(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unpinned: the step LangGraph gives the call must be the same when its step runs again,
    # here after the result was recorded but before the step itself was saved.
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("once", call_id="c1")]), {"hello": {"read_only": True}}
    )
    finish = made.store.finish_send

    async def recorded_then_lost(*args: Any, **kwargs: Any) -> None:
        await finish(*args, **kwargs)
        raise RuntimeError("the service stopped before the step was saved")

    config = {"configurable": {"thread_id": "rerun-1"}}
    with TestClient(made.app) as client:
        monkeypatch.setattr(made.store, "finish_send", recorded_then_lost)
        assert run(client, "rerun-1", sse_events)[-1][0] == "error"
        monkeypatch.undo()
        messages = client.portal.call(made.runtime.agent.ainvoke, None, config)["messages"]

    assert [m.text for m in made.stubs["hello"].record.messages] == ["once"]
    (result,) = [m for m in messages if isinstance(m, ToolMessage)]
    assert result.content.startswith("Agent 'hello' completed task ")
    assert "once" in result.content


def test_a_step_that_failed_after_sending_is_not_sent_again(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("once", call_id="c1")]), {"hello": {"read_only": True}}
    )

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database went away after the send")

    config = {"configurable": {"thread_id": "rerun-2"}}
    with TestClient(made.app) as client:
        monkeypatch.setattr(made.store, "finish_send", lost)
        assert run(client, "rerun-2", sse_events)[-1][0] == "error"
        monkeypatch.undo()
        messages = client.portal.call(made.runtime.agent.ainvoke, None, config)["messages"]

    assert [m.text for m in made.stubs["hello"].record.messages] == ["once"]
    (result,) = [m for m in messages if isinstance(m, ToolMessage)]
    assert "may or may not have received" in result.content


def test_a_reused_tool_call_id_in_a_later_run_is_still_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    script = [handoff("first", call_id="c1"), DONE, handoff("second", call_id="c1")]
    made = make_a2a_app(ScriptedChatModel(script=script), {"hello": {"read_only": True}})
    with TestClient(made.app) as client:
        run(client, "reuse-1", sse_events)
        events = run(client, "reuse-1", sse_events)

    assert [m.text for m in made.stubs["hello"].record.messages] == ["first", "second"]
    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"].endswith("second") and "first" not in result["content"]
    # The agent sees two messages, not one sent twice.
    first, second = made.stubs["hello"].record.messages
    assert first.message_id != second.message_id


def test_two_calls_with_one_id_in_one_reply_are_both_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    step = AIMessage(
        content="",
        tool_calls=[
            {"id": "c1", "name": "handoff_to_hello", "args": {"message": "one"}},
            {"id": "c1", "name": "handoff_to_hello", "args": {"message": "two"}},
        ],
    )
    made = make_a2a_app(ScriptedChatModel(script=[step]), {"hello": {"read_only": True}})
    with TestClient(made.app) as client:
        run(client, "reuse-2", sse_events)
    assert sorted(m.text for m in made.stubs["hello"].record.messages) == ["one", "two"]


def test_the_orchestrator_and_the_subagent_reusing_an_id_are_both_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    script = [
        handoff("from the orchestrator", call_id="c1"),
        DELEGATE,
        handoff("from the subagent", call_id="c1"),
    ]
    made = make_a2a_app(
        ScriptedChatModel(script=script, final_text="Done."), {"hello": {"read_only": True}}
    )
    with TestClient(made.app) as client:
        events = run(client, "reuse-3", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    assert [m.text for m in made.stubs["hello"].record.messages] == [
        "from the orchestrator",
        "from the subagent",
    ]


# Part 4 (T060, T063, T064, T071): an agent that misbehaves. Every failure is an error result
# naming the agent, a warning naming it, never the address, and never a second send.

ADDRESS = "http://localhost:9877"
LOGGERS = "freyja_agent"


def misbehaving(
    make_a2a_app: Callable[..., A2aApp], script: list[AIMessage], **options: Any
) -> A2aApp:
    return make_a2a_app(
        ScriptedChatModel(script=script, final_text="Noted."),
        {"hello": {"url": ADDRESS, "read_only": True}},
        **options,
    )


def warnings_about(caplog: pytest.LogCaptureFixture, agent: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and agent in record.getMessage()
    ]


def catalog_entry(client: TestClient, agent: str) -> dict[str, Any]:
    return next(a for a in client.get("/tools").json()["agents"] if a["name"] == agent)


@pytest.mark.parametrize(
    ("message", "status", "heading"),
    [
        ("fail", "failed", "failed."),
        ("reject", "rejected", "rejected the task."),
        ("auth", "failed", "asks for authentication, which is out of scope."),
    ],
)
def test_a_task_the_agent_does_not_complete_is_an_error_result_and_the_run_goes_on(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    message: str,
    status: str,
    heading: str,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGERS)
    made = misbehaving(make_a2a_app, [handoff(message)])
    with TestClient(made.app) as client:
        events = run(client, "bad-1", sse_events)
        tasks = client.get("/threads/bad-1/state").json()["agent_tasks"]
        entry = catalog_entry(client, "hello")

    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"].startswith("Agent 'hello' task ")
    assert heading in result["content"].split("\n")[0]
    assert events[-1] == ("end", {"status": "completed"})
    assert [task["status"] for task in tasks] == [status]
    assert warnings_about(caplog, "hello")
    # The agent answered: it is reachable, whatever it made of the task.
    assert entry["reachable"] is True
    assert len(made.stubs["hello"].record.messages) == 1


def test_an_agent_that_does_not_acknowledge_in_time_leaves_nothing_pending(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGERS)
    made = misbehaving(make_a2a_app, [handoff("hello")], a2a_call_timeout_seconds=0.3)
    with TestClient(made.app) as client:
        made.transports["hello"].hang = True
        events = run(client, "bad-2", sse_events)
        tasks = client.get("/threads/bad-2/state").json()["agent_tasks"]
        entry = catalog_entry(client, "hello")

    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"] == (
        "Agent 'hello' did not acknowledge the task within 0.3 seconds; it may or may not have "
        "received it."
    )
    assert events[-1] == ("end", {"status": "completed"})
    assert tasks == []
    assert entry["reachable"] is False
    assert warnings_about(caplog, "hello")
    assert made.stubs["hello"].record.messages == []


@pytest.mark.parametrize(
    ("blank", "why"),
    [
        ("", "its task id is longer than 256 characters"),
        ("id", "its task id is empty"),
        ("context_id", "its conversation id is empty"),
    ],
)
def test_a_reply_with_a_malformed_id_is_an_error_result(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    blank: str,
    why: str,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGERS)
    if blank:
        # The SDK's own server never sends an empty id (it fails the request instead), so the
        # reply is blanked on arrival, as if the agent had sent it so.
        send = a2a_agents._send

        async def blanked(
            link: Any, message: Any, on_progress: Any, outcome: Any, *rest: Any
        ) -> None:
            await send(link, message, on_progress, outcome, *rest)
            setattr(outcome.task, blank, "")

        monkeypatch.setattr(a2a_agents, "_send", blanked)
        stub = make_stub("hello")
    else:
        stub = make_stub("hello", task_ids=lambda: "x" * 300)
    made = misbehaving(make_a2a_app, [handoff("hello")], stubs={"hello": stub})
    with TestClient(made.app) as client:
        events = run(client, "bad-3", sse_events)
        tasks = client.get("/threads/bad-3/state").json()["agent_tasks"]
        context = client.portal.call(made.store.get_context, "bad-3", "hello")
        entry = catalog_entry(client, "hello")

    (result,) = tool_results(events, "handoff_to_hello")
    assert result["content"] == f"Agent 'hello' replied with something malformed: {why}."
    assert events[-1] == ("end", {"status": "completed"})
    # Nothing from a malformed reply is kept: no task, no conversation.
    assert (tasks, context) == ([], None)
    assert entry["reachable"] is False and why in entry["error"]
    assert warnings_about(caplog, "hello")
    assert len(stub.record.messages) == 1


def test_an_unreachable_agent_is_usable_again_once_it_answers(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGERS)
    script = [handoff("first", call_id="h1"), DONE, handoff("second", call_id="h2")]
    made = misbehaving(make_a2a_app, script)
    with TestClient(made.app) as client:
        made.transports["hello"].down = True
        failed = run(client, "bad-4", sse_events)
        down = catalog_entry(client, "hello")
        made.transports["hello"].down = False
        recovered = run(client, "bad-4", sse_events)
        up = catalog_entry(client, "hello")

    (error,) = tool_results(failed, "handoff_to_hello")
    assert error["content"].startswith("Agent 'hello' could not be reached:")
    assert failed[-1] == ("end", {"status": "completed"})
    assert (down["reachable"], bool(down["error"])) == (False, True)
    (result,) = tool_results(recovered, "handoff_to_hello")
    assert result["content"].startswith("Agent 'hello' completed task ")
    assert (up["reachable"], up["error"]) == (True, None)
    # The failed hand-off was not sent again: the stub received only the second.
    assert [m.text for m in made.stubs["hello"].record.messages] == ["second"]
    assert warnings_about(caplog, "hello")


def test_no_error_result_log_thread_or_catalog_contains_the_address(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGERS)
    made = misbehaving(make_a2a_app, [handoff("hello")])

    async def refused(request: httpx.Request) -> httpx.Response:
        # What a real refused connection says: the address it tried.
        raise httpx.ConnectError(
            f"Connect call failed to {ADDRESS}/ (localhost:9877)", request=request
        )

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.transports["hello"], "handle_async_request", refused)
        events = run(client, "bad-5", sse_events)
        texts = [
            json.dumps(events),
            client.get("/threads/bad-5/state").text,
            client.get("/tools").text,
        ]

    (error,) = tool_results(events, "handoff_to_hello")
    assert "<address>" in error["content"]
    logged = [record.getMessage() for record in caplog.records]
    assert any("hello" in line and "<address>" in line for line in logged)
    for text in [*texts, *logged]:
        assert "localhost" not in text and "9877" not in text, text


def test_a_status_check_while_watching_updates_the_agents_reachability(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGERS)
    made = misbehaving(make_a2a_app, [handoff("hold: report")], a2a_call_timeout_seconds=0.3)
    with TestClient(made.app) as client:
        run(client, "bad-6", sse_events)
        assert made.runtime.worker is not None
        made.transports["hello"].down = True
        client.portal.call(made.runtime.worker.tick)
        down = catalog_entry(client, "hello")
        made.transports["hello"].down = False
        client.portal.call(made.runtime.worker.tick)
        up = catalog_entry(client, "hello")
        made.stubs["hello"].release()

    assert (down["reachable"], up["reachable"]) == (False, True)
    checks = warnings_about(caplog, "hello")
    assert any("bad-6" in line for line in checks)


def test_a_hand_off_left_in_flight_by_a_stop_is_reported_as_possibly_received(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryAgentTaskStore(clock=clock.now)
    saver = InMemorySaver()
    stub = make_stub("hello")
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("once", call_id="c1")]),
        {"hello": {"read_only": True}},
        stubs={"hello": stub},
        store=store,
        saver=saver,
    )

    async def stopped(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the service stopped while the hand-off was in flight")

    with TestClient(made.app) as client:
        monkeypatch.setattr(store, "finish_send", stopped)
        assert run(client, "flight-1", sse_events)[-1][0] == "error"
        monkeypatch.undo()

    model = ScriptedChatModel(final_text="It may or may not have arrived.")
    restarted = make_a2a_app(
        model, {"hello": {"read_only": True}}, stubs={"hello": stub}, store=store, saver=saver
    )
    with TestClient(restarted.app) as client:
        events = run(client, "flight-1", sse_events, message="Did the hand-off go through?")
        send = client.portal.call(store.get_send, "flight-1", *_only_send(client, store))
        messages = thread_messages(restarted, "flight-1")

    assert events[-1] == ("end", {"status": "completed"})
    (answer,) = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert answer.tool_call_id == "c1"
    assert answer.content == (
        "Agent 'hello' may or may not have received this message; Freyja does not know, and "
        "will not send it again."
    )
    assert "did not complete" not in " ".join(str(m.content) for m in messages)
    # The answer directly follows the call it answers, as a provider requires.
    call = next(i for i, m in enumerate(messages) if isinstance(m, AIMessage) and m.tool_calls)
    assert isinstance(messages[call + 1], ToolMessage)
    assert messages[call + 1].tool_call_id == "c1"
    assert send is not None and send.result == answer.content
    assert [m.text for m in stub.record.messages] == ["once"]


def _only_send(client: TestClient, store: InMemoryAgentTaskStore) -> tuple[str, str]:
    (send,) = store._sends.values()
    return send.step, send.tool_call_id


def test_the_workers_logs_name_the_agent_and_never_the_address(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    clock: Any,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGERS)
    made = misbehaving(
        make_a2a_app,
        [handoff("hold: report")],
        a2a_call_timeout_seconds=0.3,
        a2a_task_max_age_seconds=100,
    )
    transport = made.transports["hello"]
    serve = transport.handle_async_request

    async def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"Connect call failed to {ADDRESS}/ (localhost:9877)", request=request
        )

    with TestClient(made.app) as client:
        run(client, "bad-7", sse_events)
        assert made.runtime.worker is not None
        monkeypatch.setattr(transport, "handle_async_request", refused)
        client.portal.call(made.runtime.worker.tick)  # a failed status check
        monkeypatch.setattr(transport, "handle_async_request", serve)
        clock.advance(101)
        client.portal.call(made.runtime.worker.tick)  # a give-up, then its background run
        made.stubs["hello"].release()

    logged = [record.getMessage() for record in caplog.records]
    check = [line for line in logged if "could not check task" in line]
    gave_up = [line for line in logged if "gave up task" in line]
    attempt = [line for line in logged if "attempt 1 of" in line]
    assert check and "Agent hello" in check[0] and "bad-7" in check[0]
    assert "<address>" in check[0]
    assert gave_up and "bad-7" in gave_up[0] and "hello" in gave_up[0]
    assert attempt and "bad-7" in attempt[0] and "hello/" in attempt[0]
    for line in logged:
        assert "localhost" not in line and "9877" not in line, line


def test_a_stream_that_breaks_after_acknowledgment_leaves_the_task_pending(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGERS)
    send = a2a_agents._send

    async def broken(*args: Any) -> None:
        # The task arrives, then the connection drops while the agent works on.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(send(*args), timeout=0.2)
        raise httpx.ReadError("the connection broke")

    monkeypatch.setattr(a2a_agents, "_send", broken)
    made = misbehaving(make_a2a_app, [handoff("hold: report")])
    with TestClient(made.app) as client:
        events = run(client, "bad-8", sse_events)
        tasks = client.get("/threads/bad-8/state").json()["agent_tasks"]
        entry = catalog_entry(client, "hello")
        made.stubs["hello"].release()

    (result,) = tool_results(events, "handoff_to_hello")
    assert "it is pending" in result["content"]
    # The agent has the task: it is kept and watched, not forgotten behind an error.
    assert [(task["status"], task["outcome_delivered"]) for task in tasks] == [("working", False)]
    assert entry["reachable"] is False
    assert len(made.stubs["hello"].record.messages) == 1
