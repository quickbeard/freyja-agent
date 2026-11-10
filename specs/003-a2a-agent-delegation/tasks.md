# Tasks: A2A Agent Delegation

**Input**: Design documents from `/specs/003-a2a-agent-delegation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi.yaml,
contracts/agent-config.md, contracts/sse-events.md, quickstart.md

**Tests**: Included. FR-026 requires automated coverage for every behavior in this feature, and
SC-009 requires the suite to run with no network and no external agent installed. In each user
story phase the test tasks come first: write them, watch them fail, then implement. Phase 2 is
the exception: its fixtures are built on the code, so its tests close the phase.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. User Story 4 is split over two phases (6 and 7) because it is the largest. Research
references (R1 to R17) point at `research.md`. "The stub" is the stand-in agent in
`tests/a2a_stub.py`; "the streaming transport" is `tests/asgi_stream.py`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1 to US7)
- Every task names the file it touches

## Path Conventions

Single project, src layout: `src/freyja_agent/`, `tests/`, repo-root files.

## Standing rules for every task

- Every resume request in a test or an example sends the proposal's `interrupt_id`
  (feature 001, FR-013).
- A message to an agent not marked read-only is never sendable without the approval gate at the
  end of any phase: Phase 2 builds the agent tools, their classification, and the gate before any
  later phase makes a tool send something.
- No new Ruff suppression; no blocking I/O in async code; SQL only with bound parameters
  (principle V, Ruff `ASYNC` and `S608`).
- The suite never opens a socket except in `tests/test_a2a_transport.py`, which uses loopback.
- A manual check stops any service it started by its port (`ss -ltnp`), never by a process
  pattern.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: The dependency, the seven settings, and their documentation.

- [X] T001 Add `a2a-sdk>=1.3,<2` and `httpx>=0.28.1` to `[project].dependencies` and
      `a2a-sdk[http-server]>=1.3,<2` to the `dev` group in `pyproject.toml`, then run `uv lock`;
      confirm the lock gains exactly `a2a-sdk`, `aiologic`, `culsans`, `google-api-core`,
      `json-rpc`, `proto-plus`, and `wrapt`, and that no other version moves (R1)
- [X] T002 [P] Add tests for the new settings to `tests/test_config.py`: defaults
      `a2a_agents == {}`, `a2a_call_timeout_seconds == 30`, `a2a_startup_timeout_seconds == 20`,
      `a2a_poll_interval_seconds == 10`, `a2a_task_max_age_seconds == 86400`,
      `background_run_attempts == 3`, `background_retry_seconds == 60`; `FREYJA_A2A_AGENTS`
      parses from one JSON value; each limit refuses zero and a negative number (FR-004, R2)
- [X] T003 [P] Write `tests/test_a2a_config.py` for one agent entry: accepted with `url`
      `http://localhost:9999`, `http://127.0.0.1:9999`, `http://[::1]:9999`, and
      `http://[::ffff:127.0.0.1]:9999`, with `read_only` omitted (then `false`) and set; refused,
      with an error located at `a2a_agents.<name>`: a missing `url`, a scheme other than `http`
      or `https`, the hosts `10.0.0.5`, `0.0.0.0`, `::ffff:10.0.0.5`, and `example.com`, an
      unknown field, and a name that does not match `^[a-z][a-z0-9_-]{0,31}$`; no refusal echoes
      the entry's values (FR-001, contracts/agent-config.md "Rules")
- [X] T004 Implement the settings in `src/freyja_agent/config.py`: `A2aAgentConfig` with
      `extra="forbid"`, `hide_input_in_errors=True`, `url: str` (scheme `http` or `https`, host
      accepted by the existing `is_local_host`), and `read_only: bool = False`; on `Settings`,
      `a2a_agents: dict[ServerName, A2aAgentConfig]` defaulting to empty, and the six limits with
      the defaults in T002, each `Field(gt=0)` (attempts as an int) (FR-001, FR-004, R2)
- [X] T005 [P] Document the seven settings in `.env.example` with their defaults and one
      commented `FREYJA_A2A_AGENTS` example taken from contracts/agent-config.md (FR-004,
      principle IV)

**Checkpoint**: `uv run pytest tests/test_config.py tests/test_a2a_config.py` passes; nothing
else changed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The task store, the agent links with every startup check, the agent tools with
their classification and gate, the runtime wiring with its one new seam, and the stream change.
This is the startup half of User Story 6, which every other story needs.

**CRITICAL**: No user story work can begin until this phase is complete.

- [X] T006 [P] Write the streaming transport in `tests/asgi_stream.py`: an
      `httpx.AsyncBaseTransport` that runs an ASGI app in-process and hands each response body
      chunk to the client as the app sends it, so a streamed reply arrives event by event; it
      also records each request's method, host, port, and path for the address tests (R16 probe)
- [X] T007 [P] Write the stub in `tests/a2a_stub.py` with the SDK's server classes
      (`DefaultRequestHandler`, `InMemoryTaskStore`, `create_agent_card_routes`,
      `create_jsonrpc_routes`): a factory `make_stub(...)` returning the ASGI app and a record of
      every message (parts, `context_id`, `task_id`, `message_id`) and every cancel it received.
      Behavior by keyword in the message text: by default it completes with the message and the
      conversation's context id; `ask` asks one question (input required) and completes once
      answered in the same task; `slow <seconds>` stays working that long, then completes;
      `fail` fails; `reject` rejects; `parts` completes with a text, a data, a raw-bytes, and a
      URL part; `progress <n>` sends n status updates before completing; `message` replies with
      a direct message instead of a task; `auth` moves to authentication required; `longid`
      replies with a task id of 300 characters. Cancel is supported. Card options: streaming on
      or off, security requirements set, default input modes without text, no JSON-RPC
      interface, an advertised address of `http://elsewhere.example:1234/a2a`, no skills, and a
      card that claims to be read-only in its description. Run as
      `python -m tests.a2a_stub --http <port>` it serves on `127.0.0.1:<port>` (R16)
