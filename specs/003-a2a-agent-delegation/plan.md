# Implementation Plan: A2A Agent Delegation

**Branch**: `003-a2a-agent-delegation` | **Date**: 2026-11-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-a2a-agent-delegation/spec.md`

## Summary

Let the orchestrator hand work to agents that carry their own reasoning. At startup the service
reads a list of A2A agents from `Settings`, each at a loopback address, reads each agent's card,
and gives the orchestrator and its subagent two tools per agent: a hand-off and a cancel. A
hand-off sends one message the orchestrator wrote, plus the context it chose, in the thread's
one conversation with that agent, and returns the agent's reply as the tool's result while the
agent's progress streams as `update` events. Every message to an agent not marked read-only,
including an answer to the agent's question and a cancel, pauses on the existing approve, edit,
reject interrupt.

A task still working at the call time limit becomes a pending task. It is kept in the service's
database with its thread, so it survives a restart and appears in the thread state. A worker in
each service instance claims pending tasks one instance at a time, checks their status, and
delivers each outcome exactly once: at the start of the user's next run, or with a background
run that goes through the same stream loop, gate, step limit, and run lease as any run, and is
retried a few times if it fails. A task past the maximum age is given up without a cancel.

The client is the official `a2a-sdk` 1.x on an httpx client Freyja owns, which pins every
request to the configured address and follows no redirect. The suite runs a stand-in agent built
with the SDK's server classes inside the test process, through an httpx transport that streams;
it needs no network. The quickstart uses the A2A project's reference `helloworld` agent from
its official samples, pinned to a commit, because no published agent runs locally without a key
(decided below).

Design details are in [research.md](research.md); the entities and tables in
[data-model.md](data-model.md); the changed API shapes in
[contracts/openapi.yaml](contracts/openapi.yaml); the agent list in
[contracts/agent-config.md](contracts/agent-config.md); the progress event in
[contracts/sse-events.md](contracts/sse-events.md); the manual walk-through in
[quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 (`.python-version`), managed by uv with the committed
`uv.lock`, src layout.

**Primary Dependencies**: unchanged from 002 (deepagents 0.7.32 on langgraph 1.2.18, fastapi
0.162.0, sse-starlette 3.6.0, pydantic-settings 2.16.1, `mcp` 2.3.0, jsonschema 4.26.0), plus
`a2a-sdk>=1.3,<2` (1.3.0 resolved; the A2A client, R1), which adds seven packages to the lock.
The `dev` group adds `a2a-sdk[http-server]` for the stand-in agent, which adds no further
package. `httpx` (0.28.1, already in the lock through FastAPI and the SDK) is declared as a
runtime dependency as well as a `dev` one, because Freyja now imports it to build the client the
SDK uses (R3).

**Storage**: three new tables in the existing Postgres database, created at startup like
`freyja_run_locks`: `freyja_agent_conversations`, `freyja_agent_sends`, `freyja_agent_tasks`
(R8). No migration tool; `CREATE TABLE IF NOT EXISTS`. An in-memory implementation of the same
protocol serves the suite. Outcomes reach the thread as ordinary messages in the existing
LangGraph checkpoints.

**Testing**: pytest 9 with pytest-asyncio, the scripted chat model, and in-memory persistence,
as before. New: `tests/a2a_stub.py`, a stand-in agent driven by keywords; `tests/asgi_stream.py`,
an httpx transport that runs it in-process and streams; a fake clock and direct worker ticks;
two runtimes sharing one store; one loopback transport test; opt-in Postgres tests for the
tables and the claim (R16). No network, no external agent, no model provider.

**Target Platform**: Linux server process (uvicorn), one or more instances sharing one
database. Agents run on the same machine.

**Project Type**: single web service, src layout (`src/freyja_agent/`).

**Performance Goals**: an outcome reaches its thread within one minute of the task changing
while the thread is free (SC-003): one poll interval, 10 s by default, plus the time to take the
lease. The catalog answers from memory within 1 s (SC-007). A misconfigured agent fails startup
within 30 s (SC-005): each card read has 20 s and all agents start concurrently.

**Constraints**: classification is a function of configuration only, evaluated at startup;
every message to an agent not marked read-only pauses before it leaves; nothing is retried
except a background run, whose retry sends nothing twice; only the configured address is
contacted; watching only reads; an outcome is recorded in its thread exactly once; a resume
records no outcome; no blocking I/O in async code (Ruff `ASYNC`); SQL is parameterized (Ruff
`S608`).

**Scale/Scope**: a handful of agents, tens of pending tasks per instance. One new setting group
(seven settings), one new runtime dependency, three tables, two tools per agent plus one of
Freyja's own, a worker task per instance, and changed API shapes (`ThreadState.agent_tasks`,
`ActionRequest.agent`, `ToolCatalog.agents`, `CatalogTool.agent`, one `update` node). Out of
scope per the spec: authentication, agents on other machines, push notifications, client
notification of background runs, files, callbacks into Freyja, routing rules, hot reload, size
limits.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | Yes, central | Each agent's hand-off and cancel tools are in `interrupt_on` unless the operator marked the agent read-only, on the orchestrator and the subagent, generated from the same catalog (R12). A background run is a run through the same `run_stream`, agent, and gate, so a proposal in it pauses and is resumed by the existing route after any delay (R10). An answer to an agent's question goes through the hand-off tool and is gated, as clarified. The worker only reads task status and sends nothing; giving a task up sends no cancel (R9). A retried background run sends no message twice, enforced by the send ledger rather than by the agent's goodwill (R5, R8). What an agent does inside its own process is out of Freyja's reach; the gate is on what Freyja sends, which is where the spec and principle II place the boundary. A cancel tool offers approve and reject only, because it has nothing an edit could change; as in 002, the plan reads this as within the principle. | PASS | PASS |
| II. Protocol-based delegation | Yes, first A2A | Agents that carry their own reasoning are reached over A2A only. The new import is the A2A project's SDK, a protocol client with no agent loop and no model client (R1); `python-a2a`, which pulls model clients and LangChain, was rejected for that reason. Each agent stays its own process owned by its team. Freyja owns routing (the model's choice of hand-off tool), the single voice (an agent's question reaches the user in the orchestrator's reply), hand-off context (only what the orchestrator writes is sent, R5), and approval. | PASS | PASS |
| III. One runtime, every environment | Yes | `build_agent` stays the only assembly point; agent tools join through `delegate_tools`. A background run is `run_stream` driven by the worker instead of an SSE client, so there is no second run loop (R10, R15). The tests swap one new seam, the httpx transport an agent link uses, at the level of the in-memory checkpointer; card checks, tools, the gate, the ledger, the worker, and the routes are the production code (R16). Environments differ only by `FREYJA_A2A_*`. | PASS | PASS |
| IV. Configuration through settings | Yes | Seven settings with safe local defaults: no agents, and finite limits (R2), all in `.env.example`. Addresses must be loopback, checked without lookup. No address, agent name, or model id is hardcoded outside tests. The card's advertised address is never used (R3). | PASS | PASS |
| V. Automated quality gates | Yes | Every FR has a test (map below); the default suite needs no network, database, model, or running agent. All agent I/O is async through httpx; time limits are `asyncio.timeout`; the worker is an asyncio task; SQL is parameterized. No new Ruff suppression is planned. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes | Whether a message to an agent pauses is decided by a fixed rule over configuration at startup. The card's claims, the agent's replies, and the model are not inputs (R12). The startup refusals (address, authentication, text input, name collisions) are checked before any request is served (R2, R3). | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; the Postgres checkpointer and SSE; Ruff only. The three tables live in the same database and pool as the run lease. | PASS | PASS |

No deviations from the constitution. Two additions carry complexity the governance section asks
to justify; neither is a violation, so they are justified here rather than in Complexity
Tracking:

- **Three tables.** The watcher must find pending tasks across threads and claim each for one
  instance, which the checkpoints and the LangGraph store cannot do atomically (R8). The send
  ledger is the only way to guarantee that a retried step does not send twice (R5).
- **A worker task per instance.** Long-running tasks need something that runs without a request.
  It lives in the FastAPI process and drives the same stream loop, so principle III holds (R9,
  R10).

### Decision taken at plan review (2026-11-09)

FR-025 and SC-008 asked for a published A2A agent that runs locally with no account or key, and
none exists (R17). The plan uses the A2A project's reference `helloworld` agent from the official
samples, pinned to commit `1bb0871294`: real, current, key-free, and run with `uv run --with`,
but not a package. The user accepted it, and the spec now says "an official sample of the A2A
project, pinned to a commit" in FR-025, SC-008, User Story 7, and Assumptions (spec
Clarifications, Plan review 2026-11-09). The alternative, naming no real agent and proving the
real-agent scenarios with the stand-in only, was not adopted.

## Project Structure

### Documentation (this feature)

```text
specs/003-a2a-agent-delegation/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── openapi.yaml     # ThreadState.agent_tasks, ActionRequest.agent, the catalog's agents
│   ├── agent-config.md  # The FREYJA_A2A_AGENTS shape, rules, and examples
│   └── sse-events.md    # The agent progress update and the background start mode
├── checklists/
│   ├── requirements.md  # Spec quality checklist (from /speckit-specify)
│   └── security-reliability.md  # Security and reliability checklist (from /speckit-checklist), reviewed
└── tasks.md             # T001 to T072 in 11 phases (/speckit-tasks; T071 added by the analysis,
                         #   T072 after Phase 4); each ticked as its phase was built
