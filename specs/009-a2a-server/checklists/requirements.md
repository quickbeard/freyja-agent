# Specification Quality Checklist: Receive Tasks from Other Agents over A2A

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-25
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

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
- Validated on 2026-11-25: 16 of 16 items pass, with the readings below.
- "No implementation details" is read as in features 003 to 008. The spec names what a caller,
  a deployer, and an operator see and set: the protocol and its operations (send, read,
  subscribe, cancel), a card, a bearer token and the authorization header it travels in, a
  conversation id, a listener and its address. It names no module, library, table, route, or
  setting.
- The description came from a design the owner agreed to on 2026-11-25, with four answers:
  callers on this machine without a token and callers on other hosts with one; a person
  decides every proposal and the caller sees the task as working; the caller sees states, step
  names, and the reply as it is written; another message in a conversation continues its
  thread.
- No `[NEEDS CLARIFICATION]` marker was left. Where the description was silent, the spec makes
  a choice and records it under Assumptions, "Behavior choices". These are the ones a reader
  is most likely to want to decide differently, and the first candidates for
  `/speckit-clarify`. The first two were not in the description at all; the review below
  brought them up.
  1. Every message to a delegate agent from a caller's thread waits for a person, also for an
     agent that runs without approval (FR-013a). Letting the agent's approval choice decide,
     as everywhere else, means hand-offs with no person anywhere; a setting per caller is the
     middle way.
  2. A named caller's runs work in a file area of its own and cannot change the skills; the
     local caller keeps the shared workspace (FR-009a).
  3. A send that does not ask for a stream waits at most 30 seconds, then returns the task as
     working (FR-011). The protocol can be read as waiting for the task's end.
  4. A cancel ends the task for its caller at once and rejects a pending proposal (FR-021).
  5. A received task stays working while a delegate agent's task is awaited (FR-016).
  6. Callers and tokens come from the environment and change only with a restart (FR-004).
  7. The service does not encrypt the outside listener's connection itself, and warns when the
     address for callers does not encrypt (FR-029).
  8. A token is honored at the service's own address too, so that two agents on one machine
     can be told apart (FR-002).
  9. The limits' defaults: 5 tasks in progress for a caller, 20 in all, 3 hand-overs, 20 named
     callers (FR-004, FR-025, FR-027).
  10. Listing a caller's tasks is not offered (FR-007).
- The draft was read by an independent reviewer before it was committed, against the
  constitution, the specs of features 003, 005, 006, and 007, the code, and the protocol's
  library. I checked its two most serious findings in the code, and both changed the design:
  every thread shares one workspace that is read and written without approval, so a caller
  could have asked Freyja for other tasks' files or changed the skills (now FR-009a); and an
  agent that runs without approval would have been handed work on a caller's word with no
  person anywhere (now FR-013a). Its other findings are in the spec as well:
  - a received task's state is defined by what its thread still has, so that none stays
    working forever, also around runs that are not console runs (FR-016, SC-014, three edge
    cases);
  - a task with a pending proposal is working whatever its last run did (FR-016);
  - a cancel says what it does for each kind of run, for a proposal the stopped run pauses
    on, and for a decision a person already took (FR-021, FR-022a);
  - a conversation takes no message while anything of its thread is in progress, pending, or
    awaited, and a canceled task still counts against the limits meanwhile (FR-010, FR-025);
  - which address a request arrived at is the listener's to say, never the request's
    (FR-002);
  - that a caller started a thread is recorded before its first run, or the message is refused
    (FR-012);
  - Freyja's reply is no boundary for what the caller's own task did, and status updates and
    reasons are (FR-018, SC-008);
  - pieces of a reply are sent only where the text is known to be Freyja's own (FR-017);
  - each refusal has one named answer, and both limits one reason (FR-025, FR-026a);
  - the hand-over count's form, and which message's count is carried on (FR-027).
- Two things are left for the plan and named in the spec so that they are not lost: how a
  run's end by a stop is told from one that was cut off, for runs that are not console runs
  (FR-016); and the protocol's error used for a request the agent does not take (FR-026a).
- The spec changes five earlier requirements, each only for a thread a caller started, and
  lists them at the end of Assumptions. With the feature off, nothing changes (FR-001,
  SC-002).
- Validated again on 2026-11-25 after `/speckit-clarify`: 16 of 16 items still pass. Five
  questions were asked and answered; each is in the spec's Clarifications section. They
  settled candidates 1, 2, 3, and 7 above and what a restart does to a running task, and
  confirmed each as written: every hand-off from a caller's thread waits for a person
  (FR-013a), a named caller has a file area of its own (FR-009a), a send without a stream
  waits at most 30 seconds (FR-011), an ordinary restart cancels a running task and nothing
  continues by itself (FR-016), and the deployment encrypts the way to the outside listener
  (FR-029). No requirement changed its meaning. Candidates 4 to 6 and 8 to 10 were not asked
  about and stay as written.
- The versions of the protocol that ARTEX and heimdall speak were looked up in their
  repositories in the same session, not asked: both serve version 1.0, which is what
  Freyja reaches them with, and neither has a client yet (Assumptions, "Scope").
