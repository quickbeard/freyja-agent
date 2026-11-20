# Implementation Plan: Agents Section of the Web Console

**Branch**: `005-web-delegate-agents` | **Date**: 2026-11-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/005-web-delegate-agents/spec.md`

## Summary

Let an operator manage the delegate agents Freyja may hand work to from the web app, without
editing a variable or restarting the service. The Agents section stops being a placeholder: it
lists every agent as a card and lets the operator add one by name and local address, change
its address or its approval choice, remove it, and check it. The console warns about agents
that are down, on the card, above the cards, and on the sidebar from every section.

On the service side, saved agents live in a new table with a one-row revision beside it, as
LLM profiles do. Every run already begins by bringing the instance in step with the active
profile; the same step now also compares the agents revision and, when it moved, rebuilds the
orchestrator through `build_agent` with the tools of the current set of agents. A run takes
that step once, under its thread's lease, and uses the one orchestrator it gets from reading
the thread's state and validating a decision to its last event, so a decision is never
checked against one set of tools and carried out on another. A run in progress keeps the set
it started with. The agents of `FREYJA_A2A_AGENTS` stay as they are and
appear as read-only cards.

An agent that cannot be used no longer stops the service, saved or from the environment: its
link starts not connected, a warning is logged, and the link connects by itself once the agent
answers. An entry that is invalid in itself still stops startup.

The approval choice stays the only input to the gate, which is still generated from the
catalog at every build. Three fixed checks are added around it. Right before any message to
a saved agent is sent, the agent is read again, so an agent that was removed or put under
approval receives nothing more, also from a run in progress. A call proposed for a saved agent
is never sent to another agent added later under its name. A task stays with the agent, at the
address, it was started with: a move or a removal gives the agent's pending tasks up in the
same transaction as the change.

A saved agent's address is returned by the Agents routes and by nothing else. Text the service
words, card details, and every log line of the process, libraries included, have it removed.

The web app remains a client of the API and nothing more, and now refuses to be shown inside
a page of another origin.

Design details are in [research.md](research.md); the entities and tables in
[data-model.md](data-model.md); the new routes in
[contracts/openapi.yaml](contracts/openapi.yaml); the one new stream code in
[contracts/sse-events.md](contracts/sse-events.md); the manual walk-through in
[quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 for the service (`.python-version`), managed by uv with the
committed `uv.lock`, src layout. TypeScript 5 on Node 20.9 or newer for the web app, managed by
pnpm with the committed `web/pnpm-lock.yaml`.

**Primary Dependencies**: unchanged. The service is on deepagents 0.7.32 over LangGraph,
FastAPI, sse-starlette, pydantic-settings, a2a-sdk 1.3.0, and httpx 0.28.1. The web app is
Next.js 16.4.6 with the App Router and Cache Components, React 19.3, Tailwind CSS 4, shadcn/ui,
and swr 2.5.1. No dependency is added on either side.

**Storage**: the existing Postgres database. Three new tables, created at startup with
`CREATE TABLE IF NOT EXISTS`: `freyja_agents`, the one-row `freyja_agents_state`, and
`freyja_agent_calls` (R1, R8). One new nullable column, `agent_ref`, on `freyja_agent_tasks`,
added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (R2). No migration tool. In-memory
implementations of the same protocols serve the suite. The web app stores nothing.

**Testing**: pytest 9 with pytest-asyncio, the scripted chat model, in-memory persistence, and
the stand-in agent `tests/a2a_stub.py` reached in the test process, as before. New: saved
agents in `make_a2a_app`, with stand-ins found by address; store contract tests collected
against the in-memory store and, opt-in, Postgres; two apps on one set of stores for two
instances (R20). The web app's unit tests are Vitest with jsdom and Testing Library; its
end-to-end tests are Playwright in Chromium and Firefox with the service's answers handed to
the page (R21). No network, no agent installed or running.

**Target Platform**: Linux server process (uvicorn), one or more instances sharing one
database; the Next.js server and the agents on the same machine; a current desktop browser.

**Project Type**: web application: the existing service in `src/freyja_agent/` and the existing
web app in `web/`.

**Performance Goals**: a change of agents applies to the next run on every instance, with one
more single-row read per run and a rebuild of about 15 ms when the revision moved (SC-002). A
message to a saved agent costs one primary-key read first (R7). Startup
waits for the card reads at most once, all agents together, within the startup time limit
(SC-006). A card read the console asked for ends within twenty seconds (SC-012). The Agents
section shows twenty cards within 2 s on the service's machine: one request, two queries
(SC-015).

**Constraints**: no message reaches an agent that requires approval without a decision, and
none reaches a removed agent, from the moment the change is committed (SC-004); a run in
progress neither fails nor stops because of a change (SC-003); an add, a move, and a check
contact an agent only to read its card (SC-011); a saved agent's address is in no response
outside the Agents routes, no stream, no thread the service words, and no log line (SC-008);
a move or a removal and the giving up of its tasks are one transaction (FR-024); an
environment-only deployment whose agents answer behaves as before (SC-014); no blocking I/O in
async code (Ruff `ASYNC`); SQL is parameterized (Ruff `S608`); the web app holds no credential,
calls no agent, and decides nothing about approval (constitution III).

**Scale/Scope**: at most 20 saved agents beside the environment's, a handful of operators. No
new setting on either side. Three tables and one column, five routes, one new `error` code,
five new modules in the service (`runtime/saved_agents.py`, `delegates/agent_registry.py`,
`delegates/agent_cards.py`, `delegates/addresses.py`, `api/agents.py`), with `api/guards.py`
split out of `api/llm.py`, and one page, one library module, and six components in the web
app.
Out of scope per the spec: tool servers, agents on another machine or with credentials,
authentication, a set of agents per thread, renaming, and DNS rebinding.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against constitution 1.4.0, and again against 1.5.0 on 2026-11-19, which added three
gates to principle V: a secret scan, Trivy and Semgrep, and a coverage floor for each suite.
The second check changed the row for principle V and nothing the plan decides.

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | Yes, central | No tool is added to the model beyond an agent's existing hand-off and cancel, and each is classified by one value: the operator's approval choice, saved or from the environment. The gate is still generated from the catalog inside `build_agent`, at every build, and the factory still refuses a gate that disagrees with the catalog (R3). Four fixed rules close the paths a runtime change opens: a decision is validated and carried out on one orchestrator, taken under the thread's lease, so a stale catalog can neither let a refused decision through nor apply one call's decision to another (R3); every message to a saved agent is checked against the saved agent right before it is sent, so a removal and a tightened choice hold at once, also for a run in progress and on every instance (R7); a call proposed for a saved agent is never sent to another agent under the same name (R8); a decision is applied under the set in force when it arrives, by the rules of 002 and 003 (FR-023). An add, a move, and a check only read a card (R5). Changing an approval choice is an operator's request to the API, behind a confirmation in the console and the refusal of other origins, never something the agent or a model does. A paused run stays resumable on its thread after any delay; a resume a tightened choice blocks leaves the proposal in place and now says why (R9). | PASS | PASS |
| II. Protocol-based delegation | Yes | Agents are still reached over A2A through the SDK's client, at the configured host and port only. No agent framework is imported. The card request is made with the link's own HTTP client and parsed by the SDK's parser (R5). | PASS | PASS |
| III. One runtime, every environment | Yes | `build_agent` stays the only assembly point and is what a rebuild calls (R3). `run_stream` stays the only run loop. The web app is a client of the API only: it calls no agent, holds no credential, and decides nothing about approval; a confirmation is the operator's own act, and the service applies the same rules to a request sent without the console (R16, R17). Tests swap the two seams they already swap. | PASS | PASS |
| IV. Configuration through settings | Yes, central | Saved agents are operator-managed configuration: kept in the database, changed only through the API (R1, R16). What the environment configures stays valid as a read-only default: environment agents are listed, used, and never stored or changed (FR-017). No secret is entered: an address may carry no user name or password (R11). No setting is added; the limits are fixed rules of the feature, like the profile limit. No address is hardcoded outside tests. | PASS, with one reading recorded below | PASS |
| V. Automated quality gates | Yes | Every FR has a test (map below). The default suites need no network, database, or agent. Card reads and database calls are async; time limits are `asyncio.timeout`; SQL is parameterized. The web app passes `biome check` and its Vitest suite; every user story's console part also has a Playwright test. No new Ruff suppression is planned. Against 1.5.0: the feature stores no secret, and its tests use addresses, not keys, so nothing is added to the secret scan's allowlist. No dependency is added on either side, so the lock files Trivy reads do not change. A statement composed with psycopg's `sql` module carries the Semgrep mark its rule needs, as the saved-agent store's do. Each phase keeps both coverage floors; the table of proposed calls (Phase 5) is a Postgres store, which CI does not measure, so that phase's pull request says what it does to the service's number. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes | Whether a message may still be sent, whether a call still belongs to its agent, whether a task still belongs to its address, whether an address is this machine's and is not another agent's, and whether a saved agent is in use are all fixed checks on stored values (R7, R8, R10, R11, R4). None asks a model, and none depends on what a card says. | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; the Postgres pool; Ruff only for Python. The web app stays Next.js with TypeScript, pnpm, Biome, and Vitest. | PASS | PASS |

No deviation. One reading of principle IV is recorded because the spec's clarification rests on
it. The principle says a deployment configured only through the environment behaves as it did
before. Today such a deployment does not start when one of its agents is down; after this
feature it starts and warns (FR-020, clarified 2026-11-19). Feature 004 read the same sentence
the same way when it let the service start without a usable model: what the environment
configures is still valid, still used, and still decides approval; what changes is that a
fault outside the service no longer keeps the service, and the console that reports the
fault, from starting. A deployment whose agents answer is unchanged, which the existing suite
checks (FR-031).

Four additions carry complexity the governance section asks to justify. None is a violation,
so they are justified here rather than in Complexity Tracking:

- **A set of agents that changes while the service runs, held per run.** Until now the links
  were opened once, and the routes read the graph and the catalog from the runtime whenever
  they needed them. A run has to keep the tools it started with while the next run gets the
  new ones, a decision has to be validated on the orchestrator that carries it out, and a
  link may only close when nothing uses it, so every reader takes a context (R3). The alternative, tools that
  look agents up when called, would leave new tools without a gate entry.
- **A table of proposed calls.** A pause stores a tool's name, not which agent held the name.
  The clarified rule that a proposal does not follow a re-added name, and the requirement that
  a blocked resume names its agent, both need that fact (R8, R9).
- **A column on an existing table.** A task has to say which agent, at which address, it was
  started with, or a move could not tell its own tasks from the next agent's (R2).
- **A log record factory.** It changes how every record of the process is made. It is the one
  mechanism that covers a library's own lines, at any level, on any handler (R12).

## Project Structure

### Documentation (this feature)

```text
specs/005-web-delegate-agents/
├── spec.md              # from /speckit-specify, clarified
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1 to R22, what is not verified, what is not covered
├── data-model.md        # Phase 1: entities, tables, transitions
├── quickstart.md        # Phase 1: manual validation, one section per user story
├── contracts/
│   ├── openapi.yaml     # the /agents routes and their shapes
│   └── sse-events.md    # the `approval_now_required` error
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # from /speckit-tasks (not created by /speckit-plan)
```

No `contracts/settings.md`: this feature adds no setting to the service and no variable to
the web app.

### Source Code (repository root)

```text
src/freyja_agent/
├── config.py                  # + canonical_address() and destination() beside is_local_host (R11)
├── runtime/
│   ├── saved_agents.py        # NEW: SavedAgentRecord; SavedAgentStore protocol;
│   │                          #   InMemorySavedAgentStore; PostgresSavedAgentStore with
│   │                          #   freyja_agents and freyja_agents_state; move and remove give
│   │                          #   the agent's pending tasks up in the same transaction (R1, R10)
│   ├── agent_tasks.py         # + agent_ref on a task; give_up_pending; the pending count by
│   │                          #   agent; the proposed calls and freyja_agent_calls (R2, R8, R10)
│   └── runtime.py             # holds the saved-agent store and the registry; begin_run()
│                              #   refreshes the set, asks the manager, and returns the
│                              #   RunContext every reader uses; assemble no longer fails on
│                              #   a card (R3, R4)
├── delegates/
│   ├── addresses.py           # NEW: the forms an address is removed in; the scrubber and the
│   │                          #   log record factory (R12)
│   ├── agent_cards.py         # NEW: read_card, CardProblem and its nine sentences; the cut
│   │                          #   and cleaned card details (R5, FR-002)
│   ├── agent_registry.py      # NEW: AgentSet and AgentRegistry: start, refresh, hold, adopt;
│   │                          #   which saved agents are in use (R3, R4)
│   ├── a2a_agents.py          # AgentLink gains source, agent_id, ref, connected, connect();
│   │                          #   open_agents gives way to the registry; describe() removes
│   │                          #   every form (R6, R12)
│   ├── mcp_tools.py           # DelegateTool gains agent_id, set for a saved agent's tools (R8)
│   ├── a2a_tools.py           # before a send: the task's ref, connect inside the call's
│   │                          #   time limit, the proposed call, then the saved agent as it
│   │                          #   is now; agent_ref on a new task; a row is only updated
│   │                          #   while its task is pending (R6, R7, R8, R10)
│   └── delegation_worker.py   # takes a context per round and per delivery; gives up a task
│                              #   whose ref is not its link's; connects a link that has a
│                              #   pending task, once per round; records a check only while
│                              #   the task is pending (R3, R6, R10)
├── agent/
│   ├── agent_tasks.py         # AgentTasksMiddleware records the calls a model proposes (R8)
│   └── factory.py             # hands the middleware which tools are an agent's, from the
│                              #   delegate tools it is given (R8)
├── llm/
│   ├── active.py              # the manager rebuilds when the agent set moved as well (R3)
│   └── check.py               # CheckSlots is given the words of its refusals (R15)
└── api/
    ├── app.py                 # + the agents router; installs the log record factory
    ├── guards.py              # NEW: NoInputRoute and refuse_other_origins, moved from llm.py
    ├── llm.py                 # imports the two from guards.py; otherwise unchanged
    ├── agents.py              # NEW: list, add, change, remove, check (R15, R16)
    ├── threads.py             # the run, resume, and state routes take a context under the
    │                          #   lease and read state, validate, and run on it (R3)
    ├── tools.py               # takes a context before it answers; its shape is unchanged (R3)
    ├── schemas.py             # + Agent, AgentList, AgentCreate, AgentUpdate, AgentChanged,
    │                          #   AgentRemoved, AgentCheck
    └── streaming.py           # runs on the context it is given; clears a thread's proposed
                               #   calls when a run starts with input; ends a blocked resume
                               #   with approval_now_required (R3, R8, R9)

