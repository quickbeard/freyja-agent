"""Requests to Freyja's A2A routes, as the suite makes them (feature 009).

The stand-in caller (``tests/a2a_caller.py``) at the service's own address, run in the app's
own event loop, and the protocol's requests as a client that is not the SDK sends them.
"""

import time
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.a2a_caller import StandInCaller, as_json
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import ServerApp

ADDRESS = "http://testserver"
VERSION = {"A2A-Version": "1.0"}


def call(client: TestClient, made: ServerApp, work: Callable[[StandInCaller], Any]) -> Any:
    """Run ``work`` with the stand-in caller, in the app's event loop."""

    async def run() -> Any:
        transport = StreamingASGITransport(made.app)
        async with StandInCaller(ADDRESS, transport=transport) as caller:
            return await work(caller)

    return client.portal.call(run)


def streamed(client: TestClient, made: ServerApp, text: str, **kwargs: Any) -> list[dict]:
    """Every event of a send with a stream, as the protocol's JSON names have them."""

    async def work(caller: StandInCaller) -> list[dict]:
        return [as_json(event) async for event in caller.stream(text, **kwargs)]

    return call(client, made, work)


def rpc(
    client: TestClient, method: str, params: dict[str, Any], headers: dict[str, str] | None = None
) -> Any:
    body = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}
    return client.post("/a2a", json=body, headers=VERSION if headers is None else headers)


def sending(text: str = "hello", **message: Any) -> dict[str, Any]:
    return {
        "message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": text}], **message}
    }


def text_of(event: dict[str, Any]) -> str | None:
    """The sentence of a status in an event, or None."""
    status = (event.get("statusUpdate") or event.get("task") or event).get("status") or {}
    parts = (status.get("message") or {}).get("parts") or []
    return parts[0]["text"] if parts else None


def kinds(events: list[dict]) -> list[str]:
    return [next(iter(event)) for event in events]


def ended(client: TestClient, made: ServerApp, task_id: str, seconds: float = 5) -> Any:
    """The task once its end is written, waiting for the run and the look after it."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        client.portal.call(made.service().close)
        found = made.task(client, task_id)
        if found is not None and found.ended:
            return found
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not end")


def at_rest(client: TestClient, made: ServerApp, *task_ids: str, seconds: float = 5) -> None:
    """Wait until the run of each task has given its thread's lease back."""
    deadline = time.monotonic() + seconds
    threads = [made.task(client, task_id).thread_id for task_id in task_ids]
    while time.monotonic() < deadline:
        if not any(client.portal.call(made.runtime.locks.is_active, t) for t in threads):
            client.portal.call(made.service().close)
            return
        time.sleep(0.02)
    raise AssertionError(f"the runs of {task_ids} did not end")
