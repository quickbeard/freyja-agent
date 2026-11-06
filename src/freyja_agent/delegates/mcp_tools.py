"""MCP tools as tools the orchestrator can call (research R5).

One ``DelegateTool`` per tool a server lists at startup. The LangChain tool inside it keeps
the server's own name and JSON Schema; LangChain does not validate arguments against a dict
schema, so the server does. Every way a call can fail is turned into a ``ToolException``,
which the tool node delivers to the orchestrator as an error result: nothing a tool server
does can end a run (FR-012).
"""

import json
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.tools import StructuredTool, ToolException

from freyja_agent.agent.catalog import Classification

# The annotations a server may publish about a tool, under the names the catalog shows.
_HINT_FIELDS = {
    "title": "title",
    "read_only_hint": "read_only",
    "destructive_hint": "destructive",
    "idempotent_hint": "idempotent",
    "open_world_hint": "open_world",
}


class ServerLink(Protocol):
    """What a converted tool needs from the link to its server."""

    name: str

    def redact(self, text: str) -> str: ...

    def call(self, tool: str, arguments: dict[str, Any]) -> Awaitable[Any]: ...


@dataclass(frozen=True)
class DelegateTool:
    """A tool provided by a tool server (data-model.md "Delegate tool")."""

    name: str
    server: str
    description: str
    input_schema: dict[str, Any]
    server_hints: dict[str, Any] | None
    classification: Classification
    tool: StructuredTool = field(repr=False, compare=False)


def classify(name: str, read_only: Collection[str]) -> Classification:
    """The whole rule for a server's tool: read-only if the operator listed it (FR-006)."""
    return Classification.READ_ONLY if name in read_only else Classification.GATED


def server_hints(annotations: Any) -> dict[str, Any] | None:
    """The hints a server set about its own tool, or None when it set none (research R10)."""
    if annotations is None:
        return None
    hints = {
        shown: value
        for attribute, shown in _HINT_FIELDS.items()
        if (value := getattr(annotations, attribute, None)) is not None
    }
    return hints or None


def _block_text(block: Any) -> str:
    """One content block as text: its text, or a note naming the kind of content left out."""
    kind = getattr(block, "type", None)
    if kind == "text":
        return block.text or ""
    mime_type = getattr(block, "mime_type", None) or "unknown type"
    if kind == "image":
        return f"[image omitted: {mime_type}]"
    if kind == "audio":
        return f"[audio omitted: {mime_type}]"
    if kind == "resource":
        resource = getattr(block, "resource", None)
        text = getattr(resource, "text", None)
        if text is not None:
            return text
        return f"[file omitted: {getattr(resource, 'mime_type', None) or 'unknown type'}]"
    if kind == "resource_link":
        return f"[resource link omitted: {getattr(block, 'uri', '')}]"
    return f"[{kind or 'unknown'} content omitted]"


def result_text(result: Any) -> str:
    """A tool result as the one string the orchestrator receives (FR-018, research R6).

    The content blocks in order, joined with blank lines. Text stays text; an image, audio, a
    binary file, or a link becomes a short note naming its kind, so the orchestrator knows
    something was left out and nothing binary reaches the model or the thread. Structured
    content is added as JSON only when the result has no text block: a server that sends
    structured content normally sends the same data as text too, and it must not arrive twice.
    """
    blocks = list(getattr(result, "content", None) or [])
    parts = [_block_text(block) for block in blocks]
    structured = getattr(result, "structured_content", None)
    if structured is not None and not any(getattr(b, "type", None) == "text" for b in blocks):
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    return "\n\n".join(part for part in parts if part)


def _coroutine(link: ServerLink, name: str) -> Callable[..., Awaitable[str]]:
    async def call(**arguments: Any) -> str:
        try:
            result = await link.call(name, arguments)
        except ToolException:
            raise  # the link has already said what happened, in text that is safe to show
        except Exception:  # every failure becomes an error result; none ends a run
            raise ToolException(
                f"Tool server '{link.name}' could not complete the call to '{name}'."
            ) from None
        text = link.redact(result_text(result))
        if getattr(result, "is_error", False):
            raise ToolException(text or f"Tool '{name}' on server '{link.name}' failed.")
        return text

    return call


def to_delegate_tool(
    link: ServerLink,
    listed: Any,
    *,
    read_only: Collection[str],
    redact: Callable[[Any], Any],
) -> DelegateTool:
    """Convert one tool from a server's listing.

    ``redact`` is applied to everything the server supplied about the tool, so neither the
    model nor the catalog sees a credential the server was given (FR-002).
    """
    description = redact(listed.description or "")
    input_schema = redact(dict(listed.input_schema or {"type": "object", "properties": {}}))
    hints = redact(server_hints(listed.annotations))
    tool = StructuredTool(
        name=listed.name,
        description=description,
        args_schema=input_schema,
        coroutine=_coroutine(link, listed.name),
        handle_tool_error=True,
    )
    return DelegateTool(
        name=listed.name,
        server=link.name,
        description=description,
        input_schema=input_schema,
        server_hints=hints,
        classification=classify(listed.name, read_only),
        tool=tool,
    )
