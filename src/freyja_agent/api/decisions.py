"""Validate resume decisions against the pending proposal and build the resume command.

The human-in-the-loop middleware's batched mode does not pin the tool name or validate edited
arguments, so an edit could otherwise switch tools or pass arguments the tool cannot accept.
This module closes that gap before the run resumes (feature 001: FR-012, FR-013; research R2,
R16).

It also applies a decision under the configuration in force (feature 002: FR-009, FR-019;
research R9). A proposal can be decided after a restart with another server list, and the gate
then runs again with the new configuration. A decision for a call the gate no longer asks about
would be ignored without a word, so such a decision is checked here and left out of what the
gate receives.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from jsonschema.validators import validator_for
from langgraph.types import Command
from pydantic import BaseModel, ValidationError
from referencing import Registry
from referencing.exceptions import NoSuchResource

from freyja_agent.agent.catalog import Catalog, CatalogTool
from freyja_agent.api.schemas import ApproveDecision, EditDecision, RejectDecision

AnyDecision = ApproveDecision | EditDecision | RejectDecision


def _invalid(errors: list[dict[str, Any]]) -> HTTPException:
    """A 422 whose body matches FastAPI's own validation error shape."""
    return HTTPException(status_code=422, detail=errors)


def build_resume_command(
    pending: Mapping[str, Any],
    decisions: Sequence[AnyDecision],
    catalog: Catalog,
) -> Command:
    """Turn validated API decisions into the ``Command`` that resumes the paused run.

    ``pending`` is the ``PendingDecision`` shape, computed against ``catalog``; ``catalog``
    holds the tools the orchestrator has now, whose declared inputs validate edited arguments.
    Raises an HTTP 422 with a list of field errors on any mismatch, leaving the pending
    decision untouched.

    There is one decision per listed action, in order. The command carries only the decisions
    for the actions that are still gated, because those are the ones the gate asks for when it
    runs again. A tool that was removed is answered by the tool node as unknown and never
    called; a tool that is now read-only runs as proposed.
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
        name = request["name"]
        tool = catalog.get(name)
        # Each action has its own allowed set; the pending-level list is their union.
        allowed_here = list(request.get("allowed_decisions") or allowed)
        if decision.type not in allowed_here:
            raise _invalid(
                [
                    {
                        "type": "value_error",
                        "loc": ["body", "decisions", index, "type"],
                        "msg": _not_allowed(name, decision.type, allowed_here, tool),
                    }
                ]
            )
        if tool is None or not tool.gated:
            # The gate will not ask about this call, so it gets no decision.
            continue
        if isinstance(decision, ApproveDecision):
            payload.append({"type": "approve"})
        elif isinstance(decision, RejectDecision):
            entry: dict[str, Any] = {"type": "reject"}
            if decision.message:
                entry["message"] = decision.message
            payload.append(entry)
        else:
            payload.append(_edit(decision, tool, index))
    return Command(resume={pending["interrupt_id"]: {"decisions": payload}})


def _not_allowed(name: str, decision: str, allowed: list[str], tool: CatalogTool | None) -> str:
    expected = f"expected one of {allowed}"
    if tool is None:
        return (
            f"Tool '{name}' is no longer configured, so it will not be called whatever is "
            f"decided. Decision '{decision}' is not allowed for it; {expected}."
        )
    if not tool.gated:
        return (
            f"Tool '{name}' no longer needs a decision under the current configuration and runs "
            f"as proposed. Decision '{decision}' is not allowed for it; {expected}."
        )
    return f"Decision '{decision}' is not allowed for action '{name}'; {expected}."


def _edit(decision: EditDecision, tool: CatalogTool, index: int) -> dict[str, Any]:
    """Validate edited arguments against the tool's declared input and build the gate's edit.

    The tool name is pinned server-side: an edit changes arguments, never the action or the
    server it belongs to (FR-009).
    """
    schema = tool.tool.args_schema if tool.tool is not None else None
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        args = _validated_by_model(decision.args, schema, tool.name, index)
    elif isinstance(schema, Mapping):
        args = _validated_by_json_schema(decision.args, schema, tool.name, index)
    else:
        raise _invalid(
            [
                {
                    "type": "value_error",
                    "loc": ["body", "decisions", index],
                    "msg": f"Action '{tool.name}' cannot be edited.",
                }
            ]
        )
    return {"type": "edit", "edited_action": {"name": tool.name, "args": args}}


def _unknown_argument(name: str, key: str, index: int) -> dict[str, Any]:
    return {
        "type": "extra_forbidden",
        "loc": ["body", "decisions", index, "args", key],
        "msg": f"Action '{name}' has no argument named '{key}'.",
    }


def _validated_by_model(
    args: dict[str, Any], schema: type[BaseModel], name: str, index: int
) -> dict[str, Any]:
    """One of Freyja's own tools: its arguments are described by a pydantic model."""
    errors: list[dict[str, Any]] = []
    try:
        validated = schema.model_validate(args)
    except ValidationError as exc:
        errors = [
            {**error, "loc": ["body", "decisions", index, "args", *error["loc"]]}
            for error in exc.errors(include_url=False, include_context=False)
        ]
    # The model ignores keys it does not define, which would drop part of the approver's
    # instruction without a word. An edit is refused instead (FR-013).
    errors += [
        _unknown_argument(name, key, index) for key in args if key not in schema.model_fields
    ]
    if errors:
        raise _invalid(errors)
    return validated.model_dump()


