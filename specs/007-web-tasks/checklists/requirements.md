# Specification Quality Checklist: Tasks Section of the Web Console

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-20
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

- Validated on 2026-11-20 at specification time, in one pass by its author, against the
  description, the constitution, the specs of features 001, 003, 004, and 005, and the code
  the draft makes claims about (the thread routes, the run stream, the thread state, and the
  refusal of other origins). No reviewer who had not written it has read it yet.
- "Thread", "run", "stream", "proposal", and "agent task" are the words of features 001 and
  003 for what exists today. The spec names no library, no table, no route, and no status
  code; `/api/...` and `agent_tasks` appear once each, as the existing names the description
  uses. Where it names a number, the number is a limit a person meets.
- No clarification markers, on purpose: the description names four decisions that are the
  owner's and are to be asked in `/speckit-clarify`, and the skill allows three markers. They
  are written as working choices and listed under Assumptions, "Open decisions", with a fifth
  found while writing:
  1. The section's name, since "task" already means a delegate agent's task.
  2. No users: everyone who can open the console sees every task and can decide any proposal.
  3. Follow-ups on the same task, or one message per task (FR-020).
  4. Whether the first version can stop a run (FR-022).
  5. A run lasts only while its page is open, as the service's API behaves today (FR-021).
- Judgment calls that are not asked and are recorded under Assumptions, "Behavior choices":
  a sixth status, stopped, which the sketch does not have (FR-003); the order of the
  statuses; that a thread from before this feature may be missing from the list until its
  next run (FR-004); arguments edited as text (FR-015); no second confirmation of an approval
  (FR-017); and the limits of 100 rows, 200 characters, and fifteen seconds.
- One dependency outside the spec: FR-010 and FR-021 rest on a run's stream passing the
  console's forwarding as it is produced, and on a page that leaves ending the run through
  it. Neither is proven (Assumptions, "Not proven"); the plan's research proves them first.
- Validated again on 2026-11-20 after `/speckit-clarify`: 16 of 16 items still pass. Five
  questions were asked and answered; each is in the spec's Clarifications section. The first
  three confirmed the draft: the name stays Tasks, with "agent task" for the other kind
  (FR-001a); the console having no users is accepted and said on the page and in the README
  (FR-026a); and follow-ups stay (FR-020). The last two changed it, so the notes above about
  FR-021 and FR-022 describe the draft and no longer the spec:
  - A run the console starts goes on after its page is closed (FR-021), where the draft kept
    today's behavior. User story 5, three edge cases, FR-010, FR-013, FR-023, and SC-011
    follow from it.
  - The first version has Stop for runs the console started (FR-022, FR-022a, SC-012), where
    the draft had none.
- One rule for Stop is the specification's and was not asked: a stop waits for the step
  under way, so that an approved action is never cut off between taking effect and being
  recorded (FR-022; Assumptions, "Behavior choices"). Feature 001 accepted that gap for a
  client that goes away; a button that causes it on request is another matter under
  principle I.
- Three things are not proven and go to the plan's research first (Assumptions, "Not
  proven"): that a stream passes the console's forwarding as it is produced, that the
  service can end a run after the step under way, and that FR-021 and FR-022 can be built
  without changing files another session is working in.
- Read again on 2026-11-20 after `/speckit-plan`, which changed FR-002, FR-003, and FR-028,
  added two edge cases, and replaced the Assumptions' "Not proven" with "Proven in the plan's
  research" (see the spec's Clarifications, "Plan, 2026-11-20"). 16 of 16 items still pass:
  no marker was added, and the new text names no implementation beyond the existing words
  this checklist already allows.
