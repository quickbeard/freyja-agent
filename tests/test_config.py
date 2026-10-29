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
