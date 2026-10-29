"""Test doubles: a scripted chat model and a controllable clock.

The agent always calls ``bind_tools`` and expects tool-call messages, which no stock fake in
langchain-core supports, so the suite carries its own (research R11).
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted ``AIMessage``s in order, then ``final_text`` forever.

    ``bound_tool_names`` records what the agent bound; ``calls`` counts model invocations;
    ``seen`` keeps the message lists the model received, so tests can assert continuity.
    """

    script: list[AIMessage] = Field(default_factory=list)
    final_text: str = "Done."
    bound_tool_names: list[str] = Field(default_factory=list)
    calls: int = 0
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    _cursor: int = PrivateAttr(default=0)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        self.bound_tool_names = [_tool_name(tool) for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        self.seen.append(list(messages))
        message = self.next_message()
        return ChatResult(generations=[ChatGeneration(message=message)])

    def next_message(self) -> AIMessage:
        if self._cursor < len(self.script):
            message = self.script[self._cursor]
            self._cursor += 1
            return message.model_copy(deep=True)
        return AIMessage(content=self.final_text)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        return {"ls_provider": "scripted", "ls_model_name": "scripted", "ls_model_type": "chat"}


class LoopingChatModel(ScriptedChatModel):
    """Always asks for the same tool call, so a run only ends at the step limit."""

    tool_name: str = "ls"
    tool_args: dict[str, Any] = Field(default_factory=lambda: {"path": "/"})

    def next_message(self) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {"id": f"call_{self.calls}", "name": self.tool_name, "args": self.tool_args}
            ],
        )


class FailingChatModel(ScriptedChatModel):
    """Raises on the given call number, after returning any scripted messages before it."""

    fail_on_call: int = 2
    error: str = "provider unavailable"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.calls + 1 == self.fail_on_call:
            self.calls += 1
            raise RuntimeError(self.error)
        return super()._generate(messages, stop, run_manager, **kwargs)


class FakeClock:
    """Monotonic clock the tests advance by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function", tool)
        return str(function.get("name", ""))
    return str(getattr(tool, "name", getattr(tool, "__name__", tool)))
