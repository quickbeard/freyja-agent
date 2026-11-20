"""005: a message is only ever sent to the agent it was proposed for (part 1), and an
agent's approval choice alone decides whether a message to it waits for a person (part 2).

FR-023, FR-030; research R8; data-model.md "Proposed call" and "What a call gives without
sending". A pause holds a tool's name and nothing about which agent had the name. So each call
of an agent's tool that a model proposes is kept as a row, from the model's reply until the
call's send is finished, and the tool reads it before it sends: a proposal made for one agent
is never sent to another that has its name by the time it is decided.

Part 2: FR-019, FR-022, FR-023, FR-030, SC-004; research R7, R9. A saved agent that requires
approval pauses every kind of message, wherever it is proposed. A switch to running without
approval holds from the next run. A switch to requiring approval, and a removal, hold at once:
a run in progress reads the saved agent right before it sends, and sends nothing to an agent
that now requires approval or is gone. A paused step that came to hold a call that needs a
decision cannot be continued, and the refusal names the agent.

Every agent is the stand-in, inside the test process. Saved agents are written to the store,
as `tests/test_agents_apply.py` part 1 does; the routes that add and change one are covered
there and in `tests/test_agents_api.py`.
"""

import asyncio
import dataclasses
import logging
import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from freyja_agent.delegates import delegation_worker
from freyja_agent.runtime.agent_tasks import AgentTaskRecord, ProposedCall
from tests.a2a_stub import StubAgent, make_stub, stub_card
from tests.conftest import A2aApp, saved_card
from tests.fakes import FakeClock, LabelledChatModel, ScriptedChatModel
from tests.test_a2a_background import RETRY, FlakyChatModel, follow_up, ready_outcome, retry_app
from tests.test_a2a_gate import (
    DELEGATE,
    PROPOSED,
    interrupted,
    pending,
    propose,
    refuse,
    resume,
)
from tests.test_a2a_handoff import DONE, handoff, run, tool_results
from tests.test_a2a_pending import stub, tick
from tests.test_agents_apply import OTHER_URL, choose, save, texts
from tests.test_agents_startup import SAVED_URL, TOOLS, seed_task, sent, tools, warnings

Events = list[tuple[str, Any]]
# What a call gives when it was not proposed for the agent its tool belongs to now.
KEPT_BACK = (
    "Agent '{agent}': the agent this message was proposed for was removed; nothing was sent."
)
HELPER = {"helper": {"url": SAVED_URL}}
TO_HELPER = ("handoff_to_helper", {"message": "Summarize the week."})


def rows(client: TestClient, made: A2aApp, thread: str) -> list[ProposedCall]:
    return client.portal.call(made.store.list_calls, thread)


def keys(calls: list[ProposedCall]) -> list[tuple[str, str]]:
    return [(call.namespace, call.tool_call_id) for call in calls]


def seed(client: TestClient, made: A2aApp, thread: str, namespace: str = "tools:stale") -> None:
    """Leave a row behind on ``thread``, as a step that never finished would."""
    stale = ProposedCall(
        thread_id=thread,
        namespace=namespace,
        tool_call_id="stale-1",
        agent="stub",
        agent_id=None,
        gated=True,
    )
    client.portal.call(made.store.replace_calls, thread, namespace, [stale])


def at_the_send(made: A2aApp, monkeypatch: pytest.MonkeyPatch, thread: str) -> list[Any]:
    """Collects the thread's rows as they are when a send is recorded as finished."""
    seen: list[Any] = []
    finish = made.store.finish_send

    async def looking(*args: Any, **kwargs: Any) -> None:
        seen.append(await made.store.list_calls(thread))
        await finish(*args, **kwargs)

    monkeypatch.setattr(made.store, "finish_send", looking)
    return seen


def at_each_reply(made: A2aApp, monkeypatch: pytest.MonkeyPatch, thread: str) -> list[Any]:
    """Collects the thread's rows as each reply of the model finds them, before it replaces
    its namespace's: after a send, what is found is what the tool left."""
    seen: list[Any] = []
    replace = made.store.replace_calls

    async def looking(*args: Any, **kwargs: Any) -> None:
        seen.append(await made.store.list_calls(thread))
        await replace(*args, **kwargs)

    monkeypatch.setattr(made.store, "replace_calls", looking)
    return seen


def restart(
    make_a2a_app: Callable[..., A2aApp],
    first: A2aApp,
    model: ScriptedChatModel,
    agents: dict[str, dict[str, Any]],
    **options: Any,
) -> A2aApp:
    """The service after a stop and a start with other agents, on the same database."""
    runtime = first.runtime
    return make_a2a_app(
        model,
        agents,
        instance_id="instance-b",
        saver=runtime.agent.checkpointer,
        store=first.store,
        locks=runtime.locks,
        saved_agents=first.saved_agents,
        at=first.at,
        **options,
    )


# The rows: written at the model's reply, gone once the send is finished.


def test_a_proposed_call_has_a_row_before_the_run_pauses_and_until_its_send_is_finished(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[propose(TO_HELPER)]), {}, saved=HELPER)
    record = made.saved["helper"]
    during = at_the_send(made, monkeypatch, "v-1")
    found = at_each_reply(made, monkeypatch, "v-1")
    with TestClient(made.app) as client:
        interrupted(run(client, "v-1", sse_events))
        (paused,) = rows(client, made, "v-1")
        events = resume(client, "v-1", [{"type": "approve"}], sse_events)
        afterwards = rows(client, made, "v-1")

    # The call as its tool will know it, and the saved agent it was proposed for.
    assert (paused.thread_id, paused.namespace, paused.tool_call_id) == ("v-1", "", "c1")
    assert (paused.agent, paused.agent_id, paused.gated) == ("helper", record.id, True)
    assert paused.proposed_at is not None
    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["Summarize the week."]
    # Still there while the message was on its way, and gone once the send was recorded:
    # the reply that followed the send found no row, before it replaced any.
    assert [keys(rows_then) for rows_then in during] == [[("", "c1")]]
    assert [keys(rows_then) for rows_then in found] == [[], []]
    assert afterwards == []


def test_a_call_that_needs_no_decision_has_its_row_while_it_is_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("Say hello.")]), {"hello": {"read_only": True}}
    )
    during = at_the_send(made, monkeypatch, "v-2")
    found = at_each_reply(made, monkeypatch, "v-2")
    with TestClient(made.app) as client:
        events = run(client, "v-2", sse_events)
        afterwards = rows(client, made, "v-2")
    assert events[-1] == ("end", {"status": "completed"})
    ((row,),) = during
    # An environment agent has no id, and its call needed no decision.
    assert (row.namespace, row.tool_call_id) == ("", "h1")
    assert (row.agent, row.agent_id, row.gated) == ("hello", None, False)
    # The tool took its row away: the next reply found none.
    assert found == [[], []]
    assert afterwards == []


def test_a_subagent_s_call_has_its_row_under_the_subagent_s_namespace(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(script=[DELEGATE, propose(TO_HELPER, step="d")])
    made = make_a2a_app(model, {}, saved=HELPER)
    during = at_the_send(made, monkeypatch, "v-3")
    found = at_each_reply(made, monkeypatch, "v-3")
    with TestClient(made.app) as client:
        interrupted(run(client, "v-3", sse_events))
        (paused,) = rows(client, made, "v-3")
        events = resume(client, "v-3", [{"type": "approve"}], sse_events)
        afterwards = rows(client, made, "v-3")

    # The namespace the subagent's model and its tools share: the delegating call's own.
    assert paused.namespace.startswith("tools:")
    assert "|" not in paused.namespace
    assert (paused.tool_call_id, paused.agent, paused.agent_id) == (
        "d1",
        "helper",
        made.saved["helper"].id,
    )
    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["Summarize the week."]
    assert [keys(rows_then) for rows_then in during] == [[(paused.namespace, "d1")]]
    # Every reply after the send, the subagent's and the orchestrator's, found no row.
    assert len(found) == 4 and found[2:] == [[], []]
    assert afterwards == []


def test_a_reply_replaces_the_rows_of_its_namespace_and_a_resume_clears_none(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            propose(TO_HELPER, ("handoff_to_helper", {"message": "And the month."})),
            propose(("handoff_to_helper", {"message": "Then only the week."}), step="e"),
            DONE,
        ]
    )
    made = make_a2a_app(model, {}, saved=HELPER)
    no = {"type": "reject", "message": "not these"}
    with TestClient(made.app) as client:
        interrupted(run(client, "v-4", sse_events))
        first = rows(client, made, "v-4")
        # Another namespace of the thread: no reply here, and no resume, touches it.
        seed(client, made, "v-4")
        interrupted(resume(client, "v-4", [no, no], sse_events))
        second = rows(client, made, "v-4")
        events = resume(client, "v-4", [no], sse_events)
        last = rows(client, made, "v-4")

    assert keys(first) == [("", "c1"), ("", "c2")]
    # The rejected calls never reached their tools; the next reply took their rows away.
    assert keys(second) == [("", "e1"), ("tools:stale", "stale-1")]
    # A reply that calls no agent leaves its namespace with no row.
    assert events[-1] == ("end", {"status": "completed"})
    assert keys(last) == [("tools:stale", "stale-1")]
    assert texts(made) == []


def test_a_run_that_starts_with_input_clears_its_thread_s_rows_and_no_other_s(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {}, saved=HELPER)
    with TestClient(made.app) as client:
        seed(client, made, "v-5")
        seed(client, made, "v-5", namespace="")
        seed(client, made, "v-5b")
        events = run(client, "v-5", sse_events)
        here, there = rows(client, made, "v-5"), rows(client, made, "v-5b")
    assert events[-1] == ("end", {"status": "completed"})
    assert here == []
    assert keys(there) == [("tools:stale", "stale-1")]


