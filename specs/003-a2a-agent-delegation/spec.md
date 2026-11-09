# Feature Specification: A2A Agent Delegation

**Feature Branch**: `003-a2a-agent-delegation`

**Created**: 2026-11-09

**Status**: Draft

**Input**: User description: "Delegate work to agents over A2A. Freyja connects at startup to A2A agents named in settings, each by a local address, reads each agent's card, and gives the orchestrator one hand-off tool per agent, built through the one agent factory beside its own tools and the MCP tools. A hand-off sends the agent a self-contained task message written by the orchestrator, plus the hand-off context the orchestrator chooses to include. The agent's reply comes back as the tool's result: text and structured data pass through, and other parts become notes, as for MCP results. The agent's progress streams as update events. Each Freyja thread keeps one conversation per agent, so a follow-up on that thread continues the same conversation with that agent, including after a service restart. When an agent needs more input, the orchestrator asks the user in its own voice, and the user's next message continues that agent's task; the user never talks to an agent directly. A task can outlive the run that started it: when a task is still working at the call time limit, the hand-off returns a note that the task is pending, and the run ends normally. Pending tasks are kept with their thread, listed in the thread state, survive a service restart, and are watched by exactly one service instance at a time. When a pending task finishes, fails, or needs input, Freyja continues the thread on its own with a background run. A background run goes through the same gate, step limit, and run lease as any other run, so a proposal pauses it and the user decides through the existing resume route. A pending task that has not finished within a configured maximum age is given up and reported as an error. The user can ask the orchestrator to cancel a pending task, and the cancel is gated like a hand-off to that agent. Approval follows the 002 rule: a hand-off to an agent the configuration lists as read-only runs without a pause, and every other hand-off is gated by the existing approve, edit, reject interrupt, where an edit changes the task message. This holds on the orchestrator, on its subagents, and in background runs. What an agent does inside its own process is its team's responsibility, not Freyja's. An agent that is unreachable, times out, or fails or rejects a task gives an error result, and the run continues. No hand-off is ever retried, and an agent that cannot be used at startup stops the service, naming the agent. Agents, their skills from the card, and their classification are shown in the catalog. The suite uses an in-process stub A2A agent and needs no network; the quickstart proves one real, published A2A agent that runs locally with no account or key. Out of scope: authentication, agents on other machines, push notifications from an agent to Freyja, telling the user's client that a background run finished or paused (the client reads the thread state), sending files to an agent, agents calling back into Freyja, and routing rules beyond the model's own choice of agent."

## Clarifications

### Session 2026-11-09

- Q: When an agent asks a question and the user answers it, does the message carrying the answer need approval like any other message to an agent not marked read-only? → A: Yes. The pause shows the answer as the orchestrator wrote it, and the user approves, edits, or rejects it. There is no exemption for answers, verbatim or not.
- Q: When a pending task reaches the maximum age and Freyja gives it up, does Freyja also tell the agent to stop? → A: No. Freyja stops watching and sends nothing. The background run that reports the give-up says the agent may still be working and offers a cancel, which the user asks for and which is gated as usual.
- Q: If a background run fails partway, for example on a model provider error, does Freyja try it again on its own? → A: Yes, a configured number of times at a configured interval, then it gives up and logs it. The outcome is recorded in the thread once; a retry continues from the last completed step and never records the outcome again or repeats a message to an agent.
- Q: If the user sends a message after an agent's task finished but before a background run reported the outcome, does the user's run deliver it? → A: Yes. A user's run records at its start every outcome that has arrived and not been delivered, and that counts as delivery. A resume's continuation does not, because it finishes a step that was already under way; a background run delivers what is still undelivered once the thread is free.
- Q: Once an agent task has ended and its outcome was delivered, does the thread state still list it? → A: Yes. The thread state lists every agent task the thread has started: pending ones with their current status, ended ones with their final status and when they ended. Nothing is dropped from the list.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Hand work to an agent and get its answer (Priority: P1)

An operator names a delegate agent in the configuration and marks it read-only. When the service starts, the orchestrator has one hand-off tool for that agent, described from the agent's own card. In a conversation, the orchestrator decides the agent is the right one, writes it a self-contained task message, and adds whatever context from the conversation the agent needs. The run streams the agent's progress, the agent's reply comes back as the hand-off's result, and the orchestrator answers the user in its own voice. A later message on the same thread that needs the same agent continues the same conversation with it, so the agent remembers what it was told before, even after the service restarts.

**Why this priority**: This is the reason for the feature: Freyja can now route work to agents that carry their own reasoning and are owned by other teams. Every other story builds on a connected agent and a working hand-off.

