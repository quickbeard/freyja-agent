"""One run on one thread, as a stream of server-sent events (contracts/sse-events.md).

``run_stream`` owns the run lease for the life of the stream: a heartbeat renews it while
the orchestrator works and a shielded ``finally`` releases it on completion, pause, error,
and client disconnect. The caller acquires the lease before starting the stream so it can
answer 409 without opening a response (research R9, R12).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

import anyio
from langchain.agents.middleware.internal_call_transformer import INTERNAL_CALL_METADATA_KEY
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Interrupt
from sse_starlette import ServerSentEvent

from freyja_agent.agent.agent_tasks import RESUME_KEY
from freyja_agent.api.serialize import (
    interrupt_to_pending,
    message_to_dict,
    to_jsonable,
    update_to_dict,
)
from freyja_agent.llm.guard import ModelError
from freyja_agent.llm.secrets import redact

if TYPE_CHECKING:  # the runtime starts the worker, which runs this stream
    from freyja_agent.llm.active import ActiveModel
    from freyja_agent.runtime.runtime import RunContext, Runtime

logger = logging.getLogger(__name__)

# A background run is started by the service, not a client (feature 003, FR-020).
RunMode = Literal["run", "resume", "background"]
# Told how a run ended: "end", "interrupt", or "error" with its code (feature 003, FR-027).
OnEnd = Callable[[str, str | None], Awaitable[None]]
# Told once that the run's input is in a checkpoint (feature 003, FR-018, research R10).
OnRecorded = Callable[[], Awaitable[None]]
_MESSAGE_METADATA_KEYS = ("langgraph_node", "langgraph_step")
# The code of the stream's error when no usable model is active (contracts/sse-events.md).
# It belongs to the stream, like step_limit and model_error, not to the HTTP refusals.
NO_USABLE_MODEL = "no_usable_model"


def lease_owner(runtime: Runtime, run_id: str) -> str:
    return f"{runtime.instance_id}:{run_id}"


def _event(kind: str, payload: Any) -> ServerSentEvent:
    # EventSourceResponse does not encode data itself; a dict would be sent as a Python repr.
    return ServerSentEvent(data=json.dumps(to_jsonable(payload)), event=kind)


def _start(
    active: ActiveModel, thread_id: str, run_id: str, mode: RunMode, debug: bool
) -> dict[str, Any]:
    """A run's first event, naming the LLM profile it uses (feature 004, FR-024a)."""
    profile = active.profile
    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "mode": mode,
        "debug": debug,
        "profile": {"id": profile.id, "name": profile.name, "source": profile.source},
    }


def _is_model_error(exc: BaseException) -> bool:
    """True when the exception came out of the chat model (provider errors, timeouts).

    Provider SDK exceptions and model-side failures propagate through langchain-core's chat
    model base class; tool and graph failures do not pass through those frames. An error
    that held a profile's key is raised by the model as a ``ModelError`` with the key taken
    out (``llm/guard.py``).
    """
    if isinstance(exc, ModelError):
        return True
    for frame, _ in traceback.walk_tb(exc.__traceback__):
        if frame.f_globals.get("__name__", "").startswith("langchain_core.language_models"):
            return True
    return False


_INTERNAL = {"code": "internal", "message": "The run failed because of an internal error."}

# The node of Freyja's own graph that calls the model (feature 009, contracts/sse-events.md).
MODEL_NODE = "model"


def is_reply(namespace: tuple[str, ...], message: BaseMessage, metadata: dict[str, Any]) -> bool:
    """Whether a streamed piece is text of Freyja's own reply (feature 009, research R13).

    True only for an AI message or chunk with text, from the ``model`` node of Freyja's own
    graph, not of a subagent's at any depth, and not from a call the libraries make for
    themselves, such as the one that summarizes a long conversation. A chunk that only writes
    a call's arguments has no text, and a tools step's messages are no AI messages.
    """
    return (
        not namespace
        and metadata.get("langgraph_node") == MODEL_NODE
        and isinstance(message, AIMessage)
        and not metadata.get(INTERNAL_CALL_METADATA_KEY)
        and metadata.get("lc_source") is None
        and bool(message.text)
    )


