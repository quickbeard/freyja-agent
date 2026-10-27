import pytest

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
