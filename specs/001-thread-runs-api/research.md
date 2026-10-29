# Research: Thread Runs API

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Every finding below was read from the packages installed in `.venv` (deepagents 0.7.32,
langchain 1.4.7, langchain-core 1.7.0, langgraph 1.2.18, langgraph-checkpoint-postgres 3.2.0,
psycopg 3.3.6, psycopg_pool 3.3.3, sse-starlette 3.6.0, starlette 1.10.0, fastapi 0.162.0), not
from memory of older releases. Paths are relative to `.venv/lib/python3.12/site-packages/`. Items
marked "probe" were confirmed by running a throwaway script against the installed code. Every
unknown from the plan's Technical Context is resolved below.

## R1. Agent assembly in `build_agent`

**Decision**: one function, `build_agent(model, *, checkpointer, store, workspace_dir,
system_prompt=DEFAULT_PROMPT)`, returns the compiled graph from `create_deep_agent`. The step
limit is not a factory argument; it is per-run config (R10). The factory passes:

- `model`: the `Settings.model` string in production, a `BaseChatModel` instance in tests
  (`deepagents/_models.py:54-57` accepts both).
- `backend=FilesystemBackend(root_dir=workspace_dir, virtual_mode=True)`; the directory is
  created if missing.
- `tools=[send_notification]`, `interrupt_on={"send_notification": {"allowed_decisions": ["approve",
  "edit", "reject"], "description": ...}}` (R2).
- `skills=["/skills/"]` (R14), `system_prompt=` the Freyja prompt (an empty authored prompt is
  what you get when `system_prompt` is `None` and no profile matches, `graph.py:363-383`).
- `checkpointer`, `store`, `name="freyja"`.

Nothing else: no harness profile registration and no custom middleware. The Deep Agents default
stack is accepted as shipped, including the general-purpose subagent and its `task` tool.
`pyproject.toml` pins `deepagents>=0.7.32,<0.8`.

**Rationale**: the default stack in 0.7.32 installs `ls, read_file, write_file, edit_file,
delete, glob, grep, execute` (`middleware/filesystem.py:1608, 2031-2040`) and adds a
general-purpose subagent with a `task` tool even when `subagents=None` (`graph.py:840-841`).
Both are kept:

- `execute` never reaches the model. `FilesystemBackend` implements no `execute`, so the
  filesystem middleware strips the tool from every model request and answers any call with an
  error `ToolMessage` (`middleware/filesystem.py:1706-1725, 3057-3081, 3200-3211`). A shell
  exists only with `LocalShellBackend`, which is not used. The factory test asserts that
  `execute` is absent from the tools the model receives.
- The subagent is Deep Agents' context-isolation mechanism: a delegated phase returns one
  report instead of filling the parent's window. It inherits `interrupt_on` (`graph.py:897-902`),
  so `send_notification` proposed from inside a subagent still pauses the run, and the step limit
  reaches into it (R10), so a `task` call is bounded. Keeping it on from the first increment
  lets the suite prove that the gate holds through the first nested code path Freyja has, which
  principle I demands of every path.
- `FilesystemBackend(virtual_mode=True)` rejects `..` and `~`, resolves under `root_dir`, and
  checks `relative_to(root)` (`backends/filesystem.py:182-223`), which satisfies FR-017.

The version pin exists because the human-in-the-loop payload shapes (R2) and the default
middleware stack are the surfaces this feature depends on, and both have moved between minor
releases. A bump is a deliberate step verified by the factory, gate, and subagent tests.

**Alternatives considered**: disabling the subagent through a harness profile
(`GeneralPurposeSubagentProfile(enabled=False)`, `graph.py:438-441`) and hiding `execute` and
`delete` with `excluded_tools`. Rejected: it depends on a beta API, removes a capability that
will be wanted as soon as case files grow, and postpones the gate-through-subagent test. A
custom `FilesystemMiddleware(tools=[...])` to drop the `execute` object from the tool node
(`graph.py:216-249`): unnecessary, since the model cannot call it. `StateBackend`: files would
live in checkpoints, so skills could not be committed files.

## R2. Human-in-the-loop mode and decision validation

