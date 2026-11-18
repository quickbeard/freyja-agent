"""004 FR-004, FR-008, FR-011a: providers, building a model, and the environment's profile (R3).

No client is ever called: building one opens no connection.
"""

import importlib
import os
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from freyja_agent.config import Settings
from freyja_agent.llm.providers import (
    ENVIRONMENT_ID,
    ENVIRONMENT_NAME,
    PROVIDERS,
    ModelSpec,
    NoUsableModelError,
    UnusableChatModel,
    build_model,
    environment_access,
    environment_key,
    environment_profile,
    environment_spec,
)
from tests.fakes import ScriptedChatModel

KEY = "sk-recognizable-key-0001"
PROVIDER_VARIABLES = (
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_URL",
    "ANTHROPIC_BASE_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_BASE",
    "LANGSMITH_GATEWAY",
    "LANGSMITH_GATEWAY_API_KEY",
    "LANGSMITH_API_KEY",
)


@pytest.fixture(autouse=True)
def no_provider_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROVIDER_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_the_table_has_the_three_providers_a_profile_may_use() -> None:
    assert list(PROVIDERS) == ["openai", "anthropic", "openrouter"]
    assert [p.has_api_choice for p in PROVIDERS.values()] == [True, False, False]
    assert PROVIDERS["openai"].key_variable == "OPENAI_API_KEY"
    assert PROVIDERS["openai"].base_url_variable == "OPENAI_API_BASE"
    assert PROVIDERS["anthropic"].key_variable == "ANTHROPIC_API_KEY"
    assert PROVIDERS["anthropic"].base_url_variable == "ANTHROPIC_API_URL"
    assert PROVIDERS["openrouter"].key_variable == "OPENROUTER_API_KEY"
    assert PROVIDERS["openrouter"].base_url_variable == "OPENROUTER_API_BASE"


@pytest.mark.parametrize(
    ("provider", "model", "base_url", "attribute"),
    [
        ("openai", "served-model", "http://127.0.0.1:9/v1", "openai_api_base"),
        ("anthropic", "claude-x", "http://127.0.0.1:9", "anthropic_api_url"),
        ("openrouter", "vendor/model", "http://127.0.0.1:9/api/v1", "openrouter_api_base"),
    ],
)
def test_a_model_is_built_from_a_profiles_own_key_and_address(
    provider: str, model: str, base_url: str, attribute: str
) -> None:
    built = build_model(ModelSpec(provider=provider, model=model, base_url=base_url, api_key=KEY))
    assert getattr(built, attribute) == base_url
    assert KEY not in repr(built)


ELSEWHERE = "http://10.9.9.9:31000/v1"
# Every variable from which a provider's client takes an address when it is given none.
ADDRESS_VARIABLES = [
    ("openai", "OPENAI_API_BASE"),
    ("openai", "OPENAI_BASE_URL"),
    ("anthropic", "ANTHROPIC_API_URL"),
    ("anthropic", "ANTHROPIC_BASE_URL"),
    ("openrouter", "OPENROUTER_API_BASE"),
]


def sends_to(model: Any) -> str:
    """Where a built client sends its requests, as the client itself has it."""
    for path in ("root_client.base_url", "_client.base_url", "openrouter_api_base"):
        found: Any = model
        try:
            for name in path.split("."):
                found = getattr(found, name)
        except AttributeError:
            continue
        if found:
            return str(found).rstrip("/")
    raise AssertionError(f"no address found on {type(model).__name__}")


