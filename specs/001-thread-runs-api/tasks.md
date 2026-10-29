# Tasks: Thread Runs API

**Input**: Design documents from `/specs/001-thread-runs-api/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi.yaml,
contracts/sse-events.md, quickstart.md

**Tests**: Included. FR-021 requires automated coverage for every behavior in this feature, and
SC-007 requires the suite to run without a model provider or a database. Test tasks come first in
each phase; write them, watch them fail, then implement.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. Research references (R1 to R15) point at `research.md`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1 to US5)
- Every task names the file it touches

## Path Conventions

Single project, src layout: `src/freyja_agent/`, `tests/`, `workspace/`, repo-root files.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies, settings, local database, and the package skeleton every later task
builds on.

- [ ] T001 Pin `deepagents>=0.7.32,<0.8` and add `asyncio_mode = "auto"` under
      `[tool.pytest.ini_options]` in `pyproject.toml`; run `uv lock` (R1, R11)
- [ ] T002 [P] Add `workspace_dir: str = "workspace"`, `max_steps: int = 50`, and
      `run_lock_ttl_seconds: int = 60` to `Settings` in `src/freyja_agent/config.py`, each with a
      one-line comment naming the FR it serves (data-model.md "Settings added by this feature")
- [ ] T003 [P] Document `FREYJA_WORKSPACE_DIR=workspace`, `FREYJA_MAX_STEPS=50`,
      `FREYJA_RUN_LOCK_TTL_SECONDS=60`, and a commented
      `# FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test` in
      `.env.example`
- [ ] T004 [P] Add tests for the three new settings' defaults and `FREYJA_*` overrides in
      `tests/test_config.py`
- [ ] T005 [P] Create `docker-compose.yml` with one service `postgres` (image
      `postgres:17-alpine`, `POSTGRES_USER=freyja`, `POSTGRES_PASSWORD=freyja`,
      `POSTGRES_DB=freyja`, port `5432:5432`, named volume, `pg_isready` healthcheck) so the
      credentials equal `FREYJA_DATABASE_URI` in `.env.example` (R13, FR-018)
- [ ] T006 [P] Create the placeholder skill `workspace/skills/case-notes/SKILL.md` with front
      matter `name: case-notes` and a `description`, and a body telling the orchestrator to keep
      working notes under `cases/<thread_id>/notes.md`; confirm `workspace/cases/` is ignored by
      `.gitignore` (R14, FR-017)
- [ ] T007 [P] Create empty packages `src/freyja_agent/agent/__init__.py` and
      `src/freyja_agent/runtime/__init__.py`

**Checkpoint**: `uv sync`, `uv run pytest`, and `docker compose up -d postgres` all succeed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The agent factory, the run lease, persistence, the app factory, and the test
fakes. No user story can be built or tested before these exist.

**CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T008 [P] Implement `send_notification(channel: str, message: str)` as a `@tool` in
      `src/freyja_agent/agent/tools.py`: docstring says it is a placeholder that only logs; emits
      one `WARNING` record on logger `freyja_agent.actions` carrying `channel` and `message`;
      returns `"Notification to {channel} recorded (placeholder: logged only)."`; export
      `SEND_NOTIFICATION_INTERRUPT = {"send_notification": {"allowed_decisions": ["approve",
      "edit", "reject"], "description": "Send a notification to a channel (placeholder: logs
      only)."}}` (R15, FR-009)
- [ ] T009 [P] Write `DEFAULT_PROMPT` for the orchestrator in `src/freyja_agent/agent/prompts.py`
      (who it is, that it works inside its workspace, that notifications require approval)
- [ ] T010 Implement `build_agent(model, *, checkpointer, store, workspace_dir,
      system_prompt=DEFAULT_PROMPT)` in `src/freyja_agent/agent/factory.py`: create
      `workspace_dir` if missing; call `create_deep_agent` with
      `backend=FilesystemBackend(root_dir=workspace_dir, virtual_mode=True)`,
      `tools=[send_notification]`, `interrupt_on=SEND_NOTIFICATION_INTERRUPT`,
      `skills=["/skills/"]`, `checkpointer`, `store`, `name="freyja"`; nothing else (R1, FR-015,
      FR-016, FR-017)