def test_a_background_run_clears_the_rows_and_its_retry_clears_none(
    make_a2a_app: Callable[..., A2aApp], monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    model = FlakyChatModel(final_text="Told.", fail_on={1})
    made = retry_app(make_a2a_app, model, stub())
    inputs: list[Any] = []
    run_stream = delegation_worker.run_stream

    def recording(*args: Any, **kwargs: Any) -> Any:
        inputs.append(kwargs["input"])
        return run_stream(*args, **kwargs)

    monkeypatch.setattr(delegation_worker, "run_stream", recording)
    with TestClient(made.app) as client:
        ready_outcome(client, made, "v-6", "Agent 'stub' completed task task-1:\n\nThe report.")
        seed(client, made, "v-6")
        # The first attempt starts with the outcome as its input, and its model fails.
        tick(client, made.runtime)
        after_the_first = rows(client, made, "v-6")
        assert follow_up(client, made)[0] == 1
        seed(client, made, "v-6")
        # The retry continues the step that is waiting: it has no input.
        clock.advance(RETRY)
        tick(client, made.runtime)
        after_the_retry = rows(client, made, "v-6")
        state = client.get("/threads/v-6/state").json()

    assert [given is None for given in inputs] == [False, True]
    assert after_the_first == []
    assert keys(after_the_retry) == [("tools:stale", "stale-1")]
    assert state["messages"][-1]["content"] == "Told."


def test_a_row_that_cannot_be_written_fails_the_step_and_nothing_is_sent(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Saved to run without approval: nothing but the row stands between the reply and the send.
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("Say hello.", agent="helper")]),
        {},
        saved={"helper": {"url": SAVED_URL, "read_only": True}},
    )

    async def unwritable(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database went away")

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.store, "replace_calls", unwritable)
        events = run(client, "v-7", sse_events)
        monkeypatch.undo()
        state = client.get("/threads/v-7/state").json()

    kind, data = events[-1]
    assert (kind, data["code"]) == ("error", "internal")
    # The reply that could not be recorded is not in the thread, and its call never ran.
    assert [m["type"] for m in state["messages"]] == ["human"]
    assert sent(made.transport_at(SAVED_URL)) == 0


def test_a_step_that_runs_again_after_its_send_was_recorded_returns_the_recorded_result(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("once", agent="helper", call_id="c1")]),
        {},
        saved={"helper": {"url": SAVED_URL, "read_only": True}},
    )
    delete = made.store.delete_call

    async def deleted_then_lost(*args: Any, **kwargs: Any) -> None:
        await delete(*args, **kwargs)
        raise RuntimeError("the service stopped before the step was saved")

    config = {"configurable": {"thread_id": "v-8"}}
    with TestClient(made.app) as client:
        monkeypatch.setattr(made.store, "delete_call", deleted_then_lost)
        assert run(client, "v-8", sse_events)[-1][0] == "error"
        monkeypatch.undo()
        # The send is recorded and its row is gone: the step finds its result, and is not
        # refused for a row it no longer has.
        assert rows(client, made, "v-8") == []
        messages = client.portal.call(made.runtime.agent.ainvoke, None, config)["messages"]

    assert texts(made) == ["once"]
    (result,) = [m for m in messages if isinstance(m, ToolMessage)]
    assert result.content.startswith("Agent 'helper' completed task ")


# The rule: a proposal stays with the agent it was made for.


@pytest.mark.parametrize(
    ("read_only", "decision"),
    [
        (False, {"type": "approve"}),
        (False, {"type": "edit", "args": {**PROPOSED, "message": "Changed before sending."}}),
        (False, {"type": "reject", "message": "no"}),
        # Saved to run without approval: the proposal can only be approved, and nothing but
        # this rule would keep the message from the agent that took the name.
        (True, {"type": "approve"}),
    ],
)
def test_a_proposal_for_an_environment_agent_is_not_sent_to_a_saved_agent_of_its_name(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    read_only: bool,
    decision: dict[str, Any],
) -> None:
    first = make_a2a_app(
        ScriptedChatModel(script=[propose(("handoff_to_stub", PROPOSED))]), {"stub": {}}
    )
    with TestClient(first.app) as client:
        proposal = interrupted(run(client, "w-1", sse_events))

    # The environment no longer names the agent, and an operator saves another as `stub`.
    second = restart(make_a2a_app, first, ScriptedChatModel(final_text="Understood."), {})
    caplog.set_level(logging.WARNING)
    with TestClient(second.app) as client:
        save(client, second, name="stub", read_only=read_only)
        waiting = pending(client, "w-1")
        events = resume(client, "w-1", [decision], sse_events)
        left = rows(client, second, "w-1")

    assert waiting["interrupt_id"] == proposal["interrupt_id"]
    # The proposal accepts what the tool under the name allows now.
    (action,) = waiting["action_requests"]
    assert action["allowed_decisions"] == (
        ["approve"] if read_only else ["approve", "edit", "reject"]
    )
    assert events[-1] == ("end", {"status": "completed"})
    # Whatever was decided, neither agent received anything.
    assert first.stubs["stub"].record.messages == []
    assert sent(second.transport_at(SAVED_URL)) == 0
    assert left == []
    kept_back = KEPT_BACK.format(agent="stub")
    results = tool_results(events, "handoff_to_stub")
    if decision["type"] == "reject":
        # The call never ran: nothing was kept back, and nothing is logged as kept back.
        assert results == []
        assert warnings(caplog, TOOLS) == []
    else:
        # An edit is told to the model before the result; the result itself is the rule's.
        (result,) = results
        assert result["content"].endswith(kept_back)
        assert ("Tool response:" in result["content"]) == (decision["type"] == "edit")
        # Logged with the agent's name and the rule (FR-030).
        assert warnings(caplog, TOOLS) == [f"Agent stub: {kept_back}"]


@pytest.mark.parametrize("read_only", [False, True])
def test_a_proposal_for_a_saved_agent_is_not_sent_to_an_environment_agent_of_its_name(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    read_only: bool,
) -> None:
    first = make_a2a_app(ScriptedChatModel(script=[propose(TO_HELPER)]), {}, saved=HELPER)
    with TestClient(first.app) as client:
        interrupted(run(client, "w-2", sse_events))

    # A restart whose environment names an agent `helper`: the saved one is no longer in use.
    second = restart(
        make_a2a_app,
        first,
        ScriptedChatModel(final_text="Understood."),
        {"helper": {"read_only": read_only}},
    )
    caplog.set_level(logging.WARNING)
    with TestClient(second.app) as client:
        events = resume(client, "w-2", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    (result,) = tool_results(events, "handoff_to_helper")
    assert result["content"] == KEPT_BACK.format(agent="helper")
    assert second.stubs["helper"].record.messages == []
    assert sent(second.transports["helper"]) == 0
    assert sent(first.transport_at(SAVED_URL)) == 0
    assert f"Agent helper: {KEPT_BACK.format(agent='helper')}" in warnings(caplog, TOOLS)


def test_a_proposal_for_a_saved_agent_is_not_sent_to_the_agent_saved_again_under_its_name(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[propose(TO_HELPER)], final_text="Understood."), {}, saved=HELPER
    )
    earlier = made.saved["helper"]
    with TestClient(made.app) as client:
        interrupted(run(client, "w-3", sse_events))
        # Removed and added again while the proposal waits: another saved agent (FR-012).
        client.portal.call(made.saved_agents.remove, earlier.id, earlier.version)
        later = save(client, made, name="helper")
        events = resume(client, "w-3", [{"type": "approve"}], sse_events)

    assert later.id != earlier.id
    assert events[-1] == ("end", {"status": "completed"})
    (result,) = tool_results(events, "handoff_to_helper")
    assert result["content"] == KEPT_BACK.format(agent="helper")
    assert sent(made.transport_at(SAVED_URL)) == 0


@pytest.mark.parametrize("with_its_row", [True, False])
def test_a_proposal_for_an_environment_agent_that_is_still_configured_follows_the_name(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, with_its_row: bool
) -> None:
    first = make_a2a_app(
        ScriptedChatModel(script=[propose(("handoff_to_stub", PROPOSED))]), {"stub": {}}
    )
    with TestClient(first.app) as client:
        interrupted(run(client, "w-4", sse_events))

    second = restart(make_a2a_app, first, ScriptedChatModel(), {"stub": {}}, stubs=first.stubs)
    with TestClient(second.app) as client:
        if not with_its_row:
            # As a call proposed before this feature was installed: it has no row.
            client.portal.call(second.store.clear_calls, "w-4")
        events = resume(client, "w-4", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    # Sent as proposed, once, as before this feature.
    assert [m.parts[0][1] for m in first.stubs["stub"].record.messages] == [PROPOSED["message"]]


@pytest.mark.parametrize("read_only", [False, True])
def test_a_call_with_no_row_is_not_sent_to_a_saved_agent(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    read_only: bool,
) -> None:
    first = make_a2a_app(
        ScriptedChatModel(script=[propose(("handoff_to_stub", PROPOSED))]), {"stub": {}}
    )
    with TestClient(first.app) as client:
        interrupted(run(client, "w-5", sse_events))
        # Proposed for an environment agent before this feature was installed: no row.
        client.portal.call(first.store.clear_calls, "w-5")

    second = restart(make_a2a_app, first, ScriptedChatModel(final_text="Understood."), {})
    caplog.set_level(logging.WARNING)
    with TestClient(second.app) as client:
        save(client, second, name="stub", read_only=read_only)
        events = resume(client, "w-5", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    kept_back = KEPT_BACK.format(agent="stub")
    (result,) = tool_results(events, "handoff_to_stub")
    assert result["content"] == kept_back
    assert first.stubs["stub"].record.messages == []
    assert sent(second.transport_at(SAVED_URL)) == 0
    assert warnings(caplog, TOOLS) == [f"Agent stub: {kept_back}"]


def test_a_cancel_proposed_for_one_agent_is_not_sent_to_another_of_its_name(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    cancel = AIMessage(
        content="",
        tool_calls=[{"id": "x1", "name": "cancel_stub_task", "args": {"task_id": "t-1"}}],
    )
    first = make_a2a_app(ScriptedChatModel(script=[cancel]), {"stub": {}})
    with TestClient(first.app) as client:
        interrupted(run(client, "w-6", sse_events))
        (row,) = rows(client, first, "w-6")

    second = restart(make_a2a_app, first, ScriptedChatModel(final_text="Understood."), {})
    with TestClient(second.app) as client:
        saved = save(client, second, name="stub")
        # A task of the saved agent on this thread, which the cancel would otherwise reach.
        now = client.portal.call(second.store.now)
        client.portal.call(second.store.record_task, _task("w-6", saved.ref, now))
        events = resume(client, "w-6", [{"type": "approve"}], sse_events)

    assert (row.agent, row.agent_id, row.tool_call_id) == ("stub", None, "x1")
    assert events[-1] == ("end", {"status": "completed"})
    (result,) = tool_results(events, "cancel_stub_task")
    assert result["content"] == KEPT_BACK.format(agent="stub")
    assert second.stub_at(SAVED_URL).record.cancels == []
    assert sent(second.transport_at(SAVED_URL)) == 0


def _task(thread: str, ref: str, now: Any) -> AgentTaskRecord:
    return AgentTaskRecord(
        agent="stub",
        task_id="t-1",
        thread_id=thread,
        context_id="ctx",
        message="Compile the report.",
        state="working",
        started_at=now,
        last_checked_at=now,
        agent_ref=ref,
    )


def test_a_cancel_that_was_sent_takes_its_row_away(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel = AIMessage(
        content="",
        tool_calls=[{"id": "x1", "name": "cancel_stub_task", "args": {"task_id": "t-1"}}],
    )
    made = make_a2a_app(ScriptedChatModel(script=[cancel]), {"stub": {"read_only": True}})
    during = at_the_send(made, monkeypatch, "v-9")
    found = at_each_reply(made, monkeypatch, "v-9")
    with TestClient(made.app) as client:
        now = client.portal.call(made.store.now)
        task = dataclasses.replace(_task("v-9", "", now), agent_ref=None)
        client.portal.call(made.store.record_task, task)
        events = run(client, "v-9", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    # The cancel reached the agent, which does not know the made-up task, with its row in
    # place; the row was gone afterwards.
    (result,) = tool_results(events, "cancel_stub_task")
    assert "no longer knows task t-1" in result["content"]
    assert sent(made.transports["stub"]) == 1
    assert [keys(rows_then) for rows_then in during] == [[("", "x1")]]
    assert found == [[], []]


@pytest.mark.parametrize("saved", [True, False])
def test_a_row_that_cannot_be_read_fails_the_step_and_nothing_is_sent(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    saved: bool,
) -> None:
    # Neither kind of agent is sent a message whose row could not be looked at.
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("Say hello.", agent="helper")]),
        {} if saved else {"helper": {"url": SAVED_URL, "read_only": True}},
        saved={"helper": {"url": SAVED_URL, "read_only": True}} if saved else None,
    )

    async def unreadable(*_args: Any) -> None:
        raise RuntimeError("the database went away")

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.store, "get_call", unreadable)
        events = run(client, "v-10", sse_events)
        transport = made.transport_at(SAVED_URL) if saved else made.transports["helper"]
        requests = sent(transport)
    kind, data = events[-1]
    assert (kind, data["code"]) == ("error", "internal")
    assert requests == 0


def test_rows_that_cannot_be_cleared_end_the_run_before_the_model_is_asked(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(script=[handoff("Say hello.", agent="helper")])
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})

    async def unclearable(_thread: str) -> None:
        raise RuntimeError("the database went away")

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.store, "clear_calls", unclearable)
        events = run(client, "v-11", sse_events)
        monkeypatch.undo()
        busy = client.portal.call(made.runtime.locks.is_active, "v-11")
    assert [kind for kind, _data in events] == ["start", "error"]
    assert events[-1][1]["code"] == "internal"
    assert model.calls == 0
    # The thread's lease was given back.
    assert busy is False
    assert sent(made.transport_at(SAVED_URL)) == 0