**Independent Test**: Configure one agent marked read-only, start the service, send a message that needs that agent, and read the stream until it closes. The stream shows the hand-off, the agent's progress, and its result; no approval was requested; the reply reflects the agent's answer. Send a follow-up on the same thread, restart the service in between, and confirm the agent received it in the same conversation.

**Acceptance Scenarios**:

1. **Given** a configured agent marked read-only, **When** the service starts, **Then** the orchestrator has one hand-off tool for that agent in its first run, described from the agent's card, and the service log names the agent and the number of skills its card lists.
2. **Given** a running service with such an agent, **When** a user sends a message that the orchestrator answers by handing off to it, **Then** the agent receives one task message written by the orchestrator plus the context the orchestrator chose, the stream shows the hand-off, each progress update from the agent, and the result, no pause event appears, and the reply reflects the result.
3. **Given** the same agent, **When** the orchestrator delegates to a subagent and the subagent hands off to the agent, **Then** the subagent gets the result and the run completes the same way.
4. **Given** an agent that replies with text, structured data, and a part of another kind, such as an image, **When** the hand-off completes, **Then** the text and structured data reach the orchestrator and the other part is replaced by a note naming its kind.
5. **Given** a thread that has already handed off to an agent, **When** a later message on that thread leads to another hand-off to the same agent, including after a service restart, **Then** the agent receives it as part of the same conversation; a hand-off from another thread to the same agent starts a separate conversation.
6. **Given** a hand-off, **When** it is sent, **Then** nothing from the thread reaches the agent except the task message and the context the orchestrator included.

---

### User Story 2 - Every hand-off to an agent not marked read-only waits for a person (Priority: P2)

A hand-off to an agent that the configuration does not list as read-only is treated as effectful: Freyja cannot see or gate what the agent does inside its own process, so the gate sits on the hand-off itself. When the orchestrator proposes one, the run pauses before anything is sent, showing the agent, the task message, and the context. The user approves, edits the message or context, or rejects. The same holds for every message Freyja sends to that agent, including a follow-up, an answer to the agent's question, and a cancel, and it holds on subagents and in background runs.

**Why this priority**: The constitution forbids any environment-changing action without a person's approval and names delegate agents and background runs explicitly. An agent is the most capable kind of delegate, so its hand-off is where that rule matters most.

**Independent Test**: Configure an agent that is not marked read-only, send a message that leads to a hand-off, observe the pause, then on separate threads approve, edit, and reject, checking the stand-in agent received exactly the expected messages.

**Acceptance Scenarios**:

1. **Given** an agent not marked read-only, **When** the orchestrator proposes a hand-off to it, **Then** nothing reaches the agent, the stream ends with the pause event naming the agent and showing the task message and context, and the thread's state shows the pending proposal.
2. **Given** a paused thread, **When** the user approves, **Then** the agent receives exactly one task message, as proposed, and the continuation shows its result.
3. **Given** a paused thread, **When** the user edits the task message or the context and approves, **Then** the agent receives the edited version; an edit that names a different agent, leaves the task message empty, or adds a field a hand-off does not have is refused before anything resumes, with the proposal left in place.
4. **Given** a paused thread, **When** the user rejects with a reason, **Then** the agent receives nothing and the orchestrator continues, telling the user the hand-off was not made and repeating the reason.
5. **Given** an agent whose card describes it as harmless or read-only, **When** the configuration does not list it as read-only, **Then** its hand-offs are still gated: only the operator's configuration decides.
6. **Given** a subagent, or a background run, that proposes a hand-off to an agent not marked read-only, **When** the proposal is reached, **Then** the run pauses the same way and the decision applies to that hand-off.
7. **Given** a step in which the orchestrator proposes a gated hand-off together with other calls, **When** the proposal is reached, **Then** nothing from that step runs until every gated proposal in it has its decision.

---

### User Story 3 - An agent asks for more input (Priority: P3)

An agent sometimes needs more information before it can finish: a date range, a choice between two options, a confirmation. Its question comes back to the orchestrator, which asks the user in its own voice. The user answers in their next message on the thread, and the orchestrator continues that agent's task with the answer. The user only ever talks to Freyja.

**Why this priority**: Agents that reason ask questions; without this, every question ends a task as a failure. It is the part of the feature that keeps one voice toward the user, which the constitution assigns to the orchestrator.

**Independent Test**: Use a stand-in agent that asks one question before it finishes. Hand off to it, confirm the run ends with the orchestrator asking the user, send an answer, and confirm the agent received the answer in the same task and finished.

**Acceptance Scenarios**:

1. **Given** an agent that responds to a hand-off with a question, **When** the hand-off completes, **Then** the orchestrator receives the question as the result, marked as the agent needing input, and the run ends with the orchestrator asking the user.
2. **Given** a thread whose agent task is waiting for input, **When** the user's next message answers it, **Then** the orchestrator continues that same task with a message carrying the answer, gated by the agent's classification, and the agent's eventual result reaches the user. For an agent not marked read-only, the run pauses with the answer as the orchestrator wrote it, and nothing reaches the agent until the user approves or edits it.
3. **Given** a thread whose agent task is waiting for input, **When** the user's next message is about something else, **Then** the task keeps waiting, the thread state still lists it as waiting for input, and the orchestrator can return to it later in the thread.
4. **Given** an agent's question, **When** it reaches the user, **Then** it arrives in the orchestrator's reply, never as a message attributed to the agent.

---

### User Story 4 - Long-running tasks continue after the run ends (Priority: P4)

Some agent tasks take minutes or hours. When a task is still working at the call time limit, the hand-off returns a note that the task is pending and the run ends normally, so the user is not left waiting on an open stream. The thread state lists the pending task. Freyja keeps watching it, and when it finishes, fails, or needs input, Freyja continues the thread on its own with a background run that reports the outcome. The background run follows every rule a user's run follows: a proposal pauses it, and the user decides later through the existing resume route. The user can also ask the orchestrator to cancel a pending task. A task that runs past a configured maximum age is given up and reported as an error.

**Why this priority**: Real delegate work is often slow, and holding a run open for it ties up the thread and the stream. It is ranked after the basic hand-off, the gate, and questions because it builds on all three.

**Independent Test**: Use a stand-in agent whose task finishes only after the call time limit. Hand off, confirm the run ends with a pending note and the thread state lists the task, let the task finish, and confirm a background run adds the outcome to the thread. Repeat with a service restart while the task is pending.

**Acceptance Scenarios**:

1. **Given** an agent task still working at the call time limit, **When** the limit is reached, **Then** the hand-off result is a note that the task is pending, naming the agent, the run continues and ends normally, and the thread state lists the task as pending.
2. **Given** a pending task, **When** it finishes, **Then** Freyja starts one background run on the thread once the thread is free, the orchestrator receives the task's outcome as it would have received the hand-off's result, the reply is added to the thread, and the thread state lists the task as finished, with when it ended.
3. **Given** a pending task, **When** it fails or the agent rejects it, **Then** a background run reports the failure to the user in the thread; the hand-off is not repeated.
4. **Given** a pending task, **When** the agent needs input, **Then** a background run has the orchestrator ask the user, and the flow of User Story 3 continues from the user's next message.
5. **Given** a background run, **When** the orchestrator proposes a gated action in it, **Then** the run pauses with the usual pause, the thread state shows the proposal, and the user's decision through the resume route continues it.
6. **Given** a pending task, **When** the service restarts, **Then** the task is still listed in the thread state and its outcome is still delivered, exactly once.
7. **Given** several service instances sharing the same persistence, **When** a pending task changes, **Then** exactly one instance watches it and its outcome is delivered exactly once.
8. **Given** a pending task older than the configured maximum age, **When** the age is reached, **Then** Freyja stops watching it and sends the agent nothing, a background run reports it to the user as given up, saying the agent may still be working and offering a cancel, and the thread state lists it as given up.
9. **Given** a pending task, **When** the user asks the orchestrator to cancel it, **Then** the cancel is gated as a hand-off to that agent would be; once sent and accepted, the task is listed as canceled and its later outcome is not delivered.
10. **Given** a thread with pending tasks, **When** any run on it starts, **Then** the orchestrator is told which tasks are pending and their last known status.
11. **Given** a task that finished while the thread had no run, **When** the user sends a message before a background run has reported it, **Then** the user's run receives the outcome at its start and can answer with it, and no background run reports it afterwards.

---

### User Story 5 - See which agents Freyja can hand off to (Priority: P5)

A user, an operator, or a tool acting for them reads the catalog and finds every connected agent next to Freyja's own tools and the tool servers' tools: the agent's name, its description and skills from its card, whether its hand-offs are read-only or gated, and whether it was reachable the last time Freyja used it.

**Why this priority**: Choosing which agents to mark read-only, and understanding a pause that names an agent, both need a trustworthy list. The catalog already exists; this story extends it.

**Independent Test**: Start the service with one agent configured, read the catalog, and confirm the agent appears with its card's description and skills and the classification the configuration implies.

**Acceptance Scenarios**:

1. **Given** a running service with configured agents, **When** the catalog is read, **Then** each agent's hand-off tool is listed with the agent's name, the description and skills from its card, and its classification, alongside the tools already listed.
2. **Given** an agent whose most recent use failed because it could not be reached, **When** the catalog is read, **Then** that agent is shown as unavailable and its hand-off tool is still listed with its classification.
3. **Given** an agent whose card claims to be read-only while the configuration does not list it, **When** the catalog is read, **Then** the agent is listed as gated, and the card's claims appear separately, marked as the agent's own.
4. **Given** no agents configured, **When** the catalog is read, **Then** it is exactly what it was before this feature.

