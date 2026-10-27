from fastapi import FastAPI

from freyja_agent.config import settings

app = FastAPI(title="Freyja SOC Orchestrator")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    uvicorn.run("freyja_agent.api.app:app", host=settings.host, port=settings.port)