**Decision**: use the default batched `HumanInTheLoopMiddleware` that `interrupt_on` installs.
Resume with `Command(resume={interrupt_id: {"decisions": [...]}})`, keyed by the interrupt id.
The API validates every decision before building that payload: the list length equals the
number of `action_requests`; each `type` is in `allowed_decisions`; for `edit`, `args` is
validated with the tool's `args_schema` and the `edited_action.name` is set server-side to the
original tool name; a validation failure returns 422 with pydantic's error list so a client can
show which field was wrong. Reject passes `message` through when given.

**Rationale**: batched mode raises one interrupt per model step covering all gated calls in
that step (`human_in_the_loop.py:790-815`); the value is `{"action_requests": [{name, args,
description}], "review_configs": [{action_name, allowed_decisions}]}` (105-112), which maps
directly onto the spec's pause event and the "more than one gated action in one step" edge case.
The resume shape is `{"decisions": [...]}` with positional decisions whose count must match or
the middleware raises `ValueError` (818-823); decision dicts are `{"type": "approve"}`,
`{"type": "edit", "edited_action": {"name", "args"}}`, `{"type": "reject", "message"}` (115-163).
Batched mode does not pin the tool name or validate edited args (886-911): an edit could switch
to any available tool. FR-013 and principle I need both guarantees, so the API layer provides
them. On reject the middleware appends an error `ToolMessage` ("User rejected the tool call ...
with reason: ...") and the model runs again (266-284; `agents/factory.py:2021-2040`), which is
exactly the spec's "orchestrator continues and tells the user". The `respond` decision type
exists but is not exposed (spec names three decisions).

**Alternatives considered**: `interrupt_mode="per_call"` pins the name and validates args
natively, but raises one interrupt per tool call (several per step) and changes the resume
format to one value per interrupt id (`human_in_the_loop.py:172-194, 477-483`); it would push the
"several pending decisions" complexity into the API for no gain in this increment.

## R3. Model resolution from settings

**Decision**: pass `Settings.model` as the string to `create_deep_agent`; do not construct the
chat model in Freyja code. Tests pass a `BaseChatModel` instance instead.

**Rationale**: `resolve_model` calls `init_chat_model(model, **apply_provider_profile(model))`
(`deepagents/_models.py:54-57`), so Deep Agents' provider profiles apply (for example the
OpenAI responses API and OpenRouter headers). `init_chat_model` in langchain 1.4.7 lists
`openrouter` among its built-in providers (`langchain/chat_models/base.py:94`), so all three
installed providers work through the one string and FR-016 holds: the identifier appears only in
settings. `ChatOpenRouter` requires `OPENROUTER_API_KEY` at construction (`chat_models.py:477-481`),
which is why tests must never resolve a real provider string.

**Alternatives considered**: building the model in Freyja to pass temperature or timeouts.
Nothing in the spec needs that, and it would duplicate the provider-profile logic.

## R4. Streaming modes and the SSE event mapping

**Decision**: `agent.astream(input, config, stream_mode=["updates", "messages"] + (["debug"] if
debug else []), durability="sync")`. Chunks arrive as `(mode, payload)` tuples
(`pregel/main.py:4426-4429`). Mapping:

| LangGraph chunk | SSE event |
|---|---|
| (emitted by Freyja before `astream`) | `start` |
| `("updates", {node: update})` | `update` with `node` and the serialized update |
| `("updates", {"__interrupt__": (Interrupt, ...)})` | recorded; emitted as terminal `interrupt` after the stream drains (R5) |
| `("messages", (chunk, metadata))` | `message` |
| `("debug", {...})` | `debug` (only with the flag) |
| stream exhausted, no interrupt | `end` |
| exception | `error` (R10, R12) |

Payloads are JSON-encoded by Freyja (`json.dumps`) and sent as
`ServerSentEvent(data=..., event=kind)`. `EventSourceResponse` does not encode `data`
(`sse_starlette/event.py:48-51`) and treats a yielded dict as constructor kwargs (93-95), so a
raw dict would be sent as a Python repr (probe).

**Rationale**: "updates" is one chunk per completed node, which is the spec's progress event;
"messages" yields `(AIMessageChunk, metadata)` with `langgraph_node` and `langgraph_step`
(`pregel/_messages.py:113`, `_algo.py:653-659`), the spec's message event; "debug" is the same
run with extra `checkpoint`/`task`/`task_result` records (`pregel/_loop.py:1579-1597`), which is
what FR-004 asks for and what principle III requires of a debug mode. The `start` event is sent
before the first model call so SC-001 does not depend on provider latency.

