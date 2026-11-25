# Implementation Plan: Progress of Pending Agent Tasks

**Branch**: `008-agent-task-progress` | **Date**: 2026-11-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/008-agent-task-progress/spec.md`

## Summary

A task handed to a delegate agent can run for hours, and once its hand-off has stopped
waiting, the service knows only that the agent is working. This feature keeps the text an
agent attaches to a working status, with the time the service first learned it, and shows it
in three places: the thread's state, the task's page of the console, and the result of the
orchestrator's read-only check. A hand-off to an agent that does not stream also reports a
changed text in the run's stream.

Nothing new is asked of an agent. The service already reads a task's status in two places:
while a hand-off follows the task, and at each regular check of a pending task. The research
showed, in a run of the real hand-off code against a stand-in agent, that the second of
these sees a text the agent set after the hand-off stopped waiting
([research.md](research.md) R1). So the whole feature is to keep what those reads already
return.

On the service: two nullable columns on the table of agent tasks that exists, written by the
hand-off when it records its task and by the worker in the statement that already sets when
a task was last checked; one new key in an agent task of the thread's state; one more line
in the check's result; and a second condition in the loop that follows a task of an agent
that does not stream. On the web: one more block under a working agent task, and the age of
its text, worded from the browser's clock at the timer the view already has.

No table, route, setting, dependency, timer, or request to an agent is added, and no log
line.

Design details are in [research.md](research.md); the two columns and their rules in
[data-model.md](data-model.md); the changed shape of the thread's state in
[contracts/openapi.yaml](contracts/openapi.yaml); the check's result in
[contracts/check-agent-tasks.md](contracts/check-agent-tasks.md); the stream's change in
[contracts/sse-events.md](contracts/sse-events.md); the manual walk-through in
[quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 for the service (`.python-version`), managed by uv with the
committed `uv.lock`, src layout. TypeScript 5 on Node 20.9 or newer for the web app, managed
by pnpm with the committed `web/pnpm-lock.yaml`.

**Primary Dependencies**: unchanged. The service: a2a-sdk 1.3.0, deepagents 0.7.32 on
langgraph 1.2.18, fastapi 0.162.0, psycopg 3.3.6 with its pool. The web app: Next.js 16.4.6
with the App Router and Cache Components, React 19.3, swr 2.5.1, Tailwind CSS 4, shadcn/ui.
No dependency is added to either lock file.

**Storage**: the existing Postgres table `freyja_agent_tasks` gains two nullable columns,
`progress` and `progress_at`, added at startup when missing, by the mechanism feature 005
built for `agent_ref` ([data-model.md](data-model.md), research R2). The in-memory store of
the suite carries the same two fields. The web app stores nothing.

**Testing**: pytest 9 with pytest-asyncio, the scripted chat model, in-memory persistence,
and the stand-in agent, extended so that it reports a progress text and changes it; no
network. The two columns have opt-in tests under `tests/integration/`. Vitest with jsdom for
the web app's units; Playwright in Chromium and Firefox for the console part of User Story 2
(research R10, R11).

**Target Platform**: a Linux server for the service, bound to loopback, and a current desktop
browser for the console, as in features 004 to 007.

**Project Type**: a web service and the web app that is its client.

**Performance Goals**: a progress text an agent starts to report is in the thread's state
within 15 seconds and on an open page within 30, at the default intervals and for an agent
that answers a status read within a second (SC-001): one
check interval of 10 seconds, plus one reading of the page every 12. A check costs what it
costs today: one request to the agent and one statement, which now may set two more columns.
A reading of the thread's state costs what it costs today.

**Constraints**: no request to an agent that the service would not have made (FR-015); no
interval changes; nothing is written into a thread (FR-016); no route is added (FR-018); the
text is never logged (FR-018a) and never searched for an address (FR-004); the page adds no
reading and no timer (FR-011). No new setting, no change to a CI file, `pyproject.toml`,
`web/vitest.config.mts`, `.gitleaks.toml`, or `web/playwright.config.ts`. Coverage floors:
88% of the service's lines and 90% of each web measure.

**Scale/Scope**: one text of at most 2,000 characters per agent task. About seven service
modules change by a few lines each, three web components and one web module change, one web
module is new, and there is one new test file on the service and two on the web.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against constitution 1.5.0.

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | Yes | No tool is added or reclassified, and the gate, its three decisions, and a resume are not changed. Progress proposes nothing: it is never written into a thread, starts no run, and creates no proposal (FR-016). The one place an agent's text reaches the model is the result of a read-only tool, where an agent's reply has always reached it; whether an action pauses is the catalog's and the gate's to say, and a test hands the model a progress text that reads like an instruction and checks that a gated action still pauses (research R7; FR-017, SC-007). | PASS | PASS |
| II. Protocol-based delegation | Yes, central | Progress is read over A2A, in the status the protocol defines, by the reads the service already makes: the events of a hand-off and `GetTask` (research R1). No agent framework is imported, and no agent is asked for anything new: no message, no further read, no cancel (FR-015). The agent stays the owner of its words; the service keeps and shows them as the agent's claim. | PASS | PASS |
| III. One runtime, every environment | Yes | `build_agent` stays the only assembly point and the worker the only watcher. Both stores implement the same protocol, with the two fields in each. The web app stays a client of the API: it shows what the thread's state returns, holds no credential, calls no agent, and decides nothing the orchestrator does. Wording how long ago a time was is presentation, from a time the service returned (research R8). | PASS | PASS |
| IV. Configuration through settings | Yes | No setting is added, on the service or in the web app. One number is fixed by the specification and is a constant beside the code that applies it: 2,000 characters of a kept text, the length feature 005 keeps of a card's text, the same on every deployment (spec, Assumptions). Progress is data about a task, not configuration, so nothing an operator manages is added. | PASS | PASS |
| V. Automated quality gates | Yes | Every requirement has a test (map below). The default suites need no network, database, model, or agent. No statement is added to the Postgres store, so no new `# nosemgrep` mark; the two `ALTER TABLE` entries follow the two that exist. The web app passes `biome check` and its Vitest suite with coverage. No Ruff suppression is planned. One thing the gates do not measure is said where it applies: CI does not run the Postgres tests, so the two entries that add the columns are run only by the opt-in test. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes | Whether a task is working, has ended, or waits for input is read from its state, never from what an agent says of itself (FR-017). What counts as progress is one fixed function over a status (research R1). Where the console shows progress is a fixed rule on the task's status. No model is asked whether a text is progress, or whether to show it. | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; the Postgres pool; Ruff only. The web app stays Next.js with TypeScript, pnpm, Biome, and Vitest. | PASS | PASS |