```

### Source Code (repository root)

```text
src/freyja_agent/
├── config.py                  # + A2aAgentConfig; a2a_agents and six limits; agent and server
│                              #   names share one namespace
├── agent/
│   ├── factory.py             # build_agent(..., agent_tasks=...); with a store (agents
│   │                          #   configured), AgentTasksMiddleware on both agents and
│   │                          #   check_agent_tasks added to Freyja's tools
│   ├── catalog.py             # CatalogTool.agent and .pinned_args; OWN_TOOLS + check_agent_tasks
│   │                          #   (offered only with agents); gate description for agent tools
│   ├── agent_tasks.py         # NEW: AgentTasksMiddleware (pending tasks in the prompt) and the
│   │                          #   check_agent_tasks tool
│   ├── context.py, prompts.py # prompts: one paragraph on hand-offs, answers, pending tasks
│   └── tools.py               # unchanged
├── delegates/
│   ├── a2a_agents.py          # NEW: AgentLink (card read and checks, pinned address, send,
│   │                          #   get_task, cancel, status, describe); open_agents()
│   ├── a2a_tools.py           # NEW: the hand-off and cancel tools; the send ledger use;
│   │                          #   reply -> text (R6); progress via the stream writer
│   ├── delegation_worker.py   # NEW: DelegationWorker: claim, check, give up, deliver, retry
│   ├── mcp_servers.py         # unchanged
│   └── mcp_tools.py           # DelegateTool gains agent and pinned_args
├── runtime/
│   ├── agent_tasks.py         # NEW: AgentTaskStore protocol; PostgresAgentTaskStore (three
│   │                          #   tables, claim with SKIP LOCKED); InMemoryAgentTaskStore
│   ├── runtime.py             # Runtime + agents, agent_tasks, worker; assemble(...,
│   │                          #   agent_transport=...); open order (R15)
│   ├── persistence.py         # unchanged
│   └── locks.py               # unchanged
└── api/
    ├── streaming.py           # mode background; custom stream mode as update node "agent";
    │                          #   subgraphs=True filtered to the 001 contract; terminal callback
    ├── threads.py             # start_run records undelivered outcomes first; state lists
    │                          #   agent_tasks
    ├── outcomes.py            # NEW: outcome messages, the exactly-once check (R10)
    ├── schemas.py             # + AgentTask, AgentStatus, AgentCardClaims; ActionRequest.agent;
    │                          #   CatalogTool.agent; ThreadState.agent_tasks
    ├── serialize.py           # interrupt_to_pending fills agent
    ├── decisions.py           # pinned arguments refused on edit
    └── tools.py               # catalog lists agents

