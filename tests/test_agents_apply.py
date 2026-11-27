"""005: a change of the saved agents takes effect without a restart.

FR-019, FR-030, SC-001, SC-002, SC-003, SC-013; research R3. In part 1 the saved agents are
written to the store, as another instance's route would write them. A run that starts after
a change has the new set, on the orchestrator and on its subagent; a run in progress keeps
the tools it started with; a second instance on the same stores has the change in its next
run, its catalog, and its next round of watching; and a decision is validated and carried
out on one orchestrator.

Part 2 adds through the route, `POST /agents`: the agent can be handed work in the next run
with no restart, always under approval, and is still there after a restart.

Part 3 changes an approval choice through the route, `PATCH /agents/{id}`, on one instance
and sends each decision to another (FR-019, FR-023; research R3, R20): a proposal can then
only be approved once its agent runs without approval, and a step whose call came to require
approval cannot be continued, whatever is decided.

Every agent is the stand-in, inside the test process.
"""

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from freyja_agent.api.streaming import lease_owner, take_context
from freyja_agent.runtime import runtime as runtime_module
from freyja_agent.runtime.agent_tasks import AgentTaskRecord
from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.saved_agents import SavedAgentRecord
from tests.a2a_stub import StubAgent, make_stub
from tests.conftest import A2aApp, saved_card
from tests.fakes import FakeClock, LabelledChatModel, ScriptedChatModel
from tests.test_a2a_background import seen_outcomes
from tests.test_a2a_gate import DELEGATE, interrupted, pending, propose, refuse, resume
from tests.test_a2a_handoff import DONE, handoff, run, tool_results
from tests.test_agents_startup import SAVED_URL, listed, seed_task, sent, task_state, tick, tools

Events = list[tuple[str, Any]]
REGISTRY = "freyja_agent.delegates.agent_registry"
OTHER_URL = "http://127.0.0.1:9101"
MOVED_URL = "http://127.0.0.1:9102"
HELPER_TOOLS = {"handoff_to_helper", "cancel_helper_task"}


def save(
    client: TestClient,
    made: A2aApp,
    name: str = "helper",
    url: str = SAVED_URL,
    *,
    read_only: bool = False,
    stub: StubAgent | None = None,
) -> SavedAgentRecord:
    """Save an agent while the app runs, as another instance's route would."""
    stub = stub or make_stub(name)
    made.add_stub(url, stub)
    return client.portal.call(
        lambda: made.saved_agents.add(
            name=name, url=url, card=saved_card(stub), read_only=read_only
        )
    )


def choose(
    client: TestClient, made: A2aApp, record: SavedAgentRecord, *, read_only: bool
) -> SavedAgentRecord:
    """Change an agent's approval choice in the store."""
    changed, _given_up = client.portal.call(
        lambda: made.saved_agents.change(record.id, record.version, read_only=read_only)
    )
    return changed


