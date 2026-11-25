"""An A2A agent's two tools: the hand-off and the cancel (feature 003, research R4, R12).

Each agent in use contributes ``handoff_to_<agent>`` and ``cancel_<agent>_task``. Both carry
the agent's classification, decided by its approval choice alone: the environment's entry
for an agent named there (``read_only: true`` or not), the saved choice for a saved agent,
which the agent set carries (FR-011, 005 FR-022). What the card says about the agent
describes the hand-off tool and never classifies it, and neither does whether it answers.

Before a tool sends anything it checks, in this order (005 research R7): the send ledger, so
a step that runs again returns what it recorded; for an answer or a cancel, that the task
belongs to the agent this tool was built for; that the link holds a usable card, reading
it when it does not; and that the call was proposed for the agent this tool was built for,
which the row written at the model's reply says (005 FR-023, research R8). A saved agent's
tool then reads its agent from the store, by id, with nothing left to await before the send's
first write: an agent that was removed is sent nothing, and neither is one that requires
approval now when no person decided the call (005 FR-022). So a removal and a switch to
requiring approval hold at once, on every instance, for runs in progress and for a step
that runs again. A message kept back by one of these is an error result that names the
agent, the run continues, and nothing is sent.

The argument models are pydantic, so an edit decision is validated by the existing model path
and an unknown key is refused; ``task_id`` is pinned, so an answer or a cancel stays bound to its
task (FR-013).

A hand-off sends the orchestrator's message and chosen context in the thread's conversation with
the agent and returns the reply (research R5). A cancel asks the agent to cancel one of this
thread's pending or given-up tasks (FR-022). Both go through the send ledger, so a step that
runs again never sends twice.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from freyja_agent.config import destination
from freyja_agent.delegates.a2a_agents import (
    AgentLink,
    SendOutcome,
    cancel_tool_name,
    handoff_tool_name,
    send_message,
    state_name,
)
from freyja_agent.delegates.agent_cards import SENTENCES, CardError
from freyja_agent.delegates.mcp_tools import DelegateTool
from freyja_agent.runtime.agent_tasks import (
    PENDING_STATES,
    AgentSend,
    AgentTaskRecord,
    AgentTaskStore,
    ProposedCall,
    call_namespace,
)
from freyja_agent.runtime.saved_agents import SavedAgentStore

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
        agent_id=link.agent_id,
    )


@dataclass(frozen=True)
class _Send:
    """What one agent's two tools send with: fixed when the tools are built (research R7)."""

    link: AgentLink
    store: AgentTaskStore
    # Whether a call of these tools waits for a person in the run they were built for. Kept
    # here and not read from the link, which a later set of agents may share.
    gated: bool
    # Where a saved agent is read before a send; None for an agent named in the environment,
    # which cannot change while the service runs.
    saved: SavedAgentStore | None


