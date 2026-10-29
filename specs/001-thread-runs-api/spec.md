# Feature Specification: Thread Runs API

**Feature Branch**: `001-thread-runs-api`

**Created**: 2026-10-28

**Status**: Draft

**Input**: User description: "Thread runs API: the minimal Freyja runtime. A Deep Agents orchestrator is built by one factory (build_agent) using the model from Settings, a Postgres checkpointer and store opened in the FastAPI lifespan, a FilesystemBackend rooted at workspace/, and skills loaded from workspace/skills/. POST /threads/{thread_id}/runs accepts a message and streams the run over SSE (updates and messages, with an optional debug flag). GET /threads/{thread_id}/state returns the thread's current state. One placeholder environment-changing tool (isolate_host, which only logs) is gated by an interrupt with approve, edit, and reject decisions; POST /threads/{thread_id}/resume submits a decision and continues the same thread, including after a server restart. Local Postgres comes from a docker-compose file whose credentials match .env.example. Out of scope: delegate agents, authentication, real SOC tools, the CLI, and multiple models."

## Clarifications

### Session 2026-10-29

- Project direction: Freyja is a general-purpose agent orchestrator, not a security operations product. Domain wording below was generalized accordingly, and the placeholder gated action is `send_notification(channel, message)`. The Input line above is kept verbatim as the original request.

### Session 2026-10-28

- Q: When a thread's state is read, should the conversation include every message, meaning the orchestrator's tool calls and tool results as well as human and assistant text, or only the human and assistant text? → A: Full transcript: human, assistant, tool-call, and tool-result messages, in order.
- Q: Should a single run have a maximum number of orchestrator steps, and when that limit is reached, should the run end with an error event rather than continue? → A: Yes: a step limit read from settings with a local default; on reaching it the run ends with an error event naming the limit, and the thread stays usable.
- Q: Is this increment allowed to assume a single running service instance, so the one-active-run rule can be enforced in the process's memory and a crash clears it, or must the rule hold across several instances sharing one database? → A: Multi-instance from the start: durable lock in the database with an expiry so a crashed instance does not strand the thread.
- Q: What is the maximum length of a thread id, and which characters are allowed in it? → A: 128 characters; letters, digits, hyphen, underscore, dot.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Send a message and watch the run live (Priority: P1)

A user opens a conversation with the orchestrator by sending a message to a thread they name, and watches the orchestrator work as it happens: each step it completes and its reply as it is written. A later message to the same thread continues the conversation with full memory of the earlier turns.

**Why this priority**: Talking to the orchestrator is the product. Every other story builds on a thread that can receive a message and stream a run.

**Independent Test**: Start the service against a local database, send a message to a new thread id, read the stream until it closes, then send a follow-up that refers to the first message and confirm the reply reflects it.

**Acceptance Scenarios**:

1. **Given** a running service and a thread id that has never been used, **When** the user sends a message to that thread, **Then** the response is a live event stream that starts promptly, carries progress events and message events each labeled by kind, and closes when the orchestrator finishes its reply.
2. **Given** a thread with one completed turn, **When** the user sends a follow-up to the same thread id, **Then** the orchestrator's reply reflects the content of the earlier turn.
3. **Given** a thread with one completed turn, **When** the service is restarted and the user sends a follow-up, **Then** the earlier turn is still part of the conversation.
4. **Given** a run in progress on a thread, **When** a second run is requested on the same thread, **Then** the second request is refused with a message that a run is already active, and the first run is unaffected.
5. **Given** a request whose message is empty, **When** it is submitted, **Then** it is rejected as invalid and no run starts.

---

### User Story 2 - Decide on an environment-changing action (Priority: P2)

When the orchestrator wants to take an action with effects outside its workspace, the run pauses before the action executes. In this increment the only such action is a placeholder "send notification" that does nothing but write a log line. The stream tells the user exactly what is proposed, with its arguments. The user approves it as proposed, edits the arguments and approves, or rejects it. The run then continues on the same thread and the orchestrator reports what happened.