---

### User Story 6 - Find out quickly when an agent misbehaves (Priority: P6)

An operator misconfigures an agent, or an agent stops working. Startup refuses to run with an agent it cannot use and says which one. An agent that fails during a conversation produces a clear error result, a log line naming the agent, and an "unavailable" mark in the catalog, instead of a hung run.

**Why this priority**: Agents fail in ways Freyja's own code does not, and a broken agent should look broken, not like a confused orchestrator.

**Independent Test**: Point the configuration at an agent that is not running and confirm startup fails naming it. Then start with a working agent, make it fail a task, stop it during a run, and confirm the error results, the log lines, and the catalog status.

**Acceptance Scenarios**:

1. **Given** a configured agent that cannot be reached, or whose card cannot be read, **When** the service starts, **Then** startup fails with an error naming that agent, and the service does not serve requests.
2. **Given** an agent whose card says it requires authentication, or does not accept text messages, **When** the service starts, **Then** startup fails naming the agent and the reason.
3. **Given** an agent whose hand-off tool would share a name with another tool, **When** the service starts, **Then** startup fails naming the collision.
4. **Given** an agent that fails or rejects a task, or stops responding, during a run, **When** the hand-off is made, **Then** the orchestrator receives an error result naming the agent, the run continues, the service log names the agent, and the thread stays usable.
5. **Given** an agent marked unavailable after a failed hand-off, **When** it is answering again and the orchestrator hands off to it, **Then** the hand-off succeeds without a service restart and the catalog shows it as reachable.

---

### User Story 7 - Bring a real agent up locally (Priority: P7)

A developer follows the README to run one real, published agent on their machine, configures it, starts Freyja, and completes a hand-off that runs without a pause and one that pauses and is approved. The automated suite proves all of the above against a stand-in agent inside the test process, so it still needs no network.

**Why this priority**: The feature is only real once it works with an agent Freyja did not write. It is last because the automated tests do not depend on it.

**Independent Test**: From a clean clone, follow the README only: run the documented agent, configure it, start the service, and complete a read-only hand-off and an approved gated hand-off.

**Acceptance Scenarios**:

1. **Given** a clean clone and the README, **When** the developer runs the documented agent, configures it as read-only, and starts the service, **Then** the catalog lists the agent and a run that hands off to it completes.
2. **Given** the same agent configured without the read-only mark, **When** the developer triggers a hand-off, **Then** the run pauses, approval sends it, and the agent's answer reaches the reply.
3. **Given** the automated suite, **When** it runs without a network and without any external agent installed, **Then** every behavior in this feature is covered by tests against an in-process stand-in agent.

---

### Edge Cases

- **An agent's card advertises an address other than the configured one**: Freyja uses only the configured local address and never follows the card's address; an agent at a local address that redirects to another host is not followed there.
- **An agent's card lists no skills**: startup succeeds, the catalog shows the agent with an empty skill list, and the orchestrator still has its hand-off tool.
- **The agent does not acknowledge a hand-off within the call time limit**: the hand-off fails with an error result naming the limit and saying the agent may or may not have received the task; it is not tried again and nothing becomes pending, because Freyja has no task to watch.
- **The agent acknowledged the hand-off and is still working at the limit**: the task becomes pending (User Story 4); this is not an error.
- **The thread is busy when a pending task changes**: a user's run is in progress, or a proposal is waiting for a decision. The background run waits until the run has ended and no proposal is pending, then delivers the outcome. A user's message that arrives while a background run holds the thread is refused the same way a second concurrent run is refused today.
- **A user's run starts after a task finished but before its outcome was delivered**: the user's run records the outcome at its start and the orchestrator can answer with it; no background run delivers it afterwards. Whichever run records an outcome first delivers it, and a run that finds it already recorded skips it.
- **Several pending tasks on one thread change at once**: each outcome is delivered, in the order the changes were observed; one background run may deliver several outcomes together.
- **Several hand-offs to the same agent from one thread**: they belong to the same conversation; each task is tracked and listed separately, and an answer or a cancel names the task it is for.
- **A subagent's hand-off becomes pending**: the subagent has ended by the time the outcome arrives, so the background run delivers it to the orchestrator of that thread.
- **A pending task's agent stops responding while Freyja watches it**: Freyja keeps checking until the task changes or reaches the maximum age, and the catalog shows the agent as unavailable meanwhile. Checking a task's status sends the agent no new work and is not a retry.
- **A pending task's agent is removed from the configuration**: after the restart that applies the change, the task is given up and reported as such by a background run.
- **A cancel is sent for a task that has already finished**: the cancel reports that the task had already ended, and the outcome is delivered as usual.
- **A cancel is rejected or the agent cannot be reached**: the orchestrator is told, the task stays pending, and nothing is retried.
- **A proposal is pending when the service restarts with a different configuration**: the decision is applied under the configuration in force, with the same rules feature 002 applies to tool server tools: a hand-off to an agent that was removed is never sent, a hand-off to an agent that is now read-only accepts approve only, and a step that holds a hand-off that has since become gated cannot be resumed.
- **A background run reaches the step limit**: it ends with the step limit error like any run; the task's outcome counts as delivered, because it is in the thread.
- **A background run fails for another reason**, such as a model provider error or an internal error: the failure is logged, and the service tries the background run again after the configured interval, up to the configured number of attempts. A retry continues from the last completed step, so the outcome is not recorded twice and no message already sent to an agent is sent again. Retries stop early once another run on the thread has completed, because that run already had the outcome. After the last attempt fails, the service logs that it gave up, the outcome stays in the thread, and the next run on the thread sees it.
- **The service restarts between background run attempts**: the remaining attempts resume after the restart, under the same exactly-once rule.
- **An agent's reply is large or arrives as many progress updates**: it passes through unchanged; size limits are out of scope for this increment.
- **Configuration changes while the service runs**: not picked up until restart, as for tool servers.

