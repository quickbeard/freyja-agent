# Freyja

General-purpose agent orchestrator built on Deep Agents (LangGraph) and served with FastAPI. It
routes work to delegate agents and keeps a human in the loop for any action with effects outside
its workspace.

This file is the shared instruction set for every coding agent used on the repo (Claude Code,
Codex CLI). The project principles live in the Spec Kit constitution at
`.specify/memory/constitution.md` and are binding in every session. If that file is not already
in your context, read it before planning or changing code.

## Commands

Always go through uv. Never call python, pip, or pytest directly.

- `uv sync` installs runtime and dev dependencies into `.venv`.
- `uv run --env-file .env uvicorn freyja_agent.api.app:app --reload` runs the dev server on
  127.0.0.1:8000.
- `uv run --env-file .env freyja-api` runs it through the console script, binding from settings.
- `uv run ruff check .` and `uv run ruff format .` lint and format; use `--fix` and `--check`
  variants as usual.
- `uv run pytest` runs the suite. Tests run the production routes through FastAPI's test
  client with in-memory persistence and a scripted model; no server, database, or model
  provider is needed. Tool servers are played by the stub in `tests/mcp_stub.py`, so no MCP
  server needs to be installed and no network is used; only the transport tests launch it as
  a process or serve it on the loopback interface.
- `uv run python -m tests.mcp_stub --http 8765` serves the stub at
  `http://127.0.0.1:8765/mcp` for a manual run against an address server.
- `FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest
  tests/integration` runs the opt-in Postgres tests; without the variable they are skipped.
  Create the database once with `docker compose exec postgres createdb -U freyja freyja_test`.
- `uv run pre-commit run --all-files` runs the commit hook over the whole repo.
- `docker compose up -d postgres` starts the local Postgres the service and the integration
  tests use; its credentials equal `FREYJA_DATABASE_URI` in `.env.example`.

`.env` is git-ignored; copy it from `.env.example`. Settings do not read `.env` themselves, so
pass `--env-file .env` to `uv run` whenever a command needs it. The service needs Postgres at
startup and exits with an error naming the connection when it is unreachable.

## Layout

- `src/freyja_agent/config.py`: the `Settings` class (pydantic-settings, `FREYJA_*` prefix) and
  the shared `settings` instance. `McpServerConfig` is one entry of `FREYJA_MCP_SERVERS`;
  `FREYJA_MCP_CALL_TIMEOUT_SECONDS` and `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` are the two time
  limits for tool servers.
- `src/freyja_agent/agent/`: `factory.py` is the one place the orchestrator is assembled
  (`build_agent`; the service, the tests, and any future CLI call it). `build_agent` takes the
  tool server tools as `delegate_tools` and returns an `Orchestrator`: the compiled graph as
  `.agent` and the `Catalog` of exactly the tools it was given as `.catalog`. `catalog.py`
  holds the three classifications, the fixed table of Freyja's own tools (`OWN_TOOLS`), the
  catalog, and the interrupt config generated from it; a tool with no classification raises
  `UnclassifiedToolError` and stops startup. `prompts.py` is the system prompt, `tools.py` the
  gated placeholder tool `send_notification`.
- `src/freyja_agent/delegates/`: what the orchestrator reaches over a protocol.
  `mcp_servers.py` connects to the configured MCP servers (`open_tool_servers`, `ToolServer`,
  `default_connect`), applies the call time limit, records each server's status, and redacts
  `pass_env` values from everything a server link emits. `mcp_tools.py` turns each listed tool
  into a `DelegateTool`, classifies it from the `read_only` list (`classify`), and turns a
  result into text.
- `src/freyja_agent/runtime/`: `persistence.py` opens the shared Postgres pool, checkpointer,
  and store; `locks.py` is the per-thread run lease (`PostgresRunLocks`, `InMemoryRunLocks`);
  `runtime.py` is the `Runtime` dataclass with the catalog and the tool servers.
  `Runtime.assemble` opens the tool servers and builds the agent; `Runtime.open` adds Postgres
  and is what the FastAPI lifespan enters. Both take `connect`, the one seam the tests swap to
  reach stub servers.
- `src/freyja_agent/api/`: `app.py` is `create_app(open_runtime)`, `/healthz`, and the
  `freyja-api` entry point; `threads.py` the three thread routes (runs, state, resume);
  `tools.py` the catalog route `GET /tools`, which reads memory only; `streaming.py` the SSE
  run loop with the lease heartbeat; `decisions.py` resume validation, including edits checked
  against a server tool's JSON Schema; `schemas.py` and `serialize.py` the request, response,
  and event shapes.
- `workspace/`: the orchestrator's file root (`FREYJA_WORKSPACE_DIR`). `skills/` is committed
  source; `cases/` is written at runtime and git-ignored.
- `docker-compose.yml`: local Postgres for development and the integration tests.
- `tests/`: pytest suite. `conftest.py` supplies the required environment and the in-memory
  runtime fixtures, so no `.env` is needed; `make_mcp_app` assembles an app the production way
  with stub tool servers. `fakes.py` holds the scripted chat model; `mcp_stub.py` the stub MCP
  server, which records every call it receives; `integration/` holds the Postgres tests,
  skipped unless `FREYJA_TEST_DATABASE_URI` is set.
- `.specify/`: Spec Kit templates, scripts, installed extensions, and the constitution. Feature
  artifacts go under `specs/<NNN-slug>/` as `spec.md`, `plan.md`, and `tasks.md`.
- `.claude/skills/speckit-*/`: the Spec Kit skills for Claude Code. Codex CLI reads its own copy
  from `.agents/skills/speckit-*/`, installed with `specify integration install codex`. Both
  directories are generated by Spec Kit; edit neither by hand.
- `AGENTS.md`: this file. `CLAUDE.md` only imports it plus the constitution for Claude Code.

## Conventions

- Configuration goes through `Settings`. Never hardcode hosts, ports, connection strings, or
  model ids; add a field with a safe local default, or no default when the value carries a
  credential, and document it in `.env.example`.
- New behavior ships with a test in `tests/`.
- A Ruff suppression carries a reason in its comment and is removed once the cause is fixed.
- Commit messages use the `type: summary` form: `feat:`, `chore:`, `docs:`.
- The README documents only what exists in the repo, never planned work.

## Spec Kit

Features follow specify, plan, tasks, implement, then converge, with clarify, checklist, and
analyze as optional gates. The skills are named `speckit-<step>`: Claude Code invokes them as
`/speckit-<step>`, Codex CLI as `$speckit-<step>`. Do not start a Spec Kit skill unless the user
asks for it; a human reviews each step's output before the next one runs. Every plan includes a
Constitution Check that names the principles it touches and justifies any deviation.

The Spec Kit git extension is installed. Its `before_specify` hook creates the numbered feature
branch off `main`, so `speckit-specify` never writes a spec on `main`. Its auto-commit hooks are
optional and disabled in `.specify/extensions/git/git-config.yml`; commits stay manual and keep
the `type: summary` form. The CLI registers an extension's skills only for the active
integration, so after `specify extension add <id>` run `specify integration use codex` and then
`specify integration use claude` to give Codex CLI its copies under `.agents/skills/`.
