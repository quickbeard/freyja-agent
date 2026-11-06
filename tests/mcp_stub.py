"""A stand-in MCP server for the suite, the transport tests, and the quickstart (research R13).

``make_stub()`` returns a real MCP server object. Handed to ``mcp.Client`` it is connected
inside the test process, so both sides run the actual protocol code and only the byte
transport is missing: no network, no subprocess, nothing to install. The stub records every
call it receives, which is how a test proves what did and did not reach a server.

Run as a module it serves the same tools over pipes, or over HTTP on the loopback interface::

    python -m tests.mcp_stub               # stdio
    python -m tests.mcp_stub --http 8765   # http://127.0.0.1:8765/mcp
"""

import argparse
import asyncio
import base64
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Annotated, Any

from mcp.server.mcpserver import Audio, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import BlobResourceContents, EmbeddedResource, ToolAnnotations
from pydantic import BaseModel, Field

# The environment variable the stub reads its credential from.
STUB_ENV = "STUB_TOKEN"

# One transparent pixel; small, valid enough for a content block.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_WAV = b"RIFF$\x00\x00\x00WAVEfmt "
_BINARY = b"\x00\x01\x02stub-attachment"


STANDARD_TOOLS = (
    "lookup",
    "record_note",
    "ping",
    "file_report",
    "fail",
    "slow",
    "picture",
    "sound",
    "attachment",
    "stats",
    "whoami",
)


class ReportOptions(BaseModel):
    tags: list[str]
    draft: bool = False


class StubServer(MCPServer):
    """An MCP server that remembers what it was asked to do."""

    def __init__(self, name: str) -> None:
        super().__init__(name, log_level="ERROR")
        # (tool name, arguments) for every call that reached the server, in order.
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # What the effectful tools did, so a test can tell an effect from a mere call.
        self.notes: list[dict[str, Any]] = []

    def record(self, tool: str, **arguments: Any) -> None:
        self.calls.append((tool, arguments))

    def called(self, tool: str) -> list[dict[str, Any]]:
        return [arguments for name, arguments in self.calls if name == tool]


def _token() -> str:
    return os.environ.get(STUB_ENV, "")


def _register_standard_tools(stub: StubServer) -> None:
    @stub.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True))
    def lookup(key: str) -> str:
        """Look up the value stored under a key.

        Reads only; the stub keeps no real store and answers from the key itself.
        """
        stub.record("lookup", key=key)
        return f"value-of-{key}"

    # The server calls this read-only although it changes the stub's notes: a server's own
    # label is exactly what the gate must not believe.
    @stub.tool(annotations=ToolAnnotations(read_only_hint=True))
    def record_note(note: str, priority: int = 0) -> str:
        """Record a note on the server."""
        stub.record("record_note", note=note, priority=priority)
        stub.notes.append({"note": note, "priority": priority})
        return f"recorded note {len(stub.notes)}: {note}"

    @stub.tool()
    def ping() -> str:
        """Ask the server whether it is alive. Takes no arguments."""
        stub.record("ping")
        return "pong"

    @stub.tool()
    def file_report(title: str, opts: ReportOptions) -> str:
        """File a report with a title and options."""
        stub.record("file_report", title=title, opts=opts.model_dump())
        stub.notes.append({"report": title, **opts.model_dump()})
        return f"filed report '{title}' with tags {', '.join(opts.tags)}"

    @stub.tool()
    def fail(reason: str) -> str:
        """Always fails, with the given reason."""
        stub.record("fail", reason=reason)
        raise ToolError(f"stub failure: {reason}")

    @stub.tool()
    async def slow(seconds: float) -> str:
        """Answer after waiting the given number of seconds."""
        stub.record("slow", seconds=seconds)
        await asyncio.sleep(seconds)
        return f"waited {seconds} seconds"

    @stub.tool()
    def picture() -> list[Any]:
        """Return a caption and an image."""
        stub.record("picture")
        return ["a picture of one pixel", Image(data=_PNG, format="png")]

    @stub.tool()
    def sound() -> Audio:
        """Return a short sound and no text."""
        stub.record("sound")
        return Audio(data=_WAV, format="wav")

    @stub.tool()
    def attachment() -> EmbeddedResource:
        """Return a binary file and no text."""
        stub.record("attachment")
        return EmbeddedResource(
            type="resource",
            resource=BlobResourceContents(
                uri="file:///stub/attachment.bin",
                mime_type="application/octet-stream",
                blob=base64.b64encode(_BINARY).decode(),
            ),
        )

    @stub.tool()
    def stats() -> dict[str, int]:
        """Return structured data about the stub."""
        stub.record("stats")
        return {"calls": len(stub.calls), "notes": len(stub.notes)}

    @stub.tool()
    def whoami() -> str:
        """Say which token the server was started with."""
        stub.record("whoami")
        token = _token()
        sys.stderr.write(f"stub: whoami called with token {token}\n")
        sys.stderr.flush()
        return f"token is {token}"


def _register_leaky_tool(stub: StubServer) -> None:
    """A tool whose listing itself carries the token: description, schema text, and a hint."""
    token = _token()

    @stub.tool(
        description=f"Explains the token. The current one is {token}.",
        annotations=ToolAnnotations(title=f"Token help ({token})", read_only_hint=True),
    )
    def token_help(
        topic: Annotated[str, Field(description=f"What to explain; not {token}.")] = "all",
    ) -> str:
        stub.record("token_help", topic=topic)
        return f"help on {topic}"


def make_stub(
    name: str = "stub",
    *,
    only: Iterable[str] | None = None,
    empty: bool = False,
    extra: Mapping[str, Callable[..., Any]] | None = None,
    leak_token_in_listing: bool = False,
) -> StubServer:
    """Build a stub server.

    ``only`` keeps just the named standard tools; ``empty`` serves no tools at all; ``extra``
    adds tools under any name, including one that collides with another tool or that a model
    could not be offered; ``leak_token_in_listing`` adds a tool whose description, input
    schema, and hint contain the value of ``STUB_TOKEN``.
    """
    stub = StubServer(name)
    if not empty:
        _register_standard_tools(stub)
        if only is not None:
            for tool_name in set(STANDARD_TOOLS) - set(only):
                stub.remove_tool(tool_name)
    if leak_token_in_listing:
        _register_leaky_tool(stub)
    for tool_name, function in (extra or {}).items():
        stub.add_tool(function, name=tool_name)
    return stub


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the suite's stub MCP server.")
    parser.add_argument(
        "--http",
        type=int,
        metavar="PORT",
        help="serve Streamable HTTP on 127.0.0.1 at /mcp instead of stdio",
    )
    parser.add_argument("--leak-token-in-listing", action="store_true")
    options = parser.parse_args(argv)
    stub = make_stub(leak_token_in_listing=options.leak_token_in_listing)
    if _token():
        sys.stderr.write(f"stub: started with token {_token()}\n")
        sys.stderr.flush()
    if options.http is None:
        stub.run("stdio")
    else:
        stub.run("streamable-http", host="127.0.0.1", port=options.http)


if __name__ == "__main__":
    main()
