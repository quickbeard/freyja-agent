"""Request and response models for the thread endpoints (contracts/openapi.yaml)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from freyja_agent.config import SERVER_NAME_PATTERN

# Client-chosen, opaque. Letters, digits, hyphen, underscore, dot; 1 to 128 chars; at least one
# character that is not a dot, so "." and ".." never name a thread (FR-001a). The length is a
# separate bound because the pattern is written without lookahead, which the validation
# engine does not support.
THREAD_ID_PATTERN = r"^[A-Za-z0-9._-]*[A-Za-z0-9_-][A-Za-z0-9._-]*$"
THREAD_ID_MAX_LENGTH = 128


class RunRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=32000)]


class ApproveDecision(BaseModel):
    type: Literal["approve"]


class EditDecision(BaseModel):
    type: Literal["edit"]
    args: dict[str, Any]


class RejectDecision(BaseModel):
    type: Literal["reject"]
    message: Annotated[str | None, Field(max_length=4000)] = None


Decision = Annotated[
    ApproveDecision | EditDecision | RejectDecision,
    Field(discriminator="type"),
]


class ResumeRequest(BaseModel):
    # The proposal being decided, as the interrupt event and the thread state report it. A
    # decision sent twice therefore cannot be applied to a later proposal (FR-013).
    interrupt_id: str
    decisions: Annotated[list[Decision], Field(min_length=1)]


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]


class Message(BaseModel):
    id: str | None = None
    type: Literal["human", "ai", "tool", "system"]
    content: str | list[Any]
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


AllowedDecision = Literal["approve", "edit", "reject"]


class ActionRequest(BaseModel):
    name: str
    # The tool server the proposed tool belongs to now; None for Freyja's own tools and for a
    # tool that is no longer configured (feature 002, FR-008, FR-019).
    server: str | None = None
    args: dict[str, Any]
    description: str | None = None
    # The decisions this action accepts; ``PendingDecision.allowed_decisions`` is the union.
    allowed_decisions: list[AllowedDecision] = Field(default_factory=list)


class PendingDecision(BaseModel):
    interrupt_id: str
    action_requests: list[ActionRequest]
    allowed_decisions: list[AllowedDecision]


class ThreadState(BaseModel):
    thread_id: str
    run_active: bool
    messages: list[Message]
    pending: PendingDecision | None
    updated_at: str | None = None


class ServerHints(BaseModel):
    """What a tool server says about its own tool. The server's claim only: it is shown and
    never affects the classification. Only the hints the server set are serialized."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    open_world: bool | None = None


class CatalogTool(BaseModel):
    """One tool the orchestrator can call (feature 002 contracts/openapi.yaml)."""

    name: str
    # The tool server that provides the tool; None for Freyja's own tools.
    server: str | None
    description: str
    # JSON Schema of the tool's arguments; an edit decision is validated against it.
    input_schema: dict[str, Any]
    classification: Literal["read_only", "workspace", "gated"]
    # Empty unless the tool is gated.
    allowed_decisions: list[AllowedDecision]
    server_hints: ServerHints | None


class ToolServerStatus(BaseModel):
    """A configured tool server and the outcome of the most recent attempt to use it."""

    name: Annotated[str, Field(pattern=SERVER_NAME_PATTERN)]
    # Which form of connection the entry uses; nothing else about the entry is shown.
    connection: Literal["command", "address"]
    reachable: bool
    checked_at: datetime
    error: Annotated[str | None, Field(max_length=500)]
    tool_count: Annotated[int, Field(ge=0)]


class ToolCatalog(BaseModel):
    servers: list[ToolServerStatus]
    tools: list[CatalogTool]


class ErrorDetail(BaseModel):
    code: Literal[
        "run_active",
        "decision_pending",
        "nothing_pending",
        "proposal_mismatch",
        "thread_not_found",
        "internal",
    ]
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail
