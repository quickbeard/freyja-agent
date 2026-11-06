"""Every tool the orchestrator can call, with the one classification each of them has.

Whether a tool pauses for a person is decided here, by a fixed rule, before any run starts
(constitution principles I and VI). The model, a tool's description, and whatever a tool
server says about its own tools are not inputs:

- a tool from a tool server is ``read_only`` only if the operator's configuration lists it,
  and ``gated`` otherwise (the conversion in ``delegates/mcp_tools.py`` applies that rule);
- Freyja's own tools are classified by the fixed table below;
- a tool with no classification stops the service from starting.

The approval gate's configuration is generated from the result, so the set of tools that pause
and the set the catalog reports as gated cannot differ.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langchain_core.tools import BaseTool


class Classification(StrEnum):
    # Changes nothing; runs without approval.
    READ_ONLY = "read_only"
    # Changes only the orchestrator's own workspace, or hands work to its subagent; runs
    # without approval. Only Freyja's own tools can have it.
    WORKSPACE = "workspace"
    # May have effects outside the workspace; every call pauses for a decision.
    GATED = "gated"


APPROVE, EDIT, REJECT = "approve", "edit", "reject"

# Freyja's own tools, in the order the catalog lists them (research R8). A Deep Agents upgrade
# that adds a tool fails startup until someone adds a row here: silence means "not classified",
# never "allowed". The `deepagents<0.8` pin and tests/test_factory.py keep the table honest.
OWN_TOOLS: dict[str, Classification] = {
    "ls": Classification.READ_ONLY,
    "read_file": Classification.READ_ONLY,
    "glob": Classification.READ_ONLY,
    "grep": Classification.READ_ONLY,
    "write_file": Classification.WORKSPACE,
    "edit_file": Classification.WORKSPACE,
    "delete": Classification.WORKSPACE,
    "task": Classification.WORKSPACE,
    "send_notification": Classification.GATED,
}

# In the tool node but never offered to the model: no backend supports it (001 research R1).
# Known, so it does not count as unclassified, and it has no catalog entry.
NOT_OFFERED: frozenset[str] = frozenset({"execute"})

# No tool server may provide a tool with one of these names (FR-004).
RESERVED_TOOL_NAMES: frozenset[str] = frozenset(OWN_TOOLS) | NOT_OFFERED

# What the pause shows for the placeholder; unchanged from feature 001.
SEND_NOTIFICATION_DESCRIPTION = "Send a notification to a channel (placeholder: logs only)."


class UnclassifiedToolError(RuntimeError):
    """A tool reached the orchestrator without a classification. Startup stops (FR-004)."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Tool '{name}' has no classification: it is neither provided by a configured tool "
            "server nor listed in the fixed table of Freyja's own tools "
            "(freyja_agent.agent.catalog.OWN_TOOLS). Refusing to start with an unclassified tool."
        )
        self.tool = name


def has_declared_input(schema: Mapping[str, Any] | None) -> bool:
    """False when the tool names no arguments: no ``properties``, or an empty one (FR-009)."""
    return bool((schema or {}).get("properties"))


def allowed_decisions(
    classification: Classification, input_schema: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """The decisions a tool's proposal accepts; empty unless the tool is gated.

    A gated tool with no declared input offers approve and reject: there is nothing an edit
    could change.
    """
    if classification is not Classification.GATED:
        return ()
    if has_declared_input(input_schema):
        return (APPROVE, EDIT, REJECT)
    return (APPROVE, REJECT)


@dataclass(frozen=True)
class CatalogTool:
    """One tool the orchestrator is offered (data-model.md "Catalog entry")."""

    name: str
    # The tool server that provides it; None for Freyja's own tools.
    server: str | None
    description: str
    input_schema: dict[str, Any]
    classification: Classification
    allowed_decisions: tuple[str, ...]
    # What the server says about its own tool. Shown, never used (FR-006, FR-014).
    server_hints: dict[str, Any] | None = None
    # The callable itself, for validating an edit; not part of any API shape.
    tool: BaseTool | None = field(default=None, repr=False, compare=False)

    @property
    def gated(self) -> bool:
        return self.classification is Classification.GATED


@dataclass(frozen=True)
class Catalog:
    """The tools given to the orchestrator at startup, in catalog order."""

    tools: tuple[CatalogTool, ...] = ()

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Tool names must be unique in the catalog; repeated: {duplicates}")

    def __iter__(self) -> Iterator[CatalogTool]:
        return iter(self.tools)

    def __len__(self) -> int:
        return len(self.tools)

    def get(self, name: str) -> CatalogTool | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    def gated(self) -> tuple[CatalogTool, ...]:
        return tuple(tool for tool in self.tools if tool.gated)

    def decisions_for(self, name: str) -> tuple[str, ...]:
        """The decisions a pending proposal for ``name`` accepts under this catalog (FR-019).

        A proposal can outlive a restart with another configuration, so the answer comes from
        the tools in force now, not from the ones the proposal was made under:

        - still gated: what the catalog says for the tool;
        - no longer provided: approve and reject, and the call is never made either way;
        - no longer gated: approve only, because the call no longer needs a decision and the
          gate would silently ignore any other.
        """
        tool = self.get(name)
        if tool is None:
            return (APPROVE, REJECT)
        if tool.gated:
            return tool.allowed_decisions
        return (APPROVE,)


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def gate_description(tool: CatalogTool) -> str:
    """What the pause says about a gated tool: ``<server>: <first line of its description>``."""
    if tool.server is None:
        if tool.name == "send_notification":
            return SEND_NOTIFICATION_DESCRIPTION
        return first_line(tool.description) or tool.name
    return f"{tool.server}: {first_line(tool.description) or tool.name}"


def interrupt_config(tools: Iterable[CatalogTool]) -> dict[str, dict[str, Any]]:
    """The approval gate's configuration: one entry per gated tool, and no other (FR-007).

    The same mapping goes to the orchestrator and to its subagent.
    """
    return {
        tool.name: {
            "allowed_decisions": list(tool.allowed_decisions),
            "description": gate_description(tool),
        }
        for tool in tools
        if tool.gated
    }
