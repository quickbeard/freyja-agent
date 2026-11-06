"""Links to the tool servers named in ``FREYJA_MCP_SERVERS`` (research R2 to R4, R7, R12).

``open_tool_servers`` is entered once, by the runtime, before the orchestrator is built. It
connects to every configured server, lists its tools, checks the result against the
configuration, and fails naming the server when anything is wrong, so the service never
serves requests with a tool server it could not reach or a tool it cannot classify (FR-004).

The form of an entry decides how long its connection lives (research R3):

- a ``command`` server is launched once and holds one connection for the life of the service.
  A dedicated task, the owner task, enters and exits that connection, because a transport
  failure cancels the scope of the task that entered it and that must never be the task
  running the service's lifespan;
- a ``url`` server is connected once per use, so a server that is answering again works on
  the next call with no reconnect logic.

Text that comes from a server, or is written about it, passes through the server's redactor,
which replaces the exact values of its ``pass_env`` variables (FR-002).
"""

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from langchain_core.tools import ToolException
from mcp import Client, StdioServerParameters, stdio_client

from freyja_agent.agent.catalog import RESERVED_TOOL_NAMES
from freyja_agent.config import McpServerConfig
from freyja_agent.delegates.mcp_tools import DelegateTool, to_delegate_tool

logger = logging.getLogger(__name__)

# The form model providers accept for a function name. A tool outside it would make every
# model request fail, which would look like a broken model (research R4).
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ERROR_TEXT_LIMIT = 500
REDACTED = "***"
# Stands in for a server's address in text that may be shown, such as the catalog (FR-014).
ADDRESS = "<address>"

# How long closing a launched server may take before its owner task is cancelled. The SDK
# bounds its own shutdown (close stdin, terminate, kill), each step within about 2 seconds.
_CLOSE_SECONDS = 10.0
# How long the reader of a launched server's error output may drain after the process ended.
_DRAIN_SECONDS = 0.5
_ERROR_OUTPUT_LINE_LIMIT = 8192
# The MCP library's own loggers all live under this name.
_SDK_LOGGER = "mcp"

Connection = Literal["command", "address"]
# Returns what ``mcp.Client`` connects to: a URL, a transport, or (in tests) a server object.
# The third argument receives each line a launched server writes to its error output.
Connect = Callable[[str, McpServerConfig, Callable[[str], None]], Any]


class ToolServerError(RuntimeError):
    """A tool server is unusable as configured. Raised at startup; names the server (FR-004)."""


