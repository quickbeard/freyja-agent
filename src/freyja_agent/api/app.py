import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from starlette.routing import BaseRoute

from freyja_agent.api import a2a_server
from freyja_agent.api.agents import router as agents_router
from freyja_agent.api.hosts import AllowedHosts
from freyja_agent.api.llm import router as llm_router
from freyja_agent.api.received import ReceivedTasks
from freyja_agent.api.thread_index import router as thread_index_router
from freyja_agent.api.threads import router as threads_router
from freyja_agent.api.tools import router as tools_router
from freyja_agent.config import Settings, settings
from freyja_agent.delegates.addresses import install_log_scrubber
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
    """The A2A service of this runtime, told of the end of every run on any thread (R9)."""
    received = ReceivedTasks(runtime)
    if isinstance(runtime.locks, IndexedRunLocks):
        runtime.locks.on_release = received.on_release
    return received


async def _stop_receiving(runtime: Runtime, received: ReceivedTasks) -> None:
    """Stop looking at tasks when runs end, and wait for the looks under way. A task whose
    run the service stops on its way down is read from its thread when it is next asked
    for."""
    if isinstance(runtime.locks, IndexedRunLocks):
        runtime.locks.on_release = None
    await received.close()


def create_app(open_runtime: OpenRuntime = Runtime.open) -> FastAPI:
    """Build the FastAPI app.

    ``open_runtime`` is the one seam for tests: the lifespan enters it with the process
    settings and stores the yielded ``Runtime`` on ``app.state``. Production uses
    ``Runtime.open``; tests pass a context manager yielding an in-memory runtime, so the
    routes run the same code either way (constitution principle III).

    Before any runtime opens, every log record of the process is set to be cleaned of agent
    addresses as it is made: the HTTP and agent libraries log the addresses they request
    (feature 005, FR-028, research R12).
    """
    install_log_scrubber()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        async with open_runtime(settings) as runtime:
            app.state.runtime = runtime
            # Feature 009: the runtime's own settings say whether the feature is on, since
            # the application is made before its runtime (research R3).
            received = _receive_tasks(runtime) if runtime.settings.a2a_server else None
            app.state.received = received
            # The card and the operations, served while the feature is on and only while this
            # lifespan runs: a lifespan entered again never serves a closed runtime's (R3).
            added: list[BaseRoute] = []
            if received is not None:
                added = list(a2a_server.routes(received))
                app.router.routes.extend(added)
                logger.info(
                    "Receiving tasks from other agents over A2A is on: the card and the "
                    "operations are served at this address; %d callers are named; the "
                    "outside listener is off",
                    0,
                )
            try:
                yield
            finally:
                for route in added:
                    app.router.routes.remove(route)
                if received is not None:
                    await _stop_receiving(runtime, received)

    app = FastAPI(title="Freyja Orchestrator", lifespan=lifespan)
    # Before any route: a request made for a host name the deployment does not list is
    # refused, a request that only reads included (api/hosts.py).
    app.add_middleware(AllowedHosts, names=settings.allowed_hosts)

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
