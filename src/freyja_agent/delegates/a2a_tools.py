"""An A2A agent's two tools: the hand-off and the cancel (feature 003, research R4, R12).

Each configured agent contributes ``handoff_to_<agent>`` and ``cancel_<agent>_task``. Both carry
the agent's classification, decided by its entry alone: ``read_only`` when the entry says
``read_only: true``, ``gated`` otherwise (FR-011). What the card says about the agent describes
the hand-off tool and never classifies it.

The argument models are pydantic, so an edit decision is validated by the existing model path
and an unknown key is refused; ``task_id`` is pinned, so an answer or a cancel stays bound to its
task (FR-013).

A hand-off sends the orchestrator's message and chosen context in the thread's conversation with
the agent and returns the reply (research R5). A cancel asks the agent to cancel one of this
thread's pending or given-up tasks (FR-022). Both go through the send ledger, so a step that
runs again never sends twice.
"""

import hashlib
import json
import logging
from typing import Annotated, Any

from a2a.client.errors import A2AClientError, A2AClientTimeoutError
from a2a.types import Message, Part, Role, Task
from a2a.utils.errors import A2AError, TaskNotCancelableError, TaskNotFoundError
from google.protobuf.json_format import MessageToDict
from langchain_core.tools import InjectedToolCallId, StructuredTool, ToolException
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.config import get_config, get_stream_writer
from pydantic import BaseModel, ConfigDict, Field

from freyja_agent.agent.catalog import Classification
from freyja_agent.delegates.a2a_agents import (
    AgentLink,
    SendOutcome,
    cancel_tool_name,
    handoff_tool_name,
    send_message,
    state_name,
)
from freyja_agent.delegates.mcp_tools import DelegateTool
from freyja_agent.runtime.agent_tasks import (
    PENDING_STATES,
    AgentSend,
    AgentTaskRecord,
    AgentTaskStore,
)

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 32000
DESCRIPTION_LIMIT = 2000
PINNED = ("task_id",)


class HandOffArgs(BaseModel):
    """The arguments of a hand-off tool (data-model.md "Agent tools")."""

    model_config = ConfigDict(extra="forbid")

    message: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MESSAGE_LIMIT,
            description="A self-contained task message for the agent, in your own words.",
        ),
    ]
    context: Annotated[
        str | None,
        Field(
            max_length=MESSAGE_LIMIT,
            description="Only the context from this conversation the agent needs, if any.",
        ),
    ] = None
    task_id: Annotated[
        str | None,
        Field(description="To answer the agent's question, the id of the task that asked it."),
    ] = None


class CancelArgs(BaseModel):
    """The argument of a cancel tool."""

    model_config = ConfigDict(extra="forbid")

    task_id: Annotated[str, Field(description="The id of the agent's task to cancel.")]


def handoff_description(link: AgentLink) -> str:
    """The agent's name, its card's name and description, and one line per skill."""
    card = link.card
    if card is None:
        return f"Hand a task to the agent '{link.name}'."
    lines = [f"Hand a task to the agent '{link.name}' ({card.name}): {card.description}".rstrip()]
    if card.skills:
        lines.append("Skills:")
        lines += [f"- {skill.name}: {skill.description}".rstrip(": ") for skill in card.skills]
    return "\n".join(lines)[:DESCRIPTION_LIMIT]


def _delegate(
    tool: StructuredTool, link: AgentLink, classification: Classification
) -> DelegateTool:
    parameters = convert_to_openai_tool(tool)["function"].get("parameters") or {
        "type": "object",
        "properties": {},
    }
    return DelegateTool(
        name=tool.name,
        server=None,
        description=tool.description,
        input_schema=parameters,
        server_hints=None,
        classification=classification,
        tool=tool,
        agent=link.name,
        pinned_args=PINNED,
    )


