"""004 FR-004, FR-008, FR-011a: providers, building a model, and the environment's profile (R3).

No client is ever called: building one opens no connection.
"""

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
    environment_key,
    environment_profile,
    environment_spec,
)
from tests.fakes import ScriptedChatModel

KEY = "sk-recognizable-key-0001"
PROVIDER_VARIABLES = (
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_BASE",
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
