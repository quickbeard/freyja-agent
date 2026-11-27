"""The outside listener: Freyja's A2A routes for agents on other hosts (feature 009, R4).

A second, small application with the card and the operations and nothing else, served on an
address and a port of its own by a ``uvicorn.Server`` that the service's lifespan starts and
stops, in the same process and event loop, however the service itself is started
(principle III). Every other route of the service, the health check included, answers there
as a path it does not have, because this application was never given one (FR-003).

Every operation there needs a named caller's token; the card does not (FR-005, FR-006). A
page of another origin is refused there as at the service's own address. The host names the
service answers for are not checked there (spec, Assumptions): callers reach it under the
address the deployment configured.

The server keeps the process's signal handlers to the main server, and writes no access log
of its own without touching the main server's: uvicorn's logger for it is shared, so the
choice is made by the protocol it serves connections with. An open stream is closed when the
service stops, within a bounded time. The lifespan binds the socket itself,
so that a port that cannot be had stops startup with an error that names the setting, where
uvicorn would exit the process from inside a task.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import socket
from collections.abc import Generator
from typing import Any

import uvicorn
from fastapi import FastAPI
from uvicorn.protocols.http.h11_impl import H11Protocol

from freyja_agent.api.a2a_server import routes
from freyja_agent.api.received import ReceivedTasks
from freyja_agent.api.telemetry import RequestTelemetry
from freyja_agent.runtime.callers import Callers
from freyja_agent.runtime.telemetry import OUTSIDE_LISTENER, Telemetry

logger = logging.getLogger(__name__)

# How long a connection still open, such as a caller's stream, may hold the stop up.
GRACE_SECONDS = 5
# How long the server may take to start listening on the bound socket.
START_SECONDS = 10


class ListenerError(RuntimeError):
    """The outside listener could not be started on the address the settings give."""


def application(
    received: ReceivedTasks, callers: Callers, url: str, telemetry: Telemetry | None = None
) -> FastAPI:
    """The outside listener's application: the card, for ``url``, and the operations.

    ``telemetry`` records its requests as the outside listener's (feature 011); it has no
    runtime of its own to take one from.
    """
    app = FastAPI(
        title="Freyja A2A", docs_url=None, redoc_url=None, openapi_url=None, lifespan=None
    )
    app.router.routes.extend(routes(received, callers, outside=True, url=url))
    if telemetry is not None:
        app.add_middleware(RequestTelemetry, listener=OUTSIDE_LISTENER, telemetry=telemetry)
    return app


class _Server(uvicorn.Server):
    """A server that leaves the process's signals to the main server."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


class _Quiet(H11Protocol):
    """HTTP for the outside listener, writing no access log line. uvicorn turns its access
    log off by emptying the logger it shares with the main server, which would silence that
    one too; this leaves the logger as it is."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.access_log = False


def _bound(host: str, port: int) -> socket.socket:
    """A socket bound to ``host`` and ``port``; raises ``ListenerError`` naming the setting."""
    try:
        family = socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
    except ValueError:
        family = socket.AF_INET
    try:
        sock = socket.create_server((host, port), family=family)
    except OSError as exc:
        raise ListenerError(
            "The outside listener could not listen at FREYJA_A2A_SERVER_HOST and "
            f"FREYJA_A2A_SERVER_PORT: {exc.strerror or type(exc).__name__}"
        ) from None
    sock.setblocking(False)
    return sock


class Listener:
    """The outside listener's server, started and stopped by the service's lifespan."""

    def __init__(self, app: FastAPI, host: str, port: int) -> None:
        self.host = host
        self.port = port
        config = uvicorn.Config(
            app,
            http=_Quiet,
            lifespan="off",
            log_config=None,
            timeout_graceful_shutdown=GRACE_SECONDS,
        )
        self._server = _Server(config)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Bind the socket and serve on it; returns once the server listens."""
        # A host name is looked up when it is bound: away from the event loop.
        sock = await asyncio.to_thread(_bound, self.host, self.port)
        self._task = asyncio.create_task(
            self._server.serve(sockets=[sock]), name="freyja-a2a-outside"
        )
        self._task.add_done_callback(self._ended)
        try:
            async with asyncio.timeout(START_SECONDS):
                while not self._server.started:
                    if self._task.done():
                        break
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pass
        if not self._server.started:
            await self.stop()
            sock.close()
            raise ListenerError(
                "The outside listener did not start at FREYJA_A2A_SERVER_HOST and "
                "FREYJA_A2A_SERVER_PORT"
            )

    def _ended(self, task: asyncio.Task[None]) -> None:
        """Say so when the server stopped without being asked to."""
        if self._server.should_exit or task.cancelled():
            return
        error = task.exception()
        logger.error(
            "The outside listener stopped serving on its own (%s); callers on other hosts "
            "cannot reach Freyja until the service is started again",
            type(error).__name__ if error is not None else "no error",
        )

    async def stop(self) -> None:
        """Stop serving; an open stream is given ``GRACE_SECONDS`` before it is closed."""
        task, self._task = self._task, None
        if task is None:
            return
        self._server.should_exit = True
        try:
            async with asyncio.timeout(GRACE_SECONDS + 5):
                await asyncio.gather(task, return_exceptions=True)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