def _no_remote_reference(uri: str) -> Any:
    """A tool server's schema is never a reason to fetch something from the network."""
    raise NoSuchResource(ref=uri)


def _validated_by_json_schema(
    args: dict[str, Any], schema: Mapping[str, Any], name: str, index: int
) -> dict[str, Any]:
    """A tool server's tool: its arguments are described by the server's JSON Schema.

    The arguments are sent to the server as given, so nothing is dropped; but a server that
    ignores a key it does not know would turn a mistyped name into a call the approver did not
    mean. An argument the schema's ``properties`` do not name is therefore refused, unless the
    schema itself says it accepts more (FR-009, research R9).
    """
    prefix = ["body", "decisions", index, "args"]
    additional = schema.get("additionalProperties")
    accepts_more = (
        additional is True
        or isinstance(additional, Mapping)
        or bool(schema.get("patternProperties"))
    )
    errors: list[dict[str, Any]] = []
    if not accepts_more:
        named = schema.get("properties") or {}
        errors = [_unknown_argument(name, key, index) for key in args if key not in named]
    try:
        validator = validator_for(schema)(schema, registry=Registry(retrieve=_no_remote_reference))
        for error in validator.iter_errors(args):
            path = list(error.absolute_path)
            if error.validator == "additionalProperties" and not path and not accepts_more:
                continue  # already reported above, one entry per argument
            if error.validator == "required":
                missing = [
                    key
                    for key in error.validator_value
                    if isinstance(error.instance, Mapping)
                    and key not in error.instance
                    and repr(key) in error.message
                ]
                errors.append(
                    {"type": "missing", "loc": [*prefix, *path, *missing[:1]], "msg": error.message}
                )
            else:
                errors.append(
                    {"type": "value_error", "loc": [*prefix, *path], "msg": error.message}
                )
    except Exception as exc:  # a schema that cannot be used must not let an edit through
        raise _invalid(
            [
                {
                    "type": "value_error",
                    "loc": ["body", "decisions", index],
                    "msg": (
                        f"Action '{name}' cannot be edited: its declared input could not be "
                        f"used to check the arguments ({type(exc).__name__})."
                    ),
                }
            ]
        ) from None
    if errors:
        raise _invalid(sorted(errors, key=lambda entry: [str(part) for part in entry["loc"]]))
    return dict(args)