def test_one_tool_call_id_used_twice_for_two_environment_agents_reaches_both_as_before(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    # Nothing makes a provider keep the id unique within a reply. The two calls have one
    # row; an environment agent's tool follows the name, as it did before the rows existed.
    step = AIMessage(
        content="",
        tool_calls=[
            {"id": "same", "name": "handoff_to_alpha", "args": {"message": "one"}},
            {"id": "same", "name": "handoff_to_beta", "args": {"message": "two"}},
        ],
    )
    made = make_a2a_app(
        ScriptedChatModel(script=[step]),
        {"alpha": {"read_only": True}, "beta": {"read_only": True}},
    )
    with TestClient(made.app) as client:
        events = run(client, "v-12", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    assert [m.text for m in made.stubs["alpha"].record.messages] == ["one"]
    assert [m.text for m in made.stubs["beta"].record.messages] == ["two"]


def test_one_tool_call_id_used_twice_for_two_saved_agents_reaches_only_the_one_its_row_names(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    step = AIMessage(
        content="",
        tool_calls=[
            {"id": "same", "name": "handoff_to_helper", "args": {"message": "one"}},
            {"id": "same", "name": "handoff_to_other", "args": {"message": "two"}},
        ],
    )
    other_url = "http://127.0.0.1:9101"
    made = make_a2a_app(
        ScriptedChatModel(script=[step]),
        {},
        saved={
            "helper": {"url": SAVED_URL, "read_only": True},
            "other": {"url": other_url, "read_only": True},
        },
    )
    with TestClient(made.app) as client:
        events = run(client, "v-13", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    # The one row names the later call's agent. The other call is kept back: it fails toward
    # sending nothing, and says so.
    (kept_back,) = tool_results(events, "handoff_to_helper")
    assert kept_back["content"] == KEPT_BACK.format(agent="helper")
    assert sent(made.transport_at(SAVED_URL)) == 0
    assert [m.text for m in made.stub_at(other_url).record.messages] == ["two"]


# Part 2: the approval choice decides, and a tightened choice or a removal holds at once.

STREAMING = "freyja_agent.api.streaming"
NOW_REQUIRES = "Agent '{agent}' now requires approval, so this message was not sent."
REMOVED = "Agent '{agent}' was removed; nothing was sent."
# The message of contracts/sse-events.md for a resume that a tightened choice blocks.
BLOCKED = (
    "This step cannot continue: it holds a message to agent '{agent}' that needed no approval "
    "when it was proposed, and the agent now requires approval. Switch the agent back, or "
    "remove it, then decide the proposal again."
)
WHERE = ["orchestrator", "subagent", "background"]
KINDS = ["handoff", "follow_up", "answer", "cancel"]
# Where a message that needs no decision is seen to leave: a background run starts at a
# round of the worker, which also checks the agent's pending tasks, so an answer and a
# cancel, which need such a task, are followed on the orchestrator and on its subagent.
SENDS = [(kind, where) for where in WHERE[:2] for kind in KINDS] + [
    (kind, "background") for kind in KINDS[:2]
]
THIRD_URL = "http://127.0.0.1:9103"


def a_call(kind: str) -> tuple[str, dict[str, Any]]:
    if kind == "cancel":
        return "cancel_helper_task", {"task_id": "t-1"}
    args: dict[str, Any] = {"message": "Summarize the week."}
    if kind == "answer":
        args["task_id"] = "t-1"
    return "handoff_to_helper", args


def one_helper(
    make_a2a_app: Callable[..., A2aApp],
    kind: str,
    where: str,
    thread: str,
    *,
    read_only: bool,
    model: type[ScriptedChatModel] = ScriptedChatModel,
) -> tuple[A2aApp, ScriptedChatModel]:
    """An app with the saved agent ``helper``, and a model that proposes one message to it:
    on the orchestrator, on its subagent, or in the background run a round of the worker
    starts on ``thread``."""
    call = a_call(kind)
    script = {
        "orchestrator": [propose(call)],
        "subagent": [DELEGATE, propose(call, step="d")],
        "background": [propose(call, step="b")],
    }[where]
    scripted = model(script=script)
    background = where == "background"
    made = make_a2a_app(
        scripted,
        {"hello": {"read_only": True}} if background else {},
        saved={"helper": {"url": SAVED_URL, "read_only": read_only}},
    )
    record = made.saved["helper"]
    if background:
        # An outcome for the worker to deliver: a task the stand-in will not know.
        seed_task(made, "hello", "task-1", thread=thread)
    elif kind in ("answer", "cancel"):
        state = "input_required" if kind == "answer" else "working"
        seed_task(made, "helper", "t-1", ref=record.ref, state=state, thread=thread)
    if kind == "follow_up":
        # The thread already has a conversation with the agent.
        asyncio.run(made.store.set_context(thread, "helper", "ctx-1"))
    return made, scripted


def started(
    client: TestClient, made: A2aApp, where: str, thread: str, sse_events: Any
) -> dict[str, Any] | None:
    """Start the run in which the message is proposed; the proposal it paused on, or None."""
    if where == "background":
        tick(client, made.runtime)
    else:
        events = run(client, thread, sse_events)
        assert events[-1][0] in ("interrupt", "end"), events[-1]
    return client.get(f"/threads/{thread}/state").json()["pending"]


def results(model: ScriptedChatModel, tool: str) -> list[ToolMessage]:
    """Every result of ``tool`` the model was shown, on the orchestrator or its subagent."""
    found: dict[str, ToolMessage] = {}
    for seen in model.seen:
        for message in seen:
            if isinstance(message, ToolMessage) and message.name == tool:
                found[message.tool_call_id] = message
    return list(found.values())


def before_the_send(made: A2aApp, monkeypatch: pytest.MonkeyPatch, change: Any) -> None:
    """Have ``change`` happen once, while a tool is on its way to sending: after its run
    started, and before the tool looks at the saved agent."""
    get_call = made.store.get_call
    done: list[bool] = []

    async def changing(*args: Any) -> Any:
        if not done:
            done.append(True)
            await change()
        return await get_call(*args)

    monkeypatch.setattr(made.store, "get_call", changing)


def kept_back_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [line for line in warnings(caplog, TOOLS) if line.startswith("Agent helper: ")]


@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize("kind", KINDS)
def test_every_message_to_a_saved_agent_that_requires_approval_waits_for_a_person(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, kind: str, where: str
) -> None:
    thread = f"p-1-{kind}-{where}"
    made, _model = one_helper(make_a2a_app, kind, where, thread, read_only=False)
    with TestClient(made.app) as client:
        proposal = started(client, made, where, thread, sse_events)
    assert proposal is not None
    (action,) = proposal["action_requests"]
    assert (action["name"], action["agent"]) == (a_call(kind)[0], "helper")
    # Nothing left before the decision.
    assert sent(made.transport_at(SAVED_URL)) == 0


@pytest.mark.parametrize(("kind", "where"), SENDS)
def test_after_a_switch_to_running_without_approval_the_next_run_sends_with_no_pause(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, kind: str, where: str
) -> None:
    thread = f"p-2-{kind}-{where}"
    made, _model = one_helper(make_a2a_app, kind, where, thread, read_only=False)
    with TestClient(made.app) as client:
        choose(client, made, made.saved["helper"], read_only=True)
        proposal = started(client, made, where, thread, sse_events)
    assert proposal is None
    assert sent(made.transport_at(SAVED_URL)) == 1


def test_a_run_in_progress_keeps_pausing_after_a_switch_to_running_without_approval(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = LabelledChatModel(script=[propose(TO_HELPER)])
    made = make_a2a_app(model, {}, saved=HELPER)
    streamed: dict[str, Events] = {}
    with TestClient(made.app) as client:
        model.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "p-3", sse_events))
        )
        running.start()
        with model.held():
            # The run has reached its model and waits there while the choice is changed.
            choose(client, made, made.saved["helper"], read_only=True)
        running.join(10)
        assert not running.is_alive()
        before_the_decision = sent(made.transport_at(SAVED_URL))
        # The run that was in progress still has its gate. Its continuation is a run of its
        # own, with the agent as it is now: the proposal can only be approved.
        proposal = interrupted(streamed["events"])
        errors = refuse(client, "p-3", [{"type": "reject", "message": "no"}])
        events = resume(client, "p-3", [{"type": "approve"}], sse_events)

    assert [action["name"] for action in proposal["action_requests"]] == ["handoff_to_helper"]
    assert before_the_decision == 0
    assert "no longer needs a decision" in errors[0]["msg"]
    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["Summarize the week."]


def test_after_a_switch_to_requiring_approval_the_next_run_pauses_on_every_instance(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    saved = {"helper": {"url": SAVED_URL, "read_only": True}}
    first = make_a2a_app(
        ScriptedChatModel(script=[handoff("Before.", agent="helper"), DONE, propose(TO_HELPER)]),
        {},
        saved=saved,
    )
    second = make_a2a_app(
        ScriptedChatModel(script=[propose(TO_HELPER)]),
        {},
        instance_id="instance-b",
        store=first.store,
        saved_agents=first.saved_agents,
        at=first.at,
    )
    with TestClient(first.app) as one, TestClient(second.app) as two:
        before = run(one, "p-4", sse_events)
        # Changed in the store, as a third instance's route would: neither has seen it.
        choose(one, first, first.saved["helper"], read_only=False)
        here = run(one, "p-4b", sse_events)
        there = run(two, "p-4c", sse_events)

    assert before[-1] == ("end", {"status": "completed"})
    for events in (here, there):
        (action,) = interrupted(events)["action_requests"]
        assert (action["name"], action["agent"]) == ("handoff_to_helper", "helper")
    assert texts(first) == ["Before."]


@pytest.mark.parametrize(("kind", "where"), SENDS)
def test_a_run_in_progress_sends_nothing_to_an_agent_that_now_requires_approval(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kind: str,
    where: str,
) -> None:
    thread = f"p-5-{kind}-{where}"
    made, model = one_helper(make_a2a_app, kind, where, thread, read_only=True)
    record = made.saved["helper"]

    async def tighten() -> None:
        await made.saved_agents.change(record.id, record.version, read_only=False)

    # The run starts while the agent runs without approval, so its tool waits for no one.
    before_the_send(made, monkeypatch, tighten)
    caplog.set_level(logging.WARNING, logger=TOOLS)
    with TestClient(made.app) as client:
        proposal = started(client, made, where, thread, sse_events)
        state = client.get(f"/threads/{thread}/state").json()

    sentence = NOW_REQUIRES.format(agent="helper")
    # Nothing was sent, the call says why, and the run went on to its end.
    assert sent(made.transport_at(SAVED_URL)) == 0
    (result,) = results(model, a_call(kind)[0])
    assert (result.status, result.content) == ("error", sentence)
    assert proposal is None and state["run_active"] is False
    assert state["messages"][-1]["type"] == "ai"
    assert kept_back_lines(caplog) == [f"Agent helper: {sentence}"]


def test_a_send_that_began_before_the_change_is_not_recalled_and_none_follows_it(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(
        script=[
            handoff("Began before the change.", agent="helper"),
            handoff("Proposed after it.", agent="helper", call_id="h2"),
            DONE,
        ]
    )
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})
    record = made.saved["helper"]
    start_send = made.store.start_send
    begun: list[int] = []

    async def then_tightened(send: Any) -> None:
        await start_send(send)
        # The first write of the first send is made: the change is answered right after it.
        if not begun:
            begun.append(sent(made.transport_at(SAVED_URL)))
            await made.saved_agents.change(record.id, record.version, read_only=False)

    monkeypatch.setattr(made.store, "start_send", then_tightened)
    with TestClient(made.app) as client:
        events = run(client, "p-6", sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    first, second = tool_results(events, "handoff_to_helper")
    # When the change was answered nothing had left yet; what had begun was sent all the same.
    assert begun == [0]
    assert first["content"].startswith("Agent 'helper' completed task ")
    assert second["content"] == NOW_REQUIRES.format(agent="helper")
    # By the stand-in's own record, nothing reached it after the change.
    assert texts(made) == ["Began before the change."]
    assert sent(made.transport_at(SAVED_URL)) == 1


@pytest.mark.parametrize("saved_again", [False, True])
@pytest.mark.parametrize("read_only", [True, False])
def test_a_run_in_progress_sends_nothing_to_an_agent_that_was_removed(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    read_only: bool,
    saved_again: bool,
) -> None:
    model = ScriptedChatModel(script=[propose(TO_HELPER)])
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": read_only}})
    record = made.saved["helper"]
    again = make_stub("helper")

    async def remove() -> None:
        await made.saved_agents.remove(record.id, record.version)
        if saved_again:
            # Another agent under the same name: another id.
            made.add_stub(OTHER_URL, again)
            await made.saved_agents.add(name="helper", url=OTHER_URL, card=saved_card(again))

    caplog.set_level(logging.WARNING, logger=TOOLS)
    with TestClient(made.app) as client:
        if read_only:
            before_the_send(made, monkeypatch, remove)
            events = run(client, "p-7", sse_events)
        else:
            # A person approves it, and the continuation has started when the agent is
            # removed: it still holds the removed agent's tool.
            interrupted(run(client, "p-7", sse_events))
            before_the_send(made, monkeypatch, remove)
            events = resume(client, "p-7", [{"type": "approve"}], sse_events)

    sentence = REMOVED.format(agent="helper")
    assert events[-1] == ("end", {"status": "completed"})
    (result,) = results(model, "handoff_to_helper")
    assert (result.status, result.content) == ("error", sentence)
    assert sent(made.transport_at(SAVED_URL)) == 0
    assert again.record.messages == []
    assert kept_back_lines(caplog) == [f"Agent helper: {sentence}"]


@pytest.mark.parametrize("change", ["requires approval", "removed"])
def test_a_step_that_runs_again_after_its_send_was_recorded_is_not_refused_afterwards(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("once", agent="helper", call_id="c1")]),
        {},
        saved={"helper": {"url": SAVED_URL, "read_only": True}},
    )
    record = made.saved["helper"]
    delete = made.store.delete_call

    async def deleted_then_lost(*args: Any, **kwargs: Any) -> None:
        await delete(*args, **kwargs)
        raise RuntimeError("the service stopped before the step was saved")

    config = {"configurable": {"thread_id": "p-8"}}
    with TestClient(made.app) as client:
        monkeypatch.setattr(made.store, "delete_call", deleted_then_lost)
        assert run(client, "p-8", sse_events)[-1][0] == "error"
        monkeypatch.undo()
        if change == "removed":
            client.portal.call(made.saved_agents.remove, record.id, record.version)
        else:
            choose(client, made, record, read_only=False)
        # The message left before the change. The step that runs again finds its result in
        # the send ledger, which is read first, and is not told afterwards that it may not send.
        messages = client.portal.call(made.runtime.agent.ainvoke, None, config)["messages"]

    assert texts(made) == ["once"]
    (result,) = [m for m in messages if isinstance(m, ToolMessage)]
    assert result.content.startswith("Agent 'helper' completed task ")


def reads_of_the_saved_agent(made: A2aApp, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    get_agent = made.saved_agents.get_agent
    read: list[str] = []

    async def counting(agent_id: str) -> Any:
        read.append(agent_id)
        return await get_agent(agent_id)

    monkeypatch.setattr(made.saved_agents, "get_agent", counting)
    return read


def test_an_environment_agent_s_tools_read_no_saved_agent_before_they_send(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("Say hello.")]),
        {"hello": {"read_only": True}},
        saved=HELPER,
    )
    read = reads_of_the_saved_agent(made, monkeypatch)
    with TestClient(made.app) as client:
        events = run(client, "p-9", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    assert [m.text for m in made.stubs["hello"].record.messages] == ["Say hello."]
    assert read == []


def test_a_saved_agent_s_tool_reads_its_agent_once_before_each_send(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(
        script=[handoff("one", agent="helper"), handoff("two", agent="helper", call_id="h2")]
    )
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})
    read = reads_of_the_saved_agent(made, monkeypatch)
    with TestClient(made.app) as client:
        events = run(client, "p-10", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["one", "two"]
    assert read == [made.saved["helper"].id] * 2


def test_a_saved_agent_that_cannot_be_read_fails_the_step_and_nothing_is_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("Say hello.", agent="helper")]),
        {},
        saved={"helper": {"url": SAVED_URL, "read_only": True}},
    )

    async def unreadable(_agent_id: str) -> None:
        raise RuntimeError("the database went away")

    with TestClient(made.app) as client:
        monkeypatch.setattr(made.saved_agents, "get_agent", unreadable)
        events = run(client, "p-11", sse_events)
    kind, data = events[-1]
    assert (kind, data["code"]) == ("error", "internal")
    assert sent(made.transport_at(SAVED_URL)) == 0


