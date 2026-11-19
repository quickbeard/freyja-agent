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

Checked against constitution 1.4.0.

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | Yes, central | No tool is added to the model beyond an agent's existing hand-off and cancel, and each is classified by one value: the operator's approval choice, saved or from the environment. The gate is still generated from the catalog inside `build_agent`, at every build, and the factory still refuses a gate that disagrees with the catalog (R3). Four fixed rules close the paths a runtime change opens: a decision is validated and carried out on one orchestrator, taken under the thread's lease, so a stale catalog can neither let a refused decision through nor apply one call's decision to another (R3); every message to a saved agent is checked against the saved agent right before it is sent, so a removal and a tightened choice hold at once, also for a run in progress and on every instance (R7); a call proposed for a saved agent is never sent to another agent under the same name (R8); a decision is applied under the set in force when it arrives, by the rules of 002 and 003 (FR-023). An add, a move, and a check only read a card (R5). Changing an approval choice is an operator's request to the API, behind a confirmation in the console and the refusal of other origins, never something the agent or a model does. A paused run stays resumable on its thread after any delay; a resume a tightened choice blocks leaves the proposal in place and now says why (R9). | PASS | PASS |
| II. Protocol-based delegation | Yes | Agents are still reached over A2A through the SDK's client, at the configured host and port only. No agent framework is imported. The card request is made with the link's own HTTP client and parsed by the SDK's parser (R5). | PASS | PASS |
| III. One runtime, every environment | Yes | `build_agent` stays the only assembly point and is what a rebuild calls (R3). `run_stream` stays the only run loop. The web app is a client of the API only: it calls no agent, holds no credential, and decides nothing about approval; a confirmation is the operator's own act, and the service applies the same rules to a request sent without the console (R16, R17). Tests swap the two seams they already swap. | PASS | PASS |
| IV. Configuration through settings | Yes, central | Saved agents are operator-managed configuration: kept in the database, changed only through the API (R1, R16). What the environment configures stays valid as a read-only default: environment agents are listed, used, and never stored or changed (FR-017). No secret is entered: an address may carry no user name or password (R11). No setting is added; the limits are fixed rules of the feature, like the profile limit. No address is hardcoded outside tests. | PASS, with one reading recorded below | PASS |
| V. Automated quality gates | Yes | Every FR has a test (map below). The default suites need no network, database, or agent. Card reads and database calls are async; time limits are `asyncio.timeout`; SQL is parameterized. The web app passes `biome check` and its Vitest suite; every user story's console part also has a Playwright test. No new Ruff suppression is planned. | PASS | PASS |
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
│   ├── a2a_tools.py           # before a send: the task's ref, connect inside the call's
│   │                          #   time limit, the proposed call, then the saved agent as it
│   │                          #   is now; agent_ref on a new task; a row is only updated
│   │                          #   while its task is pending (R6, R7, R8, R10)
│   └── delegation_worker.py   # takes a context per round and per delivery; gives up a task
│                              #   whose ref is not its link's; connects a link that has a
│                              #   pending task, once per round; records a check only while
│                              #   the task is pending (R3, R6, R10)
├── agent/
│   └── agent_tasks.py         # AgentTasksMiddleware records the calls a model proposes (R8)
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
│   ├── components/app-sidebar.test.tsx  # + the badge, and no badge when the read fails
│   ├── components/agents/*.test.tsx   # NEW: one file per component
│   └── app/agents/page.test.tsx       # REPLACED
└── e2e/
    ├── api.ts                 # + AgentService and answerAgents
    ├── agents.spec.ts         # NEW: the section as it is read, the warnings, the frame rule
    ├── agents-manage.spec.ts  # NEW: adding, checking, approval, moving, removing
    └── console.spec.ts        # CHANGED: the Agents entry leads to the section
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