- [ ] T011 [P] Implement the run lease in `src/freyja_agent/runtime/locks.py`: a `RunLocks`
      protocol with `acquire(thread_id, owner) -> bool`, `renew(thread_id, owner) -> None`,
      `release(thread_id, owner) -> None`, `is_active(thread_id) -> bool`; `InMemoryRunLocks`
      taking an injectable clock; `PostgresRunLocks(pool, ttl_seconds)` using the table
      `freyja_run_locks(thread_id text primary key, owner text not null, acquired_at timestamptz
      not null, expires_at timestamptz not null)` with single-statement SQL: acquire = `INSERT ...
      ON CONFLICT (thread_id) DO UPDATE ... WHERE freyja_run_locks.expires_at < now() RETURNING
      owner`; renew = `UPDATE ... SET expires_at = now() + ttl WHERE thread_id = %s AND owner =
      %s`; release = `DELETE ... WHERE thread_id = %s AND owner = %s`; is_active = `SELECT
      EXISTS(... WHERE thread_id = %s AND expires_at > now())`; plus a `setup()` that creates
      the table (R7, data-model.md RunLock)
- [ ] T012 [P] Implement `open_persistence(settings)` in
      `src/freyja_agent/runtime/persistence.py`: one
      `AsyncConnectionPool(settings.database_uri, kwargs={"autocommit": True,
      "prepare_threshold": 0, "row_factory": dict_row}, open=False, min_size=1, max_size=10)`,
      `await pool.open()`, `AsyncPostgresSaver(pool)` and `AsyncPostgresStore(pool)` with
      `setup()` on both; return pool, saver, store; let connection errors propagate so startup
      fails naming the database (R6, FR-008)
- [ ] T013 Implement `Runtime` in `src/freyja_agent/runtime/runtime.py`: a dataclass with
      `agent`, `locks`, `instance_id` (UUID4 string, injectable), `settings`; an async context
      manager classmethod `Runtime.open(settings)` that calls `open_persistence`, creates
      `PostgresRunLocks` and runs its `setup()`, calls `build_agent(settings.model, ...)` with
      `workspace_dir=settings.workspace_dir`, yields the runtime, and closes the pool on exit
      (depends on T010, T011, T012)
- [ ] T014 [P] Define request and response models in `src/freyja_agent/api/schemas.py` per
      `contracts/openapi.yaml`: `THREAD_ID_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"`;
      `RunRequest(message: str, minLength 1, maxLength 32000)`; `ApproveDecision`,
      `EditDecision(args: dict)`, `RejectDecision(message: str | None, maxLength 4000)` as a
      discriminated union on `type`; `ResumeRequest(decisions: list, minItems 1)`; `Message`,
      `ActionRequest`, `PendingDecision`, `ThreadState`, `ErrorDetail(code, message)`
- [ ] T015 [P] Implement `src/freyja_agent/api/serialize.py`: `message_to_dict` (fields `id`,
      `type` in human/ai/tool/system, `content`, `name`, `tool_calls` as `{id, name, args}` on
      `ai`, `tool_call_id` on `tool`), `update_to_dict` for an `updates` chunk,
      `interrupt_to_pending` mapping the HITL value `{"action_requests": [...],
      "review_configs": [...]}` plus the interrupt id to `PendingDecision`, and a `to_jsonable`
      fallback for debug chunks (R4, R8, data-model.md Message and PendingDecision)
- [ ] T016 Refactor `src/freyja_agent/api/app.py` into `create_app(open_runtime=Runtime.open)`:
      a lifespan that enters `open_runtime(settings)` and stores the runtime on `app.state`,
      the unchanged `/healthz`, a module-level `app = create_app()`, and `main()` unchanged
      (R6, R11, FR-015)