def agent_tools(
    link: AgentLink,
    store: AgentTaskStore,
    *,
    gated: bool | None = None,
    saved: SavedAgentStore | None = None,
) -> tuple[DelegateTool, DelegateTool]:
    """The hand-off and cancel tools of one agent, classified by its approval choice (FR-011).

    ``gated`` is the choice the agent set carries. Given none, the link's own entry decides,
    as it always has for an agent named in the environment; a saved agent's link is never
    read-only, so a caller that forgets the set gates. The tools keep the classification
    they are built with, whatever becomes of the link.

    ``saved`` is the saved-agent store, which a saved agent's tools read before every send
    (005 FR-022). Tools for a saved agent are not built without it.
    """
    if link.source == "saved" and saved is None:
        raise ValueError(
            f"Agent '{link.name}' is a saved agent: its tools need the saved-agent store."
        )
    if gated is None:
        gated = not link.read_only
    classification = Classification.GATED if gated else Classification.READ_ONLY
    send = _Send(link, store, gated, saved if link.source == "saved" else None)
    handoff = StructuredTool(
        name=handoff_tool_name(link.name),
        description=handoff_description(link),
        args_schema=HandOffArgs,
        coroutine=_handoff_coroutine(send),
        handle_tool_error=True,
    )
    cancel = StructuredTool(
        name=cancel_tool_name(link.name),
        description=f"Ask the agent '{link.name}' to cancel one of its tasks.",
        args_schema=CancelArgs,
        coroutine=_cancel_coroutine(send),
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


def artifact_texts(artifact: Any) -> list[str]:
    """One artifact's parts as text, with text parts that follow one another read as one text.

    An agent that streams an artifact in chunks adds each chunk as a further part of it, and
    the task it keeps holds them that way. Joined like separate parts, a streamed sentence
    would come apart at every chunk (FR-006).
    """
    texts: list[str] = []
    after_text = False
    for part in artifact.parts:
        is_text = part.WhichOneof("content") == "text"
        if is_text and after_text:
            texts[-1] += part.text
        else:
            texts.append(part_text(part))
        after_text = is_text
    return texts


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
        parts = [text for artifact in source.artifacts for text in artifact_texts(artifact)]
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


def given_up_with_its_agent(agent: str, task_id: str) -> str:
    """The result of an answer or a cancel for a task of the agent as it was (005 FR-025)."""
    return (
        f"Agent '{agent}' task {task_id} was given up when the agent was moved or removed; "
        "nothing was sent."
    )


async def _connected(link: AgentLink) -> float | None:
    """Have the link hold a usable card, reading it inside the call's own time limit.

    Returns what is left of that limit when the card had to be read, and None when the link
    was connected already. A card that cannot be read or used is the tool's error result,
    with the card problem's sentence; nothing of what the read raised leaves the tool, where
    it would end the step and be kept with the thread (005 FR-021, research R6).
    """
    if link.connected:
        return None
    clock = asyncio.get_running_loop().time
    began = clock()
    try:
        await link.connect(limit=link.call_timeout)
    except CardError as error:
        sentence = error.message
    except Exception:  # anything else is a card that could not be read; none ends a run
        sentence = SENTENCES["unreadable"]
    else:
        return max(link.call_timeout - (clock() - began), 0.0)
    raise _failed(link, f"Agent '{link.name}' cannot be used: {sentence}")


def proposed_for_another(agent: str) -> str:
    """The result of a call that was not proposed for the agent its tool sends to (005 FR-023)."""
    return (
        f"Agent '{agent}': the agent this message was proposed for was removed; nothing was sent."
    )


async def _proposed_for(
    link: AgentLink, store: AgentTaskStore, thread_id: str, step: str, tool_call_id: str
) -> ProposedCall | None:
    """Keep back a call that was proposed for another agent than this tool's (005 FR-023).

    Returns the call's row, which also says whether the call needed a person's decision
    when it was proposed; None for a call that has none and is an environment agent's.

    A pause holds the tool's name and nothing about which agent had the name. The row written
    when the model proposed the call does: the saved agent's id, or none for an agent named
    in the environment. A call whose row has another id than this tool's agent is not sent,
    none on either side included: a saved agent added under the name of an environment agent
    that left, an environment agent that took a saved agent's name at a restart, or a saved
    agent removed and added again. Two agents named in the environment both have none, so
    between them a call follows the tool's name, as it always has.

    A call with no row follows the name when this is an environment agent's tool, as every
    call did before the rows existed. A saved agent's tool sends nothing for one: every call
    proposed for a saved agent has a row, so a call without one was proposed for another
    agent, or for no agent at all.
    """
    call = await store.get_call(thread_id, call_namespace(step), tool_call_id)
    if call is None:
        if link.source != "saved":
            return None
    elif call.agent_id == link.agent_id:
        return call
    raise _failed(link, proposed_for_another(link.name))


def was_removed(agent: str) -> str:
    """The result of a message to a saved agent that is gone (005 FR-022)."""
    return f"Agent '{agent}' was removed; nothing was sent."


def now_requires_approval(agent: str) -> str:
    """The result of a message no person decided, to an agent that requires approval now."""
    return f"Agent '{agent}' now requires approval, so this message was not sent."


def address_is_another_agent_s(agent: str) -> str:
    """The result of a message a run would send to an address its agent has left (R7)."""
    return (
        f"Agent '{agent}': the address this run started with is another agent's now; "
        "nothing was sent."
    )


async def _not_removed(send: _Send) -> None:
    """Keep back a message to a saved agent that is gone, before its card is asked for.

    A link that holds no usable card reads the card before it sends, which is a request to
    the agent. The rules of ``_still_allowed`` are asked again after that read, right
    before the send (005 FR-019, FR-022).
    """
    if send.saved is None:
        return
    if await send.saved.get_agent(send.link.agent_id or "") is None:
        raise _failed(send.link, was_removed(send.link.name))


async def _still_allowed(send: _Send, call: ProposedCall | None) -> None:
    """Keep back a message to a saved agent that was removed, and a message that no person
    decided to an agent that requires approval now (005 FR-022, research R7).

    A saved agent is read from the store by its id, so the answer is the same on every
    instance from the moment a change is committed, and an agent saved again under the
    name, which has another id, is not taken for this one. An agent named in the environment
    cannot change while the service runs: its choice is the one its tools were built with.

    A person decided a call only when its row records it: the approval gate received an
    approve or an edit for the call in a resume (``AgentTasksMiddleware.aafter_model``).
    Nothing about the tool or the build is taken for a decision. A step that passed the gate
    while its agent ran without approval can run again in a later run, whose tools wait for
    a person: a background run that failed is tried again from its last checkpoint, which
    lies after the gate, and the gate is not asked again. A row that says the call needed a
    decision when it was proposed says nothing about whether one was made: the step that
    would have asked may have failed, and a retry under another configuration let the call
    through unasked.

    This is the last thing a tool awaits before the send's first write; a change committed
    after it is not recalled.
    """
    link = send.link
    decided = call is not None and call.decided
    if send.saved is None:
        # A call with no row was proposed before the rows existed, and follows the tool.
        if send.gated and call is not None and not decided:
            raise _failed(link, now_requires_approval(link.name))
        return
    record = await send.saved.get_agent(link.agent_id or "")
    taken = False
    if record is not None and record.ref != link.ref:
        # The agent was moved since this run's tools were built, and the run finishes at the
        # address it started with (FR-019), unless that address has become another agent's:
        # an agent that requires approval may have been saved where this one ran without.
        # Every saved agent is read once more, and all three rules are asked of that reading.
        records = await send.saved.list_agents()
        record = next((found for found in records if found.id == link.agent_id), None)
        left = destination(link.config.url)
        taken = any(found.id != link.agent_id and found.destination == left for found in records)
    if record is None:
        raise _failed(link, was_removed(link.name))
    if not record.read_only and not decided:
        raise _failed(link, now_requires_approval(link.name))
    if taken:
        raise _failed(link, address_is_another_agent_s(link.name))


def may_not_have_received(agent: str) -> str:
    """The result of a message whose sending started and was never confirmed (FR-017)."""
    return (
        f"Agent '{agent}' may or may not have received this message; Freyja does not know, and "
        "will not send it again."
    )


def _handoff_coroutine(send: _Send) -> Any:
    link, store = send.link, send.store

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
        # The state an answered task is in before the answer is sent.
        before: str | None = None
        if task_id:
            known = await store.get_task(link.name, task_id)
            before = known.state if known is not None else None
            if known is not None and known.thread_id != thread_id:
                # A task belongs to the thread that started it; conversations are never shared
                # between threads (FR-008), so an answer cannot reach another thread's task.
                raise ToolException(
                    f"Agent '{link.name}' has no task {task_id} on this thread; nothing was sent."
                )
            if known is not None and known.agent_ref != link.ref:
                # The task is the agent's as it was when the task started: it is never
                # answered at a later address, nor by another agent under the name (FR-025).
                raise _failed(link, given_up_with_its_agent(link.name, task_id))
        if not link.connected:
            await _not_removed(send)
        limit = await _connected(link)
        context_id = await store.get_context(thread_id, link.name)
        sent_at = await store.now()
        # The last checks, with nothing between them and the send's first write (research R7).
        proposed = await _proposed_for(link, store, thread_id, step, tool_call_id)
        await _still_allowed(send, proposed)
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
                sent_at=sent_at,
            )
        )
        writer = _progress_writer()

        def on_progress(task: str, state: str, text: str) -> None:
            writer({"agent": link.name, "task_id": task, "state": state, "text": text})

        outcome = await send_message(link, a2a_message, on_progress, clock=store.now, limit=limit)
        result, ok = await _record(link, store, thread_id, message, outcome, before)
        await store.finish_send(
            thread_id,
            step,
            tool_call_id,
            result=result,
            task_id=outcome.task.id if outcome.task is not None else None,
        )
        # The send is finished and recorded: the call's row has done its work.
        await store.delete_call(thread_id, call_namespace(step), tool_call_id)
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
    link: AgentLink,
    store: AgentTaskStore,
    thread_id: str,
    message: str,
    outcome: SendOutcome,
    before: str | None = None,
) -> tuple[str, bool]:
    """Keep what the send produced, and say what the orchestrator receives.

    ``before`` is the state an answered task was in when the answer began.
    """
    if outcome.malformed is not None:
        # Nothing from the reply is kept: an unusable id could not be watched or answered.
        return f"Agent '{link.name}' replied with something malformed: {outcome.malformed}.", False
    known_task = (
        await store.get_task(link.name, outcome.task.id) if outcome.task is not None else None
    )
    if known_task is not None and known_task.agent_ref != link.ref:
        # The row under this id belongs to the agent the name had before, at another address
        # or under another id, and to that task's thread. Nothing from the reply is kept
        # (005 FR-025, research R10).
        return (
            f"Agent '{link.name}' replied with something malformed: it named a task that "
            "another agent under this name already has."
        ), False
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
    progress = _progress(outcome, now)
    if known_task is None:
        known |= progress
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
                # The agent, at the address, the task was started with (005 FR-025).
                agent_ref=link.ref,
                **known,
            )
        )
    else:
        # An answer continues a task this thread started: the row keeps the message the task
        # was started with and its start, and takes the state the answer led to (FR-016). An
        # outcome delivered before the answer is history: the next one is delivered anew.
        # Only while the task is pending, or as it was when the answer began: an answer
        # under way when its agent was moved or removed must not write over the row that
        # change gave up (005 FR-024, R10). A task that was already given up for its age
        # when it was answered takes the state the answer led to, as it always has.
        unchanged = (*PENDING_STATES, *([before] if before is not None else []))
        if progress and _learned_later(known_task, progress):
            known |= progress
        await store.update_task(link.name, task.id, when_state=unchanged, **known)
    return result, ok


