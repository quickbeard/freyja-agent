# Specification Quality Checklist: Delegate Agents on Other Machines, with a Credential

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

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
- Validated on 2026-11-19: 16 of 16 items pass, with the readings below.
- "No implementation details" is read as in features 003 to 005. The spec names things an
  operator or a deployer sees and sets: `FREYJA_A2A_AGENTS`, a host, a bearer token, the
  authorization header a token travels in, a card. It names no module, library, table, or
  route.
- No `[NEEDS CLARIFICATION]` marker was left. Where the description was silent, the spec makes
  a choice and records it under Assumptions, "Behavior choices". These are the ones a reader
  is most likely to want to decide differently, and the first candidates for
  `/speckit-clarify`:
  1. An allowed host may be listed with a port or without; without one, every port of the
     host is accepted (FR-001). Requiring a port on every entry is the stricter choice.
  2. A stored token is discarded when its agent moves to another scheme, host, or port, so
     the operator enters it again (FR-011).
  3. A remote address without encryption is accepted for an agent with a token, with a warning
     and a confirmation, and no setting to forbid it (FR-019).
  4. A token is sent only to an agent whose card asks for one, and the card is read without
     it, so an agent that protects even its card cannot be used (FR-013, FR-016).
  5. Allowed hosts are exact names or IP addresses, compared as written, with no pattern and
     no lookup; a listed name that resolves to this machine or to an address the deployment
     did not intend is not detected (FR-001, FR-002, Edge Cases).
  6. Saving and checking an agent do not tell whether the agent accepts its token; that is
     learned at the first request that carries it (FR-025).
  7. A replaced or removed token applies to runs that start afterwards, not to a run in
     progress (FR-010), unlike a tightened approval choice in feature 005.
  8. The last four characters of an environment agent's token are shown in the Agents section,
     which has no login (FR-020), as feature 004 does for the environment's model key.
  9. The token is also removed from an agent's own replies and progress updates (FR-021),
     where feature 005 leaves an agent's replies unsearched for its address.
  10. A saved agent whose host is no longer allowed is listed as not in use and its pending
      tasks are given up (FR-004), as feature 005 does for a saved agent the environment
      overrides.
- The draft was read by an independent reviewer before it was committed. Its two most serious
  findings changed the design: a stored token could be read back by moving its agent to a
  listener of one's own (now FR-011), and the list of allowed hosts said nothing about ports
  or about other spellings of a host (now FR-001 and FR-002).
- The spec is written as if feature 005 were complete, as the description asks. It refers to
  005's requirements by number and replaces earlier requirements of 003 and 005, listed at
  the end of Assumptions. It is to be read again before planning if 005 is finished differently.
- Validated again on 2026-11-19 after `/speckit-clarify`: 16 of 16 items still pass. Five
  questions were asked and answered; each is in the spec's Clarifications section. They
  settled candidates 1, 3, 4, 5, and 6 above, and changed two of them: an allowed host now
  always names its port (candidate 1, FR-001, FR-002), and a destination that is a loopback,
  unspecified, or link-local address is refused each time the service connects (candidate 5,
  new FR-005a, SC-014). The other three were confirmed as written. Candidates 2 and 7 to 10
  were not asked about and stay as written.