# The code of a resume that a tightened approval choice blocks (005 contracts/sse-events.md).
APPROVAL_NOW_REQUIRED = "approval_now_required"
# What the approval middleware raises when a paused step holds another number of calls that
# need a decision than decisions were given (``langchain.agents.middleware.
# human_in_the_loop``). The middleware has no error type of its own for it, so it is known
# by its wording; ``tests/test_a2a_gate.py`` fails the day the library words it differently,
# and such a resume then ends with ``internal`` again (005 research R9).
_DECISIONS_MISMATCH = re.compile(
    r"Number of human decisions \(\d+\) does not match number of hanging tool calls \(\d+\)\."
)


def decisions_mismatch(exc: BaseException) -> bool:
    """True for the approval middleware's refusal of a resume with too few decisions."""
    return isinstance(exc, ValueError) and _DECISIONS_MISMATCH.fullmatch(str(exc)) is not None


async def _now_require_approval(runtime: Runtime, context: RunContext, thread_id: str) -> list[str]:
    """The agents a blocked resume is about: each one the thread's paused step holds a call
    to that needed no decision when it was proposed and whose tool is gated in this run's
    catalog (005 FR-023, research R9). Empty when none is found, or when the proposed calls
    cannot be read: the resume then ends as it always has.
    """
    try:
        calls = await runtime.agent_tasks.list_calls(thread_id)
    except Exception as exc:
        logger.warning(
            "thread %s: the proposed calls could not be read (%s)", thread_id, type(exc).__name__
        )
        return []
    gated = {tool.agent for tool in context.catalog.gated() if tool.agent is not None}
    return sorted({call.agent for call in calls if not call.gated and call.agent in gated})


def _approval_now_required(agents: list[str]) -> dict[str, str]:
    """The error a blocked resume ends with, naming each agent and never an address."""
    named = " and ".join(
        part for part in (", ".join(f"'{name}'" for name in agents[:-1]), f"'{agents[-1]}'") if part
    )
    if len(agents) == 1:
        message = (
            f"This step cannot continue: it holds a message to agent {named} that needed no "
            "approval when it was proposed, and the agent now requires approval. Switch the "
            "agent back, or remove it, then decide the proposal again."
        )
    else:
        message = (
            f"This step cannot continue: it holds messages to agents {named} that needed no "
            "approval when they were proposed, and the agents now require approval. Switch "
            "the agents back, or remove them, then decide the proposal again."
        )
    return {"code": APPROVAL_NOW_REQUIRED, "message": message}


async def take_context(runtime: Runtime, thread_id: str, run_id: str) -> RunContext | None:
    """The context a run uses, taken under its thread's lease; None when it cannot be had.

    The one place a run picks up a change of agents or of LLM profile: the orchestrator is
    brought in step with both here, and the run keeps what it gets, whatever is changed
    while it runs (feature 004, FR-013; feature 005, FR-019). The caller releases the
    context, or hands it to ``run_stream``, which does.

    A failure is logged and answered with None, and the caller still holds the lease. A
    cancellation, such as the service stopping, is not a failure to report: the lease is
    given back here before it goes on, so the thread is not left busy until the lease
    expires.
    """
    try:
        # Feature 009: the thread's own build, the strict one for a caller's thread. A lookup
        # that fails takes no context, so nothing runs.
        if await runtime.caller_of(thread_id) is not None:
            return await runtime.begin_run(thread_id)
        return await runtime.begin_run()
    except Exception:
        logger.exception(
            "run %s on thread %s: the agents or the active LLM profile could not be applied",
            run_id,
            thread_id,
        )
        return None
    except BaseException:
        with anyio.CancelScope(shield=True):
            await runtime.locks.release(thread_id, lease_owner(runtime, run_id))
        raise


async def no_context_stream(
    runtime: Runtime,
    thread_id: str,
    *,
    mode: RunMode,
    debug: bool,
    run_id: str,
    on_end: OnEnd | None = None,
) -> AsyncIterator[ServerSentEvent]:
    """The stream of a run for which no context could be taken. Nothing runs (005 FR-019).

    The caller holds the lease for ``run_id`` and has logged the failure; the stream names
    the profile this instance last applied, reports an internal error, and releases the
    lease.
    """
    owner = lease_owner(runtime, run_id)
    try:
        yield _event("start", _start(runtime.applied_model(), thread_id, run_id, mode, debug))
        yield _event("error", _INTERNAL)
    finally:
        with anyio.CancelScope(shield=True):
            if on_end is not None:
                try:
                    await on_end("error", "internal")
                except Exception:
                    logger.exception("run %s on thread %s: on_end failed", run_id, thread_id)
            await runtime.locks.release(thread_id, owner)


