"""FR-001, FR-004: one tool server entry in ``FREYJA_MCP_SERVERS`` (contracts/server-config.md).

Every refusal happens when the settings are read, before the service starts, and its location
names the server, so a bad entry cannot reach a connection attempt.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from freyja_agent.config import McpServerConfig, Settings

# Stands in for a credential an operator put in the wrong field.
MARKER = "zz-marker-value-in-entry"


def settings_with(servers: dict[str, Any]) -> Settings:
    return Settings(mcp_servers=servers)


def test_a_command_entry_is_accepted() -> None:
    settings = settings_with(
        {
            "git": {
                "command": "uvx",
                "args": ["mcp-server-git", "--repository", "/srv/repo"],
                "pass_env": ["TRACKER_TOKEN"],
                "read_only": ["git_status", "git_log"],
            }
        }
    )
    entry = settings.mcp_servers["git"]
    assert isinstance(entry, McpServerConfig)
    assert entry.command == "uvx"
    assert entry.url is None
    assert entry.args == ["mcp-server-git", "--repository", "/srv/repo"]
    assert entry.pass_env == ["TRACKER_TOKEN"]
    assert entry.read_only == ["git_status", "git_log"]


def test_entry_defaults_are_empty_lists() -> None:
    entry = settings_with({"notes": {"url": "http://127.0.0.1:8765/mcp"}}).mcp_servers["notes"]
    assert entry.command is None
    assert entry.args == []
    assert entry.pass_env == []
    assert entry.read_only == []


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8765/mcp",
        "http://127.0.0.1:8765/mcp",
        "https://127.0.0.1:8765/mcp",
        "http://127.8.9.10:8765/mcp",
        "http://[::1]:8765/mcp",
        # Judged by the IPv4 address it carries, whatever the interpreter's patch release says.
        "http://[::ffff:127.0.0.1]:8765/mcp",
        # User information does not change the host that is checked.
        "http://user:pw@127.0.0.1:8765/mcp",
    ],
)
def test_a_local_address_is_accepted(url: str) -> None:
    assert settings_with({"notes": {"url": url}}).mcp_servers["notes"].url == url


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param({"command": "x", "url": "http://127.0.0.1:9/mcp"}, id="both-forms"),
        pytest.param({"read_only": ["x"]}, id="neither-form"),
        pytest.param({"url": "http://127.0.0.1:9/mcp", "args": ["a"]}, id="args-with-url"),
        pytest.param({"url": "http://127.0.0.1:9/mcp", "pass_env": ["A"]}, id="pass-env-with-url"),
        pytest.param({"url": "ftp://127.0.0.1:9/mcp"}, id="scheme"),
        pytest.param({"url": "127.0.0.1:9/mcp"}, id="no-scheme"),
        pytest.param({"url": "http://10.0.0.5:8000/mcp"}, id="private-address"),
        pytest.param({"url": "http://0.0.0.0:8000/mcp"}, id="unspecified-address"),
        pytest.param({"url": "http://[::ffff:10.0.0.5]:8000/mcp"}, id="mapped-private-address"),
        pytest.param({"url": "http://example.com/mcp"}, id="other-name"),
        pytest.param(
            {"url": "http://localhost.example.com/mcp"}, id="name-starting-with-localhost"
        ),
        pytest.param({"url": "http://127.0.0.1@example.com/mcp"}, id="local-looking-user-info"),
        pytest.param({"command": "x", "transport": "stdio"}, id="unknown-field"),
    ],
)
def test_a_bad_entry_is_refused_and_the_server_is_named(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as refused:
        settings_with({"tracker": entry})
    locations = [error["loc"] for error in refused.value.errors()]
    assert all(loc[:2] == ("mcp_servers", "tracker") for loc in locations), locations


@pytest.mark.parametrize(
    "name",
    ["Git", "9git", "-git", "git server", "git.server", "g" * 33, ""],
)
def test_a_bad_server_name_is_refused(name: str) -> None:
    with pytest.raises(ValidationError) as refused:
        settings_with({name: {"command": "x"}})
    assert all(error["loc"][0] == "mcp_servers" for error in refused.value.errors())


@pytest.mark.parametrize("name", ["g", "git", "git-2", "git_tools", "g" * 32])
def test_a_good_server_name_is_accepted(name: str) -> None:
    assert name in settings_with({name: {"command": "x"}}).mcp_servers


@pytest.mark.parametrize(
    "entry",
    [
        {"command": MARKER, "url": "http://127.0.0.1:9/mcp"},
        {"url": f"http://10.0.0.5:8000/mcp?token={MARKER}"},
        {"command": "x", "args": [MARKER], "nope": 1},
        {"command": "x", "nope": MARKER},
    ],
)
def test_a_refusal_never_echoes_the_entry(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as refused:
        settings_with({"tracker": entry})
    assert MARKER not in str(refused.value)
    assert "tracker" in str(refused.value)


def test_a_refusal_read_from_the_environment_names_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREYJA_MCP_SERVERS", '{"tracker": {"url": "http://10.0.0.5:8000/mcp"}}')
    with pytest.raises(ValidationError) as refused:
        Settings()
    assert "mcp_servers.tracker" in str(refused.value)
    assert "10.0.0.5" not in str(refused.value)
