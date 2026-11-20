# Implementation Plan: Tasks Section of the Web Console

**Branch**: `007-web-tasks` | **Date**: 2026-11-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/007-web-tasks/spec.md`

## Summary

Give the console a Tasks section: a person enters a task for Freyja, watches it run, decides
the actions that need approval, and reads the result. A task is a thread of the service, and
the conversation, the proposal, and the decisions are the thread routes that exist. The
service gains what the console lacks: a list of threads with a status, a run that goes on when
its reader leaves, and a stop.

Three things were not proven when the spec was written. The research settled each before
anything was designed on it:

- **A stream through the console's forwarding** does not arrive as it is produced with the
  answer the service gives today: Next.js compresses it and holds it back to the end, in
  Chromium and in Firefox. It does arrive event by event when the answer says
  `Cache-Control: no-transform`, and the forwarding ends a stream that is silent for 30
  seconds unless a ping keeps it open. The new routes send that header and a ping every 10
  seconds ([research.md](research.md) R1).
- **A stop after the step under way** is possible. A run started for the console is run in
  segments that end before each model call and each tool step, by LangGraph's own pause
  points, and a stop is looked for only between segments. A segmented run was shown to leave
  the same thread as a plain one (R4).
- **No file of the session building features 005 and 006 is changed.** `api/streaming.py` is
  untouched: the run is driven from outside it, and the graph it calls is handed to it
  wrapped (R2, R3, R16).

On the service: one table, `freyja_threads`, kept through the run lease, which every run
takes and gives back; five routes under `/threads` in a router of their own; and two objects
of the runtime, `UnattendedRuns` and `SegmentedGraph`. On the web: `web/app/tasks/`,
`web/components/tasks/`, `web/lib/tasks.ts`, and a store of the runs this browser tab started,
so that a run begun from the list's page is shown on the task's page as it arrives.

Design details are in [research.md](research.md); the entities and the table in
[data-model.md](data-model.md); the routes in [contracts/openapi.yaml](contracts/openapi.yaml);
the stream of the new routes in [contracts/sse-events.md](contracts/sse-events.md); the manual
walk-through in [quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 for the service (`.python-version`), managed by uv with the
committed `uv.lock`, src layout. TypeScript 5 on Node 20.9 or newer for the web app, managed
by pnpm with the committed `web/pnpm-lock.yaml`.

**Primary Dependencies**: unchanged. The service: deepagents 0.7.32 on langgraph 1.2.18,
fastapi 0.162.0, sse-starlette 3.6.0, psycopg with its pool. The web app: Next.js 16.4.6 with
the App Router and Cache Components, React 19.3, swr 2.5.1, Tailwind CSS 4, shadcn/ui. No
dependency is added to either lock file: the stream is read with `fetch` and a parser of the
feature's own (R11).

**Storage**: one new table in the existing Postgres database, `freyja_threads`, created at
startup with `CREATE TABLE IF NOT EXISTS` like the run lease and the other stores
([data-model.md](data-model.md)). An in-memory implementation of the same protocol serves the
suite. The web app stores nothing; a run's events are held in the page's memory while the tab
is open.

**Testing**: pytest 9 with pytest-asyncio, the scripted chat model, in-memory persistence,
and the stand-in agent, no network. The Postgres index has opt-in tests under
`tests/integration/`, run against a database of this feature's own. Vitest with jsdom for the
web app's units; Playwright in Chromium and Firefox for each user story's console part (R15).

**Target Platform**: a Linux server for the service, bound to loopback, and a current desktop
browser for the console, as in features 004 and 005.

**Project Type**: a web service and the web app that is its client.

**Performance Goals**: the list within two seconds with 100 threads (SC-009): one query on
one table, plus one read of a thread's state for each run that ended since the list was last
asked for (R6). The first event of a run within one second, as today (001 SC-001).

**Constraints**: nothing of the other session's files changes (R16). No new setting, no new
dependency, no change to a CI file, `pyproject.toml`, `web/vitest.config.mts`,
`.gitleaks.toml`, or `web/playwright.config.ts`. The existing thread routes behave as before
for a client that is not a web page (FR-027). Coverage floors: 88% of the service's lines and
90% of each web measure.

**Scale/Scope**: a console for the people who run one Freyja deployment. 100 rows in the
list; each run bounded by `FREYJA_MAX_STEPS`; no limit on how many run at once (spec,
Assumptions). About five new service modules, a dozen web components, and eight test files.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against constitution 1.5.0.

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | Yes, central | No tool is added or reclassified and the gate is not changed. A decision from the console goes through the existing `resume_run` and its validation: the new route calls that function and adds nothing to it (R3). A run without a reader pauses exactly as one with a reader, because the pause is inside `run_stream` and the graph, which are not changed; it stays resumable after any delay, since the proposal is in the thread as before. A stop is applied only between steps, so it never cuts off an action a person approved and never causes one to run twice (R4); an approved action that had not started when the stop was applied does not start. The page offers the service's three decisions, only those the service lists for an action, and has no way to decide in advance or by itself (R14). | PASS | PASS |
| II. Protocol-based delegation | No | No delegate is added or reached. Agent tasks are read from the store that exists, and shown. | PASS | PASS |
| III. One runtime, every environment | Yes, central | `build_agent` stays the only assembly point and `run_stream` the only run loop: an unattended run is the same generator, driven by a task where a response drove it (R2). `SegmentedGraph` wraps the graph `build_agent` made and calls its `astream`; it builds nothing (R4). The web app is a client of the API only: it holds no credential, calls no model, agent, or tool, has no Server Action or Route Handler, and reads a task's status from the service and works none out (R7, R11). Tests run the production routes through the test client. | PASS | PASS |
| IV. Configuration through settings | Yes | No setting is added, on the service or in the web app. The limits this feature fixes (100 rows, 200 characters, the ping every 10 seconds, the pages' 5 and 15 seconds) are the specification's, not a deployment's, and are constants beside the code that applies them. Nothing an operator manages is added: a task is a thread, which is data, not configuration. | PASS | PASS |
| V. Automated quality gates | Yes | Every requirement has a test (map below). The default suites need no network, database, model, or agent. Store calls are async; SQL is composed with psycopg's `sql` module and carries the `# nosemgrep` mark `AGENTS.md` describes. The web app passes `biome check` and its Vitest suite with coverage. No Ruff suppression is planned. One thing the gates do not measure is said where it applies: CI does not run the Postgres tests, so the Postgres index counts against the coverage number. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes | Where a run may be stopped is a fixed rule of the run loop's wrapper, the two node names, checked at startup; no model is asked whether a step may be cut. A task's status is a fixed order over recorded facts (R7). The refusal of other origins is a fixed check before any route's work (R9). | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; streaming over server-sent events through sse-starlette; the Postgres pool; Ruff only. The web app stays Next.js with TypeScript, pnpm, Biome, and Vitest. | PASS | PASS |