## Requirements *(mandatory)*

### Functional Requirements

**Configuration and startup**

- **FR-001**: Operators MUST be able to name one or more delegate agents in the service configuration, each with a unique name, a local address, and whether its hand-offs are read-only. An agent is not read-only unless the configuration says so. An address that does not refer to the local machine MUST be refused, under the same rule feature 002 applies to tool servers. Everything about an agent MUST come from configuration; nothing MAY be hardcoded.
- **FR-002**: When the service starts, it MUST read each configured agent's card from its configured address and give the orchestrator and its subagents one hand-off tool per agent, through the one construction path the service already has, beside Freyja's own tools and the tool server tools. The hand-off tool MUST be described from the card. Agents and their cards MUST be read once at startup; configuration changes take effect on restart. Freyja MUST reach an agent only at its configured address, never at an address taken from the card or from a redirect to another host.
- **FR-003**: Startup MUST fail with an error naming the agent in each of these cases: the agent cannot be reached or its card cannot be read; an agent entry lacks an address, gives an address that is not local, or has a setting the configuration does not define; an agent's name is not in the allowed form; the card says the agent requires authentication or does not accept text messages; or the agent's hand-off tool would share a name with any other tool. The service MUST NOT serve requests in that state.
- **FR-004**: Every new configurable value MUST be read through settings from the project's environment-variable prefix, MUST have a safe local default, and MUST be documented in the example configuration.

**The hand-off**

- **FR-005**: A hand-off MUST send the agent one task message written by the orchestrator, plus the hand-off context the orchestrator chooses to include. Nothing else from the thread, such as the transcript, earlier tool results, or files in the workspace, MAY reach the agent unless the orchestrator puts it in the task message or the context.
- **FR-006**: The agent's reply MUST reach the orchestrator as the hand-off's result: its text and structured data pass through, and each part of another kind MUST be replaced by a short note naming its kind and MUST NOT be sent to the model or stored in the thread. A reply with such parts is a normal result.
- **FR-007**: While a hand-off is in progress within a run, each progress update the agent sends MUST appear in the run's stream as a progress event, in the order received, before the hand-off's result.
- **FR-008**: Each thread MUST keep one conversation per agent. Every hand-off from that thread to that agent, whether made by the orchestrator, a subagent, or a background run, MUST continue that conversation, and the conversation MUST survive a service restart. Two threads MUST NOT share a conversation with an agent.
- **FR-009**: A hand-off to an agent that cannot be reached, does not acknowledge the task within the call time limit, fails the task, or rejects it MUST become an error result naming the agent, MUST be logged with the agent's name, and MUST NOT end the run or affect the thread's run lease. The error result of an unacknowledged hand-off MUST say that the agent may or may not have received the task. A hand-off to an agent whose connection was lost MUST try to connect again within the same time limit, so an agent that is answering again is usable without a restart.
- **FR-010**: The service MUST NOT repeat a hand-off, an answer, or a cancel that failed or timed out: an approval authorizes one attempt, and another attempt is a new proposal. Checking the status of a pending task is not a repeat.

**Classification and the gate**

