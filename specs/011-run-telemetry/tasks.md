# Tasks: Telemetry of Runs

**Input**: Design documents from `specs/011-run-telemetry/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: every behavior ships with a test (constitution principle V). A phase's tests are
written with its code and listed before it; where a test task and a code task name the
same behavior, the test is written first and seen to fail.

**Organization**: one phase for the work that existed before the spec, one for what every
story needs, one per user story for what the spec adds, and one for what crosses them.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- **[Story]**: the user story the task belongs to (US1 to US4)

## Pull requests

The owner asked for one pull request holding the spec, the plan, this file, and the
implementation, as for feature 010. Every task below is in it.

## Phase 1: Setup (the work that exists)

**Purpose**: bring the commit of the local branch `telemetry-full` to this branch, without
`/readyz`, which is PR 163.

- [X] T001 Take from `telemetry-full`: `src/freyja_agent/runtime/telemetry.py`, `src/freyja_agent/agent/telemetry.py`, `tests/telemetry_capture.py`, `tests/test_telemetry.py`, `tests/test_telemetry_nowhere.py`, `docs/observability.md`, and its changes to `pyproject.toml`, `uv.lock`, `.env.example`, `README.md`, `docs/web-console.md`, `src/freyja_agent/config.py`, `src/freyja_agent/agent/factory.py`, `src/freyja_agent/api/streaming.py`, `src/freyja_agent/delegates/delegation_worker.py`, `src/freyja_agent/llm/active.py`, `src/freyja_agent/runtime/runtime.py`, `src/freyja_agent/runtime/unattended.py`, `tests/conftest.py`, `tests/test_factory.py`, `AGENTS.md`, and the nested `AGENTS.md` files of `src/freyja_agent/`, `agent/`, `runtime/`, and `tests/`. Leave out everything of `/readyz`: `Runtime.ping`, `Persistence.ping`, the route in `src/freyja_agent/api/app.py`, `tests/test_readyz.py`, the two tests of `tests/integration/test_postgres_runtime.py`, the "Is it ready" section of `docs/observability.md`, and the sentences of `docs/api.md` and `src/freyja_agent/api/AGENTS.md`.
- [X] T002 Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, and `uv run pytest tests/test_telemetry.py tests/test_telemetry_nowhere.py tests/test_factory.py`: all pass before anything is added.

## Phase 2: Foundational (what every story needs)

**Purpose**: the scope that says what a span belongs under, and the bound on a value.

- [X] T003 In `tests/test_telemetry.py`, tests of the scope of `data-model.md`: a call's scope is `kind` `call` with `in_run` true under a run and `model_call` true inside a model call; a run's scope is current only while the graph is asked for its next item.
- [X] T004 In `src/freyja_agent/runtime/telemetry.py`, give `_Scope` the fields of `data-model.md` ("`kind`: `request`, `run`, or `call`", "`in_run`", "`model_call`"), set by `Telemetry.run` and `Telemetry._call`; nothing recorded changes.
- [X] T005 In `tests/test_telemetry.py`, a test that a value of text longer than 256 characters is cut to 256 on a span and on a metric (FR-030); then in `src/freyja_agent/runtime/telemetry.py`, cut every text value where values are gathered (`_present`), since the SDK's limit holds for spans alone.
- [X] T006 In `src/freyja_agent/runtime/telemetry.py`, word the one warning of `_safely` as FR-034 has it (the first failure of an instance, its kind, and that later ones are not logged); hold the wording in `tests/test_telemetry.py`.

**Checkpoint**: the existing tests pass with the scope in place.

## Phase 3: User Story 1 - Follow one run in a trace (Priority: P1) 🎯 MVP

**Goal**: every call of the model within a run has a span that says who made it: the
orchestrator or a subagent, a library by itself, or a profile's test.

**Independent Test**: with telemetry kept in memory, drive a run in which a library asks
the model by itself; the run's trace holds that call once, marked `library`, beside the
orchestrator's own, marked `agent`.

### Tests for User Story 1

- [X] T007 [P] [US1] In `tests/test_telemetry.py`: every model call the middleware records has `freyja.model.caller` `agent`, on its span and its count.
- [X] T008 [P] [US1] In `tests/test_telemetry.py`: a request to a guarded model made inside a run and outside a model call (from a tool, and by the summarization of a long conversation) has one `freyja.model_call` span with `freyja.model.caller` `library`, under the span it was made under, with the run's provider and model; a request inside a model call the middleware records adds no second span; a request outside any run records nothing; a model whose `_agenerate` streams through its own `_astream` is recorded once; a request that raises is recorded as `error` with the class name, and the exception raised is the one the guard raises.
- [X] T009 [P] [US1] In `tests/test_llm_guard.py`: a guarded model with no recording behaves as before, and the recording is entered around each of the four wrapped methods without the context variable being set across a `yield` of the two generators.
- [X] T010 [P] [US1] In `tests/test_llm_check.py` and `tests/test_telemetry.py`: a profile's test has two `freyja.model_call` spans with `freyja.model.caller` `test`, under the request's span once Phase 6 is in and as roots before; a test of a saved profile and of the environment's names the provider and the model; a test of values that are not saved names the provider alone; a test that fails or runs out of time is recorded as `error` or `cancelled` and its answer is unchanged.

### Implementation for User Story 1

- [X] T011 [US1] In `src/freyja_agent/runtime/telemetry.py`: the value `freyja.model.caller` (`agent`, `library`, `test`); `Telemetry.model_call(caller=..., provider=..., model=...)`, whose scope has `model_call` true; and `Telemetry.model_request()`, which returns what records one request to the model made inside a run and outside a model call, and None otherwise, without setting the context variable (research R8).
- [X] T012 [US1] In `src/freyja_agent/llm/guard.py`: `guard_model(model, secrets, record=None)` keeps `record` on the instance, and each of the four wrapped methods tells it of the request's start and end; the cleaned error is what is raised and what is recorded (research R8).
- [X] T013 [US1] In `src/freyja_agent/llm/active.py` and `src/freyja_agent/runtime/runtime.py`: the model manager is given the runtime's telemetry and hands `Telemetry.model_request` to every `guard_model` call.
- [X] T014 [US1] In `src/freyja_agent/llm/check.py` and `src/freyja_agent/api/llm.py`: `check_model` takes how to record a call and wraps each of its two requests; `_check` hands it `Telemetry.model_call` with `caller` `test` and the provider, and with the model's name when the test is of a listed profile, saved or the environment's (`POST /llm/profiles/{profile_id}/test`), and not when it is of values that are not saved (`POST /llm/test`) (FR-010a, research R9).

**Checkpoint**: a run's trace holds every call of the model made within it, once.

## Phase 4: User Story 2 - Counts and durations (Priority: P2)

**Goal**: the count of model calls is told apart by who made the call, and every value that
tells a count apart comes from a bounded set.

**Independent Test**: drive runs with the orchestrator's calls, a library's, and a
profile's test; `freyja.model.calls` holds one series for each caller with the right count.

- [X] T015 [US2] In `tests/test_telemetry.py`: `freyja.model.calls` is told apart by `freyja.model.caller` for the three callers; no metric this feature sends has a run's id or a thread's id among its values (FR-018); every count is of the instance that made it (FR-019); the sampler keeps every trace and `Settings` has no field that thins them (FR-006).
- [X] T016 [US2] In `src/freyja_agent/runtime/telemetry.py`: the caller is among the values of `freyja.model.calls`, for every way a model call is recorded.

**Checkpoint**: User Stories 1 and 2 hold together.

## Phase 5: User Story 3 - See the work that waits (Priority: P3)

**Goal**: two values read from what every instance shares: the threads awaiting a decision,
and the age of the oldest outcome not delivered.

**Independent Test**: pause three threads and decide one; hold one outcome ready and
undelivered. The two gauges read 2 and the outcome's age; after the rest is decided and
delivered they read 0.

### Tests for User Story 3

- [X] T017 [P] [US3] In `tests/test_agent_task_store.py`, the store's contract tests, and `tests/integration/test_a2a_tasks_postgres.py`: `oldest_ready_outcome_age()` is 0 with no task, 0 when every ready outcome is delivered, and otherwise the seconds since the earliest of `coalesce(ended_at, last_checked_at)` among the tasks with `outcome_ready` and no `delivered_at`; a task that asks for input counts by when it was last checked.
- [X] T018 [P] [US3] In `tests/test_telemetry_waiting.py`: the two gauges send what the last reading found; with no reading, or one older than two intervals, they send nothing; a reading that fails leaves both values out at once and affects no other metric; the first failure is said in the log in one line, the next ones are not, and a failure after a reading succeeded is said again (FR-022).
- [X] T019 [P] [US3] In `tests/test_telemetry_waiting.py`: a reading counts a thread whose run paused and whose row nobody read since (it settles the row, as the list of threads does), leaves a thread whose lease is held alone, takes no lease, changes no thread's state, and contacts no model, tool server, or agent; two runtimes that share the stores read the same two values (FR-023); with telemetry off no reading is started.

### Implementation for User Story 3

- [X] T020 [US3] In `src/freyja_agent/runtime/agent_tasks.py`: `oldest_ready_outcome_age() -> float` on `AgentTaskStore`, `PostgresAgentTaskStore` (one statement over `freyja_agent_tasks` against the database's `now()`, composed with psycopg's `sql` module and marked for Semgrep as the others are), and `InMemoryAgentTaskStore`.
- [X] T021 [US3] In `src/freyja_agent/runtime/telemetry.py`: the observable gauges `freyja.threads.awaiting_decision` and `freyja.outcomes.oldest_undelivered.age`, `Telemetry.waiting(awaiting_decision, oldest_undelivered)`, and a callback that returns the reading "while the reading is no older than two intervals, and nothing otherwise".
- [X] T022 [US3] New `src/freyja_agent/runtime/waiting.py`: `read_waiting(runtime)`, which takes a context for reading, settles the unsettled rows whose lease is not held with `settle_thread`, reads `ThreadIndex.counts()` and `oldest_ready_outcome_age()`, and hands both to `Telemetry.waiting`; and the loop that calls it at the interval metrics are sent, logging the first failed reading and the next one only after a reading succeeded.
- [X] T023 [US3] In `src/freyja_agent/runtime/runtime.py`: `Runtime.assemble` starts the loop when telemetry is on and stops it before telemetry is shut down.

**Checkpoint**: the two values follow the stores within two intervals.

## Phase 6: User Story 4 - See the requests the service answers (Priority: P4)

**Goal**: a span and a duration for every request on both listeners, and a run's span under
the request that started it.

**Independent Test**: send a request that starts a run, one that lists threads, one for a
path nobody routes, and one under a host name the service does not answer for; each has
one span named by its declared route or `unmatched`, and the run's span stands under its
request's.

### Tests for User Story 4

- [X] T024 [P] [US4] New `tests/test_telemetry_requests.py`: a request has one server span named `<method> <route>` with `http.request.method`, `http.route` (the declared path with its placeholders), `http.response.status_code`, and `freyja.http.listener`, and one point of `http.server.request.duration` with the same values; a path nobody routes and a request refused for its host name are each `unmatched` with the status sent, and a method its route does not take is named by that route with 405; a method that is not a standard one is `_OTHER`; a request the application fails to answer is 500 with `error.type`; `/healthz` records nothing; a stream's span ends when the stream ends and when its reader leaves.
- [X] T025 [P] [US4] In `tests/test_telemetry_requests.py`: nothing of a request's path as written, query, headers, body, host name, or client's address is in what is sent; a `traceparent` header a client sends changes no trace's id (FR-028); the answer to a request is the same, header for header and byte for byte, with telemetry on and off (FR-039); the outside listener's requests are recorded with `freyja.http.listener` `outside`.
- [X] T026 [P] [US4] In `tests/test_telemetry_requests.py`: the span of a run a request starts stands under that request's span, for a run with a reader, a resume, a console run, and a caller's message over A2A; a background run starts a trace of its own (FR-013); a profile's test's two model calls stand under its request's span.

### Implementation for User Story 4

- [X] T027 [US4] In `src/freyja_agent/runtime/telemetry.py`: `Telemetry.request(method, listener)`, a request under way whose scope is `kind` `request` and which is ended with the route and the status code; the histogram `http.server.request.duration`; the method held to "`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`, `CONNECT`, `TRACE`, or `_OTHER`"; and `Telemetry.run` starting under the current scope when that is a request's (research R7).
- [X] T028 [US4] New `src/freyja_agent/api/telemetry.py`: `RequestTelemetry`, the ASGI middleware of research R6: it passes `/healthz` and `/readyz` on untouched, reads the status code from `http.response.start`, reads the declared route from the scope once the application has answered (`declared_route`), changes nothing that is sent, and ends the request when the application returns, raises, or is cancelled.
- [X] T029 [US4] In `src/freyja_agent/api/app.py`, add `RequestTelemetry` after `AllowedHosts`, so that it is the outermost, reading the telemetry of the runtime the lifespan opened; in `src/freyja_agent/api/a2a_listener.py`, add it to the outside listener's application with the listener `outside`.

**Checkpoint**: every request but the two probes has one span, and a run is found from its request.

## Phase 7: Polish and what crosses the stories

- [X] T030 Extend `tests/test_telemetry_nowhere.py` to everything the phases added: drive the recognizable key, caller's token, agent address, and text through a request's path, query, headers, and body, a library's model call, a profile's test (the key under test included), and the waiting values; add every new span, metric, and value to `allowed`, so that an unknown one fails (FR-033, SC-004); and show that the thread's id is on the run's span and on no other span and no metric (FR-032).
- [X] T031 In `tests/test_telemetry.py`: recording made to fail at each of the three points (the start of a span, the end of a span, a count) for a request, a library's model call, a profile's test, and a reading of the waiting values changes nothing they do, and an approved action still runs once (SC-006).
- [X] T032 In `tests/test_telemetry.py`: with a collector on the loopback interface that takes a connection and never answers, a run produces the same events as with telemetry off and ends in less than `EXPORT_TIMEOUT_SECONDS` (SC-005); what a model, a tool server, and an agent received is the same with telemetry on and off, the new paths included (SC-007); and the span processor is given the queue's bound, past which a span is dropped and nothing waits (FR-036).
- [X] T033 [P] Rewrite `docs/observability.md` from `contracts/telemetry.md` and `contracts/settings.md`: every span, metric, and value; what is never sent; that the two waiting values are not added up; what the exporter reads from the environment by itself. Keep it in the README's list and the **Next:** links.
- [X] T034 [P] Update `.env.example`, the README's settings table, `AGENTS.md` (the convention on telemetry: names never content, the one module, the proof to extend), and the nested `AGENTS.md` files of `src/freyja_agent/`, `agent/`, `llm/`, `runtime/`, `api/`, and `tests/` for every new module and seam.
- [X] T035 Run every gate: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest --cov` with `FREYJA_TEST_DATABASE_URI` set and, at the hook's floor, without; Semgrep and Trivy as CI runs them.
- [X] T036 Walk `quickstart.md` against a collector on this machine and write under each step what was seen.
- [ ] T037 Once PR 163 is on `main`: bring this branch onto `main`, and add to `tests/test_telemetry_requests.py` that `/readyz` records nothing (FR-027).

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 before everything. Phase 2 before Phases 3 to 6.
- Phase 3 (US1) before Phase 4 (US2), which only adds the caller to a count Phase 3 makes.
- Phase 5 (US3) and Phase 6 (US4) depend on Phase 2 alone and on neither of the others.
- T010's "under the request's span" and T026's last clause need both Phase 3 and Phase 6.
- Phase 7 after the phases whose behavior it proves or documents.

