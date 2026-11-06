# Implementation Plan: MCP Tool Delegation

**Branch**: `002-mcp-tool-delegation` | **Date**: 2026-11-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-mcp-tool-delegation/spec.md`

## Summary

Give the orchestrator tools it did not write. At startup the service reads a list of MCP
servers from `Settings`, connects to each, lists its tools, and passes them to the one agent
factory beside Freyja's own. A server is either a command the service launches and stops, held
on one connection for the life of the process, or a loopback address of a server the operator
runs, connected once per call so it recovers by itself. Every tool gets exactly one
classification from a deterministic rule: a server tool is read-only only if the operator's
configuration lists it, and everything else pauses on the existing approve, edit, reject
interrupt, on the orchestrator and its subagent alike. Hints a server publishes about its own
tools are shown and never used. `build_agent` returns the catalog together with the agent, so
the new `GET /tools` endpoint reports exactly what the orchestrator was given, plus each
server's last known reachability. A failing, slow, or dead server becomes an error result the
run continues from; a misconfigured one stops startup and is named.

The client is the `mcp` SDK 2.x used directly; its `Client` accepts launch parameters, a URL,
or an in-process server object, which is the test stand-in. The suite needs no network and no
installed server. The quickstart proves the reference git server.

Design details are in [research.md](research.md); the entities in
[data-model.md](data-model.md); the catalog endpoint and the changed proposal shape in
[contracts/openapi.yaml](contracts/openapi.yaml); the operator-facing server list in
[contracts/server-config.md](contracts/server-config.md); the manual validation walk-through
in [quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 (`.python-version`), managed by uv with the committed
`uv.lock`, src layout.

**Primary Dependencies**: unchanged from 001 (deepagents 0.7.32 on langgraph 1.2.18 and
langchain 1.4.7, fastapi 0.162.0, sse-starlette 3.6.0, pydantic-settings 2.16.1), plus two new
runtime dependencies: `mcp>=2.3,<3` (2.3.0 resolved; the MCP client, R1) and `jsonschema>=4.20`
(4.26.0 resolved; already required by `mcp`, declared because Freyja imports it to validate
edits, R9). Together they add eight packages to the lock. `langchain-mcp-adapters` is not used.

**Storage**: none added. No table and no migration. Tool results are ordinary tool messages in
the existing LangGraph checkpoints. Server status is in memory per service instance.

**Testing**: pytest 9 with pytest-asyncio, the scripted chat model, and in-memory persistence,
as in 001. New: `tests/mcp_stub.py`, an MCP server the suite connects to inside the test
process through the SDK's in-process transport; two transport tests that run the same stub
over pipes and over the loopback interface. No network, no external server, no model provider.

**Target Platform**: Linux server process (uvicorn). Tool servers run on the same machine.

**Project Type**: single web service, src layout (`src/freyja_agent/`).

**Performance Goals**: the catalog read answers from memory within 1 s (SC-003). A
misconfigured server fails startup within 30 s (SC-004): each server has 20 s to connect and
list, and all servers connect concurrently. A connect-and-call round trip to an address server
on loopback is about 10 ms (probe), so per-call connections are not a cost that needs design.

**Constraints**: classification is a pure function of configuration, evaluated at startup; no
tool reaches the orchestrator without one; every gated call pauses before it reaches a server;
no call is retried; a tool call is bounded by `FREYJA_MCP_CALL_TIMEOUT_SECONDS`; an address
must be loopback, checked without DNS; credentials reach a launched server only through named
environment variables and are redacted from everything a server link emits; the catalog read
contacts no server; no blocking I/O in async handlers (Ruff `ASYNC`).

**Scale/Scope**: a handful of servers and tens of tools. One new endpoint, three new settings,
one new package (`delegates/`), one changed API schema (`ActionRequest.server`), and a changed
factory return type. Out of scope per the spec: A2A delegates, authentication, servers on
other machines, the CLI, configuration reload, relaunching a server, health checks, output
size limits, per-thread tool sets.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | Yes, central | Every server tool that the operator has not listed as read-only is registered in `interrupt_on` with approve, edit, and reject, on the orchestrator and on the general-purpose subagent, generated from the same classification (R8). A server connection is reachable only through a tool in the tool node, which the HITL middleware wraps; the catalog route and startup only list tools and never call one. Probe: a proposal paused before the server received anything, and approve and edit each produced exactly one call. Edits are validated against the tool's JSON Schema and the tool name stays pinned (R9). Nothing is retried, so an approval authorizes one call (R7). The pause is the same checkpointed interrupt as today, so a decision can arrive after any delay. A tool with no declared input allows approve and reject only. | PASS | PASS |
| II. Protocol-based delegation | Yes, first exercise | Tool collections are integrated over MCP and nothing else. The only new import is the `mcp` SDK, a protocol client with no agent loop and no model client; no agent framework enters the process (R1). Each server stays a separate process owned by its team. Freyja owns routing, the single voice, and approval. Deterministic server tools are called as tools, not wrapped in another model loop. | PASS | PASS |
| III. One runtime, every environment | Yes | `build_agent` remains the only place the orchestrator is assembled; it now takes the delegate tools and returns the catalog with the agent, so the service, the tests, and the catalog endpoint cannot disagree (R11). `Runtime.open` is the only place servers are connected. Tests swap one seam, the target handed to `mcp.Client`, at the same level as the in-memory checkpointer: listing, conversion, classification, gating, calling, status, and the routes are the production code (R13). Environments differ only by `FREYJA_MCP_SERVERS`. LangGraph Server and Studio are not used. | PASS | PASS |
| IV. Configuration through settings | Yes | Three settings with safe local defaults: `FREYJA_MCP_SERVERS={}` (no servers), `FREYJA_MCP_CALL_TIMEOUT_SECONDS=30`, `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS=20`, all documented in `.env.example` (R2). No command, address, or tool name is hardcoded outside tests. An address must be loopback, so the default posture stays local. Credentials are named in `pass_env` and read from the environment; a launched process receives only those and six basic variables; values are redacted on the way out and settings errors do not echo input (R2, R12). | PASS | PASS |
| V. Automated quality gates | Yes | Every FR has a test (map in R13); the default suite still needs no network, database, model, or installed server. All server I/O is async through the SDK; the time limit is `asyncio.timeout`; connections are owned by asyncio tasks, not threads. Launching a command is done by the SDK without a shell, from operator configuration only, never from model output. No new Ruff suppression is planned. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes, first exercise | Whether a tool pauses is decided by a fixed rule over configuration at startup: listed read-only or gated. The model, the tool's description, and the server's own hints are not inputs (R8, FR-006). A tool with no classification stops startup instead of running ungated, including a tool a Deep Agents upgrade might add. The loopback rule and the unique-name rule are likewise checked from configuration before any request is served (R2, R4). | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; the Postgres checkpointer and SSE unchanged; Ruff only. The model string is untouched. `deepagents<0.8` stays pinned and now also guards the fixed table of Freyja's own tools. | PASS | PASS |

No deviations. The Complexity Tracking table is therefore empty.

One refinement of the spec's wording is recorded rather than hidden: the spec gives two
classifications, read-only and effectful. The API reports three values. For server tools the
spec's rule holds exactly, as `read_only` and `gated`. The third, `workspace`, is given only by
a fixed table to Freyja's own file-writing and delegation tools, which FR-011 says need no
approval and which are not read-only (R8).

## Project Structure

### Documentation (this feature)

```text
specs/002-mcp-tool-delegation/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── openapi.yaml     # GET /tools and the changed ActionRequest
│   └── server-config.md # The FREYJA_MCP_SERVERS shape, rules, and examples
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/freyja_agent/
├── config.py                  # + McpServerConfig; mcp_servers, mcp_call_timeout_seconds,
│                              #   mcp_startup_timeout_seconds; hide_input_in_errors
├── agent/
│   ├── factory.py             # build_agent(..., delegate_tools=()) -> Orchestrator(agent, catalog);
│   │                          #   interrupt_on generated from the catalog; tool-node verification
│   ├── catalog.py             # NEW: Classification, CatalogTool, the table of Freyja's own tools,
│   │                          #   classify(), gate configuration, UnclassifiedToolError
│   ├── tools.py               # send_notification unchanged; GATED_TOOLS and the fixed interrupt
│   │                          #   mapping move into the catalog
│   ├── context.py             # unchanged
│   └── prompts.py             # unchanged
├── delegates/                 # NEW package: what the orchestrator integrates over protocols
│   ├── __init__.py
│   ├── mcp_servers.py         # default_connect(); ToolServer (owner task or per-call connection,
│   │                          #   call() with time limit, status, redaction); open_tool_servers()
│   └── mcp_tools.py           # MCP tool -> StructuredTool; result -> text (FR-018); server hints
├── runtime/
│   ├── runtime.py             # Runtime + catalog, servers; Runtime.open(..., connect=default_connect)
│   ├── persistence.py         # unchanged
│   └── locks.py               # unchanged
└── api/
    ├── app.py                 # includes the tools router
    ├── tools.py               # NEW: GET /tools
    ├── schemas.py             # + ToolCatalog, ToolServerStatus, CatalogTool; ActionRequest.server
    ├── serialize.py           # interrupt_to_pending(interrupt, catalog) fills server
    ├── decisions.py           # edits validated against JSON Schema for delegate tools
    ├── streaming.py           # passes the catalog to interrupt_to_pending
    └── threads.py             # uses runtime.catalog in place of GATED_TOOLS