def test_a_paused_proposal_decided_after_a_switch_to_running_without_approval_is_sent_as_proposed(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[propose(TO_HELPER)]), {}, saved=HELPER)
    with TestClient(made.app) as client:
        proposal = interrupted(run(client, "p-12", sse_events))
        choose(client, made, made.saved["helper"], read_only=True)
        now = pending(client, "p-12")
        edit = {"type": "edit", "args": {"message": "Something else."}}
        refused = [
            refuse(client, "p-12", [decision])[0]["msg"]
            for decision in (edit, {"type": "reject", "message": "no"})
        ]
        before_the_decision = sent(made.transport_at(SAVED_URL))
        events = resume(client, "p-12", [{"type": "approve"}], sse_events)

    assert now["interrupt_id"] == proposal["interrupt_id"]
    assert now["action_requests"][0]["allowed_decisions"] == ["approve"]
    assert all("no longer needs a decision" in message for message in refused)
    assert before_the_decision == 0
    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["Summarize the week."]


TO_OTHER = ("handoff_to_other", {"message": "To the one that needed no approval."})
TWO_AGENTS = {
    "helper": {"url": SAVED_URL},
    "other": {"url": OTHER_URL, "read_only": True},
}


@pytest.mark.parametrize("where", WHERE[:2])
def test_a_paused_step_whose_call_came_to_require_approval_cannot_be_continued(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    where: str,
) -> None:
    step = propose(TO_HELPER, TO_OTHER, step="d" if where == "subagent" else "c")
    script = [DELEGATE, step] if where == "subagent" else [step]
    made = make_a2a_app(ScriptedChatModel(script=script), {}, saved=TWO_AGENTS)
    caplog.set_level(logging.WARNING, logger=STREAMING)
    with TestClient(made.app) as client:
        proposal = interrupted(run(client, "p-13", sse_events))
        # The step waits for a decision about `helper`. Its call to `other` needed none.
        tightened = choose(client, made, made.saved["other"], read_only=False)
        blocked = resume(client, "p-13", [{"type": "approve"}], sse_events)
        still = pending(client, "p-13")
        left = (sent(made.transport_at(SAVED_URL)), sent(made.transport_at(OTHER_URL)))
        refusals = warnings(caplog, STREAMING)
        # Once the agent is switched back, the same decision goes through.
        choose(client, made, tightened, read_only=True)
        events = resume(client, "p-13", [{"type": "approve"}], sse_events)

    assert [action["name"] for action in proposal["action_requests"]] == ["handoff_to_helper"]
    assert blocked[-1] == (
        "error",
        {"code": "approval_now_required", "message": BLOCKED.format(agent="other")},
    )
    # Nothing from the step ran, and the proposal is as it was.
    assert left == (0, 0)
    assert still["interrupt_id"] == proposal["interrupt_id"]
    # The refusal is in the log with its thread and the agent.
    (line,) = refusals
    assert "thread p-13" in line and "refused" in line and line.endswith("agents: other")
    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["Summarize the week."]
    assert texts(made, OTHER_URL) == ["To the one that needed no approval."]