tests/
├── a2a_stub.py                        # unchanged in behavior; cards of any size for the bounds
├── conftest.py                        # make_a2a_app gains saved agents and stand-ins by address
├── test_addresses.py                  # canonical form and its refusals, destination, the
│                                      #   forms removed, the log record factory on the
│                                      #   libraries' real lines and on uvicorn's access line
├── test_agent_cards.py                # read_card: each of the nine problems, the size limit,
│                                      #   the cut, the cleaning
├── test_saved_agent_store.py          # store contract, in memory
├── test_agent_task_store.py           # + agent_ref, giving up by ref, the pending counts, the
│                                      #   proposed calls
├── test_llm_check.py                  # + slots made with other words
├── test_agents_api.py                 # the routes, every refusal, other origins, a body that
│                                      #   is not JSON, limits, stale versions, log lines
├── test_agents_apply.py               # next run, run in progress, pause and resume,
│                                      #   background run, the catalog; two instances: a
│                                      #   change through one, then each decision through
│                                      #   the other
├── test_agents_startup.py             # an agent down at startup, saved and environment;
│                                      #   entry errors still stop; connecting later; not in use
├── test_agents_approval.py            # the gate after each change; the check before a send;
│                                      #   proposals across a move and a re-add; a blocked resume
├── test_agents_tasks.py               # given up at a move and a removal, the count, one
│                                      #   transaction, answers and cancels refused, late tasks
├── test_agents_check.py               # the check, its limits, its time limit, what it keeps
├── test_agents_address_nowhere.py     # one recognizable address, searched for everywhere
├── test_a2a_startup.py                # CHANGED: seven cases start the service and warn
├── test_a2a_gate.py                   # CHANGED: one test expects approval_now_required
├── test_a2a_transport.py              # + the log at DEBUG holds no address, over loopback
└── integration/
    ├── test_a2a_tasks_postgres.py     # + the new contract tests, by name
    └── test_saved_agents_postgres.py  # the store contract, the limits under two pools, the
                                       #   move and removal transactions, the added column