def agent_tools(link: AgentLink, store: AgentTaskStore) -> tuple[DelegateTool, DelegateTool]:
    """The hand-off and cancel tools of one agent, classified by its entry (FR-011)."""
    classification = Classification.READ_ONLY if link.read_only else Classification.GATED
    handoff = StructuredTool(
        name=handoff_tool_name(link.name),
        description=handoff_description(link),
        args_schema=HandOffArgs,
        coroutine=_handoff_coroutine(link, store),
        handle_tool_error=True,
    )
    cancel = StructuredTool(
        name=cancel_tool_name(link.name),
        description=f"Ask the agent '{link.name}' to cancel one of its tasks.",
        args_schema=CancelArgs,
        coroutine=_cancel_coroutine(link, store),
        handle_tool_error=True,
    )
    return _delegate(handoff, link, classification), _delegate(cancel, link, classification)


def _jsonable(value: Any) -> Any:
    """A protobuf value as JSON, with whole numbers back as integers (protobuf keeps doubles)."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def part_text(part: Any) -> str:
    """One part as text: its text, compact JSON for data, or a note naming what was left out."""
    kind = part.WhichOneof("content")
    if kind == "text":
        return part.text
    if kind == "data":
        data = _jsonable(MessageToDict(part.data))
        # A protobuf Struct keeps no key order, so the keys are sorted for a stable result.
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    media_type = part.media_type or "unknown type"
    if kind == "raw":
        return f"[file omitted: {media_type}]"
    if kind == "url":
        return f"[file link omitted: {media_type}]"
    return f"[{kind or 'unknown'} content omitted]"


def a2a_result_text(source: Task | Message | None) -> str:
    """A reply as the one string the orchestrator receives (FR-006, research R6).

    A task gives its artifacts in order, then its final status message unless that repeats the
    last artifact's text; a direct message gives its parts. Bytes and links never reach the
    model or the thread: each becomes a note naming its kind.
    """
    if source is None:
        return ""
    if isinstance(source, Message):
        parts = [part_text(part) for part in source.parts]
    else:
        parts = [part_text(part) for artifact in source.artifacts for part in artifact.parts]
        if source.status.HasField("message"):
            status = "\n".join(part_text(part) for part in source.status.message.parts)
            if status and (not parts or status != parts[-1]):
                parts.append(status)
    return "\n\n".join(part for part in parts if part)


def _context_part(context: str) -> Part:
    part = Part(text=context)
    part.metadata.update({"freyja": "context"})
    return part


def _thread_id() -> str:
    thread_id = get_config().get("configurable", {}).get("thread_id")
    if not thread_id:
        raise ToolException(
            "A message to an agent needs the thread it is sent on; none was given, so nothing "
            "was sent."
        )
    return str(thread_id)


def _step() -> str:
    """The graph namespace this tool call runs in, which tells it apart on the thread.

    LangGraph runs each tool call as its own task, whose namespace names the node and the task
    id. The task id is derived from the checkpoint and the call's place in the step, so a call
    whose step runs again gets the same namespace, and every other call, in another step, in the
    same reply, or in a subagent, gets another. The provider's tool call id does not have that
    guarantee, so it is not enough on its own to know a call again.
    """
    return str(get_config().get("configurable", {}).get("checkpoint_ns") or "")


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _progress_writer() -> Any:
    try:
        return get_stream_writer()
    except RuntimeError:  # outside a streamed run there is nobody to tell
        return lambda _chunk: None


_UNHANDLED = {
    "failed": "failed",
    "rejected": "rejected the task",
    "canceled": "was canceled",
    "auth_required": "asks for authentication, which is out of scope",
}


async def _sent_before(
    link: AgentLink,
    store: AgentTaskStore,
    thread_id: str,
    step: str,
    tool_call_id: str,
    request: str,
) -> str | None:
    """The recorded result when this call's step already sent its message; None if it did not.

    Raises instead when the ledger holds another message under this call's key, or when the
    message left and its result was never recorded: nothing is sent again (FR-010, FR-027).
    """
    earlier = await store.get_send(thread_id, step, tool_call_id)
    if earlier is None:
        return None
    if earlier.request != request:
        # The same key for another message: not a step running again. Returning the earlier
        # result would answer this call with another one's (FR-010).
        raise ToolException(
            f"Agent '{link.name}': Freyja has already recorded a different message "
            "under this call's id, so it sent nothing."
        )
    if earlier.result is not None:
        return earlier.result
    raise ToolException(may_not_have_received(link.name))


def may_not_have_received(agent: str) -> str:
    """The result of a message whose sending started and was never confirmed (FR-017)."""
    return (
        f"Agent '{agent}' may or may not have received this message; Freyja does not know, and "
        "will not send it again."
    )


def _handoff_coroutine(link: AgentLink, store: AgentTaskStore) -> Any:
    async def call(
        message: str,
        context: str | None = None,
        task_id: str | None = None,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """One hand-off, answer, or follow-up (research R5)."""
        thread_id = _thread_id()
        step = _step()
        kind = "answer" if task_id else "handoff"
        request = _digest(link.name, kind, message, context, task_id)
        earlier = await _sent_before(link, store, thread_id, step, tool_call_id, request)
        if earlier is not None:
            return earlier
        if task_id:
            known = await store.get_task(link.name, task_id)
            if known is not None and known.thread_id != thread_id:
                # A task belongs to the thread that started it; conversations are never shared
                # between threads (FR-008), so an answer cannot reach another thread's task.
                raise ToolException(
                    f"Agent '{link.name}' has no task {task_id} on this thread; nothing was sent."
                )
        context_id = await store.get_context(thread_id, link.name)
        a2a_message = Message(
            message_id=f"freyja-{thread_id}-{tool_call_id}-{_digest(step)[:8]}",
            role=Role.ROLE_USER,
            parts=[Part(text=message), *([_context_part(context)] if context else [])],
            context_id=context_id or "",
            task_id=task_id or "",
        )
        await store.start_send(
            AgentSend(
                thread_id=thread_id,
                step=step,
                tool_call_id=tool_call_id,
                agent=link.name,
                kind=kind,
                message_id=a2a_message.message_id,
                request=request,
                sent_at=await store.now(),
            )
        )
        writer = _progress_writer()

        def on_progress(task: str, state: str, text: str) -> None:
            writer({"agent": link.name, "task_id": task, "state": state, "text": text})

        outcome = await send_message(link, a2a_message, on_progress, clock=store.now)
        result, ok = await _record(link, store, thread_id, message, outcome)
        await store.finish_send(
            thread_id,
            step,
            tool_call_id,
            result=result,
            task_id=outcome.task.id if outcome.task is not None else None,
        )
        if not ok:
            raise _failed(link, result)
        return result

    return call


def outcome_text(agent: str, task_id: str, state: str, text: str) -> tuple[str, bool]:
    """What the orchestrator receives for a task in ``state``, and whether that is a success.

    The same words whether the hand-off saw the state itself or the worker found it later and a
    run records it as the outcome of a pending task (FR-020: as the hand-off would have).
    """
    if state == "completed":
        return f"Agent '{agent}' completed task {task_id}:\n\n{text}", True
    if state == "input_required":
        return (
            f"Agent '{agent}' needs input for task {task_id}:\n\n{text}\n\n"
            "Ask the person this in your own words. To continue this task once they answer, "
            f"hand off to '{agent}' again with task_id \"{task_id}\" and their answer."
        ), True
    why = _UNHANDLED.get(state, f"ended as {state}")
    detail = f"\n\n{text}" if text else ""
    return f"Agent '{agent}' task {task_id} {why}.{detail}", False


def pending_text(agent: str, task_id: str) -> str:
    return (
        f"Agent '{agent}' is still working on task {task_id}; it is pending, and Freyja will "
        "report its outcome in this thread."
    )


async def _record(
    link: AgentLink, store: AgentTaskStore, thread_id: str, message: str, outcome: SendOutcome
) -> tuple[str, bool]:
    """Keep what the send produced, and say what the orchestrator receives."""
    if outcome.malformed is not None:
        # Nothing from the reply is kept: an unusable id could not be watched or answered.
        return f"Agent '{link.name}' replied with something malformed: {outcome.malformed}.", False
    if outcome.context_id is not None:
        await store.set_context(thread_id, link.name, outcome.context_id)
    if outcome.reply is not None:
        return f"Agent '{link.name}' replied:\n\n{a2a_result_text(outcome.reply)}", True
    if outcome.task is None:
        if outcome.timed_out:
            return (
                f"Agent '{link.name}' did not acknowledge the task within "
                f"{link.call_timeout:g} seconds; it may or may not have received it."
            ), False
        if outcome.unreachable:
            return f"Agent '{link.name}' could not be reached: {outcome.error}", False
        return (
            f"Agent '{link.name}' answered with an error: {outcome.error or 'unknown error'}",
            False,
        )
    task = outcome.task
    state = outcome.state or "working"
    if outcome.timed_out or state == "working":
        # Acknowledged and still working at the limit: pending, watched by the worker, and
        # reported in this thread once it changes (FR-017, FR-019).
        result, ok = pending_text(link.name, task.id), True
    else:
        result, ok = outcome_text(link.name, task.id, state, a2a_result_text(task))
    now = await store.now()
    final = state in ("completed", "failed", "rejected", "canceled", "auth_required")
    stored = "failed" if state == "auth_required" else state
    # A result the hand-off returns is delivered with it, so nothing waits to be delivered; a
    # pending task gets its outcome from the worker (data-model.md "Agent task").
    known = {
        "state": stored,
        "last_checked_at": now,
        "ended_at": now if final else None,
        "outcome": None if stored == "working" else result,
        "outcome_ready": False,
        "delivered_at": None,
    }
    if await store.get_task(link.name, task.id) is None:
        await store.record_task(
            AgentTaskRecord(
                agent=link.name,
                task_id=task.id,
                thread_id=thread_id,
                context_id=task.context_id,
                message=message,
                # When the agent acknowledged it, on the store's clock, so its age counts from
                # then on every instance (FR-017, FR-021).
                started_at=outcome.acknowledged_at or now,
                **known,
            )
        )
    else:
        # An answer continues a task this thread started: the row keeps the message the task
        # was started with and its start, and takes the state the answer led to (FR-016). An
        # outcome delivered before the answer is history: the next one is delivered anew.
        await store.update_task(link.name, task.id, **known)
    return result, ok


# What a task was in before it could be canceled: pending, or given up by Freyja (FR-022).
CANCELABLE = (*PENDING_STATES, "given_up")


async def record_outcome(
    store: AgentTaskStore,
    task: AgentTaskRecord,
    state: str,
    text: str,
    *,
    ready: bool,
) -> tuple[str, bool]:
    """Record that ``task`` moved to ``state`` at the agent, unless it already moved on.

    ``ready`` marks the outcome to be delivered by a run; a result a tool returns is delivered
    with it, so it is not. The update applies only while the task is still pending or given
    up, so an outcome that arrives for a task Freyja canceled is ignored (FR-022).
    """
    outcome, ok = outcome_text(task.agent, task.task_id, state, text)
    stored = "failed" if state == "auth_required" else state
    now = await store.now()
    await store.update_task(
        task.agent,
        task.task_id,
        when_state=CANCELABLE,
        state=stored,
        last_checked_at=now,
        ended_at=None if stored == "input_required" else now,
        outcome=outcome,
        outcome_ready=ready,
        delivered_at=None,
    )
    return outcome, ok


def _cancel_coroutine(link: AgentLink, store: AgentTaskStore) -> Any:
    async def call(task_id: str, *, tool_call_id: Annotated[str, InjectedToolCallId]) -> str:
        """One cancel of one of this thread's tasks (FR-022)."""
        thread_id = _thread_id()
        step = _step()
        request = _digest(link.name, "cancel", task_id)
        earlier = await _sent_before(link, store, thread_id, step, tool_call_id, request)
        if earlier is not None:
            return earlier
        task = await store.get_task(link.name, task_id)
        if task is None or task.thread_id != thread_id:
            # Only a task this thread started can be canceled from it (FR-008).
            raise ToolException(
                f"Agent '{link.name}' has no task {task_id} on this thread; nothing was sent."
            )
        if task.state not in CANCELABLE:
            return f"Agent '{link.name}' task {task_id} already ended as {task.state}; nothing was sent."
        await store.start_send(
            AgentSend(
                thread_id=thread_id,
                step=step,
                tool_call_id=tool_call_id,
                agent=link.name,
                kind="cancel",
                message_id=f"freyja-cancel-{thread_id}-{tool_call_id}-{_digest(step)[:8]}",
                request=request,
                sent_at=await store.now(),
                task_id=task_id,
            )
        )
        result, ok = await _cancel(link, store, task)
        await store.finish_send(thread_id, step, tool_call_id, result=result, task_id=task_id)
        if not ok:
            raise _failed(link, result)
        return result

    return call


