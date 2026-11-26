# Feature Specification: Auto Mode for Agents

**Feature Branch**: `010-agent-auto-mode`

**Created**: 2026-11-26

**Status**: Implemented

**Input**: User description: "Add an auto mode to agents. In the UI, it will be both in Agent cards in Agents section and a dropdown (contains Manual and Auto) in Tasks section of the sidebar."

## Clarifications

### Session 2026-11-26

- Q: What does the Manual/Auto dropdown in the sidebar's Tasks section do, beside each agent's own Manual/Auto? → A: It is a master switch for the instance. Manual: every message to every agent asks a person, whatever the agents' own modes say. Auto: an agent whose own mode is Auto runs without asking; the others still ask. Turning the switch to Auto skips approval for no agent that nobody set to Auto. An override that makes every agent run without asking, and a mode chosen per task, were considered and not adopted.
- Q: What does Auto cover? → A: Messages to agents only: hand-offs, answers, follow-ups, and cancels. A tool server's tool and `send_notification` keep asking as today. Auto also applies to tasks another agent started over A2A (feature 009): there, too, an agent whose mode is Auto runs without asking while the switch is Auto. This amends 009's FR-013a, which made every message to an agent from such a task ask, whatever the agent's choice.
- Q: Which steps of Spec Kit does this feature run, and in how many pull requests? → A: The owner asked for one pull request holding the spec, the plan with its Constitution Check, the tasks, and the implementation. The project's rule of one pull request per step, each reviewed before the next, is set aside for this feature at the owner's request; the clarifications above stand in for `speckit-clarify`.
- Q (decided in the plan, the owner's to overturn): What does the switch hold where nobody set it? → A: Auto. Every agent's own mode defaults to Manual (a saved agent requires approval unless saved otherwise, an environment agent unless its entry says `read_only: true`), so with the switch on Auto nothing runs without asking that an operator did not set to run so, and a deployment configured before this feature behaves as it did, apart from the tasks other agents start (FR-007).

## User Scenarios & Testing *(mandatory)*

Terms. An agent's **own mode** is the approval choice features 003 and 005 gave it, now named Manual or Auto: a saved agent's `approval` (`required` is Manual, `none` is Auto) and an environment agent's `read_only` (`true` is Auto). The **switch** is the one Manual/Auto value of the whole instance, shown in the sidebar's Tasks section. A message **runs without asking** when it is sent with no person's decision.

### User Story 1 - Stop every agent from running without asking (Priority: P1)

An operator sees something they do not like, or hands the console to someone new, and wants every message to every agent to wait for a person for a while, without changing any agent. They open the dropdown under Tasks in the sidebar and choose Manual. From then on, on every instance, every message to every agent waits for a decision, also in runs already in progress. Choosing Auto again brings back each agent's own mode.

**Why this priority**: It is the one control that makes the agents safe at once, and it is what makes an Auto mode acceptable: a person can always take it back in one step.

**Independent Test**: Save an agent with Auto. With the switch on Auto, a hand-off to it is sent without a pause. Choose Manual: the next hand-off to it pauses; a run that was in progress when the switch moved, and that proposes a hand-off to it afterwards, is sent nothing and is told that the agent now requires approval. Choose Auto: the next hand-off is sent without a pause again.

**Acceptance Scenarios**:

1. **Given** the switch on Auto and an agent whose own mode is Auto, **When** the switch is set to Manual, **Then** every later message to that agent, and to every other agent, waits for a person's decision, on every instance.
2. **Given** a run in progress whose orchestrator was built while the switch was Auto, **When** the switch is set to Manual and the run then proposes a message to an Auto agent, **Then** nothing is sent, the call's result says that the agent now requires approval, and the run continues.
3. **Given** a paused step that holds, beside a call that waits for a decision, a message to an Auto agent proposed while the switch was Auto, **When** the switch is set to Manual and the proposal is decided, **Then** the resume ends with `approval_now_required` naming the agent, nothing of the step runs, and the proposal stays.
4. **Given** the switch on Manual, **When** it is set to Auto, **Then** the console asks for confirmation first, saying how many agents are set to Auto, and agents whose own mode is Auto run without asking from the next run.
5. **Given** the switch on Manual, **When** the console is opened on any page, **Then** the sidebar shows Manual under Tasks.

---

### User Story 2 - Set one agent to Auto from its card (Priority: P2)

An operator trusts one agent and wants Freyja to hand it work without asking. On the agent's card in the Agents section they switch it from Manual to Auto and confirm. While the switch is Auto, messages to that agent run without asking; the card of an Auto agent says when the switch holds it to Manual.

**Why this priority**: The per-agent mode existed as an approval choice in the agent's form; this story puts it on the card, in the words the switch uses.

**Independent Test**: Save an agent, which is Manual. On its card switch it to Auto and confirm: the service holds it as running without approval, and a hand-off to it is sent without a pause. Switch it back to Manual: the next hand-off pauses. An environment agent's card shows its mode and offers no control.

**Acceptance Scenarios**:

1. **Given** a saved agent in use, **When** the operator switches its card to Auto, **Then** the console asks for confirmation, and on confirming changes the agent at the version the card read, as a change of its approval choice does today.
2. **Given** a saved agent changed since the card was read, **When** the operator confirms, **Then** the service refuses with `agent_changed`, nothing changes, and the list is read again.
3. **Given** an environment agent, **When** its card is shown, **Then** it shows Manual or Auto from its entry and offers no control to change it.
4. **Given** the switch on Manual, **When** an Auto agent's card is shown, **Then** the card says that the switch holds it to Manual.

---

### User Story 3 - Tasks another agent started follow the same rule (Priority: P3)

Another agent hands Freyja a task over A2A (feature 009), and Freyja's work on it leads to a hand-off to one of Freyja's own agents. The hand-off follows the same rule as on a task a person started: it runs without asking when the switch is Auto and the agent's own mode is Auto, and waits for a person otherwise.

**Why this priority**: The owner chose one rule for every task. It changes what feature 009 promised, so it is stated, tested, and documented on its own.

**Independent Test**: With feature 009 on, a caller's task leads to a hand-off to an Auto agent: with the switch on Auto it is sent without a pause; with the switch on Manual, or with the agent on Manual, it pauses for a person in the console.

**Acceptance Scenarios**:

1. **Given** feature 009 on, the switch on Auto, and an Auto agent, **When** a caller's task leads to a message to that agent, **Then** it is sent without a person's decision.
2. **Given** feature 009 on and the switch on Manual, **When** a caller's task leads to a message to any agent, **Then** it waits for a person's decision.
3. **Given** a caller's task, **When** it leads to a call of a tool server's tool that is not marked read-only, or to `send_notification`, **Then** it waits for a person's decision, whatever the switch says.

### Edge Cases

- **The switch is set to Auto while a proposal to an Auto agent waits for a decision.** The proposal then accepts approve only and is sent as proposed once approved, as for an agent switched to run without approval today (005, Edge Cases). The confirmation says so.
- **The switch is changed on one instance while another runs.** Every instance reads the switch with the saved agents' revision, and every agent tool reads it again before it sends a message no person decided, so Manual holds at once everywhere.
- **The switch cannot be read.** It is read with the saved agents, so no run starts, as when the saved agents cannot be read; an agent tool that cannot read it before a send sends nothing, and its step fails. The list of agents answers with an error, and the console's dropdown says that the switch is not known.
- **A loop between Freyja and an Auto agent that hands work back to Freyja.** On a caller's task it can now turn without a person while the switch is Auto (009, Edge Cases). Until 009's hand-over count is built (009 User Story 5), it is bounded only by the limits on tasks in progress. Setting the switch to Manual stops it at its next message.
- **An older build runs beside this one during a rollout.** It does not know the switch and keeps each agent's own mode, as before.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The service MUST keep one switch for the instance, Manual or Auto, as operator-managed configuration in its database, the same for every instance, and changed only through its API. A database where nobody set it holds Auto.
- **FR-002**: Every agent MUST keep its own mode, Manual or Auto, as the approval choice of features 003 and 005: a saved agent's `approval` and an environment entry's `read_only`. Nothing else, such as what an agent's card says, decides it.
- **FR-003**: A message to an agent, whether a hand-off, a follow-up, an answer, or a cancel, MUST run without a person's decision only when the switch is Auto and the agent's own mode is Auto, on the orchestrator and its subagent, in background runs, and on every thread, a caller's thread included. Otherwise it MUST pause for a decision.
- **FR-004**: Setting the switch to Manual MUST hold at once, on every instance, also for runs in progress and for a step that runs again: a message no person decided MUST NOT be sent to any agent, and the call's result says that the agent now requires approval. Setting it to Auto holds from the next run.
- **FR-005**: The service MUST return the switch with the list of agents (`GET /agents`, `mode`) and change it with `PUT /agents/mode`, whose body holds `mode` alone, `manual` or `auto`. The route contacts no agent, refuses a request a browser marks as another origin's, answers with the switch as kept, and logs each change.
- **FR-006**: A tool server's tool and `send_notification` MUST keep their classification whatever the switch says.
- **FR-007**: A caller's thread (feature 009) MUST follow FR-003 like any other. 009's FR-013a is amended to say so.
- **FR-008**: The sidebar's Tasks section MUST show the switch as a dropdown with Manual and Auto. Choosing Auto MUST ask for confirmation, saying how many agents are set to Auto and that each of them will be sent messages with no person seeing them first; choosing Manual MUST apply at once. While the list of agents cannot be read, the dropdown MUST say that the switch is not known and MUST NOT be changeable.
- **FR-009**: Each agent's card MUST show its own mode as Manual or Auto. A saved agent in use MUST offer a control that switches it, behind a confirmation that says what the new mode means, sent at the version the card read. An environment agent, and a saved agent not in use, MUST show the mode without a control. When the switch is Manual, an Auto agent's card MUST say that the switch holds it to Manual.
- **FR-010**: 007's FR-017 is amended: the console's one way to stop being asked is the operator's choice of FR-008 and FR-009, made apart from any proposal. Nothing in a proposal's card approves later proposals or stops the asking.

### Key Entities

- **Switch**: the instance's Manual/Auto value, kept beside the saved agents' revision; changing it moves the revision, so every instance builds its orchestrator again.
- **Agent's own mode**: the existing approval choice, shown as Manual or Auto.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After the switch is set to Manual, no message to any agent is sent without a person's decision, on any instance, in any run, from the moment the change is answered.
- **SC-002**: With the switch on Auto, a message to an Auto agent is sent with no pause, and to a Manual agent never without a decision.
- **SC-003**: An operator changes the switch, or an agent's own mode, in at most two presses and one confirmation, from any page of the console for the switch and from the agent's card for its mode.

## Assumptions

- The console has no users (feature 007): anyone who can reach the service's port can change the switch and the agents' modes, as they can change an agent's approval choice today. This is unchanged and documented.
- The words Manual and Auto replace "Requires approval" and "Runs without approval" in the console; the API keeps `approval: required | none` and `read_only`, so no client breaks.
