"""Spec edge case "service stops while a run is in flight", FR-006: a clean stop leaves no lease behind.

Under uvicorn a clean stop waits for in-flight responses before the lifespan closes; the test
client closes the lifespan at once and cancels the stream, which is the harsher path. The
lease must be released either way, through ``run_stream``'s shielded ``finally``.
"""

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.runtime.runtime import Runtime
from tests.fakes import ScriptedChatModel


def test_a_clean_stop_during_an_active_run_releases_the_lease(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    reached_model = threading.Event()
    let_model_answer = threading.Event()

    class BlockingModel(ScriptedChatModel):
        def next_message(self) -> AIMessage:
            reached_model.set()
            let_model_answer.wait(timeout=5)
            return super().next_message()

    runtime = make_runtime(BlockingModel(final_text="done"))
    outcome: dict[str, Any] = {}

    def stream() -> None:
        with client.stream("POST", "/threads/stop-1/runs", json={"message": "slow"}) as response:
            outcome["status"] = response.status_code
            outcome["events"] = sse_events(response.iter_lines())

    with TestClient(make_app(runtime)) as client:
        worker = threading.Thread(target=stream)
        worker.start()
        assert reached_model.wait(timeout=5)
        assert client.portal.call(runtime.locks.is_active, "stop-1") is True
        # The stop is requested while the model call is in flight; the model answers shortly after,
        # as a real provider would, and the service must not leave the lease behind.
        threading.Timer(0.3, let_model_answer.set).start()
    # Leaving the block runs the lifespan shutdown, the production stop path.
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert asyncio.run(runtime.locks.is_active("stop-1")) is False
    assert outcome["status"] == 200
    assert outcome["events"][0][0] == "start"
    # The stream was cut by the stop (no terminal event) and the lease did not leak with it.
    assert asyncio.run(runtime.locks.is_active("stop-1")) is False