**Why this priority**: The constitution makes human approval non-negotiable. This gate is what makes the runtime safe to connect to real tools later, and the placeholder lets the gate be built and proven before any real tool exists.

**Independent Test**: Send a message that leads the orchestrator to propose sending a notification, observe the pause, then on separate threads submit each decision kind and check the log output and the continuation.

**Acceptance Scenarios**:

1. **Given** a run in which the orchestrator proposes the send-notification action, **When** the proposal is reached, **Then** the action does not execute, the stream emits a pause event naming the action and its arguments, the stream closes, and the thread's state shows the pending decision.
2. **Given** a paused thread, **When** the user approves, **Then** the action executes exactly once with the proposed arguments, one log line records the channel and message, and the continuation stream shows the orchestrator acknowledging the result.
3. **Given** a paused thread, **When** the user edits the arguments (for example a different channel) and approves, **Then** the action executes with the edited arguments and the log line shows the edited values, not the originals.
4. **Given** a paused thread, **When** the user rejects, optionally with a reason, **Then** the action never executes, no log line is written, and the orchestrator continues and tells the user the action was not taken, repeating the reason when one was given.
5. **Given** a thread with no pending decision, **When** a decision is submitted, **Then** it is refused with an explanation and the thread is unchanged.
6. **Given** a pending decision, **When** the submitted decision is malformed (an unknown decision kind, or edited arguments the action cannot accept), **Then** it is refused and the pending decision remains available.

---

### User Story 3 - Inspect where a thread stands (Priority: P3)

A user, or a tool acting for one, reads a thread's current state: the conversation so far, whether a run is active, and any decision waiting on them with the full proposal. This is how someone picking up a thread hours later sees the context before deciding.

**Why this priority**: Deciding on a pause requires seeing the proposal and the conversation that led to it. Without state inspection a paused thread is a black box.

**Independent Test**: Run one turn on a thread, read its state and confirm the messages; pause a thread on a proposal, read its state and confirm the pending decision matches what the stream reported.

**Acceptance Scenarios**:

1. **Given** a thread with completed turns, **When** its state is read, **Then** the response includes the full transcript in order (human, assistant, tool-call, and tool-result messages) and shows that no decision is pending and no run is active.
2. **Given** a paused thread, **When** its state is read, **Then** the response includes the pending decision with the same action name and arguments the stream reported.
3. **Given** a thread id that has never been used, **When** its state is read, **Then** the response says the thread was not found.
4. **Given** a stream that has just closed, **When** state is read immediately afterwards, **Then** it already reflects that run's outcome.

---

### User Story 4 - Decide after a restart (Priority: P4)

A paused thread outlives the service process. The service is stopped and started again (a deploy, a crash, an overnight gap) and the user decides later. The run continues as if there had been no interruption.

**Why this priority**: The constitution requires a paused run to be resumable after any delay. Restart durability is where persistence bugs show, so it is a separate, deliberately tested story.

**Independent Test**: Pause a thread, stop the service, start it again, read the state and confirm the pause is still there, approve, and observe the continuation complete.

**Acceptance Scenarios**:

1. **Given** a paused thread, **When** the service is stopped and started again, **Then** reading the thread's state shows the same pending decision and proposal as before the restart.
2. **Given** the restarted service, **When** the user submits a decision on that thread, **Then** the run continues and completes with the same outcome it would have had without the restart.
3. **Given** a paused thread, **When** the user waits an arbitrary time before deciding, **Then** the service imposes no time limit and the decision is accepted.

---

### User Story 5 - Bring the runtime up locally and troubleshoot a run (Priority: P5)

A developer clones the repository, starts the local database with one command using the definition in the repo, copies the example configuration without editing credentials, starts the service, and completes a streamed run. When something looks wrong, they request the same run with a debug flag and see verbose internal detail in the stream.

**Why this priority**: Nothing above can be exercised end to end without a local database that matches the documented configuration, and the debug view is how a developer finds out why a run misbehaved. It is last because the automated tests do not depend on it.

