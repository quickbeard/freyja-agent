# Implementation Plan: Thread Runs API

**Branch**: `001-thread-runs-api` | **Date**: 2026-10-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-thread-runs-api/spec.md`

## Summary

Build the minimal Freyja runtime: one agent factory (`build_agent`) that assembles a Deep Agents
orchestrator from the model named in `Settings`, a Postgres checkpointer and store opened for the
life of the FastAPI process, a filesystem backend confined to `workspace/`, and skills read from
`workspace/skills/`. Three endpoints expose it: start a run on a thread and stream it over SSE,
read a thread's full state, and resume a paused thread with approve, edit, or reject decisions.
The one tool that changes the environment, a placeholder `isolate_host` that only logs, is gated
by Deep Agents' human-in-the-loop interrupt; the pause is checkpointed, so a decision can arrive
after any delay or a restart. A small `freyja_run_locks` table with an expiring lease enforces one
active run per thread across every service instance that shares the database, and a per-run
recursion limit from settings bounds every run. A compose file provides the local Postgres with
credentials that match `.env.example`.

Design details are in [research.md](research.md); the data shapes in
[data-model.md](data-model.md); the HTTP and event contracts in
[contracts/openapi.yaml](contracts/openapi.yaml) and [contracts/sse-events.md](contracts/sse-events.md);
the manual validation walk-through in [quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 (`.python-version`), managed by uv with the committed `uv.lock`, src layout.

**Primary Dependencies**: deepagents 0.7.32 (on langgraph 1.2.18 and langchain 1.4.7),
langgraph-checkpoint-postgres 3.2.0, psycopg 3.3.6 with psycopg_pool 3.3.3, fastapi 0.162.0,
sse-starlette 3.6.0, pydantic-settings 2.16. All already in `pyproject.toml`; this feature adds no
runtime dependency.

**Storage**: PostgreSQL for the LangGraph checkpointer and store (tables created by their
`setup()`), plus one Freyja-owned table, `freyja_run_locks`. The agent's files live on disk under
`FREYJA_WORKSPACE_DIR` (default `workspace/`): `skills/` is committed, `cases/` is runtime output.

**Testing**: pytest 9 with pytest-asyncio in auto mode; FastAPI's `TestClient` consuming the SSE
stream incrementally; a scripted `BaseChatModel` subclass in `tests/fakes.py` so no model provider
is contacted; `InMemorySaver`, `InMemoryStore`, and an in-memory run-lock implementation injected
through the same factory the service uses. Postgres-backed integration tests run only when
`FREYJA_TEST_DATABASE_URI` is set.

**Target Platform**: Linux server process (uvicorn). Local development uses Docker Compose for Postgres.

**Project Type**: single web service, src layout (`src/freyja_agent/`).

**Performance Goals**: first SSE event within 1 s of accepting a run, before any model output
(SC-001); a state read immediately after a stream closes reflects that run (SC-005). Throughput is
not a goal; a handful of analysts and tens of concurrent threads.

**Constraints**: binds 127.0.0.1 unless configured; no authentication; one active run per thread
enforced in the database with an expiring lease; every run bounded by `FREYJA_MAX_STEPS`; no
blocking I/O in async handlers (Ruff `ASYNC`); no shell or exec tool reachable by the model; file
tools confined to the workspace root; the model identifier appears only in settings.

**Scale/Scope**: three endpoints plus the unchanged health check; one orchestrator; one gated
placeholder tool; one placeholder skill; no delegates, CLI, or multi-model support (spec scope).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Analyst approval for environment-changing actions | Yes, central | `isolate_host` is registered with `interrupt_on` allowing exactly approve, edit, reject. The tool is reachable only through the agent's tool node, which the HITL middleware wraps; no route or helper calls it directly. The interrupt is checkpointed in Postgres, so `POST .../resume` works after any delay or restart (research R2, R5, R8). The API validates decisions (count, allowed type, edited args against the tool's schema) and pins the tool name, closing the batched-mode gap where an edit could switch tools (R2). A new message on a paused thread is refused rather than silently discarding the proposal (R9). The general-purpose subagent inherits the gate, and a test proves a proposal raised inside it pauses the run (R1). | PASS | PASS |
| II. Protocol-based delegation | Not exercised | No delegate agents in scope. No other agent framework is imported. The Deep Agents general-purpose subagent stays enabled: it is an in-process context-isolation tool of the orchestrator itself, not a delegate; it inherits the approval gate and is bounded by the step limit (R1, R10). | PASS | PASS |
| III. One runtime, every environment | Yes | `build_agent` is the only place the orchestrator is constructed; the lifespan and the tests both call it. Persistence and the model are parameters of that factory, so tests inject `InMemorySaver`, `InMemoryStore`, an in-memory lock store, and a scripted model without a second code path: the routes, streaming loop, lock protocol, and factory are identical in tests and production (R11). The debug flag is LangGraph's `debug` stream mode on the same run, not a separate execution mode (R4). LangGraph Server and Studio are not used. | PASS | PASS |
| IV. Configuration through settings | Yes | Three new settings with safe local defaults: `FREYJA_WORKSPACE_DIR=workspace`, `FREYJA_MAX_STEPS=50`, `FREYJA_RUN_LOCK_TTL_SECONDS=60`, all documented in `.env.example`. The compose file's credentials equal the example `FREYJA_DATABASE_URI`; no credential is committed beyond that documented local-only value. Bind stays 127.0.0.1 (R6, R13). | PASS | PASS |
| V. Automated quality gates | Yes | Every FR has a test in `tests/` (map in research R11); the suite runs without a model or a database; Postgres paths have opt-in integration tests. All database access uses psycopg's async API through a pool; the lock heartbeat is an asyncio task; no blocking calls in handlers. SQL uses parameters only. No new Ruff suppressions are planned. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Not exercised | No severity signal exists yet. The step limit and the run lock are deterministic controls, but they are not escalation rules; the spec records this principle as deferred. | N/A | N/A |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; `langgraph-checkpoint-postgres`; `sse-starlette`; model from the provider-prefixed string in settings; Ruff only; `workspace/skills/` committed, `workspace/cases/` ignored (already in `.gitignore`). | PASS | PASS |

No deviations. The Complexity Tracking table is therefore empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-thread-runs-api/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── openapi.yaml     # HTTP contract for the three thread endpoints
│   └── sse-events.md    # Event-stream contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/freyja_agent/
├── __init__.py
├── config.py                  # Settings: + workspace_dir, max_steps, run_lock_ttl_seconds
├── agent/
│   ├── __init__.py
│   ├── factory.py             # build_agent(...): the one place the orchestrator is assembled
│   ├── prompts.py             # the orchestrator's system prompt
│   └── tools.py               # isolate_host (logs only) and its interrupt config
├── runtime/
│   ├── __init__.py
│   ├── persistence.py         # open_persistence(settings): pool, saver, store; setup(); close
│   ├── locks.py               # RunLocks protocol; PostgresRunLocks; InMemoryRunLocks
│   └── runtime.py             # Runtime: agent, locks, instance_id, settings; Runtime.open(settings)
└── api/
    ├── __init__.py
    ├── app.py                 # create_app(open_runtime=Runtime.open); lifespan; /healthz; main()
    ├── threads.py             # router: POST runs, GET state, POST resume
    ├── schemas.py             # request/response models (contracts/openapi.yaml)
    ├── serialize.py           # LangChain messages and interrupts -> JSON shapes
    └── streaming.py           # run_stream(): astream -> SSE events, lock heartbeat, terminal events

workspace/
└── skills/
    └── case-notes/
        └── SKILL.md           # placeholder skill proving skill loading; not a SOC capability

docker-compose.yml             # local Postgres; credentials equal .env.example

tests/
├── conftest.py                # env defaults; fixtures: scripted model, in-memory runtime, app factory
├── fakes.py                   # ScriptedChatModel (bind_tools, scripted tool calls), FakeClock
├── test_config.py             # existing, plus the three new settings
├── test_health.py             # existing
├── test_factory.py            # execute never reaches the model; task present; skills source; prompt; model wiring
├── test_thread_ids.py         # FR-001a on all three routes
├── test_runs.py               # start/update/message/end events; debug; empty message; continuity
├── test_gate.py               # interrupt event; approve/edit/reject with caplog; 409 and 422 cases
├── test_state.py              # full transcript; pending; 404; run_active
├── test_restart.py            # resume through a second app sharing the saver and lock store
├── test_locks.py              # acquire/renew/release/expiry; 409 run_active; release on disconnect and error
├── test_step_limit.py         # error event names the limit; thread usable afterwards
├── test_subagent.py           # gate holds for a proposal raised inside the subagent; limit propagates into it
└── integration/
    ├── conftest.py            # skip unless FREYJA_TEST_DATABASE_URI; create and drop tables
    ├── test_postgres_locks.py # the SQL lease against a real database
    └── test_postgres_runtime.py  # run -> interrupt -> new Runtime.open -> resume, through Postgres
```

Also edited: `.env.example` (three settings plus the commented test URI), `README.md` (compose,
endpoints, settings table, tests), `pyproject.toml` (pytest-asyncio auto mode; `deepagents` pinned
below 0.8).

**Structure Decision**: single project in the existing src layout. Three packages separate the
concerns the constitution cares about: `agent/` is the factory and the gated tool, `runtime/` is
persistence and the run lease, `api/` is the HTTP and SSE surface. No `cli.py` is added; a future
CLI calls `build_agent` and `Runtime.open` like the API does.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

None. The Constitution Check passed without deviations before research and after design.
