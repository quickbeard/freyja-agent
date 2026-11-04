"""Per-run context for the orchestrator: the thread it is working on (FR-017, research R14).

The thread id lives in the run config (``configurable.thread_id``), which the API sets per
request and the ``task`` tool passes on to subagents. Nothing else tells the model which
conversation it is in, and the ``case-notes`` skill needs the id to name its working folder.
"""

from collections.abc import Awaitable, Callable

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.config import get_config

THREAD_LINE = (
    "You are working on thread `{thread_id}`. Working files for this conversation belong "
    "under `cases/{thread_id}/` in your workspace."
)


class ThreadContextMiddleware(AgentMiddleware):
    """Append the current thread id to the system message on every model call."""

    @staticmethod
    def _with_thread(request: ModelRequest) -> ModelRequest:
        try:
            thread_id = get_config().get("configurable", {}).get("thread_id")
        except RuntimeError:  # outside a graph run there is no config and nothing to add
            return request
        if not thread_id:
            return request
        line = THREAD_LINE.format(thread_id=thread_id)
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
