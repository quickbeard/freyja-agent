"""A span and a count for every call of the model and of a tool (``runtime/telemetry.py``).

``TelemetryMiddleware`` is on the orchestrator and on its subagent, on both builds, whether
telemetry is on or off: off, the tracer and the meter it is given do nothing, so the graph is
the same either way (constitution principle III).

It reads nothing of a call but which tool it is for, and that from the tools the orchestrator
was built with: a name a model wrote for a tool that does not exist is not repeated. What a
call was asked and what it answered are never looked at.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp, GraphInterrupt
from langgraph.types import Command

from freyja_agent.runtime.telemetry import (
    CANCELLED,
    ERROR,
    INTERRUPTED,
    OK,
    UNKNOWN_TOOL,
    Call,
    Telemetry,
    ToolLabels,
)


def _left(call: Call, exc: BaseException) -> None:
    """Say how a call that raised ended, where that is no error of the call's."""
    if isinstance(exc, GraphInterrupt):
        # The graph's own way of pausing a run: a proposal inside the call waits for a person.
        call.outcome = INTERRUPTED
    elif isinstance(exc, GraphBubbleUp):
        # Any other signal the graph passes up through a call is its control flow.
        call.outcome = OK
    elif isinstance(exc, asyncio.CancelledError):
        call.outcome = CANCELLED


def _answered(result: Any) -> str:
    """How a tool call that returned ended: a tool's failure comes back as a message."""
    if isinstance(result, ToolMessage) and result.status == "error":
        return ERROR
    return OK


class TelemetryMiddleware(AgentMiddleware):
    """Record each model call and each tool call of a run as a span and a count.

    ``tools`` names every tool the orchestrator was built with, with its classification and
    the tool server or agent it belongs to.
    """

    def __init__(self, telemetry: Telemetry, tools: Mapping[str, ToolLabels]) -> None:
        super().__init__()
        self.telemetry = telemetry
        self.tool_labels = dict(tools)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        with self.telemetry.model_call() as call:
            try:
                return handler(request)
            except BaseException as exc:
                _left(call, exc)
                raise

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        with self.telemetry.model_call() as call:
            try:
                return await handler(request)
            except BaseException as exc:
                _left(call, exc)
                raise

    def _labels(self, request: ToolCallRequest) -> ToolLabels:
        # By the tool the node holds, not by the name in the call, which a model wrote.
        if request.tool is None:
            return UNKNOWN_TOOL
        return self.tool_labels.get(request.tool.name, UNKNOWN_TOOL)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        with self.telemetry.tool_call(self._labels(request)) as call:
            try:
                result = handler(request)
            except BaseException as exc:
                _left(call, exc)
                raise
            call.outcome = _answered(result)
            return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        with self.telemetry.tool_call(self._labels(request)) as call:
            try:
                result = await handler(request)
            except BaseException as exc:
                _left(call, exc)
                raise
            call.outcome = _answered(result)
            return result