- [ ] T017 [P] Write `tests/fakes.py`: `ScriptedChatModel(BaseChatModel)` implementing
      `_generate`, `_llm_type`, `bind_tools` (records tool names, returns self), and
      `_get_ls_params` (fixed provider); scripted per turn with `AIMessage`s that may carry
      `tool_calls`, returning a final text message when the script is exhausted; a
      `LoopingChatModel` variant that always calls `ls`; `FakeClock` with `now()` and
      `advance(seconds)` (R11)
- [ ] T018 Extend `tests/conftest.py` with fixtures: `scripted_model`, `in_memory_runtime`
      (`build_agent` with `InMemorySaver()`, `InMemoryStore()`, `tmp_path` workspace containing a
      copy of `workspace/skills/`, `InMemoryRunLocks(FakeClock())`), `make_app(runtime)` that
      calls `create_app` with an `open_runtime` yielding that runtime, and an `sse_events(response)`
      helper that parses `event:`/`data:` pairs from `iter_lines()` and ignores `: ping` comments
      (depends on T016, T017)
- [ ] T019 [P] Write `tests/test_factory.py`: the tools bound to the model exclude `execute`
      and include `task` and `send_notification`; the skills list in the system prompt names
      `case-notes`; the system prompt is `DEFAULT_PROMPT`; a `BaseChatModel` instance is used
      as given and a string is passed through unchanged (FR-015, FR-016, FR-017; R1, R3, R14)
- [ ] T020 [P] Write `tests/test_locks.py` unit tests for `InMemoryRunLocks`: acquire succeeds
      once, second acquire fails while unexpired, renew moves expiry, release frees, expiry via
      `FakeClock.advance` frees for a new owner, release with a wrong owner is a no-op (FR-006)

**Checkpoint**: `uv run pytest` passes with `test_factory` and `test_locks` green; `uv run
--env-file .env freyja-api` starts against the compose database and logs that the checkpointer,
store, and lock table are ready.

---

## Phase 3: User Story 1 - Send a message and watch the run live (Priority: P1) 🎯 MVP

**Goal**: `POST /threads/{thread_id}/runs` streams a run over SSE with `start`, `update`,
`message`, optional `debug`, and a terminal `end` or `error`; turns on one thread form one
conversation; one active run per thread; a step limit bounds every run.

**Independent Test**: With the in-memory runtime and the scripted model, start a run on a new
thread, read the stream until it closes, then send a follow-up and assert the model saw the
earlier turn. Manually: quickstart.md Scenario 1.

### Tests for User Story 1

- [ ] T021 [P] [US1] Write `tests/test_thread_ids.py` with parametrized invalid ids (empty,
      129 chars, space, slash, unicode) asserting 422 on `POST /threads/{id}/runs` before any
      work starts (FR-001a); leave a parametrized route list so US2 and US3 add their routes
- [ ] T022 [P] [US1] Write `tests/test_runs.py`: first event is `start` with `thread_id`,
      `run_id`, `mode: "run"`, `debug: false`; `update` and `message` events follow; last event
      is `end` with `{"status": "completed"}`; empty message is 422 and starts no run; a
      follow-up on the same thread passes the earlier human message to the model; `?debug=true`
      adds `debug` events without changing the outcome and no `debug` events appear without it;
      `start` is emitted before the scripted model's first call (call counter, SC-001); a
      scripted model that raises on its second turn yields `error` with code `model_error`,
      the lease is released, and a following run on the thread completes;
      a second run while one streams gets 409 `run_active` and the first completes (FR-002,
      FR-003, FR-004, FR-005, FR-006)
- [ ] T023 [P] [US1] Write `tests/test_step_limit.py`: with `max_steps=2` and
      `LoopingChatModel`, the stream ends with `error` whose `code` is `step_limit` and whose
      message names `2` and `FREYJA_MAX_STEPS`; a following run on the same thread works
      (FR-004a)

