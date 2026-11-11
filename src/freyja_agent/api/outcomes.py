"""Recording the outcomes of pending agent tasks in their thread (feature 003, research R10).

An outcome is recorded as Freyja's own check of the task followed by its result: an
``AIMessage`` with one ``check_agent_tasks`` call per task and one ``ToolMessage`` per task
holding the outcome, so the transcript shows it came from Freyja checking, never from the user,
and the orchestrator receives it as it would have received the hand-off's result (FR-020).

Each message's id names the agent, the task, and when the outcome arrived. A run first looks
for those ids in the thread: an outcome already there was recorded by a run that stopped before
marking it delivered, so it is marked now and not recorded again. The messages reducer replaces
a message whose id exists, so even a repeated write cannot add a second copy.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from freyja_agent.agent.agent_tasks import CHECK_AGENT_TASKS
from freyja_agent.agent.factory import AGENT_NAME
from freyja_agent.delegates.a2a_tools import may_not_have_received
from freyja_agent.runtime.agent_tasks import AgentSend, AgentTaskRecord, AgentTaskStore

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Outcomes that the hand-off would have returned as an error result.
_ERROR_STATES = frozenset({"failed", "rejected", "canceled", "given_up"})


def _arrived(task: AgentTaskRecord) -> int:
    """When the outcome arrived, in microseconds: a task answered after a question can have a
    second outcome, which must not share the first one's ids."""
    return (task.last_checked_at - _EPOCH) // timedelta(microseconds=1)


def outcome_id(task: AgentTaskRecord) -> str:
    return f"a2a-outcome-{task.agent}-{task.task_id}-{_arrived(task)}"


def outcome_call_id(task: AgentTaskRecord) -> str:
    """The tool call id: short and plain, because providers limit both (OpenAI to 40 characters)."""
    digest = hashlib.sha256(outcome_id(task).encode()).hexdigest()[:24]
    return f"a2a_{digest}"


def outcome_messages(tasks: Sequence[AgentTaskRecord]) -> list[BaseMessage]:
    """The messages that record ``tasks``' outcomes, in the order given (data-model.md).

    One call per task, each answered by its own result: a provider takes exactly one result for
    each tool call.
    """
    if not tasks:
        return []
    first = tasks[0]
    call = AIMessage(
        content="",
        name=AGENT_NAME,
        id=f"a2a-outcome-call-{first.agent}-{first.task_id}-{_arrived(first)}",
        tool_calls=[
            {
                "id": outcome_call_id(task),
                "name": CHECK_AGENT_TASKS,
                "args": {"task_ids": [task.task_id]},
                "type": "tool_call",
            }
            for task in tasks
        ],
    )
    results: list[BaseMessage] = [
        ToolMessage(
            content=task.outcome or "",
            name=CHECK_AGENT_TASKS,
            id=outcome_id(task),
            tool_call_id=outcome_call_id(task),
            status="error" if task.state in _ERROR_STATES else "success",
        )
        for task in tasks
    ]
    return [call, *results]


async def take_undelivered(
    store: AgentTaskStore, thread_id: str, snapshot: Any
) -> tuple[list[BaseMessage], list[AgentTaskRecord]]:
    """The messages a run starting now records, and the outcomes they deliver (FR-018, FR-020).

    Every ready outcome already in ``snapshot``'s messages is marked delivered and left out.
    The caller marks the rest delivered once the run's input is in a checkpoint
    (``mark_delivered``); if the run fails before that, they stay ready, and the next run's
    check finds them in the thread if they got there.
    """
    recorded = {message.id for message in (snapshot.values or {}).get("messages", [])}
    waiting: list[AgentTaskRecord] = []
    for task in await store.ready_outcomes(thread_id):
        if outcome_id(task) in recorded:
            await store.mark_delivered(task.agent, task.task_id)
        else:
            waiting.append(task)
    return outcome_messages(waiting), waiting


async def mark_delivered(store: AgentTaskStore, tasks: Sequence[AgentTaskRecord]) -> None:
    for task in tasks:
        await store.mark_delivered(task.agent, task.task_id)


async def answer_in_flight(
    store: AgentTaskStore, thread_id: str, snapshot: Any
) -> tuple[list[BaseMessage], list[AgentSend]]:
    """Results for the thread's messages to agents left in flight by a stop (FR-017, T071).

    A send whose result was never recorded, and whose tool call in the thread has no answer,
    was cut off: the agent may or may not have received it. Without an answer, Deep Agents'
    ``PatchToolCallsMiddleware`` would add its own "did not complete" text, which hides that.
    The run's input carries these results ahead of everything else; the caller records them as
    the sends' results once the input is in a checkpoint (``record_answers``), so nothing is
    sent again. A send made inside a subagent has no call in the thread's own messages and is
    left alone: the subagent's call is answered when its step runs again.
    """
    messages = (snapshot.values or {}).get("messages", [])
    answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
    calls = {
        call["id"]: call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    results: list[BaseMessage] = []
    sends: list[AgentSend] = []
    for send in await store.in_flight_sends(thread_id):
        call = calls.get(send.tool_call_id)
        if call is None or send.tool_call_id in answered:
            continue
        answered.add(send.tool_call_id)
        results.append(
            ToolMessage(
                content=may_not_have_received(send.agent),
                name=call["name"],
                tool_call_id=send.tool_call_id,
                status="error",
            )
        )
        sends.append(send)
    return results, sends


async def record_answers(store: AgentTaskStore, sends: Sequence[AgentSend]) -> None:
    for send in sends:
        await store.finish_send(
            send.thread_id,
            send.step,
            send.tool_call_id,
            result=may_not_have_received(send.agent),
        )
