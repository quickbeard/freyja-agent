"""What the orchestrator knows about its thread's agent tasks (feature 003, FR-018, research R11).

``AgentTasksMiddleware`` adds one line per pending task of the current thread to the system
message on every model call, so a status that changed during a long run is current. With no
pending task it adds nothing. ``check_agent_tasks`` is one of Freyja's own tools, read-only in
the fixed table: it lists the thread's agent tasks, or the named ones, on demand. It is also the
call an outcome of a pending task is recorded as (research R10).

Since feature 005 the middleware also records, after every reply of the model, which agent
each call of an agent's tool was proposed for (005 FR-023, research R8). A pause keeps a
tool's name and nothing about which agent had the name, so the record is what lets the tool
refuse a message proposed for another agent.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException
from langgraph.config import get_config
from pydantic import BaseModel, ConfigDict, Field

from freyja_agent.agent.prompts import append_to_system_message
from freyja_agent.runtime.agent_tasks import (
    AgentTaskRecord,
    AgentTaskStore,
    ProposedCall,
    call_namespace,
)

CHECK_AGENT_TASKS = "check_agent_tasks"
PENDING_HEADER = (
    "Agent tasks still pending on this thread. Their outcomes will be reported in this thread:"
)
NO_TASKS = "This thread has no agent tasks."
# How much of a task's message the check repeats, so the model can tell tasks apart.
MESSAGE_PREVIEW = 200
# The key of a run's configuration that holds, in a resume, what the person decided: the
# namespace of the approval gate whose interrupt the resume answers, and the calls of that
# interrupt they approved or edited (``run_stream``, ``decided_calls``; 005 FR-022).
RESUME_KEY = "freyja_resume"


def _thread_id() -> str | None:
    try:
        thread_id = get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:  # outside a graph run there is no thread
        return None
    return str(thread_id) if thread_id else None


# How a line break in a field of a task's line is written: in sight, and on the same line.
LINE_BREAK = " ⏎ "


def one_line(text: str) -> str:
    """``text`` on one line, each of its line breaks, of any kind, written as ``LINE_BREAK``.

    A task's line is read line by line, and every entry starts a line with ``- Agent``. A
    line break inside a field would start a line that could pass for another entry or for
    the service's own words.
    """
    return LINE_BREAK.join(text.splitlines())


def pending_line(task: AgentTaskRecord) -> str:
    """One task as the model is told of it on every call, and as the check's entry begins.

    The agent's name is a configured one. The task's id is the agent's choice: a reply whose
    id holds a line break is refused as malformed (``a2a_agents.malformed``), and the id is
    written on one line all the same, for a task recorded before that was so.
    """
    return (
        f"- Agent '{task.agent}', task {one_line(task.task_id)}: {task.state}, "
        f"started {task.started_at.isoformat()}"
    )


def task_line(task: AgentTaskRecord) -> str:
    """One task as ``check_agent_tasks`` lists it: the pending line, then when it was checked.

    A task an agent is working on, with progress kept, is followed by the progress, set apart
    (feature 008, FR-013): a line in fixed words that say whose text it is and when it was
    learned, indented by two spaces, then every line of the text indented by four, however
    its lines are broken (``splitlines``). An entry starts at the beginning of a line with
    ``- Agent``, so no line of the progress text, whatever it holds, can pass for another
    entry or for the service's own words. The entry's own line holds the task's id and the
    start of its message on that one line (``one_line``), so neither can start a line either.
    """
    line = f"{pending_line(task)}, last checked {task.last_checked_at.isoformat()}"
    if task.ended_at is not None:
        line += f", ended {task.ended_at.isoformat()}"
    message = one_line(task.message[:MESSAGE_PREVIEW])
    entry = f"{line}. Task message: {message}"
    if task.state != "working" or task.progress is None or task.progress_at is None:
        return entry
    words = "\n".join(f"    {text}" for text in task.progress.splitlines())
    return (
        f"{entry}\n  Progress the agent reported, in its own words "
        f"(learned {task.progress_at.isoformat()}):\n{words}"
    )


@dataclass(frozen=True)
class AgentTool:
    """Which agent one of the orchestrator's tools sends to (005 research R8)."""

    # The agent's name, and the saved agent's id; None for an agent named in the environment.
    agent: str
    agent_id: str | None
    # Whether a call of the tool needs a person's decision in this build.
    gated: bool


def _namespace() -> str:
    """The namespace this model call's tools will run under (``call_namespace``)."""
    return call_namespace(str(get_config().get("configurable", {}).get("checkpoint_ns") or ""))