- **FR-011**: Every message Freyja sends to an agent, whether a new hand-off, a follow-up, an answer to the agent's question, or a cancel, MUST be classified by a deterministic rule that uses configuration alone. It is read-only only if the configuration lists the agent as read-only; otherwise it is effectful. What the agent's card says about itself and the model's own judgment MUST NOT influence the classification.
- **FR-012**: Every effectful message to an agent MUST be gated by the existing approval interrupt with approve, edit, and reject decisions, on the orchestrator, on every subagent, and in background runs. No code path MAY send one without an approve or edit decision submitted through the resume operation for that specific proposal while it is pending in the thread's state. When one step proposes several calls, no call from that step MAY run until every effectful proposal in it has its decision.
- **FR-013**: The pause event and the pending proposal in thread state MUST name the agent, and MUST show the task message and the context. An edit MAY change only the task message and the context; the agent, and for an answer or a cancel the task it is for, stay those of the proposal. An edit that leaves the task message empty or adds a field a hand-off does not have MUST be refused, and a refused edit MUST leave the proposal in place.
- **FR-014**: A decision on a proposal MUST be applied under the configuration in force when the decision arrives, with the rules feature 002 set for tool server tools: a message to an agent that was removed MUST NOT be sent, whatever the decision; a message to an agent that is now read-only accepts approve only; and if a call in the paused step has become effectful since the pause, the resume MUST fail, nothing from the step MAY run, and the proposal MUST stay in place.

**An agent that needs input**

- **FR-015**: When an agent's task needs more input, the orchestrator MUST receive the agent's question as the result, marked as needing input, and MUST put the question to the user in its own reply. The agent's question MUST NOT be presented to the user as the agent's own message.
- **FR-016**: The orchestrator MUST be able to continue a task that is waiting for input with a message carrying the user's answer, gated per FR-011. A task waiting for input MUST stay listed in the thread state as waiting until it is answered, canceled, or given up.

**Pending tasks and background runs**

- **FR-017**: When an agent's task is still working at the call time limit, the hand-off MUST return a note that the task is pending, naming the agent, and the run MUST continue. The thread state MUST list every agent task the thread has started, pending or ended, with its agent, the task message it was started with, its status, when it started, when it was last checked, and, once it has ended, its final status and when it ended. An ended task MUST stay listed; nothing is dropped from the list. The list MUST survive a service restart.
- **FR-018**: At the start of every run on a thread, the orchestrator MUST be told which of the thread's tasks are pending and their last known status. A user's run and a background run MUST also record, at their start, every outcome that has arrived and not yet been delivered, which counts as its delivery. A resume's continuation MUST NOT record outcomes, because it finishes a step already under way. A background run is needed only for an outcome still undelivered when the thread is free.
- **FR-019**: The service MUST watch each pending task until it finishes, fails, is rejected, needs input, is canceled, or reaches the maximum age. Each pending task MUST be watched by exactly one service instance at a time, including when several instances share the same persistence, and watching MUST resume after a restart. Watching MUST only read the task's status and MUST NOT send the agent new work.
- **FR-020**: When a watched task finishes, fails, is rejected, needs input, or is given up, the service MUST continue the thread with a background run that gives the orchestrator the outcome as it would have received the hand-off's result. The background run MUST start only when the thread has no run in progress and no pending proposal, MUST go through the same gate, step limit, and run lease as any other run, and MUST be persisted like any other run, so a proposal in it pauses it and the existing resume route decides it. Each outcome MUST be delivered into the conversation exactly once: it counts as delivered once a run, this background run or a user's run (FR-018), has recorded it in the thread.
- **FR-027**: A background run that fails with an error other than the step limit MUST be tried again after a configured interval, up to a configured number of attempts, both with safe local defaults. Each retry MUST continue from the last completed step, MUST NOT record the outcome again, and MUST NOT repeat a message already sent to an agent (FR-010). Retries MUST obey FR-020's rule for a free thread, MUST survive a restart, and MUST stop once another run on the thread has completed. When the last attempt fails, the service MUST log that it gave up, naming the thread and the task; the outcome stays in the thread for the next run. A background run that ends with the step limit or pauses for a decision is not a failure and is not retried.
- **FR-021**: The maximum age of a pending task MUST be configurable, with a safe local default. A task that reaches it MUST be given up: the service stops watching it, a background run reports it to the user as given up, and the thread state lists it as given up. The service MUST NOT send the agent a cancel on its own when it gives a task up, and the background run MUST NOT propose one on its own either. The report MUST say that the agent may still be working on the task and that the user can ask for a cancel, which FR-022 then handles.
- **FR-022**: The orchestrator MUST be able to cancel a pending task, or one that was given up, when the user asks, with a message gated per FR-011. A canceled task MUST be listed as canceled, and an outcome that arrives for it afterwards MUST NOT be delivered. A cancel for a task that had already ended at the agent MUST report that.
- **FR-023**: After a restart, a pending task whose agent is no longer configured MUST be given up and reported by a background run.

