"""Providers, building a model from a profile, and the environment's profile (004, R3, R8).

The provider table is the only place a provider's names appear: its id, the label the console
shows, and the two variables its own client reads. A saved profile uses one of the three; the
environment's profile may carry any prefix LangChain accepts.

``build_model`` turns a ``ModelSpec`` into a chat model the way the factory resolves the
environment's model: through ``init_chat_model`` with Deep Agents' provider profile, so a saved
profile and the environment's model are built alike (001 research R3). A spec with a key and a
base URL reads nothing from the environment.
"""

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from deepagents.profiles.provider.provider_profiles import apply_provider_profile
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult

from freyja_agent.config import Settings
from freyja_agent.llm.secrets import key_hint

# The argument Deep Agents' provider profile sets for ``openai:`` models.
RESPONSES_API = "use_responses_api"

# The environment's profile: never stored, and its id and name are reserved (FR-008).
ENVIRONMENT_ID = "environment"
ENVIRONMENT_NAME = "Environment"


@dataclass(frozen=True)
class Provider:
    """One provider a saved profile may use (data-model.md "Provider")."""

    id: str
    label: str
    key_variable: str
    base_url_variable: str
    # The provider's own service: where a saved profile with no base URL is sent. It is what
    # the provider's client uses when it is given no address and its variables are unset, and
    # it is a fact about the provider, like its name, not something a deployment configures.
    address: str
    # Whether the provider offers the Responses API beside chat completions.
    has_api_choice: bool = False
    # Another name under which the provider's client reads a base URL, after the first.
    other_base_url_variable: str | None = None

    @property
    def base_url_variables(self) -> tuple[str, ...]:
        """The variables the client takes an address from, in the order it reads them."""
        if self.other_base_url_variable is None:
            return (self.base_url_variable,)
        return (self.base_url_variable, self.other_base_url_variable)


PROVIDERS: dict[str, Provider] = {
    provider.id: provider
    for provider in (
        Provider(
            "openai",
            "OpenAI-compatible",
            "OPENAI_API_KEY",
            "OPENAI_API_BASE",
            "https://api.openai.com/v1",
            has_api_choice=True,
            other_base_url_variable="OPENAI_BASE_URL",
        ),
        Provider(
            "anthropic",
            "Anthropic",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_API_URL",
            "https://api.anthropic.com",
            other_base_url_variable="ANTHROPIC_BASE_URL",
        ),
        Provider(
            "openrouter",
            "OpenRouter",
            "OPENROUTER_API_KEY",
            "OPENROUTER_API_BASE",
            "https://openrouter.ai/api/v1",
        ),
    )
}


@dataclass(frozen=True)
class ModelSpec:
    """Everything needed to reach one model. The key never appears in its ``repr``."""

    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    # True: the Responses API where the provider profile turns it on. False: chat completions.
    responses_api: bool = True
    # How often the client tries again by itself. None leaves the client's own default; a
    # profile's test asks for none, so that its time limit is a limit (research R7).
    max_retries: int | None = None

    @property
    def name(self) -> str:
        """The LangChain model string: ``provider:model``, or the model alone without one."""
        return f"{self.provider}:{self.model}" if self.provider else self.model


# The seam the tests swap: what turns a spec into a chat model (research R18).
ModelFactory = Callable[[ModelSpec], BaseChatModel]


def model_arguments(spec: ModelSpec) -> dict[str, Any]:
    """The arguments a spec's client is built with.

    The API choice only changes what the provider profile set, so it cannot hand another
    provider's client an argument it does not know (001 research R3).

    A spec with a key is a saved profile's, or unsaved values under test: it is reached with
    that key at its own base URL, and with no base URL at its provider's own address. The
    address is passed, never left to the client, which would take one from the environment's
    variables and send an operator's key to an endpoint they did not choose (FR-004). A spec
    with no key is the environment's: the client reads its own variables, as it always has.
    """
    arguments = dict(apply_provider_profile(spec.name))
    if not spec.responses_api and RESPONSES_API in arguments:
        arguments[RESPONSES_API] = False
    provider = PROVIDERS.get(spec.provider)
    if spec.api_key is not None:
        arguments["api_key"] = spec.api_key
    if spec.base_url:
        arguments["base_url"] = spec.base_url
    elif spec.api_key is not None and provider is not None:
        arguments["base_url"] = provider.address
    if spec.max_retries is not None:
        arguments["max_retries"] = spec.max_retries
    return arguments