No deviation from the constitution. Three additions carry complexity the governance section
asks to justify; none is a violation, so they are justified here and not in Complexity
Tracking:

- **A graph that is run in segments.** Until now a run was one call of `astream`. The owner
  chose a Stop that never cuts off an action (spec, FR-022), and the only place a run can be
  ended cleanly is the graph's own pause between steps (R4). The wrapper is used for runs
  started through the new routes and passes every other run straight through.
- **The run lease is wrapped to keep the index.** The lease is the one place every run
  passes, whoever starts it, so it is where the list learns of runs the console did not
  start, without changing the routes or the worker (R6). The wrapper's own writes can fail
  without failing the lease.
- **A route that calls another route's handler** and takes the generator out of its
  response (R3). It keeps one copy of the checks that decide what a decision may do, in a
  file this feature must not rewrite. A test pins the one attribute it relies on.

### Changes to the spec

The research found three places where the spec, as written before it, asked for more than can
be known or shown. The spec is changed in this pull request, and each change is named in its
Clarifications under "Plan, 2026-11-20":

- **FR-003, a failed run's reason.** How a run ended is known only to whoever read its
  stream. For a run started through the new routes that is the service itself, so the list
  gives the reason. A run another client or the service's own worker started, and that
  failed, is listed as stopped (R7).
