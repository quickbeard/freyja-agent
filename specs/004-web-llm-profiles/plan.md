# Implementation Plan: Web Console Shell and LLM Profiles

**Branch**: `004-web-llm-profiles` | **Date**: 2026-11-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-web-llm-profiles/spec.md`

## Summary

Let an operator choose the model Freyja runs on from the web app, without editing a file or
restarting the service. The web app gets a sidebar with two sections. The LLM section lists LLM
profiles as cards and lets the operator add, change, delete, test, and activate one. The
Agents section is a placeholder.

On the service side, saved profiles live in two new tables with their keys encrypted under a
secret from the environment. One row holds which profile is active and a revision number. Every
run already starts in `run_stream`; there the service compares the revision it built for with
the current one and, when they differ, rebuilds the orchestrator through `build_agent` with the
active profile's model, which takes about 15 ms. A run in progress keeps the orchestrator it
started on. The model the environment configures appears as a read-only profile that is active
until a saved one is activated, so a deployment configured only through the environment behaves
as before, apart from one added field in a run's first event.

The service now starts when no usable model is active; a run then ends with a new error code
and nothing falls back to another model. A test sends a short conversation written by the test
itself, with a tool call and its result, so an endpoint that refuses everything after a first
message is caught before a run depends on it. No response, log line, or thread ever holds a
key.

The web app is a client of the API and nothing more. The browser calls `/api/...` on the web
app's own origin and Next.js forwards it to the service, so the API needs no cross-origin
configuration.

Design details are in [research.md](research.md); the entities and tables in
[data-model.md](data-model.md); the new routes in
[contracts/openapi.yaml](contracts/openapi.yaml); the stream changes in
[contracts/sse-events.md](contracts/sse-events.md); the settings in
[contracts/settings.md](contracts/settings.md); the manual walk-through in
[quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 for the service (`.python-version`), managed by uv with the
committed `uv.lock`, src layout. TypeScript 5 on Node 20.9 or newer for the web app, managed by
pnpm with the committed `web/pnpm-lock.yaml`.

**Primary Dependencies**: the service is unchanged from 003 (deepagents 0.7.32 on langgraph
1.2.18, fastapi 0.162.0, sse-starlette 3.6.0, pydantic-settings 2.16.1, langchain-openai 1.7.3,
langchain-anthropic 1.7.6, langchain-openrouter 0.2.9), plus `cryptography`, declared as a
runtime dependency and already in the lock at 50.0.2 through `a2a-sdk` (R4). The web app is
Next.js 16.4.6 with the App Router and Cache Components, React 19.3, Tailwind CSS 4, and
shadcn/ui in the `base-vega` style, plus `swr` (R14) and the shadcn components in R16. Its
`dev` dependencies gain `jsdom`, `@vitejs/plugin-react`, `@testing-library/react`,
`@testing-library/dom`, and `vite-tsconfig-paths` (R17).

**Storage**: two new tables in the existing Postgres database, created at startup like the run
lease and the agent tables: `freyja_llm_profiles` and the one-row `freyja_llm_state` (R5). No
migration tool; `CREATE TABLE IF NOT EXISTS`. An in-memory implementation of the same protocol
serves the suite. Keys are stored as Fernet tokens (R4). The web app stores nothing.

**Testing**: pytest 9 with pytest-asyncio, the scripted chat model, and in-memory persistence,
as before. New: a `model_factory` seam in `Runtime.assemble`; `tests/llm_stub.py`, a stand-in
OpenAI-compatible endpoint run in the test process; store contract tests collected against the
in-memory store and, opt-in, Postgres; two runtimes sharing one store for two instances (R18).
The web app's tests are Vitest with jsdom and Testing Library, with `fetch` stubbed (R17). No
network, no model provider.

**Target Platform**: Linux server process (uvicorn), one or more instances sharing one
database; the Next.js server on the same machine; a current desktop browser.

**Project Type**: web application: the existing service in `src/freyja_agent/` and the existing
web app in `web/`.

**Performance Goals**: a change of profile applies to the next run on every instance, with one
single-row read per run and a rebuild of about 15 ms when the revision moved (SC-002; FR-014's
bound is ten seconds). A profile test ends within 60 s (SC-005). The LLM section shows fifty
cards within 2 s on the service's machine (SC-008): one request, one query.

**Constraints**: exactly one active profile at every moment, guaranteed by the database (SC-009);
a run keeps its model (SC-003); no key in any response, log, stream, or thread, and none
readable in the database (SC-004); the service never switches profile by itself (FR-016); the
web app holds no credential and calls no model (constitution III); every existing test passes
unchanged (SC-006); no blocking I/O in async code (Ruff `ASYNC`); SQL is parameterized (Ruff
`S608`); under Cache Components, no clock read during render (R14).

**Scale/Scope**: at most 50 profiles, a handful of operators. Two settings and one web app
variable, one runtime dependency already in the lock, two tables, seven routes, one added field
in `start`, one added `error` code, one new package in the service (`llm/`), two pages and a
shell in the web app. Out of scope per the spec: the Agents section, authentication, access
from another machine, a model per role or per thread, delegate agents' models, usage reporting,
import and export.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against constitution 1.3.0, amended on 2026-11-17 for this feature.

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | No | No tool is added or reclassified, and the gate is generated from the catalog as before. Rebuilding the orchestrator reuses the same tools and the same gate (R1). A proposal pending when the model changes stays pending and is decided as usual; a run refused for lack of a model leaves it untouched (R8). Changing the model is an operator's request to the API, not an action the agent takes. | PASS | PASS |
| II. Protocol-based delegation | No | No delegate is added. The three model clients are the ones already installed; no agent framework is imported. | PASS | PASS |
| III. One runtime, every environment | Yes, central | `build_agent` stays the only assembly point and is what a rebuild calls (R1). `run_stream` stays the only run loop and is where a change is picked up (R2). The web app is a client of the API only: it holds no credential, calls no model, and has no Server Action or Route Handler; the browser's requests are forwarded by a rewrite (R13, R14). Tests swap one new seam, the model factory, and run the same routes (R18). | PASS | PASS |
| IV. Configuration through settings | Yes, central | Deployment configuration: two new settings with safe defaults, `FREYJA_SECRET_KEY` unset and the test limit at 60 s, both in `.env.example`; the web app's `FREYJA_API_URL` defaults to loopback and is in `web/.env.example` ([contracts/settings.md](contracts/settings.md)). Operator-managed configuration: profiles are kept in the database and changed only through the API (R5, R10). The environment's model stays valid as a read-only default (R3). A key an operator enters is stored encrypted under a key from the environment and is never returned, logged, or written to a thread (R4, R9). No address or model id is hardcoded outside tests. | PASS | PASS |
| V. Automated quality gates | Yes | Every FR has a test (map below); the default suites need no network, database, or model. Model calls and database calls are async; the test's limit is `asyncio.timeout`; SQL is parameterized. The web app passes `biome check` and its Vitest suite, enforced by the same hook. No new Ruff suppression is planned. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes | Whether a run may call a model is a fixed check in `run_stream` before the graph is invoked (R8). Which profile is active, and that there is exactly one, is enforced by the database, not by code that could race (R5). Key removal is one function applied at every exit (R9). None of these depends on a model. | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; the Postgres pool; Ruff only for Python. The web app stays Next.js with TypeScript, pnpm, Biome, and Vitest. The two tables use the same database and pool as the run lease. | PASS | PASS |

No deviations from the constitution. Three additions carry complexity the governance section
asks to justify; none is a violation, so they are justified here rather than in Complexity
Tracking:

- **Two tables.** "Exactly one active profile" has to hold across instances and across a
  restart in the middle of a change, which only the database can guarantee cheaply (R5).
- **A rebuild of the orchestrator at run time.** Until now the graph was built once. Rebuilding
  through the same factory is what lets a change apply without a restart while a run in
  progress keeps its model (R1); the alternative, a model object that changes underneath a
  built graph, is harder to reason about.
- **Starting without a usable model.** It reverses a startup failure. It is what the spec
  asks for (FR-016, clarified 2026-11-17), and the refusal moves to a fixed check before each
  run (R8).

## Project Structure

### Documentation (this feature)

```text
specs/004-web-llm-profiles/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1 to R20
├── data-model.md        # Phase 1: entities, tables, transitions
├── quickstart.md        # Phase 1: manual validation, one section per user story
├── contracts/
│   ├── openapi.yaml     # the /llm routes and their shapes
│   ├── sse-events.md    # `start.profile` and the `no_usable_model` error
│   └── settings.md      # FREYJA_SECRET_KEY, the test limit, FREYJA_API_URL
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # from /speckit-tasks, not created by this command
```

### Source Code (repository root)

```text
src/freyja_agent/
├── config.py                  # + secret_key (a Fernet key or unset) and
│                              #   llm_test_timeout_seconds
├── llm/                       # NEW package
│   ├── providers.py           # the provider table; ModelSpec; build_model (R3);
│   │                          #   environment_profile(); the placeholder model (R8)
│   ├── secrets.py             # encrypt, decrypt, key_hint (R4); redact (R9)
│   ├── store.py               # LlmProfileStore protocol; InMemoryLlmProfileStore;
│   │                          #   PostgresLlmProfileStore with the two tables (R5)
│   ├── active.py              # ActiveModel and the manager: load the active profile, compare
│   │                          #   the revision, rebuild under a lock (R1, R6)
│   └── check.py               # the two-step profile test (R7)
├── agent/
│   └── factory.py             # unchanged in behavior; build_agent is called again on a rebuild
├── runtime/
│   └── runtime.py             # Runtime holds the profile store and the manager;
│                              #   assemble(model_factory=...) builds through the manager and
│                              #   no longer fails when the model cannot be built (R8)
├── delegates/
│   └── delegation_worker.py   # starts no background run while no usable model is active (R8)
└── api/
    ├── app.py                 # + the llm router
    ├── llm.py                 # NEW: the seven routes; validation errors without `input` (R9, R10)
    ├── schemas.py             # + LlmProfile, LlmProfileList, LlmProfileCreate, LlmProfileUpdate,
    │                          #   LlmProfileDraft, LlmTestResult; ErrorDetail codes
    └── streaming.py           # picks up the current orchestrator, names the profile in `start`
                               #   and one log line, ends with `no_usable_model`, cleans
                               #   `model_error` of the key (R2, R8, R9, R11)

