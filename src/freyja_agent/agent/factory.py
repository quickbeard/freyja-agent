"""The one place the orchestrator is assembled (constitution principle III, FR-015).

The service, the tests, and any future command-line entry point call ``build_agent``. Model,
checkpointer, store, and workspace are parameters so tests inject in-memory versions without a
second code path.
"""

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
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from freyja_agent.agent.prompts import DEFAULT_PROMPT
from freyja_agent.agent.tools import SEND_NOTIFICATION_INTERRUPT, send_notification

AGENT_NAME = "freyja"
# Relative to the backend root, which is the workspace directory.
SKILLS_SOURCE = "/skills/"


def build_agent(
    model: str | BaseChatModel,
    *,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    workspace_dir: str | Path,
    max_steps: int,
    system_prompt: str = DEFAULT_PROMPT,
) -> Any:
    """Build the compiled orchestrator graph.

    ``model`` is the provider-prefixed string from settings in production and a chat model
    instance in tests; Deep Agents resolves the string itself, so the identifier never
    appears here (FR-016). File tools are confined to ``workspace_dir`` and skills are read
    from its ``skills/`` folder (FR-017). ``max_steps`` bounds the general-purpose subagent;
    the parent run is bounded per call by the same value (FR-004a).
    """
    root = Path(workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)
    tools = [send_notification]
    chat_model = resolve_model(model)
    return create_deep_agent(
        model=chat_model,
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        skills=[SKILLS_SOURCE],
        interrupt_on=SEND_NOTIFICATION_INTERRUPT,
        subagents=[_general_purpose_subagent(chat_model, tools, backend, max_steps)],
        checkpointer=checkpointer,
        store=store,
        name=AGENT_NAME,
    )


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
    model: BaseChatModel, tools: list[Any], backend: FilesystemBackend, max_steps: int
) -> dict[str, Any]:
    """Deep Agents' default general-purpose subagent, bound to the step limit.

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
    ]
    append_prompt_caching_middleware(middleware)
    spec: dict[str, Any] = {
        **GENERAL_PURPOSE_SUBAGENT,
        "model": model,
        "tools": tools,
        "middleware": middleware,
        "interrupt_on": SEND_NOTIFICATION_INTERRUPT,
    }
    runnable = create_sub_agent(spec).with_config(recursion_limit=max_steps)
    return {
        "name": GENERAL_PURPOSE_SUBAGENT["name"],
        "description": GENERAL_PURPOSE_SUBAGENT["description"],
        "runnable": runnable,
    }
