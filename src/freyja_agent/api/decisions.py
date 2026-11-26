"""Validate resume decisions against the pending proposal and build the resume command.

The human-in-the-loop middleware's batched mode does not pin the tool name or validate edited
arguments, so an edit could otherwise switch tools or pass arguments the tool cannot accept.
This module closes that gap before the run resumes (feature 001: FR-012, FR-013; research R2,
R16).

An edit may not change a pinned argument, such as the task an agent's answer or cancel is for
(feature 003: FR-013; research R12).

It also applies a decision under the configuration in force (feature 002: FR-009, FR-019;
research R9). A proposal can be decided after a restart with another server list, and the gate
then runs again with the new configuration. A decision for a call the gate no longer asks about
would be ignored without a word, so such a decision is checked here and left out of what the
gate receives.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from jsonschema.validators import validator_for
from langgraph.types import Command, Interrupt
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
            payload.append(_edit(decision, tool, index, request.get("args") or {}))
    return Command(resume={pending["interrupt_id"]: {"decisions": payload}})


# What a task writes that is not a result of its own: the interrupt it raised, the decisions
# it was resumed with, and an error. A task that wrote anything else has completed.
_NOT_A_RESULT = frozenset({"__interrupt__", "__resume__", "__error__"})


async def decisions_taken(
    checkpointer: Any, thread_id: str, namespace: str, interrupt_id: str
) -> list[Any] | None:
    """The decisions the gate that raised ``interrupt_id`` already took, or None.

    A resume runs the gate's step with the person's decisions, and LangGraph keeps the step's
    writes as it completes. When a later step of the same run then fails, inside a subagent,
    the orchestrator's step that runs the subagent has not completed, so the thread still
    shows the proposal. A resume after that continues from the subagent's checkpoint: the
    gate's step is not run again, and its writes, made with the earlier decisions, are used
    whatever the new resume says. Those decisions are the step's ``__resume__`` write.

    None when the gate's step has not completed: its decisions are then the next resume's.
    """
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace}}
    found = await checkpointer.aget_tuple(config)
    if found is None:
        return None
    writes = list(found.pending_writes or [])
    raised = {
        task
        for task, channel, value in writes
        if channel == "__interrupt__"
        and any(getattr(item, "id", None) == interrupt_id for item in value or [])
    }
    for task in raised:
        channels = {channel for written, channel, _ in writes if written == task}
        if channels - _NOT_A_RESULT:
            return next(
                (
                    value
                    for written, channel, value in writes
                    if (written, channel) == (task, "__resume__")
                ),
                None,
            )
    return None


def decided_calls(
    tasks: Sequence[Any], interrupt: Any, decisions: Sequence[AnyDecision]
) -> dict[str, Any] | None:
    """What a resume tells the step after the gate (``AgentTasksMiddleware``; 005 FR-022).

    The namespace of the gate whose interrupt the resume answers, and the calls of that
    interrupt the person approved or edited, by name and arguments as the interrupt holds
    them, which is how they were shown. The resume route adds the ids of the paused reply's
    calls (``calls``). None when no paused task holds the interrupt; nothing is then
    recorded as decided.
    """
    namespace = decided_namespace(tasks, interrupt.id)
    if namespace is None:
        return None
    value = interrupt.value if isinstance(interrupt.value, Mapping) else {}
    requests = list(value.get("action_requests") or [])
    approved = [
        {"name": request.get("name"), "args": request.get("args")}
        for request, decision in zip(requests, decisions, strict=False)
        if isinstance(request, Mapping) and isinstance(decision, ApproveDecision | EditDecision)
    ]
    return {"namespace": namespace, "approved": approved}


def decided_namespace(tasks: Sequence[Any], interrupt_id: str) -> str | None:
    """The namespace of the graph whose approval gate raised the interrupt ``interrupt_id``.

    It is the namespace a proposed call of that gate is kept under (``call_namespace``):
    empty when the gate is the orchestrator's own, and the paused task's when the gate is a
    subagent's, which runs inside that task. A resume marks as decided only the calls of
    that namespace (005 FR-022): a sibling subagent that stopped between its gate and the
    step after it is continued by the same resume, and nobody decided its calls.

    An interrupt's id is made from the namespace that raised it, so the orchestrator's own
    is known by its id. None when no paused task holds the interrupt; nothing is then
    marked.
    """
    for task in tasks:
        if not any(found.id == interrupt_id for found in task.interrupts):
            continue
        own = f"{task.name}:{task.id}"
        return "" if Interrupt.from_ns(None, own).id == interrupt_id else own
    return None


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


def _edit(
    decision: EditDecision, tool: CatalogTool, index: int, proposed: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate edited arguments against the tool's declared input and build the gate's edit.

    The tool name is pinned server-side: an edit changes arguments, never the action, the
    server, or the agent it belongs to (FR-009; feature 003, FR-013). So are the tool's pinned
    arguments, checked once the arguments are valid.
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
    _check_pinned(args, proposed, tool, index)
    return {"type": "edit", "edited_action": {"name": tool.name, "args": args}}


def _check_pinned(
    args: Mapping[str, Any], proposed: Mapping[str, Any], tool: CatalogTool, index: int
) -> None:
    """Refuse an edit whose value for a pinned argument differs from the proposal's.

    An answer or a cancel is for one task of one agent; an edit that named another task would
    send a message the approver was never shown for that task. An argument left out counts as
    its default, so it too must equal the proposed value (feature 003, FR-013; research R12).
    """
    errors = [
        {
            "type": "value_error",
            "loc": ["body", "decisions", index, "args", key],
            "msg": (
                f"Action '{tool.name}': '{key}' cannot be changed by an edit; it must stay "
                f"{json.dumps(proposed.get(key))}, as proposed."
            ),
        }
        for key in tool.pinned_args
        if args.get(key) != proposed.get(key)
    ]
    if errors:
        raise _invalid(errors)


def _unknown_argument(name: str, key: str, index: int) -> dict[str, Any]:
    return {
        "type": "extra_forbidden",
        "loc": ["body", "decisions", index, "args", key],
        "msg": f"Action '{name}' has no argument named '{key}'.",
    }


def _validated_by_model(
    args: dict[str, Any], schema: type[BaseModel], name: str, index: int
) -> dict[str, Any]:
    """Freyja's own tool or an agent's: its arguments are described by a pydantic model."""
    errors: list[dict[str, Any]] = []
    validated: BaseModel | None = None
    try:
        validated = schema.model_validate(args)
    except ValidationError as exc:
        errors = [
            {**error, "loc": ["body", "decisions", index, "args", *error["loc"]]}
            for error in exc.errors(include_url=False, include_context=False)
        ]
    # What the model refused, kept whole: the entries below may leave some of it out.
    refused = errors
    # The model ignores keys it does not define, which would drop part of the approver's
    # instruction without a word. An edit is refused instead (FR-013). A model that forbids
    # them, such as an agent's hand-off, reports them too; its entry says less than the one
    # below, so it is left out and each key is reported once.
    top_level = ["body", "decisions", index, "args"]
    errors = [
        error
        for error in errors
        if not (error["type"] == "extra_forbidden" and error["loc"][:-1] == top_level)
    ]
    errors += [
        _unknown_argument(name, key, index) for key in args if key not in schema.model_fields
    ]
    if errors or validated is None:
        # The arguments are never let through when the model refused them, also when every
        # entry of its refusal was left out above and none took its place.
        raise _invalid(errors or refused)
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
                required = error.validator_value
                missing = [
                    key
                    for key in (required if isinstance(required, Iterable) else ())
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
