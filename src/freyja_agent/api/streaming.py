"""One run on one thread, as a stream of server-sent events (contracts/sse-events.md).

``run_stream`` owns the run lease for the life of the stream: a heartbeat renews it while
the orchestrator works and a shielded ``finally`` releases it on completion, pause, error,
and client disconnect. The caller acquires the lease before starting the stream so it can
answer 409 without opening a response (research R9, R12).
"""

import asyncio
import json
import logging
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

import anyio
from langgraph.errors import GraphRecursionError
from langgraph.types import Interrupt
from sse_starlette import ServerSentEvent

from freyja_agent.api.serialize import (
    interrupt_to_pending,
    message_to_dict,
    to_jsonable,
    update_to_dict,
)
from freyja_agent.runtime.runtime import Runtime

logger = logging.getLogger(__name__)

# A background run is started by the service, not a client (feature 003, FR-020).
RunMode = Literal["run", "resume", "background"]
# Told how a run ended: "end", "interrupt", or "error" with its code (feature 003, FR-027).
OnEnd = Callable[[str, str | None], Awaitable[None]]
_MESSAGE_METADATA_KEYS = ("langgraph_node", "langgraph_step")


def lease_owner(runtime: Runtime, run_id: str) -> str:
    return f"{runtime.instance_id}:{run_id}"


def _event(kind: str, payload: Any) -> ServerSentEvent:
    # EventSourceResponse does not encode data itself; a dict would be sent as a Python repr.
    return ServerSentEvent(data=json.dumps(to_jsonable(payload)), event=kind)


def _is_model_error(exc: BaseException) -> bool:
    """True when the exception came out of the chat model (provider errors, timeouts).

    Provider SDK exceptions and model-side failures propagate through langchain-core's chat
    model base class; tool and graph failures do not pass through those frames.
    """
    for frame, _ in traceback.walk_tb(exc.__traceback__):
        if frame.f_globals.get("__name__", "").startswith("langchain_core.language_models"):
            return True
    return False


async def run_stream(
    runtime: Runtime,
    thread_id: str,
    *,
    input: Any,
    mode: RunMode,
    debug: bool,
    run_id: str,
    on_end: OnEnd | None = None,
) -> AsyncIterator[ServerSentEvent]:
    """Stream one run. The caller must already hold the lease for ``run_id``.

    ``on_end``, when given, is awaited with how the run ended, before the lease is released.
    A client that disconnects first ends the run without it.
    """
    owner = lease_owner(runtime, run_id)
    settings = runtime.settings
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.max_steps}
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
        yield _event(
            "start", {"thread_id": thread_id, "run_id": run_id, "mode": mode, "debug": debug}
        )
        interrupts: tuple[Interrupt, ...] = ()
        try:
            # Subgraph streaming is on so an agent's progress, written by a hand-off made inside
            # the subagent, reaches the stream. What it adds is filtered back to the 001
            # contract: updates and interrupts come from the root only (feature 003, R7).
            async for namespace, stream_mode, chunk in runtime.agent.astream(
                input, config, stream_mode=stream_modes, durability="sync", subgraphs=True
            ):
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
                    yield _event(
                        "message",
                        {
                            "message": message_to_dict(message),
                            "metadata": {k: metadata.get(k) for k in _MESSAGE_METADATA_KEYS},
                        },
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
                logger.warning("run %s on thread %s: model error: %s", run_id, thread_id, exc)
                ended = ("error", "model_error")
                yield _event(
                    "error",
                    {"code": "model_error", "message": f"The model request failed: {exc}"},
                )
            else:
                logger.exception("run %s on thread %s failed", run_id, thread_id)
                ended = ("error", "internal")
                yield _event(
                    "error",
                    {"code": "internal", "message": "The run failed because of an internal error."},
                )
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
            yield _event("interrupt", interrupt_to_pending(interrupts[0], runtime.catalog))
        else:
            ended = ("end", None)
            yield _event("end", {"status": "completed"})
    finally:
        renewals.cancel()
        # A disconnect cancels this generator; shield the release so it completes.
        with anyio.CancelScope(shield=True):
            if on_end is not None and ended is not None:
                try:
                    await on_end(*ended)
                except Exception:
                    logger.exception("run %s on thread %s: on_end failed", run_id, thread_id)
            await runtime.locks.release(thread_id, owner)