**Catalog**

- **FR-024**: The catalog MUST list each agent's hand-off tool with the agent's name, its classification, the description and skills from its card, and whether the agent is reachable, as the outcome of the most recent attempt to use it, at startup, in a hand-off, or while watching a task. What the card says MUST be presented as the agent's own claim, separate from the classification. The catalog MUST NOT show an agent's address, and reading the catalog MUST NOT contact an agent. With no agents configured, the catalog MUST be what it was before this feature.

**Local development, documentation, and quality**

- **FR-025**: The README MUST document how to configure an agent and its read-only mark, what a pending task is and where the thread state lists a thread's agent tasks, how to read agents in the catalog, and how to run the suite, and MUST name one real, published agent that runs locally with no account or key.
- **FR-026**: Automated tests MUST cover: agents and their cards read at startup; a read-only hand-off completing without a pause, directly and through a subagent; a gated hand-off pausing, directly, through a subagent, and in a background run; approve, edit, and reject reaching the agent as specified, including a refused edit; a follow-up, an answer, and a cancel each classified like a hand-off; the card's claims ignored for gating and shown in the catalog as the agent's; only the task message and chosen context reaching the agent; progress updates in the stream; a reply with a part of another kind delivered as text plus a note; one conversation per thread and agent, across a restart, never shared between threads; an agent asking for input and the answer continuing the same task; a task becoming pending at the time limit; a pending task's outcome delivered exactly once by a background run, including across a restart and with two instances sharing persistence; a background run waiting for a busy thread; a user's run delivering an outcome that arrived before it, with no background run delivering it again; a failed background run tried again up to the configured number of attempts without recording the outcome twice or repeating a message to an agent, stopping early once another run completed, and logged when it gives up; a task given up at the maximum age without a cancel being sent; a canceled task's later outcome not delivered; each startup failure in FR-003; an unreachable, failing, rejecting, or unacknowledging agent giving an error result that is not repeated; an agent answering again after it was unavailable; a decision on a pending proposal under a changed configuration in each case of FR-014; the card's address and a redirect to another host not being followed; and the catalog's agent entries. The suite MUST run without a network and without any external agent installed, using a stand-in agent inside the test process.

### Key Entities

- **Delegate Agent**: an external agent with its own reasoning, owned by another team, running on the same machine and reached at a configured local address. Has a configured name, a read-only mark, a card, and a reachability status as of the most recent attempt to use it.
- **Agent Card**: what an agent publishes about itself: its name, description, skills, and what it accepts. Informational for the catalog and the orchestrator; never an input to classification.
- **Hand-off**: one task message, plus optional context, sent by the orchestrator, a subagent, or a background run to one agent through that agent's hand-off tool. Read-only or effectful by the agent's configuration.
- **Agent Conversation**: the continuing exchange between one thread and one agent. One per thread and agent; survives restarts.
- **Agent Task**: the work an agent carries out for a hand-off. Has a status: working, waiting for input, finished, failed, rejected, canceled, or given up. Every Agent Task a thread starts stays listed in its state, with its final status and end time once it ends.
- **Pending Task**: an Agent Task that outlived the run that started it. Kept with its thread and listed in its state with agent, initial task message, status, start time, and last check; watched by one service instance at a time until it ends or reaches the maximum age.
- **Background Run**: a run the service starts on a thread, without a user message, to deliver a Pending Task's outcome that no other run has recorded. Subject to the same gate, step limit, run lease, and persistence as any run. A failed one is tried again a configured number of times.
- **Catalog**: extended with an entry per agent's hand-off tool: agent name, classification, card description and skills as the agent's claims, and reachability.
- **Proposal**: an existing entity; for a hand-off it names the agent and shows the task message and context.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the automated suite, 100% of messages to agents not marked read-only pause for a decision, whether proposed by the orchestrator, a subagent, or a background run, and whether a hand-off, a follow-up, an answer, or a cancel. Measured by the stand-in agent's record of what it received, zero such messages reach an agent without an approve or edit decision submitted for that proposal.
- **SC-002**: 100% of hand-offs to agents marked read-only in the suite complete without a pause, directly and through a subagent.
- **SC-003**: Every pending task's outcome is delivered into its thread exactly once in 100% of automated runs, including runs with a service restart in between and runs with two service instances sharing persistence, and within 1 minute of the task changing while the thread is free.
- **SC-004**: A follow-up on the same thread reaches the same agent conversation in 100% of automated runs, including across a restart, and no conversation is ever shared between two threads.
- **SC-005**: Startup with an unreachable, misdescribed, or colliding agent configuration fails within 30 seconds with an error naming the agent, in 100% of automated runs, and the process serves no requests.
- **SC-006**: A failing, rejecting, unreachable, or unacknowledging agent ends in an error result and the run continues, in 100% of automated runs; no such hand-off leaves a thread refused or paused, and none is repeated.
- **SC-007**: The catalog read on a freshly started service lists every configured agent with the classification the configuration implies, in 100% of automated runs, and responds within 1 second.
- **SC-008**: A developer following only the README goes from a clean clone to a completed read-only hand-off and an approved gated hand-off with a real, published local agent in under 20 minutes, without editing any credential.
- **SC-009**: The lint, format, and test gates pass on the merged change, and the suite completes with no network access and no external agent installed.

