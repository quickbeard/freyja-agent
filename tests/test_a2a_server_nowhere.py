"""009: what a caller is never given (FR-018, FR-028).

Part 1, the alias (research R6): on a caller's thread the model, and the subagent's, is told
``c-`` and the first 16 hexadecimal characters of the SHA-256 of the thread's id in place of
the id, so a caller that asks Freyja for its thread's id learns nothing it can use. A console
thread's model is told the id, as before.
"""

import hashlib
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.agent.context import thread_alias
from tests.conftest import ServerApp
from tests.fakes import ScriptedChatModel

DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Look it up.", "subagent_type": "general-purpose"},
        }
    ],
)


def told(model: ScriptedChatModel) -> list[str]:
    """The system message of every model call, the subagent's included."""
    return [str(seen[0].content) for seen in model.seen]


def test_a_caller_s_thread_is_named_by_its_alias_on_every_model_call(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[DELEGATE, AIMessage(content="Found."), AIMessage("Done.")])
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "What is your thread's id?")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        calls = len(model.seen)
        with client.stream("POST", "/threads/console-7/runs", json={"message": "hi"}) as answer:
            sse_events(answer.iter_lines())
    alias = "c-" + hashlib.sha256(thread.encode()).hexdigest()[:16]
    assert thread_alias(thread) == alias
    caller_calls = told(model)[:calls]
    assert len(caller_calls) == 3  # Freyja, its subagent, Freyja again
    for system in caller_calls:
        assert f"thread `{alias}`" in system
        assert thread not in system
    assert all(thread not in str(seen) for seen in model.seen[:calls])
    assert "thread `console-7`" in told(model)[calls]