tests/
├── a2a_stub.py                # NEW: stand-in agent; in-process for the suite,
│                              #   `python -m tests.a2a_stub --http PORT` for loopback
├── asgi_stream.py             # NEW: httpx transport that streams an in-process ASGI app
├── conftest.py                # + make_a2a_app fixture, fake clock, shared store for two runtimes
├── test_a2a_config.py         # NEW: entry accepted; non-local, bad name, unknown field, missing
│                              #   url refused; input not echoed; name shared with a server
├── test_a2a_startup.py        # NEW: card read and logged; unreachable; redirect; auth; no text;
│                              #   no JSON-RPC; collisions; no skills; card address ignored
├── test_a2a_handoff.py        # NEW: read-only hand-off direct and through the subagent; only
│                              #   message and context sent; progress events; parts to text;
│                              #   conversation per thread and agent, across restart; failures
│                              #   and the unacknowledged case; recovery; ledger prevents resend
├── test_a2a_gate.py           # NEW: pause names the agent; approve, edit, reject; pinned task_id;
│                              #   cancel approve/reject only; card claims ignored; subagent;
│                              #   mixed step; decisions under a changed configuration
├── test_a2a_input.py          # NEW: question as result; answer gated and continues the task;
│                              #   unrelated message keeps it waiting
├── test_a2a_pending.py        # NEW: pending at the limit; listed in state; watched once across
│                              #   two runtimes; give-up at max age without cancel; agent removed;
│                              #   cancel of pending and given-up tasks; late outcome ignored
├── test_a2a_background.py     # NEW: delivery by background run, exactly once, across restart;
│                              #   waits for a busy thread; user run delivers first; resume does
│                              #   not; proposal in a background run; retries and their limits
├── test_a2a_catalog.py        # NEW: agents and agent tools; no address; read contacts no agent;
│                              #   contract validated against contracts/openapi.yaml
├── test_a2a_transport.py      # NEW: the stub over loopback: hand-off, pending, a redirected
│                              #   card, stop and restart
├── test_agent_task_store.py   # NEW: the store contract on the in-memory store
├── integration/
│   └── test_a2a_tasks_postgres.py  # NEW: the same contract on Postgres; tables, claim across
│                                   #   two instances, ledger
└── test_factory.py, test_runs.py, test_catalog.py, test_state.py, test_gate.py,
    test_mcp_gate.py, test_config.py
                               # adjusted for check_agent_tasks, the agent field, agent_tasks,
                               #   subgraph streaming, and the new settings; behavior unchanged