def texts(made: A2aApp, url: str = SAVED_URL) -> list[str]:
    return [message.parts[0][1] or "" for message in made.stub_at(url).record.messages]


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts every build of an orchestrator: one entry per call of the factory."""
    counted: list[int] = []
    build = runtime_module.build_agent

    def counting(*args: Any, **kwargs: Any) -> Any:
        counted.append(len(kwargs.get("delegate_tools", ())))
        return build(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "build_agent", counting)
    return counted


def test_an_agent_added_to_the_store_is_in_the_next_run_gated_as_its_choice_says(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            DONE,
            propose(("handoff_to_helper", {"message": "From the orchestrator."})),
            DONE,
            DELEGATE,
            propose(("handoff_to_helper", {"message": "From the subagent."}), step="d"),
        ]
    )
    made = make_a2a_app(model, {})
    with TestClient(made.app) as client:
        run(client, "a-1", sse_events)
        before = set(model.bound_tool_names)
        assert tools(client).keys().isdisjoint(HELPER_TOOLS | {"check_agent_tasks"})

        save(client, made)
        catalog = tools(client)
        proposed = run(client, "a-1", sse_events, "use the helper")
        bound = set(model.bound_tool_names)
        # The same on the subagent, on another thread.
        resume(client, "a-1", [{"type": "reject", "message": "not now"}], sse_events)
        delegated = run(client, "a-2", sse_events, "let the subagent ask")

    assert before.isdisjoint(HELPER_TOOLS | {"check_agent_tasks"})
    # Both tools, gated: the saved choice requires approval.
    assert {name: catalog[name]["classification"] for name in HELPER_TOOLS} == {
        "handoff_to_helper": "gated",
        "cancel_helper_task": "gated",
    }
    assert bound >= HELPER_TOOLS
    # The first agent of a service that had none brings the pending-task tool with it.
    assert "check_agent_tasks" in catalog
    assert "check_agent_tasks" in bound
    for events in (proposed, delegated):
        kind, data = events[-1]
        assert kind == "interrupt", events[-1]
        assert [(a["name"], a["agent"]) for a in data["action_requests"]] == [
            ("handoff_to_helper", "helper")
        ]
    # Nothing was sent, and the service was not restarted.
    assert texts(made) == []
    assert len(made.runtimes) == 1


def test_an_agent_saved_to_run_without_approval_is_not_gated(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            handoff("From the orchestrator.", agent="helper"),
            DONE,
            DELEGATE,
            handoff("From the subagent.", agent="helper", call_id="h2"),
        ]
    )
    made = make_a2a_app(model, {})
    with TestClient(made.app) as client:
        save(client, made, read_only=True)
        catalog = tools(client)
        first = run(client, "a-3", sse_events)
        second = run(client, "a-4", sse_events)
    assert {catalog[name]["classification"] for name in HELPER_TOOLS} == {"read_only"}
    assert first[-1] == second[-1] == ("end", {"status": "completed"})
    assert texts(made) == ["From the orchestrator.", "From the subagent."]


def test_a_run_in_progress_keeps_the_tools_it_started_with(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = LabelledChatModel(
        script=[
            handoff("From the run that was in progress.", agent="helper"),
            DONE,
            handoff("From the next run.", agent="helper", call_id="h2"),
        ]
    )
    made = make_a2a_app(model, {})
    streamed: dict[str, Events] = {}
    with TestClient(made.app) as client:
        model.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "a-5", sse_events))
        )
        running.start()
        with model.held():
            # The run has reached its model and waits there while the agent is saved.
            save(client, made, read_only=True)
            assert tools(client).keys() >= HELPER_TOOLS
        running.join(10)
        assert not running.is_alive()
        after_the_first = texts(made)
        following = run(client, "a-6", sse_events)

    # The run in progress was built without the agent: its call names no tool it has.
    (unknown,) = tool_results(streamed["events"], "handoff_to_helper")
    assert "not a valid tool" in unknown["content"]
    assert streamed["events"][-1] == ("end", {"status": "completed"})
    assert after_the_first == []
    # The run that started afterwards has the tool.
    (result,) = tool_results(following, "handoff_to_helper")
    assert "completed task" in result["content"], result
    assert texts(made) == ["From the next run."]


def test_a_paused_run_continued_afterwards_has_the_new_set(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            propose(("handoff_to_stub", {"message": "To the gated agent."})),
            handoff("From the continuation.", agent="helper", call_id="h2"),
        ]
    )
    made = make_a2a_app(model, {"stub": {}})
    with TestClient(made.app) as client:
        paused = run(client, "a-7", sse_events)
        assert paused[-1][0] == "interrupt"
        save(client, made, read_only=True)
        continued = resume(client, "a-7", [{"type": "approve"}], sse_events)
    assert continued[-1] == ("end", {"status": "completed"})
    (result,) = tool_results(continued, "handoff_to_helper")
    assert "completed task" in result["content"], result
    assert texts(made) == ["From the continuation."]


def test_a_background_run_that_starts_afterwards_has_the_new_set(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    model = ScriptedChatModel(
        script=[handoff("From the background run.", agent="helper", call_id="b1")]
    )
    made = make_a2a_app(model, {"hello": {"read_only": True}})
    # An outcome for the worker to deliver: a task the stand-in will not know.
    seed_task(made, "hello", "task-1", thread="a-8")
    with TestClient(made.app) as client:
        save(client, made, read_only=True)
        # One round: the task is checked, its outcome is ready, and a background run tells
        # the thread.
        tick(client, made)
        state = client.get("/threads/a-8/state").json()
    assert texts(made) == ["From the background run."]
    assert state["messages"][-1]["content"] == "Done."


def test_nothing_is_rebuilt_while_nothing_changed_and_runs_that_start_together_rebuild_once(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    builds: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}})
    with TestClient(made.app) as client:
        at_startup = len(builds)
        run(client, "a-9", sse_events)
        run(client, "a-9", sse_events, "again")
        client.get("/tools")
        client.get("/threads/a-9/state")
        unchanged = len(builds)

        save(client, made)
        runtime = made.runtime

        # The in-memory stores answer without ever giving way, so two calls would run one
        # after the other. A database gives way at every read: these do too.
        def giving_way(read: Any) -> Any:
            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                await asyncio.sleep(0)
                return await read(*args, **kwargs)

            return wrapped

        for store, name in (
            (made.saved_agents, "revision"),
            (made.saved_agents, "list_agents"),
            (runtime.llm_profiles, "state"),
        ):
            monkeypatch.setattr(store, name, giving_way(getattr(store, name)))

        async def three_at_once() -> None:
            contexts = await asyncio.gather(*(runtime.begin_run() for _ in range(3)))
            assert len({id(context.agents) for context in contexts}) == 1
            assert len({id(context.agent) for context in contexts}) == 1
            assert all(link.connected or not link.tried for link in contexts[0].links)
            for context in contexts:
                await context.release()

        client.portal.call(three_at_once)
        together = len(builds)
        run(client, "a-9", sse_events, "once more")

    assert at_startup == 1
    assert unchanged == at_startup
    # The runs that started together after the change share one new set and one rebuild.
    assert together == at_startup + 1
    assert len(builds) == together


def test_a_second_instance_has_the_change_in_its_next_run_its_catalog_and_its_next_round(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    shared: dict[str, Any] = {
        "saver": InMemorySaver(),
        "locks": InMemoryRunLocks(60, clock=clock.now),
    }
    first = make_a2a_app(ScriptedChatModel(), {}, **shared)
    model = ScriptedChatModel(script=[handoff("From the other instance.", agent="helper")])
    second = make_a2a_app(
        model,
        {},
        instance_id="instance-b",
        store=first.store,
        saved_agents=first.saved_agents,
        at=first.at,
        **shared,
    )
    with TestClient(first.app) as one, TestClient(second.app) as two:
        assert two.get("/tools").json()["agents"] == []
        # Saved through the first instance, with a task a run of it started right away.
        record = save(one, first, read_only=True)
        now = one.portal.call(first.store.now)
        one.portal.call(first.store.record_task, _task("helper", "task-1", record.ref, now))
        # The first thing the second instance does afterwards is a round of watching: it
        # learns of the agent before it claims, and does not give its task up.
        two.portal.call(second.runtime.worker.watch)
        watched = task_state(two, second, "helper", "task-1")
        in_the_catalog = listed(two, "helper")
        events = run(two, "a-10", sse_events)
    assert watched != "given_up"
    assert watched == "failed"  # checked with the agent, which does not know the made-up task
    assert in_the_catalog["reachable"] is True
    (result,) = tool_results(events, "handoff_to_helper")
    assert "completed task" in result["content"], result
    assert texts(first) == ["From the other instance."]


def _task(agent: str, task_id: str, ref: str, now: Any, thread: str = "a-0") -> AgentTaskRecord:
    return AgentTaskRecord(
        agent=agent,
        task_id=task_id,
        thread_id=thread,
        context_id="ctx",
        message="Compile the report.",
        state="working",
        started_at=now,
        last_checked_at=now,
        agent_ref=ref,
    )


def test_a_reject_is_refused_once_the_agent_stopped_requiring_approval(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[propose(("handoff_to_helper", {"message": "Summarize the week."}))]
    )
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL}})
    with TestClient(made.app) as client:
        paused = run(client, "a-11", sse_events)
        assert paused[-1][0] == "interrupt"
        # Changed through another instance: this one has not seen it when the decision comes.
        choose(client, made, made.saved["helper"], read_only=True)
        errors = refuse(client, "a-11", [{"type": "reject", "message": "no"}])
        still = pending(client, "a-11")
    # Validated on the orchestrator that would carry it out: refused as it is today for a
    # call that no longer needs a decision, where the earlier catalog would have let the
    # reject through and the run would then have sent the message.
    assert "no longer needs a decision" in errors[0]["msg"]
    assert still["interrupt_id"] == paused[-1][1]["interrupt_id"]
    assert sent(made.transport_at(SAVED_URL)) == 0


def test_one_approve_is_not_applied_to_another_call(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    step = propose(
        ("handoff_to_helper", {"message": "To the one that required approval."}),
        ("handoff_to_other", {"message": "To the one that did not."}),
    )
    made = make_a2a_app(
        ScriptedChatModel(script=[step]),
        {},
        saved={
            "helper": {"url": SAVED_URL},
            "other": {"url": OTHER_URL, "read_only": True},
        },
    )
    with TestClient(made.app) as client:
        paused = run(client, "a-12", sse_events)
        assert [a["name"] for a in paused[-1][1]["action_requests"]] == ["handoff_to_helper"]
        # While the step is paused, the two agents swap their approval choices.
        choose(client, made, made.saved["helper"], read_only=True)
        choose(client, made, made.saved["other"], read_only=False)
        events = resume(client, "a-12", [{"type": "approve"}], sse_events)
        still = pending(client, "a-12")
    # The approve was given for the call to `helper`. It is not taken for the call to
    # `other`, which now requires one: the run ends with an error and the proposal stays.
    assert events[-1][0] == "error", events[-1]
    assert still["interrupt_id"] == paused[-1][1]["interrupt_id"]
    assert sent(made.transport_at(SAVED_URL)) == 0
    assert sent(made.transport_at(OTHER_URL)) == 0


def test_a_run_that_cannot_learn_the_current_set_does_not_run(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_app(model, {"hello": {}}, saved={"helper": {"url": SAVED_URL}})
    with TestClient(made.app) as client:
        run(client, "a-13", sse_events)
        asked = model.calls
        catalog = client.get("/tools").json()

        async def unreadable() -> int:
            raise RuntimeError("the database cannot be reached at 127.0.0.1:9100")

        monkeypatch.setattr(made.saved_agents, "revision", unreadable)
        events = run(client, "a-13", sse_events, "again")
        asked_after = model.calls
        state = client.get("/threads/a-13/state")
        fallback = client.get("/tools")

        monkeypatch.undo()
        after = run(client, "a-13", sse_events, "once more")

    assert [kind for kind, _data in events] == ["start", "error"]
    assert events[-1][1] == {
        "code": "internal",
        "message": "The run failed because of an internal error.",
    }
    # Nothing ran: the model was not asked, and the thread holds no new message.
    assert asked_after == asked
    assert state.status_code == 200
    assert [m["content"] for m in state.json()["messages"] if m["type"] == "human"] == ["go"]
    # The lease is free again.
    assert state.json()["run_active"] is False
    assert after[-1] == ("end", {"status": "completed"})
    # The catalog answers from the last set this instance had.
    assert fallback.status_code == 200
    assert fallback.json()["tools"] == catalog["tools"]
    assert [a["name"] for a in fallback.json()["agents"]] == ["hello", "helper"]
    assert "9100" not in caplog.text


def test_an_earlier_link_is_closed_once_the_run_that_held_it_ends_and_not_before(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = LabelledChatModel()
    made = make_a2a_app(model, {}, saved={"helper": {"url": SAVED_URL, "read_only": True}})
    record = made.saved["helper"]
    streamed: dict[str, Events] = {}
    with TestClient(made.app) as client:
        (earlier,) = made.runtime.agents
        model.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "a-14", sse_events))
        )
        running.start()
        with model.held():
            # The agent is moved while a run holds the set with its earlier link.
            moved_to = make_stub("helper")
            made.add_stub(MOVED_URL, moved_to)
            client.portal.call(
                lambda: made.saved_agents.change(
                    record.id, record.version, url=MOVED_URL, card=saved_card(moved_to)
                )
            )
            client.get("/tools")
            (later,) = made.runtime.agents
            open_while_held = not earlier._http.is_closed
        running.join(10)
        assert not running.is_alive()
        closed_afterwards = earlier._http.is_closed
        still_open = not later._http.is_closed

    assert later is not earlier
    assert (earlier.ref, later.ref) == (f"{record.id}:1", f"{record.id}:2")
    assert open_while_held
    assert closed_afterwards
    assert still_open
    assert streamed["events"][-1] == ("end", {"status": "completed"})


def test_learning_that_a_saved_agent_is_not_in_use_is_logged_once(
    make_a2a_app: Callable[..., A2aApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}})
    with TestClient(made.app) as client:
        # The store does not know the environment: only a route refuses this name.
        save(client, made, name="hello")
        first = client.get("/tools").json()
        client.get("/tools")
        save(client, made, name="helper", url=OTHER_URL)
        second = client.get("/tools").json()
    lines = [r.getMessage() for r in caplog.records if r.name == REGISTRY]
    assert lines == [
        "Saved agent hello is not in use: An agent named in the environment has this name."
    ]
    # The environment's agent is the one in use, and a later agent is unaffected.
    assert [a["name"] for a in first["agents"]] == ["hello"]
    assert [a["name"] for a in second["agents"]] == ["hello", "helper"]


def test_adopting_a_change_connects_the_link_with_the_card_that_was_just_read(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {})
    stub = make_stub("helper")
    with TestClient(made.app) as client:
        record = save(client, made, stub=stub)
        registry = made.runtime.registry
        assert registry is not None
        adopted = client.portal.call(registry.adopt, record, stub.card)
        (link,) = adopted.links
        connected = link.connected
        status = listed(client, "helper")
    assert connected
    assert status["reachable"] is True
    # The card the caller had just read was used: nothing was requested.
    assert made.transport_at(SAVED_URL).requests == []


def test_a_run_that_hands_nothing_over_contacts_no_saved_agent(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[AIMessage(content="No agent is needed.")])
    made = make_a2a_app(model, {})
    with TestClient(made.app) as client:
        save(client, made, read_only=True)
        events = run(client, "a-15", sse_events)
    assert events[-1] == ("end", {"status": "completed"})
    # Having the tool is not using it: a run that hands nothing over contacts no agent.
    assert made.transport_at(SAVED_URL).requests == []


def test_a_cancel_while_the_context_is_being_taken_gives_the_lease_back(
    make_a2a_app: Callable[..., A2aApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = make_a2a_app(ScriptedChatModel(), {"hello": {}})
    with TestClient(made.app) as client:
        runtime = made.runtime

        async def stopped_while_taking() -> bool:
            assert await runtime.locks.acquire("a-16", lease_owner(runtime, "run-1"))
            reached = asyncio.Event()

            async def never() -> Any:
                reached.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(runtime, "begin_run", never)
            taking = asyncio.create_task(take_context(runtime, "a-16", "run-1"))
            await reached.wait()
            # As when the service stops while a delivery or a request is at this point.
            taking.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await taking
            return await runtime.locks.is_active("a-16")

        still_held = client.portal.call(stopped_while_taking)
    # Nothing holds the thread until the lease would have expired.
    assert still_held is False


# Part 2 (T051): added through the route.

ADDED_URL = "http://127.0.0.1:9321"
ASK = ("handoff_to_helper", {"message": "Summarize the week.", "context": "Ship day is Tuesday."})


def add(
    client: TestClient,
    made: A2aApp,
    name: str = "helper",
    url: str = ADDED_URL,
    *,
    stub: StubAgent | None = None,
) -> dict[str, Any]:
    """Add an agent through the route, with a stand-in at the address; return the answer."""
    made.add_stub(url, stub or make_stub(name))
    response = client.post("/agents", json={"name": name, "url": url})
    assert response.status_code == 201, response.text
    return response.json()


def received(made: A2aApp, url: str = ADDED_URL) -> list[list[tuple[str, str | None]]]:
    """Each message the stand-in received, as its parts' kinds and texts."""
    return [
        [(kind, text) for kind, text, _metadata in message.parts]
        for message in made.stub_at(url).record.messages
    ]