**Independent Test**: From a clean clone, follow the README only: bring up the database, copy the example configuration, start the service, send a message, and see a completed stream; then repeat with the debug flag and see additional debug events.

**Acceptance Scenarios**:

1. **Given** a clean clone and a container runtime, **When** the developer runs the documented database command and copies the example configuration unchanged, **Then** the service starts and connects to the database without editing any credential.
2. **Given** a running service, **When** a run is requested with the debug flag, **Then** the stream contains the normal progress and message events plus debug events, and the run's outcome is the same as without the flag.
3. **Given** a running service, **When** a run is requested without the debug flag, **Then** no debug events appear in the stream.
4. **Given** the database is not reachable, **When** the service starts, **Then** startup fails with an error that names the database connection as the cause instead of serving requests that would fail.

---

### Edge Cases

- **Client disconnects mid-stream**: the run stops at the next step boundary; work completed before then is saved; the thread stays consistent, so a later state read or run works. The run does not continue in the background in this increment.
- **Service stops while a run is in flight (not paused)**: the in-flight step is lost and completed steps remain. The thread's active-run marker is released when the service stops cleanly; after a crash it expires on its own, so a new run is refused only until the expiry window passes. The gated action cannot have executed, because it only runs after a recorded approve or edit decision.
- **Two service instances share one database**: a run started on one instance is visible to every instance, so a run requested on another instance for the same thread is refused as a conflict; a paused thread can be resumed from any instance.
- **Model provider fails or times out during a run**: the stream emits an error event with a human-readable message and closes; the thread stays consistent; the next run on the thread works.
- **Run exceeds the step limit**: a run that reaches the configured maximum number of orchestrator steps ends with an error event that names the limit; completed steps are saved and the thread stays usable for the next run.
- **More than one gated action proposed in one step**: the pause lists every proposed action; the resume supplies one decision per action; each action executes or is skipped according to its own decision.
- **Decision submitted for a thread that was never used**: refused the same way as a thread with no pending decision.
- **New message while a decision is pending**: the run is refused as a conflict and the proposal stays pending and visible; the user decides through resume first. A message is never treated as an implicit decision.
- **Edited arguments change the action's target**: the log line and the orchestrator's acknowledgement reflect the edited values. The original proposal remains visible in the thread history.
- **Debug flag on resume**: resume accepts the same flag and streams the same event kinds as a run.
- **Thread id with disallowed characters or excessive length**: thread ids are opaque, client-chosen strings of 1 to 128 characters drawn from letters, digits, hyphen, underscore, and dot; an id that is empty, longer, or contains any other character is rejected as invalid before any work starts.

## Requirements *(mandatory)*

### Functional Requirements

**Interface**

