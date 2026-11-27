import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from starlette.routing import BaseRoute

from freyja_agent.api import a2a_listener, a2a_server
from freyja_agent.api.agents import router as agents_router
from freyja_agent.api.hosts import AllowedHosts
from freyja_agent.api.llm import router as llm_router
from freyja_agent.api.received import ReceivedTasks
from freyja_agent.api.telemetry import RequestTelemetry
from freyja_agent.api.thread_index import router as thread_index_router
from freyja_agent.api.threads import router as threads_router
from freyja_agent.api.tools import router as tools_router
from freyja_agent.config import Settings, settings
from freyja_agent.delegates.addresses import install_log_scrubber
from freyja_agent.runtime.callers import Callers
from freyja_agent.runtime.runtime import Runtime
from freyja_agent.runtime.thread_index import IndexedRunLocks

OpenRuntime = Callable[[Settings], AbstractAsyncContextManager[Runtime]]

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Make this package's INFO records visible; uvicorn configures only its own loggers.

    The root logger gets a handler at WARNING if nothing configured one yet (``basicConfig``
    is a no-op otherwise), so third-party INFO noise stays out, and ``freyja_agent`` is raised
    to INFO so startup lines such as "Persistence ready" reach the service log (FR-008).
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(name)s: %(message)s")
    logging.getLogger("freyja_agent").setLevel(logging.INFO)


def _receive_tasks(runtime: Runtime) -> ReceivedTasks:
    """The A2A service of this runtime, told of the end of every run on any thread (R9),
    with its sweep started once the runtime is open."""
    received = ReceivedTasks(runtime)
    if isinstance(runtime.locks, IndexedRunLocks):
        runtime.locks.on_release = received.on_release
    if runtime.unattended is not None:
        # A follower joins the run a person's decision starts on its thread (T058).
        runtime.unattended.on_start = received.on_run_started
        # A console run on a caller's thread stops once its caller canceled (R10).
        runtime.unattended.canceled = received.canceled
    received.start_sweeping()
    return received


async def _stop_receiving(runtime: Runtime, received: ReceivedTasks) -> None:
    """Stop looking at tasks when runs end, stop the sweep, and wait for the looks under
    way, before the runtime is closed. A task whose run the service stops on its way down
    is read from its thread when it is next asked for."""
    if isinstance(runtime.locks, IndexedRunLocks):
        runtime.locks.on_release = None
    if runtime.unattended is not None:
        runtime.unattended.on_start = None
        runtime.unattended.canceled = None
    await received.close()


def _listener_lines(runtime: Runtime, callers: Callers) -> None:
    """The startup lines of feature 009: that it is on, how many callers are named, and
    whether the outside listener is, and where; and a warning for an address callers reach
    without encryption (FR-028, FR-029). No token."""
    settings = runtime.settings
    where = (
        f"on at {settings.a2a_server_host}:{settings.a2a_server_port}"
        if settings.a2a_server_listener
        else "off"
    )
    logger.info(
        "Receiving tasks from other agents over A2A is on: the card and the operations are "
        "served at this address; %d callers are named; the outside listener is %s",
        len(callers.names),
        where,
    )
    url = settings.a2a_server_url or ""
    if settings.a2a_server_listener and url.lower().startswith("http:"):
        logger.warning(
            "FREYJA_A2A_SERVER_URL is an http address: the tokens callers send to the outside "
            "listener can be read on the network path. Put what encrypts in front of it."
        )


def create_app(open_runtime: OpenRuntime = Runtime.open, *, listen: bool = True) -> FastAPI:
    """Build the FastAPI app.

    ``open_runtime`` is the one seam for tests: the lifespan enters it with the process
    settings and stores the yielded ``Runtime`` on ``app.state``. Production uses
    ``Runtime.open``; tests pass a context manager yielding an in-memory runtime, so the
    routes run the same code either way (constitution principle III).

    Before any runtime opens, every log record of the process is set to be cleaned of agent
    addresses as it is made: the HTTP and agent libraries log the addresses they request
    (feature 005, FR-028, research R12). Feature 009 adds callers' tokens to what is cleaned.

    ``listen`` says whether the outside listener of feature 009 binds its address when it is
    set; the suite drives its application in-process instead (``app.state.a2a_outside``).
    """
    install_log_scrubber()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        async with open_runtime(settings) as runtime:
            app.state.runtime = runtime
            # Feature 009: the runtime's own settings say whether the feature is on, since
            # the application is made before its runtime (research R3).
            settings_now = runtime.settings
            received = _receive_tasks(runtime) if settings_now.a2a_server else None
            callers = Callers(settings_now.a2a_server_callers) if received is not None else None
            app.state.received = received
            app.state.callers = callers
            app.state.a2a_outside = None
            # The card and the operations, served while the feature is on and only while this
            # lifespan runs: a lifespan entered again never serves a closed runtime's (R3).
            added: list[BaseRoute] = []
            listener: a2a_listener.Listener | None = None
            try:
                if received is not None and callers is not None:
                    added = list(a2a_server.routes(received, callers))
                    app.router.routes.extend(added)
                    if settings_now.a2a_server_listener:
                        outside = a2a_listener.application(
                            received,
                            callers,
                            settings_now.a2a_server_url or "",
                            runtime.telemetry,
                        )
                        app.state.a2a_outside = outside
                        if listen:
                            listener = a2a_listener.Listener(
                                outside,
                                settings_now.a2a_server_host or "",
                                settings_now.a2a_server_port or 0,
                            )
                            await listener.start()
                    _listener_lines(runtime, callers)
                yield
            finally:
                # The outside listener stops before the runtime is closed (R4).
                if listener is not None:
                    await listener.stop()
                for route in added:
                    app.router.routes.remove(route)
                if received is not None:
                    await _stop_receiving(runtime, received)
                if callers is not None:
                    callers.forget()

    app = FastAPI(title="Freyja Orchestrator", lifespan=lifespan)
    # Before any route: a request made for a host name the deployment does not list is
    # refused, a request that only reads included (api/hosts.py).
    app.add_middleware(AllowedHosts, names=settings.allowed_hosts)
    # Added last, so it is outside every other middleware and sees a request one of them
    # refuses (feature 011).
    app.add_middleware(RequestTelemetry)

    app.include_router(threads_router)
    # Feature 007: the list of threads, beside the routes that run them.
    app.include_router(thread_index_router)
    app.include_router(tools_router)
    app.include_router(llm_router)
    app.include_router(agents_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("freyja_agent.api.app:app", host=settings.host, port=settings.port)