def test_an_added_agent_is_handed_work_in_the_next_run_once_a_person_approves(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[DONE, propose(ASK)])
    made = make_a2a_app(model, {})
    with TestClient(made.app) as client:
        run(client, "b-1", sse_events)
        added = add(client, made)
        catalog = tools(client)
        proposal = interrupted(run(client, "b-1", sse_events, "use the helper"))
        before_the_decision = received(made)
        events = resume(client, "b-1", [{"type": "approve"}], sse_events)

    # No restart, and the agent's two tools are there, gated as its choice says.
    assert len(made.runtimes) == 1
    assert added["approval"] == "required"
    assert {name: catalog[name]["classification"] for name in HELPER_TOOLS} == {
        "handoff_to_helper": "gated",
        "cancel_helper_task": "gated",
    }
    for name in HELPER_TOOLS:
        assert catalog[name]["agent"] == "helper"
        assert "9321" not in str(catalog[name])
    # The hand-off pauses, naming the agent, and nothing was sent before the decision.
    (action,) = proposal["action_requests"]
    assert (action["name"], action["agent"]) == ("handoff_to_helper", "helper")
    assert action["allowed_decisions"] == ["approve", "edit", "reject"]
    assert before_the_decision == []
    # Once approved, the stand-in received exactly the proposed message.
    assert events[-1] == ("end", {"status": "completed"})
    assert received(made) == [[("text", "Summarize the week."), ("text", "Ship day is Tuesday.")]]
    (result,) = tool_results(events, "handoff_to_helper")
    assert result["content"].startswith("Agent 'helper' completed task ")