```

Also edited: `pyproject.toml` and `uv.lock` (`a2a-sdk`, and `httpx` declared at runtime), `.env.example` (seven
settings with a commented example), `README.md` (configuring an agent and its read-only mark,
pending tasks in the thread state, agents in the catalog, the reference agent, the suite),
`AGENTS.md` (layout), and 001's `contracts/openapi.yaml` and 002's `contracts/openapi.yaml`
where `ThreadState`, `ActionRequest`, and `CatalogTool` are defined, so the three contract files
do not disagree.

**Structure Decision**: single project in the existing src layout. The A2A protocol code goes
in `delegates/`, the home feature 002 created for it. The task store sits in `runtime/` with the
run lease, because both are shared state across instances in the same database. The worker is
in `delegates/` because everything it does is about agents; it drives `api/streaming.run_stream`
for background runs so there is one run loop.

### Requirement to test map

| Requirement | Tests |
|---|---|
| FR-001, FR-004 | `test_a2a_config.py`, `test_config.py` |
| FR-002, FR-003 | `test_a2a_startup.py`, `test_a2a_transport.py` (redirect, card address) |
| FR-005, FR-006, FR-007, FR-008, FR-009, FR-010 | `test_a2a_handoff.py`, `test_a2a_transport.py` |
| FR-011, FR-012, FR-013, FR-014 | `test_a2a_gate.py`, `test_a2a_input.py`, `test_a2a_pending.py` (cancel) |
| FR-015, FR-016 | `test_a2a_input.py` |
| FR-017, FR-019, FR-021, FR-022, FR-023 | `test_a2a_pending.py`, `integration/test_a2a_tasks_postgres.py` |
| FR-018, FR-020, FR-027 | `test_a2a_background.py` |
| FR-024 | `test_a2a_catalog.py` |
| FR-025 | README review in the quickstart pass |
| FR-026 | all of the above |

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

None. The Constitution Check passed without deviations before research and after design. The
tables and the worker are justified under the Constitution Check above.

## Implementation Notes

- **Dependency choice**: the plan builds on `a2a-sdk` 1.x. Revisit when a 2.x appears; the
  transport test is the check for a bump (R1).
- **Address pinning** is done by rewriting a copy of the card before `ClientFactory.create`,
  not by a custom transport, so the SDK's version negotiation still chooses between the 1.0 and
  0.3 transports (R3).
- **Streaming in the suite** needs `tests/asgi_stream.py`; httpx's own `ASGITransport` buffers a
  response and would make every slow task look unacknowledged (R16).
- **`run_stream` with `subgraphs=True`** changes the chunk shape to `(namespace, mode, chunk)`.
  The filter keeps the 001 contract: updates and interrupts from the root only, messages and
  custom chunks from every namespace (R7). Land this change, with the existing stream and
  subagent tests green, before the first hand-off test.
- **Order inside `Runtime.open`**: persistence and the three tables, tool servers, agent links,
  the agent, the worker, yield; stop the worker, close the links, the tool servers, the pool.
- **Contract files**: `ThreadState`, `ActionRequest`, and `CatalogTool` are defined in 001's and
  002's `openapi.yaml`; they gain the new fields when the schemas change in code.
- **Phasing suggestion for tasks**: setup and configuration; the store, the links, and the
  startup checks (the startup half of User Story 6 is a prerequisite for everything); the stream
  change; then User Story 1, 2, 3, 4, 5, the runtime half of 6, and 7.
- **Phase 1 as built (2026-11-10)**:
  - The lock gained exactly the seven packages research R1 names (`a2a-sdk` 1.3.0, `aiologic`
    0.17.1, `culsans` 0.11.0, `google-api-core` 2.44.0, `json-rpc` 1.15.0, `proto-plus` 1.29.0,
    `wrapt` 2.5.2); no other version moved. `httpx` is now declared in both the runtime
    dependencies and the `dev` group.
  - `A2aAgentConfig.read_only` is a `StrictBool`, so `"read_only": "maybe"` or `"yes"` is
    refused rather than read as a truthy string. A JSON `true` from the environment still works.
  - The entry sets `hide_input_in_errors` like `McpServerConfig`, although `Settings` already
    hides input for nested values: a probe showed the echo test fails only when both are
    removed. The entry's own setting keeps a refusal quiet if the model is ever validated on
    its own.
  - The rules of contracts/agent-config.md that need the card or the tool
    server list (a name shared with a tool server, card checks) are left to Phase 2, as the
    contract says.
- **Phase 2 as built (2026-11-10)**:
  - **The stub's malformed ids are an option, not a keyword.** The SDK picks a task's id before
    the agent's code sees the message, so `make_stub(task_ids=...)` replaces the id generator
    instead of the `longid` keyword T007 named. The stub also gained `hold` (stays working until
    `StubAgent.release()`) and `message` (a direct message reply instead of a task).
  - **The store contract is tested now, on both stores.** `tests/test_agent_task_store.py` runs
    against the in-memory store, and `tests/integration/test_a2a_tasks_postgres.py` imports the
    same tests and runs them against Postgres (7 passed, including idempotent setup and a
    claim passing to another instance after it expires). T041 extends that file. The store API
    is `claim_due(instance_id, lease_seconds)` with `now()` from the store's own clock, as the
    analysis of 2026-11-09 set; it also has `in_flight_sends`, which T071 will use, and
    `threads_with_ready_outcomes` for the worker.
  - **Tool names are checked in `open_agents`.** An agent whose `handoff_to_<agent>` or
    `cancel_<agent>_task` is already a tool's name, or whose name is a tool server's, fails with
    `Agent '<name>': ...` before the catalog is built, so the error names the agent rather than
    the catalog's generic duplicate check. The tool name helpers live in `a2a_agents.py`.
  - **Subgraph streaming changed one thing a client can see.** With it off, a subagent's model
    output from a non-streaming model never reached the stream; with it on, it arrives once as
    a `message` event, which is what 001's contract already says. No `update` event comes from
    inside the subagent, and nothing is duplicated (`test_runs.py`,
    `test_a_delegated_run_keeps_the_stream_contract`).
  - `run_stream(..., on_end=...)` is awaited with `("end" | "interrupt" | "error", code)` before
    the lease is released; an exception in it is logged and does not change the stream.
  - The SDK's card resolver logs the card and its URL at INFO on `a2a.client.card_resolver`.
    The service's logging leaves third-party loggers at WARNING, so the address does not reach
    the log by default; the address checks of Phase 9 cover Freyja's own loggers.
  - The hand-off tool's description is several lines (name, card description, one line per
    skill), while `contracts/openapi.yaml` shows a one-line example; Phase 8 aligns the example.
  - `Runtime.agent_tasks` defaults to an in-memory store when `assemble` is given none, so the
    fixtures that predate feature 003 need no change.
- **Phase 3 as built (2026-11-10)**:
  - The hand-off coroutine gets its tool call id from LangChain's `InjectedToolCallId` in its own
    signature. A probe showed the injected argument stays out of `HandOffArgs`, so the model's
    schema and the edit validation of Phase 4 see only `message`, `context`, and `task_id`.
  - For an agent that does not stream, a change is judged on the protocol's own states, so
    `submitted` to `working` is reported even though both read `working` to Freyja. T020's test
    found this: the first observed change was being dropped.
  - A data part is rendered with sorted keys, because a protobuf `Struct` keeps no key order
    and the same reply came back in different orders (a flaky test showed it), and whole numbers
    are turned back into integers, since protobuf keeps every number as a double.
  - The A2A message id is `freyja-<thread>-<tool call id>`, so an agent that de-duplicates by
    message id also sees a replayed step as the same message. (T072 adds a digest of the step;
    see "Send key" below.)
  - A step replayed from the send ledger returns the recorded text as a normal result, even if
    the first attempt was an error result; the ledger keeps text, not the error flag.
  - Every acknowledged task gets a row with the state it ended the call in. Endings other than
    completed or a direct reply (input required, failed, rejected, canceled, authentication
    required, still working at the limit) are error results naming the agent until Phases 5, 6,
    and 9; a task still working at the limit stays `working` with no watcher until Phase 6.
  - Run against the real service with the `.env` model (OpenRouter) and the compose Postgres:
    quickstart Scenario 2 with the `helloworld` agent at `1bb0871294` streamed "Processing
    request..." then "Request is completed!" as progress events, returned "Hello, World! I have
    received your request (...)" with no pause, and the reply relayed it in Freyja's words. A
    thread kept one context id with the stub across a service restart, and another thread got
    another. The `agent_tasks` check of Scenario 2 waits for Phase 5, which adds the field.
- **Phase 4 as built (2026-11-10)**:
  - `ActionRequest.agent` comes from the catalog in force, like `server`: the agent of the tool
    now, and null for a tool that is not an agent's or is no longer configured. The exact-shape
    assertion in `tests/test_mcp_gate.py` gained `agent: null` alongside the T029 changes to
    `test_gate.py` and `test_state.py`.
  - The pinned check runs after the model has validated the edit, so an edit that leaves
    `task_id` out is read as `null` and must equal the proposal: for an answer, the edit has to
    name the proposed task. The refusal says `'task_id' cannot be changed by an edit; it must
    stay <value>, as proposed`. The quickstart's edit already sends `task_id` explicitly.
  - `HandOffArgs` forbids unknown keys, so pydantic reported an added key such as `agent` as
    well as Freyja's own check. Pydantic's top-level `extra_forbidden` entry ("Extra inputs are
    not permitted") is now dropped, so each unknown key is reported once, as "Action
    '<tool>' has no argument named '<key>'". Nested errors are unchanged.
  - The send ledger knows a call by thread and tool call id, so a later call that reused an id
    on the same thread would get the earlier call's result without a send. A first test draft
    did this by reusing the scripted id `c1` across runs. Not every provider behind an
    OpenAI-compatible route promises unique ids, so T072 fixed it; see "Send key" below.
  - Run against the real service with the `.env` model (OpenRouter), the compose Postgres, and
    the stub on 9998 not marked read-only: quickstart Scenario 3 paused with `handoff_to_stub`,
    `agent: stub`, `server: null`, and the model's message (it gave no context), and the stub
    had no POST. Edits setting `task_id`, leaving `message` empty, or adding `agent` were each
    refused with 422 and the proposal stayed; the edited message was the one the stub received,
    once, and the stream ended with `end`. On a second thread a reject with a reason sent
    nothing, and the reply repeated the reason.
- **Send key (2026-11-10, T072)**:
  - A send is known by `(thread_id, step, tool_call_id)`. `step` is the `checkpoint_ns` the
    tool call runs in. A probe showed LangGraph runs each tool call as its own task with its own
    namespace (`tools:<task id>`, nested for the subagent). Two calls sharing an id in one reply
    got different namespaces, as did a later step. A call whose step failed and ran again from
    the last checkpoint got the same one, and a sibling that had finished was not run again.
  - Running again from an older checkpoint (time travel) forks it, so the call gets a new
    namespace and would send again. Freyja has no such path: a retry continues from the last
    checkpoint (FR-027).
  - Each send keeps `request`, a SHA-256 of the agent, the kind, and the arguments. A row
    found with another digest is not a step running again. The tool returns an error naming
    the agent and sends nothing, rather than answer with the other call's result.
  - The A2A message id gains the first eight hex characters of the step's digest, so an agent
    that de-duplicates by message id does not drop the second of two calls sharing an id.
  - The middleware that would have rewritten repeated tool call ids was not built. A subagent
    starts with its own messages, so it could not see the ids the orchestrator had used.
    Rewriting an id would also have to rewrite it in the provider's content blocks. For
    Anthropic, a `tool_use` block keeping the old id next to the rewritten call would leave a
    call with no result.
  - A database that ran earlier 003 phases has the old `freyja_agent_sends`, which
    `CREATE TABLE IF NOT EXISTS` leaves as it is. 003 has not been released, so there is no
    migration. Drop the table once: `docker compose exec postgres psql -U freyja -d freyja -c
    'DROP TABLE freyja_agent_sends'`.
  - Run against the real service with the `.env` model (OpenRouter) and the `helloworld` agent
    on a scratch database: two hand-offs on one thread completed, and each row had its own
    step, message id, and digest.
- **Phase 5 as built (2026-11-10)**:
  - A message that continues a task gets no task event first. The SDK's `DefaultRequestHandler`
    streams status and artifact updates for the existing task, which is what the official
    samples' executors do too. `_send` ignored updates that came before a task, so every
    answer looked unacknowledged ("could not be reached"). T033's tests found this. The first
    update now starts the task from its `task_id` and `context_id`, and counts as the
    acknowledgment.
  - An answer updates the task's existing row (state, `last_checked_at`, `ended_at`,
    outcome). The row keeps the message the task was started with and its start time, so the
    thread state shows one task from question to completion.
  - The needing-input result is headed `Agent '<agent>' needs input for task <task id>:`,
    followed by the question. It ends with a line telling the orchestrator to ask the person
    and to hand off again with that `task_id`. It is a normal result, not an error result, and
    the task's outcome counts as delivered, because the hand-off returned it.
  - **Added beyond the tasks:** an answer naming a task that another thread started is
    refused before anything is sent ("Agent '<agent>' has no task <id> on this thread;
    nothing was sent."). Otherwise the reply would have overwritten that thread's task row, and
    conversations are never shared between threads (FR-008). A task id Freyja does not know is
    still sent and left to the agent; malformed ids are Phase 9's (T060 to T064).
  - `ThreadState.agent_tasks` defaults to an empty list, so a thread with no agent tasks, and
    every thread from before feature 003, reads as before plus `agent_tasks: []`.
  - Run against the real service with the `.env` model (OpenRouter), the stub on 9998 not
    marked read-only, and a scratch database (the dev database still has the send table from
    before T072):
    - Quickstart Scenario 5: the approved hand-off of `ask: plan the release` returned the
      needing-input result, the reply asked for the date in Freyja's own words, and
      `agent_tasks` listed the task as `input_required`.
    - "Friday at 10." led to a pause on `handoff_to_stub` with the model's answer and the same
      `task_id`, and the stub had one POST. On approval the stub completed the same task, and
      `agent_tasks` showed it `completed` with its first message and an `ended_at`.
- **Phase 6 as built (2026-11-10)**:
  - **Outcome messages, corrected.** data-model.md had one `check_agent_tasks` call answered by
    one result per task. A provider takes exactly one result for each tool call, so the
    `AIMessage` now carries one call per task, each with `{"task_ids": [<task id>]}`, and each
    `ToolMessage` answers its own call. Each id also carries the time the outcome arrived
    (`a2a-outcome-<agent>-<task id>-<arrived>`). A task can have a second outcome, and with
    the documented ids the exactly-once check would have taken the first for the second and
    skipped it. That happens when a task asks a question through the worker, is answered,
    and goes pending again. Tool call ids are `a2a_` plus 24 hex characters, because
    providers limit them (OpenAI to 40 characters). data-model.md and research R10 are
    updated.
  - An outcome reaches the orchestrator in the same words the hand-off would have returned
    (`outcome_text`, shared by the hand-off and the worker). A failure is an error result,
    as the hand-off's would have been.
  - **`outcome_delivered`, corrected.** The Phase 5 rule reported a task still `working` as
    delivered, because nothing was waiting yet. It is now false while the task works, and
    while an outcome waits; true once a run recorded it, or when the hand-off returned it
    itself. A hand-off that continues a task resets the row's delivery, so the next outcome
    is delivered anew.
  - An acknowledged task whose stream ends while it is still working is pending too, like one
    still working at the limit. It used to be an "ended as working" error.
  - `check_agent_tasks` is in the fixed table, so it is always offered, with or without
    agents. Without a store, `build_agent` gives it and the middleware an empty in-memory
    one. The middleware reads the store asynchronously. A synchronous invocation passes
    through unchanged, because the service only runs the agent with `astream`.
  - `run_stream` gains `on_recorded`, awaited at the run's first root update: the input is in
    a checkpoint by then. A failure to mark is logged and the run goes on, because the next
    run's check finds the outcome in the thread.
  - **The worker.**
    - It ticks at start and then at every interval. Checks and deliveries within a tick run
      concurrently.
    - A failed tick is logged and the loop goes on.
    - A claimed task whose agent is no longer configured is left for Phase 7's give-up.
    - It takes no clock: every time it sets or compares comes from the store.
    - `streaming.py` and the worker import `Runtime` only for type checking, because the
      runtime builds the worker and the worker runs the stream.
  - `Runtime.assemble` gains `run_worker` (default true). The suite passes false and calls
    `worker.tick()` itself.
  - **Bug fixed:** `assemble` gave the `Runtime` a second, empty store when none was passed,
    while the tools used the first. Now one store is used throughout.
  - The stand-in gains `hold <ending>` (`fail`, `reject`, `auth`, `ask`, `cancel`), so a
    pending task can end each way. An answer starting with `slow <seconds>` takes that long.
  - **Quickstart, corrected.** Its scenario-11 step said to "send before the next poll". An
    outcome is recorded at a poll, and a free thread gets its background run in that same
    poll, so that step never catches one. It now holds the thread's run lease, as a run on
    another instance would.
  - **Observation, unchanged:** `started_at` is when the hand-off recorded the task, at the
    end of the call (5 s after the acknowledgment in the real run below). That matters
    little for the maximum age (Phase 7, default 24 h).
  - **Run against the real service.** The `.env` model (OpenRouter), the stub on 9998 as
    `stub` (gated) and again as `quick` (read-only), a 5 s call limit and poll interval, and
    the scratch database `freyja_p6`:
    - Scenario 6: the approved `slow 60` hand-off returned the pending note after 5 s, and
      the run ended with `end`. `agent_tasks` listed it as `working` with
      `outcome_delivered: false`.
    - The service was stopped by its port and the stub finished, then the service was
      started again. Within a second the task was `completed` and delivered. The thread
      ended with the `check_agent_tasks` call, its result holding the stub's report, and a
      background run's reply. Exactly one result, and still one after two more polls.
    - Scenario 11, held as above: the poll recorded the `quick` task's outcome and did not
      deliver it. "Is the report ready?" recorded it just before the message, and the reply
      answered from it. No background run followed for that thread.
- **Phase 7 as built (2026-11-10)**:
  - **The cancel tool.** It refuses, sending nothing, a task that this thread did not start or
    that Freyja does not know. An answer with an unknown task id is still sent (Phase 5), but a
    cancel only ever stops a task this thread owns. A task Freyja already knows has ended is
    reported as ended (`already ended as <state>; nothing was sent.`), also without sending.
  - **A cancel the agent cannot honor because the task ended** (`TaskNotCancelableError`):
    the tool reads the task and returns its outcome with the cancel's result, recorded as
    delivered. Leaving it to the worker would lose it for a given-up task, which nobody
    watches any more.
  - **Errors the SDK raises for a cancel:**
    - `A2AClientError` (no answer, such as a refused connection) means the agent could not
      be reached, and `A2AClientTimeoutError` means the time limit;
    - any other `A2AError` is the agent's own refusal;
    - in each case the task stays pending, and the ledger keeps a re-run step from sending
      again.
  - **Late outcomes for canceled tasks.** `update_task` gains `when_state`, one conditional
    statement on Postgres. The worker records a check, and the tools a cancel or an ended
    task, only while the task is still pending (or given up, for a cancel). An outcome that
    arrives for a task Freyja canceled meanwhile is ignored, also when a check was already
    under way.
  - **Give-ups.** A task whose agent is no longer configured is reported without the offer to
    cancel, because there is no cancel tool for it: `Freyja stopped waiting for task <id> of
    agent '<agent>', which is no longer configured; the agent may still be working on it.`
    A given-up task has an `ended_at`.
  - **Where retry state lives.** It goes on every row the failed attempt tried to deliver,
    and on those of earlier attempts, not only on the rows it marked delivered. An attempt
    that failed before its input reached a checkpoint left its rows unmarked; without this,
    the next tick would deliver them again at once instead of after the retry interval.
  - **How retries run.** Normal delivery skips a thread whose retry is not due yet. A due
    retry runs with input `None`, or with the outcome messages when the failed attempt never
    recorded them. The store gains `threads_with_due_followups()`. `run_stream` clears a
    thread's retries itself when a run ends with `end`, `interrupt`, or `step_limit`.
  - The thread-less tool error now reads "A message to an agent needs the thread it is sent
    on; none was given, so nothing was sent." for both tools. The Phase 2 placeholder test
    for the cancel tool became a test of that.
  - **Run against the real service.** The `.env` model (OpenRouter), the stub on 9998 not
    marked read-only, a 5 s call limit and poll interval, and the scratch database
    `freyja_p7`:
    - Scenario 7, cancel: after the approved `slow 600` went pending, "Cancel that task."
      paused on `cancel_stub_task` with the task id, approve and reject only, described as
      `stub: cancel a task`. On approval the task was `canceled` in `agent_tasks` and at the
      stub.
    - Scenario 7, give-up, with the maximum age at 20 s: a background run reported
      `Freyja stopped waiting ...` 23 s after the start, and the reply offered a cancel
      without making one. The task was `given_up`, and the stub still had it working, so no
      cancel was sent. "Yes, cancel it." then paused on the cancel, and approving it canceled
      the task at the stub.
    - Retries, with a 10 s interval: a `slow 25` task finished while the service ran with an
      invalid model id. Two background attempts failed with `model_error`, 10 s apart, as
      logged. After a restart with the real model, the third attempt succeeded. The thread
      held one outcome and one reply, and the retry fields were cleared.
- **Phase 8 as built (2026-11-10)**:
  - **`check_agent_tasks` is offered only with agents.** FR-024 says the catalog with no agents
    configured is exactly what it was before this feature, and T056 tests that the tools then
    equal feature 002's. Phase 6 offered the tool always, so it, and `AgentTasksMiddleware`,
    are now added only when at least one agent is configured. `OWN_TOOLS` keeps the row: it
    is the classification table, and an own tool that is not offered has no catalog entry.
    - `build_agent(agent_tasks=None)` adds neither; `Runtime.assemble` passes the store only
      when an agent link exists.
    - The tests written in Phase 6 for the always-offered tool are back to the 002 list
      without agents, plus a factory test with a store.
  - **The one case left without the tool:** a thread whose agents were all removed.
    - Its given-up report still records Freyja's `check_agent_tasks` call and result in the
      thread; the tool node never runs them, because the call is already answered.
    - Calls to a tool not offered in the current request are left in the history. Checked
      against the `.env` model (`openrouter:anthropic/claude-sonnet-5.5`): one request whose
      history held such a call and its result, with only `send_notification` offered. It was
      accepted and answered.
  - **What changes without agents:** the catalog still gains `agents: []` and `agent: null` on
    every tool, because both are required by the contract. The tools themselves are 002's.
  - **The 002 contract:** `CatalogTool` gains `agent` (required, as in 003), and its example
    tools show `agent: null`. Both contracts' `/tools` examples validate against their own
    schemas.
  - `AgentStatus.error` is null when the agent is reachable, and otherwise the text
    `AgentLink.mark` stored, which already has the address removed and is cut to 500
    characters.
  - **Run against the real service.** The `.env` model, the helloworld sample on 9999 marked
    read-only, the stub on 9998, and the scratch database `freyja_p8`:
    - Scenario 1: `agents` listed `hello` and `stub`, both reachable, with each card's name,
      description, version, and skills under `card`. `handoff_to_hello` and
      `cancel_hello_task` were `read_only` with `agent: hello`; `handoff_to_stub` was `gated`
      with approve, edit, and reject, and `cancel_stub_task` with approve and reject.
    - No `127.0.0.1`, `9998`, or `9999` appeared anywhere in the response.
    - Three reads took under 1 ms each, and the hello agent's log gained no line.
    - Restarted with `FREYJA_A2A_AGENTS={}`: `agents` was empty, and the tools were exactly
      feature 002's nine, each with `agent: null`.
- **Phase 9 as built (2026-11-10)**:
  - **Malformed replies.** An empty, or longer than 256 characters, task id or task context id
    makes a reply malformed: `Agent '<agent>' replied with something malformed: its <task id |
    conversation id> is <empty | longer than 256 characters>.`, and the agent is marked
    unavailable with that reason. Nothing from such a reply is kept, neither a task nor a
    conversation, because an unusable id could not be watched or answered.
    - **Direct messages:** a direct message reply may leave its conversation id out, as the
      protocol allows; it then starts no conversation. An overlong one is malformed.
    - **Testing an empty id:** the SDK's own server never sends an empty task id (it fails
      the request with "Task ID mismatch in agent response"), so the empty-id tests blank the
      id on arrival. The 300-character case is a real reply from the stub.
  - **Telling failures apart.** An exception whose cause is an `httpx.TransportError` (no
    connection, or it broke off) is `could not be reached: ...`. Any other failure before
    acknowledgment is `answered with an error: ...`, such as an error status, a JSON-RPC
    error, or an unparsable reply. `A2AClientTimeoutError` counts as the time limit.
  - **Added beyond the table in R5:** a stream that breaks after the agent acknowledged keeps
    the task as last seen. A task still working becomes pending and is watched, like one
    still working at the limit, and the agent is marked unavailable with the reason.
    Reporting an error would have forgotten a task the agent has.
  - **Reachability (T063).** A task that failed, was rejected, or asked for authentication is
    an answer from the agent: the agent stays reachable. The worker's status checks already
    marked the link (Phase 6); T063 adds the test. A failed check is now also logged.
  - **Logs (T064), each naming the agent and written through `describe()`:**
    - every hand-off or cancel error result is a warning
      (`Agent <agent>: <result>`);
    - a failed status check is a warning with the thread and task ids;
    - a give-up is a warning saying no cancel was sent;
    - every background run attempt is logged at its start, with its number of the
      configured attempts, as well as at its end.
  - **Messages cut off by a stop (T071).** `answer_in_flight` answers each send with no result
    whose tool call is in the thread's messages and unanswered. The answer goes first in the
    input of a user's run, or of a background run, and the sends' results are recorded with the
    outcomes once the input is in a checkpoint.
    - **Why first:** a probe showed that new input drops the step left unfinished, and that
      Deep Agents' `PatchToolCallsMiddleware` otherwise inserts "Tool call ... did not
      complete - no result was recorded".
    - **Inside a subagent:** a send made there has no call in the thread's own messages, so it
      is left to the subagent's step, whose re-run answers it from the ledger.
    - The text is `may_not_have_received`, shared with the ledger's re-run answer.
  - `make_a2a_app(network=True)` uses the production transport. Only
    `tests/test_a2a_transport.py` uses it, with stubs served by uvicorn on free loopback
    ports.
  - **Run against the real service.** The `.env` model, the stub on 9998, and the scratch
    database `freyja_p9`:
    - Scenario 8, startup: nothing listening on 9997 stopped startup with `Agent 'hello': its
      card could not be read: ... <address> ...`; `10.0.0.5` failed settings validation for
      `a2a_agents.hello`; an agent named `git` beside the MCP stub as server `git` stopped
      startup with `the name is also a tool server's name`. None of them listened, and no log
      held the address.
    - Scenario 8, at run time: with the stub stopped, the hand-off returned `Agent 'stub'
      could not be reached: ...`, the run ended with `end`, a warning named the agent, and
      `/tools` showed it unreachable. After starting the stub again, the next hand-off
      completed without a restart, and `/tools` showed it reachable.
    - **A cut-off hand-off:** the service was killed (`kill -9` of the pid on port 8000)
      during a `slow 20` hand-off, which left its ledger row with no result after one POST
      to the stub. After a restart, "Did the stub get the report task?" ran with Freyja's
      may-or-may-not result in place, directly after the call; the ledger held that text,
      the stub received no second POST, and no "did not complete" text appeared.
