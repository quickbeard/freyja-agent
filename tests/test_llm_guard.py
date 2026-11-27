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


# Feature 011: every request to the provider passes through the guarded model, so it is also
# where telemetry is told of one that nothing else records (``runtime/telemetry.py``).


class _Recorded:
    """What stands for telemetry: every request it was asked about, and how each ended."""

    def __init__(self, records: bool = True) -> None:
        self.records = records
        self.asked = 0
        self.entered = 0
        self.ended: list[BaseException | None] = []
        # As telemetry does: a request made inside one that is the call in progress is
        # recorded by that one, and gets nothing of its own.
        self.inside = False

    def __call__(self) -> Any:
        self.asked += 1
        return self if self.records and not self.inside else None

    def enter(self) -> None:
        self.entered += 1
        self.inside = True

    def end(self, error: BaseException | None = None) -> None:
        self.inside = False
        self.ended.append(error)


async def test_each_of_the_four_ways_to_ask_tells_telemetry_of_the_request() -> None:
    recorded = _Recorded()
    model = guard_model(Streams(), [KEY], recorded)

    model.invoke("go")
    await model.ainvoke("go")
    assert [chunk.text for chunk in model.stream("go")]
    assert [chunk.text async for chunk in model.astream("go")]

    # ``Streams`` has ``_generate``, ``_stream``, and ``_astream`` of its own; ``ainvoke``
    # reaches ``_generate`` through the base class.
    assert recorded.asked == 4 and recorded.ended == [None] * 4
    # Only a request that is awaited in one piece may be made the call in progress: a
    # generator would set it across a ``yield``.
    assert recorded.entered == 2


async def test_telemetry_is_told_the_error_that_is_raised_which_holds_no_key() -> None:
    recorded = _Recorded()
    failing = guard_model(Repeats(says=f"401: {KEY}"), [KEY], recorded)
    streaming = guard_model(Streams(says="the endpoint is down"), [KEY], recorded)

    with pytest.raises(ModelError):
        failing.invoke("go")
    with pytest.raises(ProviderError):
        [chunk async for chunk in streaming.astream("go")]

    first, second = recorded.ended
    assert isinstance(first, ModelError) and KEY not in str(first)
    assert isinstance(second, ProviderError)


async def test_a_stream_that_is_left_is_ended_for_telemetry_too() -> None:
    recorded = _Recorded()
    model = guard_model(Streams(), [KEY], recorded)

    stream: Any = model._astream([])
    await stream.__anext__()
    await stream.aclose()

    assert len(recorded.ended) == 1 and isinstance(recorded.ended[0], GeneratorExit)


def test_a_request_telemetry_does_not_record_and_a_model_with_no_telemetry_ask_as_before() -> None:
    elsewhere = _Recorded(records=False)

    assert guard_model(Repeats(), [KEY], elsewhere).invoke("go").text == "hello"
    assert guard_model(Repeats(), [KEY]).invoke("go").text == "hello"
    assert elsewhere.asked == 1 and elsewhere.entered == 0 and elsewhere.ended == []


class Awaits(Repeats):
    """A model with an ``_agenerate`` of its own, as a provider's client has."""

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        if self.says is not None:
            raise ProviderError(self.says)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="hello"))])


async def test_a_model_that_is_awaited_raises_without_the_key_and_tells_telemetry_so() -> None:
    recorded = _Recorded()
    held = guard_model(Awaits(says=f"401: {KEY}"), [KEY], recorded)
    plain = guard_model(Awaits(says="the endpoint is down"), [KEY], recorded)

    with pytest.raises(ModelError) as cleaned:
        await held.ainvoke("go")
    with pytest.raises(ProviderError):
        await plain.ainvoke("go")
    assert (await guard_model(Awaits(), [KEY], recorded).ainvoke("go")).text == "hello"

    assert KEY not in str(cleaned.value)
    first, second, third = recorded.ended
    assert isinstance(first, ModelError) and isinstance(second, ProviderError) and third is None
    assert recorded.entered == 3
