"""An httpx transport that runs an ASGI app in the test process and streams its responses.

httpx's own ``ASGITransport`` collects the whole response body before returning it, so a
streamed A2A reply arrives in one piece after the agent has finished, and a task that is still
working at the call time limit would look unacknowledged (003 research R16). This transport hands
each body chunk to the client as the app sends it.

It also records every request's method, host, port, and path, so a test can show where Freyja
sent its requests (003 FR-002), and it can play an agent that is not running (``down``) or one
that never answers (``hang``).
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class SeenRequest:
    method: str
    host: str
    port: int | None
    path: str


class _StreamedBody(httpx.AsyncByteStream):
    def __init__(self, chunks: "asyncio.Queue[bytes | None]", app_task: asyncio.Task[None]) -> None:
        self._chunks = chunks
        self._app_task = app_task

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._chunks.get()
            if chunk is None:
                return
            yield chunk

    async def aclose(self) -> None:
        if not self._app_task.done():
            self._app_task.cancel()


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """Serve ``app`` in-process; each response chunk reaches the client as soon as it is sent."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.requests: list[SeenRequest] = []
        # Set to make the next requests fail as if nothing listened, or never answer.
        self.down = False
        self.hang = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            SeenRequest(request.method, request.url.host, request.url.port, request.url.path)
        )
        if self.down:
            raise httpx.ConnectError("All connection attempts failed", request=request)
        if self.hang:
            await asyncio.Event().wait()
        body = b"".join([part async for part in request.stream])
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "headers": [(key.lower(), value) for key, value in request.headers.raw],
            "server": (request.url.host, request.url.port or 80),
            "client": ("127.0.0.1", 50000),
        }
        started: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        request_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            # The client never disconnects on its own; wait until the app is cancelled.
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}  # pragma: no cover

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                started.set_result(message)
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    await chunks.put(message["body"])
                if not message.get("more_body", False):
                    await chunks.put(None)

        async def run() -> None:
            try:
                await self.app(scope, receive, send)
            except BaseException as exc:
                if not started.done():
                    started.set_exception(exc)
                raise
            finally:
                await chunks.put(None)

        app_task = asyncio.create_task(run())
        start = await started
        return httpx.Response(
            start["status"],
            headers=start.get("headers", []),
            stream=_StreamedBody(chunks, app_task),
            request=request,
        )