- **Phase 10 as built (2026-11-11)**:
  - **README (T065).** A new "Agents" section covers:
    - trying the reference `helloworld` agent at `1bb0871294`, read-only and then gated;
    - describing an agent, and that silence means gated;
    - the hand-off and cancel tools, with answers gated too;
    - pending tasks and `agent_tasks`;
    - background runs, and that no client is told of them;
    - give-ups;
    - startup and runtime failures;
    - agents in `GET /tools`.

    The Configuration table gains the seven settings. The startup paragraph names the three
    agent tables. The tests paragraph adds the stand-in agent, and that the suite needs no
    running agent.
  - **README, Setup:** uv stops reading `.env` at the first line it cannot parse, and only
    warns. A model block left with its `<...>` placeholder therefore silently drops every
    line after it, the agents line included. This happened in the clean-clone pass when the
    OpenRouter lines were appended without commenting out the active OpenAI block. The README
    now says so.
  - **Quickstart (T066), corrected:**
    - **Setup said to "add" the two limits:** uv keeps the first value of a repeated name, so
      the copies after `.env.example`'s own lines were ignored, and the call limit stayed
      30 s. It now says to change the existing lines, also for the maximum age in Scenario 7,
      and Cleanup says to set them back.
    - **Scenario 3:** the proposal shows `context` only when the orchestrator gives one, and
      the stub's terminal shows a `POST /`, not the message text.
  - **The run (T066).** Every scenario ran against the real service, from a clean clone of the
    branch with its own compose Postgres on a fresh volume, removed afterwards. The `.env`
    model (OpenRouter), the helloworld sample fetched at `1bb0871294` (both files read before
    running), and the stub on 9998. Following the README alone, the clone reached a completed
    read-only hand-off and an approved gated hand-off 85 s after the first command (SC-008),
    with uv's package cache warm. Outcomes are in the pull request. No scenario needed a
    code change, so no fix task was added.
  - **Not done:** Scenario 9's "with the network disconnected" could not be checked here, because
    this machine does not allow unprivileged network namespaces (`unshare -rn` is refused).
    The suite, Ruff, and the hook passed in the clean clone with no agent or service running.
    T069 repeats the check.
