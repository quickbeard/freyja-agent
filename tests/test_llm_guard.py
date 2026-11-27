"""004 FR-020, FR-021: a guarded model raises its errors without its profile's key."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from freyja_agent.llm.guard import ModelError, guard_model
from freyja_agent.llm.secrets import REDACTED

KEY = "sk-recognizable-key-0001"


class ProviderError(Exception):
    """What a provider's client raises, with what it was told as an attribute."""

    def __init__(self, message: str, body: str = "") -> None:
        super().__init__(message)
        self.body = body


class Repeats(BaseChatModel):
    """Fails with ``says`` when it is given one, and answers otherwise."""

    says: str | None = None

    @property
    def _llm_type(self) -> str:
        return "repeats"

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        if self.says is not None:
            raise ProviderError(self.says)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="hello"))])


class Streams(Repeats):
    """The same, as a model that streams: one chunk, then the failure."""

    def _stream(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content="hel"))
        if self.says is not None:
            raise ProviderError(self.says)

    async def _astream(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content="hel"))
        if self.says is not None:
            raise ProviderError(self.says)


def test_an_error_that_repeats_the_key_is_raised_without_it() -> None:
    model = guard_model(Repeats(says=f"401: the key {KEY} is not allowed"), [KEY])

    with pytest.raises(ModelError) as raised:
        model.invoke("hello")
    assert str(raised.value) == f"401: the key {REDACTED} is not allowed"
    # LangGraph stores an error as its repr and shows it as its text; neither holds the key,
    # and the provider's own error does not travel with it.
    assert KEY not in repr(raised.value)
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None


def test_the_same_holds_when_the_model_is_called_asynchronously() -> None:
    model = guard_model(Repeats(says=f"401: the key {KEY} is not allowed"), [KEY])

    with pytest.raises(ModelError) as raised:
        asyncio.run(model.ainvoke("hello"))
    assert str(raised.value) == f"401: the key {REDACTED} is not allowed"
    assert raised.value.__context__ is None


def test_an_error_without_the_key_is_raised_as_it_came() -> None:
    model = guard_model(Repeats(says="503: try again later"), [KEY])

    with pytest.raises(ProviderError) as raised:
        model.invoke("hello")
    assert str(raised.value) == "503: try again later"


def test_a_stream_that_fails_is_cleaned_too() -> None:
    model = guard_model(Streams(says=f"the key {KEY} is not allowed"), [KEY])

    with pytest.raises(ModelError) as raised:
        list(model.stream("hello"))
    assert str(raised.value) == f"the key {REDACTED} is not allowed"

    async def consume() -> None:
        async for _ in model.astream("hello"):
            pass

    with pytest.raises(ModelError) as raised:
        asyncio.run(consume())
    assert KEY not in str(raised.value)


def test_a_guarded_model_answers_and_is_still_what_it_was() -> None:
    plain, streaming = Repeats(), Streams()
    guarded = guard_model(plain, [KEY])

    assert guarded is plain
    assert isinstance(guarded, Repeats)
    assert type(guarded).__name__ == "Repeats"
    assert guarded.invoke("hello").content == "hello"
    assert "".join(chunk.text for chunk in guard_model(streaming, [KEY]).stream("hi")) == "hel"
    # A model that cannot stream is not taken for one that can: langchain-core tells by
    # whether the method is the base class's.
    assert type(guarded)._stream is BaseChatModel._stream
    assert type(guarded)._astream is BaseChatModel._astream


def test_guarding_again_changes_the_keys_and_not_the_class() -> None:
    model = guard_model(Repeats(says=f"the key {KEY} is not allowed"), [KEY])
    guarding = type(model)

    guard_model(model, ["sk-another-key-0002"])

    assert type(model) is guarding
    with pytest.raises(ProviderError):
        model.invoke("hello")  # the first key is no longer one of this model's
    assert isinstance(model, Repeats)
    model.says = "the key sk-another-key-0002 is not allowed"
    with pytest.raises(ModelError) as raised:
        model.invoke("hello")
    assert str(raised.value) == f"the key {REDACTED} is not allowed"
