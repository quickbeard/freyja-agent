import pytest
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
