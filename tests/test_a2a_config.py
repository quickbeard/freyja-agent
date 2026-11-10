"""FR-001, FR-004: one agent entry in ``FREYJA_A2A_AGENTS`` (contracts/agent-config.md).

Every refusal here happens when the settings are read, before the service starts, and its
location names the agent. The refusals that need the card or the tool server list happen while
agents start and are tested in tests/test_a2a_startup.py.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from freyja_agent.config import A2aAgentConfig, Settings

# Stands in for something an operator should not have put in an entry.
MARKER = "zz-marker-value-in-entry"


def settings_with(agents: dict[str, Any]) -> Settings:
    return Settings(a2a_agents=agents)


def test_an_entry_is_accepted_and_not_read_only_by_default() -> None:
    entry = settings_with({"hello": {"url": "http://127.0.0.1:9999"}}).a2a_agents["hello"]
    assert isinstance(entry, A2aAgentConfig)
    assert entry.url == "http://127.0.0.1:9999"
    assert entry.read_only is False


def test_read_only_can_be_set() -> None:
    agents = settings_with({"hello": {"url": "http://127.0.0.1:9999", "read_only": True}})
    assert agents.a2a_agents["hello"].read_only is True


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:9999",
        "http://127.0.0.1:9999",
        "https://127.0.0.1:9999",
        "http://127.8.9.10:9999/a2a",
        "http://[::1]:9999",
        # Judged by the IPv4 address it carries, whatever the interpreter's patch release says.
        "http://[::ffff:127.0.0.1]:9999",
    ],
)
def test_a_local_address_is_accepted(url: str) -> None:
    assert settings_with({"hello": {"url": url}}).a2a_agents["hello"].url == url


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param({}, id="no-url"),
        pytest.param({"read_only": True}, id="read-only-without-url"),
        pytest.param({"url": "ftp://127.0.0.1:9999"}, id="scheme"),
        pytest.param({"url": "127.0.0.1:9999"}, id="no-scheme"),
        pytest.param({"url": "http://10.0.0.5:9999"}, id="private-address"),
        pytest.param({"url": "http://0.0.0.0:9999"}, id="unspecified-address"),
        pytest.param({"url": "http://[::ffff:10.0.0.5]:9999"}, id="mapped-private-address"),
        pytest.param({"url": "http://example.com:9999"}, id="other-name"),
        pytest.param({"url": "http://127.0.0.1@example.com/"}, id="local-looking-user-info"),
        pytest.param({"url": "http://127.0.0.1:9999", "skills": ["x"]}, id="unknown-field"),
        pytest.param({"url": "http://127.0.0.1:9999", "read_only": "maybe"}, id="read-only-type"),
    ],
)
def test_a_bad_entry_is_refused_and_the_agent_is_named(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as refused:
        settings_with({"hello": entry})
    locations = [error["loc"] for error in refused.value.errors()]
    assert all(loc[:2] == ("a2a_agents", "hello") for loc in locations), locations


@pytest.mark.parametrize(
    "name", ["Hello", "9hello", "-hello", "hello agent", "hello.agent", "h" * 33, ""]
)
def test_a_bad_agent_name_is_refused(name: str) -> None:
    with pytest.raises(ValidationError) as refused:
        settings_with({name: {"url": "http://127.0.0.1:9999"}})
    assert all(error["loc"][0] == "a2a_agents" for error in refused.value.errors())


@pytest.mark.parametrize("name", ["h", "hello", "hello-2", "hello_world", "h" * 32])
def test_a_good_agent_name_is_accepted(name: str) -> None:
    assert name in settings_with({name: {"url": "http://127.0.0.1:9999"}}).a2a_agents


@pytest.mark.parametrize(
    "entry",
    [
        {"url": f"http://10.0.0.5:9999/?token={MARKER}"},
        {"url": f"ftp://127.0.0.1:9999/{MARKER}"},
        {"url": "http://127.0.0.1:9999", "nope": MARKER},
        {"url": "http://127.0.0.1:9999", "read_only": MARKER},
    ],
)
def test_a_refusal_never_echoes_the_entry(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as refused:
        settings_with({"hello": entry})
    assert MARKER not in str(refused.value)
    assert "hello" in str(refused.value)


def test_a_refusal_read_from_the_environment_names_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREYJA_A2A_AGENTS", '{"hello": {"url": "http://10.0.0.5:9999"}}')
    with pytest.raises(ValidationError) as refused:
        Settings()
    assert "a2a_agents.hello" in str(refused.value)
    assert "10.0.0.5" not in str(refused.value)