def build_model(spec: ModelSpec) -> BaseChatModel:
    """The chat model for a spec. Opens no connection."""
    return init_chat_model(spec.name, **model_arguments(spec))


def environment_spec(settings: Settings, model: str | None = None) -> ModelSpec:
    """What the environment configures: ``FREYJA_MODEL`` split at its first colon.

    It has no key and no base URL of its own: the provider's client reads its variables
    itself, as it always has. ``model`` stands in for the setting when a caller passes one.
    """
    provider, colon, name = (model or settings.model).partition(":")
    if not colon:
        provider, name = "", provider
    return ModelSpec(provider=provider, model=name, responses_api=settings.openai_responses_api)


@dataclass(frozen=True)
class EnvironmentProfile:
    """The environment's model as a read-only profile (data-model.md)."""

    provider: str
    model: str
    base_url: str | None
    key_hint: str
    # None for a provider with no such choice.
    responses_api: bool | None
    usable: bool
    # ``not_configured``: the provider's key is not set. ``cannot_build``: no model can be
    # built from the values. None when usable.
    unusable_reason: str | None
    id: str = ENVIRONMENT_ID
    name: str = ENVIRONMENT_NAME


def environment_key(spec: ModelSpec, environ: Mapping[str, str] | None = None) -> str:
    """The key the environment gives the provider of ``spec``, or "" when it gives none.

    A provider's key and base URL are its client's own variables, not ``FREYJA_*`` settings:
    the client reads them itself, as it always has, and ``Settings`` never holds them. The
    service looks at them to describe the environment's profile, which means its hint, its
    base URL, whether it can be used (FR-011a), and the key to take out of its errors
    (FR-021). This module is the one place that reads them.
    """
    provider = PROVIDERS.get(spec.provider)
    environ = os.environ if environ is None else environ
    return environ.get(provider.key_variable, "") if provider else ""


def environment_profile(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
    factory: ModelFactory = build_model,
    model: str | None = None,
) -> EnvironmentProfile:
    """Describe the environment's model for the console (FR-008, FR-011a).

    The base URL and the key's last four characters come from the provider's own variables
    (``environment_key``). Usable means that a model can be built and, for a provider in the
    table, that its key is set: one client builds without a key and fails only when called.
    """
    environ = os.environ if environ is None else environ
    spec = environment_spec(settings, model)
    provider = PROVIDERS.get(spec.provider)
    key = environment_key(spec, environ)
    # The first of the client's variables that is set, which is the one the client uses.
    base_url = next(
        (
            environ[name]
            for name in (provider.base_url_variables if provider else ())
            if environ.get(name)
        ),
        None,
    )
    reason = None
    if provider is not None and not key:
        reason = "not_configured"
    else:
        try:
            factory(spec)
        except Exception:  # whatever the client raises, the model cannot be built
            reason = "cannot_build"
    return EnvironmentProfile(
        provider=spec.provider,
        model=spec.model,
        base_url=base_url,
        key_hint=key_hint(key),
        responses_api=spec.responses_api if provider and provider.has_api_choice else None,
        usable=reason is None,
        unusable_reason=reason,
    )


class NoUsableModelError(RuntimeError):
    """No usable model is active, so nothing can be asked of one (FR-016)."""


class UnusableChatModel(BaseChatModel):
    """Stands where a model would be when none is usable (research R8).

    The orchestrator's graph needs a model to be built at all, and the thread routes need the
    graph. This one binds any tools and refuses to answer; a run is turned away before it is
    ever called.
    """

    reason: str = "no usable model is active"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "UnusableChatModel":
        # Nothing to bind to: whatever tools are offered, the answer is the same refusal.
        del tools, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # The signature is the base class's; none of it changes the refusal.
        del messages, stop, run_manager, kwargs
        raise NoUsableModelError(self.reason)

    @property
    def _llm_type(self) -> str:
        return "unusable"
