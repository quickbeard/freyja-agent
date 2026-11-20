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
| IV. Configuration through settings | Yes | No setting is added, on the service or in the web app, and nothing an operator manages: a task is a thread, which is data, not configuration. Seven numbers are fixed by the specification and are constants beside the code that applies them, as feature 005 fixed its own (twenty agents, 2,000 characters of a card's text, four card reads at once): 100 rows in the list, 200 characters of a first message, 2,000 characters of a failed run's reason, and the pages' 5 and 15 seconds are what a person meets, the same on every deployment. Two are not about what a person meets and are the ones a deployment could want to change: the ping every 10 seconds, which follows from the 30 seconds of the console's own forwarding and not from where the service runs, and the 1,000 events held for a reader that does not keep up, which bounds memory per reader. Both are kept as constants because nothing a deployment configures today changes what they rest on; making them `FREYJA_*` settings is the owner's call and is named in the analysis pull request. | PASS | PASS |
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
cuts no action off ([research.md](research.md) R17). Five add behavior a client sees: a
stream that ends without its last event is not a failed run; a thread the index does not
know gets its row when it is opened; a reader that does not keep up with a stream is
disconnected; a list that cannot be read is an error; and a stop that comes too late is said
to have come too late. No principle's verdict changes: principle I is better served by the
first, and the row for principle IV was brought up to date in the analysis below.

### Analysis (2026-11-20)

`/speckit-analyze` was run after the tasks were written. The artifacts were also read by a
reviewer that had not written them, the first such reading of this feature; each of its 24
findings was checked against the files, and one against a run, before anything was changed.
None was critical. 22 were fixed in this pull request, 1 was fixed in part, and 1 was not
taken up, with the reason.

| # | Severity | Finding | What changed |
|---|---|---|---|
| H1 | High | A new task sent while no usable model is active: the spec keeps the text, the design had already left the box, and the thread has no state, so the page had both an error and "not found" to show. | Research R12: the run store keeps the message, and the task's page shows the error with the text to send again; the error wins over "not found". FR-008; tasks T033, T034, T040, T043. |
| H2 | High | The Constitution Check's row for principle IV named four numbers; the documents fix seven, and a cap of 50 stood in one task only. | The row names all seven and says which two a deployment could want to change. The cap is gone (M2). The spec's list of limits gained three. |
| M1 | Medium | FR-027 let existing tests only gain setup, while the sidebar's test asserts two sections and this feature adds a third. `console.spec.ts` counts nothing. | FR-027 names the sidebar's tests; T043 says what it adds to `console.spec.ts`. |
| M2 | Medium | T015 settled at most 50 rows in one request. A row beyond them had no status and was left out of the count. | Every ended row is settled before the list is answered; R6 says what that costs after a long silence. T006 has the case. |
| M3 | Medium | Nothing said where the unattended route learns the run's lease owner; the handler makes it and does not return it. | R3 and the data model: from the `start` event's `run_id`; the mark of a console run needs none. T027, T029. |
| M4 | Medium | "Keeping the list never fails or changes a run" was specified for the lease wrapper only. | T027 and T029 say what a failing index does in the mark, the note, and the read of a stop; T021 and T022 test it. |
| M5 | Medium | Phase 6 was called independent of Phase 5, and its end-to-end task decides a proposal. | The dependency section says which tasks of Phase 6 are independent. |
| M6 | Medium | The check that existing end-to-end specs still pass ran in Phase 4, two phases before any page asks for the list. | Moved to T057; R15 corrected. |
| M7 | Medium | The sidebar's refresh every 15 seconds from other sections was in the research and in no task. | T053 and T056; R13. |
| M8 | Medium | `running` was defined two ways: by the lease, and by the lease and the row's owner. | The lease alone, in the data model, with what a row is when it is opened during a run and when a run the index did not see start ends. |
| M9 | Medium | The quickstart names the shared test database and the owner's `.env`, where the tasks require this feature's own. | Not taken up. The quickstart is the committed guide for any developer and uses the names `AGENTS.md` documents. The names of this machine belong to the tasks, which have them (T002, T073). |
| M10 | Medium | `[P]` stood on tasks that share a file, and T003 pointed at a part of T007 that does not exist. | `[P]` removed from T004, T005, T021, and T023; the reference fixed. |
| M11 | Medium | The refusal of another origin was tested for the stop in Phase 3, and for the two run routes only in Phase 9. | T020 tests both run routes in Phase 3; T070 stays the full matrix. |
| M12 | Medium | Either the README is right that an ordinary stop waits for a run with a reader, or the library ends streams at a stop; the checklist review assumed the first. | A run settled it: the stream is cancelled at once. R17, the spec, and the checklist's record are corrected. The decision stands. T030 corrects the README's sentence. |
| L1 | Low | The plan's seven phases, its map, and its list of shared files no longer matched the tasks. | "Phases" and the map below. |
| C1 | Low | No web task showed an id the service does not accept as not found. | T034, T043. |
| C2 | Low | No task tested keyboard use, which FR-012a requires. | T050, T068. |
| C3 | Low | SC-001's and SC-009's two seconds are measured by no task. | Fixed in part: T006 lists 100 rows, T034 shows a run as started at its first event, and T073 checks both timings by hand. No timed test: it would measure the machine. |
| C4 | Low | The stop's tests missed two of three ways a stop comes too late and the lower bound of the step limit; T029 named no log line. | T019, T022, T029. |
| S1 | Low | Three sentences still stated FR-004 as it was before a thread could be opened into the list. | Spec, data model, contract. |
| S2 | Low | R8 said "at most 100 rows". | R8. |
| S3 | Low | "Four" where five were listed, twice; R5 gave no 404 for the stop; `stopped` was tied to a client's stop only. | Plan, stream contract, R5. |
| T1 | Low | "Settled as stopped" where `settled` has other values; `not_stoppable` described by who reads the stream. | "Listed as stopped"; the contract's wording. "Console run" stays the spec's word and "unattended" the code's and the contract's; the spec's terms say they are one thing. |
| A1 | Low | Only the page that asked for a stop can say that it came too late; Phase 4's checkpoint promised a sentence Phase 6 builds. | FR-022a; the checkpoint. |

One finding of the author's own: `tests/integration/conftest.py` drops every table after a
test, so it is not changed and is no longer on the tasks' list of shared files.

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

The order the tasks follow; [tasks.md](tasks.md) numbers them 2 to 8. Its Phase 1 is setup
and changes no file, and its Phases 9 and 10 are user story 6's checks and the polish, which
this plan had folded into the last phase. Each phase that changes a file is a pull request.

1. **The index** (tasks' Phase 2). `thread_index.py`, the lease wrapper in the runtime,
   `GET /threads` and `GET /threads/{id}`, and the refusal of other origins on every thread
   route. Nothing changes for a client: the list shows the runs that happen.
2. **Runs without a reader, and the stop** (Phase 3). `unattended.py`, the two unattended
   routes, the stop route, and the search for keys and addresses.
3. **A task, start to result** (Phase 4, user story 1): the sidebar entry,
   `web/lib/tasks.ts`, the stream function, the run store, New task, and a task's page with
   its conversation.
4. **Deciding** (Phase 5, user story 2): the proposal card.
5. **The list and the number** (Phase 6, user story 3).
6. **Continuing** (Phase 7, user story 4): follow-ups, agent tasks, and following a task by
   reading.
7. **Leaving, coming back, and Stop** (Phase 8, user story 5).

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
| FR-024 | `test_threads_other_origins.py`, `test_unattended_runs.py`, `test_unattended_stop.py`; the frame rule in `tasks.spec.ts` |
| FR-025 | `test_thread_index_nowhere.py` |
| FR-026, FR-026a | `tasks.test.ts` (every call goes to a path under `/api/threads`), `api.test.ts`; the notice in `app/tasks/page.test.tsx` |
| FR-027 | the existing suite, unchanged but for the sidebar's tests; `test_segmented_graph.py` for a run that is passed through |
| FR-028 | the map above; the header and the ping in `test_unattended_runs.py` |
| SC-001, SC-009 | by hand in the quickstart walk (tasks T073); 100 rows listed in `test_thread_index_api.py`, without a timing |
| SC-006 | `test_unattended_runs.py`: a paused console run found and decided by a second runtime on the same store |

## Implementation Notes

What was built differently from the plan, phase by phase, and what a later phase should know.

### Phases 1 and 2 as built (2026-11-20)

The starting numbers (T001): 91.25% of the service's lines, with 1,437 tests passed and 108
skipped; the web app at 97.44% of statements, 95.33% of branches, 97.32% of functions, and
98.34% of lines, with 419 tests.

- **A run's note is kept until a later run records a step, not until the next run starts.**
  The data model had `noted` cleared when a lease is taken. A request that is refused after
  it took the lease, such as a resume with nothing pending, takes and gives back the lease
  without being a run; it would have wiped how the last run ended, and a thread that failed
  with no usable model would have turned to done. The row has a column the plan did not
  have, `noted_at`, written by the clock of the instance that ran the run. A note older than
  the thread's last checkpoint is dropped when the row is settled. Nothing writes a note
  before Phase 3; the column is there now because the table is made once.
- **`settle` and `remove` name the number of runs the caller saw** (`runs`), a column the
  plan did not have. That is the condition that refuses a late write, in the statement's
  `WHERE`. The first version named the owner it saw, which the review found too weak.
- **The statements are plain strings with bound parameters**, as `runtime/locks.py` has
  them, and none is composed. So no statement needs the `# nosemgrep` mark, and Semgrep
  reports nothing for the new files.
- **`running` is read from the lease for every row the list returns**, one read each
  (research R7). The in-memory lease makes that free in the suite; against Postgres it is
  up to 100 small reads for a full list.
- **A run that died with its instance is listed by what its thread's state says**, like any
  ended run: `stopped` when it left steps to take, and what the thread was before when it
  had recorded nothing. T006 has both cases; its sentence named only the first.
- **`tests/integration/conftest.py` is not changed**: it drops every table after a test.
- **The search for a key and an address (T017) finds nothing it could find yet.** The two
  routes return how a first message begins and nothing else of a thread until Phase 3 notes
  a run's error. The test is in place so that Phase 3 adds to it.
- **Not written first.** The store's tests were written before the store and could only
  fail on the import. The tests of the routes, the refusal of other origins, and the search
  were written after the code they cover.

**Read by a reviewer that had not written it**, before the pull request, as the tasks'
standing rules ask. It found one high, three medium, and five low problems. Each was checked
against the code before anything was changed, and every test added for one was shown to fail
with its fix taken out (eleven such checks, none survived).

| Finding | What was wrong | What changed |
|---|---|---|
| High | A first message with a NUL character, or a lone surrogate, cannot be written to a text column. The settle of that one row failed at every reading, and one failed row failed the whole list, for every thread, until the row was mended by hand. The in-memory store takes any string, so the suite could not see it; with the cleaning taken out, the Postgres test fails with the database's own error. | Text is cleaned before it is kept, in both stores. A row that cannot be looked at is left out of the answer, logged, and tried again at the next request. |
| Medium | A row that nobody had looked at since its run ended was shown with a status worked out from what the row held: a thread that had just paused was `done` and missing from the count for one reading. The window is one database round trip after every run's end, which is when a console reads again. | No status is shown for such a row. The routes settle it within the request, look again when a run started or ended meanwhile, and take the counts last. A settle no longer clears `run_owner`, so the late end of a run that was settled early finds its row and un-settles nothing. |
| Medium | No test noticed when a read settled the row of a run in progress, which Phase 3 depends on: a note and a stop are written for the row's owner. | The test of a run in progress asserts that its row is left alone. |
| Medium | The guard on a settle could not tell "no run since" from "a run started and ended since", so a slow reading could keep a status from before a run for good. | The row counts its runs, and a settle names the count it saw (`runs`). |
| Low | A row kept wrong, after both of the index's writes for a run failed, stayed wrong after being opened, though FR-023 says opening puts it right. | The row route reads the thread's state again each time while no run is in progress. |
| Low | The lease waited for the index without a limit, and a caller cancelled while the index was told kept a lease it did not know it held. | Five seconds at most (`INDEX_WRITE_SECONDS`), and the lease is given back before the cancellation goes on. |
| Low | Three tests were weaker than the task they ticked: a late settle was never tried with a stale reading, "the first message" was only tried on threads with one, and "refused before a lease is taken" also held for a lease taken and given back. | Each now fails for the mistake it names. Cases added: a refused request on a paused thread, a pending agent task that waits for an answer. One test was renamed for what it asserts. |
| Low | One list reading costs a lease read per row it returns, and two first readings after a long silence settle the same rows twice. | Not changed. One statement that joins the lease table would do it, at the price of reading a table that is another module's. Recorded here. |
| Low | In `AGENTS.md` the new sentences stood between two sentences that belong together, and two tasks described the store as first planned. | Both corrected. |

`INDEX_WRITE_SECONDS` is an eighth number fixed in the code. It is not a deployment's to set:
it bounds how long a run can be held up by a list it does not need.

**One existing test was changed**, `tests/test_llm_profiles_api.py`: it sent the thread
routes a body that is not JSON from another origin and expected 422. The refusal of another
origin now comes first, with 403. FR-027 names the test.

**Checked by hand**, on a check instance on port 8020 with a database of its own and the
stand-in model on 9977: an empty list; a run started with curl listed as `done` with its
first message; the row's `changed_at` equal to the state's `updated_at`; 404 for a thread
nobody ran and 422 for an id the service does not accept; `other_origin` for a request
marked as cross-site and 422 for a body sent as text; the two rows in `freyja_threads`; and
the same list after the service was restarted. The database was dropped afterwards.

### Phase 3 as built (2026-11-20)

Built as research R2 to R5 and R17 describe; the differences:

- **The step limit.** The prototype had a segmented run reach `step_limit` up to two steps
  before a plain one. With the installed LangGraph the two ended at the same step in every
  run tried (limit 9: step 9 both). The test keeps the bound the spec states: no more steps
  than a plain run, and at most two fewer.
- **The mark names its run, and is written by `UnattendedRuns`.** `mark_unattended` and
  `request_stop` take the run's owner, a change to two methods of Phase 2's store, whose
  tests were changed with them. The mark is written at the run's first event, `start`,
  which names the owner; the route hands the events over without awaiting anything first,
  so that nothing between the handler's answer and the drive can leave the lease taken.
  Until the mark is written, a stop answers `not_stoppable`.
- **The first message only at the thread's first run.** The store fills the row's first
  message from the mark only while `runs` is 1, so a follow-up's text is never kept as the
  first message. A thread from before the index, never opened, whose first run seen is a
  follow-up through the unattended route still gets that follow-up as its first message.
  Rare, and left as it is.
- **A thread is disarmed at the run's last event**, before the generator goes on to give its
  lease back, so a run that takes the lease next is never run in segments for this one.
  `close()` waits for every drive, also one disarmed and still giving its lease back.
- **One wrapper per build.** `Runtime.begin_run` reuses the `SegmentedGraph` of the graph it
  last built, so every context of one build holds one graph object, which
  `tests/test_agents_apply.py` (feature 005's) asserts. The first version made a wrapper per
  context and failed that test.
- **Where a stop is refused before a lease is taken.** The two run routes ask for the
  console's runs before they call the handler, so a runtime without them is refused before
  any lease is taken. Only a runtime put together by hand lacks them.
- **The background run of the delegation worker** is shown `not_stoppable` by taking the
  lease the way the worker does (`runtime.locks.acquire`), not by a delivery: the row is what
  decides, and holding a delivery open in a test needs the worker's internals.
- **`tests/conftest.py`**: `make_mcp_app` takes `thread_index`, for two apps on one index.
- **`tests/unattended_helpers.py`** holds what the three new test files share: a tool server
  whose tool step the test holds open, a stream read in a thread, and the comparison of two
  runs' events without their ids.

**Read by a reviewer that had not written it.** It found nothing that cuts an action off,
runs one twice, or exceeds the step limit, and found eight problems. Each was checked against
the code, and each test added for one was shown to fail with its fix taken out (seven checks,
none survived).

| Finding | What was wrong | What changed |
|---|---|---|
| Medium | The stop route read the row and then marked whatever run held it: a stop pressed as a run ended and a follow-up started stopped the follow-up (FR-022a). | `request_stop` names the owner read, and refuses a run that is not a console run. |
| Medium | The mark was written after the run had started, with no owner: a run that ended at once could have its mark land on the next run, another client's, which then showed as stoppable. | The mark names the owner and is written by `UnattendedRuns` at the run's first event. |
| Low | `_drive` never closed the run's events: a drive cancelled while it noted the run's end left the run's `finally`, which gives the lease and the context back, unrun. | The events are closed in a shielded `finally`. |
| Low | A graph the wrapper refuses, after a rebuild, failed `begin_run` with the agent set still held. | The wrapper is made inside the `try` that releases the set. |
| Low | The reads and writes of the index added here had no time limit, where the lease wrapper's have `INDEX_WRITE_SECONDS` (FR-023). | The mark, the note, and the read of a stop have the same limit. |
| Low | With `debug=true` a console run sends one more `checkpoint` debug event at each boundary. | Not changed; `contracts/sse-events.md` says so. Debug output only. |
| Test | The `stopped` note could be removed with no test failing: a stopped run reads `unfinished`, which lists it as stopped anyway. | The stop test asserts the note. |
| Test | The first-message guard could be removed with no test failing, and the test of a handler's answer did not check that the run had not started. | A test of a follow-up after a run the earlier way; the generator's state is asserted. |

**Checked by hand** on a check instance on port 8020 with a database of its own and the
stand-in model on 9977, and the console built against it on 3020: an unattended run streamed
`start`, `update`, `message`, and `end` with `Cache-Control: no-store, no-transform`; quickstart
sections 5 and 6 answered `no_run`, `not_stoppable`, `thread_not_found`, `other_origin` twice,
and 422; through the console's forwarding the unattended route came back without
`Content-Encoding`, where the existing route came back as gzip; a stop asked while the
stand-in's `slow` model answered was `stopping` until the reply came, and the thread was then
`done`, since that reply asked for no tool (FR-022a); with a console run in that model call
the service, sent SIGTERM, waited 16 seconds for the reply and recorded it; and a console run
whose reader left after four seconds ended `done` where the same run through the existing
route ended `stopped`. The database was dropped afterwards.

### Phase 4 as built (2026-11-20)

Built as research R11 to R13 describe; the differences and additions:

- **The automated proof that a stream arrives as it is produced is in this phase** (R15).
  The owner answered the plan's question, after Phase 3, with yes, and the change to
  `web/playwright.config.ts` it needs. The app under test forwards `/api/...` to port
  3109 in place of port 9, where nothing listens; `e2e/stream.spec.ts` puts a stand-in for
  the service there, which sends the start of a reply and holds the run open until the page
  shows it. It runs in two projects of its own, `stream-chromium` and then `stream-firefox`,
  which depend on the two others, so that nothing answers on that port while they run. With
  `no-transform` taken out of the stand-in's answer the test fails in both browsers: the
  forwarding then holds the reply back. T076's question is answered.
- **A dynamic route made the sidebar's links wait.** With Cache Components, a page under a
  route with a parameter, `/tasks/[id]`, knows its address only when it is asked for, and
  `usePathname` in the shared layout then fails the build. The links that mark the current
  section are read inside `<Suspense>`, with the same links unmarked as the fallback, as the
  bundled docs advise (`use-pathname.md`, "Cache Components"). Every other route still
  prerenders its shell.
- **`send-box.tsx`**, a component the tasks did not name, is the one text box with its Send
  button: New task and the box that sends a failed first message again use it, and the
  follow-up box of Phase 7 will.
- **Following a run** is done by a timer of the view's own, every five seconds, and not by
  SWR's `refreshInterval`, which skips a reading while the last one failed: one failed
  reading would have ended the following for good (review finding below). The view follows
  while the row says a run is in progress, and after its own stream was cut until the
  service says where the run stands. This is FR-013's following, built here because a cut
  stream needs it; Phase 8 adds Stop to it.
- **A run this tab started is forgotten once its end has been read again.** Until then the
  streamed view stays on the page, so that a new task does not flash "not found" between
  its last event and the reading after it. A first run that failed before anything was
  recorded is kept, for its message to be offered again (FR-008).
- **While a run streams, only what the stream brought is shown**, not the conversation read
  before it. For a new task that is everything there is. Phase 7, whose follow-ups run on a
  thread with a conversation, shows the earlier conversation above what streams.
- **Unit tests' helpers** for this section are `web/__tests__/tasks-testing.ts` (a fetch stub
  that answers by path, streams built from events, and the service's shapes); the shared
  `testing.ts` is not changed.
- **Next.js announces a route change with an element of role `alert`**, so the end-to-end
  specs look for the page's alerts without it.

**Read by a reviewer that had not written it.** It found no defect in the parser, the guard
against a second send, the rendering as text, or FR-026, and confirmed that the stream
proof would fail if the forwarding held the stream back. It found one real defect and some
smaller ones. Each was checked against the code, and each test added for one was shown to
fail with its fix taken out (nine checks).

| Finding | What was wrong | What changed |
|---|---|---|
| Medium | SWR skips a timed reading while the last one failed, and the page did not retry on error: a page that followed a run stopped reading at the first reading that failed, as while the service restarts, and showed the error in place of the conversation (FR-010, FR-013). | A timer of the view's own reads the task again whatever the last reading did; a reading that fails is said above the conversation, which stays. |
| Low | How a run of this tab ended stayed on the page for as long as the tab was open, also once the task had moved on. | The run is forgotten once its end has been read again; the cut is said until the service says the run is no longer in progress. |
| Low | A stream cut before the run's first checkpoint showed "Task not found" for a run in progress, and stopped there. | While the row says a run is in progress the page says nothing is recorded yet and follows. |
| Low | A run ended only when its stream's body did, not at its last event. | It ends at its last event, and the connection is let go. |
| Low | The headings of messages went from `h1` to `h3`. | `h2`. |
| Test | A character split over two pieces, data on several lines, the guard against a second submit, the five seconds, and no credential on a run's request were not checked. | One test each. |
| Not changed | The parser yields a last event that no blank line follows, which the SSE standard discards. | T032 asks for it; a cut `message` shows nothing and a cut last event is what the run sent. |
| Not changed | Port 3109 is fixed: two checkouts that run the end-to-end tests at the same time would answer each other's stand-in. Stream projects are skipped, not failed, when a spec they depend on fails. | Recorded here. Before `pnpm test:e2e`, port 3100 is checked anyway, which keeps two runs apart. |

**Checked by hand** on a check instance on port 8020 with a database of its own, the stand-in
model on 9977, and the console built against it on 3020, driven by a script in a real
Chromium and Firefox: quickstart section 1, a new task from the list's page to a finished
conversation and the status Done, the same after a reload, and the row in `GET /threads`
with its first message; with the stand-in's `slow` model, the page said that the run had
started 0.9 seconds after Send (SC-001), and the run went on to done after the browser was
closed. The database was dropped afterwards. The page without a usable model was checked by
the unit and end-to-end tests only.

## Complexity Tracking

No violation of the constitution to justify. The three additions that carry complexity are
justified under Constitution Check.