def _progress(outcome: SendOutcome, now: datetime) -> dict[str, Any]:
    """The task's progress as the send left it, as the two columns take it, or nothing.

    Its time is the store's clock, ``now``, less the seconds since the send first received
    the text, on the event loop's clock: never the agent's clock, and never this instance's
    wall clock (008 FR-005, research R4).
    """
    if outcome.progress is None or outcome.progress_seen is None:
        return {}
    since = max(0.0, asyncio.get_running_loop().time() - outcome.progress_seen)
    return {"progress": outcome.progress, "progress_at": now - timedelta(seconds=since)}


def _learned_later(row: AgentTaskRecord, progress: dict[str, Any]) -> bool:
    """Whether the send's progress replaces the row's: another text, learned later.

    A check may have kept a later text while the send was under way, and the text learned
    last is the one kept, not the one written last (008 FR-006, the spec's Edge Cases).
    """
    if row.progress == progress["progress"]:
        return False
    return row.progress_at is None or row.progress_at < progress["progress_at"]


# What a task was in before it could be canceled: pending, or given up by Freyja (FR-022).
CANCELABLE = (*PENDING_STATES, "given_up")


async def record_outcome(
    store: AgentTaskStore,
    task: AgentTaskRecord,
    state: str,
    text: str,
    *,
    ready: bool,
    over: Collection[str] = CANCELABLE,
) -> tuple[str, bool]:
    """Record that ``task`` moved to ``state`` at the agent, unless it already moved on.

    ``ready`` marks the outcome to be delivered by a run; a result a tool returns is delivered
    with it, so it is not. ``over`` is the states the record may be in for the outcome to be
    written. A cancel writes over a task that is pending or was given up, so an outcome that
    arrives for a task Freyja canceled is ignored (FR-022). The worker writes over a pending
    task only: a check that was under way when the task was given up, with its agent moved
    or removed, must not turn it back into an outcome and have its thread told twice
    (005 FR-024, research R10).
    """
    outcome, ok = outcome_text(task.agent, task.task_id, state, text)
    stored = "failed" if state == "auth_required" else state
    now = await store.now()
    await store.update_task(
        task.agent,
        task.task_id,
        when_state=over,
        state=stored,
        last_checked_at=now,
        ended_at=None if stored == "input_required" else now,
        outcome=outcome,
        outcome_ready=ready,
        delivered_at=None,
    )
    return outcome, ok


