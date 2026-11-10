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
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/freyja_agent/
├── config.py                  # + A2aAgentConfig; a2a_agents and six limits; agent and server
│                              #   names share one namespace
├── agent/
│   ├── factory.py             # build_agent(..., agent_tasks=...); AgentTasksMiddleware on both
│   │                          #   agents; check_agent_tasks added to Freyja's tools
│   ├── catalog.py             # CatalogTool.agent and .pinned_args; OWN_TOOLS + check_agent_tasks;
│   │                          #   gate description for agent tools
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
├── test_a2a_transport.py      # NEW: the stub over loopback: hand-off, pending, stop and restart
├── integration/
│   └── test_a2a_tasks_postgres.py  # NEW: tables, claim across two runtimes, ledger
└── test_factory.py, test_runs.py, test_subagent.py, test_catalog.py, test_state.py
                               # adjusted for check_agent_tasks, the agent field, agent_tasks,
                               #   and subgraph streaming; behavior unchanged
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