tests/
├── llm_stub.py                        # NEW: stand-in OpenAI-compatible endpoint, by model id
├── test_llm_secrets.py                # encrypt, hint, redact
├── test_llm_profile_store.py          # store contract, in memory
├── test_llm_profiles_api.py           # the routes, their refusals, no key anywhere
├── test_llm_switch.py                 # activation, runs in progress, pauses, two instances
├── test_llm_check.py                  # the test, against the stand-in
├── test_llm_no_model.py               # startup and runs without a usable model
├── test_config.py                     # + the two settings
└── integration/
    └── test_llm_profiles_postgres.py  # the store contract and the one-active rule on Postgres

web/
├── next.config.ts             # + rewrites (/api to the service) and the redirect from / (R13, R15)
├── .env.example               # NEW: FREYJA_API_URL
├── vitest.config.mts          # NEW: jsdom, React plugin, tsconfig paths (R17)
├── app/
│   ├── layout.tsx             # the shell: sidebar and page
│   ├── page.tsx               # REMOVED: / redirects to /llm
│   ├── llm/page.tsx           # NEW: the LLM section
│   └── agents/page.tsx        # NEW: the placeholder
├── components/
│   ├── app-sidebar.tsx        # NEW: the sidebar and its links (client), with its test
│   ├── llm/                   # NEW: profile list, card, form dialog, activate and delete
│   │                          #   confirmations, test result; each with its test
│   └── ui/                    # + the shadcn components in R16
└── lib/
    ├── api.ts                 # NEW: fetch to /api, the error shape, with its test
    └── llm.ts                 # NEW: the profile types and the calls, with its test
