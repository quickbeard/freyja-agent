# Tasks: Tasks Section of the Web Console

**Input**: Design documents from `/specs/007-web-tasks/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi.yaml,
contracts/sse-events.md, quickstart.md

**Tests**: Included. FR-028 requires automated coverage of the service and of the web app with
no network, no model provider, and no agent installed or running, and an end-to-end test in a
browser for the console's part of every user story; principle V requires new behavior to ship
with tests. In each phase the test tasks come first: write them, watch them fail, then
implement. Each story's end-to-end task comes after the code it covers, since it drives the
built console.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. The foundation is the service's part and is split over two phases: the index of
threads (Phase 2), then runs without a reader and the stop (Phase 3). The web app starts in
Phase 4. Research references (R1 to R17) point at `research.md`. "The index" is `ThreadIndex`;
"a row" is a `ThreadRecord`; "a console run" is a run started through the unattended routes;
"the stand-in" is the agent in `tests/a2a_stub.py`; "a context" is a `RunContext`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1 to US6)
- Every task names the file it touches

## Path Conventions

Two projects in one repo: the service in the src layout (`src/freyja_agent/`, `tests/`,
repo-root files) and the web app under `web/` (`web/app/`, `web/components/`, `web/lib/`). Web
unit tests are `*.test.ts` and `*.test.tsx` files under `web/__tests__/`, which follows the
layout of the code it covers. End-to-end tests are `*.spec.ts` files under `web/e2e/`.

## Standing rules for every task

- **Approval.** At the end of every phase, no action that needs approval runs without an
  approve or edit decision for it. A decision from the console goes through the existing
  `resume_run`; no task copies or changes its checks. The stop route is merged in the same
  phase as the rule that it is applied only between two steps (Phase 3).
- **The other session's files are never edited**: `specs/005-*`, `specs/006-*`,
  `src/freyja_agent/delegates/`, `agent/`, `llm/`, `api/agents.py`, `api/tools.py`,
  `api/streaming.py`, `api/decisions.py`, `api/outcomes.py`, `api/serialize.py`, every file
  under `runtime/` that exists today except `runtime.py`, `web/app/agents/`,
  `web/components/agents/`, `web/lib/agents.ts`, `web/e2e/agents*.spec.ts`, `web/e2e/api.ts`,
  `tests/test_agents_*`, `tests/test_a2a_*`, `tests/a2a_stub.py`. A task that turns out to
  need one stops, and the owner is told which file and why.
- **Shared files get a small addition in a block of their own**, named under "Review focus"
  in the phase's pull request: `runtime/runtime.py`, `api/threads.py`, `api/app.py`,
  `web/lib/api.ts`, `web/components/app-sidebar.tsx` and its test, `web/e2e/console.spec.ts`,
  `tests/conftest.py`, `README.md`, `AGENTS.md`, `web/README.md`.
- **Ask the owner first**: a dependency in either lock file, a CI file, `pyproject.toml`,
  `web/vitest.config.mts`, `.gitleaks.toml`, `web/playwright.config.ts`, the constitution.
  No task here needs any of them.
- **Other origins.** The new router is created with `refuse_other_origins`, so every route
  that changes something refuses a page of another origin from the phase that adds it (R9).
- **No key and no address.** At the end of every phase from Phase 2 on,
  `tests/test_thread_index_nowhere.py` covers every route the phase added (R10).
- **The stand-ins only.** Every model in a test is the scripted model or `tests/llm_stub.py`,
  and every agent is `tests/a2a_stub.py`. A manual check never starts the service from the
  owner's `.env`; it uses a copy without `FREYJA_A2A_AGENTS`, `FREYJA_MCP_SERVERS`,
  `FREYJA_PORT`, and `FREYJA_HOST`, on port 8020 with a database of its own, and stops what
  it started by its port.
- Every resume request in a test sends the proposal's `interrupt_id` (001 FR-013).
- No new Ruff suppression; no blocking I/O in async code; SQL composed with psycopg's `sql`
  module, each statement with the `# nosemgrep` mark `AGENTS.md` describes.
- The service suite opens no socket.
- Before writing any code under `web/`, read `web/AGENTS.md` and the guide it points to in
  `web/node_modules/next/dist/docs/`. No Server Action, no Route Handler, no `use cache`, no
  `export const dynamic`, and no clock read (`Date.now()`, `new Date()`) during render.
- All text of a task is rendered as a text node. Nothing builds markup or a link from it
  (FR-012). Every control has a name that says what it does, and a status is said in words
  (FR-012a).
- Files under `web/components/ui/` are not edited by hand. Web unit tests replace `fetch`
  with a stub; no web test reaches the service. An end-to-end test hands the page the
  service's answers through `web/e2e/tasks-api.ts`; `web/e2e/api.ts` is imported, not changed.
- Before `pnpm test:e2e`, nothing listens on port 3100; if something does, wait.
- **One author per remote.** A commit pushed to GitHub is authored and committed as the
  GitHub account; the same change on GitLab as the company identity. Neither carries a
  `Co-authored-by` line for the other, and no commit, pull request, or merge request carries
  an AI attribution.
- Each phase is one pull request and one merge request, and its description has a Tests
  section (AGENTS.md). Before a phase's request is opened, its diff is read by a reviewer that
  did not write it, each finding is checked against the code before it is acted on, and each
  test added for a finding is shown to fail without the fix. Phase 1 has no change of its own
  and no request; it is done at the start of Phase 2's branch.
- A task that says "record in plan.md" writes under a heading `Implementation Notes` in
  `specs/007-web-tasks/plan.md`, creating it the first time.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: know the starting point. No file changes.

- [X] T001 Run `uv run pytest --cov` and, in `web/`, `pnpm test:coverage` on the branch as
      created from `main`, and note the two coverage numbers for the description of Phase 2's
      pull request; they are what the floors in `pyproject.toml` and `web/vitest.config.mts`
      are compared with at the end
- [X] T002 [P] Create the database of this feature's opt-in tests once, in the Postgres
      container of the main checkout (`createdb -p 5434 -U freyja freyja_test_tasks`, through
      `docker compose --project-directory /home/minhnh71/works/freyja-agent exec postgres`),
      and read `tests/integration/conftest.py` to learn how a test file there gets its pool;
      it drops every table of the test database after each test, so it needs no change

**Checkpoint**: both suites pass, unchanged.

---

## Phase 2: Foundational, part 1 - The index of threads

**Purpose**: the service knows which threads there are and where each stands, whoever ran
them. Nothing changes for a client but two new routes that read, and the refusal of other
origins on the thread routes.

**⚠️ CRITICAL**: no user story can start before Phases 2 and 3 are complete.

### Tests

- [X] T003 [P] Write `tests/test_thread_index.py`, part 1, the store, against
      `InMemoryThreadIndex` through a fixture that T007 reuses for Postgres.
      `run_started(thread_id, owner)` makes the row when it is missing, sets `run_owner`,
      counts the run in `runs`, clears `stop_owner` and `settled`, sets `run_unattended` false
      and `changed_at` to now, and leaves the note of an earlier run; `created_at` is set once.
      `mark_unattended(thread_id, first_message)` sets `run_unattended` and fills
      `first_message` only when the row has none, cut to "the first 200 characters"; it takes
      no owner, since it is called while the run holds the thread's lease.
      `note(thread_id, owner, "failed", code, message)` and `note(..., "stopped")` write only
      while `owner` is the row's `run_owner`; `error_message` is cut to "at most 2,000
      characters". `run_ended(thread_id, owner)` clears `run_owner` only when it is this
      run's, and clears `settled` on a row that has no `run_owner`. `settle(thread_id, seen_runs, settled, changed_at, first_message, agents_pending)`
      writes `paused`, `finished`, or `unfinished` and is refused, returning false, when a run
      has started since the row had `seen_runs` runs, also one that has ended again; it
      leaves `run_owner` and drops a note older than `changed_at`. Text that a text column
      does not take, a NUL character or a lone surrogate, is taken out before it is kept. `open(thread_id, ...)` makes a settled row for a thread that has
      none. `remove(thread_id)`. `request_stop(thread_id)` sets `stop_owner` to `run_owner`
      and returns the row, or None with no run; `stop_requested(thread_id, owner)` is true only
      while `stop_owner` equals that owner. `unsettled()` returns every row that ended and
      was not looked at, and every row whose `run_owner` is set. `list(limit=100)` returns every
      row with `settled = paused` first, then the others by `changed_at` descending, up to
      100 rows in all, and with 100 or more paused rows those and no other; `counts()`
      returns the number of paused rows and the total (data-model.md; FR-002, FR-005)
