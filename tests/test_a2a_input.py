"""003 User Story 3: an agent asks for more input.

FR-015, FR-016. The stand-in agent asks one question for a task message starting with ``ask``,
and completes the same task once a message continues it. The question comes back to the
orchestrator as a result marked as needing input; the user's answer is a hand-off with the
task's id, gated like any message to that agent.
"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from freyja_agent.delegates import a2a_agents
from freyja_agent.runtime.agent_tasks import AgentSend
from tests.a2a_stub import StubAgent, make_stub, stub_card
from tests.conftest import A2aApp
from tests.fakes import ScriptedChatModel

Events = list[tuple[str, Any]]
TASK = "task-ask-1"
QUESTION = "Which date should it be?"
ASKED = f"Agent 'stub' needs input for task {TASK}:"
ASK_THE_USER = AIMessage(content=f"The stub agent asks: {QUESTION}")

DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Answer the stub agent.", "subagent_type": "general-purpose"},
        }
    ],
)


def handoff(message: str, *, call_id: str, task_id: str | None = None) -> AIMessage:
    args: dict[str, Any] = {"message": message}
    if task_id is not None:
        args["task_id"] = task_id
    return AIMessage(
        content="", tool_calls=[{"id": call_id, "name": "handoff_to_stub", "args": args}]
    )


def stub() -> StubAgent:
    return make_stub("stub", task_ids=lambda: TASK)


def stream(client: TestClient, path: str, body: dict[str, Any], sse_events: Any) -> Events:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def run(client: TestClient, thread: str, message: str, sse_events: Any) -> Events:
    return stream(client, f"/threads/{thread}/runs", {"message": message}, sse_events)


def state(client: TestClient, thread: str) -> dict[str, Any]:
    response = client.get(f"/threads/{thread}/state")
    assert response.status_code == 200, response.text
    return response.json()


def approve(client: TestClient, thread: str, sse_events: Any) -> Events:
    body = {
        "interrupt_id": state(client, thread)["pending"]["interrupt_id"],
        "decisions": [{"type": "approve"}],
    }
    return stream(client, f"/threads/{thread}/resume", body, sse_events)


def results(model: ScriptedChatModel, call: int) -> list[ToolMessage]:
    return [
        m for m in model.seen[call] if isinstance(m, ToolMessage) and m.name == "handoff_to_stub"
    ]


def task_status(client: TestClient, thread: str) -> list[tuple[str, str]]:
    return [(task["task_id"], task["status"]) for task in state(client, thread)["agent_tasks"]]


def test_a_question_reaches_the_user_and_the_answer_continues_the_same_task(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    script = [
        handoff("ask: plan the release", call_id="c1"),
        ASK_THE_USER,
        handoff("Friday at 10.", call_id="c2", task_id=TASK),
    ]
    model = ScriptedChatModel(script=script, final_text="The release is planned.")
    made = make_a2a_app(model, {"stub": {}}, stubs={"stub": stub()})
    with TestClient(made.app) as client:
        assert run(client, "in-1", "Plan the release with the stub.", sse_events)[-1][0] == (
            "interrupt"
        )
        events = approve(client, "in-1", sse_events)
        assert events[-1] == ("end", {"status": "completed"})

        # The orchestrator received the question, marked as needing input and naming the task.
        (result,) = results(model, 1)
        assert result.status != "error"
        assert result.content.startswith(ASKED)
        assert QUESTION in result.content
        # The reply that asks the user is the orchestrator's own message, not the agent's.
        after = state(client, "in-1")
        last = after["messages"][-1]
        assert (last["type"], last["name"]) == ("ai", "freyja")
        assert QUESTION in last["content"]
        assert task_status(client, "in-1") == [(TASK, "input_required")]

        # The answer is a hand-off in that task, gated like any message to this agent.
        kind, pause = run(client, "in-1", "Friday at 10.", sse_events)[-1]
        assert kind == "interrupt"
        (action,) = pause["action_requests"]
        assert (action["name"], action["agent"]) == ("handoff_to_stub", "stub")
        assert action["args"]["message"] == "Friday at 10."
        assert action["args"]["task_id"] == TASK
        assert len(made.stubs["stub"].record.messages) == 1

        assert approve(client, "in-1", sse_events)[-1] == ("end", {"status": "completed"})
        (task,) = state(client, "in-1")["agent_tasks"]

    first, answer = made.stubs["stub"].record.messages
    assert answer.text == "Friday at 10."
    assert answer.task_id == TASK
    assert answer.context_id == first.context_id
    *_, done = results(model, -1)
    assert done.content.startswith(f"Agent 'stub' completed task {TASK}:")
    assert "Answered: Friday at 10." in done.content
    # One task, started with the first message, now ended.
    assert task["message"] == "ask: plan the release"
    assert task["status"] == "completed"
    assert task["ended_at"] is not None
    assert task["outcome_delivered"] is True


def test_an_answer_is_recorded_as_an_answer(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = [
        handoff("ask: plan the release", call_id="c1"),
        ASK_THE_USER,
        handoff("Friday.", call_id="c2", task_id=TASK),
    ]
    made = make_a2a_app(
        ScriptedChatModel(script=script), {"stub": {"read_only": True}}, stubs={"stub": stub()}
    )
    started: list[AgentSend] = []
    start_send = made.store.start_send

    async def recording(send: AgentSend) -> bool:
        started.append(send)
        return await start_send(send)

    monkeypatch.setattr(made.store, "start_send", recording)
    with TestClient(made.app) as client:
        run(client, "in-2", "Plan the release.", sse_events)
        run(client, "in-2", "Friday.", sse_events)
    assert [(send.kind, send.agent) for send in started] == [
        ("handoff", "stub"),
        ("answer", "stub"),
    ]


def test_an_unrelated_message_leaves_the_task_waiting(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    script = [
        handoff("ask: plan the release", call_id="c1"),
        ASK_THE_USER,
        AIMessage(content="It is sunny. The stub agent is still waiting for a date."),
    ]
    made = make_a2a_app(
        ScriptedChatModel(script=script), {"stub": {"read_only": True}}, stubs={"stub": stub()}
    )
    with TestClient(made.app) as client:
        run(client, "in-3", "Plan the release.", sse_events)
        assert task_status(client, "in-3") == [(TASK, "input_required")]
        run(client, "in-3", "What is the weather?", sse_events)
        assert task_status(client, "in-3") == [(TASK, "input_required")]
        (task,) = state(client, "in-3")["agent_tasks"]
    assert task["ended_at"] is None
    assert len(made.stubs["stub"].record.messages) == 1


def test_the_subagent_can_answer_the_question(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    script = [
        handoff("ask: plan the release", call_id="c1"),
        ASK_THE_USER,
        DELEGATE,
        handoff("Monday.", call_id="s1", task_id=TASK),
    ]
    made = make_a2a_app(
        ScriptedChatModel(script=script, final_text="Done."),
        {"stub": {"read_only": True}},
        stubs={"stub": stub()},
    )
    with TestClient(made.app) as client:
        run(client, "in-4", "Plan the release.", sse_events)
        assert run(client, "in-4", "Monday.", sse_events)[-1] == ("end", {"status": "completed"})
        assert task_status(client, "in-4") == [(TASK, "completed")]
    _, answer = made.stubs["stub"].record.messages
    assert (answer.text, answer.task_id) == ("Monday.", TASK)


def test_an_agent_that_does_not_stream_takes_the_answer_the_same_way(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(a2a_agents, "POLL_SECONDS", 0.05)
    script = [
        handoff("ask: plan the release", call_id="c1"),
        ASK_THE_USER,
        handoff("Friday.", call_id="c2", task_id=TASK),
    ]
    agent = make_stub("stub", card=stub_card("stub", streaming=False), task_ids=lambda: TASK)
    made = make_a2a_app(
        ScriptedChatModel(script=script), {"stub": {"read_only": True}}, stubs={"stub": agent}
    )
    with TestClient(made.app) as client:
        run(client, "in-5", "Plan the release.", sse_events)
        assert task_status(client, "in-5") == [(TASK, "input_required")]
        run(client, "in-5", "Friday.", sse_events)
        assert task_status(client, "in-5") == [(TASK, "completed")]


def test_an_answer_for_another_threads_task_is_refused_and_nothing_is_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    script = [
        handoff("ask: plan the release", call_id="c1"),
        ASK_THE_USER,
        handoff("Friday.", call_id="c2", task_id=TASK),
    ]
    model = ScriptedChatModel(script=script, final_text="I cannot answer that task here.")
    made = make_a2a_app(model, {"stub": {"read_only": True}}, stubs={"stub": stub()})
    with TestClient(made.app) as client:
        run(client, "in-6", "Plan the release.", sse_events)
        # Another thread names the first thread's task.
        run(client, "in-7", "Friday.", sse_events)
        assert task_status(client, "in-6") == [(TASK, "input_required")]
        assert state(client, "in-7")["agent_tasks"] == []

    assert len(made.stubs["stub"].record.messages) == 1
    (refused,) = results(model, -1)
    assert refused.status == "error"
    assert f"no task {TASK} on this thread" in refused.content


def test_the_model_is_told_to_ask_the_user_and_continue_the_same_task(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_app(model, {"stub": {"read_only": True}}, stubs={"stub": stub()})
    with TestClient(made.app) as client:
        run(client, "in-8", "hi", sse_events)
    system = model.seen[0][0].text
    assert "When an agent needs input, ask the" in system
    assert "with the task's id and their answer" in system