- **FR-001**: The service MUST expose three thread operations: start a run (`POST /threads/{thread_id}/runs`, taking a message and an optional debug flag), read state (`GET /threads/{thread_id}/state`), and resume a paused thread (`POST /threads/{thread_id}/resume`, taking a decision and an optional debug flag). The existing health check is unchanged.
- **FR-001a**: A thread id MUST be 1 to 128 characters long and contain only letters, digits, hyphen, underscore, and dot. Every thread operation MUST reject any other id as invalid before doing any work.
- **FR-002**: Starting a run MUST return a live event stream that a standard HTTP client can consume incrementally. Every event MUST be labeled with its kind. By default the stream carries progress events (one per step the orchestrator completes, including tool calls and their results) and message events (the orchestrator's reply as it is produced).
- **FR-003**: The stream MUST end in exactly one of three ways, each distinguishable by the client: completion of the reply, a pause event carrying the proposed action's name and arguments, or an error event carrying a human-readable message. The stream closes after the terminal event.
- **FR-004**: With the debug flag set, the stream MUST additionally include verbose debug events. The flag MUST NOT change the run's behavior or outcome; it is the same run with more detail.
- **FR-004a**: A run MUST stop after a maximum number of orchestrator steps, read from settings with a safe local default. Reaching the limit MUST end the run with the error event described in FR-003, naming the limit, and MUST leave the thread usable.

**Threads and persistence**

- **FR-005**: All turns sent to the same thread id MUST form one conversation. Thread history, orchestrator working state, and pending decisions MUST be stored durably and MUST survive a service restart.
- **FR-006**: A thread MUST accept at most one active run at a time, enforced through the shared database so the rule holds across every service instance using that database. A run requested while another is active on that thread MUST be refused with a conflict explanation, leaving the active run unaffected. The active-run marker MUST be released when the run ends and MUST expire after a configurable period if the instance holding it dies, so a crashed instance cannot leave a thread refused indefinitely.
- **FR-007**: Reading a thread's state MUST return the full transcript so far (human, assistant, tool-call, and tool-result messages, in order), whether a run is active, and the pending decision with its full proposal when one exists. A thread id that has never been used MUST produce a not-found response.
- **FR-008**: Persistence connections MUST be opened when the service starts and released when it stops. If the database cannot be reached at startup, the service MUST fail to start with an error that names the cause.

**Gated action**

- **FR-009**: The runtime MUST include one placeholder environment-changing action, send notification, whose only effect is writing a log line that records the channel and message it was asked to send.
- **FR-010**: The send-notification action MUST NOT execute unless an approve or edit decision has been recorded on the thread for that specific proposal. No code path MAY bypass this, including any future delegate or background run.
- **FR-011**: When the orchestrator proposes the send-notification action, the run MUST pause before execution, emit the pause event described in FR-003, and leave the proposal readable through thread state. While a proposal is pending, a new message run on that thread MUST be refused with a conflict explanation; a proposal is resolved only by a decision.
- **FR-012**: A decision MUST be one of: approve (execute as proposed), edit (execute with user-supplied replacement arguments), or reject (do not execute, with an optional reason). After edit, the action MUST receive the edited arguments. After reject, the orchestrator MUST be told the action was not taken, with the reason when given, and MUST continue the conversation.
- **FR-013**: Resuming MUST continue the same thread from its pause and stream the continuation using the same event kinds and terminal events as a run. Resume MUST be refused when the thread has no pending decision, and a malformed decision MUST be refused while leaving the pending decision in place.
- **FR-014**: A paused thread MUST be resumable after the service has been stopped and restarted, and after any delay, with the same outcome as an uninterrupted run.

**Orchestrator construction**

- **FR-015**: There MUST be exactly one way to construct the orchestrator, and the service MUST use it. Any later debugging tool or command-line entry point MUST build the orchestrator through that same path so that it runs the same code as the service.
- **FR-016**: The orchestrator MUST use the model named in settings; the model identifier MUST NOT appear anywhere else in application code.
- **FR-017**: The orchestrator's file operations MUST be confined to the agent workspace directory. Skills MUST be loaded from the committed skills folder inside that workspace when the orchestrator is built, so a skill added to the repository is available after the next service start.

**Local development, configuration, and quality**

- **FR-018**: The repository MUST include a local database definition that a developer brings up with one command, whose credentials match the documented example configuration exactly, so the copied example connects without edits.
- **FR-019**: Every new configurable value introduced by this feature MUST be read through settings from the project's environment-variable prefix, MUST have a safe local default unless it carries a credential, and MUST be documented in the example configuration.
- **FR-020**: The README MUST document, as they exist, how to bring up the local database, start the service, start a run, read thread state, and resume a paused thread.
- **FR-021**: Automated tests MUST cover: the stream's event kinds and terminal events, thread continuity, the one-active-run rule including a marker left by a dead instance expiring, the step limit ending a run with an error event, the pause on the gated action, each decision kind and its effect on the log, resume after a simulated restart, and state reads including not-found, and thread id validation. The suite MUST run without a live model provider.

### Key Entities

- **Thread**: a conversation identified by a client-chosen id (1 to 128 characters: letters, digits, hyphen, underscore, dot). Holds the ordered messages, the orchestrator's working state, and at most one pending decision. Outlives the service process.
- **Run**: one turn of work on a thread, started by a message or by a decision. Ends completed, paused awaiting a decision, or failed. At most one is active per thread across all service instances; the active marker is shared and expires if its instance dies.
- **Run Event**: an item in a run's live stream, labeled by kind: progress, message, debug, pause, error, or completion. Pause events carry the proposal.
- **Pending Decision (Proposal)**: an environment-changing action the orchestrator wants to take, with the action's name and arguments, waiting for a user. Visible in thread state until resolved.
- **Decision**: a user's response to a proposal: approve, edit (with replacement arguments), or reject (with an optional reason). Recorded on the thread.
- **Skill**: a committed instruction document in the workspace skills folder that the orchestrator can draw on. Source, not runtime output.
- **Workspace**: the directory the orchestrator may read and write. Contains the skills folder (committed) and working files written at runtime (not committed).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a run request is accepted, the first stream event arrives within 1 second, before any model output, so the user knows the run started.
- **SC-002**: In the automated suite, 100% of send-notification proposals pause for a decision, and zero executions occur without a recorded approve or edit.
- **SC-003**: Each decision kind (approve, edit, reject) produces the specified log outcome and a visible continuation in 100% of automated runs.
- **SC-004**: A thread paused before a full service stop and start resumes and completes with the same outcome as an uninterrupted run, in 100% of automated runs.
- **SC-005**: A state read performed immediately after a stream closes reflects that run's outcome (completed, paused, or failed) in 100% of cases; there are no stale reads.
- **SC-006**: A developer following only the README goes from a clean clone to a completed streamed run against the local database in under 15 minutes, without editing any credential.
- **SC-007**: The lint, format, and test gates pass on the merged change, and the test suite completes with no network access to a model provider.

## Assumptions

**Scope**

- Out of scope, per the description: delegate agents, authentication, real external tools, the command-line interface, and support for more than one model at a time. Also out of scope for this increment: listing, creating, or deleting threads as separate operations; cancelling a run; continuing a run in the background after the client disconnects; and deterministic escalation of high-severity signals (no severity handling exists yet, so that principle is not exercised).
- The user's input is plain text; the service treats it as the human turn of the conversation.
- Thread ids are opaque strings chosen by the client, 1 to 128 characters of letters, digits, hyphen, underscore, and dot; the service rejects anything else and otherwise uses them as given.

**Behavior choices**

- One active run per thread; concurrent requests are refused rather than queued. The rule is enforced in the shared database rather than in process memory, so several service instances can share one database; the marker's expiry period is a setting.
- Reading state for a never-used thread returns not found rather than an empty state, so a mistyped id is not mistaken for a fresh thread.
- Resume on a never-used thread is refused the same way as a thread with nothing pending.
- A client disconnect stops the run at the next step boundary; completed steps are saved.
- The debug flag only adds detail; it never alters behavior.
- Each pending action receives its own decision when a step proposes more than one.

**Environment and dependencies**

- The runtime stack and the component choices named in the description (one construction path for the orchestrator, persistence opened for the life of the service process, a workspace root with a committed skills folder, and a container definition for the local database) are planning inputs. They are preserved verbatim in the Input line above and bound by the constitution's Technology Constraints; the plan records them, so this spec does not restate them as requirements.
- A model provider reachable through the existing provider settings is available when the service runs for real. Automated tests use a scripted stand-in model so they need no network access. How tests obtain persistence without a second runtime code path is decided in the plan; the existing tests' property of needing no database is preserved for tests that do not exercise persistence.
- A long-term store is opened alongside checkpoint storage for the orchestrator's use; no cross-thread memory feature is specified in this increment.
- At least one minimal skill is committed so skill loading is observable; it is a placeholder, not a real capability.
- The service keeps binding to the local interface by default. Because there is no authentication, anyone who can reach the port acts as a user; that is acceptable for local development and is documented.
- The existing health check remains a liveness check and is not extended.