- [X] T008 [P] Implement the store protocol and the in-memory store in
      `src/freyja_agent/runtime/agent_tasks.py`: `AgentTaskStore` with `setup()`; conversations
      (`get_context(thread_id, agent)`, `set_context(...)`); sends keyed by
      `(thread_id, tool_call_id)` with `agent`, `kind` (`handoff`, `answer`, or `cancel`),
      `message_id`, `task_id` (null until acknowledged), `sent_at`, and `result` (null while in
      flight); tasks keyed by `(agent, task_id)` with every column in data-model.md
      "Agent task", `state` one of `working`, `input_required`, `completed`, `failed`,
      `rejected`, `canceled`, `given_up`; `list_tasks(thread_id)` oldest first;
      `claim_due(instance_id, lease_seconds)`; `ready_outcomes(thread_id)`;
      `mark_delivered(...)`; and the follow-up fields; `InMemoryAgentTaskStore` takes an
      injectable clock and uses it for every time it sets or compares (R8)
- [X] T009 Implement `PostgresAgentTaskStore` in `src/freyja_agent/runtime/agent_tasks.py`:
      `CREATE TABLE IF NOT EXISTS` for `freyja_agent_conversations`, `freyja_agent_sends`, and
      `freyja_agent_tasks` with the columns and keys in data-model.md and an index on
      `freyja_agent_tasks(thread_id)`; every statement single and parameterized; the claim is one
      `UPDATE ... WHERE (agent, task_id) IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING`
      setting `watch_until = now() + lease_seconds` and judging age by the database's `now()`
      (R8, R9, spec Assumptions)