web/
├── next.config.ts             # + headers(): no page of another origin may frame the console (R19)
├── app/agents/page.tsx        # the Agents section in place of the placeholder
├── components/
│   ├── app-sidebar.tsx        # + the warning badge on the Agents entry (R18)
│   └── agents/                # NEW: agent-list, agent-card, agent-form (add and change, with
│                              #   the confirmation step), remove-dialog, check-result,
│                              #   agents-warning
├── lib/
│   ├── api.ts                 # isGone learns agent_not_found
│   └── agents.ts              # NEW: the agent types, the calls, and which agents are down
├── __tests__/
│   ├── next.config.test.ts            # + the two headers
│   ├── lib/agents.test.ts             # NEW
│   ├── lib/api.test.ts                # + isGone for agent_not_found
│   ├── components/app-sidebar.test.tsx  # + the badge, and no badge when the read fails
│   ├── components/agents/*.test.tsx   # NEW: one file per component
│   └── app/agents/page.test.tsx       # REPLACED
└── e2e/
    ├── api.ts                 # + AgentService and answerAgents
    ├── agents.spec.ts         # NEW: the section as it is read, the warnings
    ├── agents-manage.spec.ts  # NEW: adding, checking, approval, moving, removing
    ├── console.spec.ts        # CHANGED: the Agents entry leads to the section; + the frame rule
    └── llm.spec.ts, profiles.spec.ts  # + an answer for the list of agents in their setup
```

Also edited: `README.md` (the Agents section, saved agents beside `FREYJA_A2A_AGENTS`, what
changed at startup), `AGENTS.md` (layout and the convention for a new router), `.env.example`
(the comment on `FREYJA_A2A_AGENTS`: an agent that is down no longer stops startup),
`specs/003-a2a-agent-delegation/contracts/agent-config.md` (the same), and
`specs/001-thread-runs-api/contracts/sse-events.md` (the new code, where a reader looks
first).

**Structure Decision**: the two existing projects, each in its place. Saved agents and their
revision are shared state, so their store sits in `runtime/` beside the task store it meets in
one transaction. What reaches an agent stays in `delegates/`: the registry, the card reading,
and the address forms are split from `a2a_agents.py`, which is already 550 lines and keeps the
link and the sending. `api/` keeps the routes, and the two helpers both routers need move to
a module of their own.

### Requirement to test map

| Requirement | Tests |
|---|---|
| FR-001, FR-002, FR-003 | `test_agents_api.py` (the list, its states, no contact), `test_agent_cards.py` (the cut); `web/__tests__/components/agents/agent-card.test.tsx`, `agent-list.test.tsx` (plain text, the empty state) |
| FR-001a | `web/__tests__/components/agents/agents-warning.test.tsx`, `agent-card.test.tsx`, `components/app-sidebar.test.tsx`; `web/e2e/agents.spec.ts` (from the LLM section too, and gone after a reading that finds the agent reachable) |
| FR-004 | `web/__tests__/lib/agents.test.ts`, `agent-list.test.tsx` (unreachable, refusal), `agent-form.test.tsx` (nothing kept once closed, an outcome that is not known) |
| FR-005, FR-006 | `test_addresses.py`, `test_saved_agent_store.py`, `test_agents_api.py`, `integration/test_saved_agents_postgres.py` (two adds at once) |
| FR-007, FR-008, FR-016 | `test_agent_cards.py`, `test_agents_api.py` (each refusal leaves nothing behind, SC-007; only the card was requested, SC-011) |
| FR-009, FR-010, FR-012 | `test_agents_api.py` (a move, an approval change with the agent down, a name refused, a stale version); `agent-form.test.tsx` (the version is the one the form opened with; a later one in the list takes the confirmation back) |
| FR-011 | `agent-form.test.tsx`, `remove-dialog.test.tsx` (each confirmation's words, the live copy, Confirm as a press of its own); `web/e2e/agents-manage.spec.ts` |
| FR-013, FR-014, FR-015 | `test_agents_check.py`; `check-result.test.tsx` |
| FR-017, FR-018 | `test_agents_api.py` (read-only, no address), `test_agents_startup.py` (not in use, entry errors stop startup) |
| FR-019 | `test_agents_apply.py` (including a run that cannot learn the current set, and a resume on an instance that had not yet seen the change) |
| FR-020, FR-021 | `test_agents_startup.py`, the changed `test_a2a_startup.py` |
| FR-022 | `test_agents_approval.py` (every kind of message, the orchestrator, the subagent, a background run; a run in progress after a tightening and after a removal; a continuation that began before a removal; an address another agent took over) |
| FR-023 | `test_agents_approval.py`, the changed `test_a2a_gate.py` |
| FR-024, FR-025, FR-026 | `test_agents_tasks.py`, `test_saved_agent_store.py`, `integration/test_saved_agents_postgres.py` |
| FR-027 | `test_agents_api.py` and `test_agents_check.py` (what the stand-in's transport saw), `test_agent_cards.py` (no redirect, no proxy) |
| FR-028 | `test_agents_address_nowhere.py`, `test_addresses.py`, `test_a2a_transport.py` |
| FR-029 | `test_agents_api.py` (other origins on all five routes, bodies that are not JSON); `web/__tests__/next.config.test.ts`, `web/e2e/agents.spec.ts` (the frame) |
| FR-030 | `test_agents_api.py`, `test_agents_startup.py`, `test_agents_approval.py` (log lines) |
| FR-031 | the existing suite, changed only as research R22 lists |
| FR-032 | all of the above; `web/e2e/agents.spec.ts` and `agents-manage.spec.ts` for the console's part of each story |

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

None. The Constitution Check passed without deviations before research and after design. The
four additions that carry complexity are justified under the Constitution Check above.

## Rollout

Every instance has to run this version before the first agent is saved: an instance on the
earlier version gives up the tasks of agents it does not know (R2). The one schema change to
an existing table is made at startup only when it is missing, under a lock timeout.

Going back to the earlier version leaves approval intact, by that version's own rules: it
reads no saved agent, so none of their tools exist, a proposal for one is never sent, and
their pending tasks are given up. The new column is nullable and the earlier version names
the columns it reads and writes. Saved agents stay in the database and are in use again when
this version returns. All of this holds as long as the environment the earlier version runs
with gives no agent a saved agent's name: that version knows names only, so such an agent
would be handed the saved agent's waiting proposals and pending tasks. This was reasoned
from the code on `main` and is not tested.

## Corrections to the spec made with this plan

One statement of the spec did not survive the design and is corrected in the same request.
A second was met more strictly than it was worded until the spec was brought in line, and
a third is general enough to stand:

- **What a re-added agent's old proposal accepts** (Edge Cases). The spec said it accepts
  approve and reject, as for any removed agent. A pause holds no record of which agent held
  the name, so the proposal accepts what the tool under that name allows now; whatever is
  decided, nothing is sent (R8). The rule the clarification set is unchanged.
- **The check before a send** (FR-022) was worded for a message sent without a pause. The
  design makes it for every message to a saved agent, because a continuation that began
  before a removal would otherwise still send its approved message, against SC-004 (R7).
  FR-022 was brought in line at the checklist review of 2026-11-19, with the rule for an
  earlier address that became another agent's.
- **How many existing tests change** (FR-031 says "the tests that expect startup to fail").
  There are five test functions, seven cases, not three as the pull request for the spec
  said; the requirement's wording was already general and stays (R22).

## Implementation Notes

What the implementation settled differently from the plan, or found out, phase by phase.

### Phase 1

- **The refusal of another origin is logged by `freyja_agent.api.guards`.** The line "Refused
  <method> <path>: the request was made by a page of another origin" was logged by
  `freyja_agent.api.llm` and moved with the function (T001). Its text and level are unchanged;
  the logger's name is part of each line the service prints, so a filter on that name sees the
  difference. It is the one thing in Phase 1 that a deployment can observe.
- **The stand-in agent gained two options beyond T003 as first written**: `tag_count`, for the
  limit on a skill's tags that the checklist review added to FR-002, and `card_chunked`, for a
  card sent with no length announced, which the test of the size limit needs (T006). T003 now
  names both.
- **Two test files were added that the plan's tree does not list**: `tests/test_guards.py`,
  which pins the refusal's words for the two routers, and `tests/test_a2a_stub_cards.py`,
  which tests the stand-in's card options before Phase 2 relies on them.

### Phase 2

- **The column check and the lock timeout were seen on Postgres 17.11**, the server of
  `docker-compose.yml` (T015, research "Not verified"). On a table shaped as feature 003
  created it, `setup()` adds `agent_ref` and earlier rows read back with no ref. With the
  column present, `setup()` completes while another session holds the table in an open
  transaction. Without the column and with that session open, `setup()` gives up at the lock
  timeout with an error that names the table, changes nothing, and the next start adds the
  column.
- **A card problem is a value, and `read_card` raises `CardError`, which carries it.** The plan
  and the data model name the exception `CardProblem`. Ruff's naming rule wants an exception's
  name to end in `Error`, and no suppression is added in this feature, so `CardProblem` is
  the code with its sentence and `CardError` is what is raised.
- **The address rule refuses more than research R11 lists**: a space or a control character
  anywhere in the address, which the parser would drop without saying so; port 0; and, in the
  path, every character httpx would send encoded. A path holds letters, digits, slashes, and
  `- . _ ~ ! $ & ' ( ) * + , ; = : @`, so the kept form is the form that is requested. The
  limit of 2,000 characters is checked on the kept form as well, since writing the port out
  can make it longer than what was entered.
- **The scrubber finds what has the shape of an address and looks it up**, where research R12
  describes removing each known address in each of its forms. The first version did that, and
  the independent review measured its cost: it grew with the number of known addresses, to
  ten seconds for one large card text at capacity. Cleaning now costs the same however many
  addresses are known (about 1 ms for 12 KB of text at capacity). It also covers forms R12
  does not list: an address on a default port as a client requests it, without the port;
  `('::1', 9998, 0, 0)`, as a refused IPv6 connection is worded; any other way of writing
  this machine with the agent's port; and a path a library wrote encoded. The host alone is
  never removed.
- **The scrubber removes an address when in doubt.** An address is not found inside a longer
  number or a longer name (`9998` in `99980`, `localhost` in `mylocalhost`), and is found
  after a hyphen or after a letter before an IP address. Where a library logs the port apart,
  the port becomes `<port>`. An agent on a port that is also a common number, such as 200,
  has that number removed from the HTTP libraries' lines.
- **The log record factory never raises into the code that logged.** A record it cannot clean
  is handed on as it was made. Not cleaned: the keys of a mapping of arguments, and attributes
  added to a record with `extra`.
- **No error of this phase keeps a library's error behind it**, as its cause or as its
  context: the address rule's, the card read's, and the stores'. Such an error holds the
  address or the value that was refused.
- **`read_card` labels**: a redirect is one of the five answers that send a client elsewhere;
  any other answer that is not 200 is "no card". A certificate that is not accepted reads as
  "nothing answered", since the client reports both as a failed connection; feature 006 gives
  it a reason of its own.
- **Card details lose a NUL character**, which the database cannot store, and only the first
  4,100 characters of a text are cleaned before it is cut to 2,000.
- **The store keeps card details as a plain mapping**, in the form they read back from
  storage, so the same details compare equal in both stores; `AgentCardSummary.to_dict` and `from_dict` convert. `SavedAgentStore.change` is one
  method for the approval choice, a move, or both, and `replace_card` answers with whether
  the details were recorded and whether they changed, which the check of Phase 6 reports.
- **A task's `agent_ref` cannot be updated**, like its key and its thread.
- **`tests/asgi_stream.py` also records each request's headers**, so that the card test can
  see what the request asked for. Two requests are still equal by method, host, port, and
  path.
- **Known and left as it is**: two instances that start at the same moment on an empty
  database can fail in `CREATE TABLE IF NOT EXISTS`, which every store of the service uses;
  the one that failed starts on its next try. And `setup()` still creates the task table's
  index at every start, as since feature 003, which waits behind a session that is writing
  to the table. Neither is new, and neither is changed here.

### Phase 3

- **An eighth existing case expected startup to fail for a card problem.** Research R22 and
  T016 list seven, all in `tests/test_a2a_startup.py`. The redirect test of
  `tests/test_a2a_transport.py`, which serves a stand-in on loopback, expected the same and
  was changed the same way: the service starts, the agent is listed as not reachable with
  the redirect's sentence, and the target of the redirect still receives nothing. One other
  test file gained setup and no check changed: the fixture of `tests/test_addresses.py`
  starts from a plain record factory, because the app now installs the process's own when
  it is created and a factory installs once.
- **A hand-off that has to read the card first sends within what is left of its time
  limit.** FR-021 asks for the read inside the hand-off's own limit. The read is given the
  call's limit, and the send, or the cancel, the remainder, so the two together take one
  limit and not two.
- **A caller that waited for another's card read takes its result**, the failure included,
  and requests nothing itself; a read that was cancelled leaves no result, and the next
  caller reads.
- **An environment agent's tools are built again whenever its card details change**, not
  only the first time they are read: an agent that was down, came back with another card,
  and was connected again is described from the card it has now.
- **A warning that a saved agent is not in use is logged when an instance finds it out**, at
  startup and at the refresh that first sees it, and not again at every later refresh while
  it stays so (FR-030: "when it finds either out").
- **The model is not built again when only the agents changed.** The manager keeps the model
  it built for the active profile and assembles the orchestrator around it for the new set,
  so no key is read and no client is made; it logs one line, "The orchestrator was rebuilt
  for a changed set of agents".
- **The run and resume routes take their context before they read the thread.** When the
  agents or the active profile cannot be read, a request therefore gets the stream with
  `start` and the `internal` error also for a thread that is paused or has no pending
  decision, where it used to be refused with 409 first and fail in the stream otherwise.
- **A delivery that can take no context under the lease gives the lease back and is tried
  again in the next round**, and uses up none of the outcome's attempts. Before, a profile
  that could not be read ended the background run with `internal`, which counted as one.
- **The worker words a task given up after a move as moved** when the link in use under the
  name is the same saved agent at a later address, and as no longer configured otherwise.
- **A round of watching that cannot learn the current set claims nothing**, and the worker
  logs the round as failed and tries again at the next interval, as it does for any failed
  round.
- **`Runtime.agents` is now read from the registry**, and `Runtime` gained `begin_read`, the
  context of a route that only reads, which falls back to what the instance last built: its
  graph, its catalog, and the agent set those were built for.
- **Taking a context is two single-row reads, not one.** Research R3 and R22 count the
  agents' revision. The model manager reads the active profile's revision as well, as it has
  since feature 004 at the start of every run, so the catalog and the thread state now read
  both before they answer.
- **An environment agent's card is now held to the limits of FR-007**, which FR-031 does not
  list among its exceptions. Its card is read by `read_card`, like a saved agent's: a card
  larger than one megabyte, or one served compressed although the request asked for it
  plain, was accepted at startup before and now leaves the agent listed as one that cannot
  be used, with the reason.
- **A cancellation while a context is being taken gives the thread's lease back.** The
  context is taken right after the lease, before the code that releases the lease on a
  refusal; a service that stops at that moment would otherwise leave the thread busy until
  the lease expires (found by the independent review).
- **Known and left as it is: a context taken for a stream that never starts is not
  released.** A client that disconnects after the route answered and before the stream's
  first event leaves the run's hold on its agent set in place, as it leaves the thread's
  lease until it expires. The cost is a link of an earlier set that stays open until the
  service stops.
- **Seen by hand (T036, research "Not verified").** Started through uvicorn with the stand-in
  agent configured and not running, the service logs the warning and uvicorn's access lines
  as before, with the record factory installed and an address registered. A traceback is
  logged as before: uvicorn's "Exception in ASGI application" and the worker's failed round,
  with the database stopped. Once the stand-in was started and given a pending task, the
  next round read its card, checked the task, and the catalog listed the agent as reachable
  with its tool described from the card; no line of the log held the agent's port.
  `uv run pytest` passes with the factory installed beside pytest's log capture: every
  test of the suite, among them every test that reads captured records. A rebuild with twenty saved
  agents, forty agent tools of fifty in all, took 73 ms at the median over ten rebuilds (71
  to 172 ms); taking a context with nothing changed took under 0.01 ms beside its one read.
- **With the database stopped, the catalog answers from the last set after thirty
  seconds**, which is how long the connection pool waits before the read of the revision
  fails. Before this phase the catalog read nothing and answered at once. The thread state
  needs the database in any case.
- **`frame-ancestors` was seen in Chromium and in Firefox against the built app** (T035).
  Without the two headers the end-to-end test fails in both: the frame shows the console.
  Chromium refuses a page's request to an address on this machine unasked, which kept the
  frame empty whatever the headers said, so the test gives Chromium that permission first.
  `headers()` applies beside the `/api` rewrite: `/llm` and `/agents` are answered with both
  headers. The redirect at `/` carries neither, and neither does the answer to a forwarded
  `/api` path when nothing answers at the service, which is Next.js's own error; a
  forwarded answer of a running service was not looked at.

### Phase 4

- **The section has one control, Refresh**, which reads the list again, as the LLM section
  has. The tasks name no control for this phase; Add comes with Phase 5. A card has none.
- **The warning on a card is a note named "Warning"**, with the word itself in its text, a
  sentence that says what is wrong ("This agent cannot be reached.", "This agent is not in
  use."), the reason, and the time where there is one. An agent that is not in use was
  tried by nothing, so its warning gives no time.
- **A saved agent the store has and the context's set does not yet** is listed as not tried:
  it was saved, or moved, between the two reads, and nothing on this instance has tried it.
- **Pending tasks are counted in one query for both kinds of agent**, by the agent's name
  and the ref its tasks carry: null for an environment agent, the current ref for a saved
  one. A task under the name with another ref, of an agent that had the name before, is
  not counted for either.
- **The list's failure is logged by its kind only.** What a database error says may name the
  database's own address, which no response and no log line of this route repeats.
- **`AGENTS.md` names the new route and the Agents section's files**, and the README's
  sentence became a description of the section; T049 names the README alone.
- **Seen by hand, with the real service and the console in a browser** (quickstart section 1
  and section 6's first point, without Add and without Check). With nothing configured the
  page says that no agent is configured, and the list is empty with `limit` 20. With the
  service stopped the page says it cannot be reached and offers to try again, with no card.
  With three stand-ins named in `FREYJA_A2A_AGENTS`, one of them not running, the page
  shows three cards marked as coming from the environment, each with its approval choice,
  the two that answer with their card's details, and the third with the warning and its
  reason. Neither the list's answer nor the service's log held an address, and each
  stand-in received one request, for its card.
- **One Semgrep finding is marked as wrong, in a test.** The end-to-end test of card text
  puts a script tag in a card and looks for it on the page as text; Semgrep takes the tag
  for a value written into a page. The line is marked for that rule, with the reason.
- **The independent review found no defect in the route or the console.** It found that
  two tests of the list could not fail for what they were named after, that one branch and
  one failing read were not tested, and that the docs' list of end-to-end files lacked the
  new one. All four were acted on: the address test now gives an environment agent a card
  that names addresses and a reason taken from a library's error, and the failure test
  raises an error with a marker that is no address and looks for it in the answer and the
  log.

### Phase 5

- **The proposed-call rows were built and seen as a whole** (T062, research "Not verified").
  On the orchestrator the model's node and the tools' node both give the empty namespace;
  in the subagent both give `tools:<id>`, the delegating call's own, with no further
  segment. The id in the model's reply is the id the tool receives, with a scripted model
  and with a real one (`chatcmpl-tool-...`), and an edit decision keeps it. The row is in
  the table before the run pauses, is still there while the message is on its way, and is
  gone once the send is recorded. A rejected call never reaches its tool, so its row stays
  until the next reply in its namespace replaces it, or the thread's next run with input
  clears it. All of it was seen on Postgres 17.11 as well, in the opt-in tests and with
  the real service.
- **A call's row is compared by the saved agent's id alone**, as T062 says. The first
  version compared the agent's name as well, and the independent review showed what that
  broke: one tool call id used twice in a reply for two environment agents has one row,
  naming the later call's agent, and the other call was then kept back, where both were
  sent before this feature. Between two environment agents both ids are none, so a call
  follows the tool's name, as it always has.
- **The order inside a tool** is the send ledger, the task's ref for an answer or a cancel,
  the card read when the link needs it, the conversation, the store's clock, and then the
  row. Nothing is awaited between the row's check and the send's first write but that
  write. Phase 7's check of the saved agent belongs at the same place.
- **A run that starts with input clears the thread's rows just before the graph runs**, so
  a run that is refused because no usable model is active clears nothing: a step that waits
  for a background run's retry keeps its rows. A row that cannot be cleared ends the run
  with `internal`, like a row that cannot be written.
- **What the rows cost.** With at least one agent, every reply of the model writes: one
  delete, and one insert per call of an agent's tool, in one transaction. With no agent
  nothing is written at a reply. Every run with input makes one delete, with agents or
  without.
- **Known: two calls of one reply that share a tool call id have one row**, the later
  call's. To environment agents both are sent, as before. To two saved agents only the one
  the row names is sent; the other is kept back with the sentence for a removed agent. To
  one saved agent both were sent when it was tried, because both read the row before
  either finished; a second call that read after the first one's send was recorded would
  find no row and be kept back. Each of these fails toward sending nothing.
- **The address proof found a leak the first time it ran.** Card details were cleaned of
  the addresses known when the card was read, and never again. An environment agent's
  card, read at startup, or a saved agent's, kept in the database, that names an address
  where an agent is saved later went on showing that address in the catalog, in the tool
  description the model is given, and in the list's `card`. Details are now cleaned again
  where they are used (`cleaned` in `delegates/agent_cards.py`): when an instance makes an
  agent set, after every link of the set has told the scrubber its address, and in every
  answer of the Agents routes. What stands in for an address can be a character longer
  than the address was, so a text is cut again and marked as cut. A saved agent that is not
  in use has no link to tell the scrubber its address, so making a set now tells it every
  saved agent's address itself: the independent review found that such an agent's address
  stayed in an environment agent's card text after a restart. Cleaning again costs
  nothing for a text with no colon in it, which the scrubber skips, and 0.3 ms for a card
  at every limit of FR-002. What a card says of an address is cleaned from the moment an
  agent is saved there, not before: until then it is only what the card says. Research
  R12's "a card that names another agent's address is cleaned too" now holds whichever of
  the two came first.
- **Phase 4's unit tests of `web/lib/agents.ts` were never committed.** The root
  `.gitignore` ignores every `lib/` directory, a rule of the Python packaging template,
  and excepted `web/lib/` only. `web/__tests__/lib/agents.test.ts` (T038) existed on the
  machine that wrote it, passed there, and was in no commit, so CI never ran it. The
  three other files of that directory are tracked, which is why the gap did not show. The
  rule now excepts `web/__tests__/lib/` too, and the file is committed with this phase,
  with T055's tests in it.
- **The route's refusals are worded by the rule that found them.** A name or an address
  the environment has is refused with the sentence the list already shows for a saved
  agent that is not in use, from the same function (`AgentRegistry.overridden`). A value
  that breaks a rule is refused with the rule's own sentence as the whole message, under
  the error types `agent_name` and `agent_address`, with nothing of the validation
  library's put before it. `approval` is a literal, so `none` is refused in the library's
  words, "Input should be 'required'".
- **Two failures the tasks do not name are answered with 500 `internal`**: the saved
  agents cannot be read before the card is read, and the agent cannot be saved after it.
  Each is logged by its kind only. An agent refused because its card cannot be used is
  logged at INFO with the card problem's sentence.
- **An add whose change this instance could not take up answers with the agent as not
  tried.** The add stands, as T059 says; the answer cannot say that the agent is
  reachable, since nothing on this instance holds its link yet.
- **The card read for an add uses a client of its own**, made the way a link makes its
  client (`agent_http_client`), so the two cannot come to differ: an explicit transport,
  no redirect followed. The limit of twenty seconds is a constant beside the route.
- **The shared error shape (`ErrorDetail`) did not gain the three new codes.** A test of
  feature 004 holds that shape to 004's contract, and FR-031 keeps existing tests as they
  are. No route uses the shape to build an answer; the codes are in this feature's
  contract and in the route's tests.
- **Known, and the same on the LLM routes: a request of another origin whose body is
  marked as JSON and is not JSON is answered 422, not 403.** The framework reads the body
  before it runs the rule. The answer repeats nothing and nothing is done; a browser
  sends no JSON across origins without asking first, and is given no permission.
- **The console's form is beside the list, not inside it** (T065). When a request got no
  answer and the list then cannot be read either, the form stays, with what was typed and
  the sentence that the outcome is not known. A refusal about one value is shown at that
  value (a field error, a taken name, a taken address); every other refusal is shown
  over the form, the card problem's sentence among them. The form checks only that both
  values were entered; every rule is the service's. `addAgent` sends the name and the
  address and nothing else, whatever it is handed.
- **The end-to-end stand-in (`AgentService`) refuses in the service's own sentences**
  (T066), keeps addresses in the service's form, and records every request with its body.
  It has no limit and no busy refusal. The spec searches the requests for the address's
  host and port and for a path marker, never for a bare port. One assertion of Phase 4's
  spec changed: the section's buttons are now Refresh and Add. Chromium's permission for
  requests to this machine was not needed: the page talks to its own origin only.
- **A test that searches for a port looks for it as a number of its own** (`holds_port` in
  `tests/conftest.py`). A bare search for four digits can find them in an id, written
  plainly or in groups with hyphens between them, or in the fraction of a timestamp. The
  first version of the helper let a group of a hyphenated id through; the review counted
  about a hundred such ids in what the address proof searches, which would have failed it
  by chance about once in sixty-five runs. One test of Phase 4 in `tests/test_agents_api.py` searched that
  way over text that holds `checked_at`, and now uses the helper. The test of
  `tests/test_catalog.py` that fails this way on `main` is left for a request of its own.
- **An answer that arrives after its form was closed changes nothing in the form that is
  open now.** Reading a card can take twenty seconds, and the form can be closed and
  opened again meanwhile. The late answer has the list read again, when the agent was
  saved and when no answer came, and is otherwise said to no one. The first version
  closed the form that was open, with what was being typed in it; the review found it.
  The LLM section's form, which this one is modelled on, behaves the earlier way and is
  not changed in this phase.
- **Known: a 422 names an unknown field by the name it was sent under.** A request that
  sends an address as the name of a field gets that name back in `loc`. The validation
  error keeps where and why and drops the value; the name of a field the shape does not
  have is neither. The console sends two fields with fixed names. The same holds on the
  LLM routes, whose guard this is, and it is left for a change of that guard.
- **What the independent review found, and what was done.** It found no path that sends a
  message without its decision, and none that sends a proposal to another agent under its
  name. It found the two leaks and the two defects named above, that the checks of a
  refused add's log line could not fail once an earlier test had saved the same port,
  that three tests of the rows passed with the tools' deletion taken out, and that nothing
  tested a row that cannot be read or rows that cannot be cleared. All were acted on, and
  each test added or changed for a defect was seen to fail with its fix taken out: an add
  that must be refused is now made at a port nothing ever saves, and the rows are looked
  at as the next reply finds them.
- **Seen by hand, with the real service, the console in a browser, and a real model**
  (quickstart section 2). On an instance started with no agent, Add in the console saved
  the stand-in entered as `HTTP://LocalHost:9998/`: its card shows `http://localhost:9998`,
  "Requires approval", reachable, and the stand-in's own details. The stand-in's log shows
  one `GET` for its card and no `POST`. The form opened empty the second time. Each
  refusal of the quickstart left one card and kept what was typed: nothing answering, the
  service's own address ("served no agent card"), the name again, the address again, and
  the four addresses that break a rule, each at its field. A run then paused naming the
  agent, with one row in `freyja_agent_calls` holding the agent's id; once approved the
  stand-in received one `POST`, the run completed with its answer, and the table was
  empty. After a restart the agent was listed with the same address, approval choice,
  version, and card details, and reachable. A second agent was added by request as the
  README shows. No line of the service's log held a port of either agent.
- **Coverage of the service's lines went from 90.24% to 90.40%** as CI measures it,
  without the Postgres tests. The Postgres half of the proposed calls, five methods, is
  among the lines CI does not run; the floor of 88% is unchanged.
