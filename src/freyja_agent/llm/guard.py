"""A model whose errors never hold its profile's key (feature 004, FR-020, FR-021).

A provider may repeat the key it was sent in its error. That error does not only reach the run
loop, which cleans what it logs and sends: LangGraph gives a failed step's error to the debug
stream and saves it with the step's writes in the checkpointer. So the key is taken out where
the error is raised, in the model itself, before anything else is given it.
"""

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from freyja_agent.llm.secrets import redact

# Where a guarded model keeps the keys to take out, set on the instance.
_SECRETS = "_freyja_secrets"
# One guarding class per model class, so that a rebuild does not make another.
_guarded: dict[type[BaseChatModel], type[BaseChatModel]] = {}


class ModelError(RuntimeError):
    """A chat model's error, in the provider's words with the profile's key taken out.

    It stands in for the provider's own exception only when that one held a key; any other
    error is raised as it came.
    """


def _cleaned(exc: Exception, model: BaseChatModel) -> ModelError | None:
    """The error to raise in place of ``exc``, or None when ``exc`` holds no key."""
    secrets = getattr(model, _SECRETS, ())
    text, shown = str(exc), repr(exc)
    clean = redact(text, secrets)
    if clean == text and redact(shown, secrets) == shown:
        return None
    return ModelError(clean)


def _guarding(cls: type[BaseChatModel]) -> type[BaseChatModel]:
    """A subclass of ``cls`` that raises its errors without the instance's keys.

    It overrides only the methods ``cls`` has of its own: langchain-core decides whether a
    model can stream by whether ``_stream`` and ``_astream`` are the base class's.
    """

    def _generate(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return cls._generate(self, *args, **kwargs)
        except Exception as exc:
            cleaned = _cleaned(exc, self)
            if cleaned is None:
                raise
        # Raised outside the handler, so the provider's error is not kept as its context.
        raise cleaned

    async def _agenerate(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await cls._agenerate(self, *args, **kwargs)
        except Exception as exc:
            cleaned = _cleaned(exc, self)
            if cleaned is None:
                raise
        raise cleaned

    def _stream(self: Any, *args: Any, **kwargs: Any) -> Iterator[Any]:
        try:
            yield from cls._stream(self, *args, **kwargs)
        except Exception as exc:
            cleaned = _cleaned(exc, self)
            if cleaned is None:
                raise
        else:
            return
        raise cleaned

    async def _astream(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        try:
            async for chunk in cls._astream(self, *args, **kwargs):
                yield chunk
        except Exception as exc:
            cleaned = _cleaned(exc, self)
            if cleaned is None:
                raise
        else:
            return
        raise cleaned

    overrides: dict[str, Any] = {"_generate": _generate}
    for name, method in (("_agenerate", _agenerate), ("_stream", _stream), ("_astream", _astream)):
        if getattr(cls, name) is not getattr(BaseChatModel, name):
            overrides[name] = method
    # The same name and module as the model's own class: whatever tells providers apart by
    # their class still does.
    return type(
        cls.__name__, (cls,), {"__module__": cls.__module__, "__doc__": cls.__doc__, **overrides}
    )


def guard_model(model: BaseChatModel, secrets: Sequence[str]) -> BaseChatModel:
    """Make ``model`` raise its errors with ``secrets`` taken out, and return it.

    The model stays the object it was, of a subclass of its own class, so it is called, bound
    to tools, and recognized by its provider exactly as before. An error that holds none of
    the secrets is raised unchanged.
    """
    cls = type(model)
    if cls not in _guarded.values():
        guarding = _guarded.get(cls)
        if guarding is None:
            guarding = _guarded[cls] = _guarding(cls)
        model.__class__ = guarding
    object.__setattr__(model, _SECRETS, tuple(secrets))
    return model
