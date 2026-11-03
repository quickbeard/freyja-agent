from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from freyja_agent.api.threads import router as threads_router
from freyja_agent.config import Settings, settings
from freyja_agent.runtime.runtime import Runtime

OpenRuntime = Callable[[Settings], AbstractAsyncContextManager[Runtime]]


def create_app(open_runtime: OpenRuntime = Runtime.open) -> FastAPI:
    """Build the FastAPI app.

    ``open_runtime`` is the one seam for tests: the lifespan enters it with the process
    settings and stores the yielded ``Runtime`` on ``app.state``. Production uses
    ``Runtime.open``; tests pass a context manager yielding an in-memory runtime, so the
    routes run the same code either way (constitution principle III).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
