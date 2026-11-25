"""Per-run context for the orchestrator: the thread it is working on (FR-017, research R14).

The thread id lives in the run config (``configurable.thread_id``), which the API sets per
request and the ``task`` tool passes on to subagents. Nothing else tells the model which
conversation it is in, and the ``case-notes`` skill needs the id to name its working folder.

Feature 009: on the strict build, the one a caller's thread runs on, the model is told an
alias in place of the thread's id: ``c-`` and the first 16 hexadecimal characters of the
SHA-256 of the id. It is stable for the conversation and the same on every instance, and it
is not an id of anything in Freyja's API, so a caller that asks Freyja for its thread's id
learns nothing it can use (FR-018, research R6). A thread's id is 32 random hexadecimal
characters, so the alias cannot be turned back into it by trying ids.
"""

import hashlib
from collections.abc import Awaitable, Callable

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.config import get_config

THREAD_LINE = (
    "You are working on thread `{thread_id}`. Working files for this conversation belong "
    "under `cases/{thread_id}/` in your workspace."
)


def thread_alias(thread_id: str) -> str:
    """What a caller's thread is called where its id would be: ``c-`` and 16 hex digits."""
    return "c-" + hashlib.sha256(thread_id.encode()).hexdigest()[:16]


class ThreadContextMiddleware(AgentMiddleware):
    """Append the current thread id, or its alias, to the system message on every model call."""

    def __init__(self, *, alias: bool = False) -> None:
        super().__init__()
        self.alias = alias

    def _with_thread(self, request: ModelRequest) -> ModelRequest:
        try:
            thread_id = get_config().get("configurable", {}).get("thread_id")
        except RuntimeError:  # outside a graph run there is no config and nothing to add
            return request
        if not thread_id:
            return request
        line = THREAD_LINE.format(thread_id=thread_alias(thread_id) if self.alias else thread_id)
        return request.override(
            system_message=append_to_system_message(request.system_message, line)
        )

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(self._with_thread(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(self._with_thread(request))