class Redactor:
    """Replaces the exact values a server was given with ``***`` (research R12).

    A value the server encodes or shortens before printing it is not recognized. Empty values
    never get here: startup refuses them, since they would match everywhere.
    """

    def __init__(self, secrets: Iterable[str]) -> None:
        # Longest first, so a value that contains another is replaced whole.
        self._secrets = tuple(sorted({s for s in secrets if s}, key=len, reverse=True))

    def __bool__(self) -> bool:
        return bool(self._secrets)

    def text(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def deep(self, value: Any) -> Any:
        """Redact every string value inside a JSON-like structure; keys are left alone."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {key: self.deep(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.deep(item) for item in value]
        return value


class _RedactSdkLogs(logging.Filter):
    """Keeps what servers were given out of what the MCP library itself logs.

    The library logs text it received from a server. For example, a line on a launched
    server's standard output that is not a protocol message is logged with a traceback that
    quotes the line. Those records are part of the service's log like any other, so they get
    the same protection (FR-002):

    - the message has the exact values replaced, as everywhere else;
    - a traceback is reduced to the type of the exception. A traceback can quote a server's
      text in a shortened form, with the middle cut out, and a value that is cut cannot be
      recognized and replaced.

    Every record passes; only its text changes.
    """

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.text(record.getMessage())
        record.args = None
        if record.exc_info or record.exc_text:
            raised = record.exc_info[0] if record.exc_info else None
            kind = raised.__name__ if raised is not None else "an exception"
            # Let go of the exception object as well: it still holds the original text.
            record.exc_info = None
            record.exc_text = f"{kind} (details withheld: they can quote text from the tool server)"
        return True


@contextmanager
def _sdk_logs_redacted(secrets: Iterable[str]) -> Iterator[None]:
    """Attach the filter to the MCP library's loggers for as long as the servers are open."""
    redactor = Redactor(secrets)
    if not redactor:
        yield
        return
    log_filter = _RedactSdkLogs(redactor)
    names = [
        name
        for name in logging.root.manager.loggerDict
        if name == _SDK_LOGGER or name.startswith(f"{_SDK_LOGGER}.")
    ]
    loggers = [logging.getLogger(name) for name in names]
    for sdk_logger in loggers:
        sdk_logger.addFilter(log_filter)
    try:
        yield
    finally:
        for sdk_logger in loggers:
            sdk_logger.removeFilter(log_filter)


def innermost(exc: BaseException) -> BaseException:
    """The first leaf of an exception group; connection failures arrive wrapped in one."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def reason(exc: BaseException) -> str:
    """A short description of why a server could not be used: the innermost error."""
    leaf = innermost(exc)
    text = str(leaf).strip()
    return f"{type(leaf).__name__}: {text}" if text else type(leaf).__name__


async def _pump_lines(fd: int, on_line: Callable[[str], None]) -> None:
    """Read a pipe until it closes and hand over each line of text."""

    def emit(raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace").rstrip("\r")
        if line.strip():
            on_line(line)

    pipe = os.fdopen(fd, "rb", buffering=0)
    transport = None
    try:
        reader = asyncio.StreamReader()
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), pipe
        )
        buffer = b""
        while chunk := await reader.read(4096):
            *lines, buffer = (buffer + chunk).split(b"\n")
            for line in lines:
                emit(line)
            if len(buffer) > _ERROR_OUTPUT_LINE_LIMIT:
                emit(buffer)
                buffer = b""
        emit(buffer)
    except OSError:
        logger.debug("Reading a tool server's error output stopped early", exc_info=True)
    finally:
        if transport is not None:
            transport.close()
        else:
            pipe.close()


@asynccontextmanager
async def captured_stdio(
    params: StdioServerParameters, on_error_output: Callable[[str], None]
) -> AsyncIterator[Any]:
    """The SDK's stdio transport, with the launched process's error output captured.

    By default a launched server inherits the service's own error output, so a server that
    prints a credential there would put it in the service's output unredacted. Here the
    process writes to a pipe instead, and each line goes to ``on_error_output`` (research R12).
    """
    read_fd, write_fd = os.pipe()
    write_end = os.fdopen(write_fd, "w")
    reader: asyncio.Task[None] | None = None
    try:
        async with stdio_client(params, errlog=write_end) as streams:
            # The process holds its own copy now; closing ours lets the reader see the end.
            write_end.close()
            reader = asyncio.create_task(_pump_lines(read_fd, on_error_output))
            yield streams
    finally:
        write_end.close()
        if reader is None:
            os.close(read_fd)
        else:
            try:
                await asyncio.wait_for(asyncio.shield(reader), timeout=_DRAIN_SECONDS)
            except TimeoutError:
                pass
            finally:
                reader.cancel()


def default_connect(
    name: str,
    config: McpServerConfig,
    on_error_output: Callable[[str], None],
) -> Any:
    """The production target for ``mcp.Client``: a URL, or a launched command over pipes.

    A launched process receives the SDK's basic variables and the ones named in ``pass_env``,
    never the rest of the service's environment (FR-002). ``open_tool_servers`` has already
    checked that each named variable is set and not empty.
    """
    if config.url is not None:
        return config.url
    if config.command is None:  # the settings validator makes this unreachable
        raise ToolServerError(f"Tool server '{name}' needs a command or a url.")
    params = StdioServerParameters(
        command=config.command,
        args=list(config.args),
        env={variable: os.environ[variable] for variable in config.pass_env},
    )
    return captured_stdio(params, on_error_output)


class ToolServer:
    """The running link to one configured server (data-model.md "Tool server")."""

    def __init__(
        self,
        name: str,
        config: McpServerConfig,
        *,
        connect: Connect,
        call_timeout: float,
        secrets: Iterable[str] = (),
    ) -> None:
        self.name = name
        self.config = config
        self.connection: Connection = "command" if config.command is not None else "address"
        self.tools: tuple[DelegateTool, ...] = ()
        # Outcome of the most recent attempt to use the server (FR-014).
        self.reachable = False
        self.checked_at = datetime.now(UTC)
        self.error: str | None = None
        self._connect = connect
        self._call_timeout = call_timeout
        self._redactor = Redactor(secrets)
        self._client: Client | None = None
        self._owner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def redact(self, text: str) -> str:
        return self._redactor.text(text)

    def describe(self, text: str) -> str:
        """Text about this server that is safe to log, to raise, and to show in the catalog.

        The values the server was given are replaced, and so is its address: an exception's
        text can quote the URL it failed to reach, with whatever its query string holds, and
        the catalog shows which form of connection a server uses and nothing more (FR-002,
        FR-014). Cut to the length a status may have.
        """
        text = self.redact(text)
        for address in self._addresses:
            text = text.replace(address, ADDRESS)
        return text[:ERROR_TEXT_LIMIT]

    @property
    def _addresses(self) -> tuple[str, ...]:
        """The forms in which this server's address can appear, longest first."""
        if self.config.url is None:
            return ()
        parts = urlsplit(self.config.url)
        host_and_port = parts.netloc.rpartition("@")[2]
        forms = {self.config.url, parts.netloc, host_and_port}
        return tuple(sorted((form for form in forms if form), key=len, reverse=True))

    def _target(self) -> Any:
        return self._connect(self.name, self.config, self._log_error_output)

    def _log_error_output(self, line: str) -> None:
        logger.info("Tool server %s: %s", self.name, self.redact(line))

    def _mark(self, reachable: bool, error: str | None = None) -> None:
        """Record the outcome of the most recent attempt to use the server."""
        self.reachable = reachable
        self.checked_at = datetime.now(UTC)
        self.error = None if reachable else self.describe(error or "")

    async def start(self, startup_timeout: float) -> None:
        """Connect, list the tools, and convert them. Closes the link again if that fails."""
        try:
            async with asyncio.timeout(startup_timeout):
                if self.connection == "command":
                    ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                    self._owner = asyncio.create_task(
                        self._own(ready), name=f"tool-server:{self.name}"
                    )
                    await ready
                    listed = await self._list(self._connected())
                else:
                    async with Client(self._target()) as client:
                        listed = await self._list(client)
        except BaseException:
            await self.close()
            raise
        self.tools = tuple(
            to_delegate_tool(
                self, tool, read_only=self.config.read_only, redact=self._redactor.deep
            )
            for tool in listed
        )
        self._mark(reachable=True)

    async def _own(self, ready: asyncio.Future[None]) -> None:
        """Hold a launched server's connection open until the service stops."""
        try:
            async with Client(self._target()) as client:
                self._client = client
                if not ready.done():
                    ready.set_result(None)
                await self._stop.wait()
        except Exception as exc:
            if ready.done():
                logger.warning(
                    "Tool server %s: the connection ended: %s",
                    self.name,
                    self.describe(reason(exc)),
                )
            else:
                ready.set_exception(exc)
        finally:
            self._client = None

    def _connected(self) -> Client:
        if self._client is None:
            raise ConnectionError("the launched server's connection is closed")
        return self._client

    @staticmethod
    async def _list(client: Client) -> list[Any]:
        tools: list[Any] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                return tools

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Run one tool call under the time limit, including connecting, and return its result.

        Every outcome updates the server's status, and none is tried again (FR-012, research
        R7):

        - a result means the server answered, so it is reachable. That holds for a tool error
          too: the server refused or failed the call, it did not go away;
        - at the time limit the call is abandoned. Its effect may or may not have happened,
          and the error says so, because an approval authorizes one attempt and only a person
          can decide on another;
        - anything else means the server could not be used. A server at an address is
          connected again on the next call, so it recovers by itself once it answers; a
          launched server whose process has exited stays unreachable until the service is
          restarted, because the service never launches it again.

        A failure is raised as ``ToolException``, which reaches the orchestrator as an error
        result and never ends a run. It is logged with the server and the tool, never with the
        arguments.
        """
        limit = self._call_timeout
        try:
            async with asyncio.timeout(limit) as deadline:
                if self.connection == "command":
                    result = await self._connected().call_tool(tool, arguments)
                else:
                    async with Client(self._target()) as client:
                        result = await client.call_tool(tool, arguments)
        except Exception as exc:
            if isinstance(exc, TimeoutError) and deadline.expired():
                self._mark(False, f"the call to '{tool}' did not finish within {limit:g} seconds")
                logger.warning(
                    "Tool server %s: tool %s did not finish within %g seconds (timeout)",
                    self.name,
                    tool,
                    limit,
                )
                raise ToolException(
                    f"Tool '{tool}' on server '{self.name}' did not finish within {limit:g} "
                    "seconds, the time limit for one call. Its effect may or may not have "
                    "happened. It was not tried again."
                ) from None
            self._mark(False, reason(exc))
            logger.warning(
                "Tool server %s: tool %s could not be called: %s (unreachable)",
                self.name,
                tool,
                self.error,
            )
            raise ToolException(
                f"Tool server '{self.name}' could not be reached, so the call to '{tool}' was "
                "not completed. If the call had already been sent, its effect may or may not "
                "have happened. It was not tried again."
            ) from None
        self._mark(reachable=True)
        if getattr(result, "is_error", False):
            logger.warning(
                "Tool server %s: tool %s returned an error (tool_error)", self.name, tool
            )
        return result

    async def close(self) -> None:
        """Release the link. A launched server's process is stopped; an address server is not
        touched, because the operator owns it (FR-005)."""
        self._stop.set()
        owner, self._owner = self._owner, None
        if owner is None:
            return
        if self._client is None:
            # Never connected, so nothing is waiting on the stop event.
            owner.cancel()
        done, _ = await asyncio.wait({owner}, timeout=_CLOSE_SECONDS)
        if not done:
            owner.cancel()
            await asyncio.wait({owner}, timeout=_CLOSE_SECONDS)


def _resolve_pass_env(name: str, config: McpServerConfig) -> list[str]:
    values = []
    for variable in config.pass_env:
        value = os.environ.get(variable)
        if not value:
            raise ToolServerError(
                f"Tool server '{name}': the environment variable '{variable}', named in "
                "pass_env, is unset or empty."
            )
        values.append(value)
    return values


def _start_failure(server: ToolServer, exc: BaseException, startup_timeout: float) -> str:
    verb = "started" if server.connection == "command" else "reached"
    if isinstance(innermost(exc), TimeoutError):
        why = f"it did not answer within {startup_timeout:g} seconds"
    else:
        why = server.describe(reason(exc))
    return f"Tool server '{server.name}' could not be {verb}: {why}"


def _check_read_only(servers: Sequence[ToolServer]) -> None:
    """A name in ``read_only`` that the server does not provide is a typo; fail loudly."""
    for server in servers:
        provided = {tool.name for tool in server.tools}
        missing = [name for name in server.config.read_only if name not in provided]
        if missing:
            listed = ", ".join(f"'{name}'" for name in missing)
            raise ToolServerError(
                f"Tool server '{server.name}': read_only names {listed}, which the server does "
                "not provide."
            )


def _check_names(servers: Sequence[ToolServer], reserved: frozenset[str]) -> None:
    providers: dict[str, list[str]] = {}
    for server in servers:
        for tool in server.tools:
            if not TOOL_NAME_PATTERN.fullmatch(tool.name):
                raise ToolServerError(
                    f"Tool server '{server.name}': the tool name '{tool.name}' cannot be offered "
                    "to a model. A name must be 1 to 64 letters, digits, hyphens, or underscores."
                )
            if tool.name in reserved:
                raise ToolServerError(
                    f"Tool server '{server.name}': the tool name '{tool.name}' is already used "
                    "by one of Freyja's own tools."
                )
            providers.setdefault(tool.name, []).append(server.name)
    for tool_name, names in providers.items():
        if len(names) > 1:
            raise ToolServerError(
                f"The tool name '{tool_name}' is provided more than once, by tool servers "
                f"{', '.join(repr(name) for name in names)}. Tool names must be unique."
            )


async def _close_all(servers: Sequence[ToolServer]) -> None:
    await asyncio.gather(*(server.close() for server in servers), return_exceptions=True)


@asynccontextmanager
async def open_tool_servers(
    configs: Mapping[str, McpServerConfig],
    *,
    connect: Connect = default_connect,
    call_timeout: float,
    startup_timeout: float,
    reserved_names: frozenset[str] = RESERVED_TOOL_NAMES,
) -> AsyncIterator[tuple[ToolServer, ...]]:
    """Connect to every configured server and yield the links; close them on exit.

    Raises ``ToolServerError`` naming the server, or every server, that cannot be used
    (research R4). Nothing is left open when it raises.
    """
    secrets = {name: _resolve_pass_env(name, config) for name, config in configs.items()}
    servers = [
        ToolServer(name, config, connect=connect, call_timeout=call_timeout, secrets=secrets[name])
        for name, config in configs.items()
    ]
    with _sdk_logs_redacted(value for values in secrets.values() for value in values):
        async with _opened(servers, startup_timeout, reserved_names):
            yield tuple(servers)


@asynccontextmanager
async def _opened(
    servers: Sequence[ToolServer], startup_timeout: float, reserved_names: frozenset[str]
) -> AsyncIterator[None]:
    """Start every server, check the result against the configuration, and close on exit."""
    outcomes = await asyncio.gather(
        *(server.start(startup_timeout) for server in servers), return_exceptions=True
    )
    failures = [
        (server, outcome)
        for server, outcome in zip(servers, outcomes, strict=True)
        if isinstance(outcome, BaseException)
    ]
    if failures:
        await _close_all(servers)
        raise ToolServerError(
            "; ".join(_start_failure(server, exc, startup_timeout) for server, exc in failures)
        )
    try:
        _check_read_only(servers)
        _check_names(servers, reserved_names)
        for server in servers:
            read_only = sum(tool.name in server.config.read_only for tool in server.tools)
            logger.info(
                "Tool server %s (%s): %d tools, %d read-only",
                server.name,
                server.connection,
                len(server.tools),
                read_only,
            )
        yield
    finally:
        await _close_all(servers)