async def run_stream(
    runtime: Runtime,
    thread_id: str,
    *,
    context: RunContext,
    input: Any,
    mode: RunMode,
    debug: bool,
    run_id: str,
    on_end: OnEnd | None = None,
    on_recorded: OnRecorded | None = None,
    decided: dict[str, Any] | None = None,
) -> AsyncIterator[ServerSentEvent]:
    """Stream one run. The caller must already hold the lease for ``run_id``.

    ``context`` is the orchestrator the run uses, taken by the caller under the lease
    (``take_context``): the graph it runs on, the catalog a pause is worded with, and the
    model it names. The stream releases it, beside the lease, however the run ends.

    ``on_end``, when given, is awaited with how the run ended, before the lease is released.
    A client that disconnects first ends the run without it. A run that ends normally, pauses,
    or reaches the step limit first clears the thread's background run retries.

    ``on_recorded``, when given, is awaited once, at the run's first update. With
    ``durability="sync"`` the checkpoint holding the input is written before any node runs, so
    the input is in the thread by then: outcomes it carried count as delivered (FR-020).
    """
    owner = lease_owner(runtime, run_id)
    settings = runtime.settings
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if mode == "resume" and decided is not None:
        # Only a resume carries a person's decisions: which gate they answer, and which of
        # its calls the person approved or edited (``decided_calls``; 005 FR-022).
        configurable[RESUME_KEY] = decided
    config = {"configurable": configurable, "recursion_limit": settings.max_steps}
    stream_modes = ["updates", "messages", "custom"] + (["debug"] if debug else [])

    async def heartbeat() -> None:
        interval = settings.run_lock_ttl_seconds / 3
        while True:
            await asyncio.sleep(interval)
            await runtime.locks.renew(thread_id, owner)

    renewals = asyncio.create_task(heartbeat())
    # How the run ended, for ``on_end``; None while it runs and after a disconnect.
    ended: tuple[str, str | None] | None = None
    try:
        active = context.active
        agent = context.agent
        profile = active.profile
        if not active.usable:
            # A fixed rule ahead of the model (principle VI): the graph is not invoked, so
            # nothing is added to the thread and a pending proposal stays pending. The service
            # never falls back to another profile by itself (FR-016).
            logger.warning(
                "run %s on thread %s (%s) refused: no usable model is active; "
                "LLM profile %s (%s) is not usable: %s",
                run_id,
                thread_id,
                mode,
                profile.name,
                profile.id,
                active.reason,
            )
            ended = ("error", NO_USABLE_MODEL)
            yield _event("start", _start(active, thread_id, run_id, mode, debug))
            yield _event(
                "error",
                {
                    "code": NO_USABLE_MODEL,
                    "message": (
                        "No usable model is active. Activate a usable LLM profile, then send "
                        "the message again."
                    ),
                },
            )
            return
        logger.info(
            "run %s on thread %s (%s) uses LLM profile %s (%s, %s)",
            run_id,
            thread_id,
            mode,
            profile.name,
            profile.id,
            profile.source,
        )
        yield _event("start", _start(active, thread_id, run_id, mode, debug))
        interrupts: tuple[Interrupt, ...] = ()
        try:
            if input is not None and mode != "resume":
                # A run that starts with input leaves whatever step the thread had behind,
                # and with it the calls that step proposed to agents; a paused thread is
                # refused before it gets here. A resume and a background run's retry
                # continue a step, whose calls are read when they are carried out (feature
                # 005, research R8).
                await runtime.agent_tasks.clear_calls(thread_id)
            # Subgraph streaming is on so an agent's progress, written by a hand-off made inside
            # the subagent, reaches the stream. What it adds is filtered back to the 001
            # contract: updates and interrupts come from the root only (feature 003, R7).
            async for namespace, stream_mode, chunk in agent.astream(
                input, config, stream_mode=stream_modes, durability="sync", subgraphs=True
            ):
                if on_recorded is not None and not namespace and stream_mode == "updates":
                    recorded, on_recorded = on_recorded, None
                    try:
                        await recorded()
                    except Exception:
                        # What was not marked is found in the thread by the next run's check.
                        logger.exception(
                            "run %s on thread %s: on_recorded failed", run_id, thread_id
                        )
                if stream_mode == "custom":
                    yield _event("update", {"node": "agent", "data": to_jsonable(chunk)})
                elif namespace and stream_mode in ("updates", "debug"):
                    continue
                elif stream_mode == "updates":
                    if "__interrupt__" in chunk:
                        interrupts = tuple(chunk["__interrupt__"])
                        continue
                    for node, update in chunk.items():
                        if update is None:
                            continue
                        yield _event("update", {"node": node, "data": update_to_dict(update)})
                elif stream_mode == "messages":
                    message, metadata = chunk
                    kept = {k: metadata.get(k) for k in _MESSAGE_METADATA_KEYS}
                    if context.strict:
                        # Feature 009: on a caller's thread, what is Freyja's own reply.
                        kept["reply"] = is_reply(namespace, message, metadata)
                    yield _event(
                        "message",
                        {"message": message_to_dict(message), "metadata": kept},
                    )
                elif stream_mode == "debug":
                    yield _event("debug", chunk)
        except GraphRecursionError:
            logger.warning(
                "run %s on thread %s stopped at the step limit %s",
                run_id,
                thread_id,
                settings.max_steps,
            )
            ended = ("error", "step_limit")
            yield _event(
                "error",
                {
                    "code": "step_limit",
                    "message": (
                        f"Run stopped after reaching the step limit of {settings.max_steps} "
                        "(FREYJA_MAX_STEPS)."
                    ),
                },
            )
            return
        except Exception as exc:
            if _is_model_error(exc):
                # A provider may repeat the key it was sent in its error. The key of the
                # profile this run uses is taken out before the text is logged or sent
                # (feature 004, FR-021, research R9).
                reason = redact(str(exc), active.secrets)
                logger.warning("run %s on thread %s: model error: %s", run_id, thread_id, reason)
                ended = ("error", "model_error")
                yield _event(
                    "error",
                    {"code": "model_error", "message": f"The model request failed: {reason}"},
                )
            else:
                # A step that came to hold a call that needs a decision since it paused is
                # refused by the gate: nothing from it ran, and the proposal stays. Said
                # with the agents it is about, where they can be told (005 FR-023).
                blocked = (
                    await _now_require_approval(runtime, context, thread_id)
                    if decisions_mismatch(exc)
                    else []
                )
                if blocked:
                    logger.warning(
                        "run %s on thread %s (%s) refused: the paused step holds a message "
                        "that needed no approval when it was proposed, and now requires it; "
                        "agents: %s",
                        run_id,
                        thread_id,
                        mode,
                        ", ".join(blocked),
                    )
                    ended = ("error", APPROVAL_NOW_REQUIRED)
                    yield _event("error", _approval_now_required(blocked))
                else:
                    logger.exception("run %s on thread %s failed", run_id, thread_id)
                    ended = ("error", "internal")
                    yield _event("error", _INTERNAL)
            return

        if len(interrupts) > 1:
            logger.error(
                "run %s on thread %s raised %d interrupts", run_id, thread_id, len(interrupts)
            )
            ended = ("error", "internal")
            yield _event(
                "error",
                {
                    "code": "internal",
                    "message": "The run paused on more than one interrupt at once.",
                },
            )
        elif interrupts:
            ended = ("interrupt", None)
            yield _event("interrupt", interrupt_to_pending(interrupts[0], context.catalog))
        else:
            ended = ("end", None)
            yield _event("end", {"status": "completed"})
    finally:
        renewals.cancel()
        # A disconnect cancels this generator; shield the release so it completes.
        with anyio.CancelScope(shield=True):
            if ended is not None and (ended[0] != "error" or ended[1] == "step_limit"):
                # A run that completed, paused, or reached the step limit had the thread's
                # outcomes: a background run's pending retries stop (feature 003, FR-027).
                try:
                    await runtime.agent_tasks.clear_followups(thread_id)
                except Exception:
                    logger.exception(
                        "run %s on thread %s: clearing retries failed", run_id, thread_id
                    )
            if on_end is not None and ended is not None:
                try:
                    await on_end(*ended)
                except Exception:
                    logger.exception("run %s on thread %s: on_end failed", run_id, thread_id)
            try:
                # The links of an earlier set close once the last run that held it ends.
                await context.release()
            except Exception:
                logger.exception(
                    "run %s on thread %s: releasing its agents failed", run_id, thread_id
                )
            await runtime.locks.release(thread_id, owner)