- **Phase 11 as built (2026-11-11)**:
  - **AGENTS.md (T067)** describes the agent modules, the stand-in and its streaming transport,
    `make_a2a_app`, the `agent_transport` seam beside `connect`, the agent entry and its six
    limits, and the command that serves the stand-in on 9998.
  - **The trees above (T068)** now match what was built:
    - `tasks.md` is listed as it is;
    - `check_agent_tasks` and `AgentTasksMiddleware` are noted as added only with agents
      (Phase 8);
    - the tests list gains `test_agent_task_store.py`, and the adjusted tests are the ones 003
      changed. `test_subagent.py` needed no change, while `test_gate.py`, `test_mcp_gate.py`,
      and `test_config.py` did.
  - **The one Ruff suppression is gone (T069).** The Postgres test file imported the store
    contract's tests with `# noqa: F401`, since Phase 2. It now lists them in `__all__`, which
    Ruff counts as use, and still collects all ten. No other suppression was added in 003, and
    `pyproject.toml` gained only the dependencies.
  - **The suite with the network disconnected (SC-009):** `unshare -rn` is refused here
    (AppArmor restricts unprivileged user namespaces), but `bwrap --unshare-net` is allowed.
    Run that way on the working tree, the process saw only `lo` in `/proc/net/dev`, and a
    connection to an outside address failed with "Network is unreachable". The suite passed
    twice (429 passed, 19 skipped). As a second check, a copy of the tree synced in uv's
    Python 3.12 image passed the suite twice and Ruff in a container with `--network none`.
    The loopback transport tests need only `lo`.
  - **Constitution Check, re-run against the code (T070):** every principle still passes, with
    no deviation; the evidence is in the pull request.
