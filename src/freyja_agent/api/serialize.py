"""LangChain and LangGraph objects to the JSON shapes in data-model.md."""

import dataclasses
from datetime import date, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.types import Interrupt
from pydantic import BaseModel

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


def interrupt_to_pending(interrupt: Interrupt) -> dict[str, Any]:
    """Map a human-in-the-loop interrupt to the ``PendingDecision`` API shape."""
    value = interrupt.value if isinstance(interrupt.value, dict) else {}
    action_requests = [
        {
            "name": request.get("name"),
            "args": to_jsonable(request.get("args", {})),
            "description": request.get("description"),
        }
        for request in value.get("action_requests", [])
    ]
    allowed: list[str] = []
    for config in value.get("review_configs", []):
        for decision in config.get("allowed_decisions", []):
            if decision not in allowed:
                allowed.append(decision)
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
