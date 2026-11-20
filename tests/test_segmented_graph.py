"""Feature 007, research R4: a console run is run in segments and ends where a plain one ends.

``SegmentedGraph`` stands for the orchestrator's graph in every run's context. A thread that
is armed is run one step at a time, each segment ending before the next model call or tool
step; any other thread is passed straight to the real graph. These tests run the real
factory with scripted models, a plain run beside a segmented one, and compare what each left
in its thread and what each streamed (FR-021, FR-027, SC-011).
"""

from collections import Counter
from pathlib import Path
from typing import Any, ClassVar

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from freyja_agent.agent.factory import build_agent
from freyja_agent.runtime.unattended import MissingNodeError, SegmentedGraph
from tests.fakes import FailingChatModel, LoopingChatModel, ScriptedChatModel

MODES = ["updates", "messages", "custom"]
APPROVE = Command(resume={"decisions": [{"type": "approve"}]})


def ls(call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": "ls", "args": {"path": "/"}}])


NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)
THREE = AIMessage(
    content="",
    tool_calls=[
        {"id": f"g{n}", "name": "ls", "args": {"path": path}}
        for n, path in enumerate(["/", "/skills", "/cases"])
    ],
)
DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Notify #ops.", "subagent_type": "general-purpose"},
        }
    ],
)


class Arming:
    """Every thread armed; a stop said yes at the boundaries a test names."""

    def __init__(self, stop_at: int | None = None, armed: bool = True) -> None:
        self.stop_at = stop_at
        self.boundaries = 0
        self.is_armed = armed

    def armed(self, thread_id: str) -> bool:
        return self.is_armed

    async def should_stop(self, thread_id: str) -> bool:
        self.boundaries += 1
        return self.stop_at is not None and self.boundaries >= self.stop_at


def graph(model: ScriptedChatModel, workspace: Path, limit: int = 50) -> Any:
    return build_agent(
        model,
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        workspace_dir=str(workspace),
        max_steps=limit,
    ).agent


def config(limit: int = 50) -> dict[str, Any]:
    return {"configurable": {"thread_id": "t-1"}, "recursion_limit": limit}


async def drive(runner: Any, inputs: list[Any], limit: int = 50) -> list[Any]:
    """What a run streams, as ``run_stream`` reads it: root updates by node, and messages."""
    seen: list[Any] = []
    for value in inputs:
        async for namespace, mode, chunk in runner.astream(
            value, config(limit), stream_mode=MODES, durability="sync", subgraphs=True
        ):
            if mode == "updates" and not namespace:
                if "__interrupt__" in chunk:
                    seen.append(("pause", len(chunk["__interrupt__"])))
                else:
                    seen.extend(("update", node) for node in chunk)
            elif mode == "messages":
                message, _ = chunk
                seen.append(("message", message.type, str(message.content)))
    return seen


async def thread(real: Any) -> tuple[list[tuple[str, str]], tuple[str, ...], int, int]:
    snapshot = await real.aget_state(config())
    messages = [(m.type, str(m.content)) for m in snapshot.values.get("messages", [])]
    return messages, snapshot.next, snapshot.metadata["step"], len(snapshot.interrupts)


async def side_by_side(
    workspace: Path, script: list[AIMessage], inputs: list[Any]
) -> tuple[Any, Any]:
    plain = graph(ScriptedChatModel(script=list(script), final_text="All done."), workspace)
    plain_events = await drive(plain, inputs)
    real = graph(ScriptedChatModel(script=list(script), final_text="All done."), workspace)
    segmented = SegmentedGraph(real, Arming())
    segmented_events = await drive(segmented, inputs)
    return (plain_events, await thread(plain)), (segmented_events, await thread(real))


async def test_single_tool_calls_leave_the_same_thread_and_the_same_events(
    workspace: Path,
) -> None:
    plain, segmented = await side_by_side(
        workspace, [ls("l1"), ls("l2")], [{"messages": [("user", "List twice.")]}]
    )
    assert segmented == plain
    messages, next_nodes, _, _ = segmented[1]
    assert [kind for kind, _ in messages] == ["human", "ai", "tool", "ai", "tool", "ai"]
    assert next_nodes == ()


async def test_three_calls_in_one_reply_leave_the_same_thread(workspace: Path) -> None:
    plain, segmented = await side_by_side(
        workspace, [THREE], [{"messages": [("user", "List three places.")]}]
    )
    # The order of results of calls that ran at the same time differs between any two runs.
    assert Counter(segmented[0]) == Counter(plain[0])
    assert Counter(segmented[1][0]) == Counter(plain[1][0])
    assert segmented[1][1:] == plain[1][1:]