**Alternatives considered**: `stream_mode="values"` (whole state per step, too large);
`subgraphs=True` (no subgraphs in this increment).

## R5. Interrupt durability and terminal-event ordering

**Decision**: never treat the `__interrupt__` chunk as the end of the run. Keep draining
`astream` until it is exhausted, then emit the terminal `interrupt` event and release the run
lock. Use `durability="sync"`.

**Rationale**: in every durability mode the interrupt chunk can be yielded before the
checkpoint write that records it has landed; the write is only submitted when the chunk is
queued (`pregel/_loop.py:515-557`), and the loop yields after each finished task
(`_runner.py:544-545`). Everything is awaited by the time the generator is exhausted
(`_loop.py:2156-2167`, `_executor.py:206-208`). Probe with a slow saver: at chunk time, "async"
had written nothing, "sync" had the checkpoint but not the interrupt; after exhaustion all modes
had the interrupt persisted. Emitting the terminal event after exhaustion is what makes SC-005
(state read right after the stream closes reflects the pause) and FR-014 (resume on another
instance) true. "sync" also means each completed step is durable before the next starts, which is
the behavior the spec promises on client disconnect and crash.

**Alternatives considered**: `durability="async"` (default; slightly faster, but a crash between
steps can lose the last completed step); `"exit"` (loses everything on crash).

## R6. Postgres persistence and the service lifespan

**Decision**: in the FastAPI lifespan, create one
`AsyncConnectionPool(uri, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory":
dict_row}, open=False, min_size=1, max_size=10)`, `await pool.open()`, construct
`AsyncPostgresSaver(pool)` and `AsyncPostgresStore(pool)`, run both `setup()` plus the run-lock
table DDL (R7), build the agent (R1), and store a `Runtime` on `app.state`. On shutdown close the
pool. If any of this fails, let the exception propagate so the process exits with the cause
(FR-008).

**Rationale**: both classes must be constructed on a running loop (`checkpoint/postgres/aio.py:65`,
`store/postgres/aio.py:151`), and the store starts a background task at init
(`store/base/batch.py:64-81`), so module-level construction is impossible. The pool kwargs are the
ones both `from_conn_string` paths set (`aio.py:86-88`; store `aio.py:201-216`); autocommit is
required because `setup()` runs `CREATE INDEX CONCURRENTLY` (`checkpoint/postgres/base.py:85`).
Sharing one pool is safe: each call checks out its own connection (`_ainternal.py:19-21`), the
saver serializes its own operations through an `asyncio.Lock` (`aio.py:428`), and the store needs
no shared lock with a pool (`aio.py:554-558`). `setup()` is idempotent via migration version
tables (`aio.py:95-122`, store `aio.py:227-293`). Opening a pool in its constructor is deprecated
(`psycopg_pool/pool_async.py:166-173`).

**Alternatives considered**: `from_conn_string` context managers (one connection each, no
pooling, and no shared connection for the lock table); a dedicated pool per component (more
connections for no isolation benefit).

## R7. The run lease (`freyja_run_locks`)