@pytest.mark.parametrize(("provider", "variable"), ADDRESS_VARIABLES)
def test_a_profile_with_no_base_url_is_sent_to_its_providers_own_address(
    provider: str, variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-004: an empty base URL means the provider's own service, whatever the environment
    points its own model at. Otherwise an operator's key would go to an endpoint they did not
    choose."""
    monkeypatch.setenv(variable, ELSEWHERE)
    own = PROVIDERS[provider].address

    built = build_model(ModelSpec(provider=provider, model="m", api_key=KEY))

    assert sends_to(built) == own
    assert "10.9.9.9" not in sends_to(built)
    # A base URL the profile does have is still the one used.
    given = build_model(
        ModelSpec(provider=provider, model="m", api_key=KEY, base_url="http://127.0.0.1:9/v1")
    )
    assert sends_to(given) == "http://127.0.0.1:9/v1"


@pytest.mark.parametrize(("provider", "variable"), ADDRESS_VARIABLES)
def test_the_environments_model_still_goes_where_the_environment_says(
    provider: str, variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-023: the environment's model is built as it always was, from its client's variables."""
    monkeypatch.setenv(variable, ELSEWHERE)
    monkeypatch.setenv(PROVIDERS[provider].key_variable, KEY)

    built = build_model(environment_spec(Settings(model=f"{provider}:m")))

    assert sends_to(built) == ELSEWHERE


def test_each_provider_has_an_address_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    assert {p.id: p.address for p in PROVIDERS.values()} == {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    # With nothing in the environment, each client goes to that same address by itself.
    for provider in PROVIDERS.values():
        if provider.id == "openrouter":
            continue  # its client keeps no address until it is given one
        monkeypatch.setenv(provider.key_variable, KEY)
        built = build_model(environment_spec(Settings(model=f"{provider.id}:m")))
        assert sends_to(built) == provider.address


# The packages whose code decides what a provider's client takes from the environment.
CLIENT_PACKAGES = {
    "openai": ("langchain_openai", "openai"),
    "anthropic": ("langchain_anthropic", "anthropic"),
    "openrouter": ("langchain_openrouter", "openrouter"),
}


def variables_the_client_knows(provider: str) -> set[str]:
    """Every variable of the provider's that its installed client packages name.

    Read from the packages themselves, so a variable a later version starts to read is in
    the test the day the version is installed.
    """
    prefix = PROVIDERS[provider].variable_prefix
    names: set[str] = set()
    for package in CLIENT_PACKAGES[provider]:
        root = Path(importlib.import_module(package).__file__ or "").parent
        for source in root.rglob("*.py"):
            text = source.read_text(encoding="utf-8", errors="ignore")
            names |= set(re.findall(rf"""["']({prefix}[A-Z0-9_]+)["']""", text))
    return names


def marked(name: str) -> str:
    """A value for an environment variable that can be told apart wherever it turns up."""
    mark = f"env-marker-{name.lower().replace('_', '-')}"
    if "TELEMETRY" in name:
        return "false"  # an opt-out, which stays with the deployment (see ``stays``)
    if "HEADERS" in name:
        return f"X-Env-Marker: {mark}\nAuthorization: Bearer {mark}\nX-Api-Key: {mark}"
    if any(part in name for part in ("URL", "BASE", "PROXY", "HOST", "ENDPOINT")):
        return f"http://{mark}.invalid/v1"
    return mark


def stays(name: str) -> bool:
    """Whether a provider's variable is the deployment's, not its own model's: how it reaches
    the outside, and whether it lets the client report traces. Those apply to every client."""
    return name.endswith("_PROXY") or "TELEMETRY" in name


def everything_about(model: Any) -> str:
    """What a built client holds and what it would send, as text: its fields, and the address
    and headers of a request from each of its clients, built and never sent. OpenRouter's
    client builds no such request, so what its SDK was configured with is read instead."""
    parts = [repr(vars(model))]
    configuration = getattr(getattr(model, "client", None), "sdk_configuration", None)
    if configuration is not None:
        security = configuration.security
        parts += [
            f"globals: {configuration.globals!r}",
            f"security: {security() if callable(security) else security!r}",
            f"server: {configuration.server_url!r}",
            f"headers: {dict(configuration.client.headers)!r}",
            f"async headers: {dict(configuration.async_client.headers)!r}",
        ]
    for name in ("root_client", "root_async_client", "_client", "_async_client"):
        client = getattr(model, name, None)
        if client is None or not hasattr(client, "_build_request"):
            continue
        models = importlib.import_module(type(client).__module__.split(".")[0] + "._models")
        options = models.FinalRequestOptions.construct(method="post", url="/x", json_data={})
        request = client._build_request(options)
        parts.append(f"{name}: {request.url} {dict(request.headers)!r}")
    return "\n".join(parts)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter"])
@pytest.mark.parametrize("base_url", [None, "http://127.0.0.1:9/v1"])
def test_a_saved_profiles_client_takes_nothing_from_its_providers_environment(
    provider: str, base_url: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-004: a profile is its provider, its model, its base URL, and its key, and nothing
    the environment has set for its own model reaches the profile's endpoint: no header, no
    credential, no organization. Every variable the installed client names is set here."""
    known = variables_the_client_knows(provider)
    assert len(known) >= 2, known
    for name in known:
        monkeypatch.setenv(name, marked(name))

    built = build_model(ModelSpec(provider=provider, model="m", api_key=KEY, base_url=base_url))
    found = everything_about(built)

    own = {name for name in known if not stays(name)}
    reached = {name for name in own if marked(name).split("\n")[0].split(": ")[-1] in found}
    assert reached == set()
    # Nothing marked is left once what stays is set aside, whatever it was called.
    for name in known - own:
        found = found.replace(marked(name), "")
    assert "env-marker" not in found
    # The credential that travels is the profile's own.
    assert KEY in found
    assert sends_to(built) == (base_url or PROVIDERS[provider].address)
    # And the environment is as it was found.
    assert {name: os.environ.get(name) for name in known} == {n: marked(n) for n in known}


def test_a_deployments_telemetry_opt_out_holds_for_a_saved_profile_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whether a client reports traces is the deployment's choice, like its proxy. Hidden
    with the rest, a saved profile's client would report what the deployment turned off."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("ANTHROPIC_OPEN_TELEMETRY", "false")
    monkeypatch.setenv("ANTHROPIC_OPEN_TELEMETRY_PROPAGATION", "false")

    environment = build_model(environment_spec(Settings(model="anthropic:m")))
    saved = build_model(ModelSpec(provider="anthropic", model="m", api_key=KEY))

    chosen = environment._async_client._open_telemetry
    assert chosen["propagation"] is False
    assert chosen["traces"]["enabled"] is False
    assert saved._async_client._open_telemetry == chosen
    assert saved._client._open_telemetry == chosen


def test_the_environments_own_model_still_takes_its_clients_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-023: what the environment sets for its own model is still its client's to read."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_ORG_ID", "org-of-the-environment")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Env: of-the-environment")

    found = everything_about(build_model(environment_spec(Settings(model="openai:m"))))

    assert "org-of-the-environment" in found
    assert "of-the-environment" in found


def test_a_spec_never_shows_its_key() -> None:
    spec = ModelSpec(provider="openai", model="m", api_key=KEY)
    assert KEY not in repr(spec)
    assert spec.name == "openai:m"


def test_the_api_choice_changes_only_what_the_provider_profile_set() -> None:
    on = build_model(ModelSpec(provider="openai", model="m", api_key=KEY))
    off = build_model(ModelSpec(provider="openai", model="m", api_key=KEY, responses_api=False))
    assert on.use_responses_api is True
    assert off.use_responses_api is False
    # A provider with no such choice is built the same either way.
    build_model(ModelSpec(provider="anthropic", model="m", api_key=KEY, responses_api=False))


def test_the_environments_profile_is_the_model_setting_split_at_its_first_colon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    settings = Settings(model="openrouter:anthropic/claude:beta")
    profile = environment_profile(settings)
    assert (profile.id, profile.name) == (ENVIRONMENT_ID, ENVIRONMENT_NAME)
    assert (profile.provider, profile.model) == ("openrouter", "anthropic/claude:beta")
    assert environment_spec(settings).name == "openrouter:anthropic/claude:beta"


def test_the_environments_profile_shows_the_providers_own_address_and_key_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_API_BASE", "http://10.0.0.5:31000/v1")
    # Another provider's variables are not this profile's.
    monkeypatch.setenv("ANTHROPIC_API_URL", "http://elsewhere")
    profile = environment_profile(Settings(model="openai:served-model"))
    assert profile.base_url == "http://10.0.0.5:31000/v1"
    assert profile.key_hint == "0001"
    assert profile.usable is True
    assert profile.unusable_reason is None
    assert KEY not in repr(profile)


def test_the_environments_key_is_its_providers_own_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one place the service reads a provider's variable itself (constitution IV)."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert environment_key(ModelSpec(provider="openai", model="m")) == KEY
    assert environment_key(ModelSpec(provider="anthropic", model="m")) == ""
    # A provider outside the table has no variable the service knows of.
    assert environment_key(ModelSpec(provider="mistralai", model="m")) == ""
    # What a caller hands over stands in for the process environment.
    assert environment_key(ModelSpec(provider="openai", model="m"), {"OPENAI_API_KEY": "k"}) == "k"


@pytest.mark.parametrize(
    ("model", "variables", "shown"),
    [
        # The client's other name for the same thing.
        ("openai:m", {"OPENAI_BASE_URL": ELSEWHERE}, ELSEWHERE),
        ("anthropic:m", {"ANTHROPIC_BASE_URL": ELSEWHERE}, ELSEWHERE),
        # With both set, the one the client reads first.
        (
            "openai:m",
            {"OPENAI_API_BASE": "http://first/v1", "OPENAI_BASE_URL": ELSEWHERE},
            "http://first/v1",
        ),
        (
            "anthropic:m",
            {"ANTHROPIC_API_URL": "http://first", "ANTHROPIC_BASE_URL": ELSEWHERE},
            "http://first",
        ),
    ],
)
def test_the_environments_profile_shows_the_address_its_client_will_use(
    model: str, variables: dict[str, str], shown: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-008: the card shows the values the service was started with."""
    provider = model.partition(":")[0]
    monkeypatch.setenv(PROVIDERS[provider].key_variable, KEY)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)

    profile = environment_profile(Settings(model=model))

    assert profile.base_url == shown
    assert sends_to(build_model(environment_spec(Settings(model=model)))) == shown.rstrip("/")


GATEWAY_KEY = "ls-gateway-key-7777"


@pytest.mark.parametrize(
    ("model", "address"),
    [
        ("openai:m", "https://gateway.smith.langchain.com/openai/v1"),
        ("anthropic:m", "https://gateway.smith.langchain.com/anthropic"),
    ],
)
def test_an_environment_that_reaches_its_model_through_the_gateway_alone_is_usable(
    model: str, address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-023: such a deployment ran before this feature, with no provider key at all. Its
    client is built with the gateway's key, so that is the key the environment has."""
    monkeypatch.setenv("LANGSMITH_GATEWAY", "true")
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", GATEWAY_KEY)
    settings = Settings(model=model)

    profile = environment_profile(settings)

    assert (profile.usable, profile.unusable_reason) == (True, None)
    assert profile.key_hint == "7777"
    assert profile.base_url == address
    built = build_model(environment_spec(settings))
    assert sends_to(built) == address
    assert environment_access(environment_spec(settings), built).key == GATEWAY_KEY
    assert GATEWAY_KEY not in repr(profile)
    assert GATEWAY_KEY not in repr(environment_access(environment_spec(settings), built))


def test_the_environments_profile_is_described_by_what_its_client_will_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-008: with the gateway on, the client goes to the gateway whatever the SDK's own
    variable says, and sends the gateway's key. The card and the key to take out of an error
    follow the client, not the variables."""
    monkeypatch.setenv("LANGSMITH_GATEWAY", "true")
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", GATEWAY_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", ELSEWHERE)
    settings = Settings(model="openai:m")
    built = build_model(environment_spec(settings))

    profile = environment_profile(settings)
    access = environment_access(environment_spec(settings), built)

    assert sends_to(built) == "https://gateway.smith.langchain.com/openai/v1"
    assert profile.base_url == "https://gateway.smith.langchain.com/openai/v1"
    assert access.key == GATEWAY_KEY
    assert profile.key_hint == "7777"


@pytest.mark.parametrize(
    ("model", "headers_variable", "header"),
    [
        ("openai:m", "OPENAI_CUSTOM_HEADERS", "Authorization: Bearer real-credential-9999"),
        ("anthropic:m", "ANTHROPIC_CUSTOM_HEADERS", "X-Api-Key: real-credential-9999"),
    ],
)
def test_the_environments_credential_is_the_one_its_client_will_send(
    model: str, headers_variable: str, header: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-008, FR-021: a provider's SDK puts headers from its environment over the client's
    own key. Where they carry the credential, that is the one a request has, so that is the
    one the card hints at and the one to take out of an error."""
    provider = model.partition(":")[0]
    monkeypatch.setenv(PROVIDERS[provider].key_variable, "placeholder-key-0000")
    monkeypatch.setenv(headers_variable, header)
    settings = Settings(model=model)
    built = build_model(environment_spec(settings))
    assert "real-credential-9999" in everything_about(built)

    access = environment_access(environment_spec(settings), built)
    profile = environment_profile(settings)

    assert access.key == "real-credential-9999"
    assert profile.key_hint == "9999"
    # Both are kept out of text: the one that is sent, and the one the client holds.
    assert set(access.secrets) == {"real-credential-9999", "placeholder-key-0000"}
    assert "real-credential" not in repr(access)
    assert "real-credential" not in repr(profile)


def test_with_no_such_headers_the_environments_credential_is_its_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    spec = environment_spec(Settings(model="openai:m"))

    access = environment_access(spec, build_model(spec))

    assert (access.key, access.secrets) == (KEY, (KEY,))
    assert environment_access(ModelSpec(provider="mistralai", model="m")).secrets == ()


def test_without_a_client_to_look_at_the_environment_is_described_from_its_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_API_BASE", ELSEWHERE)
    spec = environment_spec(Settings(model="openai:m"))

    assert environment_access(spec).key == KEY
    assert environment_access(spec).base_url == ELSEWHERE
    # A stand-in for a model has none of a client's fields, and changes nothing.
    assert environment_access(spec, ScriptedChatModel()) == environment_access(spec)
    # The provider's own address is no base URL to show.
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1/")
    assert environment_access(spec).base_url is None


@pytest.mark.parametrize(("setting", "expected"), [(True, True), (False, False)])
def test_the_environments_api_choice_is_the_setting_for_an_openai_model_only(
    monkeypatch: pytest.MonkeyPatch, setting: bool, expected: bool
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    openai = environment_profile(Settings(model="openai:m", openai_responses_api=setting))
    anthropic = environment_profile(Settings(model="anthropic:m", openai_responses_api=setting))
    assert openai.responses_api is expected
    assert anthropic.responses_api is None


@pytest.mark.parametrize("model", ["openai:m", "anthropic:m", "openrouter:vendor/m"])
def test_the_environments_profile_is_not_usable_without_its_providers_key(model: str) -> None:
    profile = environment_profile(Settings(model=model))
    assert profile.usable is False
    assert profile.unusable_reason == "not_configured"
    assert profile.key_hint == ""


def test_the_environments_profile_is_not_usable_when_its_model_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)

    def cannot_build(_spec: ModelSpec) -> ScriptedChatModel:
        raise ValueError(f"no such model, key {KEY}")

    profile = environment_profile(Settings(model="openai:m"), factory=cannot_build)
    assert profile.usable is False
    assert profile.unusable_reason == "cannot_build"


def test_a_prefix_outside_the_table_is_shown_as_written_with_no_key_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    profile = environment_profile(
        Settings(model="someprovider:some-model"), factory=lambda _spec: ScriptedChatModel()
    )
    assert (profile.provider, profile.model) == ("someprovider", "some-model")
    assert profile.key_hint == ""
    assert profile.base_url is None
    assert profile.responses_api is None
    assert profile.usable is True


def test_a_model_setting_with_no_prefix_is_kept_whole() -> None:
    settings = Settings(model="some-model")
    profile = environment_profile(settings, factory=lambda _spec: ScriptedChatModel())
    assert (profile.provider, profile.model) == ("", "some-model")
    assert environment_spec(settings).name == "some-model"


async def test_the_placeholder_model_binds_tools_and_refuses_to_answer() -> None:
    model = UnusableChatModel(reason="its key cannot be read")
    assert model.bind_tools([]) is model
    with pytest.raises(NoUsableModelError, match="its key cannot be read"):
        await model.ainvoke([HumanMessage("hello")])