## Assumptions

**Scope**

- Out of scope, per the description: authentication in either direction, agents on other machines, push notifications from an agent to Freyja, telling the user's client that a background run finished or paused (the client reads the thread state), sending files to an agent, agents calling back into Freyja, and routing rules beyond the model's own choice of agent. Also out of scope for this increment: reloading configuration without a restart, limits on the size of an agent's reply, per-user or per-thread differences in which agents are available, and an "always allow" memory for an agent.
- Agents run on the same machine as the service and are started by the operator before the service; Freyja never launches an agent. An agent that is not running at startup is a startup failure. The local-address rule, including trusting the local machine's own name resolution, is the one feature 002 set for tool servers.
- Subagents receive the same hand-off tools as the orchestrator, with the same classification.
- What an agent does inside its own process, including any tools it calls, is its team's responsibility. Freyja gates what it sends to the agent, not what the agent does with it.

**Behavior choices**

- The gate sits on every message Freyja sends to an agent that is not marked read-only, including the message that carries the user's own answer to the agent's question. Approving the answer is one more step for the user, but the orchestrator writes that message, and a rule that let some messages through would need a judgment the constitution keeps out of the gate. Marking an agent read-only is how an operator removes the step for an agent they trust. Confirmed in clarification on 2026-11-09; forwarding the user's reply verbatim without a pause was considered and not adopted.
- Delivering a background run's outcome waits for a free thread rather than interrupting a run or a pending decision, so a person's decision is never overtaken by a later event.
- Giving a task up at the maximum age does not cancel it at the agent, because a cancel is a message to the agent and is gated; the report offers one and the user can ask for it afterwards. The task may therefore keep running at the agent after Freyja stops watching it. Confirmed in clarification on 2026-11-09: sending a cancel without approval, and proposing one at once, which would hold the thread paused until it is decided, were considered and not adopted.
- Watching a pending task means checking its status, at an interval the plan sets, so that SC-003's one-minute bound holds. Push notifications, where the agent would call Freyja, are out of scope.
- The call time limit for a hand-off is a single value for every agent in this increment, with a default in the tens of seconds. The maximum age of a pending task defaults to a value in the order of a day.
- A task's outcome that a background run has put into the thread counts as delivered, even if that run then ends with an error such as the step limit. A background run that fails for another reason is tried again (FR-027), with defaults of a few attempts minutes apart; the plan sets the values. Confirmed in clarification on 2026-11-09: no retry, relying on the next run on the thread, was considered and not adopted, because it leaves a thread silent until someone writes to it.
- Trust, stated in one place. The operator and the configuration are trusted: the read-only mark is the operator's decision. The model is not trusted to decide what needs approval. An agent is not trusted with effects, which is why hand-offs to it are gated, and the text it supplies, namely its card, its progress updates, its questions, and its replies, is untrusted text that reaches the model and, in a pause, the approver. This increment accepts that risk, as feature 002 did for tool servers: the protection is the gate, which shows the approver the agent's name and the exact message before anything is sent.
- Marking an agent read-only means its hand-offs are not reviewed, so whatever the orchestrator writes in the task message and context reaches the agent unseen. The operator accepts that for each agent marked read-only.

**Environment and dependencies**

- The existing approval gate, thread state, run lease, step limit, resume route, catalog, and the result handling feature 002 introduced are reused. This feature extends the thread state with the thread's agent tasks, extends the catalog with agents, adds hand-off tools to the one construction path, and adds a way for the service to start a run without a user message.
- Several service instances may share one persistence store; the run lease already keeps two runs off one thread, and the same store is where a pending task is claimed by one instance.
- A real, published A2A agent exists that runs locally with no account or key and can be configured both with and without the read-only mark; the plan names it, or reports that none exists before implementation starts. The automated suite uses a stand-in inside the test process.
- The service keeps binding to the local interface with no authentication, so anyone who can reach the port can read a thread's agent tasks and approve proposals; that remains acceptable for local development and is documented.
