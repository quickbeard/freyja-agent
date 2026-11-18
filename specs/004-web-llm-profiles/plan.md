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

Checked against constitution 1.3.0, amended on 2026-11-17 for this feature, and again against
1.4.0, which says where the web app reads its configuration (see Analysis below).

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | No | No tool is added or reclassified, and the gate is generated from the catalog as before. Rebuilding the orchestrator reuses the same tools and the same gate (R1). A proposal pending when the model changes stays pending and is decided as usual; a run refused for lack of a model leaves it untouched (R8). Changing the model is an operator's request to the API, not an action the agent takes. | PASS | PASS |
| II. Protocol-based delegation | No | No delegate is added. The three model clients are the ones already installed; no agent framework is imported. | PASS | PASS |
| III. One runtime, every environment | Yes, central | `build_agent` stays the only assembly point and is what a rebuild calls (R1). `run_stream` stays the only run loop and is where a change is picked up (R2). The web app is a client of the API only: it holds no credential, calls no model, and has no Server Action or Route Handler; the browser's requests are forwarded by a rewrite (R13, R14). Tests swap one new seam, the model factory, and run the same routes (R18). | PASS | PASS |
| IV. Configuration through settings | Yes, central | Deployment configuration: two new settings with safe defaults, `FREYJA_SECRET_KEY` unset and the test limit at 60 s, both in `.env.example`; the web app's `FREYJA_API_URL` defaults to loopback and is in `web/.env.example` ([contracts/settings.md](contracts/settings.md)). Operator-managed configuration: profiles are kept in the database and changed only through the API (R5, R10). The environment's model stays valid as a read-only default (R3). A key an operator enters is stored encrypted under a key from the environment and is never returned, logged, or written to a thread (R4, R9). No address or model id is hardcoded outside tests. | PASS | PASS |
| V. Automated quality gates | Yes | Every FR has a test (map below); the default suites need no network, database, or model. Model calls and database calls are async; the test's limit is `asyncio.timeout`; SQL is parameterized. The web app passes `biome check` and its Vitest suite, enforced by the same hook. No new Ruff suppression is planned. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes | Whether a run may call a model is a fixed check in `run_stream` before the graph is invoked (R8). Which profile is active, and that there is exactly one, is enforced by the database, not by code that could race (R5). Key removal is one function applied at every exit (R9). None of these depends on a model. | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; the Postgres pool; Ruff only for Python. The web app stays Next.js with TypeScript, pnpm, Biome, and Vitest. The two tables use the same database and pool as the run lease. | PASS | PASS |

No deviations from the constitution. One reading of principle IV is recorded here because the
converge step asked for it (T089): the service reads the providers' own key and base URL
variables, such as `OPENAI_API_KEY`, from the process environment, outside the `Settings`
class. Principle IV says deployment configuration is read through `Settings` from `FREYJA_*`
variables. These are not `FREYJA_*` variables and not the service's configuration: they belong
to the provider's client library, which has read them itself since feature 001, where
`.env.example` says that settings never see the key. Feature 002 reads the variables named in
a tool server's `pass_env` the same way. The service does not use them to configure itself.
It looks at them to describe the environment's profile (R3): its key's last four characters,
its base URL, whether a key is set, and the key's text to take out of an error. One module
reads them, `llm/providers.py`. Putting them into `Settings` would give the service a copy of
a key it has no use for, which is what feature 001 avoided.

A second reading of the same principle, recorded with T095: it says hosts are not hardcoded
in application code, and the provider table holds each provider's own address, such as
`https://api.openai.com/v1`. That is not where a deployment's parts are; it is what the
provider is, like its name, and it is the constant its client library already carries. It is
passed to the client only for a saved profile with no base URL, so that the client does not
take one from the environment's variables instead. Nothing a deployment could want to change
is fixed by it: an operator who wants another address gives the profile a base URL.

Three additions carry complexity the governance section asks to justify; none is a
violation, so they are justified here rather than in Complexity Tracking:

- **Two tables.** "Exactly one active profile" has to hold across instances and across a
  restart in the middle of a change, which only the database can guarantee cheaply (R5).
- **A rebuild of the orchestrator at run time.** Until now the graph was built once. Rebuilding
  through the same factory is what lets a change apply without a restart while a run in
  progress keeps its model (R1); the alternative, a model object that changes underneath a
  built graph, is harder to reason about.
- **Starting without a usable model.** It reverses a startup failure. It is what the spec
  asks for (FR-016, clarified 2026-11-17), and the refusal moves to a fixed check before each
  run (R8).

### Analysis (2026-11-17)

`/speckit-analyze` found one point where this check was too generous. Principle IV says
deployment configuration MUST be read through the `Settings` class. The web app's one variable,
`FREYJA_API_URL`, is read by `web/next.config.ts`; a Python class cannot cover it. The row above
passes for the service and for everything else it lists. For this variable the constitution has
to say where the web app's configuration is read, in an amendment of its own, before the tasks
that add the variable (T008, T009). Until then the variable is also named in the root
`.env.example`, so that every variable is documented where principle IV says to look.

Resolved the same day by constitution 1.4.0: principle IV now says the web app reads its
deployment configuration from `FREYJA_*` variables in `web/next.config.ts` and documents it in
`web/.env.example`, which is what R13 and T008 and T009 do. The row for principle IV passes
for the web app as well.

### Checklist review (2026-11-17)

`checklists/security-reliability.md` raised sixteen findings against the spec and this plan.
All were resolved in the spec the same day, and the design changed in four places: the API
refuses other origins by requirement and by test (FR-003a, R12); the worker's rule without a
usable model is now a requirement (FR-016a, R8); tests are limited to one per profile and four
per instance, refused with `test_busy` (FR-019b, R7); and a confirmation is asked before a
change to the active profile is saved (FR-010). The Constitution Check is unchanged: the fixes
add fixed rules of the kind principle VI asks for and touch no other principle.

## Project Structure

### Documentation (this feature)