### Implementation for User Story 1

- [ ] T024 [US1] Implement `run_stream(runtime, thread_id, *, input, mode, debug, run_id)` in
      `src/freyja_agent/api/streaming.py`: yield `start`; call `agent.astream(input,
      config={"configurable": {"thread_id": thread_id}, "recursion_limit":
      runtime.settings.max_steps}, stream_mode=["updates", "messages"] + (["debug"] if debug
      else []), durability="sync")`; map `updates` to `update` (recording an `__interrupt__`
      chunk instead of emitting it), `messages` to `message`, `debug` to `debug`; after the
      stream drains emit `interrupt` if one was recorded else `end`; catch
      `GraphRecursionError` as `error` code `step_limit` naming the limit and `FREYJA_MAX_STEPS`,
      exceptions raised by the chat model (provider API errors, timeouts) as `error` code
      `model_error`, every other exception as `error` code `internal` (logged with run id); every
      payload is `json.dumps`-encoded into `ServerSentEvent(data=..., event=kind)` (R4, R5, R10)
- [ ] T025 [US1] Add the lease to `run_stream` in `src/freyja_agent/api/streaming.py`: owner is
      `f"{runtime.instance_id}:{run_id}"`; a heartbeat `asyncio.Task` calls `locks.renew` every
      `run_lock_ttl_seconds / 3`; a `try/finally` cancels the heartbeat and calls
      `locks.release` inside `anyio.CancelScope(shield=True)` so disconnect, error, interrupt,
      and completion all release it (R7, R12)
- [ ] T026 [US1] Implement `POST /threads/{thread_id}/runs` in `src/freyja_agent/api/threads.py`:
      validate the path id against `THREAD_ID_PATTERN` (422) and the body as `RunRequest`;
      acquire the lease first (409 `run_active` on failure); read `aget_state` under the lease
      and refuse with 409 `decision_pending` if `snapshot.interrupts` is non-empty, releasing the
      lease; otherwise return `EventSourceResponse(run_stream(...))` with the human message as
      input (R9)
- [ ] T027 [US1] Register the threads router in `create_app` in `src/freyja_agent/api/app.py`
- [ ] T028 [US1] Extend `tests/test_locks.py` with API-level cases: the lease is released after
      `end`, after `error`, and when the client closes the stream early (FR-006, R12)

**Checkpoint**: User Story 1 is fully functional; `test_runs`, `test_step_limit`, and the runs
cases in `test_thread_ids` and `test_locks` pass.

---

## Phase 4: User Story 2 - Decide on an environment-changing action (Priority: P2)

**Goal**: a proposed `send_notification` pauses the run with a terminal `interrupt` event; `POST
/threads/{thread_id}/resume` validates approve, edit, or reject and streams the continuation;
the tool runs only after a recorded approve or edit.

**Independent Test**: Script the model to call `send_notification`; assert the stream ends with
`interrupt` and no log line exists; resume on separate threads with each decision kind and assert
the `freyja_agent.actions` log and the continuation. Manually: quickstart.md Scenario 2.

### Tests for User Story 2

- [ ] T029 [P] [US2] Write `tests/test_gate.py`: a scripted `send_notification` call ends the
      stream with `interrupt` carrying `interrupt_id`, one action request with `name`
      `send_notification` and the proposed `args`, and `allowed_decisions`
      `["approve", "edit", "reject"]`, with no `freyja_agent.actions` record in `caplog`;
      approve runs the tool exactly once with the proposed args and logs the channel; edit with
      `{"channel": "#ops-staging", "message": "..."}` logs the edited channel and not the
      original; reject with a message logs nothing and the model receives a tool message
      containing the reason; resume on a thread with nothing pending is 409 `nothing_pending`;
      resume on a never-used thread is 404 `thread_not_found`; an unknown decision type, a wrong
      decision count, and edit args failing the tool schema are 422 and `aget_state` still shows
      the interrupt; a new message on a paused thread is 409 `decision_pending` and the proposal
      remains; two `send_notification` calls in one step pause once with two action requests,
      approving one and rejecting the other logs exactly one line; resume with `?debug=true`
      emits `debug` events (FR-010 to FR-013, SC-002, SC-003)