def _cancel_coroutine(send: _Send) -> Any:
    link, store = send.link, send.store

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
        if task.agent_ref != link.ref:
            # Given up when its agent was moved or removed, or the name passed between the
            # environment and a saved agent: never canceled at another agent (005 FR-025).
            raise _failed(link, given_up_with_its_agent(link.name, task_id))
        if task.state not in CANCELABLE:
            return f"Agent '{link.name}' task {task_id} already ended as {task.state}; nothing was sent."
        if not link.connected:
            await _not_removed(send)
        limit = await _connected(link)
        sent_at = await store.now()
        proposed = await _proposed_for(link, store, thread_id, step, tool_call_id)
        await _still_allowed(send, proposed)
        await store.start_send(
            AgentSend(
                thread_id=thread_id,
                step=step,
                tool_call_id=tool_call_id,
                agent=link.name,
                kind="cancel",
                message_id=f"freyja-cancel-{thread_id}-{tool_call_id}-{_digest(step)[:8]}",
                request=request,
                sent_at=sent_at,
                task_id=task_id,
            )
        )
        result, ok = await _cancel(link, store, task, limit)
        await store.finish_send(thread_id, step, tool_call_id, result=result, task_id=task_id)
        await store.delete_call(thread_id, call_namespace(step), tool_call_id)
        if not ok:
            raise _failed(link, result)
        return result

    return call


async def _cancel(
    link: AgentLink, store: AgentTaskStore, task: AgentTaskRecord, limit: float | None = None
) -> tuple[str, bool]:
    """Send the cancel and record what the agent answered; never repeated (FR-022).

    ``limit`` is what is left of the call time limit when the card had to be read first.
    """
    name, task_id = link.name, task.task_id
    try:
        answer = await link.cancel(task_id, limit=limit)
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

    A message one of the tool's own rules kept back is logged here as well, with the rule in
    the result's words (005 FR-030).

    The text goes through ``describe`` for the log, so the agent's address is never written
    (FR-009, research R14); the result itself was built from described text already.
    """
    logger.warning("Agent %s: %s", link.name, link.describe(result))
    return ToolException(result)