def test_a_blocked_resume_names_every_agent_that_came_to_require_approval(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    third = ("handoff_to_third", {"message": "To another that needed no approval."})
    saved = {**TWO_AGENTS, "third": {"url": THIRD_URL, "read_only": True}}
    made = make_a2a_app(
        ScriptedChatModel(script=[propose(TO_HELPER, TO_OTHER, third)]), {}, saved=saved
    )
    with TestClient(made.app) as client:
        interrupted(run(client, "p-14", sse_events))
        for name in ("third", "other"):
            choose(client, made, made.saved[name], read_only=False)
        blocked = resume(client, "p-14", [{"type": "approve"}], sse_events)

    kind, data = blocked[-1]
    assert (kind, data["code"]) == ("error", "approval_now_required")
    assert data["message"] == (
        "This step cannot continue: it holds messages to agents 'other' and 'third' that "
        "needed no approval when they were proposed, and the agents now require approval. "
        "Switch the agents back, or remove them, then decide the proposal again."
    )
    assert [sent(made.transport_at(url)) for url in (SAVED_URL, OTHER_URL, THIRD_URL)] == [0, 0, 0]


def test_a_blocked_resume_whose_calls_cannot_be_read_ends_as_it_did_before(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[propose(TO_HELPER, TO_OTHER)]), {}, saved=TWO_AGENTS
    )

    async def unreadable(_thread: str) -> None:
        raise RuntimeError("the database went away")

    with TestClient(made.app) as client:
        proposal = interrupted(run(client, "p-15", sse_events))
        choose(client, made, made.saved["other"], read_only=False)
        monkeypatch.setattr(made.store, "list_calls", unreadable)
        refused = client.post(
            "/threads/p-15/resume",
            json={"interrupt_id": proposal["interrupt_id"], "decisions": [{"type": "approve"}]},
        )
        monkeypatch.undo()
        still = pending(client, "p-15")

    # Since Phase 11 the resume reads the calls before anything runs, to know which of them
    # the person decided, and is refused when it cannot: nothing runs, and the proposal is
    # still there to be decided.
    assert refused.status_code == 500
    assert refused.json()["detail"]["code"] == "internal"
    assert still["interrupt_id"] == proposal["interrupt_id"]
    assert sent(made.transport_at(OTHER_URL)) == 0


def test_the_code_of_a_blocked_resume_is_not_among_those_a_background_run_is_retried_for() -> None:
    assert "approval_now_required" not in delegation_worker.RETRIED
    assert "internal" in delegation_worker.RETRIED