- [ ] T030 [P] [US2] Write `tests/test_subagent.py`: a script where the parent calls `task` and
      the subagent calls `send_notification` ends with `interrupt`; with `max_steps=2` the limit
      also ends a looping subagent with `error` `step_limit` (R1, R10, principle I through the
      nested path)

### Implementation for User Story 2

- [ ] T031 [US2] Implement decision validation in `src/freyja_agent/api/decisions.py`:
      `build_resume_command(pending, decisions, tool)`; require `len(decisions) ==
      len(pending.action_requests)`; require each `type` in `pending.allowed_decisions`; for
      `edit`, validate `args` with `send_notification.args_schema` and set `edited_action.name`
      to the original action name; raise a 422 error carrying pydantic's error list on any
      failure; return `Command(resume={pending.interrupt_id: {"decisions": [...]}})` with
      decision dicts `{"type": "approve"}`, `{"type": "edit", "edited_action": {"name", "args"}}`,
      `{"type": "reject", "message": ...}` (R2, FR-012, FR-013)
- [ ] T032 [US2] Implement `POST /threads/{thread_id}/resume` in
      `src/freyja_agent/api/threads.py`: validate the id (422); acquire the lease (409
      `run_active`); read state under the lease; 404 `thread_not_found` when `metadata is None`;
      409 `nothing_pending` when `interrupts` is empty; 422 from T031 with the lease released and
      the pending decision untouched; read the `debug` query flag; otherwise stream
      `run_stream(..., input=command, mode="resume", debug=debug)` (R9)
- [ ] T033 [US2] Confirm `run_stream` in `src/freyja_agent/api/streaming.py` accepts a
      `Command` as input and reports `mode: "resume"` in `start`; the resume stream uses the
      same event kinds and terminal events as a run (FR-013)
- [ ] T034 [US2] Add the resume route to the parametrized route list in
      `tests/test_thread_ids.py` (FR-001a)

**Checkpoint**: User Stories 1 and 2 work independently; `test_gate` and `test_subagent` pass.

---

## Phase 5: User Story 3 - Inspect where a thread stands (Priority: P3)

**Goal**: `GET /threads/{thread_id}/state` returns the full transcript, `run_active`, `pending`
with the same proposal the stream reported, and `updated_at`; a never-used thread is 404.

**Independent Test**: Run one turn, read state and assert the human, ai, and tool messages in
order; pause a thread on a proposal, read state and assert `pending` equals the `interrupt`
event's data. Manually: quickstart.md Scenario 3.

### Tests for User Story 3

- [ ] T035 [P] [US3] Write `tests/test_state.py`: after a completed turn with a tool call the
      transcript contains `human`, `ai` with `tool_calls`, `tool` with `tool_call_id`, and the
      final `ai` message in order, `pending` is null, `run_active` is false; on a paused thread
      `pending` matches the `interrupt` event payload; a never-used thread is 404
      `thread_not_found`; `run_active` is true while a lease is held and false right after the
      stream closes (FR-007, SC-005)

### Implementation for User Story 3

- [ ] T036 [US3] Implement `GET /threads/{thread_id}/state` in `src/freyja_agent/api/threads.py`:
      validate the id (422); `snapshot = await agent.aget_state(...)`; 404 when
      `snapshot.metadata is None`; build `ThreadState` with `messages` from
      `snapshot.values["messages"]` through `serialize.message_to_dict`, `pending` from
      `snapshot.interrupts` through `interrupt_to_pending`, `run_active` from
      `locks.is_active`, `updated_at` from `snapshot.created_at` (R8)