- **FR-002, when a task last changed.** While a run is in progress it is when that run
  started; the time of the last recorded step is read when the run has ended (R7).
- **FR-028, the proof that a stream arrives as it is produced.** It was made by hand in both
  browsers (R1). The end-to-end tests cannot repeat it without a change to
  `web/playwright.config.ts`, which this plan does not make; service tests pin the header and
  the ping it rests on (R15).

Two edge cases are added: a stop that is applied before an approved action started, and the
step limit of a run that goes on without a reader (R4).

### Checklist review (2026-11-20)

[checklists/approval-runs.md](checklists/approval-runs.md) was reviewed against the spec and
this plan; its "Review record" lists 20 findings and what each changed. One changes the
design: when the service is stopped in the ordinary way, a run nobody reads is no longer
cancelled. It is asked to stop at its next boundary and waited for, so stopping the service
cuts no action off ([research.md](research.md) R17). Four add behavior a client sees: a
thread the index does not know gets its row when it is opened; a reader that does not keep up
with a stream is disconnected; a list that cannot be read is an error; and a stop that comes
too late is said to have come too late. The Constitution Check is unchanged by them:
principle I is better served by the first.

## Project Structure

### Documentation (this feature)

```text
specs/007-web-tasks/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── openapi.yaml     # The five new routes
│   └── sse-events.md    # The stream of the two new run routes
├── checklists/
│   └── requirements.md  # From specify and clarify
└── tasks.md             # /speckit-tasks; not written by this step
```

### Source Code (repository root)

New files are this feature's own. Files marked *shared* get a small addition in a block of its
own (R16); no other existing file changes.

```text
src/freyja_agent/
├── runtime/
│   ├── thread_index.py          # new: ThreadRecord, ThreadIndex, InMemoryThreadIndex,
│   │                            #      PostgresThreadIndex (freyja_threads), IndexedRunLocks
│   ├── unattended.py            # new: UnattendedRuns (drives a run without a reader, hands
│   │                            #      its events to readers, applies a stop) and SegmentedGraph
│   └── runtime.py               # shared: two fields; assemble, open, begin_run
├── api/
│   ├── thread_index.py          # new: the router, GET /threads, GET /threads/{id},
│   │                            #      the two unattended routes, POST /threads/{id}/stop
│   ├── thread_index_schemas.py  # new: ThreadSummary, ThreadList, StopAnswer
│   ├── threads.py               # shared: the router's dependencies, one line
│   └── app.py                   # shared: one router

tests/
├── test_thread_index.py             # the store and the lease wrapper
├── test_thread_index_api.py         # the list and the row, every status
├── test_unattended_runs.py          # run and resume without a reader, beside the existing routes
├── test_unattended_stop.py          # the stop and the step limit
├── test_segmented_graph.py          # a segmented run leaves what a plain one leaves
├── test_threads_other_origins.py    # every thread route, old and new
├── test_thread_index_nowhere.py     # no key and no address in the new answers
└── integration/test_thread_index_postgres.py   # opt-in

web/
├── app/tasks/
│   ├── page.tsx                 # the list
│   └── [id]/page.tsx            # one task
├── components/tasks/            # task-list, new-task, task-view, message, proposal-card,
│   │                            # agent-tasks, follow-up, stop-button, status
│   └── ...
├── lib/
│   ├── tasks.ts                 # types, the calls, the stream parser, THREADS_KEY
│   ├── task-runs.ts             # the runs this tab started
│   └── api.ts                   # shared: the stream function
├── components/app-sidebar.tsx   # shared: the Tasks entry and its number
├── __tests__/                   # lib/tasks.test.ts, lib/task-runs.test.ts,
│                                # components/tasks/*.test.tsx, app/tasks/*.test.tsx;
│                                # shared: components/app-sidebar.test.tsx, lib/api.test.ts
└── e2e/
    ├── tasks-api.ts             # what the service would answer, for these specs
    ├── tasks.spec.ts            # the list, the sidebar's number, a new task
    ├── tasks-decide.spec.ts     # the proposal card
    ├── tasks-run.spec.ts        # follow-ups, agent tasks, leaving, coming back, Stop
    └── console.spec.ts          # shared: the sidebar lists three sections

README.md, AGENTS.md, web/README.md   # shared: paragraphs of this feature's own
```