- **Phase 12 as built (2026-11-11, convergence)**:
  - **A task starts at its acknowledgment (T073).** The hand-off passes the store's `now` to
    `send_message`, which reads it once, when the first task, update, or direct reply arrives
    (`SendOutcome.acknowledged_at`). The task's row takes that as `started_at`, so the listed
    start and the maximum age count from the acknowledgment rather than from the end of the
    call. This corrects the Phase 6 observation above. An answer still keeps the start of the
    task it continues.
  - **Read at the acknowledgment, not computed afterwards.** T073 suggested the store's `now()`
    at recording less the time elapsed since the acknowledgment. Built that way first, it
    mixed the process's clock with the store's. Under the suite's frozen store clock, two
    tasks started 0.3 s apart got starts ordered by jitter, and
    `test_several_outcomes_are_delivered_by_one_run_in_the_order_they_arrived` failed in two
    of ten full runs. Reading the store's clock at the acknowledgment uses one clock, the one
    every instance shares. It costs one `now()` per message to an agent, inside the call's
    time limit.
  - **A failing clock is the store's failure, not the agent's.** If reading the clock fails,
    the acknowledgment stays untimed and the task starts when it is recorded. The failure is
    not reported as the agent's, and the agent is not marked unavailable.
  - **The Phase 9 empty-id tests** wrapped `_send` and took the outcome as its last argument;
    the wrapper now names its arguments.
  - **Run against the real service.** The `.env` model, the stub on 9998 marked read-only, a
    5 s call limit and poll interval, and the scratch database `freyja_p12`, dropped
    afterwards. The `slow 20` hand-off went pending with `started_at` exactly 5.00 s before
    `last_checked_at`; before this change the two were equal. The task completed and was
    delivered once, 24.2 s after its start: 20 s of work and up to one poll interval.