def test_an_added_agent_is_handed_work_by_the_subagent_once_a_person_approves(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = make_a2a_app(ScriptedChatModel(script=[DELEGATE, propose(ASK, step="d")]), {})
    with TestClient(made.app) as client:
        add(client, made)
        proposal = interrupted(run(client, "b-2", sse_events))
        before_the_decision = received(made)
        events = resume(client, "b-2", [{"type": "approve"}], sse_events)
    (action,) = proposal["action_requests"]
    assert (action["name"], action["agent"]) == ("handoff_to_helper", "helper")
    assert before_the_decision == []
    assert events[-1] == ("end", {"status": "completed"})
    assert received(made) == [[("text", "Summarize the week."), ("text", "Ship day is Tuesday.")]]


def test_an_added_agent_is_handed_work_in_a_background_run_once_a_person_approves(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[propose(ASK, step="b")])
    made = make_a2a_app(model, {"hello": {"read_only": True}})
    # An outcome for the worker to deliver: a task the stand-in will not know.
    seed_task(made, "hello", "task-1", thread="b-3")
    with TestClient(made.app) as client:
        add(client, made)
        # One round: the outcome is ready, and the background run that tells the thread
        # proposes a hand-off to the new agent.
        tick(client, made)
        waiting = pending(client, "b-3")
        before_the_decision = received(made)
        events = resume(client, "b-3", [{"type": "approve"}], sse_events)

    assert len(seen_outcomes(model, 0)) == 1
    (action,) = waiting["action_requests"]
    assert (action["name"], action["agent"]) == ("handoff_to_helper", "helper")
    # A background run is gated like any other: nothing left before the decision.
    assert before_the_decision == []
    assert events[-1] == ("end", {"status": "completed"})
    assert received(made) == [[("text", "Summarize the week."), ("text", "Ship day is Tuesday.")]]


def test_a_run_in_progress_finishes_without_the_added_agent_and_the_next_on_its_thread_has_it(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = LabelledChatModel(
        script=[
            propose(("handoff_to_helper", {"message": "Too early."})),
            DONE,
            propose(ASK, step="n"),
        ]
    )
    made = make_a2a_app(model, {})
    streamed: dict[str, Events] = {}
    with TestClient(made.app) as client:
        model.hold()
        running = threading.Thread(
            target=lambda: streamed.update(events=run(client, "b-4", sse_events))
        )
        running.start()
        with model.held():
            # The run has reached its model and waits there while the agent is added.
            add(client, made)
        running.join(10)
        assert not running.is_alive()
        during = received(made)
        following = run(client, "b-4", sse_events, "now use the helper")

    # The run in progress was built without the agent: it did not fail, and sent nothing.
    (unknown,) = tool_results(streamed["events"], "handoff_to_helper")
    assert "not a valid tool" in unknown["content"]
    assert streamed["events"][-1] == ("end", {"status": "completed"})
    assert during == []
    # The next run on the same thread has the agent, and pauses for it.
    (action,) = interrupted(following)["action_requests"]
    assert (action["name"], action["agent"]) == ("handoff_to_helper", "helper")


def test_the_first_agent_of_a_service_that_had_none_brings_the_pending_tasks_with_it(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    check = AIMessage(
        content="", tool_calls=[{"id": "k1", "name": "check_agent_tasks", "args": {}}]
    )
    model = ScriptedChatModel(script=[DONE, check, DONE, DONE])
    made = make_a2a_app(model, {})
    with TestClient(made.app) as client:
        run(client, "b-5", sse_events)
        before = set(model.bound_tool_names)
        added = add(client, made)
        # A task of the new agent that is still pending on the thread.
        now = client.portal.call(made.store.now)
        ref = f"{added['id']}:1"
        client.portal.call(made.store.record_task, _task("helper", "task-9", ref, now, "b-5"))
        events = run(client, "b-5", sse_events, "what is pending?")
        after = set(model.bound_tool_names)

    assert "check_agent_tasks" not in before
    assert "check_agent_tasks" in after
    # The tool lists the thread's tasks, and every model call is told of the pending ones.
    (listed_tasks,) = tool_results(events, "check_agent_tasks")
    assert "Agent 'helper', task task-9: working" in listed_tasks["content"]
    system = model.seen[-1][0]
    assert "Agent tasks still pending on this thread." in system.text
    assert "- Agent 'helper', task task-9: working" in system.text


def test_an_added_agent_is_the_same_after_a_restart_and_is_handed_work(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    first = make_a2a_app(ScriptedChatModel(), {})
    with TestClient(first.app) as client:
        added = add(client, first)
        before = client.get("/agents").json()

    # The service stops and starts again on the same database.
    runtime = first.runtime
    second = make_a2a_app(
        ScriptedChatModel(script=[propose(ASK)]),
        {},
        instance_id="instance-b",
        saver=runtime.agent.checkpointer,
        store=first.store,
        locks=runtime.locks,
        saved_agents=first.saved_agents,
        at=first.at,
    )
    with TestClient(second.app) as client:
        after = client.get("/agents").json()
        interrupted(run(client, "b-6", sse_events))
        events = resume(client, "b-6", [{"type": "approve"}], sse_events)

    (then,), (now,) = before["agents"], after["agents"]
    # The same agent: its name, its address, its approval choice, and its card details.
    for field in ("id", "name", "url", "approval", "card", "version", "created_at", "source"):
        assert now[field] == then[field] == added[field], field
    assert now["url"] == ADDED_URL
    # In use again from the start: its card was read at startup, and work reaches it.
    assert now["state"] == "reachable"
    assert events[-1] == ("end", {"status": "completed"})
    assert received(second) == [[("text", "Summarize the week."), ("text", "Ship day is Tuesday.")]]


def test_a_run_on_a_second_instance_has_the_agent_once_the_add_was_answered_on_the_first(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    shared: dict[str, Any] = {
        "saver": InMemorySaver(),
        "locks": InMemoryRunLocks(60, clock=clock.now),
    }
    first = make_a2a_app(ScriptedChatModel(), {}, **shared)
    second = make_a2a_app(
        ScriptedChatModel(script=[propose(ASK)]),
        {},
        instance_id="instance-b",
        store=first.store,
        saved_agents=first.saved_agents,
        at=first.at,
        **shared,
    )
    with TestClient(first.app) as one, TestClient(second.app) as two:
        assert two.get("/tools").json()["agents"] == []
        add(one, first)
        # The very next thing the second instance does is a run.
        proposal = interrupted(run(two, "b-7", sse_events))
        in_its_list = two.get("/agents").json()["agents"]
        events = resume(two, "b-7", [{"type": "approve"}], sse_events)

    (action,) = proposal["action_requests"]
    assert (action["name"], action["agent"]) == ("handoff_to_helper", "helper")
    assert [(agent["name"], agent["approval"]) for agent in in_its_list] == [("helper", "required")]
    assert events[-1] == ("end", {"status": "completed"})
    assert received(first) == [[("text", "Summarize the week."), ("text", "Ship day is Tuesday.")]]


# Part 3: the approval choice is changed through the route on one instance, and the decision
# is sent to another.

TO_HELPER = ("handoff_to_helper", {"message": "Summarize the week."})
TO_OTHER = ("handoff_to_other", {"message": "To the one that needed no approval."})
DECISIONS = {
    "approve": {"type": "approve"},
    "edit": {"type": "edit", "args": {"message": "Summarize the month."}},
    "reject": {"type": "reject", "message": "no"},
}


def two_instances(
    make_a2a_app: Callable[..., A2aApp],
    clock: FakeClock,
    step: AIMessage,
    saved: dict[str, dict[str, Any]],
) -> tuple[A2aApp, A2aApp]:
    """Two instances on one database; the second one's model proposes ``step``."""
    shared: dict[str, Any] = {
        "saver": InMemorySaver(),
        "locks": InMemoryRunLocks(60, clock=clock.now),
    }
    first = make_a2a_app(ScriptedChatModel(), {}, saved=saved, **shared)
    second = make_a2a_app(
        ScriptedChatModel(script=[step]),
        {},
        instance_id="instance-b",
        store=first.store,
        saved_agents=first.saved_agents,
        at=first.at,
        **shared,
    )
    return first, second


def switch(client: TestClient, record: SavedAgentRecord, approval: str) -> dict[str, Any]:
    """Change an agent's approval choice through the route, as the console does."""
    response = client.patch(
        f"/agents/{record.id}", json={"version": record.version, "approval": approval}
    )
    assert response.status_code == 200, response.text
    return response.json()["agent"]


@pytest.mark.parametrize("decision", list(DECISIONS))
def test_a_proposal_decided_on_another_instance_after_a_switch_to_running_without_approval(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock, decision: str
) -> None:
    first, second = two_instances(
        make_a2a_app, clock, propose(TO_HELPER), {"helper": {"url": SAVED_URL}}
    )
    events, errors, still = [], [], {}
    with TestClient(first.app) as one, TestClient(second.app) as two:
        proposal = interrupted(run(two, "c-1", sse_events))
        changed = switch(one, first.saved["helper"], "none")
        # The very next thing the second instance hears of the thread is the decision.
        if decision == "approve":
            events = resume(two, "c-1", [DECISIONS[decision]], sse_events)
        else:
            errors = refuse(two, "c-1", [DECISIONS[decision]])
            still = pending(two, "c-1")

    assert (changed["approval"], changed["version"]) == ("none", 2)
    if decision == "approve":
        # Sent as it was proposed.
        assert events[-1] == ("end", {"status": "completed"})
        assert texts(first) == ["Summarize the week."]
    else:
        # Neither a reject nor an edit is taken for a call that no longer waits for one,
        # and the proposal is left in place.
        assert "no longer needs a decision" in errors[0]["msg"]
        assert still["interrupt_id"] == proposal["interrupt_id"]
        assert still["action_requests"][0]["allowed_decisions"] == ["approve"]
        assert sent(first.transport_at(SAVED_URL)) == 0


@pytest.mark.parametrize("decision", list(DECISIONS))
def test_a_step_decided_on_another_instance_after_one_of_its_calls_came_to_require_approval(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock, decision: str
) -> None:
    saved = {"helper": {"url": SAVED_URL}, "other": {"url": OTHER_URL, "read_only": True}}
    first, second = two_instances(make_a2a_app, clock, propose(TO_HELPER, TO_OTHER), saved)
    with TestClient(first.app) as one, TestClient(second.app) as two:
        proposal = interrupted(run(two, "c-2", sse_events))
        # The step waits for a decision about `helper`; its call to `other` needed none.
        switch(one, first.saved["other"], "required")
        events = resume(two, "c-2", [DECISIONS[decision]], sse_events)
        still = pending(two, "c-2")

    assert [action["name"] for action in proposal["action_requests"]] == ["handoff_to_helper"]
    kind, data = events[-1]
    assert (kind, data["code"]) == ("error", "approval_now_required")
    assert "agent 'other'" in data["message"] and "'helper'" not in data["message"]
    # Whatever was decided, nothing was sent to any agent, and the proposal is as it was.
    assert sent(first.transport_at(SAVED_URL)) == 0
    assert sent(first.transport_at(OTHER_URL)) == 0
    assert still["interrupt_id"] == proposal["interrupt_id"]


def test_a_run_in_progress_on_another_instance_sends_nothing_once_the_switch_was_answered(
    make_a2a_app: Callable[..., A2aApp],
    sse_events: Any,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared: dict[str, Any] = {
        "saver": InMemorySaver(),
        "locks": InMemoryRunLocks(60, clock=clock.now),
    }
    saved = {"helper": {"url": SAVED_URL, "read_only": True}}
    first = make_a2a_app(ScriptedChatModel(), {}, saved=saved, **shared)
    model = LabelledChatModel(
        script=[
            handoff("Before the switch.", agent="helper"),
            handoff("After it.", agent="helper", call_id="h2"),
            DONE,
        ]
    )
    second = make_a2a_app(
        model,
        {},
        instance_id="instance-b",
        store=first.store,
        saved_agents=first.saved_agents,
        at=first.at,
        **shared,
    )
    streamed: dict[str, Events] = {}
    finish_send = first.store.finish_send
    held: list[bool] = []

    async def then_hold(*args: Any, **kwargs: Any) -> None:
        await finish_send(*args, **kwargs)
        if not held:
            # The run's first hand-off is sent and recorded: its model's next call waits.
            held.append(True)
            model.hold()

    monkeypatch.setattr(first.store, "finish_send", then_hold)
    with TestClient(first.app) as one, TestClient(second.app) as two:
        running = threading.Thread(
            target=lambda: streamed.update(events=run(two, "c-3", sse_events))
        )
        running.start()
        with model.held():
            before = texts(first)
            switch(one, first.saved["helper"], "required")
        running.join(10)
        assert not running.is_alive()

    assert before == ["Before the switch."]
    events = streamed["events"]
    assert events[-1] == ("end", {"status": "completed"})
    _first, second_result = tool_results(events, "handoff_to_helper")
    assert second_result["content"] == (
        "Agent 'helper' now requires approval, so this message was not sent."
    )
    # By the stand-in's own record, nothing reached it after the switch was answered.
    assert texts(first) == ["Before the switch."]