- [ ] T037 [US3] Add the state route to the parametrized route list in
      `tests/test_thread_ids.py` (FR-001a)

**Checkpoint**: all three endpoints work; `test_state` passes.

---

## Phase 6: User Story 4 - Decide after a restart (Priority: P4)

**Goal**: a paused thread survives a full service stop and start, on the same or another
instance, and resumes with the same outcome.

**Independent Test**: Pause a thread through one app, build a second app whose runtime shares
the saver, store, and lock store but has a fresh `instance_id`, read state and resume through the
second app. Manually: quickstart.md Scenario 4.

### Tests for User Story 4

- [ ] T038 [P] [US4] Write `tests/test_restart.py`: pause a thread on app A; create app B sharing
      A's `InMemorySaver`, `InMemoryStore`, and `InMemoryRunLocks` with a new `instance_id`;
      `GET state` on B shows the same `pending`; resume with approve on B completes and logs the
      channel; a lease acquired on A before "crash" blocks B until `FakeClock.advance(ttl)`,
      after which B's run is accepted (FR-005, FR-014, FR-006, SC-004)
- [ ] T039 [P] [US4] Write `tests/integration/conftest.py`: skip the directory unless
      `FREYJA_TEST_DATABASE_URI` is set; a fixture that opens a `Runtime` against it with the
      scripted model and drops the LangGraph and `freyja_run_locks` tables afterwards (R11)
- [ ] T040 [P] [US4] Write `tests/integration/test_postgres_locks.py`: the SQL lease against a
      real database: acquire, concurrent acquire fails, renew extends `expires_at`, release
      deletes, an expired row is taken over by a new owner (R7)
- [ ] T041 [US4] Write `tests/integration/test_postgres_runtime.py`: run to `interrupt` through
      one `Runtime.open`, close it, open a second `Runtime` with a new `instance_id`, read state
      and resume to `end` through Postgres; and `Runtime.open` against an unreachable URI raises
      an error naming the database connection (FR-008, FR-014) (depends on T039)

### Implementation for User Story 4

- [ ] T042 [US4] Make `instance_id` and the model injectable on `Runtime.open` in
      `src/freyja_agent/runtime/runtime.py` so the integration tests can open two runtimes
      against one database with the scripted model (R11)

**Checkpoint**: `test_restart` passes without a database; the integration tests pass with
`FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest
tests/integration` after `createdb freyja_test` through the compose container.

---

## Phase 7: User Story 5 - Bring the runtime up locally and troubleshoot a run (Priority: P5)

**Goal**: a developer goes from a clean clone to a completed streamed run using only the README,
without editing a credential, and can request the same run with the debug flag.

**Independent Test**: Follow quickstart.md Setup and Scenario 5 on a clean clone with the compose
database (SC-006).

### Implementation for User Story 5

- [ ] T043 [US5] Update `README.md`: start the local database with `docker compose up -d
      postgres`; copy `.env.example` unchanged for the database line; the three endpoints with
      one `curl` each and the SSE event kinds; the three new rows in the configuration table;
      `uv run pytest` needs no database and the integration suite needs
      `FREYJA_TEST_DATABASE_URI` (FR-020, documents only what now exists)
- [ ] T044 [US5] Verify in `src/freyja_agent/runtime/persistence.py` that a connection failure
      at startup surfaces as a startup error whose message names the database connection, and
      that `uvicorn` exits rather than serving (FR-008; covered by T041, verified manually with
      the compose database stopped)
- [ ] T045 [US5] Run every scenario in `specs/001-thread-runs-api/quickstart.md` against the
      compose database with a real provider key and record any mismatch as a fix task before
      closing the story (SC-001, SC-006)

**Checkpoint**: a clean clone reaches a completed run in under 15 minutes following the README.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T046 [P] Update `AGENTS.md` Layout and Commands for the new packages (`agent/`,
      `runtime/`, `api/threads.py`, `api/streaming.py`), `workspace/`, `docker-compose.yml`,
      and the integration test variable
