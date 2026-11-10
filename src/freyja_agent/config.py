import ipaddress
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shown in proposals, logs, and the catalog, so it is kept short and plain (FR-001).
SERVER_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"
ServerName = Annotated[str, StringConstraints(pattern=SERVER_NAME_PATTERN)]


def is_local_host(host: str | None) -> bool:
    """True when ``host`` names this machine directly: ``localhost`` or a loopback IP literal.

    No name is looked up, so the answer cannot be changed by a resolver. ``localhost`` is
    accepted as written; any other name is refused even if it would resolve here. An
    IPv4-mapped IPv6 literal is judged by the IPv4 address it carries, because how
    ``is_loopback`` answers for that form differs between Python patch releases (research R2).
    """
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


class McpServerConfig(BaseModel):
    """One tool server in ``FREYJA_MCP_SERVERS`` (contracts/server-config.md).

    Exactly one form of connection: ``command``, which the service launches and stops, or
    ``url``, the address of a server the operator runs on this machine. No error raised here
    repeats a value from the entry, since an operator may have put a credential in the wrong
    field.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    # Names of environment variables, never values, so a credential cannot be written here.
    pass_env: list[str] = Field(default_factory=list)
    url: str | None = None
    # The tools exempt from approval. Every tool not listed is gated (FR-006).
    read_only: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_local_form(self) -> Self:
        if (self.command is None) == (self.url is None):
            raise ValueError("exactly one of 'command' and 'url' must be set")
        if self.url is not None:
            if self.args or self.pass_env:
                raise ValueError("'args' and 'pass_env' are only allowed with 'command'")
            parts = urlsplit(self.url)
            if parts.scheme not in ("http", "https"):
                raise ValueError("'url' must use http or https")
            if not is_local_host(parts.hostname):
                raise ValueError(
                    "'url' must be an address on this machine: localhost or a loopback IP address"
                )
        return self


class A2aAgentConfig(BaseModel):
    """One agent in ``FREYJA_A2A_AGENTS`` (feature 003, contracts/agent-config.md).

    Where the agent runs on this machine, and whether messages to it skip approval. Like a tool
    server entry, no error raised here repeats a value from the entry.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    url: str
    # True exempts every hand-off, answer, and cancel to this agent from approval (FR-011).
    read_only: StrictBool = False

    @model_validator(mode="after")
    def _local_address(self) -> Self:
        parts = urlsplit(self.url)
        if parts.scheme not in ("http", "https"):
            raise ValueError("'url' must use http or https")
        if not is_local_host(parts.hostname):
            raise ValueError(
                "'url' must be an address on this machine: localhost or a loopback IP address"
            )
        return self


class Settings(BaseSettings):
    """Runtime configuration, read from FREYJA_* environment variables."""

    # hide_input_in_errors: a refused value is never repeated in the error, which matters for
    # the tool server list (FR-002) and costs nothing elsewhere.
    model_config = SettingsConfigDict(
        env_prefix="FREYJA_", extra="ignore", hide_input_in_errors=True
    )

    # Local runs listen only on this machine. Containers set FREYJA_HOST=0.0.0.0.
    host: str = "127.0.0.1"
    port: int = 8000
    # Carries credentials, so it has no default: startup fails until FREYJA_DATABASE_URI is set.
    database_uri: str
    model: str = "anthropic:claude-sonnet-5-5"
    # Root of the agent's file tools; skills/ inside it is committed (FR-017).
    workspace_dir: str = "workspace"
    # LangGraph recursion limit per run; reaching it ends the run with an error (FR-004a).
    max_steps: int = 50
    # Expiry of the per-thread run lock; the heartbeat renews every third of it (FR-006).
    run_lock_ttl_seconds: int = 60
    # Tool servers, as one JSON object of server name to entry. Empty means none (002 FR-001).
    mcp_servers: dict[ServerName, McpServerConfig] = Field(default_factory=dict)
    # Time limit for one tool call, including connecting (002 FR-012).
    mcp_call_timeout_seconds: Annotated[float, Field(gt=0)] = 30
    # Time limit per server for connecting and listing its tools at startup (002 SC-004).
    mcp_startup_timeout_seconds: Annotated[float, Field(gt=0)] = 20
    # A2A agents, as one JSON object of agent name to entry. Empty means none (003 FR-001).
    a2a_agents: dict[ServerName, A2aAgentConfig] = Field(default_factory=dict)
    # How long one hand-off, answer, or cancel waits within a run; a task still working then
    # becomes pending (003 FR-009, FR-017).
    a2a_call_timeout_seconds: Annotated[float, Field(gt=0)] = 30
    # Time limit per agent for reading its card at startup (003 SC-005).
    a2a_startup_timeout_seconds: Annotated[float, Field(gt=0)] = 20
    # How often pending tasks are checked and outcomes delivered (003 FR-019, SC-003).
    a2a_poll_interval_seconds: Annotated[float, Field(gt=0)] = 10
    # A pending task older than this, counted from its start, is given up (003 FR-021).
    a2a_task_max_age_seconds: Annotated[float, Field(gt=0)] = 86400
    # Attempts for one background run, the first included (003 FR-027).
    background_run_attempts: Annotated[int, Field(gt=0)] = 3
    # Wait between background run attempts (003 FR-027).
    background_retry_seconds: Annotated[float, Field(gt=0)] = 60


settings = Settings()
