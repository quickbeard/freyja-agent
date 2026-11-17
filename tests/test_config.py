import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from freyja_agent.config import Settings


def test_defaults_bind_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FREYJA_HOST", raising=False)
    monkeypatch.delenv("FREYJA_PORT", raising=False)
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREYJA_HOST", "10.0.0.5")
    monkeypatch.setenv("FREYJA_PORT", "9000")
    settings = Settings()
    assert settings.host == "10.0.0.5"
    assert settings.port == 9000


def test_database_uri_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FREYJA_DATABASE_URI")
    with pytest.raises(ValidationError, match="database_uri"):
        Settings()


def test_feature_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FREYJA_WORKSPACE_DIR", "FREYJA_MAX_STEPS", "FREYJA_RUN_LOCK_TTL_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.workspace_dir == "workspace"
    assert settings.max_steps == 50
    assert settings.run_lock_ttl_seconds == 60


def test_feature_settings_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREYJA_WORKSPACE_DIR", "/srv/freyja")
    monkeypatch.setenv("FREYJA_MAX_STEPS", "7")
    monkeypatch.setenv("FREYJA_RUN_LOCK_TTL_SECONDS", "15")
    settings = Settings()
    assert settings.workspace_dir == "/srv/freyja"
    assert settings.max_steps == 7
    assert settings.run_lock_ttl_seconds == 15


def test_an_openai_model_uses_the_responses_api_unless_told_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FREYJA_OPENAI_RESPONSES_API", raising=False)
    assert Settings().openai_responses_api is True


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("0", False), ("true", True)])
def test_the_responses_api_setting_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("FREYJA_OPENAI_RESPONSES_API", value)
    assert Settings().openai_responses_api is expected


def test_the_responses_api_setting_must_be_true_or_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREYJA_OPENAI_RESPONSES_API", "chat")
    with pytest.raises(ValidationError, match="openai_responses_api"):
        Settings()


MCP_VARS = (
    "FREYJA_MCP_SERVERS",
    "FREYJA_MCP_CALL_TIMEOUT_SECONDS",
    "FREYJA_MCP_STARTUP_TIMEOUT_SECONDS",
)


def test_mcp_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MCP_VARS:
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.mcp_servers == {}
    assert settings.mcp_call_timeout_seconds == 30
    assert settings.mcp_startup_timeout_seconds == 20


def test_mcp_settings_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "FREYJA_MCP_SERVERS",
        '{"git": {"command": "uvx", "args": ["mcp-server-git"], "read_only": ["git_status"]}}',
    )
    monkeypatch.setenv("FREYJA_MCP_CALL_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("FREYJA_MCP_STARTUP_TIMEOUT_SECONDS", "5")
    settings = Settings()
    assert list(settings.mcp_servers) == ["git"]
    assert settings.mcp_servers["git"].command == "uvx"
    assert settings.mcp_servers["git"].args == ["mcp-server-git"]
    assert settings.mcp_servers["git"].read_only == ["git_status"]
    assert settings.mcp_call_timeout_seconds == 2.5
    assert settings.mcp_startup_timeout_seconds == 5


@pytest.mark.parametrize(
    "name", ["FREYJA_MCP_CALL_TIMEOUT_SECONDS", "FREYJA_MCP_STARTUP_TIMEOUT_SECONDS"]
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_mcp_time_limits_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=name.removeprefix("FREYJA_").lower()):
        Settings()


A2A_LIMITS = {
    "FREYJA_A2A_CALL_TIMEOUT_SECONDS": ("a2a_call_timeout_seconds", 30),
    "FREYJA_A2A_STARTUP_TIMEOUT_SECONDS": ("a2a_startup_timeout_seconds", 20),
    "FREYJA_A2A_POLL_INTERVAL_SECONDS": ("a2a_poll_interval_seconds", 10),
    "FREYJA_A2A_TASK_MAX_AGE_SECONDS": ("a2a_task_max_age_seconds", 86400),
    "FREYJA_BACKGROUND_RUN_ATTEMPTS": ("background_run_attempts", 3),
    "FREYJA_BACKGROUND_RETRY_SECONDS": ("background_retry_seconds", 60),
    "FREYJA_BACKGROUND_RUN_CONCURRENCY": ("background_run_concurrency", 4),
}


def test_a2a_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FREYJA_A2A_AGENTS", *A2A_LIMITS):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.a2a_agents == {}
    for field, default in A2A_LIMITS.values():
        assert getattr(settings, field) == default, field


def test_a2a_settings_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "FREYJA_A2A_AGENTS",
        '{"hello": {"url": "http://127.0.0.1:9999", "read_only": true},'
        ' "stub": {"url": "http://localhost:9998"}}',
    )
    monkeypatch.setenv("FREYJA_A2A_CALL_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("FREYJA_A2A_POLL_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("FREYJA_BACKGROUND_RUN_ATTEMPTS", "1")
    settings = Settings()
    assert list(settings.a2a_agents) == ["hello", "stub"]
    assert settings.a2a_agents["hello"].read_only is True
    assert settings.a2a_agents["stub"].read_only is False
    assert settings.a2a_agents["stub"].url == "http://localhost:9998"
    assert settings.a2a_call_timeout_seconds == 5
    assert settings.a2a_poll_interval_seconds == 2.5
    assert settings.background_run_attempts == 1


@pytest.mark.parametrize("name", list(A2A_LIMITS))
@pytest.mark.parametrize("value", ["0", "-1"])
def test_a2a_limits_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=name.removeprefix("FREYJA_").lower()):
        Settings()


@pytest.mark.parametrize(
    "name", ["FREYJA_BACKGROUND_RUN_ATTEMPTS", "FREYJA_BACKGROUND_RUN_CONCURRENCY"]
)
def test_background_run_counts_are_whole_numbers(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "2.5")
    with pytest.raises(ValidationError, match=name.removeprefix("FREYJA_").lower()):
        Settings()


# Feature 004: the secret that protects stored API keys, and the profile test's time limit
# (contracts/settings.md).


def test_the_secret_key_is_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FREYJA_SECRET_KEY", raising=False)
    assert Settings().secret_key is None


def test_a_blank_secret_key_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREYJA_SECRET_KEY", "  ")
    assert Settings().secret_key is None


def test_a_fernet_key_is_accepted_as_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FREYJA_SECRET_KEY", key)
    secret = Settings().secret_key
    assert secret is not None
    assert secret.get_secret_value() == key
    assert key not in repr(secret)


@pytest.mark.parametrize("value", ["not-a-key", "c2hvcnQ=", "x" * 44])
def test_a_secret_that_is_not_a_fernet_key_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("FREYJA_SECRET_KEY", value)
    with pytest.raises(ValidationError, match="secret_key") as refused:
        Settings()
    assert value not in str(refused.value)


def test_the_llm_test_time_limit_defaults_to_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FREYJA_LLM_TEST_TIMEOUT_SECONDS", raising=False)
    assert Settings().llm_test_timeout_seconds == 60


def test_the_llm_test_time_limit_can_be_shortened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREYJA_LLM_TEST_TIMEOUT_SECONDS", "2.5")
    assert Settings().llm_test_timeout_seconds == 2.5


@pytest.mark.parametrize("value", ["0", "-1", "61"])
def test_the_llm_test_time_limit_is_between_zero_and_a_minute(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("FREYJA_LLM_TEST_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError, match="llm_test_timeout_seconds"):
        Settings()
