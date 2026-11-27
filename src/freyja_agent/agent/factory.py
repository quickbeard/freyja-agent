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

from deepagents import CompiledSubAgent, SubAgent, create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
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

from freyja_agent.agent.agent_tasks import AgentTasksMiddleware, AgentTool, check_agent_tasks
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
from freyja_agent.agent.telemetry import TelemetryMiddleware
from freyja_agent.agent.tools import send_notification
from freyja_agent.runtime.agent_tasks import AgentTaskStore
from freyja_agent.runtime.telemetry import Telemetry, ToolLabels

AGENT_NAME = "freyja"
# Relative to the backend root, which is the workspace directory.
SKILLS_SOURCE = "/skills/"
# The argument Deep Agents' provider profile sets for ``openai:`` models (``resolve_model``).
RESPONSES_API = "use_responses_api"


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
    agent_tasks: AgentTaskStore | None = None,
    openai_responses_api: bool = True,
    backend: BackendProtocol | None = None,
    alias_threads: bool = False,
    telemetry: Telemetry | None = None,
) -> Orchestrator:
    """Build the orchestrator and its catalog.

    ``model`` is the provider-prefixed string from settings in production and a chat model
    instance in tests; Deep Agents resolves the string itself, so the identifier never
    appears here (FR-016). ``openai_responses_api`` is ``FREYJA_OPENAI_RESPONSES_API``: false
    has an ``openai:`` string resolved to a model that is called through chat completions
    (``resolve_model``). File tools are confined to ``workspace_dir`` and skills are read
    from its ``skills/`` folder (FR-017). ``max_steps`` bounds the general-purpose subagent;
    the parent run is bounded per call by the same value (FR-004a). ``ThreadContextMiddleware``
    tells the model, and every subagent, which thread it is working on.

    ``delegate_tools`` are the converted tools of the configured tool servers
    (``delegates.mcp_tools.DelegateTool``). They join Freyja's own tools on the orchestrator
    and on its subagent, and every one of them that the operator did not list as read-only is
    gated on both. A tool that ends up in either tool node without a classification raises
    ``UnclassifiedToolError`` instead of running ungated (feature 002, FR-006, FR-007, FR-011).

    ``agent_tasks`` is the store of the thread's agent tasks, given when at least one agent is
    configured. ``check_agent_tasks`` reads it and ``AgentTasksMiddleware`` lists the pending
    ones on every model call, on the orchestrator and its subagent (feature 003, FR-018).
    Without it neither is added, so with no agents configured the orchestrator and its catalog
    are what they were before feature 003 (FR-024). The middleware is told which agent each
    agent tool among ``delegate_tools`` sends to, and records every call of one that the
    model proposes, on the orchestrator and on its subagent (feature 005, FR-023).

    Feature 009 builds a second, strict orchestrator for the threads callers start over A2A
    with the same function. ``backend`` is the file backend of the orchestrator and its
    subagent, the shared workspace's when none is given; the strict build passes
    ``CallerFiles``, which gives a named caller its own area (FR-009a). ``alias_threads``
    tells the model an alias in place of its thread's id (research R6).

    ``telemetry`` is what records a span and a count for every call of the model and of a
    tool, on the orchestrator and its subagent (``TelemetryMiddleware``). Given none, as
    when it is off, the middleware is there all the same with a tracer and a meter that do
    nothing, so the graph does not depend on it.
    """
    if backend is None:
        backend = workspace_backend(workspace_dir)
    delegate_entries = [_delegate_entry(tool) for tool in delegate_tools]
    own_tools: list[BaseTool] = [send_notification]
    task_middleware: list[Any] = []
    # Which agent each agent tool sends to, and whether a call of it is gated in this build.
    agent_tools = {
        tool.name: AgentTool(
            agent=tool.agent,
            agent_id=tool.agent_id,
            gated=tool.classification is Classification.GATED,
        )
        for tool in delegate_tools
        if tool.agent is not None
    }
    if agent_tasks is not None:
        own_tools.append(check_agent_tasks(agent_tasks))
        task_middleware.append(AgentTasksMiddleware(agent_tasks, agent_tools))
    tools: list[BaseTool] = [*own_tools, *(tool.tool for tool in delegate_tools)]
    recording = TelemetryMiddleware(
        telemetry if telemetry is not None else Telemetry.off(), _tool_labels(delegate_entries)
    )
    # The gate is generated from the classification before anything is compiled: the tools
    # passed in explicitly are the only ones that can be gated.
    gate = interrupt_config([*(_own_entry(tool) for tool in own_tools), *delegate_entries])
    chat_model = resolve_model(model, openai_responses_api=openai_responses_api)
    subagent_graph, subagent = _general_purpose_subagent(
        chat_model,
        tools,
        backend,
        max_steps,
        gate,
        agent_tasks,
        agent_tools,
        alias_threads,
        recording,
    )
    agent = create_deep_agent(
        model=chat_model,
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        skills=[SKILLS_SOURCE],
        interrupt_on=gate,
        middleware=[ThreadContextMiddleware(alias=alias_threads), *task_middleware, recording],
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


def _tool_labels(delegates: Sequence[CatalogTool]) -> dict[str, ToolLabels]:
    """What telemetry says of each tool the orchestrator can hold: its name and its
    classification from the fixed table or the delegate's entry, and the tool server or the
    agent a delegate's tool belongs to, by its configured name."""
    labels = {
        name: ToolLabels(name=name, classification=classification.value)
        for name, classification in OWN_TOOLS.items()
    }
    for entry in delegates:
        labels[entry.name] = ToolLabels(
            name=entry.name,
            classification=entry.classification.value,
            server=entry.server,
            agent=entry.agent,
        )
    return labels


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


def resolve_model(
    model: str | BaseChatModel, *, openai_responses_api: bool = True
) -> BaseChatModel:
    """Resolve a provider-prefixed string the way Deep Agents does; pass instances through.

    ``apply_provider_profile`` supplies the provider-specific defaults Deep Agents registers
    (for example OpenRouter attribution headers), so a string resolves identically whether
    Deep Agents or this factory does it.

    The one exception is ``openai_responses_api``. Deep Agents turns on OpenAI's Responses API
    for every ``openai:`` model. An OpenAI-compatible server may answer a first message over
    that API and refuse every later one, because its Responses API takes no earlier assistant
    message, tool call, or tool result as input. False turns the API off where the profile
    turned it on, and the model is then called through chat completions. A provider whose
    profile does not name the API is resolved as before, so the setting cannot hand another
    provider's client an argument it does not know.
    """
    if isinstance(model, BaseChatModel):
        return model
    defaults = dict(apply_provider_profile(model))
    if not openai_responses_api and RESPONSES_API in defaults:
        defaults[RESPONSES_API] = False
    return init_chat_model(model, **defaults)


def workspace_backend(workspace_dir: str | Path) -> FilesystemBackend:
    """The file tools' backend on the shared workspace, which every thread but a named
    caller's works in (FR-017; feature 009, FR-009a)."""
    root = Path(workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    return FilesystemBackend(root_dir=root, virtual_mode=True)


def _general_purpose_subagent(
    model: BaseChatModel,
    tools: list[Any],
    backend: BackendProtocol,
    max_steps: int,
    gate: dict[str, Any],
    agent_tasks: AgentTaskStore | None,
    agent_tools: dict[str, AgentTool],
    alias_threads: bool = False,
    recording: TelemetryMiddleware | None = None,
) -> tuple[Any, CompiledSubAgent]:
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
        ThreadContextMiddleware(alias=alias_threads),
        *([AgentTasksMiddleware(agent_tasks, agent_tools)] if agent_tasks is not None else []),
        *([recording] if recording is not None else []),
    ]
    append_prompt_caching_middleware(middleware)
    spec: SubAgent = {
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
