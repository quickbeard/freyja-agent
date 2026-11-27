import ipaddress
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Self
from urllib.parse import SplitResult, urlsplit

from cryptography.fernet import Fernet
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    if host is None or not is_local_host(host):
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


# A caller's name and token (feature 009, contracts/settings.md "A caller"). A name is in the
# form agents' names have; a token is what feature 006 calls a token.
CALLER_NAME = re.compile(SERVER_NAME_PATTERN)
CALLER_TOKEN = re.compile(r"[!-~]{16,4096}")
LOCAL_CALLER = "local"
MAX_CALLERS = 20


class A2aCallerConfig(BaseModel):
    """One caller in ``FREYJA_A2A_SERVER_CALLERS`` (feature 009): its token, and nothing else.

    The token is a secret value from the moment it is read: it neither prints nor
    serializes, and no error raised for it repeats it.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: SecretStr


def _caller_errors(callers: dict[str, A2aCallerConfig]) -> str | None:
    """What is wrong with the named callers, naming the setting and the caller, never a
    token; None when nothing is. A name that breaks its rule is named by its place."""
    if len(callers) > MAX_CALLERS:
        return f"FREYJA_A2A_SERVER_CALLERS names more than {MAX_CALLERS} callers"
    seen: dict[str, str] = {}
    for place, (name, caller) in enumerate(callers.items(), start=1):
        if not CALLER_NAME.fullmatch(name) or name == LOCAL_CALLER:
            return (
                f"FREYJA_A2A_SERVER_CALLERS: the name of caller {place} must be lowercase "
                "letters, digits, hyphens, and underscores, start with a letter, be at most 32 "
                "characters, and not be 'local'"
            )
        token = caller.token.get_secret_value()
        if not CALLER_TOKEN.fullmatch(token):
            return (
                f"FREYJA_A2A_SERVER_CALLERS: the token of caller {name!r} must be 16 to 4,096 "
                "visible ASCII characters, with no space and no control character"
            )
        if token in seen:
            return (
                f"FREYJA_A2A_SERVER_CALLERS: caller {name!r} has the token of caller "
                f"{seen[token]!r}; each caller's token must be its own"
            )
        seen[token] = name
    return None


class _NamedTwiceError(ValueError):
    """A JSON object that names one caller twice."""


def _once_each(pairs: list[tuple[str, object]]) -> dict[str, object]:
    names = [name for name, _ in pairs]
    if len(set(names)) != len(names):
        raise _NamedTwiceError
    return dict(pairs)


def _caller_address_error(url: str) -> str | None:
    """What is wrong with ``FREYJA_A2A_SERVER_URL``, without repeating it; None when it is
    the address callers reach the outside listener under: a scheme, a host, a port at most."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "FREYJA_A2A_SERVER_URL must be http or https, a host, and at most a port"
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or url.endswith(("?", "#"))
        or port == 0
    ):
        return (
            "FREYJA_A2A_SERVER_URL must be http or https, a host, and at most a port: no "
            "path, query, fragment, user name, or password"
        )
    return None


# What listens on every interface, and so also on the service's own address: compared with,
# never bound.
_EVERY_ADDRESS = frozenset({"", ".".join("0000"), "::"})


