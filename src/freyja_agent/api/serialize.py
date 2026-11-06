"""LangChain and LangGraph objects to the JSON shapes in data-model.md."""

import dataclasses
from datetime import date, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.types import Interrupt
from pydantic import BaseModel

from freyja_agent.agent.catalog import Catalog

_DECISION_ORDER = ("approve", "edit", "reject")

# Message chunks report their own type names; the API exposes the base message type.
_CHUNK_TYPES = {
    "AIMessageChunk": "ai",
    "HumanMessageChunk": "human",
    "ToolMessageChunk": "tool",
    "SystemMessageChunk": "system",
}


def message_to_dict(message: BaseMessage) -> dict[str, Any]:
    """Serialize one message as the ``Message`` API shape."""
    data: dict[str, Any] = {
        "id": message.id,
        "type": _CHUNK_TYPES.get(message.type, message.type),
        "content": to_jsonable(message.content),
        "name": message.name,
    }
    if isinstance(message, AIMessage):
        data["tool_calls"] = [
            {"id": call.get("id"), "name": call["name"], "args": to_jsonable(call["args"])}
            for call in message.tool_calls
        ]
    if isinstance(message, ToolMessage):
        data["tool_call_id"] = message.tool_call_id
    return data


def update_to_dict(update: Any) -> Any:
    """Serialize one node's state update from an ``updates`` stream chunk."""
    return to_jsonable(update)


def interrupt_to_pending(interrupt: Interrupt, catalog: Catalog) -> dict[str, Any]:
    """Map a human-in-the-loop interrupt to the ``PendingDecision`` API shape.

    Each action names the tool server its tool belongs to and carries its own allowed
    decisions, both read from ``catalog``: the tools in force now. A proposal can be shown and
    decided after a restart with another configuration, and the approval gate runs again under
    that configuration when the thread resumes, so the stored proposal's own lists are not
    what a decision is checked against (feature 002, FR-008, FR-019; research R9).
    """
    value = interrupt.value if isinstance(interrupt.value, dict) else {}
    action_requests = []
    for request in value.get("action_requests", []):
        name = request.get("name")
        tool = catalog.get(name)
        action_requests.append(
            {
                "name": name,
                "server": tool.server if tool is not None else None,
                "args": to_jsonable(request.get("args", {})),
                "description": request.get("description"),
                "allowed_decisions": list(catalog.decisions_for(name)),
            }
        )
    # The union, in the contract's canonical order; anything unknown keeps its own order last.
    union = {decision for request in action_requests for decision in request["allowed_decisions"]}
    allowed = [d for d in _DECISION_ORDER if d in union] + sorted(union - set(_DECISION_ORDER))
    return {
        "interrupt_id": interrupt.id,
        "action_requests": action_requests,
        "allowed_decisions": allowed,
    }


def to_jsonable(value: Any) -> Any:
    """Recursively convert messages, models, dataclasses, and dates to JSON-safe values."""
    if isinstance(value, BaseMessage):
        return message_to_dict(value)
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
