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
from typing import Annotated

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, ToolException
from langgraph.config import get_config
from pydantic import BaseModel, ConfigDict, Field

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


def _thread_id() -> str | None:
    try:
        thread_id = get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:  # outside a graph run there is no thread
        return None
    return str(thread_id) if thread_id else None


def pending_line(task: AgentTaskRecord) -> str:
    return (
        f"- Agent '{task.agent}', task {task.task_id}: {task.state}, "
        f"started {task.started_at.isoformat()}"
    )


def task_line(task: AgentTaskRecord) -> str:
    """One task as ``check_agent_tasks`` lists it: the pending line, then when it was checked."""
    line = f"{pending_line(task)}, last checked {task.last_checked_at.isoformat()}"
    if task.ended_at is not None:
        line += f", ended {task.ended_at.isoformat()}"
    message = task.message[:MESSAGE_PREVIEW]
    return f"{line}. Task message: {message}"


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
            "status: pending (working or waiting for input) or ended, and when."
        ),
        args_schema=CheckArgs,
        coroutine=call,
        handle_tool_error=True,
    )