- [X] T004 Write `tests/test_thread_index.py`, part 2, `IndexedRunLocks`: every call is
      passed to the inner locks and its answer returned; `run_started` is written only when
      `acquire` returned true, and `run_ended` after `release`; an index whose write raises
      leaves the lease taken or released as the inner locks left it and logs a warning naming
      the thread; an attribute the wrapper does not define, such as the in-memory locks'
      `_rows`, is the inner object's; wrapping a wrapper wraps its inner locks once (R6)
- [X] T005 Write `tests/test_thread_index.py`, part 3, `status_of` and `settle_thread`:
      the six statuses in the order of data-model.md "Status", `running` by the lease alone,
      whatever the row's `run_owner`, one case per row of that table
      and one per precedence (a proposal over a noted failure; a noted failure over
      `unfinished`; `stopped` over `agents_pending`); `stoppable` and `stopping`.
      `settle_thread` reads a thread's state once through a context: `paused` when it has a
      proposal, `finished` when it has no step left to take, `unfinished` otherwise;
      `changed_at` is the last checkpoint's time; `first_message` is the first message of
      type `human`, a non-text one kept as an empty string; `agents_pending` is true when
      `AgentTaskStore.list_tasks` holds a task that is `working` or `input_required`; a
      thread with no state has its row removed (R7; FR-003, FR-008)
- [X] T006 [P] Write `tests/test_thread_index_api.py`, `GET /threads` and
      `GET /threads/{thread_id}`, on apps assembled the production way (`make_mcp_app`,
      `make_a2a_app`). Runs through the existing routes give: `done` after a reply;
      `needs_decision` after a pause, and `done` again after its approval; `stopped` for a
      run whose reader left before its end; `running` while a held model call is in
      progress, with `stoppable` false; `waiting_for_agent` after a hand-off to the stand-in
      that stays working, and `done` once the delegation worker delivered its outcome
      (`worker.tick()`); no row for a run that recorded nothing. A row whose `run_owner` is
      set and whose lease expired (advance the clock) is listed as `stopped`. A thread
      opened while a run the index did not see start is in progress is `running`, and takes
      its status from its state once that run has ended. Sixty runs that ended since the
      list was last read are all settled by one reading, and all counted. A thread that
      ran before the index existed (make its state, then empty the index) answers 200 on
      `GET /threads/{id}` and is in the list afterwards; a thread with no state answers 404
      `thread_not_found`; an id the service does not accept answers 422. The list: the order
      and the limit with seeded rows, 100 rows answered in one request, `needs_decision` counting rows beyond those listed,
      `total`, `more`, `first_message` cut to 200 characters, `error` null. The model and the
      stand-in saw no request because of either route, and no thread's state changed. An
      index whose read raises answers 500 `internal` (contracts/openapi.yaml; FR-002 to
      FR-005, FR-023, SC-009, SC-010)
- [X] T007 Write `tests/integration/test_thread_index_postgres.py`, after T003, whose
      fixture it reuses: the store cases of T003 against `PostgresThreadIndex`, `setup()` run twice, two indexes on one database
      seeing each other's writes, and a late `settle` or `note` for an earlier owner changing
      nothing
- [X] T008 [P] Write `tests/test_threads_other_origins.py`: for `POST /threads/{id}/runs` and
      `POST /threads/{id}/resume`, a request with `Sec-Fetch-Site: cross-site`, and one whose
      `Origin` differs from `X-Forwarded-Host`, is refused with 403 `other_origin` before a
      lease is taken (the thread is not `run_active`, no row is made); with `same-origin`,
      with `none`, and with none of the headers it is served as before; a body sent as
      `text/plain`, as a form, and with no content type is refused with 422. `GET /threads`,
      `GET /threads/{id}`, and `GET /threads/{id}/state` are served whatever the headers say
      and carry no `Access-Control-Allow-Origin` (R9; FR-024, FR-027, SC-007)

### Implementation

- [X] T009 Create `src/freyja_agent/runtime/thread_index.py` with `ThreadRecord` (the fields
      of data-model.md "Thread record"), the `ThreadIndex` protocol with the methods T003
      names, the constants `FIRST_MESSAGE_LIMIT = 200`, `ERROR_MESSAGE_LIMIT = 2000`, and
      `LIST_LIMIT = 100`, and `InMemoryThreadIndex`. Every write that is for one run takes
      that run's owner and changes nothing when it is not the row's `run_owner`;
      `mark_unattended` is the exception T003 names
- [X] T010 Add `PostgresThreadIndex` to `src/freyja_agent/runtime/thread_index.py`: the table
      `freyja_threads` with the columns of data-model.md and the index
      `(settled, changed_at DESC)`, made by `setup()` with `CREATE TABLE IF NOT EXISTS`; each
      method one statement, so that two instances cannot interleave inside it; the
      conditions on `run_owner` in the statement's `WHERE`
- [X] T011 Add `IndexedRunLocks` to `src/freyja_agent/runtime/thread_index.py`: it holds the
      inner `RunLocks` and the index, passes `setup`, `acquire`, `renew`, `release`, and
      `is_active` on, writes `run_started` and `run_ended` as T004 says inside a `try` that
      logs and goes on, and passes any other attribute to the inner object
- [X] T012 Add `status_of(record, lease_active)` and
      `settle_thread(index, context, agent_tasks, thread_id, seen_runs)`, with `open_thread`, to
      `src/freyja_agent/runtime/thread_index.py`, as T005 says. `settle_thread` reads the
      state with `context.agent.aget_state`, as `api/threads.py` does; `status_of` is given
      whether the thread's lease is held, read with `RunLocks.is_active`
- [X] T013 Change `src/freyja_agent/runtime/runtime.py`, in a block of its own: a field
      `threads: ThreadIndex`, defaulting to an in-memory index; `assemble` takes
      `thread_index: ThreadIndex | None = None` and wraps the locks it was given in
      `IndexedRunLocks` before the runtime is made; `open` makes a `PostgresThreadIndex` on
      the pool, runs its `setup()`, and hands it over
- [X] T014 [P] Create `src/freyja_agent/api/thread_index_schemas.py`: `ThreadStatus`,
      `ThreadSummary`, `ThreadList`, and `StopAnswer` as in contracts/openapi.yaml
- [X] T015 Create `src/freyja_agent/api/thread_index.py`: the router with the prefix
      `/threads` and `dependencies=[Depends(refuse_other_origins("Threads"))]`;
      `GET /threads` and `GET /threads/{thread_id}`. Each takes a context with
      `runtime.begin_read()` and releases it, settles every row that needs it (all of them for
      the list; the one row for the row route, which also opens a thread the index does not
      know), reads the lease of each row it returns, and answers from the index. A failure of the index is logged and
      answered with 500 `internal`
- [X] T016 Change `src/freyja_agent/api/app.py` to include the new router after the thread
      router, and `src/freyja_agent/api/threads.py` to give its router
      `dependencies=[Depends(refuse_other_origins("Threads"))]`: one line each, plus the
      imports
- [X] T017 Write `tests/test_thread_index_nowhere.py`, part 1: with a saved LLM profile whose
      key is recognizable (a value `.gitleaks.toml` lets through) and a saved agent on a
      recognizable port, run threads that finish, pause, and fail with a model error that
      repeats the key, and search every answer of `GET /threads` and `GET /threads/{id}` and
      the log for the key and, with `holds_port`, for the port. Reuse the helpers of
      `tests/test_llm_secrets_nowhere.py` and `tests/test_agents_address_nowhere.py` by
      import; change neither file (R10; FR-025, SC-008)
- [X] T018 Add to `README.md` ("Use the API") and `AGENTS.md` (Commands, Layout, Conventions)
      paragraphs of this feature's own for what Phase 2 added: the two routes, the table, the
      lease wrapper, the refusal of other origins on the thread routes, and the opt-in test
      database. Record in plan.md what was built differently from the plan

**Checkpoint**: `uv run pytest --cov` passes with every earlier test unchanged; the opt-in
tests pass against `freyja_test_tasks`; `curl localhost:8020/threads` on a check instance
lists a thread started with curl.

---

## Phase 3: Foundational, part 2 - Runs without a reader, and the stop

