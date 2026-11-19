"""Testing a profile: a short conversation with its endpoint (feature 004, research R7).

A test sends two requests under one time limit. The first is one user message. The second
carries that message, an assistant turn that calls a tool, the tool's result, and a second
user message, with the tool's definition, which is what every request of a run looks like
after its first step. An endpoint can answer the first and refuse the second: on 2026-11-16 a
server did, over the Responses API, rejecting earlier turns as input (001 research R3). The
conversation is written here, not produced by the model, so that failure shows every time.

A test changes nothing: no profile, no thread, not the active choice (FR-019). Its caller
decides whether the result is kept.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from freyja_agent.llm.secrets import redact
from freyja_agent.llm.store import REASON_LIMIT

Step = Literal["first_message", "conversation"]

FIRST_MESSAGE = "This is a connection test. Reply with the single word: ready."
SECOND_MESSAGE = "Thank you. Reply with the single word: done."
TOOL_NAME = "lookup_status"
# A tool definition in the shape every provider's client accepts.
TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Look up the status of this connection test.",
        "parameters": {
            "type": "object",
            "properties": {"test": {"type": "string", "description": "The name of the test."}},
            "required": ["test"],
        },
    },
}
# The most tests one service instance runs at once (FR-019b). A constant, not a setting.
TESTS_AT_ONCE = 4


@dataclass(frozen=True)
class CheckResult:
    """How a test ended (data-model.md "Profile test")."""

    ok: bool
    # The step that failed; None when the test passed.
    step: Step | None = None
    # Why it failed, with any key removed and at most 500 characters; None when it passed.
    reason: str | None = None


@dataclass(frozen=True)
class LastCheck:
    """The last test of a listed profile, as it is kept (FR-019a)."""

    ok: bool
    at: datetime
    step: Step | None = None
    reason: str | None = None


def conversation() -> list[BaseMessage]:
    """The second step: an exchange with a tool call and its result, then a new message."""
    call = {"id": "call_connection_test", "name": TOOL_NAME, "args": {"test": "connection"}}
    return [
        HumanMessage(FIRST_MESSAGE),
        AIMessage(content="", tool_calls=[call]),
        ToolMessage(content="The connection test is in progress.", tool_call_id=call["id"]),
        HumanMessage(SECOND_MESSAGE),
    ]


def _reason(text: str, secrets: Sequence[str]) -> str:
    return redact(text, secrets)[:REASON_LIMIT]


async def check_model(
    model: BaseChatModel, *, limit: float, secrets: Sequence[str] = ()
) -> CheckResult:
    """Hold the two-step conversation with ``model`` and say how it went.

    ``limit`` is the time both steps have together, in seconds. The model is expected to try
    nothing again by itself (``ModelSpec.max_retries``), or the limit would only be reached.
    ``secrets`` are removed from the reason: an endpoint may repeat a key in its error (FR-021).
    """
    step: Step = "first_message"
    try:
        async with asyncio.timeout(limit):
            await model.ainvoke([HumanMessage(FIRST_MESSAGE)])
            step = "conversation"
            await model.bind_tools([TOOL]).ainvoke(conversation())
    except TimeoutError:
        return CheckResult(
            ok=False, step=step, reason=f"The endpoint did not answer within {limit:g} seconds."
        )
    except Exception as exc:
        # Whatever the client raises is the endpoint's reason, not a failure of the service.
        return CheckResult(
            ok=False, step=step, reason=_reason(str(exc) or type(exc).__name__, secrets)
        )
    return CheckResult(ok=True)


# What a test is told when its profile is being tested, and when no slot is free.
BUSY = "This profile is already being tested. Wait for that test to end."
FULL = "{at_once} tests are already running on this instance. Try again when one of them ends."


class CheckSlots:
    """The tests in progress on this instance, counted in memory (FR-019b).

    One per listed profile and ``at_once`` in all. A test holds no database row, so the
    limits are this instance's own.

    The two refusals are worded for LLM profiles unless other words are given, so that
    something else counted the same way can refuse in its own (feature 005, research R15).
    ``full`` may hold ``{at_once}``, which is replaced by the limit.
    """

    def __init__(self, *, busy: str = BUSY, full: str = FULL, at_once: int = TESTS_AT_ONCE) -> None:
        self._busy = busy
        self._full = full.replace("{at_once}", str(at_once))
        self._at_once = at_once
        self._profiles: set[str] = set()
        self._running = 0

    def refusal(self, profile_id: str | None) -> str | None:
        """Why a test cannot start now, or None. ``profile_id`` is None for unsaved values."""
        if profile_id is not None and profile_id in self._profiles:
            return self._busy
        if self._running >= self._at_once:
            return self._full
        return None

    def take(self, profile_id: str | None) -> None:
        self._running += 1
        if profile_id is not None:
            self._profiles.add(profile_id)

    def release(self, profile_id: str | None) -> None:
        self._running -= 1
        if profile_id is not None:
            self._profiles.discard(profile_id)
