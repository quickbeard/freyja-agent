"""Request and response models for the thread endpoints (contracts/openapi.yaml)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# Client-chosen, opaque. Letters, digits, hyphen, underscore, dot; 1 to 128 chars (FR-001a).
THREAD_ID_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"

ThreadId = Annotated[str, Field(pattern=THREAD_ID_PATTERN)]


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


class ActionRequest(BaseModel):
    name: str
    args: dict[str, Any]
    description: str | None = None


class PendingDecision(BaseModel):
    interrupt_id: str
    action_requests: list[ActionRequest]
    allowed_decisions: list[Literal["approve", "edit", "reject"]]


class ThreadState(BaseModel):
    thread_id: str
    run_active: bool
    messages: list[Message]
    pending: PendingDecision | None
    updated_at: str | None = None


class ErrorDetail(BaseModel):
    code: Literal["run_active", "decision_pending", "nothing_pending", "thread_not_found"]
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail
