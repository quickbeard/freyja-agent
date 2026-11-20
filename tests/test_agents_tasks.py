"""005 User Story 5: a task stays with the agent it was given to.

FR-024 to FR-026, SC-010; research R10, R13. Moving a saved agent, and removing one, gives up
every task that is pending for it, together with the change: each is listed in its thread as
given up at once, a background run tells the thread exactly once, and no cancel is sent. A
task is never checked, answered, or canceled at the agent's later address, nor at another
agent that comes to hold the name, and nothing the agent at an earlier address gave the
service is sent to a later one.

The changes are made through the routes. Every agent is the stand-in, inside the test
process.
"""

import asyncio
import itertools
import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.config import canonical_address, destination
from freyja_agent.delegates import delegation_worker
from freyja_agent.llm.store import InMemoryLlmProfileStore
from freyja_agent.runtime.agent_tasks import (
    GIVEN_UP_MOVED,
    GIVEN_UP_REMOVED,
    AgentTaskRecord,
    given_up_text,
)
from tests.a2a_stub import StubAgent, make_stub
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import TEST_SECRET, A2aApp, saved_card
from tests.fakes import FakeClock, LabelledChatModel, ProfileModels, ScriptedChatModel
from tests.test_a2a_background import outcomes
from tests.test_a2a_handoff import DONE, tool_results
from tests.test_a2a_pending import INTERVAL, LIMIT, MAX_AGE, run, state, tasks, tick
from tests.test_agents_startup import GIVEN_UP, seed_task, sent
from tests.test_llm_no_model import unreadable

Events = list[tuple[str, Any]]
# Ports nothing else in the suite uses.
HERE = "http://127.0.0.1:9401"
THERE = "http://127.0.0.1:9402"
MOVED = "Freyja stopped waiting for task {task} of agent 'helper', which was moved to another address; the agent may still be working on it where it was."
REMOVED = "Freyja stopped waiting for task {task} of agent 'helper', which is no longer configured; the agent may still be working on it."


def numbered(name: str = "helper") -> StubAgent:
    """A stand-in whose tasks are numbered ``task-1``, ``task-2``, ..."""
    numbers = itertools.count(1)
    return make_stub(name, task_ids=lambda: f"task-{next(numbers)}")


def to_helper(message: str, *, call_id: str = "h1", task_id: str | None = None) -> AIMessage:
    args: dict[str, Any] = {"message": message}
    if task_id is not None:
        args["task_id"] = task_id
    return AIMessage(
        content="", tool_calls=[{"id": call_id, "name": "handoff_to_helper", "args": args}]
    )


def cancel(task_id: str, *, call_id: str = "x1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "cancel_helper_task", "args": {"task_id": task_id}}],
    )


def one_agent(
    make_a2a_app: Callable[..., A2aApp],
    model: ScriptedChatModel,
    *,
    pending: int = 0,
    **options: Any,
) -> A2aApp:
    """`helper`, saved to run without approval at the first address, with ``pending`` tasks
    on threads ``k-1``, ``k-2``, ..., and a second stand-in at the second address."""
    made = make_a2a_app(
        model,
        {},
        saved={"helper": {"url": HERE, "read_only": True, "stub": numbered()}},
        a2a_call_timeout_seconds=LIMIT,
        a2a_poll_interval_seconds=INTERVAL,
        **options,
    )
    made.add_stub(THERE, numbered())
    for number in range(1, pending + 1):
        seed_task(
            made, "helper", f"seeded-{number}", ref=made.saved["helper"].ref, thread=f"k-{number}"
        )
    return made


def listed(client: TestClient) -> dict[str, Any]:
    (agent,) = [a for a in client.get("/agents").json()["agents"] if a["name"] == "helper"]
    return agent


def move(client: TestClient, url: str = THERE) -> dict[str, Any]:
    """Move `helper` through the route, as the console does."""
    agent = listed(client)
    response = client.patch(
        f"/agents/{agent['id']}", json={"version": agent["version"], "url": url}
    )
    assert response.status_code == 200, response.text
    return response.json()


def remove(client: TestClient) -> dict[str, Any]:
    agent = listed(client)
    response = client.delete(f"/agents/{agent['id']}", params={"version": agent["version"]})
    assert response.status_code == 200, response.text
    return response.json()


def reports(client: TestClient, thread: str) -> list[str]:
    """What the thread was told about its tasks, in order."""
    return [message["content"] for message in outcomes(state(client, thread)["messages"])]


def task(client: TestClient, made: A2aApp, task_id: str) -> AgentTaskRecord:
    found = client.portal.call(made.store.get_task, "helper", task_id)
    assert found is not None
    return found


@pytest.mark.parametrize("change", ["move", "remove"])
def test_a_move_or_a_removal_gives_up_every_pending_task_and_tells_each_thread_once(
    make_a2a_app: Callable[..., A2aApp], change: str
) -> None:
    made = one_agent(make_a2a_app, ScriptedChatModel(final_text="Told."), pending=3)
    ref = made.saved["helper"].ref
    # A task that ended before the change, and one given up for another reason.
    seed_task(made, "helper", "ended", ref=ref, state="completed", thread="k-1")
    here, there = made.transport_at(HERE), made.transport_at(THERE)
    threads = ["k-1", "k-2", "k-3"]
    with TestClient(made.app) as client:
        before = listed(client)["pending_tasks"]
        requests = list(here.requests)
        answer = move(client) if change == "move" else remove(client)
        # No run has used these threads yet, so what they will list is read from the store.
        at_once = [task(client, made, f"seeded-{number}") for number in (1, 2, 3)]
        for _ in range(3):
            tick(client, made.runtime)
        told = [reports(client, thread) for thread in threads]
        afterwards = [tasks(client, thread) for thread in threads]
        ended = task(client, made, "ended")

    assert before == 3 and answer["given_up"] == 3
    # Given up at once, before any round of the worker, with the report ready and untold.
    for row in at_once:
        assert (row.state, row.outcome_ready, row.delivered_at) == ("given_up", True, None)
    # Each thread was told exactly once, in the words for what happened to the agent.
    text = MOVED if change == "move" else REMOVED
    assert told == [[text.format(task=f"seeded-{number}")] for number in (1, 2, 3)]
    assert afterwards[1:] == [[("seeded-2", "given_up", True)], [("seeded-3", "given_up", True)]]
    # No cancel was sent, the earlier address heard nothing more, and nothing about those
    # tasks reached the new one (SC-010).
    assert made.stub_at(HERE).record.cancels == [] and here.requests == requests
    assert sent(there) == 0 and made.stub_at(THERE).record.cancels == []
    # A task that had ended keeps its state and gets no report.
    assert ended.state == "completed" and ended.outcome is None


def test_the_reports_are_the_texts_the_store_keeps() -> None:
    assert MOVED.format(task="t") == given_up_text(GIVEN_UP_MOVED, "helper", "t")
    assert REMOVED.format(task="t") == given_up_text(GIVEN_UP_REMOVED, "helper", "t")
    # A moved agent's task cannot be reached through Freyja any more: no cancel is offered.
    assert "cancel" not in MOVED.lower()