- [X] T010 [P] Extend `src/freyja_agent/agent/catalog.py`: `CatalogTool.agent: str | None`
      (null for every tool that is not an agent's) and `pinned_args: tuple[str, ...]`;
      `allowed_decisions` gives approve and reject only for a gated tool whose every declared
      argument is pinned; `gate_description` returns `<agent>: hand-off` for a hand-off tool and
      `<agent>: cancel a task` for a cancel tool; `RESERVED_TOOL_NAMES` unchanged (FR-013, R4,
      R12)
- [X] T011 [P] Add `agent: str | None = None` and `pinned_args: tuple[str, ...] = ()` to
      `DelegateTool` in `src/freyja_agent/delegates/mcp_tools.py`, and pass both through
      `_delegate_entry` in `src/freyja_agent/agent/factory.py` (R15)
- [X] T012 Implement the links in `src/freyja_agent/delegates/a2a_agents.py`: `AgentLink` owning
      one `httpx.AsyncClient` (`follow_redirects=False`, the SDK's version header, the call time
      limit as timeout, the transport from the seam); `start()` reads the card from
      `<url>/.well-known/agent-card.json` with `A2ACardResolver` within the startup limit, and
      refuses with `AgentStartupError("Agent '<name>': <reason>")` when: it cannot be read or is
      redirected; `security_requirements` or `security_schemes` is non-empty; non-empty
      `default_input_modes` name no `text/*` type; no interface uses the `JSONRPC` binding.
      It copies the card, replaces the scheme, host, and port of every interface URL with the
      configured ones keeping only the path, and builds the client with `ClientFactory.create`.
      `AgentCardSummary` keeps `name`, `description`, `version`, and each skill's `id`, `name`,
      `description`, `tags`. Status fields `reachable`, `checked_at`, `error` (at most 500
      characters, null when reachable); `describe(text)` removes the configured address in URL,
      host-and-port, and host forms from every text that is logged, raised, or stored.
      `open_agents(configs, *, transport, startup_timeout, call_timeout, server_names)` starts
      all links concurrently, refuses a name that is also a tool server's, closes every link on
      the way out, and logs `Agent <name>: read-only|gated, <n> skill(s)` (FR-001, FR-002,
      FR-003, FR-009 address rule, R3, R14)
- [X] T013 Build the tools in `src/freyja_agent/delegates/a2a_tools.py`: per link,
      `handoff_to_<agent>` with a pydantic model `message: str` (min length 1, max length
      32,000), `context: str | None` (max length 32,000), `task_id: str | None`, pinned
      `task_id`; and `cancel_<agent>_task` with `task_id: str`, pinned `task_id`; both
      classified `read_only` when the entry says `read_only: true` and `gated` otherwise,
      returned as `DelegateTool` with `agent` set and `server` null. The hand-off description is
      the agent's name, its card name and description, and one line per skill, cut at 2,000
      characters. Until later phases the coroutines raise `ToolException` naming the agent and
      saying hand-offs are not available yet, so nothing is sent (FR-011, FR-013, R4, R12)
- [X] T014 Wire the runtime in `src/freyja_agent/runtime/runtime.py`: `Runtime` gains `agents`
      and `agent_tasks`; `Runtime.assemble(..., agent_tasks, agent_transport=default_transport)`
      opens the agent links after the tool servers, passes the server names for the shared
      namespace, and adds the agent tools to `delegate_tools`; `Runtime.open` builds
      `PostgresAgentTaskStore` on the shared pool and runs its `setup()` beside the run-lock
      table. Order: persistence and tables, tool servers, agent links, the agent, yield; close
      the links, the tool servers, the pool (FR-002, R15)
- [X] T015 Change `src/freyja_agent/api/streaming.py`: call `astream(..., subgraphs=True)` with
      the `custom` mode added; read `updates` and `__interrupt__` from the root namespace only,
      forward `messages` from every namespace as today, and emit every `custom` chunk as an
      `update` event with `node: "agent"` and the chunk as `data`; accept the mode `background`
      in `RunMode`; add an optional callback receiving the terminal event's kind and code. Run
      the existing suite before anything else depends on it: `tests/test_runs.py`,
      `tests/test_subagent.py`, `tests/test_gate.py`, and `tests/test_state.py` must pass
      unchanged (contracts/sse-events.md, R7)
- [X] T016 Add fixtures to `tests/conftest.py`: a fake clock; `make_a2a_app(model, agents=...,
      read_only=..., store=..., servers=...)` assembling the app the production way with
      `InMemoryRunLocks`, an `InMemoryAgentTaskStore`, and `agent_transport` returning the
      streaming transport bound to each agent's stub app; and access to each stub's record
- [X] T017 [P] Write `tests/test_a2a_startup.py`: one agent's card is read, its two tools reach
      the orchestrator and the subagent, and the log line names it, its classification, and its
      skill count; startup fails within the startup limit with an error beginning `Agent '<name>'`
      for an agent that is not answering, a card fetch that is redirected, a card with security
      requirements, a card whose input modes are not text, a card with no JSON-RPC interface, an
      agent named like a configured tool server, and a tool name taken by another tool; a card
      with no skills starts; a card advertising `elsewhere.example` gets every request sent to
      the configured host and port with the card's path, as the streaming transport records; no
      error text contains the configured address (FR-002, FR-003, FR-009 address rule, SC-005)
- [X] T018 [P] Add to `tests/test_factory.py`: an agent marked read-only gives two `read_only`
      tools and nothing in `interrupt_on`; an agent not marked gives two `gated` tools in
      `interrupt_on` on the orchestrator and the subagent, the hand-off with approve, edit, and
      reject, the cancel with approve and reject; a card that claims to be read-only changes
      nothing (FR-011, FR-013, US2 scenario 5)
- [X] T019 [P] Add to `tests/test_runs.py`: with subgraph streaming on, a run through the
      subagent still produces no `update` event from inside the subagent, the `interrupt` event
      is unchanged, and `message` events from the subagent still arrive (contracts/sse-events.md
      "Steps inside a subagent", R7)

**Checkpoint**: the service starts with agents configured, refuses every bad card or name, and
every agent tool is classified and gated. No hand-off sends anything yet.

---

## Phase 3: User Story 1 - Hand work to an agent and get its answer (Priority: P1) 🎯 MVP

**Goal**: A read-only agent receives the orchestrator's task message and chosen context, its
progress streams, its reply comes back as the result, and the thread keeps one conversation per
agent across restarts.

**Independent Test**: with one stub agent marked read-only, a run that hands off completes with
the stub's reply, progress events, and no pause; a follow-up after a restart reaches the same
context id.

### Tests for User Story 1

- [X] T020 [P] [US1] Write `tests/test_a2a_handoff.py` part 1: a read-only hand-off directly and
      through the subagent completes without a pause and with the stub's reply in the result;
      the stub received exactly two text parts, the message and the context with metadata
      `{"freyja": "context"}`, and nothing else from the thread; `progress 3` produces three
      `update` events with `node: "agent"` in order, before the `tools` update with the result;
      with the stub's card set not to stream, each status change Freyja observes appears as a
      progress event, in order;
      `parts` gives the text, compact JSON for the data part, `[file omitted: <media type>]` and
      `[file link omitted: <media type>]` notes, and no bytes in the thread (FR-005, FR-006,
      FR-007, SC-002, US1 scenarios 1 to 4, 6)
- [X] T021 [P] [US1] Write `tests/test_a2a_handoff.py` part 2: two hand-offs from one thread to
      one agent carry the same `context_id`, also across a new runtime on the same store; a
      hand-off from another thread gets a different one; a `message` reply (no task) continues
      the conversation and creates no task row (FR-008, FR-017, SC-004, US1 scenario 5)
- [X] T022 [P] [US1] Write `tests/test_a2a_handoff.py` part 3: a tool call whose send is already
      in the ledger with a result returns that result and sends nothing; one in flight (result
      null) returns an error saying the agent may or may not have received it and sends nothing
      (FR-010, data-model.md "Agent send")

### Implementation for User Story 1

- [X] T023 [P] [US1] Implement `a2a_result_text(task_or_message)` in
      `src/freyja_agent/delegates/a2a_tools.py` with the table in research R6: `text` as text,
      `data` as compact JSON, `raw` as `[file omitted: <media type or "unknown type">]`, `url` as
      `[file link omitted: <media type or "unknown type">]`; artifacts in order, then the final
      status message unless it repeats the last artifact's text (FR-006, R6)
- [X] T024 [US1] Implement `AgentLink.send(...)` in `src/freyja_agent/delegates/a2a_agents.py`:
      the message (role user, `message_id` derived from thread and tool call id, `context_id`,
      optional `task_id`, the two parts); streaming send when the card streams, otherwise
      non-streaming with `return_immediately` and `get_task` every second; report each status
      update with a message and each artifact update to a progress callback; return on a
      terminal state, `input_required`, a direct message, the time limit, or a failure, with
      whether it was acknowledged; mark reachability (R5)
- [X] T025 [US1] Implement the hand-off coroutine in `src/freyja_agent/delegates/a2a_tools.py`
      following research R5 steps 1 to 6: ledger lookup and row before sending; conversation
      read and written on acknowledgment; a task row recorded on acknowledgment; progress
      written with `get_stream_writer()` as `{"agent", "task_id", "state", "text"}`; a completed
      task or a direct message returns its text headed with the agent and task id. Every other
      ending returns an error result naming the agent and the state until Phases 5, 6, and 9
      handle it. Inject the thread id from the run config and the tool call id
      (FR-005, FR-007, FR-008, FR-010, R5)
- [X] T026 [US1] Add one paragraph to `src/freyja_agent/agent/prompts.py`: an agent tool hands
      a self-contained task to another agent; write the message and include only the context
      the agent needs; relay the reply in your own words (FR-005, FR-015)

**Checkpoint**: quickstart Scenario 2 works against the stub; User Story 1 is complete.

---

## Phase 4: User Story 2 - Every hand-off to an agent not marked read-only waits for a person (Priority: P2)

**Goal**: A hand-off to a gated agent pauses with the agent, message, and context shown;
approve, edit, and reject do what they say; edits cannot change the agent or the task.

**Independent Test**: with one stub agent not marked read-only, on separate threads approve,
edit, and reject a proposed hand-off; the stub's record shows exactly the expected messages.

### Tests for User Story 2

- [X] T027 [P] [US2] Write `tests/test_a2a_gate.py`: the proposal pauses before the stub
      receives anything, and the pause and the thread state name `handoff_to_stub`,
      `agent: "stub"`, `server: null`, the message, and the context; approve sends exactly one
      message as proposed; an edit of message and context sends the edited version; edits that
      leave `message` empty, change `task_id`, or add a key such as `agent` are refused with 422
      and the proposal stays; reject with a reason sends nothing and the reply repeats it; a
      card claiming to be read-only is still gated; a subagent's hand-off pauses the same way; a
      step with a gated hand-off and a read-only call runs neither before the decision
      (FR-011, FR-012, FR-013, SC-001, US2 scenarios 1 to 7)
- [X] T028 [P] [US2] Add to `tests/test_a2a_gate.py`: a proposal decided after a restart with a
      changed configuration: agent removed, approve and reject accepted and nothing sent; agent
      now read-only, approve only; a step whose call became gated, resume fails and the proposal
      stays; a new answer or cancel for a task started before the agent's read-only mark changed
      is classified under the configuration in force (FR-014)
- [X] T029 [P] [US2] Add to `tests/test_gate.py` and `tests/test_state.py`: every action request
      carries `agent`, null for Freyja's own tools and for tool server tools

### Implementation for User Story 2

- [X] T030 [US2] Add `agent: str | None = None` to `ActionRequest` in
      `src/freyja_agent/api/schemas.py`, and fill it in `interrupt_to_pending` in
      `src/freyja_agent/api/serialize.py` from the catalog in force (null when the tool is not an
      agent's or is no longer configured) (FR-013, contracts/openapi.yaml `ActionRequest`)
- [X] T031 [US2] Refuse pinned arguments in `src/freyja_agent/api/decisions.py`: after model
      validation, an edit whose value for a pinned argument differs from the proposal's is
      refused with a 422 entry of type `value_error` at that argument (FR-013, R12)
- [X] T032 [US2] Add `agent` to `ActionRequest` in
      `specs/001-thread-runs-api/contracts/openapi.yaml` and
      `specs/002-mcp-tool-delegation/contracts/openapi.yaml`, matching
      `specs/003-a2a-agent-delegation/contracts/openapi.yaml` (plan Implementation Notes)

**Checkpoint**: quickstart Scenario 3 works; no message reaches a gated agent without a
decision.

---

## Phase 5: User Story 3 - An agent asks for more input (Priority: P3)

**Goal**: An agent's question reaches the user in the orchestrator's voice; the user's answer
continues the same task, gated like any message to that agent.

**Independent Test**: hand off `ask: ...` to the stub, see the question in the reply and the task
as `input_required` in the thread state, answer, approve, and see the same task complete.

### Tests for User Story 3

- [ ] T033 [P] [US3] Write `tests/test_a2a_input.py`: the result carries the stub's question,
      is marked as needing input, and names the task id; the scripted reply asks the user; the
      thread state lists the task as `input_required`; the next user message leads to a hand-off
      with that `task_id`, which pauses for a gated agent with the answer as the orchestrator
      wrote it, and on approval the stub completes the same task; an unrelated message leaves
      the task `input_required`; an answer from the subagent continues the task the same way
      (FR-015, FR-016, US3 scenarios 1 to 4)
- [ ] T034 [P] [US3] Add to `tests/test_state.py`: `agent_tasks` is present, empty for a thread
      with no agent tasks, and lists tasks oldest first with every field of `AgentTask` in
      contracts/openapi.yaml (FR-017)

### Implementation for User Story 3

- [ ] T035 [US3] Handle `input_required` in the hand-off coroutine in
      `src/freyja_agent/delegates/a2a_tools.py`: return the question headed
      `Agent '<agent>' needs input for task <task_id>:`, record the task as `input_required`
      with the outcome marked delivered; with `task_id` set, send in that task and conversation,
      and record the send as `answer` (FR-015, FR-016)
- [ ] T036 [US3] Add `AgentTask` (fields `agent`, `task_id`, `message`, `status` one of
      `working`, `input_required`, `completed`, `failed`, `rejected`, `canceled`, `given_up`,
      `started_at`, `last_checked_at`, `ended_at`, `outcome_delivered`) and
      `ThreadState.agent_tasks` to `src/freyja_agent/api/schemas.py`, and fill it from the store
      in `read_state` in `src/freyja_agent/api/threads.py`; add `agent_tasks` to `ThreadState`
      in `specs/001-thread-runs-api/contracts/openapi.yaml` (FR-017, contracts/openapi.yaml)
- [ ] T037 [US3] Extend the paragraph in `src/freyja_agent/agent/prompts.py`: when an agent
      needs input, ask the user in your own words and continue the same task with its id once
      they answer (FR-015, FR-016)

**Checkpoint**: quickstart Scenario 5 works.

---

## Phase 6: User Story 4, part 1 - Pending tasks and their delivery (Priority: P4)

**Goal**: A task still working at the call limit becomes pending, is watched by one instance,
and its outcome is delivered exactly once, by the user's next run or by a background run that
follows every rule of a run.

**Independent Test**: hand off `slow 60` with a 5-second limit, see the pending note and the
task `working` in the thread state, let the stub finish, advance the fake clock, tick the
worker, and see one outcome and one reply in the thread; repeat with a restart in between.

### Tests for User Story 4, part 1

- [ ] T038 [P] [US4] Write `tests/test_a2a_pending.py`: at the call limit after acknowledgment
      the result says the task is pending and names the agent and task id, the run ends with
      `end`, and the thread state lists the task `working` with `outcome_delivered: false`;
      before acknowledgment nothing becomes pending (covered in Phase 9); a tick claims the task
      for one instance only, also with two runtimes sharing one store; a restarted runtime
      watches it again; status changes to completed, failed, rejected, canceled by the agent,
      input required, not found (ends `failed` with that reason), and authentication required
      (ends `failed`) are recorded with an outcome ready; with the fake clock, the instance
      holding the claim stops ticking, the other runtime claims the task once `watch_until`
      passes (3 × the interval), and the outcome is delivered within 60 s of the task changing
      (FR-017, FR-019, FR-020, SC-003, US4 scenarios 1, 3, 4, 6, 7)
- [ ] T039 [P] [US4] Write `tests/test_a2a_background.py`: a ready outcome on a free thread is
      delivered by one background run whose input is one `check_agent_tasks` call and one tool
      result per task with ids `a2a-outcome-<agent>-<task id>`, followed by the reply; a second
      tick delivers nothing; across a restart it is still delivered once; a thread with a run in
      progress or a pending proposal waits, then gets it; a user's run started first records the
      outcome before the user's message and no background run follows; a resume's continuation
      records nothing; a background run whose model proposes a gated action pauses, the state
      shows the proposal, and the resume route continues it; several outcomes on one thread are
      delivered by one run in the order they were recorded; a pending task started by a
      subagent's hand-off is delivered to the orchestrator of its thread (FR-018, FR-020,
      SC-003, US4 scenarios 2, 5, 6, 11, Edge Cases)
- [ ] T040 [P] [US4] Add to `tests/test_a2a_background.py`: on each model call the system message
      lists the thread's pending tasks (agent, task id, status, start time), and nothing when
      there are none; `check_agent_tasks` lists the thread's tasks, or the named ones, and is
      `read_only` with no pause (FR-018, US4 scenario 10)
- [ ] T041 [P] [US4] Write `tests/integration/test_a2a_tasks_postgres.py`: the three tables are
      created idempotently; two runtimes on one database claim a task once; an expired claim
      passes to the other instance; the ledger and conversations survive a new pool (FR-019,
      R8, R9)

### Implementation for User Story 4, part 1

- [ ] T042 [P] [US4] Implement `src/freyja_agent/agent/agent_tasks.py`: `AgentTasksMiddleware`,
      which appends one line per pending task of the current thread (from the run config) to the
      system message on every model call, and the `check_agent_tasks(task_ids: list[str] | None)`
      tool reading the store; add `check_agent_tasks` to `OWN_TOOLS` in
      `src/freyja_agent/agent/catalog.py` as `READ_ONLY`; pass the store to both through
      `build_agent(..., agent_tasks=...)` in `src/freyja_agent/agent/factory.py`, on the
      orchestrator and the subagent (FR-018, R10, R11)
- [ ] T043 [US4] Handle the time limit after acknowledgment in the hand-off coroutine in
      `src/freyja_agent/delegates/a2a_tools.py`: record the task `working` with
      `outcome_ready = false`, and return `Agent '<agent>' is still working on task <task_id>;
      it is pending, and Freyja will report its outcome in this thread.` (FR-017)
- [ ] T044 [US4] Implement `src/freyja_agent/api/outcomes.py`: build the outcome messages of
      data-model.md "Outcome messages" (an `AIMessage` with one `check_agent_tasks` call and
      one `ToolMessage` per task, ids `a2a-outcome-call-<first agent>-<first task id>` and
      `a2a-outcome-<agent>-<task id>`, content headed with agent, task id, and state); and
      `take_undelivered(runtime, thread_id, snapshot)`, which marks as delivered every ready
      outcome already in the snapshot and returns the messages for the rest (FR-020, R10)
- [ ] T045 [US4] In `start_run` in `src/freyja_agent/api/threads.py`, after the paused-thread
      check, prepend the undelivered outcome messages to the input and mark them delivered when
      the run's first `update` event arrives, which with `durability="sync"` comes after the
      checkpoint holding the input; if the run fails before that, they stay marked undelivered
      and the next run's check (T044) finds them already in the thread; `resume_run` records
      nothing (FR-018, R10)
- [ ] T046 [US4] Implement `src/freyja_agent/delegates/delegation_worker.py`:
      `DelegationWorker(runtime, clock)` with `tick()` and a loop at the poll interval; each
      tick claims due tasks (`watch_until = now + 3 × interval`), checks each `working` task with
      `get_task`, records the new state and the outcome text (`a2a_result_text`), treats a
      not-found answer and authentication required as `failed` with that reason, marks the
      agent unavailable on a failed check and leaves the task, skips `input_required` tasks;
      then for each thread with ready outcomes takes the run lease, skips it if a proposal is
      pending, and runs `run_stream(..., mode="background")` to completion with the outcome
      messages as input (FR-019, FR-020, R9, R10)
- [ ] T047 [US4] Start the worker in `Runtime.assemble` in `src/freyja_agent/runtime/runtime.py`
      after the agent is built and stop it before the links close; tests get it without its
      loop and call `tick()` (R15)
- [ ] T048 [US4] Extend the paragraph in `src/freyja_agent/agent/prompts.py`: a pending task is
      reported later in this thread; use `check_agent_tasks` to answer questions about it
      (FR-018)

**Checkpoint**: quickstart Scenario 6 works; outcomes are delivered exactly once.

---

## Phase 7: User Story 4, part 2 - Cancel, give-up, and retries (Priority: P4)

**Goal**: A pending or given-up task can be canceled through the gate; a task past the maximum
age is given up without a cancel; a failed background run is retried without duplicating
anything.

**Independent Test**: cancel a `slow 600` task with approval; give one up with a short maximum
age and see no cancel at the stub; make the model fail twice in a background run and see one
outcome, one message to the stub, and the third attempt succeed.

### Tests for User Story 4, part 2

- [ ] T049 [P] [US4] Add to `tests/test_a2a_pending.py`: `cancel_stub_task` pauses with approve
      and reject only, and once approved the task is `canceled` and a later outcome is ignored;
      a cancel for a task that already ended reports that and its outcome is delivered; a
      rejected or unreachable cancel leaves the task pending and is not repeated; a task that
      reaches the maximum age, counted from `started_at` and including time spent
      `input_required`, is `given_up`, the stub received no cancel, and the delivered report says
      the agent may still be working and offers a cancel; a given-up task can be canceled; a
      cancel proposed by the subagent pauses and works the same way; after
      a restart without its agent, a pending task is given up and reported (FR-021, FR-022,
      FR-023, SC-011, US4 scenarios 8, 9, Edge Cases)
- [ ] T050 [P] [US4] Add to `tests/test_a2a_background.py`: a background run that ends with
      `model_error` is tried again after the retry interval, at most the configured number of
      attempts, each continuing from the last checkpoint; across the attempts the outcome appears
      once and the stub receives each message once, including a step whose send was in flight;
      an attempt that failed before recording the outcome records it on retry; retries stop once
      another run ended with `end`, `interrupt`, or `step_limit`, but not after one that failed;
      remaining attempts resume after a restart; the last failure is logged with the thread and
      task ids; a background run ending with `step_limit` is not retried; a retried attempt that
      proposes a gated hand-off pauses like the first attempt (FR-012, FR-027, SC-010, Edge
      Cases)

### Implementation for User Story 4, part 2

- [ ] T051 [US4] Implement the cancel coroutine in `src/freyja_agent/delegates/a2a_tools.py` and
      `AgentLink.cancel` in `src/freyja_agent/delegates/a2a_agents.py`: ledger as for a hand-off
      with kind `cancel`; on success record `canceled` with no outcome to deliver; when the agent
      says the task cannot be canceled because it ended, say so and leave the outcome to the
      worker; every failure an error result, never repeated (FR-022)
- [ ] T052 [US4] Give tasks up in `src/freyja_agent/delegates/delegation_worker.py`: a claimed
      `working` or `input_required` task older than the maximum age, or whose agent is no longer
      configured, becomes `given_up` with the outcome `Freyja stopped waiting for task <task_id>
      of agent '<agent>'; the agent may still be working on it. Ask to cancel it if it should
      stop.`; no cancel is sent; an outcome arriving for a `canceled` task is ignored (FR-021,
      FR-022, FR-023)
- [ ] T053 [US4] Retry background runs in `src/freyja_agent/delegates/delegation_worker.py`: on a
      terminal `model_error` or `internal`, set `followup_attempts`, `followup_next_at`, and
      `followup_error` on the delivered rows; a due retry repeats the delivery check, then runs
      with input `None`; give up after `background_run_attempts` with a log line naming the
      thread and the task ids (FR-027, R10)
- [ ] T054 [US4] Clear the follow-up fields of a thread when any run on it ends with `end`,
      `interrupt`, or `step_limit`, through the terminal callback in
      `src/freyja_agent/api/streaming.py` (FR-027)
- [ ] T055 [US4] Extend the paragraph in `src/freyja_agent/agent/prompts.py`: cancel a pending or
      given-up task only when the user asks, with the cancel tool and the task id (FR-022)

**Checkpoint**: quickstart Scenario 7 works; User Story 4 is complete.

---

## Phase 8: User Story 5 - See which agents Freyja can hand off to (Priority: P5)

**Goal**: The catalog lists every agent with its card's claims, classification, and
reachability, and every agent tool with its agent.

**Independent Test**: read `GET /tools` with one gated and one read-only stub agent configured.

### Tests for User Story 5

- [ ] T056 [P] [US5] Write `tests/test_a2a_catalog.py`: `agents` lists each configured agent in
      configuration order with `reachable`, `checked_at`, `error`, and `card` (`name`,
      `description`, `version`, `skills` with `id`, `name`, `description`, `tags`); agent tools
      carry `agent` and the classification the entry implies; a card's read-only claim leaves the
      agent gated and appears only under `card`; no response contains an agent's address; reading
      makes no request to any stub; with no agents, `agents` is empty and `tools` equals the
      catalog of feature 002; the response validates against
      `specs/003-a2a-agent-delegation/contracts/openapi.yaml`; the response arrives within 1
      second (FR-024, SC-007, US5 scenarios 1 to 4)

### Implementation for User Story 5

- [ ] T057 [US5] Add `AgentCardClaims`, `AgentStatus`, `ToolCatalog.agents`, and
      `CatalogTool.agent` to `src/freyja_agent/api/schemas.py` (`AgentStatus.error` at most 500
      characters, null when reachable) (contracts/openapi.yaml)
- [ ] T058 [US5] List the agents and fill `agent` in `src/freyja_agent/api/tools.py`, from memory
      only (FR-024)
- [ ] T059 [US5] Add `agent` to `CatalogTool` in
      `specs/002-mcp-tool-delegation/contracts/openapi.yaml`, matching the 003 contract (plan
      Implementation Notes)

**Checkpoint**: quickstart Scenario 1 works.

---

## Phase 9: User Story 6 - Find out quickly when an agent misbehaves (Priority: P6)

**Goal**: An agent that fails, rejects, stops answering, or replies with something malformed
gives an error result naming it, a log line, and an unavailable mark, and recovers by itself.

**Independent Test**: make the stub fail, reject, stop answering, and return a 300-character
task id; each gives an error result and the run continues; start it again and hand off once more.

### Tests for User Story 6

- [ ] T060 [P] [US6] Add to `tests/test_a2a_handoff.py`: `fail`, `reject`, and `auth` give error
      results naming the agent and the state, the run ends with `end`, and a warning names the
      agent; an agent that does not acknowledge within the limit gives an error saying it may or
      may not have received the task and leaves nothing pending; `longid` and an empty task id
      give a malformed-reply error; an unreachable agent gives an error result and the catalog
      shows it unavailable, and the next hand-off after it answers again succeeds and shows it
      reachable; no error result, log record, thread message, or catalog entry contains the
      configured address; none of these is sent twice; a runtime stopped while a `slow` hand-off
      is in flight, then a new runtime on the same store: the next user's run gives the
      orchestrator the may-or-may-not result for that call, not Deep Agents' cancelled text, and
      the stub received the message once (FR-009, FR-010, FR-017, SC-006, US6 scenarios 4 and 5)
- [ ] T061 [P] [US6] Write `tests/test_a2a_transport.py`: the stub served by uvicorn on a free
      loopback port, through the production httpx transport: a read-only hand-off with progress
      arriving before the result; a `slow` task becoming pending and delivered by a tick; a
      redirect from the card URL to a second listener fails startup and the second listener
      receives nothing; the stub stopped and restarted on the same port is usable again without
      restarting the service (FR-002, FR-009, R16)

### Implementation for User Story 6

- [ ] T062 [US6] Complete the error endings in the hand-off coroutine in
      `src/freyja_agent/delegates/a2a_tools.py` and `AgentLink.send` in
      `src/freyja_agent/delegates/a2a_agents.py`, per the table in research R5: `failed`,
      `rejected`, `canceled`, `auth_required`, unacknowledged at the limit, unreachable, protocol
      error, and malformed (a task or context id that is empty or longer than 256 characters),
      each an error result through `describe()`, a warning log naming the agent, and a status
      update (FR-009)
- [ ] T063 [US6] Make the worker's status checks update the agent's reachability in
      `src/freyja_agent/delegates/delegation_worker.py`, so the catalog reflects the most recent
      attempt at startup, in a hand-off, or while watching (FR-024)
- [ ] T064 [US6] Log, through `describe()` in `src/freyja_agent/delegates/delegation_worker.py`,
      every give-up and every background run attempt with the agent, thread, and task ids and
      never the address, and add those log records to the address check in
      `tests/test_a2a_handoff.py` (FR-009, R14)
- [ ] T071 [US6] Answer sends left in flight at the start of a run, in
      `src/freyja_agent/api/outcomes.py` and `src/freyja_agent/api/threads.py`: when a user's run
      or a background run starts, for every send of the thread whose `result` is null and whose
      tool call has no answer in the snapshot, add a `ToolMessage` for that tool call id saying
      `Agent '<agent>' may or may not have received this message; Freyja does not know, and will
      not send it again.`, record that text as the send's result, and put it in the run's input
      ahead of everything else, so Deep Agents' `PatchToolCallsMiddleware` finds the call
      answered instead of adding its own "cancelled or interrupted" text (FR-017, Edge Cases
      "The service stops while a hand-off is in progress"; numbered after T070 by the analysis
      of 2026-11-09, so no later ID moved)

**Checkpoint**: quickstart Scenario 8 works, and a hand-off interrupted by a restart is reported
as possibly received, never resent.

---

## Phase 10: User Story 7 - Bring a real agent up locally (Priority: P7)

**Goal**: A developer following only the README runs the A2A project's reference agent,
configures it, and completes a read-only and an approved gated hand-off.

**Independent Test**: quickstart.md, all nine scenarios, against the real service with a model
provider, local Postgres, the reference agent at commit `1bb0871294`, and the stub on loopback.

### Implementation for User Story 7

- [ ] T065 [US7] Document the feature in `README.md` as it now exists: describing an agent in
      `FREYJA_A2A_AGENTS`, what the read-only mark means and that silence means gated, the six
      limits, the hand-off and cancel tools and that answers are gated too, what a pending task
      is and where `agent_tasks` lists it, background runs and that no client is told about
      them, reading agents in `GET /tools`, startup and runtime failures, the reference
      `helloworld` agent at commit `1bb0871294` as the one to try, and that the suite needs no
      network and no running agent (FR-025)
- [ ] T066 [US7] Run every scenario of `specs/003-a2a-agent-delegation/quickstart.md` against
      the real service and record the outcome of each in the pull request description; correct
      `quickstart.md` or `README.md` where an instruction or an expected result is wrong, and add
      a fix task to this phase for anything that needs a code change (US7 scenarios 1 and 2,
      SC-008)

**Checkpoint**: a developer reaches a read-only and an approved gated hand-off with a real local
agent in under 20 minutes, following the README alone.

---

## Phase 11: Polish & Cross-Cutting Concerns

- [ ] T067 [P] Update `AGENTS.md`: the Layout entries for `delegates/a2a_agents.py`,
      `delegates/a2a_tools.py`, `delegates/delegation_worker.py`, `runtime/agent_tasks.py`,
      `agent/agent_tasks.py`, `api/outcomes.py`, `tests/a2a_stub.py`, and `tests/asgi_stream.py`;
      the `agent_transport` seam; the seven settings; and the Commands entry
      `uv run python -m tests.a2a_stub --http 9998`
- [ ] T068 [P] Update the documentation tree in `specs/003-a2a-agent-delegation/plan.md` to list
      `tasks.md` as it exists, and record under Implementation Notes anything the implementation
      settled differently from the plan
- [ ] T069 Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`,
      `FREYJA_TEST_DATABASE_URI=... uv run pytest tests/integration`, and
      `uv run pre-commit run --all-files`; confirm no Ruff suppression was added and that the
      default suite passes with the network disconnected (SC-009, principle V)
- [ ] T070 Re-run the plan's Constitution Check against the implemented code and note the
      result in the pull request description (principles I to VI)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T002, T003, T005 run in parallel after T001; T004
  follows T002 and T003.
- **Foundational (Phase 2)**: depends on Phase 1. Blocks every user story.
- **User Story 1 (Phase 3)**: depends on Phase 2. The MVP.
- **User Story 2 (Phase 4)**: depends on Phase 3, because approve and edit end in a send.
- **User Story 3 (Phase 5)**: depends on Phases 3 and 4: the answer is a gated hand-off.
- **User Story 4, part 1 (Phase 6)**: depends on Phases 3 to 5: pending tasks reuse the send,
  the gate, and the thread state's task list.
- **User Story 4, part 2 (Phase 7)**: depends on Phase 6.
- **User Story 5 (Phase 8)**: depends on Phase 2 only.
- **User Story 6 (Phase 9)**: depends on Phases 3, 6, and 8: its tests make hand-offs, tick the
  worker, and read the catalog.
- **User Story 7 (Phase 10)**: depends on Phases 3 to 9.
- **Polish (Phase 11)**: depends on all stories.

### Within Phase 2

- T006, T007, T008, T010, T011 have no dependency inside the phase.
- T008 before T009 (same file). T010 and T011 before T013. T012 before T013.
- T009, T012, T013 before T014. T015 is independent of T008 to T014.
- T006, T007, T014 before T016; T016 before T017, T018, T019.

### Within Each User Story

- Tests first, and they must fail before implementation starts.
- Tasks that share a file run in the order listed: T020, T021, T022 (one file, three parts);
  T023 then T025; T024 then T025; T030 then T031; T042 to T048 in order where they share a
  file; T051 to T054 likewise; T057 then T058; T062 then T063 then T064 then T071.

### Parallel Opportunities

- Phase 1: T002, T003, T005 together.
- Phase 2: T006, T007, T008, T010, T011 together; T015 alongside them; later T017, T018, T019
  together.
- Phase 3: T023 alongside T024.
- Phase 4: T029 alongside T027 and T028; T032 alongside the implementation.
- Phase 6: T038 to T041 together; T042 alongside T043 and T044.
- Phase 8 can be built at the same time as Phases 3 to 7, since it depends on Phase 2 only.
- Phase 11: T067 and T068 together.

---

## Parallel Example: User Story 4, part 1

```bash
# Write the tests, then watch them fail:
Task: "Pending at the limit, the claim, and status changes in tests/test_a2a_pending.py (T038)"
Task: "Delivery exactly once and background runs in tests/test_a2a_background.py (T039, T040)"
Task: "Tables and the claim on Postgres in tests/integration/test_a2a_tasks_postgres.py (T041)"

# Then implement:
Task: "AgentTasksMiddleware and check_agent_tasks in src/freyja_agent/agent/agent_tasks.py (T042)"
Task: "Outcome messages and the exactly-once check in src/freyja_agent/api/outcomes.py (T044)"
Task: "The worker in src/freyja_agent/delegates/delegation_worker.py (T046), then T045 and T047"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational. Every agent tool is already classified and gated, and no
   tool sends anything yet.
3. Complete Phase 3: User Story 1.
4. **STOP and VALIDATE**: `uv run pytest`, then quickstart Scenario 2 against the stub.
5. Open a pull request if the increment should merge on its own.

### Incremental Delivery

1. Setup + Foundational: the service starts with agents configured, refuses a bad card or name,
   and gates every agent not marked read-only.
2. User Story 1: read-only hand-offs with progress and one conversation per agent (MVP).
3. User Story 2: decisions on gated hand-offs, the constitution's core requirement.
4. User Story 3: questions and answers.
5. User Story 4, part 1: pending tasks, the worker, and delivery exactly once.
6. User Story 4, part 2: cancel, give-up, and retries.
7. User Story 5: agents in the catalog.
8. User Story 6: failures during a run and recovery.
9. User Story 7: README and the manual quickstart pass.
10. Polish.

### Notes

- [P] tasks touch different files and have no dependency on an incomplete task.
- Every test task names the FRs it covers; the requirement-to-test map in plan.md is the
  reference for completeness.
- Between Phase 3 and Phase 9 a hand-off ending other than completed, input required, or
  pending is already an error result that never ends a run; Phase 9 gives each ending its own
  text, the address rule, and the reachability record.
- T015 changes the stream for every run. It is in Phase 2 so the existing stream and subagent
  tests guard it before any hand-off test relies on it (plan Implementation Notes).
- Commit after each phase at least; commits stay manual and use the `type: summary` form.
