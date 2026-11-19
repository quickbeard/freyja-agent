# Specification Quality Checklist: Agents Section of the Web Console

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-19
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Validated on 2026-11-19 at specification time, in two passes. The first draft passed the
  checklist as its author read it. It was then read by a reviewer that had not written it,
  against the description, the constitution, the specs of features 003 and 004, and the code
  the draft makes claims about. Each finding was checked against the code before the spec was
  changed. What the second pass fixed:
  - **A contradiction with principle I.** FR-022 said no path sends a message to an agent
    that requires approval without a decision, while an edge case let a run in progress keep
    sending unseen after the operator tightened the choice. FR-022 now has the choice checked
    before every send that would not pause.
  - **A stale page could undo a tightening.** With the later save kept, as in feature 004, a
    page that still showed an agent as running without approval put it back there under a
    confirmation about an address. FR-012 refuses a change asked from stale values.
  - **Tasks could reach an agent they were not given to.** An answer or a cancel for a task
    given up at a move went to the new address, and an environment entry taking a saved
    agent's name inherited its tasks. FR-025 ties a task to the agent as it was when the task
    started.
  - **Claims about today that the code does not bear out**: which forms of an address are
    removed today (FR-028), what "accepts text" checks (FR-007), what a blocked resume
    answers (FR-023), which existing tests change (FR-031), and a way to stop a run that
    does not exist.
  - **Requirements two people would build differently**: when two spellings are one address
    (FR-006), when card text reaches the orchestrator (FR-015), what the reason of a refusal
    may hold (FR-016), which time limit a card read falls under (FR-014, FR-021), and how
    soon another instance has a change (FR-019).
  - **Edges added**: one address under two names (FR-006), the console shown inside another
    origin's page (FR-029), a query or fragment in an address, a request whose answer never
    arrives (FR-004), a card that mentions its own address (FR-028), lines a library logs
    (FR-028), and tasks of an agent that was down at startup (FR-026).
- `FREYJA_A2A_AGENTS`, "environment variables", "database", "card", and "catalog" are the
  description's words for what exists today. The spec names no library, no table, no route,
  and no status code; where it names a number, the number is a limit an operator meets.
- No clarification markers. The judgment calls are recorded in Assumptions rather than
  marked, and they are the candidates for `/speckit-clarify`, most consequential first:
  1. An environment agent whose card cannot be read or used no longer stops startup
     (FR-020). The description and principle IV can also be read as keeping today's
     behavior for environment agents.
  2. A switch to requiring approval holds at once, for runs in progress too (FR-022), where
     the description says runs in progress finish with the agents they started with. A
     removed agent, by contrast, can still be sent to by a run in progress (FR-019).
  3. A change of the approval choice alone does not read the agent's card (FR-009), where
     the description says changing an agent reads its card first.
  4. A task belongs to the address it was started at, so after a move it can be neither
     answered nor canceled through Freyja (FR-025). A pending proposal and a conversation
     follow the agent's name instead (FR-023, FR-026).
  5. A change asked from stale values is refused (FR-012), where feature 004 keeps the later
     save.
  6. One address, one agent (FR-006), and the console cannot be shown inside another
     origin's page (FR-029). Neither is in the description.
  7. What the orchestrator is told of a saved agent changes only when an operator adds,
     moves, or checks it (FR-015), and a refusal's reason is the service's own sentence
     (FR-016).
  8. Every change is confirmed, the switch to requiring approval included (FR-011).
  9. An environment agent's address is not shown (FR-017), and what an agent writes in its
     own replies is not searched for its address (FR-028).
  10. The limits: 20 saved agents, four card reads at once, twenty seconds for a card read
      the console asked for.
- No dependency outside this feature: the constitution needs no amendment (Assumptions,
  "Environment and dependencies").
- Validated again on 2026-11-19 after `/speckit-clarify`: 16 of 16 items still pass. Five
  questions were asked and answered; each is in the spec's Clarifications section. They
  settled calls 1, 2, 3, and 6 above and the proposal half of call 4, and changed two of
  them: a removed agent is now cut off from runs in progress as a tightened one is (call 2,
  FR-022), and a pending proposal follows its agent across a move but not across a removal
  and a new addition (call 4, FR-023). Calls 5 and 7 to 10, and the task half of call 4,
  were not asked about and stay as written.
- The first answer was extended the same day: beside the log, the console itself warns about
  every agent that is down, on its card, above the cards by name, and on the sidebar's Agents
  entry from every section (FR-001a, SC-017). 16 of 16 items still pass.
- Read again on 2026-11-19 after the security and reliability checklist review, which changed
  FR-002, FR-004, FR-013, FR-017, FR-022, FR-023, FR-025, FR-028, FR-030, three edge cases,
  and five assumptions (see `security-reliability.md`, "Review record"). 16 of 16 items still
  pass: no marker was added, and the new text names no implementation.
