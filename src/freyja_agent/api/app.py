from fastapi import FastAPI

app = FastAPI(title="Freyja SOC Orchestrator")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    uvicorn.run("freyja_agent.api.app:app", host="0.0.0.0", port=8000)