tests/
├── mcp_stub.py                # NEW: stub MCP server; in-process for the suite, `python -m
│                              #   tests.mcp_stub [--http PORT]` for the transport tests and quickstart
├── conftest.py                # + fixture: app assembled the production way with stub servers
├── test_config.py             # + the three new settings
├── test_mcp_config.py         # NEW: both forms accepted; both, neither, non-local, unknown field,
│                              #   bad name refused; input not echoed
├── test_mcp_startup.py        # NEW: tools loaded and logged; unreachable; unknown read-only name;
│                              #   collisions; bad tool name; empty server; unset pass_env; release
├── test_mcp_calls.py          # NEW: read-only run, direct and through the subagent; tool error;
│                              #   time limit; unreachable then recovered; non-text parts;
│                              #   structured data; concurrent calls; redaction
├── test_mcp_gate.py           # NEW: pause names the server; approve, edit, reject; edit validation;
│                              #   no declared input; unlisted tool; server label ignored; subagent
├── test_catalog.py            # NEW: contents equal the tools the model was offered; no servers;
│                              #   hints; input schema; status; read contacts no server
├── test_mcp_transports.py     # NEW: the stub over pipes (including process exit) and over
│                              #   loopback HTTP (including stop and restart)
├── test_factory.py            # + every tool classified; unknown tool fails; interrupt_on = gated set
└── test_gate.py, test_state.py, test_subagent.py  # adjusted for ActionRequest.server and the
                               #   Orchestrator return type; behavior unchanged