- [ ] T047 [P] Review every `error` event message and log line in
      `src/freyja_agent/api/streaming.py` and `src/freyja_agent/runtime/persistence.py` for
      leaked secrets (connection strings, provider keys); error messages stay human-readable
      and generic for `internal`
- [ ] T048 Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, and
      `uv run pre-commit run --all-files`; confirm no new Ruff suppressions were added (SC-007,
      principle V)
- [ ] T049 Re-run the plan's Constitution Check against the implemented code and note the
      result in the pull request description (principles I, III, IV, V)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies; T002 to T007 run in parallel after T001.
- **Foundational (Phase 2)**: depends on Phase 1. Blocks every user story.
- **User Story 1 (Phase 3)**: depends on Phase 2. The MVP.
- **User Story 2 (Phase 4)**: depends on Phase 3 (resume reuses `run_stream` and the lease).
- **User Story 3 (Phase 5)**: depends on Phase 2 only, plus Phase 4 for the `pending` assertion.
- **User Story 4 (Phase 6)**: depends on Phases 3, 4, and 5 (restart test reads state and resumes).
- **User Story 5 (Phase 7)**: depends on Phases 3 to 6 being complete.
- **Polish (Phase 8)**: depends on all stories.

### Within Phase 2

- T008 and T009 before T010; T010, T011, T012 before T013.
- T013 before T016 (`create_app` imports `Runtime` for its default `open_runtime`).
- T016 and T017 before T018; T018 before every test that uses the fixtures.
- T014 and T015 have no dependencies inside the phase.

### Within Each User Story

- Tests first, and they must fail before implementation starts.
- `streaming.py` (T024, T025) before `threads.py` (T026); router registration (T027) last.
- Decision validation (T031) before the resume route (T032).

### Parallel Opportunities

- Phase 1: T002, T003, T004, T005, T006, T007 together.
- Phase 2: T008, T009, T011, T012, T014, T015, T017 together; then T010; then T013, T016; then
  T018; then T019 and T020 together.
- Phase 3: T021, T022, T023 together.
- Phase 4: T029 and T030 together.
- Phase 6: T038, T039, T040 together.
- Phase 8: T046 and T047 together.

---

## Parallel Example: User Story 1

```bash
# Write the three test modules together, then watch them fail:
Task: "Write tests/test_thread_ids.py for POST /threads/{id}/runs (T021)"
Task: "Write tests/test_runs.py for the stream contract (T022)"
Task: "Write tests/test_step_limit.py (T023)"

# Then implement in order:
Task: "run_stream in src/freyja_agent/api/streaming.py (T024, T025)"
Task: "POST /threads/{thread_id}/runs in src/freyja_agent/api/threads.py (T026)"
Task: "Register the router in src/freyja_agent/api/app.py (T027)"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational. This is the bulk of the new code.
3. Complete Phase 3: User Story 1.
4. **STOP and VALIDATE**: `uv run pytest`, then quickstart.md Scenario 1 against the compose
   database.
5. Open a pull request if the increment should merge on its own.

### Incremental Delivery

1. Setup + Foundational: the service starts against Postgres and the suite is green.
2. User Story 1: streamed runs on threads (MVP).
3. User Story 2: the approval gate and resume, the constitution's core requirement.
4. User Story 3: state inspection.
5. User Story 4: restart durability, with the opt-in Postgres integration tests.
6. User Story 5: README and the manual quickstart pass.
7. Polish.

### Notes

- [P] tasks touch different files and have no dependency on an incomplete task.
- Every test task names the FRs it covers; the FR coverage map in research.md R11 is the
  reference for completeness.
- Commit after each phase at least; commits stay manual and use the `type: summary` form.
- No new Ruff suppressions; blocking I/O in async code is a defect (principle V).
