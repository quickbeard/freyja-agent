"""An A2A agent's two tools: the hand-off and the cancel (feature 003, research R4, R12).

Each configured agent contributes ``handoff_to_<agent>`` and ``cancel_<agent>_task``. Both carry
the agent's classification, decided by its entry alone: ``read_only`` when the entry says
``read_only: true``, ``gated`` otherwise (FR-011). What the card says about the agent describes
the hand-off tool and never classifies it.

The argument models are pydantic, so an edit decision is validated by the existing model path
and an unknown key is refused; ``task_id`` is pinned, so an answer or a cancel stays bound to its
task (FR-013).

A hand-off sends the orchestrator's message and chosen context in the thread's conversation with
the agent and returns the reply (research R5). Until cancels are implemented, the cancel tool
raises a tool error naming the agent, so it sends nothing.
"""

import json
from typing import Annotated, Any

from a2a.types import Message, Part, Role, Task
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
)
from freyja_agent.delegates.mcp_tools import DelegateTool
from freyja_agent.runtime.agent_tasks import AgentSend, AgentTaskRecord, AgentTaskStore

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


def _not_available(agent: str, what: str) -> Any:
    async def call(**_arguments: Any) -> str:
        raise ToolException(f"Agent '{agent}': {what} are not available yet; nothing was sent.")

    return call


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
        coroutine=_not_available(link.name, "cancels"),
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
        raise ToolException("A hand-off needs the thread it is made on; none was given.")
    return str(thread_id)


def _progress_writer() -> Any:
    try:
        return get_stream_writer()
    except RuntimeError:  # outside a streamed run there is nobody to tell
        return lambda _chunk: None


_UNHANDLED = {
    "input_required": "is waiting for input, which a later phase handles",
    "failed": "failed",
    "rejected": "rejected the task",
    "canceled": "was canceled",
    "auth_required": "asks for authentication, which is out of scope",
}


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
        earlier = await store.get_send(thread_id, tool_call_id)
        if earlier is not None:
            if earlier.result is not None:
                return earlier.result
            raise ToolException(
                f"Agent '{link.name}' may or may not have received this message; Freyja does "
                "not know, and will not send it again."
            )
        context_id = await store.get_context(thread_id, link.name)
        a2a_message = Message(
            message_id=f"freyja-{thread_id}-{tool_call_id}",
            role=Role.ROLE_USER,
            parts=[Part(text=message), *([_context_part(context)] if context else [])],
            context_id=context_id or "",
            task_id=task_id or "",
        )
        await store.start_send(
            AgentSend(
                thread_id=thread_id,
                tool_call_id=tool_call_id,
                agent=link.name,
                kind="answer" if task_id else "handoff",
                message_id=a2a_message.message_id,
                sent_at=await store.now(),
            )
        )
        writer = _progress_writer()

        def on_progress(task: str, state: str, text: str) -> None:
            writer({"agent": link.name, "task_id": task, "state": state, "text": text})

        outcome = await send_message(link, a2a_message, on_progress)
        result, ok = await _record(link, store, thread_id, message, outcome)
        await store.finish_send(
            thread_id,
            tool_call_id,
            result=result,
            task_id=outcome.task.id if outcome.task is not None else None,
        )
        if not ok:
            raise ToolException(result)
        return result

    return call


async def _record(
    link: AgentLink, store: AgentTaskStore, thread_id: str, message: str, outcome: SendOutcome
) -> tuple[str, bool]:
    """Keep what the send produced, and say what the orchestrator receives."""
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
        return (
            f"Agent '{link.name}' could not be reached: {outcome.error or 'unknown error'}",
            False,
        )
    task = outcome.task
    state = outcome.state or "working"
    now = await store.now()
    final = state in ("completed", "failed", "rejected", "canceled", "auth_required")
    text = a2a_result_text(task)
    await store.record_task(
        AgentTaskRecord(
            agent=link.name,
            task_id=task.id,
            thread_id=thread_id,
            context_id=task.context_id,
            message=message,
            state="failed" if state == "auth_required" else state,  # type: ignore[arg-type]
            started_at=now,
            last_checked_at=now,
            ended_at=now if final else None,
            outcome=text,
        )
    )
    if state == "completed":
        return f"Agent '{link.name}' completed task {task.id}:\n\n{text}", True
    if outcome.timed_out:
        return (
            f"Agent '{link.name}' is still working on task {task.id} at the call time limit; "
            "pending tasks arrive in a later phase."
        ), False
    why = _UNHANDLED.get(state, f"ended as {state}")
    detail = f"\n\n{text}" if text else ""
    return f"Agent '{link.name}' task {task.id} {why}.{detail}", False
