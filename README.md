# Freyja

SOC orchestrator built on [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) and served with FastAPI. The orchestrator routes security operations work to the right delegate agent and keeps analysts in the loop for any action that changes the environment.

## Setup

```bash
uv sync                     # creates .venv and installs runtime + dev dependencies
cp .env.example .env        # fill in values as needed; .env is git-ignored
uv run pre-commit install   # one-time per clone: runs Ruff on every commit
```

## Run the dev server

With auto-reload, listening on `127.0.0.1:8000`:

```bash
uv run --env-file .env uvicorn freyja_agent.api.app:app --reload
```

Or through the console script, which reads the bind address from settings:

```bash
uv run --env-file .env freyja-api
```

Check it is up:

```bash
curl localhost:8000/healthz
# {"status":"ok"}
```

Interactive API docs are at <http://localhost:8000/docs>.

## Lint and format

Ruff handles both linting and formatting. Configuration lives in `pyproject.toml` under `[tool.ruff]`.

```bash
uv run ruff check .            # lint
uv run ruff check . --fix      # lint and apply safe autofixes
uv run ruff format .           # format in place
uv run ruff format --check .   # report unformatted files without changing them (CI mode)
```

The pre-commit hook runs `ruff check --fix` and `ruff format` on staged files. If the hook changes a file, the commit is blocked: re-stage the file and commit again. To run the hook over the whole repo:

```bash
uv run pre-commit run --all-files
```

## Unit tests

```bash
uv run pytest                  # full suite
uv run pytest -q               # quiet output
uv run pytest tests/test_health.py::test_healthz   # a single test
```

Tests live in `tests/`. The health test uses FastAPI's test client without starting a server.

## Configuration

Settings are read from `FREYJA_*` environment variables by `src/freyja_agent/config.py`. Load them with `uv run --env-file .env <command>`.

| Variable | Default | Purpose |
|---|---|---|
| `FREYJA_HOST` | `127.0.0.1` | Bind address for `freyja-api`. Containers set `0.0.0.0`. |
| `FREYJA_PORT` | `8000` | Bind port for `freyja-api`. |
| `FREYJA_DATABASE_URI` | `postgresql://freyja:freyja@localhost:5432/freyja` | Postgres connection for the checkpointer and store (wiring in progress). |
| `FREYJA_MODEL` | `anthropic:claude-opus-5-5` | Model for the orchestrator (wiring in progress), as a LangChain `provider:model` string. |
| `ANTHROPIC_API_KEY` | | Required once the agent is wired. Read by the model client, not by settings. The variable follows the `FREYJA_MODEL` prefix: `OPENROUTER_API_KEY` for `openrouter:` models, which also need `uv add langchain-openrouter`. |

## Project layout

```
src/freyja_agent/
  config.py        # Settings class and the shared `settings` instance
  api/app.py       # FastAPI app, /healthz, and the freyja-api entry point
tests/             # pytest suite
.env.example       # documented environment variables
.pre-commit-config.yaml
pyproject.toml     # dependencies, scripts, Ruff and pytest config
```