class AgentTasksMiddleware(AgentMiddleware):
    """Append the current thread's pending agent tasks to the system message (R11), and
    record which agent each proposed call of an agent's tool is for (005 research R8).

    ``agent_tools`` names, per tool of an agent in this build, the agent it sends to.
    """

    def __init__(
        self, store: AgentTaskStore, agent_tools: Mapping[str, AgentTool] | None = None
    ) -> None:
        super().__init__()
        self.store = store
        self.agent_tools = dict(agent_tools or {})

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        """A synchronous invocation adds nothing: the store is read asynchronously, and the
        service only runs the agent asynchronously (``astream``)."""
        return handler(request)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        thread_id = _thread_id()
        if thread_id is None:
            return await handler(request)
        pending = [task for task in await self.store.list_tasks(thread_id) if task.pending]
        if pending:
            text = "\n".join([PENDING_HEADER, *(pending_line(task) for task in pending)])
            request = request.override(
                system_message=append_to_system_message(request.system_message, text)
            )
        response = await handler(request)
        await self._record_calls(thread_id, response)
        return response

    async def _record_calls(self, thread_id: str, response: ModelResponse) -> None:
        """Replace the rows of this thread and namespace by the calls of this reply.

        Written before the reply reaches the graph, so before the approval gate can pause on
        it and before any tool runs. A reply that calls no agent leaves the namespace with no
        row. A failure is not caught: a call that could not be recorded must not be carried
        out, and the model call fails with it.
        """
        if not self.agent_tools:
            return
        calls = [
            ProposedCall(
                thread_id=thread_id,
                namespace="",  # the store keeps each under the namespace it is given
                tool_call_id=call["id"],
                agent=tool.agent,
                agent_id=tool.agent_id,
                gated=tool.gated,
            )
            for message in response.result
            if isinstance(message, AIMessage)
            for call in message.tool_calls
            if call.get("id") and (tool := self.agent_tools.get(call["name"])) is not None
        ]
        await self.store.replace_calls(thread_id, _namespace(), calls)

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Record which proposed calls of an agent's tools a person approved or edited.

        This hook runs right after the approval gate's own, in the same graph: the gate's
        middleware comes later in the list, and hooks after the model run in reverse order.
        A resume answers one interrupt, of one gate, and the run's configuration holds under
        ``RESUME_KEY`` that gate's namespace, the ids of the calls of the reply it paused on,
        and the calls of the interrupt the person approved or edited, by name and arguments
        as they were shown. A call is recorded as decided only when it is a call of that
        reply and one of those, each matched once and in order, and the gate did not answer
        it with a message of its own, as it answers a rejection (005 FR-022). A later reply
        of the same resume, in the same namespace, is another reply, whatever it proposes:
        only the first run of this step in that namespace records anything.

        Nothing about the build or the gate is taken for a decision. The gate's step may
        have completed in an earlier run, under another configuration, and be reused by
        this one: a retried resume reuses it, and a background retry carries no decision
        at all. A call the person was not shown is not decided, whatever this build's gate
        would ask. In a resume, another namespace is another gate's: a sibling subagent is
        continued by the same resume, and nobody decided its calls.
        """
        del runtime  # named by the framework, which passes it by that name; nothing is read
        decision = get_config().get("configurable", {}).get(RESUME_KEY)
        if not isinstance(decision, Mapping) or decision.get("namespace") != _namespace():
            return None
        # Only the first time this step runs in that namespace in the resume, which is right
        # after the gate the resume answers: a later reply of the resume is another reply,
        # also when a model gives its calls the same ids again. The list is the run's own,
        # shared by every step and segment of it.
        seen = decision.get("seen")
        if not isinstance(seen, list) or _namespace() in seen:
            return None
        seen.append(_namespace())
        thread_id = _thread_id()
        messages = list(state.get("messages") or []) if isinstance(state, Mapping) else []
        index = next(
            (i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], AIMessage)),
            None,
        )
        if thread_id is None or index is None:
            return None
        answered = {
            message.tool_call_id
            for message in messages[index + 1 :]
            if isinstance(message, ToolMessage)
        }
        shown = [
            (str(action.get("name")), action.get("args"))
            for action in decision.get("approved") or []
            if isinstance(action, Mapping)
        ]
        # The calls of the reply the gate paused on, by id: a later reply of this resume
        # in the same namespace may propose the same message again, unasked.
        paused = set(decision.get("calls") or [])
        decided = []
        for call in messages[index].tool_calls:
            if (
                not call.get("id")
                or call["id"] not in paused
                or call["id"] in answered
                or call["name"] not in self.agent_tools
            ):
                continue
            key = (call["name"], call["args"])
            if key in shown:
                shown.remove(key)
                decided.append(call["id"])
        if decided:
            # Not caught: a decision that could not be recorded leaves the call unsent.
            await self.store.mark_decided(thread_id, _namespace(), decided)
        return None


class CheckArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ids: Annotated[
        list[str] | None,
        Field(description="Only these tasks; leave out to list every agent task of the thread."),
    ] = None


def check_agent_tasks(store: AgentTaskStore) -> StructuredTool:
    """The tool that lists this thread's agent tasks and their status (FR-018)."""

    async def call(task_ids: list[str] | None = None) -> str:
        thread_id = _thread_id()
        if thread_id is None:
            raise ToolException("Checking agent tasks needs the thread; none was given.")
        tasks = await store.list_tasks(thread_id)
        if task_ids is None:
            return "\n".join(task_line(task) for task in tasks) or NO_TASKS
        by_id = {task.task_id: task for task in tasks}
        return (
            "\n".join(
                task_line(by_id[task_id])
                if task_id in by_id
                else f"No agent task {task_id} on this thread."
                for task_id in task_ids
            )
            or NO_TASKS
        )

    return StructuredTool(
        name=CHECK_AGENT_TASKS,
        description=(
            "List the agent tasks started on this thread, or only the named ones, with their "
            "status: pending (working or waiting for input) or ended, and when. For a task an "
            "agent is working on, it also gives the progress the agent last reported, in the "
            "agent's own words."
        ),
        args_schema=CheckArgs,
        coroutine=call,
        handle_tool_error=True,
    )