No deviation from the constitution, and nothing that adds complexity the governance section
asks to justify: no new module on the service, no new table, no new object of the runtime.

### Changes to the spec

None when the plan was written: the research found nothing the spec asks for that cannot be
known or shown. The checklist review changed it; see below. Three things the spec leaves to
the plan are settled here and recorded in the research:

- **No log line is added** (R9). FR-018a allows one that says a task's progress changed,
  without the text; the plan adds none.
- **The proof that nothing is logged** (SC-011) searches the service's own loggers at every
  level and every logger at info level and above. A library's debug line can hold an
  update's text today, and the spec's assumptions leave that as it is.
- **How long ago** is worded from the browser's clock (R8). A browser whose clock is wrong
  shows a wrong age; the exact time is beside it.

### Checklist review (2026-11-22)

[checklists/security-reliability.md](checklists/security-reliability.md) was reviewed
against the spec and this plan; its "Review record" lists 12 findings and what each changed.
None changes the design. Three change what a reviewer would read differently:

- **A hand-off that writes over a later text.** The spec kept the text learned last, and the
  plan had the hand-off write when it records its task, whatever a check had kept meanwhile.
  The hand-off now writes over a row's progress only when that progress was learned earlier
  (research R4), and the spec's edge case says when the two can meet: a message on a task
  still working, never an answer.
- **An agent's text in the check's result** is indented line by line, so a text of several
  lines cannot pass for another entry (FR-013, contracts/check-agent-tasks.md).
- **SC-005** is measured on the check's result, which a scripted model cannot spoil.

Feature 007's last phases (#127, #128) were merged after this plan was written. They change
one number it used, the page's reading interval while an agent task is awaited, from 15
seconds to 12, and nothing it relies on. The bound of SC-001 still holds. No principle's
verdict changes.

### Analysis (2026-11-22)

`/speckit-analyze` read the spec, this plan, and `tasks.md` against each other and the code
on `main`. It found no conflict with the constitution and no requirement without a task.
Most fixes went to `tasks.md`, and the quickstart's sections 2 and 4 now name the statuses
the stand-in sends. Three changed this plan:

- **The rule on agents' addresses in `AGENTS.md`** gains its exception for an agent's own
  words in phase 1, not phase 4, since phase 1 already returns kept progress unsearched in
  the thread's state, and each request leaves the documents true.
- **The stand-in's `slow` answer** changes in one existing behavior. It is recorded below and
  in research R10.
- **`task-list.tsx`** is a third web component that changes, since its time formatter moves
  to `web/lib/ago.ts`.

## Project Structure

### Documentation (this feature)

```text
specs/008-agent-task-progress/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1 to R12, what was verified and what was run
├── data-model.md        # Phase 1: the two columns, their rules, who writes them
├── quickstart.md        # Phase 1: the manual walk-through, one section per user story
├── contracts/
│   ├── openapi.yaml           # the changed schema of an agent task in the thread's state
│   ├── check-agent-tasks.md   # the result of the orchestrator's check
│   └── sse-events.md          # when a hand-off to an agent that does not stream sends an update
├── checklists/
│   ├── requirements.md            # the spec quality checklist, from /speckit-specify
│   └── security-reliability.md    # from /speckit-checklist, all 43 items reviewed
└── tasks.md             # Phase 2 output (/speckit-tasks; not created by /speckit-plan)
```

### Source Code (repository root)

Every file below exists unless it is marked new.

```text
src/freyja_agent/
├── delegates/
│   ├── a2a_agents.py          # progress_text and PROGRESS_LIMIT (R1); SendOutcome.progress and
│   │                          # .progress_seen, noted by _send and _poll (R4); _poll reports a
│   │                          # changed text (R5)
│   ├── a2a_tools.py           # _record writes the hand-off's last progress with the task (R4)
│   └── delegation_worker.py   # _check keeps a new text in the statement that sets
│                              # last_checked_at (R3)
├── runtime/
│   └── agent_tasks.py         # AgentTaskRecord.progress and .progress_at; the two columns in
│                              # the CREATE statement and in _ADDED_COLUMNS (R2)
├── agent/
│   └── agent_tasks.py         # task_line: the progress line; the tool's description (R7)
└── api/
    ├── schemas.py             # AgentTaskProgress; AgentTask.progress (R6)
    └── threads.py             # _read_state fills it (R6)

tests/
├── a2a_stub.py                # working_text, report(), and the keyword steps (R10)
├── test_a2a_progress.py       # new: User Stories 1, 3, and 4, the edge cases, the counts of
│                              # requests, the log
├── test_agent_task_store.py   # the two fields in the in-memory store
├── test_state.py              # the shape of an agent task
├── test_a2a_*.py, test_agents_*.py   # only where a test pins a whole entry of the state or a
│                              # whole result of the check (FR-022)
└── integration/
    └── test_a2a_tasks_postgres.py   # the columns added to an existing table; a round trip

web/
├── lib/
│   ├── tasks.ts               # AgentTask.progress
│   └── ago.ts                 # new: how long ago a time was, in words (R8)
├── components/tasks/
│   ├── agent-tasks.tsx        # the progress of a working agent task, as text, with its age
│   ├── task-list.tsx          # its own time formatter replaced by exactTime from ago.ts
│   └── task-view.tsx          # the current time in the view's state, set by the timer it has
├── __tests__/
│   ├── lib/ago.test.ts        # new
│   ├── components/tasks/agent-tasks.test.tsx, task-view.test.tsx
│   └── tasks-testing.ts       # the fixture of an agent task
└── e2e/
    ├── tasks-progress.spec.ts # new: User Story 2 in Chromium and Firefox
    └── tasks-api.ts           # agentTask: progress null unless a test gives one

AGENTS.md, README.md           # what exists once each phase is built
```

**Structure Decision**: the layout of the repository as it is. On the service nothing is
added but one test file: each change sits in the module that owns the thing it changes. The
function that decides what a progress text is lives in `a2a_agents.py`, beside the two
functions it is made of and the two callers that share it. On the web one module is added,
`web/lib/ago.ts`, because wording a time is not about tasks and is tested by itself.

Two groups of these files belong to features whose owners should know:

- `web/components/tasks/`, `web/lib/tasks.ts`, `web/__tests__/tasks-testing.ts`, and
  `web/e2e/tasks-api.ts` are feature 007's, whose session may still run its last step. The
  console phase is built on the `main` of its day (research R12).
- `tests/a2a_stub.py` is shared by every feature that hands work to an agent. What is added
  to it changes nothing an existing test relies on: the first working status keeps its
  text, and the new keyword and the new method are used by this feature's tests only. One
  existing behavior changes: an answer that starts with `slow` sends its working status with
  the stand-in's working text, where it sent none, so that an answer's hand-off has progress
  to keep. The existing tests that send such an answer check only its result.

### Phases

Each phase is one pull request, and leaves the suite passing and the README true.

| Phase | What it builds | Stories | Shown working by |
|---|---|---|---|
| 1 | The stand-in agent reports and changes a progress text. The record's two fields in both stores, and the two columns. `progress_text`. The hand-off notes its last progress and records it with the task. The worker keeps a new text at a check. The thread's state returns `progress`. The rule on agents' addresses in `AGENTS.md` given the exception feature 005 already makes for an agent's own words, kept progress among them. | 1 | [quickstart.md](quickstart.md) section 1; `tests/test_a2a_progress.py`, `test_agent_task_store.py`, `test_state.py`, and the opt-in Postgres test |
| 2 | The task's page shows the progress of a working agent task and how long ago it was learned. | 2 | Section 2; the Vitest files and `web/e2e/tasks-progress.spec.ts` |
| 3 | The check's result lists progress. A hand-off to an agent that does not stream reports a changed text. | 3, 4 | Sections 3 and 4; `tests/test_a2a_progress.py` |
| 4 | The quickstart walked by hand against a real model and corrected; `README.md` and `AGENTS.md` read against the code. | all | Every section of the quickstart |

Phase 1 is the one the others read from, and it already serves every client of the API.
Phases 2 and 3 do not depend on each other and can be built in either order. Stories 3 and 4
share a phase because each is one function and one group of tests.

What phase 1 must not leave half done: the record's fields, the columns, and the state's key
go in together, since `_TASK_COLUMNS` is made from the record's fields and a field without
its column fails every reading of a task on Postgres.

### Requirement to test map

| Requirement | Where it is tested |
|---|---|
| FR-001, FR-002, FR-003 (kept, replaced, the time stays, no text keeps) | `tests/test_a2a_progress.py`: checks of a held task whose stand-in changes, repeats, and drops its text |
| FR-004 (text parts only, 2,000 characters, characters that cannot be kept, an address passes) | `tests/test_a2a_progress.py`, on `progress_text` and through a check |
| FR-005 (the store's clock) | `tests/test_a2a_progress.py`, with the suite's clock that a test moves |
| FR-006 (a task starts with its hand-off's last progress; a message on a task it has) | `tests/test_a2a_progress.py`: the state right after a hand-off ends as pending, before any check; a message on a working task whose row holds a later text |
| FR-007 (stays when a task ends, is given up, waits for input) | `tests/test_a2a_progress.py`, one case per ending of the stand-in's `hold` |
| FR-008, SC-008 (every instance, a restart) | `tests/test_a2a_progress.py` with a second runtime and a reopened one; `tests/integration/test_a2a_tasks_postgres.py` |
| FR-009, FR-021 (the state's shape, a task recorded before) | `tests/test_state.py`; `tests/test_agent_task_store.py`; the Postgres test with a row written before the columns |
| FR-010, FR-012 (shown only when working, as text, whole, usable, by keyboard, isolated in direction) | `web/__tests__/components/tasks/agent-tasks.test.tsx`; `web/e2e/tasks-progress.spec.ts` |
| FR-011 (no reload, no new reading or timer, nothing while hidden) | `web/__tests__/components/tasks/task-view.test.tsx`; `web/e2e/tasks-progress.spec.ts` with the page's clock |
| FR-013, FR-014 (the check lists it, set apart; the start of a run does not) | `tests/test_a2a_progress.py`: the check's result with a text of several lines that imitates an entry, and the system message the scripted model received |
| FR-015, SC-002 (no request more) | `tests/test_a2a_progress.py`: the requests the stand-in's transport recorded, with progress and without |
| FR-016, SC-003 (nothing in the thread, no run, no proposal) | `tests/test_a2a_progress.py`: the thread before and after, also while the lease is held and while a proposal waits |
| FR-017, SC-007 (a text that reads like an instruction) | `tests/test_a2a_progress.py`: a gated action still pauses after the check returned such a text |
| FR-018 (no route that changes something) | `tests/test_threads_other_origins.py` as it is: it fails when a route is added that its table lacks |
| FR-018a, SC-011 (never logged) | `tests/test_a2a_progress.py`: the log of a run through checks, hand-offs, readings of the state, and the check, searched as research R9 says |
| FR-019, FR-020, SC-010 (the stream of an agent that does not stream; one that streams unchanged) | `tests/test_a2a_progress.py`; the existing stream tests of `tests/test_a2a_handoff.py` pass unchanged for an agent that streams |
| FR-022, SC-009 (everything else as it was) | the whole existing suite, changed only where FR-022 lists |
| FR-023 (the tests the spec names) | the rows above; the console's end-to-end test is `web/e2e/tasks-progress.spec.ts` |
| SC-001 (15 and 30 seconds) | follows from one check (10 seconds) and one reading of the page (12 seconds) at the default intervals; walked in the quickstart, sections 1 and 2 |
| SC-004, SC-005, SC-006 | the console's tests; the check's result in `tests/test_a2a_progress.py`, and a real model's answer in the quickstart, section 3; `agent-tasks.test.tsx` and the end-to-end test with a text that holds markup |

## Complexity Tracking

No violation of the constitution to justify.

## Implementation Notes

### Phase 1: the starting point (2026-11-25)

Measured on the branch as created from `main` at `32dc9ae`, before any change of this
feature. T042 compares the finished branch with these numbers.

| Suite | Command | Result | Floor |
|---|---|---|---|
| Service | `uv run pytest --cov` | 1,897 passed, 127 skipped; 91.39% of statements (5,970, of which 514 not run) | `fail_under = 88` in `pyproject.toml` |
| Web app | `pnpm test:coverage` in `web/` | 752 tests in 33 files passed; statements 97.42%, branches 94.34%, functions 97.15%, lines 97.93% | 90 for each in `web/vitest.config.mts` |
| Postgres, opt-in | `FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5434/freyja_test uv run pytest tests/integration` | 127 passed | none: CI does not run them, and the service's number above leaves them out |

- The 127 tests the service suite skipped are exactly the opt-in Postgres tests:
  `tests/integration` collects 127, all 127 are skipped without `FREYJA_TEST_DATABASE_URI`,
  and the same 127 passed against `freyja_test`.
- `tests/test_thread_index_api.py::test_a_run_that_died_in_the_middle_is_stopped_once_its_lease_expired`,
  which failed once under load during the plan's commit, passed in the full run. It was not
  run alone, since it did not fail.
- The database `freyja_test` already existed in the compose Postgres on port 5434; nothing
  was created.

### Request 1: Phases 2 and 3, the service keeps progress (2026-11-25)

Built as planned: the two fields and columns, `progress_text`, the hand-off's `progress` and
`progress_seen`, the guard of `_record`, the worker's update, and the state's `progress`.
What differs from the tasks, and why:

- **A lone surrogate cannot reach `progress_text`.** A protocol message's strings are UTF-8,
  so `a2a.types` refuses to build a status that holds one (T005 asked for that case). The
  cleaning is a function of its own, `kept_progress(text)`, which `progress_text` calls,
  and the case is tested on it; the test also shows that such a status cannot be built.
- **The stand-in's `report()` returns once the status is stored.** It is called from the
  test's thread while the stand-in runs in the client's event loop. It wakes the held tasks
  through that loop and waits until each one's status is in the stand-in's task store, so
  the next read of the task shows it. `StubAgent.task(task_id)` reads that store without a
  request, for the test that counts requests (T012). A `steps` task stops at a cancel, as
  `slow` and `hold` do.
- **The README's startup paragraph changed**, though T020 said it needed no change: it said
  a task table of an earlier version gains its `agent_ref` column, and it now gains
  `progress` and `progress_at` too.
- **Tests:** T010's hand-off time is held within the call limit of the start plus one second,
  for what recording takes; T012 compares the messages the agent received without their
  ids, which hold the step's namespace and differ from run to run; T011's removed agent uses
  the helpers of `tests/test_agents_tasks.py`.
- **The quickstart walk (T021)** ran through the API rather than the console, with the
  stand-in model (`call-tools`, `handoff stub ...`) in place of a real one, and an
  environment built from `.env.example` with the check's overrides rather than a copy of
  the owner's `.env`, so no key of the owner's was copied and no provider was called. What
  it changed in the quickstart:
  - Section 1 uses `steps 6 25`, not `steps 12 10`. With a step every ten seconds each check
    found a new step, so `learned_at` moved with `last_checked_at` and the time that stays
    could not be seen. With a step every twenty-five seconds, two or three checks see each.
  - The hand-off records the step before the one that arrives at the thirtieth second: the
    call limit ends as it arrives. Section 1 now names "Step 1 of 6", and section 2 "Step 2
    of 30" (it said "Step 3 of 30"); section 2 is walked in request 2.
  - The stand-in completes right after its last step, so the kept progress of a completed
    task is the step before it ("Step 5 of 6").
  - After a restart, the checks resume once the stopped instance's claim has lapsed, up to
    thirty seconds later; a step reported and replaced meanwhile is not seen. The walk saw
    it: a task restarted at "Step 8 of 12" completed with that progress.
  - The stand-in received one request for each hand-off and one for each check, every ten
    seconds, and the service's log held no progress text and no error.

- **The review** by a reader who did not write the diff found nothing to change in the
  service. It found that the log test could not see a debug line of Freyja's: the app's
  startup raises Freyja's loggers to info after the test had lowered them, so the test now
  lowers them once the app has started, and fails, shown with a debug line added for the
  run, where it passed before. The stand-in's `report()` now registers a waiting task and
  appends a report under one lock, raises when no task is waiting, and no task sends a
  report after its cancel; `release()` is set in the stand-in's loop, as `report()` wakes
  it. The test of a row that waits for input now shows the agent was asked. The address
  sentences of `README.md` and `AGENTS.md` say that an agent's own words, its kept progress
  among them, pass as written, and that the console's runs take an address out of every
  event. Left as it is: a status in a state the protocol does not name counts as working,
  as `state_name` has always had it.

Numbers at the end of the request: `uv run pytest --cov`, 1,948 passed and 133 skipped,
91.45% of statements (91.39% at the start); the opt-in Postgres tests, 133 passed. The
only existing test changed is `tests/test_state.py`, which pins an agent task's whole shape
(FR-022).