**Structure Decision**: the layout the repository has. Service code under
`src/freyja_agent/` by layer, with this feature's stores and run objects under `runtime/` and
its routes under `api/`; tests in `tests/`; the web app under `web/` with tests under
`web/__tests__/` following the code's paths and end-to-end specs under `web/e2e/`.

The service's files are named for what the API calls the thing, a thread. The owner's brief
suggested `tests/test_tasks_*.py`; the tests here follow the modules they cover instead, and
none of their names begins like a file of the other session. The web files are named
`tasks`, the console's word.

### Phases

The order the tasks will follow. Each phase is a pull request.

1. **The index.** `thread_index.py`, the lease wrapper in the runtime, `GET /threads` and
   `GET /threads/{id}`, and the refusal of other origins on every thread route. Nothing
   changes for a client: the list shows the runs that happen.
2. **Runs without a reader, and the stop.** `unattended.py`, the two unattended routes, the
   stop route, and the search for keys and addresses.
3. **A task, start to result** (user story 1): the sidebar entry, `web/lib/tasks.ts`, the
   stream function, the run store, New task, and a task's page with its conversation.
4. **Deciding** (user story 2): the proposal card.
5. **The list and the number** (user story 3).
6. **Continuing** (user story 4): follow-ups, agent tasks, and following a task by reading.
7. **Leaving, coming back, and Stop** (user story 5), with user story 6's end-to-end test and
   the documentation.

### Requirement to test map

| Requirements | Where they are tested |
|---|---|
| FR-001, FR-001a, FR-006 | `app-sidebar.test.tsx`, `tasks.spec.ts`, `console.spec.ts` |
| FR-002 to FR-005, FR-023 | `test_thread_index.py`, `test_thread_index_api.py`, `task-list.test.tsx`, `tasks.spec.ts` |
| FR-007, FR-008 | `new-task.test.tsx`, `task-runs.test.ts`, `tasks.spec.ts` |
| FR-009 to FR-013 | `task-view.test.tsx`, `message.test.tsx`, `tasks.test.ts`, `tasks-run.spec.ts` |
| FR-014 to FR-019 | `proposal-card.test.tsx`, `tasks-decide.spec.ts`; the service's side by the existing `test_gate.py`, through the unattended resume in `test_unattended_runs.py` |
| FR-020 | `follow-up.test.tsx`, `tasks-run.spec.ts` |
| FR-021 | `test_unattended_runs.py`, `test_segmented_graph.py`, `tasks-run.spec.ts` |
| FR-022, FR-022a | `test_unattended_stop.py`, `stop-button.test.tsx`, `tasks-run.spec.ts` |
| FR-024 | `test_threads_other_origins.py`, `tasks-run.spec.ts` |
| FR-025 | `test_thread_index_nowhere.py` |
| FR-026, FR-026a | `tasks.test.ts`, `task-list.test.tsx`; no credential and no call but `/api` by `api.test.ts` |
| FR-027 | the existing suite, unchanged; `test_segmented_graph.py` for a run that is passed through |
| FR-028 | the map above; the header and the ping in `test_unattended_runs.py` |

## Complexity Tracking

No violation of the constitution to justify. The three additions that carry complexity are
justified under Constitution Check.
