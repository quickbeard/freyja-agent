"""The one place the orchestrator is assembled (constitution principle III, FR-015).

The service, the tests, and any future command-line entry point call ``build_agent``. Model,
checkpointer, store, and workspace are parameters so tests inject in-memory versions without a
second code path.
"""

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
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
    system_prompt: str = DEFAULT_PROMPT,
) -> Any:
    """Build the compiled orchestrator graph.

    ``model`` is the provider-prefixed string from settings in production and a chat model
    instance in tests; Deep Agents resolves the string itself, so the identifier never
    appears here (FR-016). File tools are confined to ``workspace_dir`` and skills are read
    from its ``skills/`` folder (FR-017).
    """
    root = Path(workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    return create_deep_agent(
        model=model,
        tools=[send_notification],
        system_prompt=system_prompt,
        backend=FilesystemBackend(root_dir=root, virtual_mode=True),
        skills=[SKILLS_SOURCE],
        interrupt_on=SEND_NOTIFICATION_INTERRUPT,
        checkpointer=checkpointer,
        store=store,
        name=AGENT_NAME,
    )
