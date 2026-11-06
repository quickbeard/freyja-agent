"""Validate resume decisions against the pending proposal and build the resume command.

The human-in-the-loop middleware's batched mode does not pin the tool name or validate edited
arguments, so an edit could otherwise switch tools or pass arguments the tool cannot accept.
This module closes that gap before the run resumes (FR-012, FR-013; research R2, R16).
"""

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from langchain_core.tools import BaseTool
from langgraph.types import Command
from pydantic import ValidationError

from freyja_agent.api.schemas import ApproveDecision, EditDecision, RejectDecision

AnyDecision = ApproveDecision | EditDecision | RejectDecision


def _invalid(errors: list[dict[str, Any]]) -> HTTPException:
    """A 422 whose body matches FastAPI's own validation error shape."""
    return HTTPException(status_code=422, detail=errors)


def build_resume_command(
    pending: Mapping[str, Any],
    decisions: Sequence[AnyDecision],
    tools: Mapping[str, BaseTool],
) -> Command:
    """Turn validated API decisions into the ``Command`` that resumes the paused run.

    ``pending`` is the ``PendingDecision`` shape; ``tools`` maps gated tool names to the tool
    objects whose ``args_schema`` validates edited arguments. Raises an HTTP 422 with a list
    of field errors on any mismatch, leaving the pending decision untouched.
    """
    requests = list(pending["action_requests"])
    allowed = list(pending["allowed_decisions"])
    if len(decisions) != len(requests):
        raise _invalid(
            [
                {
                    "type": "value_error",
                    "loc": ["body", "decisions"],
                    "msg": (
                        f"Expected {len(requests)} decision(s), one per pending action request "
                        f"in order; got {len(decisions)}."
                    ),
                }
            ]
        )

    payload: list[dict[str, Any]] = []
    for index, (decision, request) in enumerate(zip(decisions, requests, strict=True)):
        # Each action has its own allowed set; the pending-level list is their union.
        allowed_here = list(request.get("allowed_decisions") or allowed)
        if decision.type not in allowed_here:
            raise _invalid(
                [
                    {
                        "type": "value_error",
                        "loc": ["body", "decisions", index, "type"],
                        "msg": (
                            f"Decision '{decision.type}' is not allowed for action "
                            f"'{request['name']}'; expected one of {allowed_here}."
                        ),
                    }
                ]
            )
        if isinstance(decision, ApproveDecision):
            payload.append({"type": "approve"})
        elif isinstance(decision, RejectDecision):
            entry: dict[str, Any] = {"type": "reject"}
            if decision.message:
                entry["message"] = decision.message
            payload.append(entry)
        else:
            payload.append(_edit(decision, request, index, tools))
    return Command(resume={pending["interrupt_id"]: {"decisions": payload}})


def _edit(
    decision: EditDecision, request: Mapping[str, Any], index: int, tools: Mapping[str, BaseTool]
) -> dict[str, Any]:
    name = request["name"]
    tool = tools.get(name)
    if tool is None or tool.args_schema is None:
        raise _invalid(
            [
                {
                    "type": "value_error",
                    "loc": ["body", "decisions", index],
                    "msg": f"Action '{name}' cannot be edited.",
                }
            ]
        )
    errors: list[dict[str, Any]] = []
    try:
        validated = tool.args_schema.model_validate(decision.args)
    except ValidationError as exc:
        errors = [
            {**error, "loc": ["body", "decisions", index, "args", *error["loc"]]}
            for error in exc.errors(include_url=False, include_context=False)
        ]
    # The schema ignores keys it does not define, which would drop part of the approver's
    # instruction without a word. An edit is refused instead (FR-013).
    errors += [
        {
            "type": "extra_forbidden",
            "loc": ["body", "decisions", index, "args", key],
            "msg": f"Action '{name}' has no argument named '{key}'.",
        }
        for key in decision.args
        if key not in tool.args_schema.model_fields
    ]
    if errors:
        raise _invalid(errors)
    # The tool name is pinned server-side: an edit changes arguments, never the action.
    return {"type": "edit", "edited_action": {"name": name, "args": validated.model_dump()}}