**Purpose**: a run can be started so that it goes on when its reader leaves, and such a run
can be stopped between two steps. The existing routes are untouched.

### Tests

- [X] T019 [P] Write `tests/test_segmented_graph.py`, with the real factory and scripted
      models, a plain run beside one through `SegmentedGraph` for a thread that is armed: the
      same messages, the same number of steps, the same `next`, and the same pause, for a
      script with single tool calls, for one with three tool calls in one reply, through a
      pause on `send_notification` and its approval to the end, and for a proposal raised
      inside the subagent; the same events, compared without regard to the order of results
      of actions that ran at the same time. A thread that is not armed is passed to the real
      `astream` with the arguments given, and yields what it yields. An error the graph
      raises comes out unchanged. With `recursion_limit` 9, the segmented run raises
      `GraphRecursionError` at a boundary, having taken no more steps than the plain one
      and at most two fewer. A
      graph without a `model` node, or without a `tools` node, is refused with an error that
      names the node (R4; FR-021, FR-027, SC-011)
- [X] T020 [P] Write `tests/test_unattended_runs.py`, part 1, the two routes beside the
      existing ones, each case run through both: the same 422 for an empty message and a
      message of 32,001 characters, 409 `run_active`, 409 `decision_pending`, 404
      `thread_not_found`, 409 `nothing_pending`, 409 `proposal_mismatch`, 422 for an edit
      the action cannot accept, and the same events for a run that completes, one that
      pauses, and each of approve, edit, and reject. On the unattended routes only: the
      answer has `Cache-Control: no-store, no-transform` and a ping interval of 10 seconds;
      the row has `run_unattended` true and `stoppable` true while the run is in progress;
      `first_message` is the request's; the log says the run goes on without a reader; a
      request to either route that is marked as made by a page of another origin is refused
      with 403 `other_origin` and takes no lease. A
      test pins that the handlers of `api/threads.py` return a response whose
      `body_iterator` is an async iterator of events (R1, R3; FR-021, FR-024, FR-027, SC-002, SC-003, SC-005, SC-007)
- [X] T021 Write `tests/test_unattended_runs.py`, part 2, readers: a reader that leaves
      at the first `update` of a five-step script, and a request nobody reads at all, each
      end with the model's last reply in the thread's state, the lease released, and the
      context released; the same script through `POST /threads/{id}/runs` with the same
      reader makes no model call after the reader left. A run that ends with `error` leaves
      a row with `status` `failed` and the event's `code` and `message`, for a model error
      and for `no_usable_model` on a thread that has a state; `no_usable_model` on a new
      thread leaves no row. A reader with 1,000 events waiting is disconnected and the run
      goes on. A proposal raised by a run nobody reads is counted in `needs_decision`,
      and a second runtime made on the same saver, locks, and index, as after a restart,
      lists it and continues it through `resume/unattended`. An index whose
      `mark_unattended` or `note` raises: the run goes on to the same end, the failure is
      logged, and the row is not `stoppable`
      (R2, R3, R7; FR-003, FR-008, FR-010, FR-021, FR-023, SC-006, SC-011)
- [X] T022 [P] Write `tests/test_unattended_stop.py`, `POST /threads/{thread_id}/stop`. A
      stop asked during a model call: that reply is recorded, the tool it asked for does not
      run, the stream ends with `end` and `stopped`, the row is `stopping` and then
      `stopped`, and a new message runs on the thread. A stop asked during a tool step: the
      tool finishes, its result is in the state, and no model call follows. A stop asked
      while an approved `send_notification` is under way: the action ran once, by the log,
      and its result is in the state. A stop applied after an approval and before the action
      started: the action did not run, nothing is pending, and a new message runs. A stop
      asked twice answers 202 both times. A stop accepted while the reply under way then
      asks for an action that needs approval: the row is `needs_decision`, never `stopped`;
      the same for a run that finishes and for one that fails before the stop is applied:
      `done` and `failed`. An index whose `stop_requested` raises between two steps: the run
      goes on as if no stop had been asked, and the failure is logged.
      409 `no_run` with no run in progress and for a paused thread; 409 `not_stoppable` for
      a run through `POST /threads/{id}/runs` and for a background run of the delegation
      worker; 404 for a thread the index does not know; 403 `other_origin` for a request
      marked as made by a page of another origin, with no body, and the run goes on. Two
      runtimes that share the saver, the locks, and the index: a stop asked of the second
      ends the run on the first. With `FREYJA_MAX_STEPS` 9 a console run ends with
      `step_limit` and at most 9 steps. The log names each stop that was accepted and each
      run that ended because of one (R4, R5; FR-022, FR-022a, FR-023, FR-024, SC-007, SC-012)
- [X] T023 Write `tests/test_unattended_runs.py`, part 3, the service stopping: leaving
      the runtime while a console run is in a held tool step waits for the step, which is
      recorded; no further step starts; the lease is released; the thread is `stopped`; a
      reader whose stream was cancelled first, as the server does when it stops, changes
      none of it
      (R17; FR-021)
- [X] T024 [P] Extend `tests/test_thread_index_nowhere.py`, part 2: console runs that
      finish, pause, fail with an error that repeats the key, and are stopped; search every
      answer and stream of the three new routes, the rows, and the log (FR-025, SC-008)
- [X] T025 [P] Extend `tests/integration/test_thread_index_postgres.py`: two runtimes opened
      on one database, a console run on the first stopped through the second

### Implementation

- [X] T026 Create `src/freyja_agent/runtime/unattended.py` with `SegmentedGraph`: it holds the
      real graph and an object that answers `armed(thread_id)` and
      `should_stop(thread_id)`; `astream(input, config, **kwargs)` passes an unarmed thread
      straight through; for an armed one it runs segments with
      `interrupt_before=["model", "tools"]`, gives each segment what is left of
      `config["recursion_limit"]` by the thread's last checkpoint `step`, raises
      `GraphRecursionError` when less than 1 is left, drops the root `updates` chunk whose
      `__interrupt__` is empty, and between two segments asks `should_stop` and returns when
      it says so; every other attribute is the real graph's; the constructor refuses a graph
      without both nodes (R4)
- [X] T027 Add `UnattendedRuns` to `src/freyja_agent/runtime/unattended.py`:
      `start(thread_id, events)` arms the thread and drives the events in a task, and takes
      the run's lease owner from the `run_id` of its first event, `start`, with
      `lease_owner`;
      `subscribe(thread_id)` gives a reader its events, dropped when it leaves or when
      "1,000 events" wait for it; `should_stop` reads the index's stop mark for the run and
      the object's own closing flag, and remembers that it said yes; at the run's last event
      it notes `failed` with the code and message of an `error`, replaces `end` by
      `{"status": "stopped"}` and notes `stopped` when a stop was applied, and logs it;
      `close()` sets the closing flag and waits for the runs. A call of the index that raises,
      in `note` or in the read of a stop, is logged and treated as nothing noted and no stop
      asked (R2, R3, R5, R17; FR-023)
- [X] T028 Change `src/freyja_agent/runtime/runtime.py`, in the block T013 made: a field
      `unattended: UnattendedRuns`, made in `assemble` after the runtime and closed in its
      `finally` before the worker stops and the registry closes; `begin_run` returns the
      context with `agent` wrapped in `SegmentedGraph`; `assemble` fails at startup when the
      graph lacks a node the wrapper needs
- [X] T029 Add to `src/freyja_agent/api/thread_index.py`:
      `POST /threads/{thread_id}/runs/unattended` and
      `POST /threads/{thread_id}/resume/unattended`, each declaring the path, body, and
      query types of the existing route, calling `threads.start_run` or `threads.resume_run`,
      taking `body_iterator` from the response, marking the row unattended (a failure of
      that write is logged and the run is driven all the same), logging that the run goes on
      without a reader, handing the events to `runtime.unattended`, and answering with an `EventSourceResponse` of the
      reader's events with `Cache-Control: no-store, no-transform` and `ping=10`; and
      `POST /threads/{thread_id}/stop`, which answers 202, 404, or 409 as the contract says and logs each stop it accepts
      (R3, R5; contracts/openapi.yaml, contracts/sse-events.md)
- [X] T030 Add to `README.md` and `AGENTS.md` what Phase 3 added: the three routes, what
      differs in their stream, the stop's rule, that an ordinary stop of the service waits
      for a console run's step under way, and `unattended.py` in the Layout. Correct the
      README's sentence that a clean stop waits for in-flight runs: with the installed
      versions it ends every open stream at once (research R17); name the correction under
      "Review focus", since the sentence is feature 001's. Record in
      plan.md what was built differently from the plan