async def test_a_pause_and_its_approval_leave_the_same_thread(workspace: Path) -> None:
    paused_plain, paused_segmented = await side_by_side(
        workspace, [NOTIFY], [{"messages": [("user", "Tell #ops.")]}]
    )
    assert paused_segmented == paused_plain
    assert paused_segmented[1][3] == 1
    assert ("pause", 1) in paused_segmented[0]

    plain, segmented = await side_by_side(
        workspace, [NOTIFY], [{"messages": [("user", "Tell #ops.")]}, APPROVE]
    )
    assert segmented == plain
    assert [kind for kind, _ in segmented[1][0]] == ["human", "ai", "tool", "ai"]
    assert segmented[1][1] == ()


async def test_a_proposal_raised_inside_the_subagent_is_the_same(workspace: Path) -> None:
    plain, segmented = await side_by_side(
        workspace, [DELEGATE, NOTIFY], [{"messages": [("user", "Have it done.")]}]
    )
    assert segmented == plain
    assert segmented[1][3] == 1


async def test_a_run_is_stopped_only_between_two_steps(workspace: Path) -> None:
    # Stopped at the boundary after the first tool step: before the next model call.
    real = graph(ScriptedChatModel(script=[ls("l1"), ls("l2")]), workspace)
    arming = Arming(stop_at=3)
    await drive(SegmentedGraph(real, arming), [{"messages": [("user", "List.")]}])
    messages, next_nodes, _, pending = await thread(real)
    assert [kind for kind, _ in messages] == ["human", "ai", "tool"]
    assert (next_nodes, pending) == (("model",), 0)

    # Stopped at the boundary after the first model call: its tool did not run.
    real = graph(ScriptedChatModel(script=[ls("l1"), ls("l2")]), workspace)
    await drive(SegmentedGraph(real, Arming(stop_at=2)), [{"messages": [("user", "List.")]}])
    messages, next_nodes, _, pending = await thread(real)
    assert [kind for kind, _ in messages] == ["human", "ai"]
    assert (next_nodes, pending) == (("tools",), 0)


async def test_an_approved_action_that_had_not_started_is_not_started(workspace: Path) -> None:
    real = graph(ScriptedChatModel(script=[NOTIFY]), workspace)
    arming = Arming()
    segmented = SegmentedGraph(real, arming)
    await drive(segmented, [{"messages": [("user", "Tell #ops.")]}])
    # The pause ended the run; no boundary was asked about after it.
    asked = arming.boundaries
    arming.stop_at = asked + 1
    await drive(segmented, [APPROVE])
    messages, next_nodes, _, pending = await thread(real)
    # The decision is spent and the action never ran.
    assert [kind for kind, _ in messages] == ["human", "ai"]
    assert (next_nodes, pending) == (("tools",), 0)


async def test_a_thread_that_is_not_armed_is_passed_straight_through() -> None:
    class Graph:
        nodes: ClassVar[dict[str, Any]] = {"model": None, "tools": None}

        def __init__(self) -> None:
            self.calls: list[tuple[Any, Any, dict[str, Any]]] = []

        async def astream(self, value: Any, config: Any, **kwargs: Any) -> Any:
            self.calls.append((value, config, kwargs))
            yield "one"
            yield "two"

        def aget_state(self, config: Any) -> str:
            return "the real graph's"

    real = Graph()
    segmented = SegmentedGraph(real, Arming(armed=False))
    chunks = [chunk async for chunk in segmented.astream("in", config(), stream_mode=MODES)]
    assert chunks == ["one", "two"]
    assert real.calls == [("in", config(), {"stream_mode": MODES})]
    # Everything else is the real graph's.
    assert segmented.aget_state(None) == "the real graph's"
    # A call with no thread is passed through too.
    assert [chunk async for chunk in segmented.astream("in", None)] == ["one", "two"]


async def test_an_error_the_graph_raises_comes_out_unchanged(workspace: Path) -> None:
    real = graph(FailingChatModel(fail_on_call=1, error="provider down"), workspace)
    with pytest.raises(RuntimeError, match="provider down"):
        await drive(SegmentedGraph(real, Arming()), [{"messages": [("user", "Hi.")]}])


async def test_the_step_limit_is_never_exceeded(workspace: Path) -> None:
    plain = graph(LoopingChatModel(), workspace, limit=9)
    with pytest.raises(GraphRecursionError):
        await drive(plain, [{"messages": [("user", "Loop.")]}], limit=9)
    real = graph(LoopingChatModel(), workspace, limit=9)
    with pytest.raises(GraphRecursionError):
        await drive(SegmentedGraph(real, Arming()), [{"messages": [("user", "Loop.")]}], limit=9)

    plain_steps = (await thread(plain))[2]
    segmented_steps = (await thread(real))[2]
    assert plain_steps - 2 <= segmented_steps <= plain_steps


@pytest.mark.parametrize("missing", ["model", "tools"])
def test_a_graph_without_a_node_to_stop_at_is_refused(missing: str) -> None:
    class Graph:
        nodes: ClassVar[dict[str, Any]] = {n: None for n in ("model", "tools") if n != missing}

    with pytest.raises(MissingNodeError, match=repr(missing)):
        SegmentedGraph(Graph(), Arming())
