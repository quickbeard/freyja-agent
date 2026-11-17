"""Test doubles: a scripted chat model and a controllable clock.

The agent always calls ``bind_tools`` and expects tool-call messages, which no stock fake in
langchain-core supports, so the suite carries its own (research R11).
"""

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from freyja_agent.llm.providers import ModelSpec


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


class LabelledChatModel(ScriptedChatModel):
    """A scripted model that says which LLM profile it stands for (feature 004).

    Its replies end in ``final_text``, which names the label, so a thread shows which profile
    answered. ``hold()`` makes the next call wait until the test leaves ``held()``: the model
    runs in a worker thread, so holding it holds one run and leaves the event loop free.
    """

    label: str = "environment"
    _armed: threading.Event = PrivateAttr(default_factory=threading.Event)
    _reached: threading.Event = PrivateAttr(default_factory=threading.Event)
    _release: threading.Event = PrivateAttr(default_factory=threading.Event)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self._armed.is_set():
            self._armed.clear()
            self._reached.set()
            assert self._release.wait(10), f"the held call to {self.label} was never released"
        return super()._generate(messages, stop, run_manager, **kwargs)

    def hold(self) -> None:
        """Make the next call wait inside the model."""
        self._reached.clear()
        self._release.clear()
        self._armed.set()

    @contextmanager
    def held(self) -> Iterator[None]:
        """Inside: a run has reached the model and waits there."""
        try:
            assert self._reached.wait(5), f"no run reached the held model {self.label}"
            yield
        finally:
            self._release.set()


class ProfileModels:
    """A model factory for the suite: one labelled model per model id (004 research R18).

    It records every spec it was asked to build, so a test can see which profile a runtime
    built its orchestrator for, with which key and address, and how often.
    """

    def __init__(self) -> None:
        self.built: list[ModelSpec] = []
        self.models: dict[str, LabelledChatModel] = {}

    def __call__(self, spec: ModelSpec) -> LabelledChatModel:
        self.built.append(spec)
        return self.model(spec.model)

    def model(self, label: str) -> LabelledChatModel:
        """The model for a label, made on first use so a test can script it beforehand."""
        if label not in self.models:
            self.models[label] = LabelledChatModel(label=label, final_text=f"Answered by {label}.")
        return self.models[label]


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
