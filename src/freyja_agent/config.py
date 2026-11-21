import ipaddress
import re
from typing import Annotated, Self
from urllib.parse import SplitResult, urlsplit

from cryptography.fernet import Fernet
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shown in proposals, logs, and the catalog, so it is kept short and plain (FR-001).
SERVER_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"
ServerName = Annotated[str, StringConstraints(pattern=SERVER_NAME_PATTERN)]
# One entry of FREYJA_ALLOWED_HOSTS: labels of letters, digits, hyphens, and underscores,
# with dots between them. A name with other letters is written as a browser sends it, in
# its ``xn--`` form.
HOST_NAME = re.compile(r"[a-z0-9_-]+(\.[a-z0-9_-]+)*")


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


# The most characters an agent's address may have (feature 005, FR-006).
ADDRESS_LIMIT = 2000
_DEFAULT_PORTS = {"http": 80, "https": 443}


# What a path may hold: what httpx sends as it is written, so the kept form is the form that
# is requested (RFC 3986 unreserved characters and sub-delimiters, ``:``, ``@``, and ``/``).
_PATH_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~!$&'()*+,;=:@/"
)


def _split(url: str) -> SplitResult | None:
    """``url`` in its parts, or None when it cannot be parsed or its port is not a number."""
    try:
        parts = urlsplit(url)
        _ = parts.port, parts.hostname
    except ValueError:
        return None
    return parts


class AddressError(ValueError):
    """An agent's address that breaks a rule (feature 005, FR-006).

    The message is a sentence of the service's own. It names the rule and never repeats what
    was entered, so it can be shown, returned, and logged.
    """


def canonical_address(url: str) -> str:
    """The one form a saved agent's address is kept, shown, and compared in (005 research R11).

    Scheme and host in lowercase, the port always written, an IP address in its usual
    compressed writing, and no trailing slash. Whether two addresses are the same decides
    whether a card is read and pending tasks are given up, so every spelling of one address
    has to come to one string. httpx rewrites what it is given (lowercase host, default port
    dropped, dot segments resolved, unsafe characters encoded), so what it would rewrite is
    refused here: a query and a fragment as well, because the card's address is this address
    with a path added to it, and in the path every character httpx would encode.

    Raises ``AddressError``, with no error of the parser behind it: those repeat the input.
    """
    if len(url) > ADDRESS_LIMIT:
        raise AddressError("The address is longer than 2,000 characters.")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        # urlsplit drops tabs and newlines and trims spaces without saying so.
        raise AddressError("The address must not contain spaces or control characters.")
    if "\\" in url:
        raise AddressError("The address must not contain a backslash.")
    if "%" in url:
        raise AddressError("The address must not contain a percent sign.")
    parts = _split(url)
    if parts is None:
        raise AddressError("The address is not a web address.")
    port, host = parts.port, parts.hostname
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise AddressError("The address must start with http:// or https://.")
    if parts.username is not None or parts.password is not None:
        raise AddressError("The address must not carry a user name or a password.")
    if "?" in url:
        raise AddressError("The address must not carry a query.")
    if "#" in url:
        raise AddressError("The address must not carry a fragment.")
    if not is_local_host(host):
        raise AddressError(
            "The address must be on this machine: localhost or a loopback IP address."
        )
    if port == 0:
        raise AddressError("The address's port must be between 1 and 65535.")
    path = parts.path.removesuffix("/")
    if any(segment in ("", ".", "..") for segment in path.split("/")[1:]):
        raise AddressError("The address's path must not have an empty, '.', or '..' segment.")
    if not set(path) <= _PATH_CHARACTERS:
        raise AddressError(
            "The address's path may hold only letters, digits, slashes, and the characters "
            "- . _ ~ ! $ & ' ( ) * + , ; = : @"
        )
    if host != "localhost":
        address = ipaddress.ip_address(host)  # is_local_host accepted it, so it parses
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    kept = f"{scheme}://{host}:{port or _DEFAULT_PORTS[scheme]}{path}"
    if len(kept) > ADDRESS_LIMIT:
        # Writing the port out made it longer than what was entered.
        raise AddressError("The address is longer than 2,000 characters.")
    return kept


def destination(url: str) -> str:
    """Where an address on this machine leads, however the machine is written (005 FR-006).

    The port and the path. ``localhost``, ``127.0.0.1``, and ``[::1]`` are one machine, and
    the scheme does not choose another listener, so two saved agents whose addresses give the
    same value here are two names for one place. Raises ``AddressError`` as
    ``canonical_address`` does.
    """
    parts = urlsplit(canonical_address(url))
    return f"{parts.port}{parts.path}"


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
    # The host names the service answers for, beside ``localhost`` and every IP address
    # written as one, which need no entry. A request made for any other name is refused
    # (api/hosts.py): a name is what a hostile page can point at this machine. Empty is the
    # safe local choice. A deployment lists the name its console is opened under and, where
    # the console reaches the service by a name, that name.
    allowed_hosts: list[str] = Field(default_factory=list)
    # Carries credentials, so it has no default: startup fails until FREYJA_DATABASE_URI is set.
    database_uri: str
    model: str = "anthropic:claude-sonnet-5-5"
    # Whether an ``openai:`` model is called through OpenAI's Responses API, as Deep Agents
    # does by default. False calls it through chat completions, for an OpenAI-compatible server
    # whose Responses API does not take a conversation's earlier turns. Other providers ignore it.
    openai_responses_api: bool = True
    # The secret that protects stored API keys (feature 004, FR-020, FR-022): a Fernet key.
    # Unset, the service starts and saving an LLM profile is refused; a value that is not a
    # Fernet key stops startup like any invalid setting (contracts/settings.md).
    secret_key: SecretStr | None = None
    # The most a profile test may take, both of its steps together (FR-019).
    llm_test_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 60
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
    # Background runs under way at once on one instance; further ones wait (003 SC-003).
    background_run_concurrency: Annotated[int, Field(gt=0)] = 4

    @field_validator("allowed_hosts")
    @classmethod
    def _host_names_only(cls, value: list[str]) -> list[str]:
        # Kept as a request's ``Host`` is compared: lowercase, without the dot a fully
        # qualified name may end with. An entry that could never equal one, such as an
        # address with its scheme or its port, stops startup instead of allowing nothing.
        names = [entry.strip().lower().removesuffix(".") for entry in value]
        if not all(HOST_NAME.fullmatch(name) for name in names):
            raise ValueError(
                "each entry must be a host name alone, such as console.example.com: no "
                "scheme, no port, no path, and no wildcard"
            )
        return names

    @field_validator("secret_key", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, value: object) -> object:
        # A line left as ``FREYJA_SECRET_KEY=`` means no secret, not an invalid one.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("secret_key")
    @classmethod
    def _secret_is_a_fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        try:
            Fernet(value.get_secret_value())
        except ValueError:
            # Raised afresh so that nothing of the refused value travels with the error.
            raise ValueError(
                "must be a Fernet key: 32 bytes as URL-safe base64, as "
                "cryptography.fernet.Fernet.generate_key() gives"
            ) from None
        return value


settings = Settings()