def test_a_task_that_becomes_pending_between_the_list_and_the_change_is_given_up_and_counted(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = one_agent(make_a2a_app, ScriptedChatModel(), pending=1)
    record = made.saved["helper"]
    with TestClient(made.app) as client:
        shown = listed(client)
        # After the page read the list, a run starts another task.
        now = client.portal.call(made.store.now)
        client.portal.call(
            made.store.record_task,
            AgentTaskRecord(
                agent="helper",
                task_id="late",
                thread_id="k-9",
                context_id="ctx",
                message="Compile the report.",
                state="working",
                started_at=now,
                last_checked_at=now,
                agent_ref=record.ref,
            ),
        )
        response = client.patch(
            f"/agents/{shown['id']}", json={"version": shown["version"], "url": THERE}
        )
        late = task(client, made, "late")
    assert shown["pending_tasks"] == 1
    assert response.json()["given_up"] == 2
    assert late.state == "given_up"


def test_a_task_a_run_in_progress_starts_at_the_earlier_address_is_given_up_at_the_next_round(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = LabelledChatModel(script=[to_helper("hold: report")], final_text="Told.")
    made = one_agent(make_a2a_app, model)
    here, there = made.stub_at(HERE), made.transport_at(THERE)
    streamed: dict[str, Events] = {}
    with TestClient(made.app) as client:
        model.hold()
        running = threading.Thread(
            target=lambda: streamed.update(
                events=run(client, "k-5", "Compile the report with the helper.", sse_events)
            )
        )
        running.start()
        with model.held():
            # The run has reached its model, with the tools of the earlier address.
            moved = move(client)
        running.join(10)
        assert not running.is_alive()
        started = tasks(client, "k-5")
        tick(client, made.runtime)
        given_up = tasks(client, "k-5")
        told = reports(client, "k-5")
        here.release()

    assert moved["given_up"] == 0
    # The run finished at the address it started with: its hand-off began a task there.
    assert streamed["events"][-1] == ("end", {"status": "completed"})
    assert [message.text for message in here.record.messages] == ["hold: report"]
    assert started == [("task-1", "working", False)]
    # The worker's next round gives it up, as moved, and tells the thread.
    assert given_up == [("task-1", "given_up", True)]
    assert told == [MOVED.format(task="task-1")]
    # The agent at the new address was asked nothing about it: no check, and no cancel.
    assert sent(there) == 0 and here.record.cancels == []


@pytest.mark.parametrize("change", ["move", "remove and add again"])
@pytest.mark.parametrize("kind", ["answer", "cancel"])
def test_an_answer_or_a_cancel_for_a_task_of_the_agent_as_it_was_sends_nothing(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, kind: str, change: str
) -> None:
    call = to_helper("Last week.", task_id="seeded-1") if kind == "answer" else cancel("seeded-1")
    made = one_agent(make_a2a_app, ScriptedChatModel(script=[call]), pending=1)
    with TestClient(made.app) as client:
        if change == "move":
            move(client)
            now = made.transport_at(THERE)
        else:
            remove(client)
            added = client.post(
                "/agents", json={"name": "helper", "url": THERE, "approval": "none"}
            )
            assert added.status_code == 201, added.text
            now = made.transport_at(THERE)
        events = run(client, "k-1", "Tell the helper.", sse_events)

    assert events[-1] == ("end", {"status": "completed"})
    tool = "handoff_to_helper" if kind == "answer" else "cancel_helper_task"
    (result,) = tool_results(events, tool)
    # Refused before anything is sent: the task is the earlier agent's (FR-025).
    assert result["content"] == f"Agent 'helper' task seeded-1 {GIVEN_UP}"
    assert sent(now) == 0
    assert made.stub_at(THERE).record.cancels == [] and made.stub_at(HERE).record.cancels == []


def test_a_task_given_up_for_its_age_can_still_be_canceled(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock
) -> None:
    model = ScriptedChatModel(script=[DONE, cancel("seeded-1")], final_text="Told.")
    made = one_agent(make_a2a_app, model, pending=1, a2a_task_max_age_seconds=MAX_AGE)
    here = made.transport_at(HERE)
    with TestClient(made.app) as client:
        clock.advance(MAX_AGE + 1)
        tick(client, made.runtime)
        aged = tasks(client, "k-1")
        before = sent(here)
        events = run(client, "k-1", "Cancel it.", sse_events)

    assert aged == [("seeded-1", "given_up", True)]
    # That task still has its agent: the cancel goes to it.
    (result,) = tool_results(events, "cancel_helper_task")
    assert "nothing was sent" not in result["content"]
    assert sent(here) == before + 1


def test_an_answer_under_way_during_a_move_does_not_write_over_the_row_the_move_gave_up(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(
        script=[
            to_helper("ask: which week?"),
            DONE,
            to_helper("Last week.", call_id="h2", task_id="task-1"),
        ],
        final_text="Told.",
    )
    made = one_agent(make_a2a_app, model)
    record = made.saved["helper"]
    there = made.stub_at(THERE)
    set_context = made.store.set_context
    moved: list[int] = []

    async def then_moved(*args: Any) -> None:
        await set_context(*args)
        if len(moved) < 2:
            moved.append(1)
        if len(moved) == 2 and moved[-1] == 1:
            # The answer was sent and answered. Before its result is recorded, the agent
            # is moved, which gives the task up.
            moved[-1] = 2
            await made.saved_agents.change(
                record.id, record.version, url=THERE, card=saved_card(there)
            )

    with TestClient(made.app) as client:
        asked = run(client, "k-6", "Ask the helper.", sse_events)
        waiting = tasks(client, "k-6")
        monkeypatch.setattr(made.store, "set_context", then_moved)
        moved.append(1)
        answered = run(client, "k-6", "Last week.", sse_events)
        monkeypatch.undo()
        row = task(client, made, "task-1")
        tick(client, made.runtime)
        told = reports(client, "k-6")

    assert asked[-1] == answered[-1] == ("end", {"status": "completed"})
    assert waiting == [("task-1", "input_required", True)]
    assert moved == [1, 2]
    # The answer reached the agent at the earlier address, as it had begun before the move.
    assert [m.text for m in made.stub_at(HERE).record.messages] == [
        "ask: which week?",
        "Last week.",
    ]
    # The row is as the move left it: given up, with the report ready, told once.
    assert row.state == "given_up"
    assert row.outcome == MOVED.format(task="task-1")
    assert told == [MOVED.format(task="task-1")]


def test_a_check_under_way_at_the_earlier_address_does_not_turn_a_given_up_task_into_an_outcome(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(script=[to_helper("hold: report")], final_text="Told.")
    made = one_agent(make_a2a_app, model)
    record = made.saved["helper"]
    here, there = made.stub_at(HERE), made.stub_at(THERE)
    record_outcome = delegation_worker.record_outcome
    moved: list[bool] = []

    async def moved_meanwhile(*args: Any, **kwargs: Any) -> Any:
        if not moved:
            # The check found the task completed. Before that is recorded, the agent is
            # moved, which gives the task up.
            moved.append(True)
            await made.saved_agents.change(
                record.id, record.version, url=THERE, card=saved_card(there)
            )
        return await record_outcome(*args, **kwargs)

    with TestClient(made.app) as client:
        assert run(client, "k-7", "Compile the report.", sse_events)[-1][0] == "end"
        here.release()
        monkeypatch.setattr(delegation_worker, "record_outcome", moved_meanwhile)
        for _ in range(3):
            tick(client, made.runtime)
        row = task(client, made, "task-1")
        told = reports(client, "k-7")

    assert moved == [True]
    assert row.state == "given_up" and row.outcome == MOVED.format(task="task-1")
    # The thread was told once, that the task was given up, and not also that it completed.
    assert told == [MOVED.format(task="task-1")]


def test_a_reply_that_names_a_task_another_agent_of_the_name_has_is_malformed(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    made = one_agent(make_a2a_app, ScriptedChatModel(script=[to_helper("hold: report")]))
    # A task the name had before, under another agent's ref, on another thread, with the
    # id the stand-in gives its first task.
    seed_task(made, "helper", "task-1", ref=f"{'f' * 32}:1", state="completed", thread="k-0")
    with TestClient(made.app) as client:
        events = run(client, "k-8", "Compile the report.", sse_events)
        row = task(client, made, "task-1")
        conversation = client.portal.call(made.store.get_context, "k-8", "helper")
        made.stub_at(HERE).release()

    assert events[-1] == ("end", {"status": "completed"})
    (result,) = tool_results(events, "handoff_to_helper")
    assert result["content"] == (
        "Agent 'helper' replied with something malformed: it named a task that another agent "
        "under this name already has."
    )
    # The row is the other agent's, as it was, and nothing of the reply was kept.
    assert (row.thread_id, row.agent_ref, row.state) == ("k-0", f"{'f' * 32}:1", "completed")
    assert conversation is None


def with_a_cookie(app: Any) -> Any:
    """``app``, setting a cookie with every response, as a server may."""

    async def wrapped(scope: Any, receive: Any, send: Any) -> None:
        async def sending(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [*message.get("headers", []), (b"set-cookie", b"session=earlier; Path=/")]
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, sending)

    return wrapped


def test_a_cookie_the_earlier_address_set_is_not_sent_to_the_new_one(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            to_helper("one"),
            DONE,
            to_helper("two", call_id="h2"),
            DONE,
            to_helper("three", call_id="h3"),
        ]
    )
    made = one_agent(make_a2a_app, model)
    first = made.stub_at(HERE)
    here = StreamingASGITransport(with_a_cookie(first.app))
    made.at[destination(canonical_address(HERE))] = (first, here)
    there = made.transport_at(THERE)
    with TestClient(made.app) as client:
        for thread in ("k-10", "k-11"):
            assert run(client, thread, "Tell the helper.", sse_events)[-1][0] == "end"
        move(client)
        assert run(client, "k-12", "Tell the helper.", sse_events)[-1][0] == "end"

    # The earlier address got its own cookie back, so the client did keep it.
    assert any("session=earlier" in request.headers.get("cookie", "") for request in here.requests)
    # A new link has a new client: nothing the earlier address gave is sent to the new one.
    assert sent(there) >= 1
    assert all("cookie" not in request.headers for request in there.requests)
    assert [m.text for m in made.stub_at(THERE).record.messages] == ["three"]


def test_while_no_model_is_usable_a_given_up_task_is_listed_at_once_and_told_once_one_is(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    environment = ScriptedChatModel(final_text="Told.")
    profiles = InMemoryLlmProfileStore()
    made = one_agent(
        make_a2a_app,
        environment,
        pending=1,
        llm_profiles=profiles,
        model_factory=ProfileModels(),
        secret_key=TEST_SECRET,
    )
    with TestClient(made.app) as client:
        saved = unreadable(profiles)
        client.portal.call(profiles.activate, saved.id)
        assert remove(client)["given_up"] == 1
        for _ in range(2):
            tick(client, made.runtime)
        waiting = task(client, made, "seeded-1")
        calls = environment.calls
        # A usable profile is activated, and the next round tells the thread.
        assert client.post("/llm/profiles/environment/activate").status_code == 200
        for _ in range(2):
            tick(client, made.runtime)
        told = reports(client, "k-1")

    # Given up at once and told to nobody: no background run started without a model.
    assert (waiting.state, waiting.outcome_ready, waiting.delivered_at) == ("given_up", True, None)
    assert calls == 0
    assert told == [REMOVED.format(task="seeded-1")]


def test_a_removed_agent_s_tasks_stay_listed_and_a_conversation_follows_the_name(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            to_helper("one"),
            DONE,
            to_helper("two", call_id="h2"),
            DONE,
            to_helper("three", call_id="h3"),
        ]
    )
    made = one_agent(make_a2a_app, model, pending=1)
    again = numbered()
    with TestClient(made.app) as client:
        assert run(client, "k-1", "Tell the helper.", sse_events)[-1][0] == "end"
        move(client)
        assert run(client, "k-1", "Tell the helper.", sse_events)[-1][0] == "end"
        remove(client)
        kept = tasks(client, "k-1")
        # Another agent is added under the name, at the first address.
        made.add_stub(HERE, again)
        added = client.post("/agents", json={"name": "helper", "url": HERE, "approval": "none"})
        assert added.status_code == 201, added.text
        assert run(client, "k-1", "Tell the helper.", sse_events)[-1][0] == "end"
        still = tasks(client, "k-1")

    # The thread still lists every task the removed agent had on it, the given-up one
    # included, after the removal and after another agent took the name.
    assert ("seeded-1", "given_up") in [(task_id, status) for task_id, status, _ in kept]
    assert [entry[:2] for entry in still[: len(kept)]] == [entry[:2] for entry in kept]
    # One conversation of the thread with the name, across the move and the new add.
    contexts = [
        message.context_id
        for stub in (made.stub_at(THERE), again)
        for message in stub.record.messages
    ]
    assert len(contexts) == 2 and len(set(contexts)) == 1 and contexts[0]


# Found by the independent review of Phase 8.


class Held:
    """Holds a coroutine function once, with the event loop free: before its real call, or
    right after it returned."""

    def __init__(
        self, owner: Any, name: str, monkeypatch: pytest.MonkeyPatch, *, after: bool = False
    ) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        real = getattr(owner, name)

        async def waiting(*args: Any, **kwargs: Any) -> Any:
            result = await real(*args, **kwargs) if after else None
            if not self.reached.is_set():
                self.reached.set()
                await asyncio.get_running_loop().run_in_executor(None, self.release.wait, 10)
            return result if after else await real(*args, **kwargs)

        monkeypatch.setattr(owner, name, waiting)

    def during(self, work: Callable[[], Any], then: Callable[[], Any]) -> None:
        """Run ``work`` in a thread; once it waits, do ``then`` and let it go on."""
        running = threading.Thread(target=work)
        running.start()
        try:
            assert self.reached.wait(10), "never reached"
            then()
        finally:
            self.release.set()
        running.join(20)
        assert not running.is_alive()


def test_a_round_of_watching_under_way_asks_a_removed_agent_nothing_once_the_removal_was_answered(
    make_a2a_app: Callable[..., A2aApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = one_agent(make_a2a_app, ScriptedChatModel(final_text="Told."), pending=2)
    here = made.transport_at(HERE)
    seen: dict[str, Any] = {}
    with TestClient(made.app) as client:
        # The round has claimed the agent's tasks when the removal is answered.
        held = Held(made.store, "claim_due", monkeypatch, after=True)

        def removed() -> None:
            seen["answer"] = remove(client)
            seen["at_the_removal"] = list(here.requests)

        held.during(lambda: tick(client, made.runtime), removed)
        after = here.requests[len(seen["at_the_removal"]) :]

    assert seen["answer"] == {"given_up": 2}
    # Nothing of any kind reached the agent after its removal was answered (SC-004).
    assert after == []


def test_a_given_up_report_is_not_lost_when_another_outcome_of_the_task_is_being_delivered(
    make_a2a_app: Callable[..., A2aApp], monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    made = one_agent(make_a2a_app, ScriptedChatModel(final_text="Told."), pending=1)
    question = "Agent 'helper' needs input for task seeded-1:\n\nWhich week?"
    answer: dict[str, Any] = {}
    with TestClient(made.app) as client:
        # The task asked a question, which waits to be delivered to its thread.
        client.portal.call(
            lambda: made.store.update_task(
                "helper", "seeded-1", state="input_required", outcome=question, outcome_ready=True
            )
        )
        # The delivery has read what to deliver when the agent is removed, which gives the
        # task up with another outcome. The run then marks what it delivered.
        held = Held(delegation_worker, "take_undelivered", monkeypatch, after=True)

        async def deliver() -> None:
            worker = made.runtime.worker
            assert worker is not None
            await worker.start_deliveries()
            await asyncio.gather(*worker._deliveries.values())

        def removed_later() -> None:
            # An outcome is known in its thread by the time it arrived: time has passed.
            clock.advance(1)
            answer.update(remove(client))

        held.during(lambda: client.portal.call(deliver), removed_later)
        monkeypatch.undo()
        for _ in range(3):
            tick(client, made.runtime)
        row = task(client, made, "seeded-1")
        told = reports(client, "k-1")

    assert answer == {"given_up": 1}
    assert row.state == "given_up" and row.delivered_at is not None
    # The thread has the question, which was delivered, and was told once that the task
    # was given up (SC-010).
    assert told == [question, REMOVED.format(task="seeded-1")]


def test_the_list_counts_the_tasks_a_move_or_a_removal_would_give_up(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = one_agent(make_a2a_app, ScriptedChatModel())
    earlier = made.saved["helper"].ref
    with TestClient(made.app) as client:
        move(client)
        # A run in progress started a task at the earlier address after the move, and the
        # worker's next round has not come yet.
        now = client.portal.call(made.store.now)
        client.portal.call(
            made.store.record_task,
            AgentTaskRecord(
                agent="helper",
                task_id="late",
                thread_id="k-9",
                context_id="ctx",
                message="Compile the report.",
                state="working",
                started_at=now,
                last_checked_at=now,
                agent_ref=earlier,
            ),
        )
        shown = listed(client)["pending_tasks"]
        answer = remove(client)
    # What the confirmation shows is what the change gives up: every address the agent had.
    assert (shown, answer["given_up"]) == (1, 1)


def test_a_tool_whose_link_holds_no_card_asks_a_removed_agent_for_none(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedChatModel(script=[to_helper("report")], final_text="Told.")
    made = one_agent(make_a2a_app, model)
    here = made.transport_at(HERE)
    # Not running when the service starts: the link holds no usable card.
    here.down = True
    seen: dict[str, Any] = {}
    events: dict[str, Any] = {}
    with TestClient(made.app) as client:
        here.down = False
        # The run's tool has begun when the agent is removed.
        held = Held(made.store, "get_send", monkeypatch)

        def removed() -> None:
            seen["answer"] = remove(client)
            seen["at_the_removal"] = list(here.requests)

        held.during(
            lambda: events.update(found=run(client, "k-1", "Tell the helper.", sse_events)),
            removed,
        )
        after = here.requests[len(seen["at_the_removal"]) :]

    (result,) = tool_results(events["found"], "handoff_to_helper")
    assert result["content"] == "Agent 'helper' was removed; nothing was sent."
    # Not even its card was asked for.
    assert after == []


@pytest.mark.parametrize("source", ["environment", "saved"])
def test_an_answer_to_a_task_given_up_for_its_age_still_gives_the_task_its_new_state(
    make_a2a_app: Callable[..., A2aApp], sse_events: Any, clock: FakeClock, source: str
) -> None:
    model = ScriptedChatModel(
        script=[
            to_helper("ask: which week?"),
            DONE,
            # The background run that tells the thread the task was given up for its age.
            DONE,
            to_helper("last week", call_id="h2", task_id="task-1"),
        ],
        final_text="Told.",
    )
    if source == "saved":
        made = one_agent(make_a2a_app, model, a2a_task_max_age_seconds=MAX_AGE)
    else:
        # No saved agent at all: what feature 003 did is what happens (FR-031).
        made = make_a2a_app(
            model,
            {"helper": {"read_only": True}},
            stubs={"helper": numbered()},
            a2a_call_timeout_seconds=LIMIT,
            a2a_poll_interval_seconds=INTERVAL,
            a2a_task_max_age_seconds=MAX_AGE,
        )
    with TestClient(made.app) as client:
        run(client, "k-1", "Ask the helper.", sse_events)
        clock.advance(MAX_AGE + 1)
        tick(client, made.runtime)
        aged = tasks(client, "k-1")
        events = run(client, "k-1", "Last week.", sse_events)
        after = tasks(client, "k-1")

    assert aged == [("task-1", "given_up", True)]
    (result,) = tool_results(events, "handoff_to_helper")
    assert result["content"].startswith("Agent 'helper' completed task task-1")
    # The task was given up before the answer began, and its agent is still its agent: the
    # row takes the state the answer led to (003 FR-016).
    assert after == [("task-1", "completed", True)]


def test_the_agent_s_own_address_spelled_another_way_that_is_kept_differently_is_a_move(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    made = one_agent(make_a2a_app, ScriptedChatModel(), pending=2)
    here = made.transport_at(HERE)
    with TestClient(made.app) as client:
        before = len(here.requests)
        answer = move(client, "http://localhost:9401")
        kept = client.portal.call(made.saved_agents.get_agent, answer["agent"]["id"])
        asked = [request.method for request in here.requests[before:]]
    # `localhost` is kept as it was entered, so this is another address of the agent: its
    # card is read there, and the tasks of the address it leaves are given up.
    assert answer["agent"]["url"] == "http://localhost:9401"
    assert (answer["given_up"], kept.address_version, asked) == (2, 2, ["GET"])
