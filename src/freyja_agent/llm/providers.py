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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Literal

from deepagents.profiles.provider.provider_profiles import apply_provider_profile
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import SecretStr

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
    def key_field(self) -> str:
        """The field in which the provider's client holds the key it will send."""
        return f"{self.id}_api_key"

    @property
    def base_url_field(self) -> str:
        """The field in which the provider's client holds the address it will use."""
        return self.base_url_variable.lower()

    @property
    def variable_prefix(self) -> str:
        """What the names of the variables the provider's client reads begin with."""
        return self.key_variable.partition("_")[0] + "_"

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
# Why a profile cannot be used on this instance, as the code the list shows (FR-011a).
UnusableReason = Literal[
    "key_unreadable", "secret_not_configured", "not_configured", "cannot_build"
]


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


def _is_the_deployments(name: str) -> bool:
    """Whether a provider's variable says something of the deployment, not of its own model."""
    return name.endswith("_PROXY") or "TELEMETRY" in name


@contextmanager
def _own_variables_hidden(provider: Provider) -> Iterator[None]:
    """Take the provider's variables out of the process environment while a client is built.

    A provider's client reads many variables of its own beside the key and the address:
    headers to add to every request, an organization, a project. Its parameters cannot switch
    all of them off, and a later version may read more. Hidden, none of them reaches a client
    that is not the environment's. Two kinds stay, because they are the deployment's and not
    its own model's: a proxy, which like ``HTTP_PROXY`` is how the deployment reaches the
    outside, and what says whether a client may report traces, which a deployment that
    turned it off turned off for every client.

    The environment is the process's, so this holds only because nothing else reads these
    variables meanwhile: a client is built in one go on the event loop's thread, with no
    ``await`` in between, and the environment's own client is built and called on that thread
    too.
    """
    hidden = {
        name: os.environ.pop(name)
        for name in list(os.environ)
        if name.startswith(provider.variable_prefix) and not _is_the_deployments(name)
    }
    try:
        yield
    finally:
        os.environ.update(hidden)


def _settled(model: BaseChatModel) -> BaseChatModel:
    """Have ``model`` make now what it would otherwise make at its first call.

    Some clients build their connection objects lazily, and read the environment when they
    do. Made here, they are made while the provider's variables are hidden.
    """
    for cls in type(model).__mro__:
        for name, value in vars(cls).items():
            if isinstance(value, cached_property):
                getattr(model, name)
    return model


def build_model(spec: ModelSpec) -> BaseChatModel:
    """The chat model for a spec. Opens no connection.

    A spec with a key is a saved profile's, or unsaved values under test: its client is built
    with the provider's own variables hidden, so that the profile alone says where requests
    go and what travels with them (FR-004). The environment's own model, a spec with no key,
    is built in the environment as it is, as it always was (FR-023).
    """
    provider = PROVIDERS.get(spec.provider)
    if spec.api_key is None or provider is None:
        return init_chat_model(spec.name, **model_arguments(spec))
    with _own_variables_hidden(provider):
        return _settled(init_chat_model(spec.name, **model_arguments(spec)))


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
    unusable_reason: UnusableReason | None
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


@dataclass(frozen=True)
class EnvironmentAccess:
    """How the environment's model is reached: the key that is sent and where. Never shown."""

    # The credential a request carries, which is what the card hints at.
    key: str = field(default="", repr=False)
    # None when it is the provider's own address.
    base_url: str | None = None
    # Every credential to take out of text about this model: the one that is sent, and the
    # key the client holds or the environment names, where that is another.
    secrets: tuple[str, ...] = field(default=(), repr=False)


def environment_access(
    spec: ModelSpec, built: object = None, environ: Mapping[str, str] | None = None
) -> EnvironmentAccess:
    """The key and the address the environment's model is reached with (FR-008, FR-011a).

    ``built`` is the environment's client, when there is one to look at. What it holds is
    what will be used, which the variables alone do not always tell: with the LangSmith
    gateway on, the client goes to the gateway with the gateway's key, whatever the
    provider's variables say, and with no provider key at all. Where the client says nothing,
    or there is none, the provider's own variables are read, the first that is set of those
    the client reads.
    """
    provider = PROVIDERS.get(spec.provider)
    if provider is None:
        return EnvironmentAccess()
    environ = os.environ if environ is None else environ
    held = getattr(built, provider.key_field, None)
    key = held.get_secret_value() if isinstance(held, SecretStr) else ""
    key = key or environment_key(spec, environ)
    address = getattr(built, provider.base_url_field, None)
    if not isinstance(address, str) or not address:
        address = next(
            (environ[name] for name in provider.base_url_variables if environ.get(name)), None
        )
    if address is not None and address.rstrip("/") == provider.address.rstrip("/"):
        address = None
    # A provider's SDK puts headers from its environment over the client's own key, so the
    # credential a request carries may be another than the key the client holds.
    sent = _credentials_sent(built)
    known = (key, environment_key(spec, environ))
    return EnvironmentAccess(
        key=key if key in sent or not sent else sent[0],
        base_url=address,
        secrets=tuple(dict.fromkeys(value for value in (*sent, *known) if value)),
    )


# Where a client keeps the SDK clients it sends through, and the headers a credential is in.
_SDK_CLIENTS = ("root_async_client", "root_client", "_async_client", "_client")
_CREDENTIAL_HEADERS = ("authorization", "x-api-key")


def _credentials_sent(built: object) -> tuple[str, ...]:
    """The credentials in the headers every request of ``built`` carries, as its SDK says.

    Read from the SDK client's own account of its default headers. A stand-in for a model,
    or a client that keeps its SDK elsewhere, has none to read, and says nothing.
    """
    found: list[str] = []
    for name in _SDK_CLIENTS:
        headers = None
        # No such client on this model, or one that cannot be made: nothing to read.
        with suppress(Exception):
            headers = getattr(built, name).default_headers
        if not isinstance(headers, Mapping):
            continue
        for header, value in headers.items():
            if str(header).lower() in _CREDENTIAL_HEADERS and isinstance(value, str) and value:
                scheme, _, token = value.partition(" ")
                found.append(token if scheme.lower() == "bearer" and token else value)
    return tuple(dict.fromkeys(found))


def environment_profile(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
    factory: Callable[[ModelSpec], BaseChatModel | None] = build_model,
    model: str | None = None,
    access: EnvironmentAccess | None = None,
) -> EnvironmentProfile:
    """Describe the environment's model for the console (FR-008, FR-011a).

    The base URL and the key's last four characters are those the environment's client will
    use (``environment_access``): ``access`` when the caller has looked already, and
    otherwise the client ``factory`` builds here. Usable means that a model can be built and,
    for a provider in the table, that it has a key: one client builds without a key and fails
    only when called.
    """
    spec = environment_spec(settings, model)
    provider = PROVIDERS.get(spec.provider)
    reason: UnusableReason | None = None
    if access is None:
        built = None
        try:
            built = factory(spec)
        except Exception:  # whatever the client raises, the model cannot be built
            reason = "cannot_build"
        access = environment_access(spec, built, environ)
    elif provider is None or access.key:
        try:
            factory(spec)
        except Exception:
            reason = "cannot_build"
    if provider is not None and not access.key:
        reason = "not_configured"
    key, base_url = access.key, access.base_url
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