def test_a_card_that_calls_its_agent_read_only_changes_nothing(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    card = stub_card("helper", description="A read-only agent: it only reads, never writes.")
    made = make_a2a_app(
        ScriptedChatModel(script=[propose(TO_HELPER)]),
        {},
        saved={"helper": {"url": SAVED_URL, "stub": make_stub("helper", card=card)}},
    )
    with TestClient(made.app) as client:
        catalog = tools(client)
        proposal = interrupted(run(client, "p-16", sse_events))

    assert "read-only" in catalog["handoff_to_helper"]["description"]
    assert catalog["handoff_to_helper"]["classification"] == "gated"
    assert [action["name"] for action in proposal["action_requests"]] == ["handoff_to_helper"]
    assert sent(made.transport_at(SAVED_URL)) == 0


def test_a_saved_agent_s_tools_are_not_built_without_the_store_they_read_it_from() -> None:
    from freyja_agent.config import A2aAgentConfig
    from freyja_agent.delegates.a2a_agents import AgentLink, default_transport
    from freyja_agent.delegates.a2a_tools import agent_tools
    from freyja_agent.runtime.agent_tasks import InMemoryAgentTaskStore
    from freyja_agent.runtime.saved_agents import InMemorySavedAgentStore

    def link(source: str) -> AgentLink:
        return AgentLink(
            "helper",
            A2aAgentConfig(url=SAVED_URL, read_only=False),
            transport=default_transport,
            call_timeout=5,
            source=source,
            agent_id="a" * 32 if source == "saved" else None,
        )

    tasks = InMemoryAgentTaskStore()
    # A saved agent's tools check the agent before every send: they cannot be made unable to.
    with pytest.raises(ValueError, match="saved-agent store"):
        agent_tools(link("saved"), tasks)
    assert len(agent_tools(link("saved"), tasks, saved=InMemorySavedAgentStore(tasks))) == 2
    # An environment agent's need none, as before.
    assert len(agent_tools(link("environment"), tasks)) == 2


# A step that runs again before its send: found by the independent review of Phase 7. A
# background run that fails is tried again from its last checkpoint, which lies after the
# approval gate, on an orchestrator built for the agents as they are by then.


def failing_once(target: Any, name: str, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Make ``target.name`` fail the first time it is called, as a database does for a moment."""
    original = getattr(target, name)
    failed: list[bool] = []

    async def blip(*args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(True)
            raise RuntimeError("the database went away for a moment")
        return await original(*args, **kwargs)

    monkeypatch.setattr(target, name, blip)
    return failed


@pytest.mark.parametrize("failing", ["get_context", "get_agent", "start_send"])
def test_a_retry_sends_nothing_no_person_decided_to_an_agent_that_came_to_require_approval(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failing: str,
) -> None:
    thread = f"p-17-{failing}"
    made, model = one_helper(make_a2a_app, "handoff", "background", thread, read_only=True)
    record = made.saved["helper"]
    target = made.saved_agents if failing == "get_agent" else made.store
    caplog.set_level(logging.WARNING, logger=TOOLS)
    with TestClient(made.app) as client:
        failed = failing_once(target, failing, monkeypatch)
        # The first attempt: the hand-off needs no decision, passes the gate, and its step
        # fails before anything is sent.
        tick(client, made.runtime)
        (row,) = rows(client, made, thread)
        before = sent(made.transport_at(SAVED_URL))
        # The agent now requires approval, and the change is answered.
        choose(client, made, record, read_only=False)
        catalog = tools(client)
        # The retry continues the step, past the gate, with the tools of the agents as
        # they are now.
        clock.advance(RETRY)
        tick(client, made.runtime)
        state = client.get(f"/threads/{thread}/state").json()

    assert failed == [True] and before == 0
    assert (row.agent, row.gated) == ("helper", False)
    assert catalog["handoff_to_helper"]["classification"] == "gated"
    # No person decided this message, and the agent requires approval: nothing was sent.
    assert texts(made) == []
    assert sent(made.transport_at(SAVED_URL)) == 0
    sentence = NOW_REQUIRES.format(agent="helper")
    (result,) = results(model, "handoff_to_helper")
    assert (result.status, result.content) == ("error", sentence)
    assert state["pending"] is None and state["messages"][-1]["type"] == "ai"
    assert kept_back_lines(caplog) == [f"Agent helper: {sentence}"]


def test_a_retry_sends_what_no_person_decided_to_an_agent_that_still_runs_without_approval(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made, _model = one_helper(make_a2a_app, "handoff", "background", "p-18", read_only=True)
    with TestClient(made.app) as client:
        failed = failing_once(made.store, "get_context", monkeypatch)
        tick(client, made.runtime)
        before = sent(made.transport_at(SAVED_URL))
        clock.advance(RETRY)
        tick(client, made.runtime)
    assert failed == [True] and before == 0
    # Nothing changed in between: the retry sends the message, once.
    assert texts(made) == ["Summarize the week."]


def test_a_retry_after_a_restart_sends_nothing_to_an_environment_agent_that_lost_its_mark(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    step = propose(("handoff_to_stub", {"message": "Summarize the week."}), step="b")
    model = ScriptedChatModel(script=[step])
    first = make_a2a_app(model, {"hello": {"read_only": True}, "stub": {"read_only": True}})
    seed_task(first, "hello", "task-1", thread="p-19")
    with TestClient(first.app) as client:
        failed = failing_once(first.store, "get_context", monkeypatch)
        tick(client, first.runtime)
        (row,) = rows(client, first, "p-19")

    # The service starts again, and `stub` no longer runs without approval.
    second = restart(
        make_a2a_app,
        first,
        model,
        {"hello": {"read_only": True}, "stub": {}},
        stubs=first.stubs,
    )
    caplog.set_level(logging.WARNING, logger=TOOLS)
    with TestClient(second.app) as client:
        clock.advance(RETRY)
        tick(client, second.runtime)
        state = client.get("/threads/p-19/state").json()

    assert failed == [True]
    assert (row.agent, row.agent_id, row.gated) == ("stub", None, False)
    # The step passed the gate before the restart. No person decided its message.
    assert first.stubs["stub"].record.messages == []
    sentence = NOW_REQUIRES.format(agent="stub")
    (result,) = results(model, "handoff_to_stub")
    assert (result.status, result.content) == ("error", sentence)
    assert state["pending"] is None
    assert f"Agent stub: {sentence}" in warnings(caplog, TOOLS)


def test_a_call_a_person_approved_is_sent_when_its_agent_requires_approval_again_before_it(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changed in Phase 11. A person approved the proposal while the agent ran without
    approval, which allowed approve only; the agent then requires approval again before
    the tool sends. A person decided this message, so it is sent (FR-022, FR-023). Until
    Phase 11 the decision was inferred, and this message was refused."""
    model = ScriptedChatModel(script=[propose(TO_HELPER)])
    made = make_a2a_app(model, {}, saved=HELPER)
    with TestClient(made.app) as client:
        interrupted(run(client, "p-20", sse_events))
        loosened = choose(client, made, made.saved["helper"], read_only=True)

        async def tighten() -> None:
            await made.saved_agents.change(loosened.id, loosened.version, read_only=False)

        # The continuation is built while the agent runs without approval, so its tool
        # waits for no one, and the agent requires approval again before the tool sends.
        before_the_send(made, monkeypatch, tighten)
        events = resume(client, "p-20", [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["Summarize the week."]


def test_a_call_nobody_was_shown_is_not_sent_when_its_agent_comes_to_require_approval(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One reply proposes a notification, which pauses, and a message to `helper`, which
    runs without approval and is not shown. A person approves the notification; `helper`
    requires approval before its tool sends. Nobody decided the message to `helper`
    (found by the review of Phase 11: no test held the hook to what the person was shown)."""
    notify = ("send_notification", {"recipient": "ops", "message": "hi"})
    model = ScriptedChatModel(script=[propose(notify, TO_HELPER)])
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})
    with TestClient(made.app) as client:
        interrupted(run(client, "p-28", sse_events))
        shown = [action["name"] for action in pending(client, "p-28")["action_requests"]]
        record = made.saved["helper"]

        async def tighten() -> None:
            await made.saved_agents.change(record.id, record.version, read_only=False)

        before_the_send(made, monkeypatch, tighten)
        events = resume(client, "p-28", [{"type": "approve"}], sse_events)

    assert shown == ["send_notification"]
    assert events[-1] == ("end", {"status": "completed"})
    (result,) = results(model, "handoff_to_helper")
    assert result.content == NOW_REQUIRES.format(agent="helper")
    assert sent(made.transport_at(SAVED_URL)) == 0


# Phase 11 (convergence): a decision is recorded when the gate receives it, never inferred.


def test_a_retry_sends_nothing_when_the_step_that_would_have_asked_never_paused(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gate's step never paused, the agent's choice flipped twice, and no one decided.

    1. The agent requires approval. A background run's model proposes a hand-off, so the
       call's row says it needed a decision; the checkpoint after the model's step cannot
       be written, so the gate never asks.
    2. The agent runs without approval. The retry continues from the model's step: the gate
       lets the call through unasked, and the tool's step fails before anything is sent.
    3. The agent requires approval again. The retry continues past the gate, with a tool
       that waits for a person. The row says the call needed a decision, and nobody made one.
    """
    thread = "p-21"
    made, model = one_helper(make_a2a_app, "handoff", "background", thread, read_only=False)
    blipped: list[bool] = []
    caplog.set_level(logging.WARNING, logger=TOOLS)
    with TestClient(made.app) as client:
        saver = made.runtime.agent.checkpointer
        write = saver.aput

        async def aput(*args: Any) -> Any:
            # The first checkpoint written once the model proposed the call.
            if not blipped and await made.store.list_calls(thread):
                blipped.append(True)
                raise RuntimeError("the database went away for a moment")
            return await write(*args)

        monkeypatch.setattr(saver, "aput", aput)
        tick(client, made.runtime)
        paused = client.get(f"/threads/{thread}/state").json()["pending"]
        loosened = choose(client, made, made.saved["helper"], read_only=True)
        failed = failing_once(made.store, "get_context", monkeypatch)
        clock.advance(RETRY)
        tick(client, made.runtime)
        before = sent(made.transport_at(SAVED_URL))
        # What the step carries into the last retry.
        (row,) = rows(client, made, thread)
        choose(client, made, loosened, read_only=False)
        clock.advance(RETRY)
        tick(client, made.runtime)
        state = client.get(f"/threads/{thread}/state").json()

    assert blipped == [True] and failed == [True]
    # The gate never paused the run, so no person was ever shown the call.
    assert paused is None and before == 0
    assert (row.gated, row.decided) == (True, False)
    assert texts(made) == []
    assert sent(made.transport_at(SAVED_URL)) == 0
    sentence = NOW_REQUIRES.format(agent="helper")
    (result,) = results(model, "handoff_to_helper")
    assert (result.status, result.content) == ("error", sentence)
    assert state["pending"] is None
    assert f"Agent helper: {sentence}" in kept_back_lines(caplog)


@pytest.mark.parametrize("where", ["orchestrator", "subagent"])
@pytest.mark.parametrize("decision", ["approve", "edit"])
def test_a_call_a_person_approved_or_edited_is_recorded_as_decided_before_it_is_sent(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    decision: str,
) -> None:
    thread = f"p-22-{where}-{decision}"
    made, _model = one_helper(make_a2a_app, "handoff", where, thread, read_only=False)
    seen = at_the_send(made, monkeypatch, thread)
    chosen: dict[str, Any] = {"type": decision}
    if decision == "edit":
        chosen["args"] = {"message": "Summarize the month."}
    with TestClient(made.app) as client:
        interrupted(run(client, thread, sse_events))
        (proposed,) = rows(client, made, thread)
        events = resume(client, thread, [chosen], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    # Undecided while it waited; decided, under its own namespace, when it was sent.
    assert proposed.decided is False
    (at_send,) = seen
    (row,) = at_send
    assert (row.namespace, row.tool_call_id) == (proposed.namespace, proposed.tool_call_id)
    assert row.decided is True
    expected = "Summarize the month." if decision == "edit" else "Summarize the week."
    assert texts(made) == [expected]


@pytest.mark.parametrize("where", ["orchestrator", "subagent"])
def test_a_call_a_person_rejected_is_not_recorded_as_decided(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, where: str
) -> None:
    thread = f"p-23-{where}"
    made, _model = one_helper(make_a2a_app, "handoff", where, thread, read_only=False)
    marked: list[tuple[str, list[str]]] = []
    mark = made.store.mark_decided

    async def marking(thread_id: str, namespace: str, ids: Any) -> None:
        marked.append((namespace, list(ids)))
        await mark(thread_id, namespace, ids)

    with TestClient(made.app) as client:
        interrupted(run(client, thread, sse_events))
        made.store.mark_decided = marking  # type: ignore[method-assign]
        events = resume(client, thread, [{"type": "reject", "message": "Not now."}], sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    # The gate answered the call itself, so no row was marked, and nothing was sent.
    assert marked == []
    assert sent(made.transport_at(SAVED_URL)) == 0


def test_a_decision_that_cannot_be_recorded_leaves_the_call_unsent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = "p-24"
    made, _model = one_helper(make_a2a_app, "handoff", "orchestrator", thread, read_only=False)
    with TestClient(made.app) as client:
        interrupted(run(client, thread, sse_events))
        failed = failing_once(made.store, "mark_decided", monkeypatch)
        events = resume(client, thread, [{"type": "approve"}], sse_events)
        (row,) = rows(client, made, thread)
    assert failed == [True]
    assert events[-1][0] == "error" and events[-1][1]["code"] == "internal"
    assert row.decided is False
    assert sent(made.transport_at(SAVED_URL)) == 0


def test_a_retry_that_resumes_after_the_gate_s_step_records_no_decision(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate's step completes unasked, and the checkpoint after it cannot be written.

    The agent ran without approval, so the gate asked nothing. Once it requires approval, the
    retry continues with the step that comes right after the gate's, in a build whose gate
    would have asked. That step may only take a call for decided in a resume, which carries
    the decisions; a background run carries none.
    """
    thread = "p-26"
    made, model = one_helper(make_a2a_app, "handoff", "background", thread, read_only=True)
    written: list[bool] = []
    with TestClient(made.app) as client:
        saver = made.runtime.agent.checkpointer
        write = saver.aput

        async def aput(*args: Any) -> Any:
            if await made.store.list_calls(thread):
                written.append(True)
                # The first checkpoint is the model's, the second the gate's.
                if len(written) == 2:
                    raise RuntimeError("the database went away for a moment")
            return await write(*args)

        monkeypatch.setattr(saver, "aput", aput)
        tick(client, made.runtime)
        before = sent(made.transport_at(SAVED_URL))
        choose(client, made, made.saved["helper"], read_only=False)
        clock.advance(RETRY)
        tick(client, made.runtime)

    assert len(written) >= 2 and before == 0
    assert texts(made) == []
    (result,) = results(model, "handoff_to_helper")
    assert (result.status, result.content) == ("error", NOW_REQUIRES.format(agent="helper"))


class TwoSubagents(ScriptedChatModel):
    """The orchestrator starts two subagents in one reply. The first proposes a notification,
    which pauses the run; the second proposes a message to `helper`. Each answers by the
    task it was given, whatever order the two run in."""

    def _generate(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kw: Any):
        from langchain_core.messages import HumanMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        self.calls += 1
        self.seen.append(list(messages))
        humans = [message for message in messages if isinstance(message, HumanMessage)]
        first = str(humans[0].content) if humans else ""
        answered = any(message.type == "tool" for message in messages)
        if answered:
            reply = AIMessage(content="Done.")
        elif "first-job" in first:
            notify = {"recipient": "ops", "message": "hi"}
            reply = AIMessage(
                content="",
                tool_calls=[{"id": "s1c", "name": "send_notification", "args": notify}],
            )
        elif "second-job" in first:
            reply = AIMessage(
                content="",
                tool_calls=[
                    {"id": "s2c", "name": "handoff_to_helper", "args": {"message": "UNSEEN"}}
                ],
            )
        else:
            delegate = {"subagent_type": "general-purpose"}
            reply = AIMessage(
                content="",
                tool_calls=[
                    {"id": "t1", "name": "task", "args": {"description": "first-job", **delegate}},
                    {"id": "t2", "name": "task", "args": {"description": "second-job", **delegate}},
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


def test_a_resume_records_no_decision_for_a_sibling_subagent_s_calls(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by the review of Phase 11. `helper` runs without approval. Two subagents run at
    once: the first pauses on a notification; the second's gate lets its message to
    `helper` through unasked, and the checkpoint after that gate's step cannot be written.
    `helper` then requires approval, and a person approves the notification. The resume
    continues both subagents, and only the first one's gate received a decision: the
    second's message was never seen by anyone."""
    thread = "p-27"
    model = TwoSubagents(script=[])
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})
    with TestClient(made.app) as client:
        saver = made.runtime.agent.checkpointer
        put, put_writes = saver.aput, saver.aput_writes
        first_paused = asyncio.Event()
        failed: list[str] = []

        async def writes(config: Any, values: Any, task_id: str, task_path: str = "") -> Any:
            interrupts = any(channel == "__interrupt__" for channel, _ in values)
            if interrupts and not config["configurable"].get("checkpoint_ns"):
                first_paused.set()
            return await put_writes(config, values, task_id, task_path)

        async def checkpoint(config: Any, saved: Any, metadata: Any, versions: Any) -> Any:
            namespace = config["configurable"].get("checkpoint_ns", "")
            messages = saved["channel_values"].get("messages") or []
            last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
            after_its_gate = any("AgentTasksMiddleware.after_model" in key for key in versions)
            if (
                not failed
                and namespace
                and last is not None
                and last.tool_calls
                and last.tool_calls[0]["name"] == "handoff_to_helper"
                and after_its_gate
            ):
                failed.append(namespace)
                # Once the first subagent has paused, so that the run ends paused on it.
                await asyncio.wait_for(first_paused.wait(), 5)
                raise RuntimeError("the database went away for a moment")
            return await put(config, saved, metadata, versions)

        monkeypatch.setattr(saver, "aput", checkpoint)
        monkeypatch.setattr(saver, "aput_writes", writes)
        run(client, thread, sse_events)
        paused = pending(client, thread)
        before = sent(made.transport_at(SAVED_URL))
        marked: list[str] = []
        mark = made.store.mark_decided

        async def marking(thread_id: str, namespace: str, ids: Any) -> None:
            marked.append(namespace)
            await mark(thread_id, namespace, ids)

        made.store.mark_decided = marking  # type: ignore[method-assign]
        choose(client, made, made.saved["helper"], read_only=False)
        resume(client, thread, [{"type": "approve"}], sse_events)

    assert len(failed) == 1 and before == 0
    assert [action["name"] for action in paused["action_requests"]] == ["send_notification"]
    # The second subagent's calls are in a namespace of their own, and the resume decided
    # nothing there.
    assert failed[0] not in marked
    assert texts(made) == []
    assert sent(made.transport_at(SAVED_URL)) == 0


# Part 3 (Phase 8): removing and moving, through the routes.

ELSEWHERE = (
    "Agent '{agent}': the address this run started with is another agent's now; nothing was sent."
)


def helper_of(client: TestClient) -> dict[str, Any]:
    (agent,) = [a for a in client.get("/agents").json()["agents"] if a["name"] == "helper"]
    return agent


def remove_helper(client: TestClient) -> None:
    agent = helper_of(client)
    response = client.delete(f"/agents/{agent['id']}", params={"version": agent["version"]})
    assert response.status_code == 200, response.text


def move_helper(client: TestClient, made: A2aApp, url: str = OTHER_URL) -> StubAgent:
    """Move `helper` through the route to a stand-in of its own at ``url``."""
    there = make_stub("helper")
    made.add_stub(url, there)
    agent = helper_of(client)
    response = client.patch(
        f"/agents/{agent['id']}", json={"version": agent["version"], "url": url}
    )
    assert response.status_code == 200, response.text
    return there


class InTheTool:
    """Holds an agent's tool where it is about to look at its call's row: after its run, or
    its continuation, began, and before anything is checked or sent. The event loop stays
    free, so a request made meanwhile is served."""

    def __init__(self, made: A2aApp, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        get_call = made.store.get_call

        async def waiting(*args: Any) -> Any:
            if not self.reached.is_set():
                self.reached.set()
                await asyncio.get_running_loop().run_in_executor(None, self.release.wait, 10)
            return await get_call(*args)

        monkeypatch.setattr(made.store, "get_call", waiting)

    def during(self, work: Callable[[], Any], then: Callable[[], Any]) -> Any:
        """Run ``work`` in a thread; once its tool waits, do ``then`` and let it go on."""
        result: dict[str, Any] = {}
        running = threading.Thread(target=lambda: result.update(value=work()))
        running.start()
        try:
            assert self.reached.wait(10), "no tool reached its check"
            then()
        finally:
            self.release.set()
        running.join(10)
        assert not running.is_alive()
        return result["value"]


def test_after_a_removal_the_next_run_has_neither_of_the_agent_s_tools(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[propose(TO_HELPER)])
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})
    with TestClient(made.app) as client:
        before = set(tools(client))
        remove_helper(client)
        after = set(tools(client))
        events = run(client, "q-1", sse_events)

    assert {"handoff_to_helper", "cancel_helper_task"} <= before
    assert not {"handoff_to_helper", "cancel_helper_task"} & after
    (result,) = tool_results(events, "handoff_to_helper")
    assert "not a valid tool" in result["content"]
    assert sent(made.transport_at(SAVED_URL)) == 0


@pytest.mark.parametrize("read_only", [True, False])
def test_a_run_under_way_when_its_agent_is_removed_through_the_route_sends_nothing(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    read_only: bool,
) -> None:
    model = ScriptedChatModel(script=[propose(TO_HELPER)])
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": read_only}})
    transport = made.transport_at(SAVED_URL)
    with TestClient(made.app) as client:
        if read_only:
            # The run started while the agent ran without approval.
            tool = InTheTool(made, monkeypatch)
            events = tool.during(
                lambda: run(client, "q-2", sse_events), lambda: remove_helper(client)
            )
        else:
            # A person approved the hand-off, and its continuation began before the removal.
            interrupted(run(client, "q-2", sse_events))
            tool = InTheTool(made, monkeypatch)
            events = tool.during(
                lambda: resume(client, "q-2", [{"type": "approve"}], sse_events),
                lambda: remove_helper(client),
            )
        at_the_removal = list(transport.requests)

    assert events[-1] == ("end", {"status": "completed"})
    (result,) = results(model, "handoff_to_helper")
    assert (result.status, result.content) == ("error", REMOVED.format(agent="helper"))
    # By the stand-in's record nothing of any kind reached it once the removal was answered.
    assert sent(transport) == 0 and transport.requests == at_the_removal
    record = made.stub_at(SAVED_URL).record
    assert record.messages == [] and record.cancels == []


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_a_paused_proposal_decided_after_its_agent_was_removed_is_never_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, decision: str
) -> None:
    model = ScriptedChatModel(script=[propose(TO_HELPER)], final_text="That agent is gone.")
    made = make_a2a_app(model, {}, saved=HELPER)
    decisions = {"approve": {"type": "approve"}, "reject": {"type": "reject", "message": "no"}}
    with TestClient(made.app) as client:
        proposal = interrupted(run(client, "q-3", sse_events))
        remove_helper(client)
        now = pending(client, "q-3")
        events = resume(client, "q-3", [decisions[decision]], sse_events)

    assert now["interrupt_id"] == proposal["interrupt_id"]
    (action,) = now["action_requests"]
    # No agent has the tool any more: the proposal can be approved or rejected, and either
    # way nothing is sent.
    assert (action["agent"], action["allowed_decisions"]) == (None, ["approve", "reject"])
    assert events[-1] == ("end", {"status": "completed"})
    assert sent(made.transport_at(SAVED_URL)) == 0


@pytest.mark.parametrize(
    ("approval", "decision"),
    [("required", "approve"), ("required", "reject"), ("none", "approve")],
)
def test_a_proposal_for_a_removed_agent_is_not_sent_to_one_added_under_its_name_by_the_route(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, approval: str, decision: str
) -> None:
    model = ScriptedChatModel(script=[propose(TO_HELPER)], final_text="Understood.")
    made = make_a2a_app(model, {}, saved=HELPER)
    again = make_stub("helper")
    decisions = {"approve": {"type": "approve"}, "reject": {"type": "reject", "message": "no"}}
    with TestClient(made.app) as client:
        interrupted(run(client, "q-4", sse_events))
        remove_helper(client)
        made.add_stub(OTHER_URL, again)
        added = client.post(
            "/agents", json={"name": "helper", "url": OTHER_URL, "approval": approval}
        )
        assert added.status_code == 201, added.text
        (action,) = pending(client, "q-4")["action_requests"]
        events = resume(client, "q-4", [decisions[decision]], sse_events)

    # The proposal accepts what the tool under that name allows now.
    allowed = ["approve", "edit", "reject"] if approval == "required" else ["approve"]
    assert (action["agent"], action["allowed_decisions"]) == ("helper", allowed)
    assert events[-1] == ("end", {"status": "completed"})
    # Whatever is decided, nothing is sent: not to the agent that was removed, and not to
    # the one that has its name.
    assert sent(made.transport_at(SAVED_URL)) == 0 and again.record.messages == []
    if decision == "approve":
        (result,) = results(model, "handoff_to_helper")
        assert result.content == KEPT_BACK.format(agent="helper")


def test_a_paused_proposal_approved_after_its_agent_was_moved_goes_to_the_current_address(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[propose(TO_HELPER)]), {}, saved=HELPER)
    with TestClient(made.app) as client:
        proposal = interrupted(run(client, "q-5", sse_events))
        there = move_helper(client, made)
        now = pending(client, "q-5")
        events = resume(client, "q-5", [{"type": "approve"}], sse_events)

    assert now["interrupt_id"] == proposal["interrupt_id"]
    assert now["action_requests"][0]["allowed_decisions"] == ["approve", "edit", "reject"]
    assert events[-1] == ("end", {"status": "completed"})
    # The proposal names its agent, never an address: it went where the agent is now.
    assert [message.text for message in there.record.messages] == ["Summarize the week."]
    assert sent(made.transport_at(SAVED_URL)) == 0


@pytest.mark.parametrize("taken", [False, True])
def test_a_run_in_progress_finishes_at_its_address_unless_that_is_another_agent_s_now(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
    taken: bool,
) -> None:
    model = LabelledChatModel(script=[handoff("From the run in progress.", agent="helper")])
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})
    first = made.stub_at(SAVED_URL)
    streamed: dict[str, Events] = {}
    caplog.set_level(logging.WARNING, logger=TOOLS)
    with TestClient(made.app) as client:
        model.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "q-6", sse_events))
        )
        running.start()
        with model.held():
            # The run has reached its model, with the tool of the first address.
            there = move_helper(client, made)
            if taken:
                # Another agent, which requires approval, is saved where this one was.
                added = client.post("/agents", json={"name": "other", "url": SAVED_URL})
                assert added.status_code == 201, added.text
            before = sent(made.transport_at(SAVED_URL))
        running.join(10)
        assert not running.is_alive()

    events = streamed["events"]
    assert events[-1] == ("end", {"status": "completed"})
    (result,) = tool_results(events, "handoff_to_helper")
    assert there.record.messages == []
    if taken:
        # Nothing reaches an address that has become another agent's, unseen.
        sentence = ELSEWHERE.format(agent="helper")
        assert result["content"] == sentence
        assert first.record.messages == [] and sent(made.transport_at(SAVED_URL)) == before
        assert kept_back_lines(caplog) == [f"Agent helper: {sentence}"]
    else:
        # The run finished at the address it started with.
        assert result["content"].startswith("Agent 'helper' completed task ")
        assert [message.text for message in first.record.messages] == ["From the run in progress."]


