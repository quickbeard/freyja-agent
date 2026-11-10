"""An A2A agent's two tools: the hand-off and the cancel (feature 003, research R4, R12).

Each configured agent contributes ``handoff_to_<agent>`` and ``cancel_<agent>_task``. Both carry
the agent's classification, decided by its entry alone: ``read_only`` when the entry says
``read_only: true``, ``gated`` otherwise (FR-011). What the card says about the agent describes
the hand-off tool and never classifies it.

The argument models are pydantic, so an edit decision is validated by the existing model path
and an unknown key is refused; ``task_id`` is pinned, so an answer or a cancel stays bound to its
task (FR-013).

Until hand-offs are implemented, both coroutines raise a tool error naming the agent, so a tool
that exists sends nothing.
"""

from typing import Annotated, Any

from langchain_core.tools import StructuredTool, ToolException
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field

from freyja_agent.agent.catalog import Classification
from freyja_agent.delegates.a2a_agents import AgentLink, cancel_tool_name, handoff_tool_name
from freyja_agent.delegates.mcp_tools import DelegateTool

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


def agent_tools(link: AgentLink) -> tuple[DelegateTool, DelegateTool]:
    """The hand-off and cancel tools of one agent, classified by its entry (FR-011)."""
    classification = Classification.READ_ONLY if link.read_only else Classification.GATED
    handoff = StructuredTool(
        name=handoff_tool_name(link.name),
        description=handoff_description(link),
        args_schema=HandOffArgs,
        coroutine=_not_available(link.name, "hand-offs"),
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
