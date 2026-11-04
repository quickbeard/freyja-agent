import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from freyja_agent.api.threads import router as threads_router
from freyja_agent.config import Settings, settings
from freyja_agent.runtime.runtime import Runtime

OpenRuntime = Callable[[Settings], AbstractAsyncContextManager[Runtime]]


def configure_logging() -> None:
    """Make this package's INFO records visible; uvicorn configures only its own loggers.

    The root logger gets a handler at WARNING if nothing configured one yet (``basicConfig``
    is a no-op otherwise), so third-party INFO noise stays out, and ``freyja_agent`` is raised
    to INFO so startup lines such as "Persistence ready" reach the service log (FR-008).
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(name)s: %(message)s")
    logging.getLogger("freyja_agent").setLevel(logging.INFO)


def create_app(open_runtime: OpenRuntime = Runtime.open) -> FastAPI:
    """Build the FastAPI app.

    ``open_runtime`` is the one seam for tests: the lifespan enters it with the process
    settings and stores the yielded ``Runtime`` on ``app.state``. Production uses
    ``Runtime.open``; tests pass a context manager yielding an in-memory runtime, so the
    routes run the same code either way (constitution principle III).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        async with open_runtime(settings) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(title="Freyja Orchestrator", lifespan=lifespan)

    app.include_router(threads_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("freyja_agent.api.app:app", host=settings.host, port=settings.port)
