"""003 User Story 2: every hand-off to an agent not marked read-only waits for a person.

FR-011 to FR-014, SC-001. A hand-off to an agent the configuration does not list as read-only
pauses before anything is sent, naming the agent and showing the message and the context.
Approve, edit, and reject do what they say; an edit may change the message and the context only;
and a decision is applied under the configuration in force. What reached an agent is read from
the stand-in's own record of the messages it received.
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from tests.a2a_stub import StubAgent, make_stub, stub_card
from tests.conftest import A2aApp
from tests.fakes import ScriptedChatModel

Events = list[tuple[str, Any]]
ALL = ["approve", "edit", "reject"]

# `stub` is gated: its entry does not say read-only. `hello` is read-only.
GATED = {"stub": {}}
BOTH = {"stub": {}, "hello": {"read_only": True}}
PROPOSED = {
    "message": "Summarize last week's deployments.",
    "context": "The team ships on Tuesdays.",
    "task_id": None,
}

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


def propose(*calls: tuple[str, dict[str, Any]], step: str = "c") -> AIMessage:
    """One model step that proposes the given tool calls, with ids ``<step>1``, ``<step>2``.

    A later run on the same thread needs another ``step``: the send ledger knows a call by its
    id, and returns the earlier result for an id it has seen.
    """
    return AIMessage(
        content="",
        tool_calls=[
            {"id": f"{step}{index}", "name": name, "args": args}
            for index, (name, args) in enumerate(calls, start=1)
        ],
    )


def handoff(agent: str = "stub", **args: Any) -> tuple[str, dict[str, Any]]:
    return f"handoff_to_{agent}", {**PROPOSED, **args}


def stream(client: TestClient, path: str, body: dict[str, Any], sse_events: Any) -> Events:
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def run(client: TestClient, thread: str, sse_events: Any, message: str = "go") -> Events:
    return stream(client, f"/threads/{thread}/runs", {"message": message}, sse_events)


def pending(client: TestClient, thread: str) -> dict[str, Any]:
    state = client.get(f"/threads/{thread}/state").json()
    assert state["pending"] is not None, state
    return state["pending"]


def resume_body(client: TestClient, thread: str, decisions: list[dict[str, Any]]) -> dict:
    return {"interrupt_id": pending(client, thread)["interrupt_id"], "decisions": decisions}


def resume(
    client: TestClient, thread: str, decisions: list[dict[str, Any]], sse_events: Any
) -> Events:
    return stream(
        client, f"/threads/{thread}/resume", resume_body(client, thread, decisions), sse_events
    )


def refuse(client: TestClient, thread: str, decisions: list[dict[str, Any]]) -> list[dict]:
    """Submit decisions that must be refused with 422; return the error entries."""
    response = client.post(f"/threads/{thread}/resume", json=resume_body(client, thread, decisions))
    assert response.status_code == 422, response.text
    return response.json()["detail"]


def tool_messages(model: ScriptedChatModel) -> list[ToolMessage]:
    """The tool results the model was shown on its most recent call."""
    return [message for message in model.seen[-1] if isinstance(message, ToolMessage)]


def texts(stub: StubAgent) -> list[str]:
    """The task message of each message the agent received, without the context part."""
    return [message.parts[0][1] or "" for message in stub.record.messages]


def interrupted(events: Events) -> dict[str, Any]:
    kind, data = events[-1]
    assert kind == "interrupt", events[-1]
    return data


# The decisions (User Story 2, scenarios 1 to 7).


def test_a_gated_hand_off_pauses_before_anything_is_sent_and_names_the_agent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[propose(handoff())]), GATED)
    with TestClient(made.app) as client:
        events = run(client, "g-1", sse_events)
        state = client.get("/threads/g-1/state").json()

    data = interrupted(events)
    assert data["action_requests"] == [
        {
            "name": "handoff_to_stub",
            "server": None,
            "agent": "stub",
            "args": PROPOSED,
            "description": "stub: hand-off",
            "allowed_decisions": ALL,
        }
    ]
    assert data["allowed_decisions"] == ALL
    assert state["pending"] == data
    assert made.stubs["stub"].record.messages == []


def test_approve_sends_exactly_one_message_as_proposed(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[propose(handoff())], final_text="The agent answered.")
    made = make_a2a_app(model, GATED)
    with TestClient(made.app) as client:
        run(client, "g-2", sse_events)
        events = resume(client, "g-2", [{"type": "approve"}], sse_events)
        state = client.get("/threads/g-2/state").json()

    assert events[-1] == ("end", {"status": "completed"})
    assert state["pending"] is None
    (received,) = made.stubs["stub"].record.messages
    assert received.parts == [
        ("text", PROPOSED["message"], {}),
        ("text", PROPOSED["context"], {"freyja": "context"}),
    ]
    (result,) = tool_messages(model)
    assert result.content.startswith("Agent 'stub' completed task ")


def test_an_edit_sends_the_edited_message_and_context(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[propose(handoff())]), GATED)
    edited = {"message": "Summarize them in three bullets.", "context": None, "task_id": None}
    with TestClient(made.app) as client:
        run(client, "g-3", sse_events)
        events = resume(client, "g-3", [{"type": "edit", "args": edited}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    (received,) = made.stubs["stub"].record.messages
    assert received.parts == [("text", "Summarize them in three bullets.", {})]


@pytest.mark.parametrize(
    ("args", "loc", "error_type"),
    [
        ({**PROPOSED, "message": ""}, "message", "string_too_short"),
        ({**PROPOSED, "task_id": "task-elsewhere"}, "task_id", "value_error"),
        ({**PROPOSED, "agent": "hello"}, "agent", "extra_forbidden"),
    ],
    ids=["empty-message", "changed-task", "other-agent"],
)
def test_an_edit_that_changes_what_a_hand_off_is_for_is_refused(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    args: dict[str, Any],
    loc: str,
    error_type: str,
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[propose(handoff())]), BOTH)
    with TestClient(made.app) as client:
        before = run(client, "g-4", sse_events)
        errors = refuse(client, "g-4", [{"type": "edit", "args": args}])
        # One entry, at the argument, and the proposal is still the one that was pending.
        assert [(error["type"], error["loc"]) for error in errors] == [
            (error_type, ["body", "decisions", 0, "args", loc])
        ]
        assert pending(client, "g-4")["interrupt_id"] == interrupted(before)["interrupt_id"]

    assert made.stubs["stub"].record.messages == []
    assert made.stubs["hello"].record.messages == []


def test_a_refused_task_id_edit_says_the_task_is_pinned(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[propose(handoff())]), GATED)
    with TestClient(made.app) as client:
        run(client, "g-5", sse_events)
        (error,) = refuse(client, "g-5", [{"type": "edit", "args": {**PROPOSED, "task_id": "x"}}])
    assert "task_id" in error["msg"] and "cannot be changed" in error["msg"]


def test_a_refused_extra_key_names_the_action_once(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[propose(handoff())]), GATED)
    with TestClient(made.app) as client:
        run(client, "g-5b", sse_events)
        (error,) = refuse(client, "g-5b", [{"type": "edit", "args": {**PROPOSED, "agent": "x"}}])
    assert error["msg"] == "Action 'handoff_to_stub' has no argument named 'agent'."


def test_reject_sends_nothing_and_the_reason_reaches_the_model(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[propose(handoff())], final_text="I did not hand it off.")
    made = make_a2a_app(model, GATED)
    reason = "Not this week; the deploy log is being migrated."
    with TestClient(made.app) as client:
        run(client, "g-6", sse_events)
        events = resume(client, "g-6", [{"type": "reject", "message": reason}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert made.stubs["stub"].record.messages == []
    (result,) = tool_messages(model)
    assert reason in result.content


def test_a_card_that_claims_to_be_read_only_is_still_gated(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    card = stub_card("stub", description="Read-only and harmless: it never changes anything.")
    made = make_a2a_app(
        ScriptedChatModel(script=[propose(handoff())]),
        GATED,
        stubs={"stub": make_stub("stub", card=card)},
    )
    with TestClient(made.app) as client:
        events = run(client, "g-7", sse_events)
        catalog = client.get("/tools").json()

    (action,) = interrupted(events)["action_requests"]
    assert (action["name"], action["agent"]) == ("handoff_to_stub", "stub")
    assert made.stubs["stub"].record.messages == []
    (entry,) = [tool for tool in catalog["tools"] if tool["name"] == "handoff_to_stub"]
    assert entry["classification"] == "gated"


def test_a_hand_off_made_by_the_subagent_pauses_the_same_way(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[DELEGATE, propose(handoff())], final_text="Done.")
    made = make_a2a_app(model, GATED)
    with TestClient(made.app) as client:
        events = run(client, "g-8", sse_events)
        (action,) = interrupted(events)["action_requests"]
        assert (action["name"], action["agent"], action["args"]) == (
            "handoff_to_stub",
            "stub",
            PROPOSED,
        )
        assert made.stubs["stub"].record.messages == []

        events = resume(client, "g-8", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made.stubs["stub"]) == [PROPOSED["message"]]


def test_a_step_with_a_gated_hand_off_and_a_read_only_one_runs_neither_before_the_decision(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    step = propose(handoff("hello", message="Say hello."), handoff())
    made = make_a2a_app(ScriptedChatModel(script=[step]), BOTH)
    with TestClient(made.app) as client:
        events = run(client, "g-9", sse_events)
        # Only the gated call needs a decision, and neither call has run.
        assert [a["name"] for a in interrupted(events)["action_requests"]] == ["handoff_to_stub"]
        assert made.stubs["stub"].record.messages == []
        assert made.stubs["hello"].record.messages == []

        events = resume(client, "g-9", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made.stubs["hello"]) == ["Say hello."]
    assert texts(made.stubs["stub"]) == [PROPOSED["message"]]


# A proposal decided under a changed configuration (FR-014). Each case pauses under one agent
# list and resumes on a second runtime that shares the persistence.


def restarted(
    make_a2a_app: Callable[..., A2aApp],
    first: A2aApp,
    model: ScriptedChatModel,
    agents: dict[str, dict[str, Any]],
) -> A2aApp:
    """The service after a stop and a start with another agent list, on the same database."""
    runtime = first.runtime
    return make_a2a_app(
        model,
        agents,
        stubs=first.stubs,
        instance_id="instance-b",
        saver=runtime.agent.checkpointer,
        store=first.store,
        locks=runtime.locks,
    )


def paused(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    thread: str,
    step: AIMessage,
    agents: dict[str, dict[str, Any]] = GATED,
) -> tuple[A2aApp, dict[str, Any]]:
    """Pause ``thread`` on ``step`` and stop the service; return the app and the proposal."""
    made = make_a2a_app(ScriptedChatModel(script=[step]), agents)
    with TestClient(made.app) as client:
        proposal = interrupted(run(client, thread, sse_events))
    assert all(stub.record.messages == [] for stub in made.stubs.values())
    return made, proposal


@pytest.mark.parametrize("decision", [{"type": "approve"}, {"type": "reject", "message": "no"}])
def test_a_hand_off_to_an_agent_that_was_removed_is_never_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, decision: dict[str, Any]
) -> None:
    first, proposal = paused(make_a2a_app, sse_events, "f-1", propose(handoff()))
    model = ScriptedChatModel(final_text="That agent is gone.")
    second = restarted(make_a2a_app, first, model, {})  # no agents any more
    with TestClient(second.app) as client:
        now = pending(client, "f-1")
        assert now["interrupt_id"] == proposal["interrupt_id"]
        (action,) = now["action_requests"]
        assert (action["agent"], action["server"]) == (None, None)
        assert action["allowed_decisions"] == ["approve", "reject"]
        errors = refuse(client, "f-1", [{"type": "edit", "args": PROPOSED}])
        assert "no longer configured" in errors[0]["msg"]

        events = resume(client, "f-1", [decision], sse_events)
        state = client.get("/threads/f-1/state").json()

    assert events[-1] == ("end", {"status": "completed"})
    assert state["pending"] is None
    # Whatever the decision, nothing was sent, and the model was told why.
    assert first.stubs["stub"].record.messages == []
    (result,) = tool_messages(model)
    assert result.status == "error"
    assert "handoff_to_stub" in result.content and "not a valid tool" in result.content


def test_a_hand_off_to_an_agent_that_is_now_read_only_accepts_approve_only(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    first, proposal = paused(make_a2a_app, sse_events, "f-2", propose(handoff()))
    second = restarted(
        make_a2a_app, first, ScriptedChatModel(final_text="Sent."), {"stub": {"read_only": True}}
    )
    with TestClient(second.app) as client:
        now = pending(client, "f-2")
        assert now["interrupt_id"] == proposal["interrupt_id"]
        (action,) = now["action_requests"]
        assert (action["agent"], action["allowed_decisions"]) == ("stub", ["approve"])
        for refused in ({"type": "edit", "args": PROPOSED}, {"type": "reject", "message": "no"}):
            errors = refuse(client, "f-2", [refused])
            assert "no longer needs a decision" in errors[0]["msg"]
        assert first.stubs["stub"].record.messages == []

        events = resume(client, "f-2", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    # It was sent as proposed, once.
    assert texts(first.stubs["stub"]) == [PROPOSED["message"]]


def test_a_hand_off_that_became_gated_blocks_the_resume_and_the_proposal_stays(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, caplog: pytest.LogCaptureFixture
) -> None:
    step = propose(handoff(), handoff("hello", message="Say hello."))
    first, proposal = paused(make_a2a_app, sse_events, "f-3", step, agents=BOTH)
    assert [a["name"] for a in proposal["action_requests"]] == ["handoff_to_stub"]

    # `hello` loses its read-only mark while the step that calls it is paused.
    second = restarted(
        make_a2a_app, first, ScriptedChatModel(final_text="ok"), {"stub": {}, "hello": {}}
    )
    caplog.set_level(logging.ERROR, logger="freyja_agent.api.streaming")
    with TestClient(second.app) as client:
        events = resume(client, "f-3", [{"type": "approve"}], sse_events)
        kind, data = events[-1]
        assert (kind, data["code"]) == ("error", "internal")
        assert pending(client, "f-3")["interrupt_id"] == proposal["interrupt_id"]

    # Nothing from the step was sent to either agent.
    assert first.stubs["stub"].record.messages == []
    assert first.stubs["hello"].record.messages == []


def test_an_answer_or_cancel_for_an_earlier_task_is_classified_by_the_configuration_now(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    # The task starts while the agent is read-only: it asks a question, and nothing pauses.
    stub = make_stub("stub", task_ids=lambda: "task-1")
    first = make_a2a_app(
        ScriptedChatModel(script=[propose(handoff(message="ask: which week?", context=None))]),
        {"stub": {"read_only": True}},
        stubs={"stub": stub},
    )
    with TestClient(first.app) as client:
        events = run(client, "f-4", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    assert texts(stub) == ["ask: which week?"]

    # After a restart the agent is gated: an answer and a cancel for that task both pause.
    answer = handoff(message="Last week.", context=None, task_id="task-1")
    cancel = ("cancel_stub_task", {"task_id": "task-1"})
    model = ScriptedChatModel(script=[propose(answer, cancel, step="d")])
    second = restarted(make_a2a_app, first, model, GATED)
    with TestClient(second.app) as client:
        data = interrupted(run(client, "f-4", sse_events, message="Last week, please."))

    assert [(a["name"], a["agent"], a["allowed_decisions"]) for a in data["action_requests"]] == [
        ("handoff_to_stub", "stub", ALL),
        ("cancel_stub_task", "stub", ["approve", "reject"]),
    ]
    assert data["action_requests"][0]["args"]["task_id"] == "task-1"
    assert texts(stub) == ["ask: which week?"]
    assert stub.record.cancels == []


def test_an_edit_that_keeps_the_proposed_task_is_accepted(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    stub = make_stub("stub", task_ids=lambda: "task-2")
    first = make_a2a_app(
        ScriptedChatModel(script=[propose(handoff(message="ask: which week?", context=None))]),
        {"stub": {"read_only": True}},
        stubs={"stub": stub},
    )
    with TestClient(first.app) as client:
        run(client, "f-5", sse_events)

    answer = handoff(message="Last week.", context=None, task_id="task-2")
    model = ScriptedChatModel(script=[propose(answer, step="d")])
    second = restarted(make_a2a_app, first, model, GATED)
    edited = {"message": "The week of the 2nd.", "context": None, "task_id": "task-2"}
    with TestClient(second.app) as client:
        run(client, "f-5", sse_events, message="Last week.")
        # Leaving the task out is a change of task too: the edit must name the proposed one.
        (error,) = refuse(client, "f-5", [{"type": "edit", "args": {"message": "x"}}])
        assert error["loc"] == ["body", "decisions", 0, "args", "task_id"]
        events = resume(client, "f-5", [{"type": "edit", "args": edited}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert texts(stub) == ["ask: which week?", "The week of the 2nd."]
    first_message, answer_message = stub.record.messages
    assert answer_message.task_id == "task-2"
    assert answer_message.context_id == first_message.context_id