### Within Each User Story

- A test task before the code task that makes it pass.
- `runtime/telemetry.py` first (T011, T021, T027), then the module that calls it.

### Parallel Opportunities

- T007 to T010 are in one file for three of them; T009 is apart and can be written beside.
- T017, T018, and T019 touch different files or different parts and can be written together.
- T033 and T034 are documentation and touch no code.
- T011, T016, T021, and T027 all change `src/freyja_agent/runtime/telemetry.py` and are done one after another.

## Implementation Strategy

### MVP First

Phases 1 and 2 give what existed before the spec, with the scope the rest builds on. Phase 3
completes User Story 1: every model call of a run is in its trace.

### Incremental Delivery

Each later phase adds one story and leaves the earlier ones passing: counts by caller, the
two waiting values, then requests. Phase 7 proves the whole against the spec's success
criteria and documents it.

## Notes

- One pull request: there is no merge between phases, so a checkpoint is a run of the
  phase's tests, not a release.
- `runtime/telemetry.py` stays the one module that imports OpenTelemetry;
  `tests/test_telemetry.py` fails when another does.
- The coverage floor holds for every new module: `api/telemetry.py` and
  `runtime/waiting.py` are fully covered by their phase's tests.

## Phase 8: Convergence

- [X] T038 In `tests/test_telemetry_requests.py`, show that a request's span ends when its reader leaves a stream that had begun, with the status that was sent and no error, and that a request its client left before an answer began is recorded without a status code, per FR-029 (partial)