**Decision**: the table and four single-statement operations described in
[data-model.md](data-model.md#runlock-freyja-table-freyja_run_locks). `acquire` is an upsert whose
`ON CONFLICT ... DO UPDATE ... WHERE expires_at < now() RETURNING owner` returns a row only when
the lease was free or expired. A heartbeat `asyncio.Task` renews the lease every `ttl / 3`
seconds while the stream runs and is cancelled in the stream's `finally`. `release` deletes only
when `owner` matches, so a late heartbeat or release from a dead run cannot clear a newer lease.
Behind a `RunLocks` protocol sit `PostgresRunLocks` (production, uses the shared pool) and
`InMemoryRunLocks` (tests, with an injectable clock).

**Rationale**: LangGraph has no same-thread guard: two concurrent runs both load the latest
checkpoint, write children with fresh ids, and silently fork (`checkpoint/postgres/base.py:140-147`,
`pregel/_checkpoint.py:48`). The clarified FR-006 requires the rule across instances with expiry,
which is a lease, and a lease with a heartbeat keeps a long but healthy run from being stolen
while still freeing a crashed instance's thread within one TTL (default 60 s). Single-statement
SQL keeps the operations atomic without explicit transactions, which suits the autocommit pool.
`dict_row` and `prepare_threshold=0` are compatible with plain parameterized SQL including
`RETURNING` (probe-free reading of `psycopg/connection_async.py:308-315`).

**Alternatives considered**: Postgres advisory locks (released immediately on connection loss,
but they pin one pooled connection per active run, have no expiry semantics, and are awkward to
query for `run_active`); an in-process set (fails the multi-instance clarification); renewing the
lease on stream events instead of a timer (a provider that does not stream tokens could starve
renewals during a long model call).

## R8. Reading thread state

**Decision**: `snapshot = await agent.aget_state({"configurable": {"thread_id": tid}})`. The
thread has never been used when `snapshot.metadata is None` (equivalently `created_at is None`);
return 404. Otherwise: `messages` from `snapshot.values["messages"]`, serialized as the Message
shape; `pending` from `snapshot.interrupts` (empty tuple when none), mapped from the HITL value to
`PendingDecision`; `run_active` from `RunLocks.is_active`; `updated_at` from `created_at`.

**Rationale**: a never-used thread yields `StateSnapshot(values={}, next=(), metadata=None,
created_at=None, tasks=(), interrupts=())` (`pregel/main.py:1355-1365`, probe). `interrupts` is
the flattened tuple of pending task interrupts (`main.py:1461`), and `Interrupt` carries `value`
and `id` (`types.py:584-612`). The checkpointed `messages` channel holds the full transcript
including tool-call and tool-result messages, which is what the clarified FR-007 asks for.

**Alternatives considered**: checking `values == {}` (ambiguous for a thread whose state is
legitimately empty); reading the `checkpoints` table directly (bypasses the saver's serde).

## R9. Preconditions the API must enforce on runs and resume

**Decision**: before starting any stream, read state (R8) and the lease (R7):

| Request | Condition | Response |
|---|---|---|
| runs | lease held | 409 `run_active` |
| runs | `snapshot.interrupts` non-empty | 409 `decision_pending` |
| resume | `metadata is None` | 404 `thread_not_found` |
| resume | lease held | 409 `run_active` |
| resume | no interrupts | 409 `nothing_pending` |
| resume | decisions fail R2 validation | 422, pending untouched |

Order: acquire the lease first, then read state under the lease, then either refuse (releasing
the lease) or stream. A request that cannot acquire gets 409 `run_active` without reading state.
Two racing requests therefore resolve to exactly one 200, and every other precondition is
evaluated while nobody else can touch the thread. With the opposite order, a resume that passed
its state check while another resume was running could acquire the lease after that run
finished and send `Command(resume=...)` to a thread with nothing pending, which LangGraph
performs as a silent no-op (below); the client would see `start` and `end` with nothing applied.

**Rationale**: LangGraph enforces none of this. A new input on a paused thread discards the
pending interrupt and restarts the step (`pregel/_loop.py:941-952, 1118-1149`, probe), which would
make a proposal vanish without a decision; refusing with `decision_pending` keeps principle I
intact. `Command(resume=...)` on a thread with no interrupt is a silent no-op on a finished thread
and feeds the command to `__start__` as input on a fresh one (`_loop.py:666-669`, `_io.py:88-89`,
probe), so `nothing_pending` and `thread_not_found` must come from Freyja. With several pending
interrupts a bare resume value raises `RuntimeError` (`_loop.py:1033-1037`); keying the resume by
interrupt id (R2) avoids that path. Batched HITL produces one interrupt per step; if more than one
is ever observed the API returns 500 with a clear message rather than guessing.

**Alternatives considered**: treating a new message on a paused thread as an implicit reject
(ambiguous, and it hides a decision from the audit trail).

## R10. Step limit

**Decision**: pass `config={"configurable": {"thread_id": tid}, "recursion_limit":
settings.max_steps}` on every `astream`. Catch `langgraph.errors.GraphRecursionError` and emit
`error` with `code: step_limit` and a message naming the limit and `FREYJA_MAX_STEPS`, and log
a warning with the thread and run ids. Default 50.

**Rationale**: the effective default is not 25: langgraph's `ensure_config` uses 10007
(`_internal/_config.py:42-44`) and `create_deep_agent` presets 9999 through `with_config`
(`deepagents/graph.py:1021-1023`); an explicit per-call value wins in `merge_configs`
(`_config.py:196-198`). One step is one super-step, so a model call plus a tool call is two
steps and a loop that ends with a final answer is three (`_loop.py:789, 1849, 1886-1887`, probe:
limit N runs N node rounds). Completed steps stay checkpointed because `after_tick()` persists
before the next `tick()` detects `out_of_steps` (`_loop.py:658-659, 1381`), which gives the
clarified FR-004a its "thread stays usable". 50 allows roughly 16 tool calls per turn, generous
for a working conversation and still bounded. The limit applies per graph invocation, subagents
included: the `task` tool passes almost no config of its own and the parent's ambient
`recursion_limit` propagates into the child, which binds none (`middleware/subagents.py:856-865`;
`create_sub_agent` returns a plain `create_agent`, 1-56). The worst case per run is the limit in
the parent plus the limit inside each `task` call, bounded but not global; a test with a small
limit and a scripted subagent confirms the propagation.

**Alternatives considered**: a wall-clock timeout (does not bound cost and interacts badly with
long model calls); relying on the Deep Agents preset (effectively unbounded).

## R11. Test strategy

**Decision**:

- `tests/fakes.py`: `ScriptedChatModel(BaseChatModel)` implementing `_generate`, `_llm_type`,
  `bind_tools` (records the tool names it was given, returns `self`), and `_get_ls_params`
  (reports a fixed provider so the harness profile key in R1 is stable). It is scripted per
  turn with `AIMessage`s that may carry `tool_calls`, and returns a final text message when the
  script is exhausted. A variant that always calls `ls` drives the step-limit test. The parent
  agent and the general-purpose subagent share the one model instance, so a script that reads
  "parent calls `task`, subagent calls `send_notification`" drives the gate-through-subagent test.
- Seams: `build_agent(model=..., checkpointer=InMemorySaver(), store=InMemoryStore(),
  workspace_dir=tmp_path)`; `Runtime(agent=..., locks=InMemoryRunLocks(clock=FakeClock()),
  ...)`; `create_app(open_runtime=...)` where `open_runtime` is an async context manager
  factory that the lifespan calls (production default `Runtime.open`). Imports:
  `langgraph.checkpoint.memory.InMemorySaver`, `langgraph.store.memory.InMemoryStore`.
- Restart simulation: a second `create_app` whose runtime shares the first's saver, store, and
  lock store; a fresh `instance_id`.
- Streaming in tests: `with TestClient(app) as client:` (runs the lifespan,
  `starlette/testclient.py:557-584`), then `with client.stream("POST", ...) as r: for line in
  r.iter_lines()` (probe: incremental, backpressured, no deadlock). Never `client.post()` an
  SSE endpoint. Comment lines (`: ping`) are ignored by the test parser.
- `pyproject.toml`: `asyncio_mode = "auto"` for the async unit tests of the lock store.
- Integration tests under `tests/integration/` skip unless `FREYJA_TEST_DATABASE_URI` is set;
  they create and drop the LangGraph and lock tables in that database.

**Rationale**: every stock fake in langchain-core lacks `bind_tools`, and the agent always calls
it (`chat_models.py:2366-2383`, `agents/factory.py:1484-1491`); `GenericFakeChatModel` streams
nothing for tool-call-only messages and then fails (`chat_models.py:231-236`), and
`FakeMessagesListChatModel` cycles its responses forever. A purpose-built fake is the smallest
correct option. Without `_stream`, "messages" mode still yields one complete message per model
call (`pregel/_messages.py:175-188`), enough to assert the event kind. The seams are parameters
of the same factory and app the service uses, so principle III holds: no test-only runtime.

FR coverage map (FR → test module): 001, 001a → `test_thread_ids`, `test_runs`; 002, 003,
004 → `test_runs`; 004a → `test_step_limit`, `test_subagent`; 005 → `test_runs` (continuity),
`test_restart`; 006 → `test_locks`; 007 → `test_state`; 008 →
`integration/test_postgres_runtime` (startup failure), `test_config`; 009 to 013 → `test_gate`,
`test_subagent` (gate holds through the subagent); 014 → `test_restart`,
`integration/test_postgres_runtime`; 015, 016, 017 → `test_factory`; 018 → quickstart
(manual) plus `integration` using the compose database; 019 → `test_config`; 020 → README
review; 021 → the suite itself.

**Alternatives considered**: Postgres in every test via testcontainers (a new dependency and a
Docker requirement for `uv run pytest`, which AGENTS.md says the suite must not need); mocking
the agent (would not exercise the HITL middleware, which is the point).

## R12. Client disconnect and cleanup

**Decision**: the stream generator wraps the loop in `try/finally`. On disconnect
`EventSourceResponse` cancels the generator, which surfaces as `CancelledError` at the current
`await`. The `finally` cancels the heartbeat task (synchronous) and releases the lease inside
`anyio.CancelScope(shield=True)` so the awaited database call completes. Unexpected exceptions
become an `error` event with `code: internal` and a generic message; the exception is logged
with the run id.

**Rationale**: sse-starlette flips `active`, cancels its task group, and `aclose()`s the body
iterator on disconnect (`sse_starlette/sse.py:388-421, 556-558`); no polling is needed. An
unshielded `await` in the generator's `finally` is cancelled again (probe), which would leak the
lease until it expires. Shielding only the release keeps the window small. Completed steps are
already durable under `durability="sync"` (R5), which is the spec's disconnect edge case.

**Alternatives considered**: `asyncio.create_task` for the release (works, but detaches error
handling); relying on lease expiry (correct eventually, but refuses the thread for up to one TTL).

## R13. Local Postgres compose file

**Decision**: `docker-compose.yml` at the repo root with one service, `postgres`, image
`postgres:17-alpine`, `POSTGRES_USER=freyja`, `POSTGRES_PASSWORD=freyja`, `POSTGRES_DB=freyja`,
port `5432:5432`, a named volume, and a `pg_isready` healthcheck. The values equal
`FREYJA_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja` in `.env.example` (FR-018).
A second database for integration tests is created by the developer (`createdb freyja_test`
through the container) and referenced by the commented `FREYJA_TEST_DATABASE_URI`.

**Rationale**: the credential is already documented as the local container value; the compose
file makes that statement true. Docker 29 with Compose v5 is available locally; the file name
`docker-compose.yml` is what the feature description calls it and Compose v2+ accepts it.

**Alternatives considered**: `compose.yaml` (equally valid; the description's wording wins); a
second compose service for the test database (an extra container for a one-line `createdb`).

## R14. Skills

**Decision**: commit `workspace/skills/case-notes/SKILL.md` with front matter `name:
case-notes`, `description: ...`, and a short body telling the orchestrator to keep working notes
under `cases/<thread_id>/notes.md`. Pass `skills=["/skills/"]`.

**Rationale**: `SkillsMiddleware` lists `<source>/<dir>/SKILL.md` through the backend
(`middleware/skills.py:687-744`), requires `name` and `description` in the front matter
(504-508), requires `name` to equal the directory name (402-440, warning only), and injects a
skills list into the system prompt (873-913, 1105-1116). Skills are loaded at the first run of a
thread and cached in checkpointed state (`skills_metadata`, 1168-1212), so a skill added to the
repository is available to new threads immediately and to the service after restart, which
meets FR-017; existing threads keep the list they loaded. The placeholder skill is about the
workspace itself, not a real capability, matching the spec's assumption.

**Alternatives considered**: no committed skill (skill loading would be untestable); a skill
describing a real-world procedure (out of scope).

## R15. The `send_notification` placeholder

**Decision**: a `@tool` with arguments `channel: str` and `message: str`, docstring stating it is
a placeholder that only logs. It emits one `WARNING` record on logger `freyja_agent.actions`
with `channel` and `message`, and returns `"Notification to {channel} recorded (placeholder:
logged only)."`. Its `interrupt_on` description reads "Send a notification to a channel
(placeholder: logs only)." The tool's `args_schema` is what R2 validates edited arguments
against.

**Rationale**: a notification is the simplest action with an effect outside the workspace that
every deployment of a general-purpose agent will eventually want, and it is visibly
irreversible, which makes it a good stand-in for the gate. A structured log line is observable
in tests with `caplog` (FR-009, SC-002, SC-003) and in the quickstart through the service log.
Returning a sentence gives the model something to acknowledge (User Story 2, scenario 2). The
two arguments exercise the edit path with more than one field.

**Alternatives considered**: writing to a working file instead of logging (the description says
"only logs"); a destructive placeholder such as deleting a record (harder to motivate in a
quickstart, and a notification already proves the gate).
