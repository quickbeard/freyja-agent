"""The one place the orchestrator is assembled (constitution principle III, FR-015).

The service, the tests, and any future command-line entry point call ``build_agent``. Model,
checkpointer, store, workspace, and the tools from tool servers are parameters, so tests inject
in-memory versions without a second code path.

``build_agent`` returns the catalog together with the agent. The catalog is built from the
tools that were compiled in, so what the service reports and what the orchestrator can call
cannot differ, and the approval gate is generated from it (feature 002, research R8, R11).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware._prompt_caching import append_prompt_caching_middleware
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, create_sub_agent
from deepagents.middleware.summarization import create_summarization_middleware
from deepagents.profiles.provider.provider_profiles import apply_provider_profile
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from freyja_agent.agent.catalog import (
    NOT_OFFERED,
    OWN_TOOLS,
    Catalog,
    CatalogTool,
    Classification,
    UnclassifiedToolError,
    allowed_decisions,
    interrupt_config,
)
from freyja_agent.agent.context import ThreadContextMiddleware
from freyja_agent.agent.prompts import DEFAULT_PROMPT
from freyja_agent.agent.tools import send_notification

AGENT_NAME = "freyja"
# Relative to the backend root, which is the workspace directory.
SKILLS_SOURCE = "/skills/"


@dataclass(frozen=True)
class Orchestrator:
    """The compiled orchestrator graph and the catalog of exactly the tools it was given."""

    agent: Any
    catalog: Catalog


def build_agent(
    model: str | BaseChatModel,
    *,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    workspace_dir: str | Path,
    max_steps: int,
    delegate_tools: Sequence[Any] = (),
    system_prompt: str = DEFAULT_PROMPT,
) -> Orchestrator:
    """Build the orchestrator and its catalog.

    ``model`` is the provider-prefixed string from settings in production and a chat model
    instance in tests; Deep Agents resolves the string itself, so the identifier never
    appears here (FR-016). File tools are confined to ``workspace_dir`` and skills are read
    from its ``skills/`` folder (FR-017). ``max_steps`` bounds the general-purpose subagent;
    the parent run is bounded per call by the same value (FR-004a). ``ThreadContextMiddleware``
    tells the model, and every subagent, which thread it is working on.

    ``delegate_tools`` are the converted tools of the configured tool servers
    (``delegates.mcp_tools.DelegateTool``). They join Freyja's own tools on the orchestrator
    and on its subagent, and every one of them that the operator did not list as read-only is
    gated on both. A tool that ends up in either tool node without a classification raises
    ``UnclassifiedToolError`` instead of running ungated (feature 002, FR-006, FR-007, FR-011).
    """
    root = Path(workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)
    delegate_entries = [_delegate_entry(tool) for tool in delegate_tools]
    tools: list[BaseTool] = [send_notification, *(entry.tool for entry in delegate_entries)]
    # The gate is generated from the classification before anything is compiled: the tools
    # passed in explicitly are the only ones that can be gated.
    gate = interrupt_config([_own_entry(send_notification), *delegate_entries])
    chat_model = resolve_model(model)
    subagent_graph, subagent = _general_purpose_subagent(
        chat_model, tools, backend, max_steps, gate
    )
    agent = create_deep_agent(
        model=chat_model,
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        skills=[SKILLS_SOURCE],
        interrupt_on=gate,
        middleware=[ThreadContextMiddleware()],
        subagents=[subagent],
        checkpointer=checkpointer,
        store=store,
        name=AGENT_NAME,
    )
    catalog = _build_catalog(agent, subagent_graph, delegate_entries)
    gated = {tool.name for tool in catalog.gated()}
    if set(gate) != gated:
        raise RuntimeError(
            "The approval gate and the catalog disagree about which tools are gated: "
            f"gate {sorted(gate)}, catalog {sorted(gated)}."
        )
    return Orchestrator(agent=agent, catalog=catalog)


def _delegate_entry(tool: Any) -> CatalogTool:
    return CatalogTool(
        name=tool.name,
        server=tool.server,
        description=tool.description,
        input_schema=tool.input_schema,
        classification=tool.classification,
        allowed_decisions=allowed_decisions(
            tool.classification, tool.input_schema, tool.pinned_args
        ),
        server_hints=tool.server_hints,
        agent=tool.agent,
        pinned_args=tool.pinned_args,
        tool=tool.tool,
    )


def _own_entry(tool: BaseTool) -> CatalogTool:
    """The catalog entry of one of Freyja's own tools, from the tool as compiled."""
    if tool.name not in OWN_TOOLS:
        raise UnclassifiedToolError(tool.name)
    classification: Classification = OWN_TOOLS[tool.name]
    # The schema of the arguments the model supplies, which leaves out injected ones.
    input_schema = convert_to_openai_tool(tool)["function"].get("parameters") or {
        "type": "object",
        "properties": {},
    }
    return CatalogTool(
        name=tool.name,
        server=None,
        description=tool.description,
        input_schema=input_schema,
        classification=classification,
        allowed_decisions=allowed_decisions(classification, input_schema),
        server_hints=None,
        tool=tool,
    )