```

Also edited: `pyproject.toml` and `uv.lock` (`mcp`, `jsonschema`), `.env.example` (three
settings with a commented example), `README.md` (configuring a server and its read-only list,
the catalog, the reference git server, the suite), `AGENTS.md` (layout).

**Structure Decision**: single project in the existing src layout. One new package,
`delegates/`, holds everything that speaks a delegation protocol, so a later A2A feature has an
obvious home and nothing protocol-specific leaks into `agent/` or `api/`. Classification lives
in `agent/` beside the factory because it is a property of the assembled orchestrator, not of
MCP: Freyja's own tools are classified by the same module. `runtime/` keeps owning process
lifetime, now including server connections.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

None. The Constitution Check passed without deviations before research and after design.

## Implementation Notes

- **Dependency choice**: the plan builds on `mcp` 2.x directly. The LangChain adapter pins the
  previous SDK major, launches a command per call in its default mode, and lets audio results
  and transport failures end a run (R1). Revisit if the adapter moves to `mcp` 2 and gains an
  injectable transport.
- **Owner task**: a launched server's connection is entered and exited by one dedicated
  asyncio task, because a transport failure cancels the scope of the task that entered it and
  the lifespan task must not be that task (R3). The hub keeps a reference to each task.
- **`build_agent` return type** changes from the compiled graph to `Orchestrator(agent,
  catalog)`. Callers that only need the graph take `.agent`; the change is mechanical in the
  runtime and the test fixtures (R11).
- **Order inside `Runtime.open`**: persistence, tool servers, agent, yield; close servers, then
  the pool. A failure connecting to a server therefore closes the pool on the way out.
- **001 contract**: `ActionRequest` in `specs/001-thread-runs-api/contracts/openapi.yaml`
  gains `server` when the schema changes in code, so the two contract files do not disagree.
- **Phasing suggestion for tasks**: configuration and startup first (User Story 4's startup
  half is a prerequisite for everything), then User Story 1, 2, 3, the runtime half of 4, and 5.
