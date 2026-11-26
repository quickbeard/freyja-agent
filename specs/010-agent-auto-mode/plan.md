# Implementation Plan: Auto Mode for Agents

**Branch**: `010-agent-auto-mode` | **Date**: 2026-11-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/010-agent-auto-mode/spec.md`

## Summary

One Manual/Auto switch for the instance, kept beside the saved agents' revision, and each
agent's existing approval choice named Manual or Auto. An agent's tools are classified as
running without approval only when the switch is Auto and the agent is Auto; every other
combination gates them, on the ordinary orchestrator and on the strict one of callers' threads
alike. Manual holds at once because the check every agent tool makes before it sends a message
no person decided reads the switch; Auto holds from the next build, which the revision moves
to. The console shows the switch as a dropdown under Tasks in the sidebar and an agent's mode
as a switch on its card, each behind a confirmation where it loosens approval. The owner's
change of 2026-11-26 (spec) moved the dropdown to the left of the Send button of New task and
of a task's follow-up box; the sidebar no longer shows it.

## Technical Context

**Language/Version**: Python 3.12 (service), TypeScript with Next.js 16 and React 19 (console)

**Primary Dependencies**: Deep Agents over LangGraph, FastAPI, psycopg; Base UI's Select and
Switch through shadcn/ui, SWR. No dependency is added.

**Storage**: Postgres. Migration 2 adds `mode` to `freyja_agents_state`, `text NOT NULL DEFAULT
'auto'` with a check that it is `manual` or `auto`.

**Testing**: pytest (in-memory runtime, scripted model, stand-in agents), the opt-in Postgres
tests, Vitest with jsdom, Playwright in Chromium and Firefox.

**Target Platform**: the existing service and console; no new process.

**Project Type**: web service and web app (`src/`, `web/`).

**Performance Goals**: no new request on any page: the switch is read with the list of agents
every page already reads; one more single-row read per build and per undecided send.

**Constraints**: the gate stays generated from the catalog in `build_agent`; no code path may
send a message to an agent that a person did not decide while the switch is Manual.

**Scale/Scope**: one column, one route, one field, two console controls.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against constitution 1.6.0.

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | Yes, central | No new way around the gate is added. An agent's tools run without approval only where an operator already chose so for that agent (005's approval choice, 003's `read_only`); the switch can only take that away, never grant it. The classification is still decided by fixed rules before any run (`AgentSet.gated`, `agent_tools`) and the gate is still generated from the catalog in `build_agent`, which refuses a gate that disagrees with it. Manual holds at once for runs in progress, because every agent tool reads the switch before it sends a message no person decided (`_still_allowed`), the same way 005 made a tightened choice hold. Tool servers' tools and `send_notification` are untouched. **Deviation, by the owner's decision**: feature 009's FR-013a gated every message to an agent from a caller's thread whatever the agent's choice; the owner chose that a caller's thread follow the same rule as any other (spec, Clarifications). An operator who sets an agent to Auto, with the switch on Auto, thereby lets a task another agent started message that agent with no person involved. This is recorded as an amendment in 009's spec, in its docs, and in Complexity Tracking below. | PASS, with the deviation justified | PASS |
| II. Protocol-based delegation | No | Agents are still reached over A2A only; nothing about the link changes. | PASS | PASS |
| III. One runtime, every environment | Yes | `build_agent` stays the only assembly point; the ordinary and strict builds take the same rule from the agent set. The console only shows and sets the switch through the API: it decides nothing about approval, and the service applies the same rules to a request sent without the console. | PASS | PASS |
| IV. Configuration through settings | Yes | The switch is operator-managed configuration: kept in the database, changed only through `PUT /agents/mode`. Nothing the environment configures changes meaning, and no setting is added. Where nobody set the switch it is Auto, so a deployment configured only through the environment behaves as before: an entry with `read_only: true` still runs without approval. | PASS | PASS |
| V. Automated quality gates | Yes | Every FR has a test (Tasks, "Tests"). The migration has its checksum in `RELEASED`, and the Postgres migration tests were run. Coverage stays above both floors. No Ruff suppression, no pyright silence, and no Semgrep mark is added beyond what the stores' SQL already carries. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes | Whether a message to an agent waits is decided by the switch and the agent's choice, read from the database, never by the model. | PASS | PASS |

## Design decisions

- **R1, where the switch lives.** A column of `freyja_agents_state`, the one-row table that
  holds the saved agents' revision. A change moves the revision in the same statement, so
  every instance builds again at its next context, exactly as for a change of an agent's
  choice, and no second "has anything changed" read is needed.
- **R2, the rule.** `AgentSet` carries `auto` beside each agent's `read_only`;
  `AgentSet.gated(link)` is `not (auto and read_only[name])`. `agent_tools` classifies from it
  for both builds; the strict build no longer forces every agent tool to be gated (FR-007).
- **R3, Manual at once.** `_Send` keeps the saved-agent store as `modes` for every agent, and
  `_still_allowed` reads the switch whenever a message no person decided would be sent by a
  tool built to run without approval, for environment and saved agents alike. The 009-only
  rule that wanted a decision on a caller's thread whatever the build is removed with the
  strict flag: the strict build's tools are gated exactly when the ordinary build's are.
- **R4, default Auto.** Every agent's own mode already defaults to Manual, so Auto as the
  switch's default makes nothing run without approval that an operator did not set to; Manual
  as the default would have made every `read_only: true` entry ask after an upgrade. The
  owner may overturn this (spec, Clarifications).
- **R5, the route.** `PUT /agents/mode` with `{"mode": ...}`, on the Agents router, so it
  takes the router's refusal of other origins and its refusal that repeats no value. It
  answers with the mode as kept, logs a change, and refreshes this instance's set. No version
  is asked: the switch has two values and the last write is the one in force.
- **R6, the console.** The dropdown reads the list of agents under `AGENTS_KEY`, which every
  page already reads, and is disabled while it cannot be read. Manual is sent at once; Auto
  is confirmed first with the number of agents it lets run. The card's switch opens a
  confirmation that sends a change of the approval choice at the version the card showed,
  and refuses to send once the list shows a later version. The agent form's two choices are
  named Manual and Auto.

## Project Structure

### Documentation (this feature)

```text
specs/010-agent-auto-mode/
├── spec.md
├── plan.md
├── tasks.md
├── checklists/requirements.md
└── contracts/README.md   # the route and the field live in 005's openapi.yaml
```

### Source Code (repository root)

```text
src/freyja_agent/runtime/migrations.py      # migration 2
src/freyja_agent/runtime/saved_agents.py    # mode, set_mode
src/freyja_agent/delegates/agent_registry.py  # AgentSet.auto, gated
src/freyja_agent/delegates/a2a_tools.py     # _Send.modes, _still_allowed; no strict flag
src/freyja_agent/runtime/runtime.py         # the strict build takes the set's rule
src/freyja_agent/api/agents.py, schemas.py  # PUT /agents/mode, AgentList.mode
web/lib/agents.ts                           # AgentsMode, setAgentsMode
web/components/agents-mode.tsx              # the dropdown
web/components/tasks/send-box.tsx           # `beside`, left of the Send button (owner's change)
web/components/tasks/follow-up.tsx, new-task.tsx  # place the dropdown there (owner's change)
web/components/agents/agent-card.tsx, mode-dialog.tsx, agent-form.tsx, agent-list.tsx
```

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| A caller's thread no longer gates every message to an agent (amends 009 FR-013a) | The owner chose one rule for every task (spec, Clarifications) | Keeping 009's rule was offered and not chosen. Its main loss, a loop through an Auto agent that hands work back to Freyja, is bounded by the limits on tasks in progress until 009's hand-over count is built, and the switch set to Manual stops it at once. |

## Implementation notes

- The migration tests built "a deployment before migrations" by starting the build and
  dropping the migrations table, which with a second migration left its column behind. They
  now apply the first migration alone, and the test of a reader during the first migration
  runs the first migration alone, since any later migration that adds a column waits behind
  such a reader by design.
- Feature 009's tests that relied on a caller's hand-off pausing to a stand-in set to run
  without approval now set the switch to Manual, or use an agent set to Manual.