async def _cancel(
    link: AgentLink, store: AgentTaskStore, task: AgentTaskRecord
) -> tuple[str, bool]:
    """Send the cancel and record what the agent answered; never repeated (FR-022)."""
    name, task_id = link.name, task.task_id
    try:
        answer = await link.cancel(task_id)
    except TaskNotCancelableError:
        # The task ended at the agent first: report its outcome with this result.
        link.mark(True)
        return await _already_ended(link, store, task)
    except TaskNotFoundError:
        link.mark(True)
        outcome, _ok = await record_outcome(
            store, task, "failed", "The agent no longer knows this task.", ready=False
        )
        return (
            f"Agent '{name}' no longer knows task {task_id}; nothing was canceled.\n\n{outcome}",
            False,
        )
    except (TimeoutError, A2AClientTimeoutError):
        link.mark(False, f"it did not answer within {link.call_timeout:g} seconds")
        return (
            f"Agent '{name}' did not answer the cancel for task {task_id} within "
            f"{link.call_timeout:g} seconds; it may or may not have canceled it. The task stays "
            "pending."
        ), False
    except A2AClientError as exc:  # the request never got an answer from the agent
        error = link.describe(str(exc) or type(exc).__name__)
        link.mark(False, error)
        return f"Agent '{name}' could not be reached: {error}. The task stays pending.", False
    except A2AError as exc:  # the agent answered with a refusal
        link.mark(True)
        why = link.describe(str(exc) or type(exc).__name__)
        return (
            f"Agent '{name}' did not cancel task {task_id}: {why}. The task stays pending.",
            False,
        )
    except Exception as exc:  # unreachable or a protocol error; the task stays pending
        error = link.describe(str(exc) or type(exc).__name__)
        link.mark(False, error)
        return f"Agent '{name}' could not be reached: {error}. The task stays pending.", False
    link.mark(True)
    state = state_name(answer.status.state)
    if state == "canceled":
        now = await store.now()
        result = f"Agent '{name}' canceled task {task_id}."
        # Nothing to deliver: the orchestrator has this result (data-model.md "Agent task").
        await store.update_task(
            name,
            task_id,
            when_state=CANCELABLE,
            state="canceled",
            last_checked_at=now,
            ended_at=now,
            outcome=result,
            outcome_ready=False,
            delivered_at=None,
        )
        return result, True
    if state in ("completed", "failed", "rejected", "auth_required"):
        return await _ended(link, store, task, state, a2a_result_text(answer))
    return (
        f"Agent '{name}' accepted the cancel for task {task_id}, and the task is still {state}; "
        "Freyja keeps watching it."
    ), True