**Checkpoint**: `uv run pytest --cov` passes with every earlier test unchanged; quickstart
section 5's curl commands and section 6 work on a check instance; with the console built
against it, quickstart section 7 shows `no-transform` and no `content-encoding`.

---

## Phase 4: User Story 1 - Hand Freyja a task and read the result (Priority: P1) 🎯 MVP

**Goal**: a person opens Tasks, sends a new task, sees the reply as it is written on the
task's page, and reads the conversation again later.

**Independent Test**: quickstart section 1.

### Tests for User Story 1

- [X] T031 [P] [US1] Extend `web/__tests__/lib/api.test.ts`: `stream(path, body)` posts JSON
      to `/api<path>`, returns the response's body for a 200, and raises the same `ApiError`
      as `api` for a refusal, for refused values, and for no answer
- [X] T032 [P] [US1] Write `web/__tests__/lib/tasks.test.ts`, part 1: the stream parser
      yields `{event, data}` for each event, with an event split over two chunks, two events
      in one chunk, a `: ping` line, and a last event with no blank line after it;
      `newThreadId()` gives 32 characters the service's rule accepts and never the same
      twice; `listThreads`, `readThread`, and `readState` call `/api/threads`,
      `/api/threads/<id>`, and `/api/threads/<id>/state`; `startRun(id, message)` posts to
      `/api/threads/<id>/runs/unattended`; every call of the module goes through `api` or
      `stream` to a path under `/threads`, and none carries a credential (FR-026)
- [X] T033 [P] [US1] Write `web/__tests__/lib/task-runs.test.ts`: `begin(threadId, message, body)`
      holds the message the run was started with and the events as they arrive, and tells
      its subscribers; the run ends with its last
      event, or as "connection ended" when the body ends without `end`, `interrupt`, or
      `error`; `get(threadId)` gives the run or nothing; `clear(threadId)` forgets it; a
      second `begin` for a thread whose run has not ended is refused
- [X] T034 [P] [US1] Write `web/__tests__/components/tasks/new-task.test.tsx`,
      `message.test.tsx`, `status.test.tsx`, and `task-view.test.tsx`, part 1. New task: one
      empty text box each time it opens; an empty text cannot be sent; two presses of Send
      start one run; a refusal shows the service's reason and keeps the text; no answer says
      the service cannot be reached and keeps the text; on a 200 the page goes to
      `/tasks/<id>`. A message: a person's, Freyja's, and an action with its arguments and
      result, each as text, with markup and a link shown as characters. The status: each of
      the six in words. The view: the run is shown as started when its `start` event arrives; the reply grows
      as events arrive; when the run ends the
      conversation is the state's; an `error` shows the service's message and, for
      `no_usable_model`, a link to `/llm`; a stream that ends without its last event says the
      connection ended and does not say the run failed; a thread with no state and no run in the store is "not
      found", and so is an id the service answers 422 for; a thread with no state whose run
      in the store ended with `error` shows that error, with the link for
      `no_usable_model`, and a box holding the message the run was started with, which
      sends it again on the same id (FR-007 to FR-012a, SC-001; R12)
- [X] T035 [P] [US1] Write `web/__tests__/app/tasks/page.test.tsx` and
      `web/__tests__/app/tasks/task-page.test.tsx`: the list's page has the heading, the
      notice that the console has no users, and New task; the task's page hands the id from
      its address to the view
- [X] T036 [P] [US1] Extend `web/__tests__/components/app-sidebar.test.tsx`: the sidebar
      lists LLM, Agents, and Tasks, and Tasks is current on `/tasks` and on `/tasks/<id>`.
      This changes the existing assertion on the list of sections, which FR-027 allows
      (FR-001)

### Implementation for User Story 1

- [X] T037 [US1] Add `stream` to `web/lib/api.ts`, in a block of its own beside `api`,
      sharing its refusal handling
- [X] T038 [P] [US1] Create `web/lib/tasks.ts`: the types of contracts/openapi.yaml and of
      the thread state, the parser, `newThreadId`, `listThreads`, `readThread`, `readState`,
      `startRun`, and `THREADS_KEY`
- [X] T039 [P] [US1] Create `web/lib/task-runs.ts`: the store of T033, read by components
      with `useSyncExternalStore`
- [X] T040 [US1] Create `web/components/tasks/status.tsx`, `message.tsx`, `task-view.tsx`,
      and `new-task.tsx` as T034 says; the view reads the row and the state with SWR and
      reads them again once when a run it shows ends; for a run that ended with `error` on a
      thread with no state it offers the stored message again (R12)
- [X] T041 [US1] Create `web/app/tasks/page.tsx` and `web/app/tasks/[id]/page.tsx`; the
      second reads its route parameter inside `<Suspense>` (R13)
- [X] T042 [US1] Add the Tasks entry to `web/components/app-sidebar.tsx`, without its number
- [X] T043 [US1] Create `web/e2e/tasks-api.ts` (`answerThreads` for the list, `TaskService`
      for a page that starts runs, which keeps what the page sent and answers a run with a
      whole stream) and `web/e2e/tasks.spec.ts`, part 1: a new task from the list's page to a
      finished conversation; reload shows it again; a refused message keeps its text; a
      new task sent while no usable model is active shows the reason and offers the text
      again; an address with an id the service refuses shows "not found"; a
      stopped service; text with markup shown as characters. Add the Tasks entry to the
      sidebar test of `web/e2e/console.spec.ts`
- [X] T044 [US1] Add the Tasks section to `web/README.md`, `README.md` ("Web console"), and
      `AGENTS.md` (Layout), as far as this phase built it, with the sentence that the console
      has no users (FR-026a)

**Checkpoint**: quickstart section 1 works, apart from the sentence that there is no task
yet and the `GET /threads` check, which are the list's and come with Phase 6.

---

## Phase 5: User Story 2 - Decide an action that needs approval (Priority: P2)

**Goal**: a paused task shows a card for its proposal; the person approves, edits and
approves, or rejects, and the continuation shows on the same page.

**Independent Test**: quickstart section 2.

### Tests for User Story 2

- [X] T045 [P] [US2] Extend `web/__tests__/lib/tasks.test.ts`, part 2:
      `decide(id, interruptId, decisions)` posts to `/api/threads/<id>/resume/unattended`
      with `interrupt_id` and the decisions in order; `parseArguments(text)` gives the object
      for text that is a JSON object and a reason for anything else, an array and a number
      included
- [X] T046 [P] [US2] Write `web/__tests__/components/tasks/proposal-card.test.tsx`: each
      action's name, server or agent, description, and arguments in full as text; only the
      decisions in the action's `allowed_decisions` are offered; Approve sends one approve;
      Edit opens the arguments as text, text that is not a JSON object is not sent and the
      card says so, and an edit is sent as entered; Reject sends the reason, at most 4,000
      characters, or none; with two actions nothing is sent until each has a decision, and
      the decisions go in the service's order; Enter in a field sends nothing; a control
      pressed twice sends once; a 422 shows the service's reason beside the edit, which
      keeps its text; `proposal_mismatch`, `nothing_pending`, and `run_active` say so and
      read the task again; no answer says the outcome is not known and reads the task again;
      every control's name says its action (FR-014 to FR-019, FR-012a)
- [X] T047 [P] [US2] Extend `web/__tests__/components/tasks/task-view.test.tsx`, part 2: a
      state with `pending` shows the card and no follow-up; after a decision is accepted the
      continuation is shown as a run is, also when it pauses again

### Implementation for User Story 2

- [X] T048 [US2] Add `decide` and `parseArguments` to `web/lib/tasks.ts`
- [X] T049 [US2] Create `web/components/tasks/proposal-card.tsx` and show it in
      `web/components/tasks/task-view.tsx`; its buttons stand outside any form
- [X] T050 [US2] Extend `web/e2e/tasks-api.ts` (a proposal, and what each decision answers)
      and write `web/e2e/tasks-decide.spec.ts`: approve, edit and approve, reject with a
      reason, an edit the service refuses, text that is not JSON, a proposal decided
      elsewhere, two actions in one proposal, Enter in a field, and a decision made with the
      keyboard alone (FR-012a)
- [X] T051 [US2] Add the proposal card to `web/README.md` and `README.md`

**Checkpoint**: quickstart section 2 works.

