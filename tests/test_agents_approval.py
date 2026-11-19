"""005: a message is only ever sent to the agent it was proposed for (part 1).

FR-023, FR-030; research R8; data-model.md "Proposed call" and "What a call gives without
sending". A pause holds a tool's name and nothing about which agent had the name. So each call
of an agent's tool that a model proposes is kept as a row, from the model's reply until the
call's send is finished, and the tool reads it before it sends: a proposal made for one agent
is never sent to another that has its name by the time it is decided.

Every agent is the stand-in, inside the test process. Saved agents are written to the store,
as `tests/test_agents_apply.py` part 1 does; the route that adds one is covered there.
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from freyja_agent.delegates import delegation_worker
from freyja_agent.runtime.agent_tasks import AgentTaskRecord, ProposedCall
from tests.conftest import A2aApp
from tests.fakes import FakeClock, ScriptedChatModel
from tests.test_a2a_background import RETRY, FlakyChatModel, follow_up, ready_outcome, retry_app
from tests.test_a2a_gate import DELEGATE, PROPOSED, interrupted, pending, propose, resume
from tests.test_a2a_handoff import DONE, handoff, run, tool_results
from tests.test_a2a_pending import stub, tick
from tests.test_agents_apply import save, texts
from tests.test_agents_startup import SAVED_URL, TOOLS, sent, warnings

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
    # Still there while the message was on its way, and gone once the send was recorded.
    assert [keys(found) for found in during] == [[("", "c1")]]
    assert afterwards == []


def test_a_call_that_needs_no_decision_has_its_row_while_it_is_sent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(
        ScriptedChatModel(script=[handoff("Say hello.")]), {"hello": {"read_only": True}}
    )
    during = at_the_send(made, monkeypatch, "v-2")
    with TestClient(made.app) as client:
        events = run(client, "v-2", sse_events)
        afterwards = rows(client, made, "v-2")
    assert events[-1] == ("end", {"status": "completed"})
    ((row,),) = during
    # An environment agent has no id, and its call needed no decision.
    assert (row.namespace, row.tool_call_id) == ("", "h1")
    assert (row.agent, row.agent_id, row.gated) == ("hello", None, False)
    assert afterwards == []


def test_a_subagent_s_call_has_its_row_under_the_subagent_s_namespace(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(script=[DELEGATE, propose(TO_HELPER, step="d")])
    made = make_a2a_app(model, {}, saved=HELPER)
    during = at_the_send(made, monkeypatch, "v-3")
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
    assert [keys(found) for found in during] == [[(paused.namespace, "d1")]]
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
