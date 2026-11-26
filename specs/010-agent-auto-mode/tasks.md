# Tasks: Auto Mode for Agents

**Input**: Design documents from `specs/010-agent-auto-mode/`

**Prerequisites**: plan.md, spec.md

**Tests**: every behavior ships with a test (constitution principle V). The tests are written
with the code of their phase and listed with it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- **[Story]**: the user story the task belongs to (US1, US2, US3)

## Pull requests

The owner asked for one pull request holding this file, the spec, the plan, and the
implementation (spec, Clarifications). Every task below is in it.

## Phase 1: Foundational (the switch and the rule)

- [x] T001 Add migration 2 to `src/freyja_agent/runtime/migrations.py`: `mode` on `freyja_agents_state`, `text NOT NULL DEFAULT 'auto'`, checked to be `manual` or `auto`; add its checksum to `RELEASED` in `tests/test_migrations.py`.
- [x] T002 In `src/freyja_agent/runtime/saved_agents.py`, add `Mode`, and `mode` and `set_mode` to `SavedAgentStore` and both stores; a change moves the revision, setting the mode it has moves nothing, any other value is refused (FR-001).
- [x] T003 [P] Contract test in `tests/test_saved_agent_store.py`, run against both stores (`tests/integration/test_saved_agents_postgres.py`), and a second instance's view of the mode in the Postgres module.
- [x] T004 In `src/freyja_agent/delegates/agent_registry.py`, `AgentSet.auto`, read with the saved agents at start and at every refresh, and `AgentSet.gated` from it and the agent's choice (FR-002, FR-003).
- [x] T005 In `src/freyja_agent/delegates/a2a_tools.py`, `_Send.modes` for every agent and `_still_allowed` reading the mode before a message no person decided, for environment and saved agents (FR-004); remove the strict flag and `needs_a_person` (FR-007); in `src/freyja_agent/runtime/runtime.py`, the strict build takes the set's rule.
- [x] T006 [P] `tests/test_agents_mode.py`: the set's rule; Manual holds at once for a run built while Auto, for an environment and a saved agent; a paused step blocked with `approval_now_required` once Manual, and going on once Auto again; a proposal that can only be approved once Auto.
- [x] T007 [P] `tests/integration/test_migrations_postgres.py`: migration 2 on a database at the first migration keeps the revision and holds `auto`, and the check refuses another value; the helpers that build an older deployment apply the first migration alone.

## Phase 2: User Story 1 - Stop every agent from running without asking (P1)

- [x] T008 [US1] `PUT /agents/mode` and `AgentList.mode` in `src/freyja_agent/api/agents.py` and `schemas.py` (FR-005); the route and the schema in `specs/005-web-delegate-agents/contracts/openapi.yaml`.
- [x] T009 [P] [US1] `tests/test_agents_mode.py`: the list's mode, Auto where nobody set it; the route sets it, moves the revision once, refuses another value without repeating it, and logs the change; the catalog follows the mode and `send_notification` stays gated (FR-006); a hand-off to an Auto agent waits while Manual and not while Auto.
- [x] T010 [P] [US1] `tests/test_agents_api.py` and `tests/test_agents_address_nowhere.py`: the route is in the section's sweep and in the contract's table, refuses another origin's request, and its answers, refusals, and log lines hold no agent's address.
- [x] T011 [US1] `web/lib/agents.ts` (`AgentsMode`, `setAgentsMode`) and `web/components/agents-mode.tsx`, placed under the Tasks entry by `web/components/app-sidebar.tsx` (FR-008).
- [x] T012 [P] [US1] `web/__tests__/components/agents-mode.test.tsx` and `web/__tests__/lib/agents.test.ts`: Manual at once, Auto confirmed with its count and not on a double press, nothing sent when canceled, not changeable while the list cannot be read, a refusal and an unknown outcome said.

## Phase 3: User Story 2 - Set one agent to Auto from its card (P2)

- [x] T013 [US2] `web/components/agents/mode-dialog.tsx` and the switch on `agent-card.tsx` for a saved agent in use; the mode as a badge for every other agent; the note that Manual holds an Auto agent; `agent-list.tsx` passes the mode; the form's choices named Manual and Auto (FR-009).
- [x] T014 [P] [US2] `web/__tests__/components/agents/agent-card.test.tsx`: the switch asks first, sends the version the card showed, sends nothing once the list shows a later version, says a refusal and an unknown outcome, closes when the agent is gone, and the held note; the existing card, list, and form tests in the new words.
- [x] T015 [P] [US2] `web/e2e/agents-mode.spec.ts`: the dropdown and the card's switch in Chromium and Firefox; `agents.spec.ts` and `agents-manage.spec.ts` in the new words, with the one switch a saved agent's card has; `web/e2e/api.ts`'s `AgentService` answers `PUT /agents/mode`.

## Phase 4: User Story 3 - Tasks another agent started follow the same rule (P3)

- [x] T016 [US3] `tests/test_a2a_server_approval.py`: on a caller's thread a hand-off to an Auto agent is sent while the mode is Auto, waits while Manual, and waits for a Manual agent; the check at send time follows the mode; the tests that relied on a pause set the mode to Manual or use a Manual agent (also `test_a2a_server_send.py`, `test_a2a_server_tasks.py`).
- [x] T017 [US3] Amend 009's FR-013a and its edge case of a loop, and 007's FR-017 (FR-007, FR-010).

## Phase 5: Polish

- [x] T018 [P] `docs/agents.md` (Auto mode), `docs/a2a-server.md`, `docs/web-console.md`, and `AGENTS.md`.
- [x] T019 Run every gate: the commit hook over the changed files, `pnpm test:e2e`, and the opt-in Postgres tests.

## Phase 6: Owner's change, 2026-11-26 - the dropdown beside the Send buttons

The owner moved the dropdown after the feature was merged (spec, Owner's change). One pull
request holds this phase.

- [x] T020 `web/components/tasks/send-box.tsx` takes what stands left of its Send button (`beside`); `follow-up.tsx` and `new-task.tsx` give it `AgentsModeSelect`, restyled for that row and saying that the mode is every task's; `app-sidebar.tsx` no longer shows it (FR-008).
- [x] T021 [P] `web/__tests__/components/tasks/follow-up.test.tsx` and `new-task.test.tsx`: the dropdown on the button's row, choosing a mode sends no message and keeps the text, Manual can be chosen while a run is in progress, and Auto is confirmed inside New task without closing it; `tasks-testing.ts`'s `serve` answers the list of agents apart from what a test keeps; `agents-mode.test.tsx` in the new words; `task-view.test.tsx` keeps every end of a run it waits for, which the page forgets once it read the task again.
- [x] T022 [P] `web/e2e/agents-mode.spec.ts`: the dropdown in New task and on a task's page, by mouse and with the keyboard, hidden while a proposal waits, absent from the sidebar and the list of tasks; an Auto agent's card held to Manual once Manual is chosen beside Send.
- [x] T023 `docs/web-console.md` (Agent approvals), `docs/agents.md`, and `AGENTS.md`.