```

Also edited: `pyproject.toml` and `uv.lock` (`cryptography` declared), `.env.example` (the two
settings), `README.md` (the console, LLM profiles, the secret, starting without a model, the
web app's commands), `AGENTS.md` (layout, the `llm/` package, web tests no longer Node-only),
`web/package.json` and `web/pnpm-lock.yaml`, `web/.gitignore` (keep `.env.example`), and 001's
`contracts/sse-events.md`, so the stream contract names the new field and code where a reader
looks first.

**Structure Decision**: the two existing projects, each in its place: the service in the src
layout and the web app under `web/`. The profile code gets its own package, `llm/`, because it
is neither a delegate nor only shared state: it holds the provider table, the secrets, the
store, the manager, and the test. `runtime/` keeps the wiring, and `api/` the routes.

### Requirement to test map

| Requirement | Tests |
|---|---|
| FR-001, FR-002 | `web/components/app-sidebar.test.tsx`; quickstart 1 for the narrow screen |
| FR-003 | `web/lib/api.test.ts`, `web/components/llm/profile-list.test.tsx` (unreachable, refusal) |
| FR-004, FR-005, FR-006 | `test_llm_profile_store.py`, `test_llm_profiles_api.py`, `integration/test_llm_profiles_postgres.py` |
| FR-007 | `web/components/llm/profile-card.test.tsx`, `profile-list.test.tsx` |
| FR-008 | `test_llm_profiles_api.py` (environment profile, read-only) |
| FR-009, FR-010 | `test_llm_profiles_api.py`; `web/components/llm/profile-form.test.tsx`, `delete-dialog.test.tsx` |
| FR-011, FR-012, FR-015 | `test_llm_switch.py`, `test_llm_profile_store.py`, `integration/test_llm_profiles_postgres.py`; `web/components/llm/activate-dialog.test.tsx` |
| FR-013, FR-014 | `test_llm_switch.py` (next run, run in progress, pause, background run, two runtimes) |
| FR-016 | `test_llm_no_model.py` |
| FR-017, FR-018, FR-019, FR-019a | `test_llm_check.py`, `test_llm_profiles_api.py` |
| FR-020, FR-021, FR-022 | `test_llm_secrets.py`, `test_llm_profiles_api.py` (a recognizable key in no response or log), `test_llm_check.py` (a key repeated by the endpoint) |
| FR-023 | the existing suite, unchanged |
| FR-024, FR-024a | `test_llm_profiles_api.py` (log lines), `test_llm_switch.py` (`start.profile`) |
| FR-025 | all of the above |

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

None. The Constitution Check passed without deviations before research and after design. The
tables, the rebuild, and starting without a usable model are justified under the Constitution
Check above.
