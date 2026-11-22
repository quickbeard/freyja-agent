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
dependency, no change to a CI file, `pyproject.toml`, `web/vitest.config.mts`, or
`.gitleaks.toml`. `web/playwright.config.ts` was changed once, in Phase 4 and with the
owner's word, for the two projects of `web/e2e/stream.spec.ts`. The existing thread routes behave as before
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
| IV. Configuration through settings | Yes | No setting is added, on the service or in the web app, and nothing an operator manages: a task is a thread, which is data, not configuration. Seven numbers are fixed by the specification and are constants beside the code that applies them (two more are fixed by what was built and are constants too: `INDEX_WRITE_SECONDS`, the five seconds a lease waits for the index, since Phase 2, and `ANSWER_SECONDS`, the 35 seconds the console waits for an answer, since Phase 11), as feature 005 fixed its own (twenty agents, 2,000 characters of a card's text, four card reads at once): 100 rows in the list, 200 characters of a first message, 2,000 characters of a failed run's reason, and the pages' 5 and 15 seconds are what a person meets, the same on every deployment. Two are not about what a person meets and are the ones a deployment could want to change: the ping every 10 seconds, which follows from the 30 seconds of the console's own forwarding and not from where the service runs, and the 1,000 events held for a reader that does not keep up, which bounds memory per reader. Both are kept as constants because nothing a deployment configures today changes what they rest on; making them `FREYJA_*` settings is the owner's call and is named in the analysis pull request. | PASS | PASS |
| V. Automated quality gates | Yes | Every requirement has a test (map below). The default suites need no network, database, model, or agent. Store calls are async; the index's statements are fixed strings with named parameters, so they are composed with no `sql` module and carry no `# nosemgrep` mark, and Semgrep finds nothing in them. The web app passes `biome check` and its Vitest suite with coverage. No Ruff suppression is planned. One thing the gates do not measure is said where it applies: CI does not run the Postgres tests, so the Postgres index counts against the coverage number. | PASS | PASS |
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
  browsers (R1). The end-to-end tests could not repeat it without a change to
  `web/playwright.config.ts`, which this plan did not make; that change was made in Phase 4
  with the owner's word, and `web/e2e/stream.spec.ts` repeats the proof since. Service tests
  pin the header and the ping it rests on (R15).

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

### Phase 5 as built (2026-11-20)

Built as research R14 describes; the differences and additions:

- **One action or several.** With one action, its decision is sent by the button that
  names it: Approve, Approve with these arguments (after Edit), or Reject now (after Reject).
  With several, each action's buttons choose its decision, and Send the decisions sends them
  together, in the service's order, once each has one (FR-016). While the arguments of a
  single action are open for editing, its plain button says "as proposed", so that it is
  not taken for the one that sends the edit.
- **A refused value goes beside the action it is about.** `ApiError` keeps where each
  refused value is (`locations`), beside `fields`, which is left as it was so that no other
  section's test changes; the card reads the decision's index from it. This is a small
  addition to the shared `web/lib/api.ts`.
- **A continuation is shown below the conversation as it was when the person decided.** The
  run store keeps that conversation with the run (`before`), and a continuation has no
  person's message (`message` is null). Phase 7's follow-ups will use the same.
- **A continuation that failed while its proposal is still pending keeps its reason on the
  page**, beside the card, as when an agent came to need approval since it was proposed (005
  FR-023, `approval_now_required`): the row then reads `needs_decision`, which comes before
  `failed`, so the reason would otherwise be gone.
- **A refused decision's notice** stays until the task's proposal is another than the one it
  had when the task was read again after the refusal, or until a decision is accepted.
- **After a decision is accepted** the focus goes to the run's status, so that it is not lost
  with the card.
- **A reason's length is counted by character**, as the service counts it, not by UTF-16 unit.
- **No hand check of a decision against the real service.** The stand-in model answers in
  text only and cannot propose an action, so the service's side of a decision is the suite's
  (`test_unattended_runs.py`, Phase 3), and the console's side the unit and end-to-end tests.

**Read by a reviewer that had not written it.** It found no way around the approval gate:
the proposal's id is the one the card was drawn from, a card is keyed by it, nothing is sent
without a press, a second press sends nothing, only allowed decisions are offered, and none
of the refusals is shown as a failure. It found one real defect and some smaller ones, and
seven mutations the tests let through. Each was checked against the code, and each test
added for one was shown to fail with its fix taken out (eleven checks).

| Finding | What was wrong | What changed |
|---|---|---|
| Medium | With several actions a refused edit was shown beside every action being edited, and a refusal of an action that was not edited beside one that was: the decision's index was lost. | The index is kept and the reason goes beside its action; what names no action goes to the card. |
| Low | With one action, the plain Approve stayed beside the edited arguments under the same name, and sent the arguments as proposed. | It says "as proposed" while they are edited. |
| Low | A continuation that failed with its proposal still pending lost its reason once the task was read again. | Kept beside the card while that proposal is pending. |
| Low | A refused decision's notice stayed after the task had moved on. | It goes once the proposal is another. |
| Low | The focus was lost with the card; Edit and Reject said both `aria-pressed` and `aria-expanded`; a reason was counted in UTF-16 units. | Focus to the run's status; one state each, by mode; counted by character. |
| Test | A card not keyed by its proposal, a card shown during a run, the earlier conversation shown twice, decisions not allowed offered, a notice never dropped, a reason trimmed, and a refusal beside every action all passed. | One test each. |

### Phase 6 as built (2026-11-20)

Built as research R13 describes; the differences and additions:

- **One timer for the list, shared by its readers** (`web/hooks/use-every.ts`). The list and
  the sidebar's number read `GET /threads` under one SWR key, again every 15 seconds. SWR's
  `refreshInterval` skips a reading while the last one failed, so a timer of the console's
  own does the reading, and it goes on past a failure: once the service is back, the list
  and the number come back by themselves. The timer is one per key, started by the first
  reader and stopped by the last, so the Tasks section with the sidebar beside it reads once a
  period, not twice; it reads nothing while the page is hidden, and SWR reads again when the
  page is shown.
- **A task's view reads the list again when a run it shows ends**, so the sidebar's number
  follows a decision at once and not at the next period.
- **Every page asks for `/api/threads`** from this phase on. The whole end-to-end suite was run
  with it, before any spec of this phase was written: no spec of another section failed (T057).
  Two specs of this feature failed, because the list's "cannot be reached" stood beside the
  alert they read; `TaskService` now answers the list from the tasks it holds.
- **The sidebar's unit test** answers `/api/threads` apart from the list of agents, so each of
  its existing tests keeps its count of agent requests and its meaning (FR-027 names this
  test).
- **The Tasks entry's number** is said in words on the screen ("3 to decide") and for
  assistive technology ("3 tasks need a decision"), as the Agents entry, changed meanwhile
  by feature 005, says its own.
- **Not changed: a reading that hangs.** `api()` has no time limit, so a service that takes a
  connection and never answers leaves the list and the number as they were last read. It is
  a matter of `web/lib/api.ts` for every section, not of this feature's; recorded here.

**Read by a reviewer that had not written it.** It found nothing serious: a failed reading
shows no row and no number, the following goes on, and the list and the sidebar are one key.
It found the list read twice a period on the Tasks section, readings while the page is
hidden, and tests that let mutations through. Each was checked and fixed, and each test added
for one was shown to fail with its fix taken out (six checks).

| Finding | What was wrong | What changed |
|---|---|---|
| Medium-low | The list and the sidebar each had a timer: the Tasks section read `/threads` twice a period. | One timer per key. |
| Low | The timer read while the page was hidden. | It does not. |
| Low | A reading that hangs is never a failure. | Not changed; see above. |
| Test | A timer left running, an id put into the address as it is, the time a person reads, half of the sentence about more tasks, and a test whose name claimed three cases and checked one. | One test each; the sidebar's test checks all three cases. |

**Checked by hand** on a check instance on port 8020 with a database of its own, the stand-in
model on 9977, and the console built against it on 3020, in a real Chromium: a task sent from
the console and a thread started with curl were two rows, newest first, each `Done`; the
thread from curl opened its conversation; with no task waiting the sidebar showed no number;
with the service stopped the section said that it cannot be reached and offered to try again,
and showed no row and no number. A number above zero needs a pause, which the stand-in model
cannot make: it is the end-to-end tests'. The database was dropped afterwards.

### Phase 7 as built (2026-11-20)

Built as research R12 and R13 describe; the differences and additions:

- **The follow-up box is always there under a conversation, and says why it cannot be
  sent.** While a run is in progress or a proposal is pending, the box still takes text and
  its Send is off, with the reason beside it and as the box's description. The text stays
  through the wait, so a message typed while an agent's outcome is being recorded is not
  lost. Phase 5's unit test asserted that a paused task has no text box; it now asserts a
  box that cannot be sent (FR-017 and FR-020 both hold: nothing is sent to a task whose
  proposal is pending). `send-box.tsx` gained the reason (`blocked`); New task and the box
  that sends a failed first message again pass none.
- **What the page knows is said; the service rules.** The box is blocked by what the page
  last read. A message the service refuses with `run_active` or `decision_pending` keeps its
  text, shows the service's sentence, and has the task read again, so the box then says why.
  The same reading follows a message that got no answer, since the service may have
  accepted it.
- **A follow-up whose run failed before its message was recorded gets its text back.** As
  when no usable model is active: the run is turned away before the graph, and the
  service's own sentence says to send the message again. The rule is a fixed one: after the
  failed run was read again, the conversation holds no more messages of a person than
  before it. A message that was recorded is never put back, so nothing is sent twice by a
  press of Send. This is FR-008's rule for a new task, kept for a follow-up; the spec does
  not ask for it.
- **One timer, two periods.** The view's own timer reads the row and the state every five
  seconds while a run it does not read is in progress (Phase 4's following) and every
  fifteen while it waits for an agent. Waiting for an agent is: the row says so, or an agent
  task is pending, or an agent task has ended and its outcome is not in the conversation
  yet (`outcome_delivered` false). The third case is the time between an agent's answer and
  the service's background run that records it; without it the page stopped reading just
  before the outcome arrived. While no usable model is active, or while a proposal is
  pending, the service starts no such run, and the page goes on reading every fifteen
  seconds for as long as it is open.
- **Nothing is read while the page is hidden**, for both periods, as the list's timer
  (Phase 6). SWR reads again when the page is shown.
- **The conversation is read once more when a run the page followed has ended.** The hand
  check found it: the reading that shows the end is asked for together with the
  conversation's, and the service can answer the conversation's from before the run's last
  step. The page then showed Done with the reply missing until the next reading, which,
  with nothing pending, never came.
- **One status element, always there.** What the page says of a run ("Starting the run…",
  "A run is in progress…", that it reads again while an agent task is awaited) is one
  `<output>` that is never taken away. It takes the focus when a message or a decision is
  accepted and keeps it when the run ends; it then says, for assistive technology only,
  that the run has ended or has paused for a decision. Before, the element went away with
  the run and the focus fell to the top of the page, also after a decision (Phase 5).
- **Agent tasks** are listed under the conversation and the proposal card, oldest first as
  the service lists them, each with its agent, its status in words, a "Pending" mark, and
  the message it was started with, all as text. An ended one whose outcome is not in the
  conversation yet says so. The section is absent for a task that handed nothing to an
  agent. The console's own words say "agent task" throughout (FR-001a); a test counts them.
- **The end-to-end spec moves the page's clock** (`page.clock`) in place of waiting five and
  fifteen seconds; the clock goes on by itself too, so a period is bounded by the jump plus
  the wait of an assertion, not measured. No change to `web/playwright.config.ts`.
  `TaskService` now refuses a message for a task whose run is in progress or whose proposal
  is pending, as the service does.
- **No hand check of an agent task or of the box on a paused task.** The stand-in model
  answers in text only: it cannot propose an action or hand work to an agent. Both are the
  unit and end-to-end tests'. The stand-in also gives every reply the same message id
  (`resp_stub`), so in the service's state a second reply takes the place of the first: a
  task with a follow-up holds three messages there, not four, and the page showed those
  three. `tests/llm_stub.py` is feature 004's and is not changed here.

**Read by a reviewer that had not written it.** It found no way for a message to act as a
decision or to pass one: the button and the form both check the block, Enter in the box
sends nothing, and a stale page ends in the service's refusal. It found three real defects
in the view and some smaller ones, and mutations the tests let through. Each was
checked against the code, and each test added for one was shown to fail with its fix taken
out (nineteen checks; with the nine of this phase's own behavior, twenty-eight).

| Finding | What was wrong | What changed |
|---|---|---|
| Medium | A follow-up whose stream was cut was not said to be cut, and with the service unreachable was not followed: the cut was cleared by the row the page held from before the run, which said done. The same held for a continuation after a decision (Phase 5); a new task has no earlier row, so Phase 4 did not meet it. | A cut is cleared only by a reading of the row answered after it. |
| Medium | A follow-up that ended well went from the page when the reading after it failed: the earlier conversation was shown, without the message and the reply, with a box that could be sent, which invites sending the message a second time. | What the stream brought stays, and the box stays blocked, until the task could be read; the page reads again every five seconds until it can. |
| Medium-low | The row and the conversation are two requests. A row answered before the service's own run took the task (`done`) beside a conversation answered after it (`run_active` true, the outcome delivered) left nothing to wait for: the page stopped reading and never showed Freyja's reply. | `run_active` of the conversation's reading counts as a run in progress, for following and for the box. |
| Low | The focus fell to the top of the page when a run ended. | One status element that stays; see above. |
| Low | A refusal stayed beside the box after the task had moved on and the box could be sent again. | It goes when the reason the box cannot be sent changes. |
| Low | The sentence about waiting for an agent was wrong while a proposal was pending, when the outcome is recorded only after the decision, and for an agent that asks for input. | It says what the page does and when, which holds in every case. |
| Low | The box read a pending proposal from the conversation's reading only; with that reading failed and the row saying `needs_decision`, Send was on. The service refuses such a message, so nothing was passed. | The row's word blocks the box too. |
| Low | A message that got no answer did not have the task read again. | It does. |
| Low | A follow-up's text put back in the box replaces what was typed meanwhile. | Not changed. The text is put back only for a run that recorded nothing, which the service turns away at once; there is no time to type in between. Recorded here. |
| Test | The focus was asserted on text the page's body also holds; the periods were read from the constants they test; "no box before the task was read" passed through an earlier return; and a restored text sent again, a reading of the conversation after a refusal, a refusal that is the message's own, a notice left after a message, a count of all messages for a count of a person's, the outcome's note on a pending agent task, and a message taken between a run's end and its reading all passed. | One test or assertion each. |
| Test | The end-to-end helper took a message for a task with a run in progress or a pending proposal; the task "running elsewhere" said `run_active` false; the agent's outcome arrived in one step, as a plain reply. | The helper refuses as the service does; the spec goes through "ended, outcome not recorded", the service's run, and the outcome as the `check_agent_tasks` call and its result. |
| Not changed | A refused read of the row while a run is followed costs one more reading of the conversation. | Harmless; no test pins it. |

**Checked by hand** on a check instance on port 8020 with a database of its own, the
stand-in model on 9977 answering as `slow`, and the console on 3020, driven by a script in a
real Chromium: a run started with curl was shown as in progress on a page opened after it,
with the box taking text and its Send off and the reason beside it; twenty seconds later,
without a reload, the page showed Done and both messages, the typed text was still in the
box, and Send was on; the follow-up showed Running 39 ms after Send, the earlier
conversation once above it, an emptied box, and Send off; when it ended the page showed
what the service holds, at the same address, and the same after a reload; no agent tasks
section for a task without one; and no request for the task in 31 idle seconds. The database
was dropped afterwards. One thing the reviewer did by accident is recorded for whoever
meets it: running the unit tests from a copy of `web/` whose `node_modules` pointed at this
worktree's relinked the packages while the console was served from it, and the dev server
failed until it was restarted.

**Left for later**: what was typed into the box while a follow-up that recorded nothing
failed (the table's last Low), and Stop, which is Phase 8's.

### Phase 8 as built (2026-11-20)

Built as research R12 to R14 describe. Leaving and coming back needed no code: since Phase 4
the console starts every run as one that goes on without a reader, and since Phase 7 a page
follows a run it does not read. This phase adds the tests that pin it, and Stop. The
differences and additions:

- **Where Stop is offered.** For a run of this tab, once its `start` event has arrived: the
  service marks a run as one that can be stopped at that event, and the row the page holds
  is from before the run. For a run the page follows by reading, when the row says
  `stoppable`. With no run in progress there is no Stop whatever the row says, also not in
  the moment between the end of the tab's own run and the reading after it. A run in
  progress that the row does not call stoppable has no Stop and a sentence that says why;
  a console run read in the instant before its first event shows that sentence until the
  next reading, five seconds at most.
- **"Stopping" is said from two sources**: the page's own memory of a stop the service
  accepted, at once, and the row's `stopping`, which also covers a stop asked in another
  window. The task is shown as stopped only when the row says so.
- **How an asked stop is settled.** A page that reads the run's stream is told by the run's
  last event, which is about this run and no other: `end` with `stopped`, or anything else,
  which is "the run ended before the stop took effect" (FR-022a). A page that follows the
  run by reading remembers how many readings of the row had been answered when the stop
  was accepted, asks for one more, and is told by a later one: still stopping, wait;
  another run in progress that nobody asked to stop, forget the stop and say nothing; ended
  as stopped, nothing more to say; ended another way, say that the stop came too late.
- **The "too late" sentence stays until the task's next run**: a decision or a message of
  this page, or a run the row shows once it has said that the earlier one was over. Only
  the page that asked says it, and a reload forgets it.
- **Everything the page says of a run is in the one status element** Phase 7 made, "stopping"
  and "too late" included, so that it is announced as a change and not as a new element.
  The focus goes there when a stop is accepted, since the button goes away.
- **Refusals** (`no_run`, `not_stoppable`, a thread the list does not know, no answer) are
  said in the page's notice, in the console's words for the first two and for no answer
  and in the service's otherwise, and the task is read again, as for a decision that was
  not applied.
- **The control's name** is "Stop the run of the task:" with how the task's first message
  begins, 60 characters at most, and "Stop" on the screen (FR-012a).
- **A run's end waits for a reading of the conversation made after it** (`readAfter`). The
  readings of the row and of the conversation are both counted now. Found by the review:
  a reading asked for meanwhile, as after a refused stop, takes the place of the one made
  at the run's end, which is then answered with what the page already held.
- **`TaskService`** (the end-to-end helper) answers a stop as the service rules it and keeps
  each in `stops`; `runningTask` and `goesOn` make a run in progress and a run whose stream
  stops while the run goes on, which is how a closed page is played.
- **Found by the end-to-end run before the review**: for a run followed by reading, the
  "too late" sentence was taken away at once by an older reading of the conversation that
  still said a run was active. What ends the sentence is now the row alone.

**Read by a reviewer that had not written it.** It found nothing of high severity, and no
stop, decision, or message that is sent without a press: for opening, reloading, leaving,
and Enter in a field. It found two races and some smaller gaps, and tests that let mutations
through. Each was checked against the code, and each test added or tightened was shown to
fail with its fix taken out (twenty-five checks for this phase).

| Finding | What was wrong | What changed |
|---|---|---|
| Medium | "Too late" was read from the first reading that said no run was in progress, which can be of a later run: after a stop that was applied, a run started and ended elsewhere before the page read again, and the page said the stop came too late. | A page that reads the stream is told by the run's last event. For a page that follows by reading it is not changed: the row names no run. Recorded below. |
| Medium, narrow | A refused stop's readings took the place of the readings made at a run's end. The page then took the conversation from before the run for the one after it, and for a follow-up that failed put the recorded message back in the box, to be sent twice. | A run's end waits for a reading of the conversation answered after it. |
| Low | Pressing Stop dropped the focus to the top of the page, and "stopping" and "too late" stood in elements that appear with their text, which may not be announced. | Both are said in the one status element, which takes the focus. |
| Low | Between the end of the tab's own run and the reading after it, Stop, or "has no Stop", was shown from the row held during the run. | Nothing of Stop is shown in that moment. |
| Low | A stop asked for stayed remembered when this page then started another run. | It is forgotten at a decision and at a message. No test pins it: it needs a reading of the row that fails at the right moment. |
| Not changed | A stop that got no answer and was in fact accepted is never followed by "too late"; the sentence is not shown on the two short pages that hold no conversation. | The row's `stopping` still says that the run is stopping. Recorded here. |
| Test | The tests of the tab's own run answered every reading with "not found" until the run ended, where the service says running and stopping. A press before the page drew again, the name's length, "stopping" between the press and the next row, a run only the conversation's reading knows of, a refusal that stays after an accepted stop, and a wait that passed before its reading was answered all passed. | One test or assertion each; the own-run test now has a row that goes on saying "running" for a moment after the run's last event. |
| Test | No browser test presses Stop on a page that reads the run's stream: the helper hands a page its stream whole. | Not changed. Unit tests cover it; only the stand-in of `stream.spec.ts` could, and it has no stop. Recorded here. |

**What a page that follows by reading cannot know.** After a stop it asked for, such a page
learns how the run ended from its next reading of the row, which names no run. If the run
ended and another started and ended before that reading, which takes a hidden tab or a
five-second gap, the page says the stop came too late when it did not. If a paused run was
decided elsewhere before that reading, the page says nothing. Telling the two apart needs
the row to carry the run it describes, which is the service's to add.

**Checked by hand** on a check instance on port 8020 with a database of its own, the
stand-in model on 9977 answering as `slow`, and the console on 3020, driven by a script in a
real Chromium: a task sent and its page closed as soon as the run had started sent one
request, the run's, and nothing at closing; the service showed the run as running and
stoppable, and 26 seconds later, nobody watching, as done; the page opened again showed
Done with both messages and no Stop. Stop, named "Stop the run of the task: Count slowly.",
was followed 51 ms later by "The run is stopping", with no Stop meanwhile and the status
still Running; the service's row said `stopping`; when the reply under way was complete,
20 seconds later, the task was Done and the page said that the run ended before the stop
took effect, and the follow-up could be sent. A run another client started and read itself
had no Stop and the sentence that says why, and showed Done without a reload when it ended.
The database was dropped afterwards. A task that ends as stopped needs a run with a step
after the one under way, which the stand-in model, answering in text only, never has: that
is the unit and end-to-end tests', and the service's own (`tests/test_unattended_stop.py`).

**Left for later**: Phase 9 (the checks from another origin) and Phase 10 (polish).

### Phase 9 as built (2026-11-21)

User story 6 needed no code: the two thread routers were made with the refusal in Phases 2
and 3, and the console has sent the frame headers for every path since feature 005. T070
and T071 found nothing to fix (T072). This phase adds the proof for every route, in one
place, and says in the README what a page of another origin cannot do with tasks.

- **One table of every thread route that changes something** (`CHANGING` in
  `tests/test_threads_other_origins.py`), each with the test that refuses it. The file walks
  every route the application serves, through the routers it includes, and fails when a
  thread route that changes something is not in the table, when a route under any prefix
  that changes something serves a request marked as another origin's, and when it meets a
  kind of route it does not know, such as a WebSocket route or a mounted application. The
  table is also compared with the feature's contract, which promises the 403 for each.
- **"Before a lease is taken or a stop is marked"** is asserted on what would be left behind:
  no row in the index, no lease, no run that goes on by itself, no further run counted on a
  paused thread, no stop mark on a run in progress, which then runs to its end through the
  step a stop would have kept from starting.
- **The forms a page can send** are each tried on the console's routes: a body sent as plain
  text, as a form, as a multipart form, and with no content type is refused with 422; a
  page that asks first is given no permission, for every route of the table; an origin of
  `null`, and the service's own address beside the console's, are refused; and the
  console's own requests, with the headers its server passes on, are served.
- **The refusal's sentence** is compared whole, for all five routes, with a recognizable
  thread id, message, proposal id, and origin sent: none of them is in the answer, and the
  answer carries no header that would let a page of another origin read it. One answer
  comes before the refusal: 422 for a body that cannot be read as JSON, which the framework
  reads before any rule of the service runs. The README said "before anything else is
  looked at", since Phase 2; it now says what is true.
- **The frame tests** are the ones `console.spec.ts` has for the LLM section, for `/tasks`
  and `/tasks/<id>`, with a task that waits for a decision behind the frame. They also wait
  for the console's answer to the frame's request and check its two headers, so that an
  empty frame is seen to be the console's doing and not a request that never left. Nothing
  of the console is in the frame, the console asked the service for nothing from it, and
  nothing was sent, decided, or stopped. `console.spec.ts` is not changed.
- **What the forwarding passes on** is in `stream.spec.ts`, the one spec with something
  behind the console's forwarding: a request of the console's own page reaches the stand-in
  with its origin, `same-origin`, and the host the browser asked for, and a request of a
  page of another origin with that page's origin and `cross-site`. The service's tests show
  that it refuses on exactly these; no test joins the two ends, which the hand check did.
- **`tests/unattended_helpers.py`**: `Reader` takes headers.

**Not covered: DNS rebinding.** A request that reaches the service, or the console, under a
name an attacker controls is to the browser a request of that page's own origin: the origin
and the host asked for are the same name, and the rule has nothing to tell them apart by.
Feature 004 left this open on purpose and feature 005 recorded that it inherits it. This
feature inherits it too, and with more at stake: such a page could read the list of tasks
and a pending proposal, and approve it. The spec (Assumptions, "Scope"), the README, and
the test file's header now say so. Closing it needs the service to know the names it
answers to, a new setting and a check in `api/guards.py` or as middleware, for the LLM and
Agents routes as well; that is a change for all three features and the owner's to decide.

**Read by a reviewer that had not written it.** It found that the three new tests prove
"before a lease or a mark" and found no form of request a page can send that the rule lets
through, apart from DNS rebinding, above. It found the README overclaiming, tests that
proved less than they said, and forms of request that were not tried. Each was checked
against the code, and each test added or changed was shown to fail with what it rests on
taken out (twelve checks for the service's tests, two for the browser's).

| Finding | What was wrong | What changed |
|---|---|---|
| High, known since 004 | DNS rebinding passes the rule, and the new README sentence said a page of another origin "can do none of this" without the exception. | Not changed in code; see above. The README, the spec, and the test file say what is not covered. |
| Test | The frame test never saw the frame's request reach the console: with Chromium's permission line taken out the browser itself blocks the request, the frame is empty, and the test passed. What the page asked the service for was read once, at once. | The test waits for the console's answer to the frame's request and checks its headers, and reads what was asked for after a second. Without the permission line it now fails. `console.spec.ts`, which has the same weakness for the LLM section, is feature 005's and is not changed. |
| Test | The table was checked against the application's description of itself, which leaves out a route declared as not described, a WebSocket route, a mounted application, and a route under another prefix. | The file walks the routes themselves and asks every changing route of the application, under any prefix. |
| Test | The console's routes were tried with JSON only, which a page cannot send without asking; no route had a test of the asking; an origin of `null` and the service's own address were not tried; and the console's own requests were shown to be served on the earlier routes only. | One test or case each. |
| Docs | "Through the console's own address" had no automated proof. | The forwarding's part is in `stream.spec.ts`. |
| Docs | "Before anything else is looked at" was not exact. | Reworded in both places, with a test of the one answer that comes first. |
| Low, not changed | The frame headers are the console's. The service's own pages, such as `/docs`, carry none. | Not this feature's: `api/app.py` serves them for every feature. Recorded here. |

**Checked by hand** on a check instance on port 8020 with a database of its own, the
stand-in model on 9977 answering as `slow`, and the console on 3020, in a real Chromium and a
real Firefox. With a console run in progress, a page of another origin
(`http://127.0.0.1:9978`) tried, both through the console's address and straight to the
service: to stop the run by `fetch` without asking, by `sendBeacon`, and by a form; to start
a run and to send a decision with a body sent as plain text, and with JSON; to read the
list, the task, and its conversation; and to show the task's page in a frame. In both
browsers the run went on, not stopping; the thread it tried to start did not exist; the
conversation held one message of a person; no answer could be read; and the frame was
empty. The service's log had 22 requests refused with 403 `other_origin`, the stops, the
runs, and the decisions, and the two requests Firefox sent to ask first answered 405. Stop
pressed in the console itself was accepted. One thing of the tool: with requests handed to
a page, Playwright's Chromium answers a page's asking-first itself and lets the JSON
request go; the service refused that one too. The database was dropped afterwards.

**Left for later**: DNS rebinding, above; and Phase 10 (polish).

### Phase 10 as built (2026-11-21)

The polish. No code of the service or the console changed.

**The quickstart, walked from its first section to its last (T073)** on a check instance on
port 8020 with a database of its own, the stand-in agent on 9978, and the console on 3020,
driven by a script in a real Chromium. The model was a scripted endpoint of the walk's own,
kept outside the repository: OpenAI chat completions, answering by what the last message
says, with a call of `send_notification`, a hand-off to the stand-in agent, eight calls of
`ls` one a step, or text. `tests/llm_stub.py` answers in text only and is feature 004's;
no provider's key was used, as the hand checks' rule asks. Every section did what the
quickstart says, apart from five places, which are corrected in it:

- **No usable model** (section 1). A profile that is not usable cannot be activated; the
  service answers 409. The state arises when the active profile stops being usable. The
  walk made it by starting the service with another `FREYJA_SECRET_KEY`: the profile read
  active and `key_unreadable`, a new task's page said that the run failed because no usable
  model is active, pointed to the LLM section, and offered the text again, no thread was
  listed, and after the first key was back the same text, sent again, ran.
- **A proposal decided in another window** (section 2): the second window says that nothing
  is pending any more. "No longer the pending one" is said when another proposal has taken
  its place.
- **Text that is not JSON** (section 2): the button that would send it cannot be pressed.
- **An agent task** (section 4) is listed when the run that handed the work over has ended,
  thirty seconds after the approval, which is the limit of one hand-off; the outcome was in
  the conversation 61 seconds after the approval, without a reload: forty seconds of the
  agent's, up to ten until the service looks, the service's run, and up to fifteen until
  the page reads. The quickstart said "about forty seconds".
- **Stop** (section 5): pressed during a reply that asked for an action, it left that reply
  as the last entry and the action not run, which is "the step under way completes, and no
  further step starts". The quickstart said the step is there "with its result". Two of the
  script's eight steps had run; four seconds later the service held the same six messages;
  `Go on.` continued the task. The quickstart also gained what the page says when a run
  ends by itself before the stop takes effect, and section 6 the frame headers.

What the walk saw besides: the reply growing while the run was in progress (the
conversation's length at six readings: 72, 121, 121, 168, 273, 273 characters); approve,
edit, and reject with the log line as proposed, as edited, and none; the service's refusal
of arguments that lack one beside the edit, which kept its text; the sidebar's number on
another section, gone when the proposal was decided; with the service stopped, "cannot be
reached" after 15 seconds with no row and no number, and the list back by itself once the
service was; a follow-up answered from the earlier conversation; a tab closed at once and
the task found done half a minute later; `no_run`, `not_stoppable`, `other_origin`, and 422
from the commands; and a stream through the console that came in nine pieces over a second
and a half, with `no-transform` and no content encoding.

**The two timings no test makes** (SC-001, SC-009): the page said that a run had started
475 ms after Send; with 100 tasks the section showed its 100 rows 182, 185, and 167 ms
after the page was asked for, and `GET /threads` alone took 24 ms. Both on the machine the
service ran on, against a model that answers at once.

**One thing the walk taught about the tests**: after a move inside the console, as from the
list to a task, the page that was left stayed in the document, hidden; this was seen with
the development server. A locator that looks for a status by its text then finds two, and
the walk's own script had to look at what is visible. The end-to-end specs pass as they
are, against the built app; one that goes from a list with rows to a task and finds a
status twice has met this.

**The documents read against the code (T074).** `web/README.md` still opened by calling
the console an app that shows and changes configuration, and did not say that it has no
users: both corrected. The README said that the console "is getting" a Tasks section, and
`AGENTS.md` spoke of phases in two places and described "stopping" as Phase 8 first built
it: corrected. Named now: `web/__tests__/tasks-testing.ts` and
`tests/unattended_helpers.py`, the two helpers the feature's tests share. Single test
files are not named in the Layout, for this feature as for the others.

**Coverage (T075).** Neither floor is changed: both files are the owner's to change.

| | At the start (T001) | Now | Floor |
|---|---|---|---|
| Service, lines, as CI measures | 91.25% (1,437 tests) | 91.27% (1,728 tests) | 88% |
| Web, statements | 97.44% (419 tests) | 97.86% (617 tests) | 90% |
| Web, branches | 95.33% | 94.62% | 90% |
| Web, functions | 97.32% | 98.21% | 90% |
| Web, lines | 98.34% | 98.33% | 90% |

The web app's branches fell by 0.7 of a point over the feature: the task's view has many
conditions, and not every combination of a failed reading with each state has a test. The
service's number hides what CI does not run: `runtime/thread_index.py` stands at 86.17% as
CI measures it, 39 lines missed, 35 of them the Postgres index's statements. With the
opt-in Postgres tests the file is at 98.58% and the whole service at 96.84% (1,855 tests);
the Postgres index alone is 0.6 of a point of the service's number.

**What T076 asked to say.** Both are settled, by others or earlier:

- The three proofs that no key and no address gets out were joined while this feature was
  built, by the other session's pull request #117: they share their tools in
  `tests/nowhere.py`, and `tests/test_thread_index_nowhere.py` imports them from there.
- The automated browser proof that a stream arrives as it is produced was answered with
  yes after Phase 3 and built in Phase 4 (`web/e2e/stream.spec.ts`), with the change to
  `web/playwright.config.ts` it needed.

**Every gate once more (T077)**, on this branch: the commit hook over all files; the
end-to-end suite, 270 tests in Chromium and Firefox; the opt-in Postgres tests against
`freyja_test_tasks`, 1,855 tests with the rest of the suite; Semgrep, no finding; Trivy,
nothing of HIGH or CRITICAL severity.

**Read by a reviewer that had not written it**, against the code. It found nine sentences
to correct, three of them written in this phase: the quickstart said that a reply which
asks for an action is left as the last entry by a stop, which holds only for an action
that needs no approval, since a run pauses on a proposal before a stop is looked at; it
told the person to activate a usable profile once the first key is back, where there is
nothing to activate; and its two-windows step cannot be followed by hand as written, since
a window that is given the focus reads its task again. Six were in sentences of earlier
phases: `web/README.md` on a failed reading that is "not retried", its opening sentence,
and its `hooks/` entry; the quickstart's prerequisites, which lacked `FREYJA_SECRET_KEY`;
`AGENTS.md` on what the proposal card checks; and the README on `not_stoppable`, which a
console run also gets in the moment before its first event. All nine are corrected. No
test is added: the change is documentation.

**Open at the end of the feature**, each recorded where it arose:

- DNS rebinding is not covered by the refusal of other origins (Phase 9; the owner's to
  decide, for the LLM and Agents routes as well). Closed after this phase, in a change of
  its own: the service refuses a request made for a host name it does not answer for
  (`src/freyja_agent/api/hosts.py`, `FREYJA_ALLOWED_HOSTS`, `tests/test_allowed_hosts.py`).
- A page that follows a run by reading cannot tell a stop that came too late from a stopped
  run followed by another run: the row names no run (Phase 8).
- `api()` in `web/lib/api.ts` has no time limit, so a reading that hangs is never a failure
  (Phase 6).
- No browser test presses Stop on a page that is reading the run's stream (Phase 8).
- `web/e2e/console.spec.ts` has a frame test that passes when the frame's request never
  leaves the browser, and the service's own pages carry no frame headers (Phase 9).
- One reading of the list costs a lease read per row (Phase 2).
- The stand-in model gives every reply the same id (Phase 7); a model that can ask for
  tools would let the quickstart be walked without a script of its own (this phase).

The next step is `/speckit-converge`.

## Complexity Tracking

No violation of the constitution to justify. The three additions that carry complexity are
justified under Constitution Check.

## Phase 11 as built

Convergence: the twenty tasks `/speckit-converge` appended after Phase 10 (T078 to T097).
Each has its test, shown to fail without its fix.

**What was built, where it differs from its task or adds to it**:

- **T078**: a failed task's row gives the service's reason, under how the message begins.
  The reason is shown with `failed` only, since the service gives one with no other status.
- **T079**: two tests in `web/e2e/stream.spec.ts` press Stop on the page that reads the
  run's stream, one where the run then ends as stopped and one where it finishes first.
- **T080**: `parseArguments` refuses text that holds a whole number beyond 2^53, however it
  is written, or a number too large to hold at all, and names it; the card says when a
  proposed argument is such a number. The page cannot show such an argument as proposed:
  a browser has changed it by the time the page has the answer. Reading the answer's text
  without losing digits was not built; approving sends no arguments, so it stays exact. A
  number such as `1e30`, which is whole and beyond 2^53, is refused too, though a service
  would read it as the browser does: the page cannot tell it from a 22-digit id, which a
  browser writes the same way.
- **T081**: the owner chose to narrow FR-001a to what this feature adds. No file of
  feature 005 was edited.
- **T082**: a row has `runs` and the answer to a stop has `run`. A page that follows a run
  by reading says "too late" only of the run it stopped; when the row is of a later run it
  says nothing of how the earlier one ended, since nothing keeps that. A reading of the
  same run that says "running" and not "stopping" was made before the stop: the page goes
  on saying "stopping", where it used to drop it.
- **T083**: `api()` and `stream()` give up after `ANSWER_SECONDS` (35), `stream()` only
  until its answer begins. Measured first: the console's own forwarding ends a request the
  service does not answer after 30.0 seconds, with a 500 that the pages already read as
  "cannot be reached". So the limit of the function is a backstop for what the forwarding
  does not end. `web/e2e/stream.spec.ts` shows the forwarding's limit in both browsers,
  waiting the 30 seconds; the function's own limit is shown by unit tests.
- **T084**: the periods are 10, 4, and 12 seconds, each under its bound. The browser test
  pauses the page's clock and makes the change just after a reading, the worst moment; an
  earlier form of it passed with the old period, because Playwright's clock runs on by
  itself.
- **T085**: the sidebar's Tasks entry says "not known" while the list cannot be read. The
  words are not marked up as the number is: the Agents section's browser tests, which this
  feature does not edit, find the sidebar's numbers by that markup.
- **T086**: built in the route, not in the stores: `POST .../runs/unattended` opens a
  thread that has a state and no row before the run starts, as reading it would. No store
  changed, so there is no Postgres test; the tests are in `tests/test_thread_index_api.py`.
  A new task pays one reading of the index and one of its state for it.
- **T087 to T091**: the five assertions, as their tasks say.
- **T092, T093**: as their tasks say. The card's sentence is now "An approved action runs
  once at most, and not at all when it is no longer set up on the service".
- **T094**: the test found a control out of reach at 360 pixels: "Approve ... with these
  arguments" was wider than the window. The card's decision buttons now wrap.
- **T095, T096**: the contract's answers, and the sentences of the spec and this plan.
- **T097**: the stand-in model's `call-tools` id, chat completions only, and an id of its
  own for every reply of every model id. The quickstart can be walked without a
  provider's key.

**Found while building, and fixed here**: a run that fails with a model's error which
names an agent's address sent that address in its `error` event, and the list repeated it
as the failed task's reason. A console run's error is now cleaned of addresses in
`runtime/unattended.py`, for the list and for the reader's `error` event.

**Read by a reviewer that had not written it.** No finding was high. Checked in the code
and fixed, each with a test that fails without the fix:

| Finding | What was done |
|---|---|
| A reading that failed after an accepted stop made the page say "too late" while the run was still stopping. | A reading that failed says nothing of the run. |
| A proposed whole number of 22 digits or more is written with an exponent by a browser, so an edit of another argument could send a number nobody proposed. | Refused however the number is written. |
| Each tick of a timer asked for a reading while the one before was under way, which drops the earlier answer: with a service slower than the period no reading would ever be shown. | A tick asks for nothing while its reading is under way. In the task's view this is kept in a ref: reading SWR's `isValidating` there re-renders the view on every reading, which its effects are not written for. |
| The address fix was described as wider than it is. | The comment, the README, `AGENTS.md`, and the contract say what it does. |
| A test held the earlier route to sending the address. | Removed. |
| `_open_first` adds a second wait of five seconds when the index hangs; the spec said five in all. | The spec says five each time; a test of the lookup that never answers was added. |

**Checked by hand** on a check instance, with the repository's stand-in model as
`call-tools` and the stand-in agent, in a real Chromium: a proposal from `notify` approved;
an edit with a 20-digit number refused beside the edit with its reason; Stop on the page
that reads the stream of `steps 8`, "stopping" 36 ms after the press and Stopped 0.8 s
later with two of eight steps taken; a hand-off from `handoff stub`; a failed task's reason
on its row; and "not known" on the sidebar 8 seconds after the service was stopped.

**Open after Phase 11**, none of them this feature's to close:

- **A model's error that names an agent's address** is sent as it is by
  `POST /threads/{id}/runs` and `/resume` in their `error` event, is in the debug stream
  of every route, and is kept by the checkpointer with the failed step. Features 004 and
  005 clean a model's error of the profile's key in `llm/guard.py`; the address needs the
  same there, or in `api/streaming.py`. Neither file is this feature's.
- **The Agents section's words** for an agent task and a task (FR-001a, narrowed).
- **A profile's test may take 60 seconds** (`FREYJA_LLM_TEST_TIMEOUT_SECONDS`), and the
  console's forwarding ends a request after 30: a slow test is cut by the console. Feature
  004's.
- **How a stopped run ended is not known** to a page that follows by reading once a later
  run has started: the page says nothing then.
- **No browser test** of a decision that gets no answer; unit tests cover it. Added in
  Phase 12.
- DNS rebinding, open at the end of Phase 10, is closed (`api/hosts.py`).

## Phase 12 as built

The second convergence: fourteen tasks (T098 to T111), one high, three medium, ten low.
Each has its test, shown to fail without its fix.

- **T098, high**: a reply's pieces that arrive as lists of blocks are joined while the run
  streams. The default model (`anthropic:`) and an `openai:` model through the Responses API
  send each piece as a list with one block, and an empty list last; the page showed the last
  piece alone, then nothing, until the run had ended. Read from a real run of the stand-in
  model through the Responses API, the pieces are: one empty list under the response's id,
  then text blocks with `index` 0 under the run's id, one carrying the text's own `id`, and
  an empty list. `web/lib/merge.ts` is a port of the library's own join (`merge_content`,
  `merge_lists`, `merge_dicts`), so what streams is what the conversation holds after; the
  unit tests' expectations were each computed with the library. Each piece also carries
  the actions as that piece alone makes them, the later ones under no name: only a piece
  whose every action is named replaces the actions shown. A reply of Freyja's that holds
  nothing yet is not shown. A browser test in `web/e2e/stream.spec.ts` sends the Responses
  API's pieces through the console's forwarding.
- **T099**: `web/lib/json.ts` reads JSON with a whole number beyond 2^53 kept as it is
  written, where the browser gives a number's own text to a reviver (`context.source`) and
  writes text back as a number (`JSON.rawJSON`); current Chromium and Firefox, and the
  Node the unit tests run on, do. `api()`, `stream()`, the stream's events, and
  `parseArguments` read through it, so such an argument is shown digit for digit, in the
  card and in the conversation, and an edit sends it as it was entered. A browser that
  cannot keeps the refusal and the note of Phase 11. The browser test hands the page the
  service's answer as text, since built as a value in the test the id would already be
  another.
- **T100**: tests that a hidden page and a hidden list read again when shown, by no timer.
  The list's unit tests now set the reading library's focus throttle to zero, as the
  view's did: it waits five seconds after a page was mounted before it reads on focus.
- **T101**: every text of every event a console run sends its reader, keys included, is
  cleaned of agents' addresses as it reads (`_scrubbed`), the debug stream included; the
  proofs drive the debug stream of both console routes, with an error that writes the
  address after a line break and after letters of another script. The earlier routes and the checkpointer are still not cleaned (below).
- **T102**: when the lookup before a console run fails or is given up, the index is handed
  no text, and the row takes its first message from the thread's state once the run is
  settled.
- **T103**: when the service counts tasks and lists none, the section says so, not that
  there is no task.
- **T104 to T106**: a decision the service stops listing is dropped from the card's state,
  so it takes a press to choose it again; values the service refuses make the view read
  the task again; two actions of one name are named by their place as well.
- **T107**: unit and browser tests of a decision that gets no answer, applied and not.
- **T108**: "This run has no Stop: the console did not start it, or the service could not
  mark it as one that can be stopped."
- **T109, T110**: a page of another origin reads nothing of `/api/threads` through the
  forwarding, in both browsers; the sidebar says "not known" while a reading hangs; the
  rows go when a later reading fails.
- **T111**: the spec (three waits; FR-006 and a hidden console; FR-007's "blank";
  FR-022a and a row of a later run; FR-014 and FR-015 and the numbers), the contract's
  `runs`, which counts every time the lease was taken, and a dated note in `research.md`.

**Read by a reviewer that had not written it.** Checked in the code and fixed, each with a
test that fails without the fix:

| Finding | What was done |
|---|---|
| Medium: `_scrubbed` cleaned the event's JSON text, where a line break or a letter of another script before an address is an escape sequence; the address was then not found, in the reader's `error` event and in the reason the list keeps. The code it replaced cleaned the decoded message. | Each text is decoded and cleaned, keys included, and the data written again with the same encoder. |
| Medium: while a run streamed, an action was shown half built, under no name: each piece carries the action as that piece alone makes it. | Only a piece whose every action is named replaces the actions shown. The test's pieces are now those the service's serializer makes of the library's own. |
| Low: the view's own join differed from the library's in places (lists and numbers inside a block, a text place that is not the library's, blocks of one place with different ids, text followed by blocks). | Replaced by a port of the library's join (`web/lib/merge.ts`). |
| Low: a bare whole number beyond 2^53 passed the check that edited arguments are a JSON object. | A set of named arguments is a plain object. |
| Low: the filter for empty messages hid an action's result that holds no text. | It hides only a reply of Freyja's. A bare string among blocks is shown as text, and an empty one as nothing. |

**Also changed while building**: two unit tests that fake `setInterval` waited for the
run to be forgotten with Testing Library's wait, which looks again only by that faked
timer or when the page changes. Once answers were read as text and then parsed (T099),
one of them failed every time, the run forgotten with nothing on the page left to change.
Both now wait in real time (`forgotten`).

**Open after Phase 12**, none of them this feature's to close:

- **A model's error that names an agent's address** is sent as it is by
  `POST /threads/{id}/runs` and `/resume`, and kept by the checkpointer with the failed
  step: `llm/guard.py` or `api/streaming.py`, features 004 and 005.
- **The Agents section's words** for an agent task and a task (FR-001a, narrowed).
- **A profile's test may take 60 seconds**, and the console's forwarding ends a request
  after 30: feature 004's.

## Phase 13 as built

The third convergence: eight low tasks (T112 to T119), nothing critical, high, or medium.
Each code task has its test, shown to fail without its fix.

- **T112**: an action is shown from the piece that names it, and a reply that asks for two
  shows both while it streams: a named action is added, or takes the place of the one of
  its id, and one that names nothing is left out. Once the model's step is done, its
  `update` event holds the reply whole, and each action shown takes its arguments from it,
  matched by the action's id. The id and not the message's is matched: through the
  Responses API, the reply the update holds keeps the provider's id while most pieces
  streamed under the run's. The update's shape was read from a run of the stand-in model
  through the service (`{"node": "model", "data": {"messages": [...]}}`). Before the step
  is done, an action whose arguments arrive in pieces is still shown with `{}`: the
  service sends no piece's part of the arguments, only what each piece alone makes of them.
  A subagent's actions keep `{}` until the run has ended: the stream carries no update from
  inside a subagent (`api/streaming.py`, feature 003).
- **T113**: every reading of tasks takes `READING` (`web/lib/tasks.ts`), which sets the
  reading library's focus throttle to zero, so a page that was hidden is read as soon as it
  is shown, also within five seconds of being opened. Being shown fires two events; the
  second finds the first's reading under way. The list's and the view's unit tests no
  longer set the throttle themselves, so their tests of a page shown again prove it.
- **T114**: while the page reads its own run's stream, the view's timer reads the row, and
  only the row, every `FOLLOW_SECONDS`. A stop asked in another window is then said, and
  Stop is no longer offered. How the run ended is still the stream's last event to say: a
  row read while the page streams does not settle a stop the page asked for, since the row
  can say the run is over before that event arrives.
- **T115**: a decision refused because a run is in progress says that the proposal was most
  likely decided in another window.
- **T116**: the spec says a number is kept by its value: `5.0` is shown and sent as `5`.
  Only a whole number beyond 2^53 is kept as it was written, since its value would change.
- **T117 to T119**: the contract's example rows carry `runs`; its other reasons for `500`,
  the `422` that comes before `403`, and the cleaning of a console run's events are said
  (`openapi.yaml`, `sse-events.md`); and the Constitution Check's row for principle V says
  that the index's statements are fixed strings with named parameters, with no `sql`
  module and no `# nosemgrep` mark.

**Read by a reviewer that had not written it.** No high or medium finding. Each low one was
checked in the code and fixed, with a test that fails without the fix:

| Finding | What was done |
|---|---|
| Low: on the render the stream's last event arrives in, the effect that settles a stop from the row still acted on a row read during the stream; a row that disagreed with the stream, as one whose note of the stop was lost, made the page say "too late" of a run the stream said stopped. | A stop records whether the page read the run's stream when it was asked; only a stream that was cut leaves such a stop to the row. |
| Low: one failed reading of the row while the page streams its own run showed "could not be loaded", now that the row is read every four seconds then. | Not said while the page reads its own run's stream; the row is read again a few seconds later. |
| Low: between its own run's last event and the reading after it, the page said that a run is in progress, from the row read during the stream, and read the conversation once more, which replaced the reading made at the end. | Neither for the page's own run while it is shown as it streamed. |
| Low: the join trusted actions' ids to be non-empty and unique across replies. | An empty id is no id, and a step's update fills only the replies streamed since the previous one. |
| Low: README and `AGENTS.md` said an action's arguments appear once the reply is complete, which holds for Freyja's own replies only. | Both say so. |

**Checked by hand** on a check instance, with the stand-in model as a saved profile whose
one model call takes 20 seconds: window A started a run and read its stream, window B
opened the task and pressed Stop. Window A said "stopping" 3.8 seconds after the press,
after one reading of the row, and offered Stop no more. The stop came during the reply's
only model call, so the run ended by itself, as the spec has it: both windows showed the
task done, window B said that the stop came too late, and window A, which had not asked,
said nothing of it.

**Open after Phase 13**, none of them this feature's to close: the three of Phase 12 above.

**Phase 14 as built.** T120 first, on its own: a new task's id is 16 bytes from
`crypto.getRandomValues`, which a browser gives every page, in place of
`crypto.randomUUID`, which it gives a secure origin only (https, `localhost`, `127.0.0.1`):
a console reached over http by a name or another address threw at New task before its
dialog opened. A unit test takes `randomUUID` away and asks for 200 ids. Then T121 to T127:

| Task | As built |
|------|----------|
| T121 | When the row or the conversation read at the end of the page's own run still says a run is in progress, the view reads both once more `AGAIN_SECONDS` (1) later. The timer is kept in a ref, cleared only when the page goes, since the page forgets its run once the first reading is answered. |
| T122 | `rowFailure` is not shown while the row the page holds may be from before its own run: from the run's start until the reading made at its end was answered (`rowFromBefore`). |
| T123 | The follow-up box and the box that sends a failed first message again say `run_active` and `decision_pending` in the console's words (`inWords` in `follow-up.tsx`); Stop says `thread_not_found` and `internal` in its own; the list says "The list of tasks could not be read." and a task's page "The task could not be read." for the service's `internal` answer to a reading of the list or of the row. `send-box.tsx` is unchanged. |
| T124 | The list compares the tasks it shows that need a decision with the service's count of them; when fewer are shown as such, it says how many, and that they could not be read just now or changed while the list was read, in place of saying that every one is listed. No change to the service or the contract. |
| T125 | `_summary` passes a failed run's stored reason through `SCRUBBER` again. |
| T126 | The contract's introduction says the `unknown_host` refusal comes before anything else, on every path but `/healthz`, for every client, also for a request whose `Host` names no host; each GET has a `403` for it, and each POST's `403` names both refusals. FR-024 and FR-027 name it as the one change for a client that is not a web page. |
| T127 | FR-002 is narrowed to what data-model.md's "Opened" says, the second of the two ways the task gave: the time the row is opened is not when the run started either, and LangGraph writes a checkpoint as a run starts, so the last recorded step is the nearer of the two. No code changed. |

An independent reviewer read the diff. It found nothing high, one medium that Phase 13 had
left, and four low; each was checked in the code and fixed, with a test:

| Finding | Fixed by |
|---------|----------|
| Medium: a stop asked while the page read its own stream, and the stream then cut, was never settled: once the page forgot its run, the guard that leaves an own run to its last event skipped every reading of the row. The page said "stopping" on, and never that the stop came too late. | A cut stream makes the stop the row's to settle (`own: false`), as for a run the page follows. |
| Low: Stop, and the box that sends a failed first message again, still gave service sentences that name a thread. | Both say them in the console's words. |
| Low: T124's words were untrue for a task whose run took the lease while the list was read, which is listed as Running. | The words name both causes. |
| Low: T122 hid a failure the row read after a cut stream reported, while the conversation could not be read. | The row's failure is hidden only until the reading made at the run's end was answered. |
| Low: the contract's `unknown_host` left out a request whose `Host` names no host, and FR-027 left out `/healthz`. | Both say so. |

Each fix was taken away in turn and its tests failed every time: 17 mutation checks.