---

## Phase 6: User Story 3 - See every task and which ones wait for a decision (Priority: P3)

**Goal**: the list shows one row per task with the ones that need a decision first, and the
sidebar says how many wait, from every section.

**Independent Test**: quickstart section 3.

### Tests for User Story 3

- [X] T052 [P] [US3] Write `web/__tests__/components/tasks/task-list.test.tsx`: one row per
      thread with the beginning of its first message as text, its status in words, and when
      it last changed; each row opens `/tasks/<id>`; the rows in the service's order; with
      none it says so; `more` true says that more tasks exist; a failed reading says the
      service cannot be reached, offers to try again, and shows no row; the list is read
      again every 15 seconds
- [X] T053 [P] [US3] Extend `web/__tests__/components/app-sidebar.test.tsx`: the Tasks entry
      shows `needs_decision` with words for whoever does not see the number ("1 task needs a
      decision", "3 tasks need a decision"); no number for zero, before the first answer,
      and after a failed reading; the number follows a later reading; on a section other than Tasks, with
      fake timers, the sidebar reads the list again every 15 seconds by itself; the list and
      the sidebar ask `/api/threads` once between them (FR-001, FR-006, SC-004)
- [X] T054 [P] [US3] Extend `web/__tests__/app/tasks/page.test.tsx`: the list stands under
      the notice

### Implementation for User Story 3

- [X] T055 [US3] Create `web/components/tasks/task-list.tsx`, reading `GET /threads` under
      `THREADS_KEY` with a refresh every 15 seconds and no retry behind the person's back,
      and show it in `web/app/tasks/page.tsx`
- [X] T056 [US3] Add the number to the Tasks entry in `web/components/app-sidebar.tsx`, read
      under the same key, with a refresh every 15 seconds of the sidebar's own, since on
      another section it is the only reader, and no retry behind the person's back
- [X] T057 [US3] Extend `web/e2e/tasks.spec.ts`, part 2: four tasks in four statuses in
      order; the sidebar's number on the LLM section; the number gone after a decision
      without a reload; a stopped service; a thread started by another client as a row. It follows T050, whose
      helper answers a decision. Then run the whole end-to-end suite, in both browsers: from
      this phase on every page asks for `/api/threads`, and every existing spec, which hands
      its page no answer for it, must still pass; if one does not, stop and tell the owner
      which, since those specs are not this feature's to edit (R15)
- [X] T058 [US3] Add the list and the number to `web/README.md` and `README.md`

**Checkpoint**: quickstart section 3 works.

---

## Phase 7: User Story 4 - Continue a task (Priority: P4)

**Goal**: a follow-up continues a task; agent tasks are listed; a page follows a task whose
run it is not reading.

**Independent Test**: quickstart section 4.

### Tests for User Story 4

- [X] T059 [P] [US4] Write `web/__tests__/components/tasks/follow-up.test.tsx` and
      `agent-tasks.test.tsx`: the box sends a message on the same thread and shows the run;
      it cannot be sent, and says why, while a run is in progress and while a proposal is
      pending; a refusal keeps the text; each agent task with its agent, its status in
      words, and its message as text, the pending ones marked, and the word "agent task",
      never "task" alone (FR-001a, FR-009, FR-020)
- [X] T060 [P] [US4] Extend `web/__tests__/components/tasks/task-view.test.tsx`, part 3: a
      task that is `running` with no run in this tab's store says a run is in progress,
      offers no message and no decision, and reads the row and the state again every 5
      seconds until the run has ended; a task with a pending agent task reads them again
      every 15 seconds; neither reads again once nothing is pending (FR-013)

### Implementation for User Story 4

- [X] T061 [US4] Create `web/components/tasks/follow-up.tsx` and `agent-tasks.tsx` and show
      them in `web/components/tasks/task-view.tsx`, with the refresh intervals of T060
- [X] T062 [US4] Write `web/e2e/tasks-run.spec.ts`, part 1, with `web/e2e/tasks-api.ts`
      extended: a follow-up on a finished task; the box on a paused task; a task that waits
      for an agent and then shows its outcome without a reload; a task whose run is in
      progress elsewhere
- [X] T063 [US4] Add follow-ups and agent tasks to `web/README.md` and `README.md`

**Checkpoint**: quickstart section 4 works with the stand-in agent.

---

## Phase 8: User Story 5 - Leave a task running, come back to it, or stop it (Priority: P5)

**Goal**: closing the page changes nothing about a run; a page opened later shows where the
task stands; Stop ends a run after the step under way.

**Independent Test**: quickstart section 5.

### Tests for User Story 5

- [X] T064 [P] [US5] Extend `web/__tests__/lib/tasks.test.ts`, part 3: `stopRun(id)` posts to
      `/api/threads/<id>/stop` with no body
- [X] T065 [P] [US5] Write `web/__tests__/components/tasks/stop-button.test.tsx`: Stop is
      shown only while the row is `stoppable`; one press sends one request, and a second
      press none; while `stopping` the page says the run is stopping and offers no Stop; a
      run in progress that is not stoppable shows no Stop and says why; `no_run` and
      `not_stoppable` say so and read the task again; the control is a button outside any
      form and is named "Stop" with the task it stops (FR-022, FR-022a, FR-012a)
- [X] T066 [P] [US5] Extend `web/__tests__/components/tasks/task-view.test.tsx`, part 4: a
      stream that ends with `end` and `stopped` shows the task as stopped with what the
      state holds and offers the follow-up; a page that asked for a stop and then reads a
      task that needs a decision, is done, or failed shows it as it is and says the run
      ended before the stop took effect; leaving the view while a run streams sends nothing

### Implementation for User Story 5

- [X] T067 [US5] Add `stopRun` to `web/lib/tasks.ts`; create
      `web/components/tasks/stop-button.tsx` and show it in
      `web/components/tasks/task-view.tsx`, with the messages of T066
- [X] T068 [US5] Extend `web/e2e/tasks-run.spec.ts`, part 2: a page closed during a run and
      opened again shows the finished task; a task found paused from the sidebar after its
      page was closed; Stop to stopped and a follow-up after it; a run that cannot be
      stopped; a stop that came too late; Stop pressed with the keyboard alone (FR-012a)
- [X] T069 [US5] Add leaving, coming back, and Stop to `web/README.md` and `README.md`

**Checkpoint**: quickstart section 5 works: a tab closed during a run, and Stop.

---

## Phase 9: User Story 6 - Another site cannot hand Freyja work or decide for the person (Priority: P6)

**Goal**: what the service refuses a page of another origin holds through the console, and
the section cannot be shown in another origin's frame.

**Independent Test**: quickstart section 6.

### Tests for User Story 6

- [X] T070 [P] [US6] Extend `tests/test_threads_other_origins.py`, part 2: every route of
      contracts/openapi.yaml that changes something, with each of the two headers, is
      refused before a lease is taken or a stop is marked; the refusal's sentence names
      threads and repeats nothing that was sent (FR-024, SC-007)
- [X] T071 [P] [US6] Extend `web/e2e/tasks.spec.ts`, part 3: a page of another origin that
      holds `/tasks` and `/tasks/<id>` in a frame shows nothing of the console, in both
      browsers, by the headers the console sends for every path

### Implementation for User Story 6

- [X] T072 [US6] Fix what T070 and T071 find; none is expected, since the router was made
      with the refusal in Phase 2 and `web/next.config.ts` sends the frame headers for every
      path. Say in `README.md` ("Web console") what a page of another origin cannot do with
      tasks

**Checkpoint**: quickstart section 6 works.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [X] T073 Walk through `specs/007-web-tasks/quickstart.md` from the first section to the
      last on a check instance (port 8020, a database `freyja_check_tasks` dropped
      afterwards, the stand-in agent on 9978, the console on 3020), with a model that calls
      tools, and correct the quickstart where the built feature differs. Time two things by
      hand, since no test does: the page showing that a run started within two seconds of
      sending (SC-001), and the list with 100 threads within two seconds (SC-009)
- [X] T074 [P] Read `README.md`, `AGENTS.md`, and `web/README.md` once more against the
      code: they describe only what exists, name every new file in the Layout, and say that
      the console has no users
- [X] T075 [P] Compare the two coverage numbers with T001's. Raise `fail_under` in
      `pyproject.toml` or a threshold in `web/vitest.config.mts` only with the owner's
      word, since both files are on the list to ask about; say in the pull request what the
      Postgres index, which CI does not run, does to the service's number
- [X] T076 [P] Say in the last pull request, and record in plan.md, that
      `tests/test_thread_index_nowhere.py` has to be joined with
      `tests/test_llm_secrets_nowhere.py` and `tests/test_agents_address_nowhere.py` once
      feature 005 is done, and that an automated browser proof of a stream arriving as it is
      produced still waits for the owner's answer about `web/playwright.config.ts` (R15)
- [X] T077 Run every gate once more on the finished branch: the commit hook over all files,
      `pnpm test:e2e` in both browsers, the opt-in Postgres tests against
      `freyja_test_tasks`, Semgrep, and Trivy as `AGENTS.md` gives them

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational, part 1 (Phase 2)**: depends on Phase 1.
- **Foundational, part 2 (Phase 3)**: depends on Phase 2: the stop is a mark in the index,
  and a console run is told from another by its row. Blocks every user story.
- **User Story 1 (Phase 4)**: depends on Phase 3. The MVP.
- **User Story 2 (Phase 5)**: depends on Phase 4: the card stands on the task's page.
- **User Story 3 (Phase 6)**: depends on Phase 4 for the page and the library module. Its
  unit tasks, T052 to T056, do not depend on Phase 5. Its end-to-end task, T057, decides a
  proposal and follows T050.
- **User Story 4 (Phase 7)**: depends on Phase 5, since the follow-up box and the card share
  the rule for a pending proposal.
- **User Story 5 (Phase 8)**: depends on Phase 7: a page that follows a run by reading is
  Phase 7's.
- **User Story 6 (Phase 9)**: depends on Phases 2 and 3 for its service part and on Phase 4
  for the pages.
- **Polish (Phase 10)**: depends on all stories.

### Within Phase 2

- T003, T004, and T005 share a file and are written in that order. T006 and T008 depend on
  none of them. T007 follows T003, whose fixture it reuses.
- T009 before T010, T011, and T012 (same file, in that order). T013 after T011. T014 beside
  them. T015 after T012, T013, and T014. T016 after T015. T017 after T016. T018 last.

### Within Phase 3

- T020, T021, and T023 share a file and are written in that order. T019, T022, T024, and
  T025 depend on none of them.
- T026 before T027 (same file). T028 after T027. T029 after T028. T030 last.

### Within Each User Story

- Tests first, and they must fail before implementation starts.
- Tasks that share a file run in the order listed. `web/lib/tasks.ts` grows in T038, T048,
  and T067, and its test file in T032, T045, and T064; `task-view.tsx` in T040, T049, T061,
  and T067, and its test file in T034, T047, T060, and T066; `app-sidebar.tsx` in T042 and
  T056, and its test file in T036 and T053; `web/e2e/tasks-api.ts` in T043, T050, T057,
  T062, and T068; `web/e2e/tasks.spec.ts` in T043, T057, and T071; `tasks-run.spec.ts` in
  T062 and T068; `api/thread_index.py` in T015 and T029; `runtime/thread_index.py` in T009
  to T012; `runtime/unattended.py` in T026 and T027; `runtime/runtime.py` in T013 and T028;
  `tests/test_thread_index_nowhere.py` in T017 and T024; `README.md` in T018, T030, T044,
  T051, T058, T063, T069, T072, and T074.
- A story's end-to-end task (T043, T050, T057, T062, T068, T071) comes after its
  implementation: it drives the built console.

### Parallel Opportunities

- Phase 2: T006 and T008 beside T003 to T005; T014 beside T009 to T013.
- Phase 3: T019, T022, T024, and T025 beside T020, T021, and T023.
- Phase 4: T031 to T036 together; T038 and T039 beside T037.
- Phase 5: T045, T046, and T047 together.
- Phase 6: T052, T053, and T054 together. T052 to T056 beside Phase 5, by a second
  person.
- Phase 7: T059 and T060 together.
- Phase 8: T064, T065, and T066 together.
- Phase 9: T070 and T071 together.
- Phase 10: T074, T075, and T076 together.

---

## Parallel Example: User Story 1

```text
# The tests of User Story 1, each in a file of its own:
T031 web/__tests__/lib/api.test.ts
T032 web/__tests__/lib/tasks.test.ts
T033 web/__tests__/lib/task-runs.test.ts
T034 web/__tests__/components/tasks/{new-task,message,status,task-view}.test.tsx
T035 web/__tests__/app/tasks/{page,task-page}.test.tsx
T036 web/__tests__/components/app-sidebar.test.tsx

# Then the two library modules beside the change to api.ts:
T037 web/lib/api.ts
T038 web/lib/tasks.ts
T039 web/lib/task-runs.ts
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1, then Phases 2 and 3: the service's part. After Phase 3 a console run can be
   started, read, left, and stopped with curl.
2. Phase 4: the Tasks entry, New task, and a task's page.
3. **Stop and validate**: quickstart section 1. A task that needs no approval can be entered,
   watched, and read. One that pauses shows as paused and is decided with curl until Phase 5.

### Incremental Delivery

Each phase after the third adds to the section without changing what the one before built:

1. Phase 5: the card. The section is usable for real work from here on.
2. Phase 6: the list and the sidebar's number. Before it, a task is found again by its
   address only.
3. Phase 7: follow-ups, agent tasks, and a page that follows a run it is not reading.
4. Phase 8: Stop, and what a page says after it. Leaving a run already changes nothing from
   Phase 4 on, since the page starts every run as a console run.
5. Phases 9 and 10: the checks from another origin, the documents, and the gates. They are
   small; whether they share one pull request is the owner's choice.

### Parallel Team Strategy

One person builds this feature, beside another session that builds features 005 and 006 on
the same `main`. Fetch both remotes and rebase before every pull request. The files the two
share are named in the standing rules; a conflict in a document keeps both sides.

---

## Notes

- 77 tasks: 2 of setup, 28 for the service's foundation (16 and 12), 14 for User Story 1, 7
  for User Story 2, 7 for User Story 3, 5 for User Story 4, 6 for User Story 5, 3 for User
  Story 6, and 5 of polish.
- `[P]` tasks touch different files and depend on nothing unfinished. Tasks that share a
  file carry no `[P]` within a phase, whatever their order.
- A test task lists the cases it must hold; a case that cannot be written as listed is
  recorded in plan.md with the reason, not dropped.
- Commit after each task or each small group of tasks; the commit hook runs the suites.
- Stop at any checkpoint to validate the phase by hand before its pull request.

---

## Phase 11: Convergence

Appended by `/speckit-converge` on 2026-11-21, after Phase 10: what the code does not yet
do of what spec.md, plan.md, and the tasks above ask, found by reading every requirement,
acceptance scenario, edge case, and the contract against the code. No finding is critical
or high: T078 to T081 are medium, the rest low. The standing rules above hold for each
task: its test first, shown to fail.

- [X] T078 Show the service's reason on a failed task's row in
      `web/components/tasks/task-list.tsx`: the row's `error.message`, as plain text, with
      a unit test in `web/__tests__/components/tasks/task-list.test.tsx` and an assertion in
      `web/e2e/tasks.spec.ts`. Today only the task's page gives it, per FR-003 (missing)
- [X] T079 Add two browser tests to `web/e2e/stream.spec.ts` that press Stop on the page
      that started the run and is reading its stream: one where the stand-in then ends the
      run as stopped (the page says "stopping", then shows Stopped), and one where the run
      ends another way after the press (the page says the stop came too late). Every Stop
      test in `web/e2e/tasks-run.spec.ts` follows a run by reading, per FR-028, FR-022,
      US5/AC4 (partial)
- [X] T080 Keep the numbers of an edit as the person entered them, in `web/lib/tasks.ts`
      (`parseArguments`) and `web/components/tasks/proposal-card.tsx`: text is read with
      `JSON.parse`, which rounds an integer beyond 2^53, such as a 64-bit id, so an edit of
      another argument sends a value nobody proposed or entered. Refuse such text beside the
      edit, say on the card when a proposed argument holds such a number, and test both with
      a 19-digit integer, per FR-015, FR-014 (partial)
- [X] T081 Ask the owner before anything is changed: the Agents section calls an agent task
      a "pending task" and a task a "thread" (`web/components/agents/agent-card.tsx`,
      `agent-list.tsx`, `agent-form.tsx`, `remove-dialog.tsx`, `web/lib/agents.ts`), where
      the console is to say "agent task" and "task" in every place it names either. Those
      files are feature 005's. Either they are reworded there, or spec.md narrows the rule
      to the Tasks section, per FR-001a (contradicts)
- [X] T082 Have a row name its run: add the number of runs the thread has had to
      `ThreadSummary` and to the answer of `POST /threads/{thread_id}/stop`
      (`src/freyja_agent/api/thread_index_schemas.py`, `api/thread_index.py`,
      `contracts/openapi.yaml`, `web/lib/tasks.ts`), and have
      `web/components/tasks/task-view.tsx` say that a stop came too late only when a reading
      of the same run shows it ended another way. Today a page that follows a run by
      reading cannot tell that from a stopped run followed by another, per FR-022a (partial)
- [X] T083 Bound how long the console waits for an answer: a time limit in `api()` and, until
      the answer begins, in `stream()` (`web/lib/api.ts`, a shared file), turned into the
      error a request with no answer already gets. First a test of what a page shows when the
      service accepts a request and does not answer: the list and the sidebar's number not
      shown as current, and a decision's outcome said to be not known, per FR-006, FR-018
      (partial)
- [X] T084 Shorten the three periods so that each bound holds with the request's round
      trip: `LIST_SECONDS` in `web/lib/tasks.ts` and `FOLLOW_SECONDS` and `AGENT_SECONDS` in
      `web/components/tasks/task-view.tsx` equal the fifteen, five, and fifteen seconds the
      spec gives as the most. Add a browser test with `page.clock` in which the sidebar's
      number appears on `/llm` by the timer alone, per SC-004, FR-006, FR-013, US4/AC4
      (partial)
- [X] T085 Say on the sidebar's Tasks entry, in words, that the number is not known while
      the list cannot be read (`web/components/app-sidebar.tsx` and its test, shared
      files): it shows nothing then, as it does when no task needs a decision, per Edge
      Cases "The list cannot be kept or read", FR-006 (partial)
- [X] T086 Take a row's first message only from a thread's first message: in
      `src/freyja_agent/runtime/thread_index.py` (`mark_unattended`, both stores), a thread
      that has no row yet and is continued through `/runs/unattended` by another client is
      listed under the follow-up's text from then on. Tests in `tests/test_thread_index.py`
      and `tests/integration/`, per FR-002, FR-004 (partial)
- [X] T087 [P] In `tests/test_unattended_runs.py`, leave the stream of a
      `resume/unattended` as the test of a run does, and compare the whole conversation of a
      run whose reader left with one whose reader stayed, not its last message alone, per
      SC-011, US5/AC1, FR-028 (partial)
- [X] T088 [P] Add a task that fails to both address proofs of
      `tests/test_thread_index_nowhere.py`, which go through tasks that finish and pause
      only, per SC-008 (partial)
- [X] T089 [P] In `tests/test_unattended_stop.py`, assert that after a stop applied before
      an approved action started, the conversation of the continued task shows the action
      as not carried out, per Edge Cases "Stop is applied before an action that was just
      approved has started" (partial)
- [X] T090 [P] Test that a second message is refused with `run_active` while a run nobody
      reads is in progress (`tests/test_unattended_runs.py`), and that reading the list
      contacts no tool server (`tests/test_thread_index_api.py`, which asserts the model and
      the agent only), per FR-021, FR-023 (partial)
- [X] T091 [P] Test that after an edit the conversation shows what was proposed and that a
      person changed it: in the service's tests of a console decision, and in a test of the
      task's view. It holds today only by the approval library's default notice, which no
      test names, per US2/AC3 (partial)
- [X] T092 [P] In `web/components/tasks/proposal-card.tsx`, close an open edit or reject
      form, and drop a chosen decision, when a later reading of the same proposal no longer
      lists that decision for the action, with a unit test, per FR-014 (partial)
- [X] T093 [P] Reword the card's sentence that an approved action runs once so that it
      claims nothing for an action the service names neither a tool server nor an agent
      for, and add a unit test of such a proposal
      (`web/components/tasks/proposal-card.tsx`), per Edge Cases "A proposal's action is no
      longer configured, or its agent was removed" (partial)
- [X] T094 [P] Add a browser test of the Tasks section in a narrow window to
      `web/e2e/tasks.spec.ts`: the list, New task, and a task's page with a proposal, every
      control reachable, per Edge Cases "The Tasks section is opened in a narrow window"
      (partial)
- [X] T095 [P] Add to `specs/007-web-tasks/contracts/openapi.yaml` the answers the routes
      give and it lacks: `500` on `GET /threads/{thread_id}`, on
      `POST /threads/{thread_id}/stop`, and on the two `unattended` routes, and
      `already_decided` among the `409` codes of `resume/unattended`, per plan: contracts
      (contradicts)
- [X] T096 [P] Correct the sentences of spec.md and plan.md that the code no longer bears
      out: that the end-to-end tests do not repeat the proof of a stream arriving as it is
      produced, and that `web/playwright.config.ts` is not changed (`web/e2e/stream.spec.ts`
      and its two projects exist); the seven fixed numbers of the Constitution Check's row
      for principle IV (`INDEX_WRITE_SECONDS` is an eighth); that a run is never held up for
      the list (a write may hold it for `INDEX_WRITE_SECONDS`); and FR-013, which has no
      word of a page that is hidden and reads nothing until it is shown, per plan:
      Constitution Check, FR-013, FR-023 (contradicts)
- [X] T097 Give the stand-in model endpoint a model id that asks for tools
      (`tests/llm_stub.py`): by a keyword at the start of the last message it calls
      `send_notification`, hands work to an agent, or takes several steps, and every reply
      gets an id of its own. Name it in `specs/007-web-tasks/quickstart.md`, which cannot be
      walked today with what the repository holds, per T073, plan: quickstart (partial)

---

## Phase 12: Convergence

Appended by `/speckit-converge` on 2026-11-21, after Phase 11: a second reading of every
requirement, acceptance scenario, edge case, the contract, and the tasks of Phase 11
against the code. T098 is high, T099 to T101 are medium, the rest low. The standing rules
above hold for each task: its test first, shown to fail.

- [X] T098 Join the pieces of a reply that arrive as lists of blocks, in `streamed()` of
      `web/components/tasks/task-view.tsx`: it adds a piece to the text only when both are
      plain strings, and otherwise shows the last piece alone. The default model and an
      `openai:` model through the Responses API send each piece as a list with one block,
      and an empty list last, so the page shows a fragment and then nothing until the run
      has ended. Join blocks by their `index`, show no message that has nothing in it yet,
      and test with the pieces the service really sends for both shapes, in the unit tests
      and in one browser test, per FR-010, US1/AC3 (partial)
- [X] T099 Read and send a whole number beyond 2^53 exactly, where the browser can
      (`web/lib/api.ts`, `web/lib/tasks.ts`, `web/components/tasks/proposal-card.tsx`,
      `message.tsx`): the card shows such an argument rounded and refuses every edit of a
      proposal that holds one, so a person approves an id they cannot read and cannot edit
      it. Keep the digits of the service's answer and of the entered text as they are
      written, on the card, in the conversation, and in what is sent; keep the refusal and
      the note for a browser that cannot, and say in spec.md which case holds when, per
      FR-014, FR-015, FR-009 (partial)
- [X] T100 Test that a task's page that was hidden reads the task again when it is shown,
      without waiting for its timer (`web/__tests__/components/tasks/task-view.test.tsx`),
      and the same for the list: it rests on the reading library's default and nothing
      holds the page to it, per FR-013 (partial)
- [X] T101 Drive the debug stream of both console-run routes in the two proofs of
      `tests/test_thread_index_nowhere.py`, as the proofs of the existing routes do, and
      take an agent's address out of every event a console run sends its reader
      (`src/freyja_agent/runtime/unattended.py`), not out of its `error` event alone: a
      debug stream carries a failed step's error as the model raised it, per FR-025
      (partial)
- [X] T102 Keep a thread's own first message when the lookup before a console run fails or
      is given up (`src/freyja_agent/api/thread_index.py`): the run's text is then kept as
      the first message of a thread that had one. Hand the index no text in that case, so
      that the row takes it from the thread's state, with a test of a thread that has a
      state and no row, per FR-002, T086 (partial)
- [X] T103 Show "There is no task yet." only when the service counts no task
      (`web/components/tasks/task-list.tsx`): when every listed row was left out as
      unreadable the list is empty and `total` is not, and the section then says something
      untrue, per FR-023, FR-002 (partial)
- [X] T104 Clear a chosen decision the service no longer lists from the card's state, in
      `web/components/tasks/proposal-card.tsx`, where it is only hidden: when a later
      reading lists the decision again, the choice and its open form come back without a
      press. Test the decision listed again, per FR-014, FR-017, T092 (partial)
- [X] T105 Read the task again when the service refuses a decision's values
      (`web/components/tasks/proposal-card.tsx`, `task-view.tsx`): a refusal that says a
      decision is no longer allowed is shown beside the action, and the control for it
      stays until some other reading, per FR-014, Edge Cases "A proposal's action is no
      longer configured, or its agent was removed" (partial)
- [X] T106 Name each decision's control by its action's place as well when a proposal has
      two actions of the same name (`web/components/tasks/proposal-card.tsx`): their
      controls carry the same name, which does not say on which action, per FR-012a
      (partial)
- [X] T107 Test, in the task's view and in a browser, a decision that gets no answer: the
      page says that the outcome is not known, reads the task again, and shows the card
      only when the proposal is still pending. Today it is tested at the card alone, per
      FR-018, Edge Cases "The answer to a decision never arrives" (partial)
- [X] T108 Say that a run has no Stop in words that hold for a run the console started and
      could not mark as one that can be stopped
      (`web/components/tasks/stop-button.tsx`), which is told "the console did not start
      it", per FR-022a (partial)
- [X] T109 [P] Add to `web/e2e/stream.spec.ts` a page of another origin that reads
      `/api/threads` through the console's forwarding and is given nothing by its browser;
      only a request that changes something is shown there today, per US6/AC2, FR-028
      (partial)
- [X] T110 [P] Assert in `web/e2e/stream.spec.ts` what the sidebar says while a reading of
      the list gets no answer, and in `web/__tests__/components/tasks/task-list.test.tsx`
      that the rows are gone after a later reading failed, per T083, FR-006 (partial)
- [X] T111 [P] Correct what the documents say and the code does not: the spec's
      Convergence note that a console run waits for the list twice before its first event
      (it is three times); FR-006, which has no word of a console that is hidden; FR-007,
      where the box sends no message that is only spaces and the service would take one;
      FR-022a, which does not say that a page following a run by reading says nothing once
      the row is of a later run; the contract's `runs`, which counts every time the
      thread's lease was taken, a refused request included; and `research.md`, which still
      says the stream's arrival cannot be shown by an automated test, per FR-006, FR-007,
      FR-022a, T096, plan: contracts (contradicts)

---

## Phase 13: Convergence

Appended by `/speckit-converge` on 2026-11-22, after Phase 12: a third reading of every
requirement, acceptance scenario, edge case, the contract, and the tasks of Phases 11 and
12 against the code. Every finding is low: nothing critical, high, or medium remains. The
standing rules above hold for each task: its test first, shown to fail.

- [X] T112 Show an action's arguments as they arrive while a run streams
      (`web/components/tasks/task-view.tsx`, `streamed`): each piece carries the action as
      that piece alone makes it, so an action is shown with `{}` until the run has ended,
      and of two actions in one reply only the last named one. Join the pieces of the
      actions by their place, as the library joins `tool_call_chunks`, or take the reply's
      actions from the model step's `update` event, and test with the pieces the service
      sends, per FR-010, T098 (partial)
- [X] T113 Read the task and the list again as soon as a hidden page is shown, also within
      five seconds of the page being opened (`web/components/tasks/task-view.tsx`,
      `task-list.tsx`, `web/components/app-sidebar.tsx`): the reading library waits five
      seconds after a page was mounted before it reads on being shown, and the tests set
      that to zero, so they do not hold the console as it runs, per FR-006, FR-013, T100
      (partial)
- [X] T114 Read the task's row while the page reads its own run's stream, so that a stop
      asked in another window is said ("stopping") and Stop is no longer offered
      (`web/components/tasks/task-view.tsx`): today the page learns of it only from the
      run's last event, per FR-022 (partial)
- [X] T115 Say, when a decision is refused because a run is in progress, that the proposal
      was most likely decided in another window (`web/components/tasks/proposal-card.tsx`),
      with a unit test, per Edge Cases "Two people decide the same proposal" (partial)
- [X] T116 Keep the spelling of a number the service sent or a person entered, where it is
      not the browser's own, such as `5.0` (`web/lib/json.ts`), or say in spec.md that only
      a number's value is kept: `5.0` is shown and sent back as `5`, the same number, per
      FR-014, FR-015, T099 (partial)
- [X] T117 [P] Add `runs` to each row of the contract's example list
      (`specs/007-web-tasks/contracts/openapi.yaml`), where the field is required, per
      plan: contracts (contradicts)
- [X] T118 [P] Correct four sentences of the contract the code does not bear out: the other
      reasons for `500` on `resume/unattended` and on `GET /threads/{thread_id}`; that a
      body that is not JSON is refused with `422` before `403`; and that a console run's
      events are cleaned of agents' addresses, which the earlier routes' are not, per plan:
      contracts (partial)
- [X] T119 [P] Correct the Constitution Check's row for principle V in
      `specs/007-web-tasks/plan.md`: the index's statements are fixed strings with
      parameters, composed with no `sql` module and carrying no `# nosemgrep` mark, per
      plan: Constitution Check (contradicts)

## Phase 14: Convergence

Appended by `/speckit-converge` on 2026-11-22, after Phase 13: a fourth reading of every
requirement, acceptance scenario, edge case, success criterion, the contracts, and the
tasks of Phases 11 to 13 against the code, by three readers, one per slice. One finding is
high, on a deployment the default one does not show; the other seven are low. The standing
rules above hold for each task: its test first, shown to fail.

- [ ] T120 Build a new task's id from `crypto.getRandomValues`, which every origin has, in
      place of `crypto.randomUUID`, which a browser gives only to a secure origin (https,
      `localhost`, `127.0.0.1`): over plain http under a name or an address other than
      loopback, as the README's section on the containers describes, New task throws
      before its dialog opens (`web/lib/tasks.ts`, `newThreadId`), with a unit test where
      `randomUUID` is missing, per FR-007, US1/AC1 (partial)
- [ ] T121 Read the task again shortly after the reading made at the end of the page's own
      run when that reading still says a run is in progress: the service sends the run's
      last event before it gives the lease back (`runtime/unattended.py`, `_drive`), so
      until the next reading, four seconds later, the page can show Running, offer Stop,
      hide a proposal the run paused on, and block the follow-up box
      (`web/components/tasks/task-view.tsx`), with a test where that reading says running,
      per FR-010, FR-019, FR-020 (partial)
- [ ] T122 Show no earlier run's failure while the page streams its own run
      (`web/components/tasks/task-view.tsx`, `rowFailure`): the row held from before says
      `failed` until the next reading of the row, up to four seconds into a follow-up's
      run, per FR-011, FR-003 (partial)
- [ ] T123 Say in the console's own words, as the proposal card does, the service's
      sentences that name a thread: `run_active` and `decision_pending` on the follow-up
      box (`follow-up.tsx`, `send-box.tsx`), and "The list of threads could not be read."
      on the list and on a task's page (`task-list.tsx`, `task-view.tsx`), per FR-001a
      (partial)
- [ ] T124 Do not say that every task that needs a decision is listed when the service left
      rows out because they could not be read (`web/components/tasks/task-list.tsx`):
      `more` is true then too (`api/thread_index.py`, `more = total > len(threads)`), per
      FR-005, FR-023 (partial)
- [ ] T125 Clean a failed run's stored reason again when a row is answered
      (`api/thread_index.py`, `_summary`, through `SCRUBBER`), so that an address that
      became an agent's after the run failed is not returned, as card details are cleaned
      again where they are used, with a test that saves the agent after the run failed,
      per FR-025, SC-008 (partial)
- [ ] T126 Add the service's `403` `unknown_host` refusal of a request made for a name it
      does not answer for to the contract (`contracts/openapi.yaml`, its introduction and
      every path), and say in FR-024 and FR-027 that such a request is the one exception
      to a client that is not a web page being served as before, per contract, FR-024,
      FR-027 (contradicts)
- [ ] T127 Give a row opened while the thread's lease is held the time it was opened as
      `changed_at`, not its last checkpoint's (`runtime/thread_index.py`, `open_thread`),
      or narrow FR-002 to what data-model.md's "Opened" rule says, per FR-002 (partial)