class Settings(BaseSettings):
    """Runtime configuration, read from FREYJA_* environment variables."""

    # hide_input_in_errors: a refused value is never repeated in the error, which matters for
    # the tool server list (FR-002) and costs nothing elsewhere.
    model_config = SettingsConfigDict(
        env_prefix="FREYJA_", extra="ignore", hide_input_in_errors=True
    )

    if TYPE_CHECKING:
        # What a call takes, for the type checker alone. pydantic-settings fills a field that
        # is not given from the environment, one that has no default included, and pydantic
        # turns what a field is given into the field's type. The signature a type checker
        # works out from the fields says neither: a call would look short of an argument,
        # or given the wrong type.
        def __init__(self, **values: Any) -> None: ...

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
    # Feature 009: whether Freyja receives work from other agents over A2A. Off by default,
    # and with it off the settings below are not used and not checked
    # (specs/009-a2a-server/contracts/settings.md).
    a2a_server: bool = False
    # The most a send without a stream waits for its task to end before it is answered with
    # the task as it stands (FR-011).
    a2a_server_wait_seconds: float = 30
    # Received tasks in progress at once: for one caller, and for all callers (FR-025).
    a2a_server_caller_tasks: int = 5
    a2a_server_tasks: int = 20
    # The hand-over count at which a caller's message is refused (FR-027): how many
    # hand-overs between agents it may have passed through, at most, is one less.
    a2a_server_max_hops: int = 3
    # Where a named caller's runs keep their files, one directory per caller (FR-009a). It
    # is beside the workspace and never inside it, so that no thread that works in the
    # workspace reaches a caller's files.
    a2a_server_files_dir: str = "caller-files"
    # Named callers, each with its token (FR-004): a JSON object of name to ``{"token":
    # ...}``. None by default: only the local caller, on the service's own address.
    a2a_server_callers: Annotated[dict[str, A2aCallerConfig], NoDecode] = Field(
        default_factory=dict
    )
    # The outside listener (FR-003): the address and port it binds, which have no default,
    # and the address callers reach it under, as its card gives it.
    a2a_server_host: str | None = None
    a2a_server_port: int | None = None
    a2a_server_url: str | None = None

    @field_validator("a2a_server_callers", mode="wrap")
    @classmethod
    def _callers_read(
        cls, value: object, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> dict[str, A2aCallerConfig]:
        """The callers, read by the service itself: a name given twice is refused rather
        than the last kept, and no error repeats a name, a token, or any other part of the
        value. With the feature off they are not used, and nothing of them is checked."""
        try:
            if isinstance(value, str):
                value = json.loads(value, object_pairs_hook=_once_each) if value.strip() else {}
            return handler(value)
        except (ValueError, ValidationError) as exc:
            if not info.data.get("a2a_server"):
                return {}
            if isinstance(exc, _NamedTwiceError):
                raise ValueError("FREYJA_A2A_SERVER_CALLERS names a caller twice") from None
            raise ValueError(
                "FREYJA_A2A_SERVER_CALLERS must be a JSON object of callers' names, each with "
                'an object that holds its "token" and nothing else'
            ) from None

    @property
    def a2a_server_listener(self) -> bool:
        """Whether the outside listener is set: its address and its port."""
        return self.a2a_server_host is not None and self.a2a_server_port is not None

    @model_validator(mode="after")
    def _a2a_server_rules(self) -> Self:
        # Named in the message: an error raised here belongs to no one field.
        if (self.a2a_server_host is None) != (self.a2a_server_port is None):
            raise ValueError(
                "FREYJA_A2A_SERVER_HOST and FREYJA_A2A_SERVER_PORT are set together or not at all"
            )
        if not self.a2a_server:
            if self.a2a_server_listener:
                raise ValueError(
                    "FREYJA_A2A_SERVER_HOST and FREYJA_A2A_SERVER_PORT open the outside "
                    "listener, which needs FREYJA_A2A_SERVER, and it is off"
                )
            return self
        problem = _caller_errors(self.a2a_server_callers)
        if problem is not None:
            raise ValueError(problem)
        if self.a2a_server_url is not None:
            problem = _caller_address_error(self.a2a_server_url)
            if problem is not None:
                raise ValueError(problem)
        if self.a2a_server_listener:
            if not (self.a2a_server_host or "").strip():
                raise ValueError("FREYJA_A2A_SERVER_HOST must name an address, not be blank")
            if not self.a2a_server_callers:
                raise ValueError(
                    "The outside listener (FREYJA_A2A_SERVER_HOST) takes only named callers, "
                    "and FREYJA_A2A_SERVER_CALLERS names none"
                )
            if self.a2a_server_url is None:
                raise ValueError(
                    "The outside listener (FREYJA_A2A_SERVER_HOST) needs FREYJA_A2A_SERVER_URL, "
                    "the address callers reach it under"
                )
            port = self.a2a_server_port
            host = self.a2a_server_host or ""
            same = (
                host == self.host
                or host in _EVERY_ADDRESS
                or self.host in _EVERY_ADDRESS
                # localhost, 127.0.0.1, and ::1 are one machine's loopback.
                or (is_local_host(host.strip("[]")) and is_local_host(self.host.strip("[]")))
            )
            if port is None or not 1 <= port <= 65535 or (same and port == self.port):
                raise ValueError(
                    "FREYJA_A2A_SERVER_PORT must be from 1 to 65535 and not the service's own "
                    "port on the same address (FREYJA_PORT)"
                )
        if not 0 <= self.a2a_server_wait_seconds <= 600:
            raise ValueError("FREYJA_A2A_SERVER_WAIT_SECONDS must be from 0 to 600")
        if not 1 <= self.a2a_server_caller_tasks <= 100:
            raise ValueError("FREYJA_A2A_SERVER_CALLER_TASKS must be from 1 to 100")
        if not self.a2a_server_caller_tasks <= self.a2a_server_tasks <= 1000:
            raise ValueError(
                "FREYJA_A2A_SERVER_TASKS must be at least FREYJA_A2A_SERVER_CALLER_TASKS and "
                "at most 1000"
            )
        if not 1 <= self.a2a_server_max_hops <= 16:
            raise ValueError("FREYJA_A2A_SERVER_MAX_HOPS must be from 1 to 16")
        files = Path(self.a2a_server_files_dir).resolve()
        workspace = Path(self.workspace_dir).resolve()
        if files == workspace or workspace in files.parents or files in workspace.parents:
            raise ValueError(
                "FREYJA_A2A_SERVER_FILES_DIR must not be the workspace, be inside it, or "
                "contain it (FREYJA_WORKSPACE_DIR)"
            )
        return self

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