async def _already_ended(
    link: AgentLink, store: AgentTaskStore, task: AgentTaskRecord
) -> tuple[str, bool]:
    try:
        found = await link.get_task(task.task_id)
    except Exception as exc:  # the outcome is then left to the watcher
        link.mark(False, str(exc) or type(exc).__name__)
        return (
            f"Agent '{link.name}' task {task.task_id} had already ended, so it was not canceled."
        ), True
    state = state_name(found.status.state)
    if state in PENDING_STATES:  # not ended after all: the watcher reports it when it is
        return (
            f"Agent '{link.name}' did not cancel task {task.task_id}; it is still {state}, and "
            "Freyja keeps watching it."
        ), True
    return await _ended(link, store, task, state, a2a_result_text(found))


async def _ended(
    link: AgentLink, store: AgentTaskStore, task: AgentTaskRecord, state: str, text: str
) -> tuple[str, bool]:
    outcome, _ok = await record_outcome(store, task, state, text, ready=False)
    return (
        f"Agent '{link.name}' task {task.task_id} had already ended, so it was not canceled."
        f"\n\n{outcome}"
    ), True


def _failed(link: AgentLink, result: str) -> ToolException:
    """The error result of a message the agent did not carry out, logged naming the agent.

    The text goes through ``describe`` for the log, so the agent's address is never written
    (FR-009, research R14); the result itself was built from described text already.
    """
    logger.warning("Agent %s: %s", link.name, link.describe(result))
    return ToolException(result)