def test_an_approved_continuation_sends_nothing_to_an_address_that_became_another_agent_s(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(script=[propose(TO_HELPER)])
    made = make_a2a_app(model, {}, saved=HELPER)
    first = made.stub_at(SAVED_URL)
    moved: dict[str, StubAgent] = {}

    def moved_and_taken() -> None:
        moved["there"] = move_helper(client, made)
        added = client.post("/agents", json={"name": "other", "url": SAVED_URL})
        assert added.status_code == 201, added.text

    with TestClient(made.app) as client:
        interrupted(run(client, "q-7", sse_events))
        # A person approved the hand-off. Its continuation has begun, with the tool of the
        # first address, when the agent is moved and another is saved where it was.
        tool = InTheTool(made, monkeypatch)
        events = tool.during(
            lambda: resume(client, "q-7", [{"type": "approve"}], sse_events), moved_and_taken
        )

    assert events[-1] == ("end", {"status": "completed"})
    (result,) = results(model, "handoff_to_helper")
    assert result.content == ELSEWHERE.format(agent="helper")
    assert first.record.messages == [] and moved["there"].record.messages == []


def test_a_resume_names_no_namespace_for_an_interrupt_no_paused_task_holds() -> None:
    """Nothing is then marked as decided (005 Phase 11)."""
    from types import SimpleNamespace

    from langgraph.types import Interrupt

    from freyja_agent.api.decisions import decided_namespace

    own = Interrupt.from_ns("value", "HumanInTheLoopMiddleware.after_model:t-1")
    inner = Interrupt.from_ns("value", "tools:t-2|HumanInTheLoopMiddleware.after_model:t-3")
    gate = SimpleNamespace(name="HumanInTheLoopMiddleware.after_model", id="t-1", interrupts=(own,))
    tools = SimpleNamespace(name="tools", id="t-2", interrupts=(inner,))
    assert decided_namespace([gate, tools], own.id) == ""
    assert decided_namespace([gate, tools], inner.id) == "tools:t-2"
    assert decided_namespace([gate, tools], "another") is None
    assert decided_namespace([], own.id) is None


def test_a_retried_resume_records_no_decision_for_a_call_its_person_was_not_shown(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by the second review of Phase 11. The subagent proposes a message to `helper`,
    which runs without approval and is not shown, and a notification, which pauses. The
    first resume approves; the gate's step completes and the checkpoint after it cannot be
    written, so the same proposal is still pending. `helper` then requires approval, and
    the second resume approves the notification again. It reuses the gate's step from the
    first, whose build did not gate `helper`, and the step after the gate runs in the build
    that does. Nobody was shown the message to `helper`."""
    thread = "p-29"
    notify = ("send_notification", {"recipient": "ops", "message": "hi"})
    model = ScriptedChatModel(script=[DELEGATE, propose(TO_HELPER, notify, step="d")])
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})
    with TestClient(made.app) as client:
        interrupted(run(client, thread, sse_events))
        shown = [action["name"] for action in pending(client, thread)["action_requests"]]
        saver = made.runtime.agent.checkpointer
        put = saver.aput
        failed: list[Any] = []

        async def checkpoint(*args: Any) -> Any:
            if not failed:
                failed.append(True)
                raise RuntimeError("the database went away for a moment")
            return await put(*args)

        monkeypatch.setattr(saver, "aput", checkpoint)
        first = resume(client, thread, [{"type": "approve"}], sse_events)
        still = pending(client, thread)
        choose(client, made, made.saved["helper"], read_only=False)
        second = resume(client, thread, [{"type": "approve"}], sse_events)

    assert shown == ["send_notification"] and failed == [True]
    assert first[-1][0] == "error"
    assert [action["name"] for action in still["action_requests"]] == ["send_notification"]
    assert second[-1] == ("end", {"status": "completed"})
    assert texts(made) == []
    assert sent(made.transport_at(SAVED_URL)) == 0


def test_a_retry_across_two_restarts_sends_nothing_nobody_decided_to_an_environment_agent(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first sequence of Phase 11, for an agent named in the environment, whose choice
    changes only at a restart (found by the second review: nothing tested this branch).

    `stub` requires approval; the checkpoint after the model's step cannot be written, so
    the gate never asks. After a restart `stub` runs without approval: the retry passes the
    gate unasked and its tool's step fails. After another restart `stub` requires approval
    again, and the retry runs the tool past the gate. Nobody decided the message."""
    thread = "p-30"
    step = propose(("handoff_to_stub", {"message": "Summarize the week."}), step="b")
    model = ScriptedChatModel(script=[step])
    gated = {"hello": {"read_only": True}, "stub": {}}
    first = make_a2a_app(model, gated)
    seed_task(first, "hello", "task-1", thread=thread)
    blipped: list[bool] = []
    with TestClient(first.app) as client:
        saver = first.runtime.agent.checkpointer
        write = saver.aput

        async def checkpoint(*args: Any) -> Any:
            if not blipped and await first.store.list_calls(thread):
                blipped.append(True)
                raise RuntimeError("the database went away for a moment")
            return await write(*args)

        monkeypatch.setattr(saver, "aput", checkpoint)
        tick(client, first.runtime)
        (row,) = rows(client, first, thread)

    loose = {"hello": {"read_only": True}, "stub": {"read_only": True}}
    second = restart(make_a2a_app, first, model, loose, stubs=first.stubs)
    with TestClient(second.app) as client:
        failed = failing_once(second.store, "get_context", monkeypatch)
        clock.advance(RETRY)
        tick(client, second.runtime)
        before = list(first.stubs["stub"].record.messages)

    third = restart(make_a2a_app, first, model, gated, stubs=first.stubs)
    with TestClient(third.app) as client:
        clock.advance(RETRY)
        tick(client, third.runtime)

    assert blipped == [True] and failed == [True] and before == []
    assert (row.agent, row.agent_id, row.gated, row.decided) == ("stub", None, True, False)
    assert first.stubs["stub"].record.messages == []
    (result,) = results(model, "handoff_to_stub")
    assert result.content == NOW_REQUIRES.format(agent="stub")


def test_a_later_reply_of_a_resume_that_proposes_the_approved_message_again_is_not_decided(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A person approves a message to `helper` while it runs without approval. In the same
    resume the model proposes the same message again, which passes the gate unasked, and
    `helper` requires approval before that second message is sent. The person decided the
    first one only (found when Phase 11 was rebased on feature 007's segmented runs)."""
    thread = "p-31"
    model = ScriptedChatModel(script=[propose(TO_HELPER), propose(TO_HELPER, step="e")])
    made = make_a2a_app(model, {}, saved=HELPER)
    caplog.set_level(logging.WARNING, logger=TOOLS)
    with TestClient(made.app) as client:
        interrupted(run(client, thread, sse_events))
        loosened = choose(client, made, made.saved["helper"], read_only=True)
        get_call = made.store.get_call

        async def tightening(thread_id: str, namespace: str, tool_call_id: str) -> Any:
            if tool_call_id == "e1":
                await made.saved_agents.change(loosened.id, loosened.version, read_only=False)
            return await get_call(thread_id, namespace, tool_call_id)

        monkeypatch.setattr(made.store, "get_call", tightening)
        events = resume(client, thread, [{"type": "approve"}], sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    # The approved message was sent once; the second one, which nobody decided, was not.
    assert texts(made) == ["Summarize the week."]
    contents = [result.content for result in results(model, "handoff_to_helper")]
    assert contents[-1] == NOW_REQUIRES.format(agent="helper")
    assert len(contents) == 2


def test_a_later_reply_that_gives_its_call_the_paused_call_s_id_is_not_decided(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As the test before, with the later reply's call under the paused call's own id, as
    a model server that numbers its calls from 0 in each reply gives it (found by the last
    review of Phase 11)."""
    thread = "p-32"
    model = ScriptedChatModel(script=[propose(TO_HELPER), propose(TO_HELPER)])
    made = make_a2a_app(model, {}, saved=HELPER)
    with TestClient(made.app) as client:
        interrupted(run(client, thread, sse_events))
        loosened = choose(client, made, made.saved["helper"], read_only=True)
        get_call = made.store.get_call
        seen: list[str] = []

        async def tightening(thread_id: str, namespace: str, tool_call_id: str) -> Any:
            seen.append(tool_call_id)
            if len(seen) == 2:
                await made.saved_agents.change(loosened.id, loosened.version, read_only=False)
            return await get_call(thread_id, namespace, tool_call_id)

        monkeypatch.setattr(made.store, "get_call", tightening)
        events = resume(client, thread, [{"type": "approve"}], sse_events)

    assert seen == ["c1", "c1"]
    assert events[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["Summarize the week."]
    contents = [result.content for result in results(model, "handoff_to_helper")]
    assert contents[-1] == NOW_REQUIRES.format(agent="helper")