```text
specs/004-web-llm-profiles/
├── spec.md              # from /speckit-specify, clarified
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1 to R20
├── data-model.md        # Phase 1: entities, tables, transitions
├── quickstart.md        # Phase 1: manual validation, one section per user story
├── contracts/
│   ├── openapi.yaml     # the /llm routes and their shapes
│   ├── sse-events.md    # `start.profile` and the `no_usable_model` error
│   └── settings.md      # FREYJA_SECRET_KEY, the test limit, FREYJA_API_URL
├── checklists/
│   ├── requirements.md  # from /speckit-specify
│   └── security-reliability.md  # from /speckit-checklist
└── tasks.md             # from /speckit-tasks: nine phases, each one pull request
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
│   ├── guard.py               # guard_model: a model raises its errors without its key
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
├── fakes.py                           # + the test model factory and a labelled model
├── conftest.py                        # + make_llm_app
├── test_llm_secrets.py                # encrypt, hint, redact
├── test_llm_guard.py                  # a guarded model's errors, called, streamed, unchanged
├── test_llm_providers.py              # the provider table, building a model, the
│                                      #   environment's profile
├── test_llm_schemas.py                # the request and response shapes of the routes
├── test_llm_profile_store.py          # store contract, in memory
├── test_llm_profiles_api.py           # the routes, their refusals, other origins
├── test_llm_secrets_nowhere.py        # one recognizable key, searched for in everything
│                                      #   the service returns, streams, keeps, logs, stores
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
├── vitest.setup.ts            # NEW: unmounts after each test
├── __tests__/                 # NEW: the unit tests, laid out as the code they cover, and
│                              #   testing.ts, the fetch stub they share
├── e2e/                       # + console.spec.ts, llm.spec.ts, profiles.spec.ts: the
│                              #   end-to-end tests of the console (Playwright), and api.ts,
│                              #   the service's answers they hand to a page
├── app/
│   ├── layout.tsx             # the shell: sidebar and page
│   ├── page.tsx               # REMOVED: / redirects to /llm
│   ├── llm/page.tsx           # NEW: the LLM section
│   └── agents/page.tsx        # NEW: the placeholder
├── components/
│   ├── app-sidebar.tsx        # NEW: the sidebar and its links (client)
│   ├── llm/                   # NEW: profile-list, profile-card, profile-form (add and
│   │                          #   edit), activate-dialog, delete-dialog, test-result
│   └── ui/                    # + the shadcn components in R16
├── hooks/use-mobile.ts        # NEW: written by the shadcn CLI for the sidebar
└── lib/
    ├── api.ts                 # NEW: fetch to /api, the error shape
    └── llm.ts                 # NEW: the profile types and the calls
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
| FR-001, FR-002 | `web/__tests__/components/app-sidebar.test.tsx`; quickstart 1 for the narrow screen |
| FR-003 | `web/__tests__/lib/api.test.ts`, `web/__tests__/components/llm/profile-list.test.tsx` (unreachable, refusal), `profile-form.test.tsx` (a typed key is cleared and never stored) |
| FR-003a | `test_llm_profiles_api.py` (no permission for another origin; a body that is not JSON is refused) |
| FR-004, FR-005, FR-006 | `test_llm_providers.py`, `test_llm_schemas.py`, `test_llm_profile_store.py`, `test_llm_profiles_api.py`, `integration/test_llm_profiles_postgres.py` |
| FR-007 | `web/__tests__/components/llm/profile-card.test.tsx`, `profile-list.test.tsx` |
| FR-008 | `test_llm_providers.py` (the environment's profile), `test_llm_profiles_api.py` (listed, read-only) |
| FR-009, FR-010 | `test_llm_profiles_api.py`; `web/__tests__/components/llm/profile-form.test.tsx` (confirmation when the profile is active), `delete-dialog.test.tsx` |
| FR-011, FR-011a, FR-012, FR-015 | `test_llm_switch.py`, `test_llm_profile_store.py`, `integration/test_llm_profiles_postgres.py`; `web/__tests__/components/llm/activate-dialog.test.tsx` (names the endpoint conversations go to) |
| FR-013, FR-014 | `test_llm_switch.py` (next run, run in progress, pause, background run, two runtimes) |
| FR-016, FR-016a | `test_llm_no_model.py` (runs refused, the health check unchanged, no background run and no attempt used) |
| FR-017, FR-018, FR-019, FR-019a, FR-019b | `test_llm_check.py`, `test_llm_profiles_api.py` |
| FR-020, FR-021, FR-022 | `test_llm_secrets.py`, `test_llm_secrets_nowhere.py` (a recognizable key in no response, stream, thread, log, or stored row), `test_llm_profiles_api.py` (a missing or changed secret), `test_llm_check.py` (a key repeated by the endpoint), `test_config.py` (a secret that is not valid stops startup); `web/e2e/profiles.spec.ts` (no key on the page or in the browser's storage) |
| FR-023 | the existing suite, unchanged but for one assertion on the whole `start` event (see Phase 2 as built) |
| FR-024, FR-024a | `test_llm_profiles_api.py` (log lines), `test_llm_switch.py` (`start.profile`), `test_llm_no_model.py` (a refused run and an unusable profile are logged) |
| FR-025 | all of the above |

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

None. The Constitution Check passed without deviations before research and after design. The
tables, the rebuild, and starting without a usable model are justified under the Constitution
Check above.

## Implementation Notes

What the implementation settled differently from the plan, or found where the plan could not
check, phase by phase.

### Phase 1 as built (2026-11-17)

- **`uv.lock`** (T001): declaring `cryptography>=50,<51` added two lines and no package;
  `cryptography` stays at 50.0.2. uv 0.12.21 rewrote the lock's `revision` from 5 to 3; the
  line was put back to 5 so the diff holds only the dependency, and `uv lock --check` passes.
- **A blank secret** (T003): `FREYJA_SECRET_KEY=` with nothing after it counts as unset, not as
  an invalid value, so a line left empty in `.env` does not stop startup.
- **SWR** (T005, research "Not verified"): `swr` 2.5.1 lists React `^19.0.0` among its peer
  dependencies, so it is kept. pnpm reports one peer issue that was there before this phase:
  `vitest` 5.1.0 wants `@types/node` 22 or 24, and the scaffold pins `^20`. Type-checking
  passes; nothing was changed.
- **Vitest** (T006): `web/vitest.config.mts` is the guide's, plus `web/vitest.setup.ts`, which
  unmounts after each test. Testing Library only does that by itself with Vitest's globals,
  which stay off.
- **shadcn components** (T007, research "Not verified"): the CLI has all eleven for the
  `base-vega` style. It also wrote what they depend on: `separator`, `tooltip`, `sheet`, and
  `web/hooks/use-mobile.ts`, and added `sonner` and `next-themes` to the dependencies. Existing
  files were kept, not overwritten. The sidebar's breakpoint is 768 pixels, as FR-001 says.
  The tooltip needs a `TooltipProvider` around the app, which Phase 3's layout adds.
- **Lint rules for generated files** (T007): after `pnpm format` and Biome's safe fixes, three
  rules still fail in `label.tsx` and `sidebar.tsx`: `a11y/noLabelWithoutControl`,
  `correctness/useExhaustiveDependencies`, and `suspicious/noDocumentCookie`. They are off for
  `web/components/ui/**` only, in `web/biome.json`, because those files are generated by the
  shadcn CLI and are not edited by hand. Every other file keeps the full rule set.
- **When the rewrite's destination is read** (T009, research "Not verified"): at `next build`.
  It is written into the build, and `next start` does not read `FREYJA_API_URL` again: an app
  built for one address kept forwarding to it when started with another. `next dev` reads it
  when it starts. `web/.env.example` and [contracts/settings.md](contracts/settings.md) say so.
  A deployment that must change the address without rebuilding would need a Route Handler in
  place of the rewrite, which R13 set aside; nothing in this feature needs that.
- **The checkpoint** was run with the real service: through the web app's dev server,
  `/api/healthz` and `/api/tools` answered what the service answers directly.

### Phase 2 as built (2026-11-17)

- **One earlier test changed** (T021). R11 said no existing test compares the whole `start`
  event. `tests/test_runs.py` does, so its assertion now expects `profile`. It is the only
  earlier test that changed. The spec's FR-023 and SC-006 and research R11 and R19 are
  corrected to say so.
- **The environment's model still goes through `resolve_model`** (T019). When no model factory
  is given, the manager builds the environment's model with the function the factory has
  always used, and `build_model` is for saved profiles. A test factory, when given, builds
  both. This keeps the environment's path exactly what it was, which the earlier tests check.
- **`build_model` and `resolve_model` both hold the API-choice rule** (T014). The rule is two
  lines; `agent/factory.py` was left untouched so that nothing about the environment's path
  moved in this phase.
- **`environment_profile` builds the model to say whether it is usable** (T014). That is the
  only way to know, since one client builds without a key and another does not. It opens no
  connection.
- **The store checks the rules too** (T015, T016), not only the request shapes: lengths, the
  provider, the address, and the reserved name. A value sent unchanged is not a change, so it
  moves neither `config_version` nor `revision`; a key is always a change, because two tokens
  of one key differ.
- **`Runtime.models` may be absent** (T019). A runtime put together by hand, as one test
  fixture does, has no manager; its runs name the environment's profile.
- **A run whose profile cannot be read** (T021) ends with `start` and an `internal` error and
  releases its lease. The plan did not say what happens when the single-row read fails.
- **Unusable models still stop startup**, as planned for this phase: Phase 7 is where that
  changes. The placeholder model exists and is tested, and nothing uses it yet.
- **Test files beyond the plan's tree**: `tests/test_llm_providers.py` (T011) and
  `tests/test_llm_schemas.py`, for the request and response shapes of T020.
- **The checkpoint** was run with the real service and model: a run answered, its first event
  named the environment's profile, the log had one line for it, and `freyja_llm_profiles` and
  `freyja_llm_state` existed with the environment active at revision 0.

### Phase 3 as built (2026-11-17)

- **No hydration warning** (T037, research "Not verified"). The browser console showed none
  with `usePathname()` in `web/components/app-sidebar.tsx`, on `/llm`, on `/agents`, and after
  the redirect from `/`, so the documented fix was not needed.
- **The browser check** (T037) was run in headless Chrome against `next dev` and the real
  service: `/` lands on `/llm` with LLM marked; one card, Environment, marked active and from
  the environment, with the provider, model, base URL, API choice, and four key characters,
  and no Edit or Delete; the Agents page has its sentence and no control of its own; in a
  500-pixel window the sidebar is hidden and opens from its control; with the service stopped
  the page says the service cannot be reached, offers to try again, and shows no card.
- **One main landmark** (T033). The generated `SidebarInset` is itself `<main>`, so the layout
  wraps the page in a `<div>`. The first version nested a second `<main>`; the check found it.
- **In development the page is opened at `localhost`**, as the quickstart says. `next dev`
  refuses its own scripts to a page opened at `127.0.0.1` unless `allowedDevOrigins` names it,
  and the list then stays on its loading state. Nothing was configured: a built app is not
  affected, and the documented address works.
- **The environment is described once, at startup** (T029). `Runtime.environment` holds it, so
  reading the list builds no model. When the environment's profile is the active one the
  manager has just built its model, and the description does not build it a second time. A
  model handed to the runtime already built, as the tests do, is listed under the model
  setting's name and as usable.
- **`secret_not_configured` comes before `key_unreadable`** (T029): with no secret set every
  saved profile is listed as not usable for that reason, and a key is only tried against a
  secret that exists.
- **A reply that is not JSON counts as unreachable** (T030). When the service is stopped the
  web app's server answers the forwarded request with a 500 and no JSON body, so
  `web/lib/api.ts` reports an error reply it cannot parse the same way as a failed request.
- **A Refresh button** (T035) sits above the cards. The plan named only "try again" after a
  failure; the list is otherwise read once, and a second way to read it costs one button.
  SWR does not retry a failed read by itself here, and a failed read never shows older cards.
- **The loading state is an `<output>`** (T035), which carries the status role Biome asks for
  in place of `role="status"` on a `<div>`.
- **Web tests live under `web/__tests__/`** (decided in review of this phase). The plan had
  each test next to the code it covers. They were moved to one directory that follows the
  layout of the code, so `web/__tests__/lib/api.test.ts` covers `web/lib/api.ts`, and they
  import what they cover through the `@/` alias. `AGENTS.md`, the README, research R17, and
  every path in tasks.md say so, for the tests of later phases too.
- **Files beyond the plan's tree**: `web/__tests__/testing.ts`, the `fetch` stub the web tests
  share, and `web/__tests__/app/agents/page.test.tsx`. `web/vitest.setup.ts` now also stubs
  `matchMedia`, which jsdom lacks and the sidebar reads, and undoes stubbed globals after each test.
- **The stock page's files are gone** (T036): `web/app/page.tsx` and the five images under
  `web/public/`, which only that page used.
- **End-to-end tests** (T037a, added after the plan). Playwright came to `main` while this phase
  was in review, so the browser check of T037 is now kept as tests: `web/e2e/console.spec.ts`
  and `web/e2e/llm.spec.ts`, eleven tests beside the existing smoke test, run in Chromium and
  Firefox against the built app. They need no service: `web/e2e/api.ts` hands a page what the
  service would answer, in the browser. The app under test forwards `/api/...` to an address
  nothing listens on, so the two tests about a stopped service go through the web app's own
  server, as the hand check did.
- **Every later story has an end-to-end task too** (T047a, T055a, T063a, T067a, T073a, added
  with Phase 4). They follow the story's implementation and drive the built console in
  Chromium and Firefox. From Phase 4 on a page changes profiles, so `web/e2e/api.ts` becomes
  a stand-in for the profile routes that keeps what a page saved and activated, still in the
  browser and with no service.
- **The sidebar now closes once a section is chosen in a narrow window** (T032, FR-001). The
  end-to-end test found that it stayed open over the page it had just led to; the hand
  check of T037 had only opened it. `web/components/app-sidebar.tsx` closes it on a link's
  click, and a unit test covers that.
- **The sidebar has a grey and an edge of its own** (T032, decided in review). With the stock
  theme the sidebar was a 0.985 grey beside a white page, and its edge the page's border
  color, so neither could be seen. `web/app/globals.css` now gives the light theme's sidebar
  a 0.955 grey, a 0.85 edge, and a 0.9 accent for the section in use, which has to stay
  darker than the sidebar; `web/components/app-sidebar.tsx` draws the edge in the sidebar's
  border color. The dark theme's values already differed and are unchanged. An end-to-end
  test compares the computed colors, which a unit test in jsdom cannot.
- **Two things a browser test of this app must allow for.** Next.js keeps an element with
  the alert role of its own outside the page, to announce navigations, so the tests look
  for alerts inside the main landmark. And after moving to another section, the one left
  behind is still in the document, hidden, so a count of what a page offers counts only
  what can be seen.
- **Order of the service test** (T024, T029). The web tests were seen failing before their
  code existed. The service test was written before the route and first run after it.

### Phase 4 as built (2026-11-18)

- **What activation refuses** (T043). A profile that is not usable in the sense of FR-011a: a
  saved profile whose key cannot be read on this instance, or the environment's profile when
  the environment's is not usable. The refusal comes before anything is changed, so the earlier
  profile stays active, as FR-012 asks.
- **Applying it at once does not decide the activation** (T043). After the choice is changed,
  this instance rebuilds its orchestrator at once (R6). If that rebuild fails, the activation
  still stands and the failure is logged without the key: every instance takes the choice up
  at its next run, where a run reports what is wrong. The plan did not say what an immediate
  rebuild that fails should do; refusing would have left the choice changed and the answer an
  error.
- **An activation that changes nothing is not logged and rebuilds nothing** (T043): the
  store's revision stays where it was, which is how the route tells.
- **A body that is not JSON was already refused** (T042, FR-003a). FastAPI 0.162 answers 422
  to a body sent as text or as a form on these routes, so nothing was added; three tests hold
  it in place.
- **A rule the request shape and the store disagree on is an internal error** (T042). Both
  check the same lengths and the web address. The store's refusal can only be reached if the
  two drift apart, and is answered 500 rather than shown as the operator's mistake.
- **The switch tests give the environment its key** (T039). `tests/test_llm_switch.py` now
  sets `OPENAI_API_KEY` for every test, because activating the environment's profile requires
  it to be usable. Part 1's tests never activated through the route and did not need it.
- **A key stays in the open form after a refused save** (T045, FR-003). The form drops the
  key when a save succeeds and when the form closes. After a refusal, such as a taken name,
  the form stays open with what was typed, the key included, in a password field, so only the
  wrong value has to be changed. FR-003's "until the form is sent or closed" is read as a
  form that was sent successfully.
- **The form's fields start again whenever it opens or closes** (T045, R14): they are keyed
  by a counter that both move, and the dialog's content is unmounted while closed.
- **Set active is offered where it can succeed** (T047): on a usable profile that is not the
  active one, the environment's included. The service still refuses a profile that became
  unusable since the list was read, and the confirmation shows that refusal.
- **The provider is the generated select** (T045). In a unit test it is opened and chosen
  from with the keyboard: under jsdom a click on an option does not select it.
- **The form scrolls inside itself** (T045) when it is taller than the window, which it can
  be once it says what is wrong with several fields.
- **End-to-end** (T047a). `web/e2e/api.ts` gained `ProfileService`, a stand-in for the profile
  routes that keeps what a page saved and activated and records every request, and
  `web/e2e/profiles.spec.ts` has nine tests on it. `answerProfiles` stays for the tests that
  only read the list.
- **The checkpoint** was run by hand against the real service on a database made for it and
  dropped afterwards: a profile added through the API was stored with its key as a token that
  does not contain the key; a taken name, a body sent as text, a base URL that is not a web
  address, and an unknown id were refused as the contract says; after an activation a run's
  first event and log line named the profile, with no restart; after a restart the profile was
  still active; a second instance on the same database took up an activation made through the
  first at its next run; and in a browser the console added a profile through the form, showed
  the service's "name taken" at the name, and moved the Active mark after the confirmation.
  No log held either key. **Not checked**: a run answered by the environment's real model
  after switching back to it. The run named the environment's profile, and the model's
  endpoint could not be reached from the machine at the time, with or without Freyja.

### Phase 5 as built (2026-11-18)

- **A model built for a test tries nothing twice** (T052). `ModelSpec` gained `max_retries`,
  which `model_arguments` passes on when it is set, and a test builds its model with 0. All
  three providers' clients take the argument (*verified*). Without it a refused request was
  sent three times and the time limit was only ever reached.
- **The limit is called `limit`, not `timeout`** (T052). Ruff's `ASYNC109` refuses an async
  function with a `timeout` parameter; `check_model` takes `limit` and applies it with one
  `asyncio.timeout` around both steps.
- **The time limit covers both steps together**, as FR-019 says. Seen by hand: with the limit
  set to 4 seconds the environment's real model answered the first message and ran out of
  time on the second; with the default it passed in 3.5 seconds.
- **Values that make no model are a failed test** (T053), reported at the first step with the
  reason, not an error of the service.
- **Which tests count against which limit** (T053, FR-019b). The environment's profile is a
  listed profile here, under the id `environment`, so it too is tested once at a time. A test
  of unsaved values counts only toward the four. Both limits are checked before a model is
  built.
- **The environment's profile is tested as the environment configures it** (T053): from the
  model setting, with no key or address of its own, so the client reads its variables as a
  run's does. A model handed to the runtime already built, as tests do, is asked as it is.
  An environment that is not usable is refused with `profile_unusable`, like a saved profile
  whose key cannot be read.
- **The time in a result** (T053). A kept result carries the time the store recorded it. A
  result that is not kept, and the environment's, carry the service's own clock.
- **One log line per test** (T053), naming the profile and the step that failed, with no
  reason and no key. FR-024 does not ask for it; a failed test is worth finding later.
- **The stand-in** (T048) answers in both APIs' real shapes, so the suite reaches it through
  the real `ChatOpenAI` client with only the transport replaced (`LlmStub.factory`). One test
  goes the whole way: the routes, the model built for a test, the client, and the stand-in.
- **A card's own result gives way once the profile changes** (T055). A result of a test run
  from a card is shown at once and tied to the profile's `updated_at`; when that moves, the
  card shows what the list says again, so an old pass is never shown for new values.
- **The time is the service's, put in the reader's words in the browser** (T055, R14): a
  `<time>` element carries the value the service gave, formatted there. Cards are only
  rendered in the browser, after the list is read, so nothing is formatted while a page is
  prerendered.
- **The environment's card says "Not tested since the service started"** (T055), because its
  result is kept in memory; a saved profile's says "since its last change".
- **Test in the Edit form with an empty key** (T051). `toDraft(values, storedKeyOf)` in
  `web/lib/llm.ts` sends the profile's id in place of a key and is unit tested. The form that
  uses it with a profile is the Edit form, which arrives in Phase 6 (T063); its form-level
  test moved to T058.
- **Test needs no name** (T055): the form checks the model id, the base URL, and the key
  before a test, and the name only before a save. A changed value takes a shown result back.
- **End-to-end** (T055a). `ProfileService` answers the two test routes by model id, as the
  stand-in does, and `change()` plays another operator editing a profile. While a
  confirmation is open, the page behind it cannot be looked into by role, so one test asks
  the stand-in what is active. The environment card's test in `llm.spec.ts` now expects the
  one button a card always has, Test.
- **The checkpoint** was run by hand against the real service on a database made for it and
  dropped afterwards, with `tests/llm_stub.py` served on loopback: a working profile passed;
  one refusing earlier turns failed at the conversation over the Responses API with the
  endpoint's reason; one rejecting the key failed at the first message; one that never
  answers ended at the limit, in 4.02 seconds with the limit at 4; a second test of a profile
  being tested was refused with `test_busy`; unsaved values were tested and nothing was saved,
  with a repeated key removed from the reason; unsaved values with no key used a saved
  profile's; results were still there after a restart and the environment's was gone; and
  the environment's real model passed. In a browser the cards showed the kept results, Test
  on a card showed a pass and a failure with its reason, the confirmation warned and tested
  first when asked, and Test in the form saved nothing. No log held a key.

### Phase 6 as built (2026-11-18)

- **Only a base URL can be taken away** (T059). A field left out of a change is kept. Sending
  null clears a base URL and is refused with 422 for any other field, the key included, so
  "no key" can only ever mean "keep the stored key" (FR-009).
- **A change that changes nothing is not logged and rebuilds nothing** (T059). The log line
  for a real change names the fields that changed and, of a key, only that it did.
- **A change to the active profile is applied at once on this instance** (T059), by the same
  best-effort step as an activation: the change stands whatever that step finds, and every
  instance takes it up at its next run.
- **Without a secret, a new key is refused and other changes are not** (T059). A profile's
  name, model, or address can still be changed; `secret_not_configured` is for the key.
- **Entering its key again makes an unreadable profile usable** (T059, FR-022): the change
  stores the new key under the current secret. The card offers Edit on a profile that is not
  usable for that reason.
- **Delete is offered on every saved card, the active one included** (T063). The service
  refuses the active one, and the confirmation shows its refusal as it worded it, which says
  to activate another profile first (FR-010).
- **The confirmation for the active profile is part of the form** (T063), not a second
  dialog over it: Save shows what the change means and the buttons become Back and Confirm.
  Nothing is sent before Confirm, and changing a value afterwards takes the confirmation back.
- **The Edit form sends every value but an empty key** (T063). The service ignores a value
  that is unchanged, so the form does not work out what changed.
- **The list keeps a profile while its form closes** (T063), so the form does not turn into
  another one on its way out.
- **The form test moved from T051 is here** (T058): in the Edit form, Test with the key left
  empty sends the profile's id in place of a key.
- **The stand-in answers a request that asks for a stream** (found at this checkpoint). A run
  streams its requests, and `tests/llm_stub.py` only answered unstreamed ones, so a run on a
  profile pointing at it ended with `model_error`, while the quickstart expects such runs to
  complete. It now streams in both APIs' own events, logs the model each request asked for
  when it is served, as quickstart section 4 relies on, and a test runs a whole run through
  the real client against it.
- **End-to-end** (T063a). `ProfileService` gained change and delete, with the service's
  refusals, and `web/e2e/profiles.spec.ts` eight tests of this story.
- **The checkpoint** was run by hand against the real service on a database made for it and
  dropped afterwards, on a port of its own beside a service that was already running, with
  the stand-in on loopback: a rename with the key left out kept the key's four characters and
  the test result; a new key changed the four characters and cleared the result, and a test
  then reached the stand-in with it; with the profile active, a change of model was asked of
  the stand-in at the next run, with no restart, and again when it was changed back; the
  active profile and the environment's refused deletion and change as the contract says; an
  inactive profile was deleted and stayed gone after a restart, where the edit was still
  there. In a browser the Edit form opened with the values and an empty key, a change to the
  active profile asked first, the active profile's deletion showed the service's refusal, and
  a confirmed deletion removed the card. No log held a key.

### Phase 7 as built (2026-11-18)

- **What "not usable" is for the active profile** (T065). A saved profile: its key cannot be
  read on this instance, or the model factory cannot build a model from its values. The
  environment's: its provider is one of the three and that provider's key is not set, or no
  model can be built from the setting (FR-011a). Either way the manager records the reason,
  builds the orchestrator around the placeholder model, and the service starts.
- **The environment's model is still built as it was, key or no key** (T065). Whether the
  result may be used is decided after the build. Deciding before it, and skipping the build
  when the key is missing, made one earlier test fail (`tests/test_factory.py`, which builds
  the environment's model with no key set and a stand-in for the client). Building first
  keeps an environment-only deployment on the path it took, and every earlier test passes
  unchanged, as FR-023 asks.
- **A run is turned away in `run_stream`, before the graph** (T066): `start` naming the active
  profile, then `error` with `no_usable_model`. The thread's input is never given to the
  graph, so nothing is added to the thread, an outcome carried by the run is not counted as
  delivered, and a proposal a resume was for stays pending. The lease is released as for any
  run. `no_usable_model` is the stream's code, as planned, and is not one of the HTTP
  refusals.
- **A refused run does not count as a failed background run** (T067). `no_usable_model` is
  not among the codes a background run is retried for, so even a run that slips in while the
  model becomes unusable uses up no attempt and clears no retry.
- **The worker starts no delivery while no usable model is active** (T067). It asks once per
  round, only when an outcome is waiting, and brings this instance in step with the active
  profile by the same single-row read a run does. It says once that it is holding back, and
  once when it delivers again.
- **What is logged** (FR-024). At startup, every profile that is not usable, the
  environment's included, by name with the reason. Each refused run, with the profile and the
  reason. A profile that becomes the active one later and is not usable on this instance is
  logged when this instance takes it up. No line holds a key: a build error is recorded as
  "no model can be built from its values", not as the error's own text.
- **The reason helpers moved** to `llm/active.py` (`UNUSABLE`, `unusable_reason`), where the
  manager, the routes, and startup all use them.
- **End-to-end** (T067a). Two tests in `web/e2e/llm.spec.ts`: the notice while the active
  profile is not usable, gone once the environment's is activated, and a console that opens
  on a service with no key in its environment. The refusal of activating a profile that is
  not usable already had its test in `web/e2e/profiles.spec.ts` since Phase 4. No web code
  changed in this phase: the notice, the "Not usable" mark, and its reason were built in
  Phases 3 and 4.
- **The checkpoint** was run by hand against the real service on a database made for it and
  dropped afterwards, on a port of its own. Started with a secret that cannot read the active
  profile's key: it started, the health check answered `ok`, the list said no usable model
  is active with the profile still the active one and the environment's not, a run ended with
  `start` and `no_usable_model` and left no thread, activating the unusable profile again was
  refused, and after the environment's profile was activated the same message completed on
  the real model with no restart. Started with no provider key in the environment: it
  started, the environment's profile was `not_configured`, a run was turned away, and the
  console in a browser showed the notice and both cards as not usable. The log named each
  unusable profile at startup and each refused run, and held no key.

### Phase 8 as built (2026-11-18)

- **The proof found one leak** (T068, T071). `tests/test_llm_secrets_nowhere.py` saves one
  recognizable key, drives every `/llm` route with it, refusals and all, with tests that pass
  and fail and runs that complete, pause, and fail, and then searches every response, every
  stream as it went over the wire, every thread, every log record with its traceback, and the
  stored row. One thing held the key: the `model_error` message of a run, and its log line,
  when a provider repeats the key it was sent. `run_stream` now takes the keys of the profile
  the run uses out of that text before it is logged or sent. With that fix taken out again,
  the proof fails.
- **Nothing else turned up** (T072). Every other place was already clean: the routes return
  no key and repeat no refused value since Phases 3 and 4, a test's reason is cleaned since
  Phase 5, and the logs name profiles and fields, not values.
- **Which keys are taken out of a run's error** (T071): those of the profile the run uses,
  which the manager keeps with the active model. For the environment's profile that is the
  provider's key from the environment.
- **A missing or changed secret needed no change** (T069). Its tests passed as written: with
  no secret a save is refused and the environment's profile lists, runs, and tests as before;
  under another secret the service starts, each profile is `key_unreadable`, cannot be
  activated or tested, and is usable again once its key is sent. These prove what Phases 4 to
  7 built, so they were not seen failing first.
- **No other origin is given permission, because nothing gives any** (T069, SC-012). The
  service has no cross-origin middleware, so a preflight to any `/llm` route is answered with
  no `access-control-*` header, and so is the request itself. Every route that takes a body
  refuses one sent as text or as a form, which are the bodies a page from another origin can
  send without asking. Seven routes and three content types are held in place by tests.
- **The Edit form asks for the key again when the stored one cannot be read** (T073). The card
  has said why a profile is not usable since Phase 3. Its form now says that the key has to
  be entered again, does not save or test without it, since there is no stored key to keep,
  and no longer offers the stored key for a test.
- **No type the web app receives has a key field** (T070). The check is in the types: a
  field with "key" in its name, other than `key_hint`, in anything the service sends would
  not compile. It is checked when the app is built, which the end-to-end job does.
- **An open form's own field is the one place a page holds a key** (T073a). The end-to-end
  tests search the page's markup and the browser's storage. While the form is open the key is
  the value of its password field and nowhere else, in no text; once the form is closed or
  sent the page holds it nowhere, and storage and cookies are empty throughout.
- **The checkpoint** was run by hand against the real service on a database made for it and
  dropped afterwards, on a port of its own, with the stand-in on loopback: the key was in no
  response; the stored rows began with `gAAAA` and did not hold it; a run and a test on a
  profile whose endpoint repeats the key gave `<key>` in its place; a preflight from another
  origin got no permission and a body sent as text was refused; with no secret the saved
  profiles were listed as not usable, a save was refused, and a run on the environment's real
  model completed; with the secret back they were usable; with a secret that is not a key the
  service did not start and named the setting without its value. No log held a key.

### Phase 9 as built (2026-11-18)

- **The documents** (T074 to T077). `README.md` has two new sections, LLM profiles and Web
  console, and names `profile` and `no_usable_model` where it describes a run's stream; its
  settings table has the two new settings and the web app's variable. `AGENTS.md` was kept
  current phase by phase, so this phase only added the two settings to its `config.py` entry
  and took out "in progress". 001's stream contract names `start.profile`, the
  `no_usable_model` code, and that a `model_error` message has the profile's key replaced.
  The trees above list what was built, `tests/test_llm_providers.py`,
  `tests/test_llm_schemas.py`, and `tests/test_llm_secrets_nowhere.py` among it.
- **What T005, T007, T009, and T037 found** is under Phase 1 as built (SWR's peer range, the
  shadcn components and their lint rules, when the rewrite's destination is read) and Phase 3
  as built (no hydration warning, the browser check).
- **The stand-in needs the service's env file** (T079). `python -m tests.llm_stub` imports the
  provider table, and with it the service's settings, so without `--env-file .env` it stops on
  the missing database setting. `AGENTS.md` already had the right command; the quickstart,
  research R20, and the module's own text did not, and now do.
- **The error for a secret that is not a key names the setting** (T079): `secret_key`, as
  pydantic reports a field of `Settings`, not the variable `FREYJA_SECRET_KEY`. The value is
  not repeated. The quickstart, [contracts/settings.md](contracts/settings.md), and the README
  now say "the setting".
- **Two steps of the quickstart were worded for a profile that is not active** (T079). In
  section 4 `Stub` is the active profile, so the console asks for confirmation before each of
  the three saves, not only the last, as FR-010 requires; in section 5, with the secret
  removed, the console also says that no usable model is active. The quickstart says both now.
- **The gates** (T078). Ruff check and format, `uv run pytest` (724 passed, 56 skipped), the
  Postgres tests (56 passed), `pnpm lint`, `pnpm test` (138), `pnpm build`, `pnpm test:e2e`
  (84 in Chromium and Firefox), and `uv run pre-commit run --all-files` pass. The feature
  added no Ruff suppression and no `biome-ignore`. Without network: both default suites were
  run with every connection and name lookup outside loopback refused and counted, in Python
  by replacing `socket.connect` and `getaddrinfo` and in Node by replacing
  `net.Socket.connect` and `dns.lookup`; both passed with no attempt counted, and a probe
  showed that each guard does count one. A network namespace was not available on the
  machine, so the interface itself was not taken down.
- **The quickstart** (T079) was run in full against the real service on a database made for
  it and dropped afterwards, the stand-in on loopback, and the built web app in Chromium:
  sections 1 to 6 as written, with the two corrections above, and section 7, the whole 001
  quickstart on the environment's real model with no saved profile, where every step behaved
  as written there and each `start` named the environment's profile.
- **Fifty profiles** (T079, SC-008). With 50 saved through the API, a 51st was refused with
  `profile_limit`, the list answered in 3 ms, and the LLM section showed its 51 cards 0.15 to
  0.18 s after navigation in Chromium and 0.16 to 0.37 s in Firefox, five loads each, on the
  built app served by `next start`. The bound is two seconds.
- **The Constitution Check was run again against the code** (T080), constitution 1.4.0:

  | Principle | Result | What the code shows |
  |---|---|---|
  | I. Human approval | PASS | No tool was added or reclassified; `agent/catalog.py` and the gate are untouched by this feature. A rebuild goes through `build_agent` with the same tools. In the quickstart a proposal paused before a restart was decided after it, and a run refused for lack of a model left its proposal pending (`test_llm_no_model.py`). |
  | II. Protocol-based delegation | PASS | No delegate and no agent framework was added. The model clients are the three already installed; `cryptography` is the one dependency declared, and it was in the lock. |
  | III. One runtime | PASS | `build_agent` is still the only place the orchestrator is assembled, and `ModelManager` calls it; `run_stream` is still the only run loop. The web app has no Route Handler, no Server Action, and no credential: it calls `/api/...` on its own origin and `web/next.config.ts` forwards it. The tests swap one seam, `model_factory`. |
  | IV. Configuration through settings | PASS | The two settings are read through `Settings` and documented in `.env.example`; `FREYJA_API_URL` is read in `web/next.config.ts` only and documented in `web/.env.example`. Profiles are kept in the database and changed only through the API. A key is stored as a Fernet token under `FREYJA_SECRET_KEY` and is returned, logged, and written to a thread nowhere, which `test_llm_secrets_nowhere.py` searches for. No address or model id is hardcoded outside tests. |
  | V. Automated quality gates | PASS | Every gate above passes; every requirement has a test (the map above); no suppression was added; the `S` and `ASYNC` rule sets stay on, and the test's time limit is `asyncio.timeout`. The end-to-end tests are an addition the constitution does not require. |
  | VI. Deterministic guardrails | PASS | Whether a run may call a model is a fixed check in `run_stream` before the graph is invoked. One active profile is the database's single row and foreign key. Key removal is one function, `redact`. None of the three asks a model. |
  | Technology constraints | PASS | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; the same Postgres pool; Ruff only. The web app is Next.js with TypeScript, pnpm, Biome, and Vitest. |

  No deviation, and nothing new for Complexity Tracking.

### Phase 10 as built (2026-11-18)

The converge step read the code against the spec again and found fourteen gaps (T081 to
T094). What closing them settled:

- **A model is guarded where its errors are raised** (T081, T082). `run_stream` cleaned what it
  logged and sent, but LangGraph is given a failed step's error first: it shows it in a
  `debug` event and saves it with the step's writes in the checkpointer. `llm/guard.py` makes
  the model itself raise without the key: `guard_model` gives the model a subclass of its own
  class that catches what `_generate`, `_agenerate`, `_stream`, and `_astream` raise, and
  raises a `ModelError` with the same words and `<key>` for the key, outside the handler so
  that the provider's error is not kept as its context. An error that holds no key is raised
  as it came, so nothing changes for it, such as the handling of a context that is too long.
  The model stays the same object under the same class name, so it is bound to tools and
  recognized by its provider as before; a wrapper around the model would have lost that. A
  middleware was the other choice and was set aside: it would see only the calls made
  through it, and Deep Agents' summarizing call is made by a middleware of its own.
  `ModelManager` guards every model it builds. The proof now streams the failing run with `debug` and searches
  everything the checkpointer holds, and an opt-in test searches `checkpoint_writes` in
  Postgres; each fails without the guard.
- **A page of another origin is refused by what the browser says of the request** (T083). No
  permission for other origins stops a page from reading an answer and from sending JSON. It
  does not stop a form post, and two routes take no body. `refuse_other_origins`, on the
  whole `/llm` router, refuses POST, PATCH, and DELETE with 403 and the new code
  `other_origin` when `Sec-Fetch-Site` is anything but `same-origin` or `none`, or when
  `Origin` is not the host the browser asked for: `X-Forwarded-Host` when the web app's
  server passed the request on, which Next.js sets, and `Host` otherwise. A page cannot set
  any of the three. A client that sends none, such as curl, is served, so every documented
  command works as before. A check of the content type was the other choice: it would have
  made every curl command for the two routes need a header. This does not cover DNS
  rebinding, where the hostile page's origin and the host it asked for are the same name;
  FR-003a does not say whether it should.
- **The card shows the newer of its result and the list's** (T084). The card kept its own
  result while the profile's `updated_at` stood still, and a test does not move it. The card
  now gives way when the list reports a result at least as new, and, for a result the
  service kept, as soon as the list has been read again, so a result the service no longer
  has is not shown. The list counts its readings and tells the cards.
- **Confirming a change to the active profile takes a press of its own** (T085). Save and
  Confirm were one button in place. Confirm is now a button of its own, not the form's, so
  Enter in a field does not confirm; it ignores a press that the browser counts as the second
  of a double press (`detail` above 1); and the notice takes the focus, so a key pressed again
  lands on it.
- **A profile that is gone goes from the list** (T086). A `profile_not_found` refusal of an
  activation, a change, a deletion, or a test now has the list read again, while the dialog
  keeps showing the refusal.
- **The confirmations scroll in themselves** (T087), as the form does: after a failed "Test
  first" with a reason of 500 characters the activation dialog was 880 pixels tall and, in a
  window 360 by 560, its buttons could not be reached.
- **The subagent's model is proved** (T088) by a run that hands work to it after the profile
  changed: the subagent is asked through the model the run started with, and the next run's
  through the new one. The code was right; the test passed as written.
- **The providers' variables are read in one place** (T089): `environment_key` in
  `llm/providers.py`. The Constitution Check above records why they are not settings, and R3
  no longer calls the read "display only".
- **A saved active profile that cannot be built is listed as not usable** (T090), with
  `cannot_build`, on the instance that tried. No values of the three providers are known to
  fail to build; the test uses a factory that raises.
- **An answer that is an error is not called a silence** (T091). `web/lib/api.ts` says "cannot
  be reached" for a request that failed and for a server error with no JSON body, which is
  what the web app's server answers while the service is down, and "answered with an error"
  for anything else, with the service's words when it gave any.
- **Three tests were added for code that had none** (T092): the form's own limit on a name, a
  refused value the form has no field for, and the environment's wording in the activation
  warning.
- **Two documents now say what the code does** (T093, T094): FR-021 states that a key shorter
  than eight characters is not searched for, which R9 had chosen, and FR-023 and SC-006 name
  the three assertions of feature 003 that Phase 3 changed from `"9877"` to `":9877"`,
  because a run id can hold those digits.
- **The checkpoint** was run against the real service on a database made for it and dropped
  afterwards, with the stand-in on loopback and the built web app in Chromium. Adding,
  activating, testing, changing, and deleting from the console all passed through the web
  app's server with the new refusal in place. A fetch and a form post from another origin
  were answered with `other_origin`, and the active profile did not change. On a profile
  whose endpoint repeats the key, a run with the debug stream held the key nowhere: not in
  the stream, not in `checkpoint_writes`, not in the log; each showed `<key>`. A run and a
  test on the environment's real model went through the guarded client as before.
- **Looked at and left** by the converge step: whether a browser's password manager saves or
  fills the key field, which needs a run by hand; announcing field errors to assistive
  technology; and DNS rebinding.

### Phase 11 as built (2026-11-18)

A second converge run read the Phase 10 changes with a reviewer that did not write them. All
fourteen held, and the three security fixes withstood an attempt to break them. Nine gaps
remained (T095 to T103), one of them in code no earlier phase had questioned:

- **A saved profile with no base URL goes to its provider's own address** (T095). R3 had
  verified that a client given a key and a base URL reads nothing from the environment. It
  had not looked at a key with no base URL: the client then takes its address from the
  environment's variables, so on a deployment that points `OPENAI_API_BASE` at a self-hosted
  endpoint, a profile saved for the provider's own service sent its key and its
  conversations to that endpoint, while its card showed no base URL. The provider table now
  holds each provider's own address, and `model_arguments` passes it for a spec that has a
  key and no base URL. The environment's own model, which has no key in its spec, is built
  as it always was. The Constitution Check above records why those three addresses are not
  hardcoded configuration.
- **The environment's card shows the address its client uses** (T097): the first that is set
  of the variables the client reads, `OPENAI_BASE_URL` and `ANTHROPIC_BASE_URL` included.
- **The guard on the environment's model is proved in the default suite** (T096). The test of
  the environment's key now streams its failing run with `debug` and searches the
  checkpointer; it fails when that model is left unguarded.
- **The service's list of codes has `other_origin`** (T098), and a test reads the contract's
  list and the service's together, so neither gains a code alone.
- **A page of another origin cannot start or resume a run** (T099): held in place by tests for
  a body sent as text, as a form, and with no content type, which until now rested on a
  FastAPI default. The behaviour was already so; the tests passed as written.
- **The list brings its instance in step first** (T100): `profile_list` takes up the active
  choice as a run would before it answers, so an active profile that cannot be built is
  listed as not usable on every instance, before a run is turned away there. Building opens
  no connection, and a rebuild is the same one the next run would have made.
- **A card notes the list's reading when its result arrives** (T101), from a ref, so a list
  read while the test runs is not taken for a later one.
- **The dialogs are given the profile as the list has it now** (T102), found by its id: a
  test that fails after the activation dialog opened brings the warning, and a profile that
  became the active one while its form was open is saved only after the confirmation. What
  was typed in the form stays, since the fields keep their own state.
- **Three documents were brought in line** (T103): `why` in the data model's "Model in use"
  and each provider's own address in its table; `llm/guard.py` and `tests/test_llm_guard.py`
  in the trees above; and a 403 `other_origin` answer on each of the six operations of the
  contract that change something.
- **The checkpoint** was run against the real service on a database made for it and dropped
  afterwards, started with `OPENAI_API_BASE` pointing at the stand-in. In that environment a
  saved profile with no base URL is built for `https://api.openai.com/v1`, and one with a
  base URL for that base URL; this was read from the built client, and no request was sent
  to the provider. The console's changes went through the web app's server as before, and a
  second instance listed the newly active profile, and had taken it up, before any run.