def tool_node_tools(graph: Any) -> dict[str, BaseTool]:
    """The tools a compiled agent can execute, by name."""
    return dict(graph.nodes["tools"].bound.tools_by_name)


def _build_catalog(agent: Any, subagent_graph: Any, delegates: Sequence[CatalogTool]) -> Catalog:
    """Classify every tool the compiled orchestrator and its subagent hold, or refuse.

    Freyja's own tools come first, in the order of the fixed table, then the tool servers'
    in the order given. ``execute`` is in the tool node and never offered to the model, so it
    is known and has no entry.
    """
    own = tool_node_tools(agent)
    delegate_names = {entry.name for entry in delegates}
    for name in [*own, *tool_node_tools(subagent_graph)]:
        if name not in OWN_TOOLS and name not in NOT_OFFERED and name not in delegate_names:
            raise UnclassifiedToolError(name)
    own_entries = [_own_entry(own[name]) for name in OWN_TOOLS if name in own]
    return Catalog(tools=(*own_entries, *delegates))


def resolve_model(model: str | BaseChatModel) -> BaseChatModel:
    """Resolve a provider-prefixed string the way Deep Agents does; pass instances through.

    ``apply_provider_profile`` supplies the provider-specific defaults Deep Agents registers
    (for example OpenRouter attribution headers), so a string resolves identically whether
    Deep Agents or this factory does it.
    """
    if isinstance(model, BaseChatModel):
        return model
    return init_chat_model(model, **apply_provider_profile(model))


def _general_purpose_subagent(
    model: BaseChatModel,
    tools: list[Any],
    backend: FilesystemBackend,
    max_steps: int,
    gate: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Deep Agents' default general-purpose subagent, bound to the step limit.

    Returns the compiled graph, so the factory can check its tools, and the subagent entry
    for ``create_deep_agent``. It receives the same tools and the same gate as the parent.

    Deep Agents builds this subagent itself when none is supplied, but the compiled subagent
    carries its own recursion limit (9,999), which takes precedence over the limit the parent
    run passes per call. Supplying the same stack as a compiled subagent lets the limit be
    bound here, so a looping delegate stops at ``max_steps`` like the parent does. The
    middleware list mirrors ``deepagents.graph`` for version 0.7; the ``deepagents<0.8`` pin
    and ``tests/test_subagent.py`` guard the match.
    """
    middleware: list[Any] = [
        FilesystemMiddleware(backend=backend),
        create_summarization_middleware(model, backend),
        PatchToolCallsMiddleware(),
        SkillsMiddleware(backend=backend, sources=[SKILLS_SOURCE]),
        ThreadContextMiddleware(),
    ]
    append_prompt_caching_middleware(middleware)
    spec: dict[str, Any] = {
        **GENERAL_PURPOSE_SUBAGENT,
        "model": model,
        "tools": tools,
        "middleware": middleware,
        "interrupt_on": gate,
    }
    graph = create_sub_agent(spec)
    return graph, {
        "name": GENERAL_PURPOSE_SUBAGENT["name"],
        "description": GENERAL_PURPOSE_SUBAGENT["description"],
        "runnable": graph.with_config(recursion_limit=max_steps),
    }
