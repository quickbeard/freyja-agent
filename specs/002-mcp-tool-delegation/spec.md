# Feature Specification: MCP Tool Delegation

**Feature Branch**: `002-mcp-tool-delegation`

**Created**: 2026-11-04

**Status**: Draft

**Input**: User description: "Delegate tools over MCP. Freyja connects at startup to MCP servers named in settings and loads their tools into the one agent factory, alongside its own. Every tool is classified deterministically from configuration as read-only or effectful; effectful and unclassified tools are gated by the existing approve, edit, reject interrupt, on the orchestrator and its subagents alike. Tool catalogs are visible through an endpoint. The suite uses an in-process fake MCP server and needs no network; the quickstart proves one real local MCP server. Out of scope: A2A delegate agents, authentication, remote MCP servers over the network, the CLI."

## Clarifications

### Session 2026-11-06

- Q: What should happen when the service starts and one configured MCP server cannot be started or reached? → A: Fail startup: the service refuses to serve and the error names the server, as it does for an unreachable database. There is no degraded start and no per-server opt-out.
- Q: How should an operator describe a local MCP server in the configuration: as a command Freyja launches itself, as an address of a server that is already running on this machine, or either? → A: Either form. Each server entry is a launch command, which the service starts and stops, or a local address of a server the operator runs, which the service only connects to. An address that is not on the local machine is refused at startup.
- Q: After a server fails during operation, what should the next call to one of its tools do: try to connect again, or fail immediately until the service is restarted? → A: Reconnect on the next call, inside the per-call time limit. A server that is answering again works without restarting the service, and the catalog shows the outcome of the most recent attempt. There is no background health check, and the service never relaunches a server process that exited.
- Q: When an MCP tool returns something that is not plain text, such as an image, audio, or an attached file, what should the orchestrator receive? → A: Text and structured data only. Each non-text part is replaced by a short note naming its kind, so the orchestrator knows something was left out; the result is not an error.
- Q: Beyond name, origin, description, and classification, what else should the tool catalog show for each tool? → A: The arguments the tool accepts and the hints its server publishes about it, such as a read-only label. The hints are labelled as the server's claim and never affect gating.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Use a delegate's read-only tools without friction (Priority: P1)

An operator names a tool server in the configuration and lists which of its tools are read-only. When the service starts, the orchestrator can use those tools in a conversation exactly as it uses its own file tools: the run streams the call and its result and completes without pausing, because reading cannot change anything outside the workspace.

**Why this priority**: This is the first capability that comes from outside the process, and it is the one people will feel immediately: Freyja can answer questions it could not answer before. Everything else in this feature builds on a server being connected and its tools being callable.

**Independent Test**: Configure one tool server with one tool marked read-only, start the service, send a message that needs that tool, and read the stream until it closes. The run completes, the tool's result appears in the transcript, and no approval was requested.

**Acceptance Scenarios**:

1. **Given** a configured tool server with a tool marked read-only, **When** the service starts, **Then** the orchestrator has that tool available in its first run, and the service log names the server and the number of tools it provided.
2. **Given** a running service with such a tool, **When** a user sends a message that the orchestrator answers by calling it, **Then** the stream shows the call and its result, no pause event appears, and the reply reflects the result.
3. **Given** the same tool, **When** the orchestrator delegates to a subagent and the subagent calls it, **Then** the subagent gets the result and the run completes the same way.
4. **Given** a tool server that returns an error for a call, **When** the orchestrator calls it, **Then** the error is reported to the orchestrator as the tool's result, the run continues, and the reply tells the user the tool failed.

---

### User Story 2 - Every effectful delegate tool waits for a person (Priority: P2)

A tool that is not on the read-only list is treated as effectful. When the orchestrator proposes calling one, the run pauses before the call with the tool's name, the server it belongs to, and the arguments, exactly as the placeholder notification does today. The user approves, edits the arguments, or rejects, and the conversation continues. A tool the configuration says nothing about is gated too: the safe interpretation of silence is "ask".

**Why this priority**: The constitution forbids any environment-changing action without a person's approval. Connecting external tools is the first time that rule meets actions Freyja did not write itself. The classification has to be a deterministic rule that the model, and the tool server, cannot override.

**Independent Test**: Configure a server with one tool that is not listed as read-only, send a message that leads the orchestrator to propose it, observe the pause, then on separate threads approve, edit, and reject, checking the server received exactly the expected calls.

**Acceptance Scenarios**:

1. **Given** a tool not marked read-only, **When** the orchestrator proposes calling it, **Then** the call does not reach the server, the stream ends with the pause event naming the server, the tool, and the arguments, and the thread's state shows the pending proposal.
2. **Given** a paused thread, **When** the user approves, **Then** the server receives exactly one call with the proposed arguments and the continuation shows its result.
3. **Given** a paused thread, **When** the user edits the arguments and approves, **Then** the server receives the edited arguments, and arguments the tool's declared input does not accept are refused before anything resumes, with the proposal left in place.
4. **Given** a paused thread, **When** the user rejects with a reason, **Then** the server receives no call and the orchestrator continues, telling the user the action was not taken and repeating the reason.
5. **Given** a tool the configuration neither lists as read-only nor mentions at all, **When** the orchestrator proposes it, **Then** it pauses like an effectful tool.
6. **Given** a tool server that labels its own tool as read-only in the information it publishes, **When** the configuration does not list that tool as read-only, **Then** the tool is still gated: only the operator's configuration decides.
7. **Given** a subagent that proposes an effectful delegate tool, **When** the proposal is reached, **Then** the run pauses the same way and the decision applies to that call.

---

### User Story 3 - See what Freyja can call (Priority: P3)

A user, an operator, or a tool acting for them reads the catalog of everything the orchestrator can call: Freyja's own tools and each connected server's tools, with each tool's description, whether it is read-only or gated, and whether its server was reachable the last time it was used. This is how someone decides what to mark read-only and how they find out why a tool is unavailable.

**Why this priority**: Approval decisions and configuration choices both need a trustworthy list of what exists. Without it the operator is editing a list of names blind, and a user seeing a pause cannot tell where the tool came from.

**Independent Test**: Start the service with one server configured, read the catalog, and confirm it lists Freyja's own tools and the server's tools with the classification the configuration implies.

**Acceptance Scenarios**:

1. **Given** a running service, **When** the catalog is read, **Then** every tool the orchestrator can call is listed with its name, origin (Freyja itself or a named server), description, declared input, and classification, and the list matches what the orchestrator was given.
2. **Given** a server whose most recent call failed because it could not be reached, **When** the catalog is read, **Then** that server is shown as unavailable and its tools are still listed with their classification.
3. **Given** no servers configured, **When** the catalog is read, **Then** only Freyja's own tools appear, and the notification placeholder is listed as gated.
4. **Given** a server that labels a tool as read-only while the configuration does not, **When** the catalog is read, **Then** the tool is listed as gated, and the server's label appears separately, marked as the server's own claim.

---

### User Story 4 - Find out quickly when a server misbehaves (Priority: P4)

An operator misconfigures a server, or a server stops working. Startup refuses to run with a server it cannot reach and says which one. A server that fails mid-conversation produces a clear tool error, a log line naming the server and tool, and an "unavailable" mark in the catalog, instead of a hung run.

**Why this priority**: External processes fail in ways Freyja's own code does not. Clear failure is what makes the feature operable; without it a broken server looks like a confused model.

**Independent Test**: Point the configuration at a server that cannot start and confirm startup fails naming it. Then start with a working server, stop that server while the service runs, make a call, and confirm the error result, the log line, and the catalog status.

**Acceptance Scenarios**:

1. **Given** a configured server that cannot be started or reached, **When** the service starts, **Then** startup fails with an error naming that server, and the service does not serve requests.
2. **Given** a configuration that marks as read-only a tool no server provides, **When** the service starts, **Then** startup fails naming the server and the tool, so a typo cannot silently gate or ungate anything.
3. **Given** two servers that provide tools with the same name, or a server tool named like one of Freyja's own, **When** the service starts, **Then** startup fails naming the collision.
4. **Given** a server that stops responding during a run, **When** the orchestrator calls one of its tools, **Then** the call fails within the configured time limit, the orchestrator receives the error as the tool's result, the service log names the server and the tool, and the thread stays usable.
5. **Given** any failure above, **When** the error is written to the stream, the log, or the catalog, **Then** it never contains a credential the server was given.
6. **Given** a server marked unavailable after a failed call, **When** it is answering again and the orchestrator calls one of its tools, **Then** the call succeeds without a service restart and the catalog shows the server as reachable.

---

### User Story 5 - Bring a real server up locally (Priority: P5)

A developer follows the README to add one real tool server that runs on their machine, marks one of its tools read-only, starts Freyja, and completes a run that uses that tool and a run that pauses on one of its effectful tools. The automated suite proves all of the above against a stand-in server inside the test process, so it still needs no network.

**Why this priority**: The feature is only real once it works with a server Freyja did not write. It is last because the automated tests do not depend on it.

**Independent Test**: From a clean clone, follow the README only: configure the documented local server, start the service, complete a read-only run and an approved effectful run.

**Acceptance Scenarios**:

1. **Given** a clean clone and the README, **When** the developer configures the documented local server and starts the service, **Then** the catalog lists that server's tools and a run using a read-only one completes.
2. **Given** the same setup, **When** the developer triggers an effectful tool, **Then** the run pauses, approval executes it, and the server's effect is observable.
3. **Given** the automated suite, **When** it runs without a network and without any external server installed, **Then** every behavior in this feature is covered by tests against an in-process stand-in server.

---

### Edge Cases

- **A server provides no tools**: startup succeeds, the catalog lists the server with an empty tool list, and the log says so; an empty server is unusual but not an error.
- **A tool has no declared input**: approve and reject work; an edit is refused as not applicable, since there is nothing to validate an edit against.
- **A tool's declared input is a structure with nested or optional fields**: the pause shows the proposed arguments in full; an edit must satisfy the declared input, and the refusal names the offending field.
- **A server is removed from the configuration while old threads reference its tools**: the transcript still shows the old calls and results; a new call to a tool that no longer exists fails as unknown, and the run continues.
- **Two runs on different threads call the same server at the same time**: both complete and each receives its own result; the service MUST NOT mix results between runs.
- **A call exceeds the per-call time limit**: it fails with an error result naming the limit; the orchestrator continues; the lease and the thread are unaffected.
- **The server's own read-only labels differ from the configuration**: the configuration wins in every case; server labels are informational only and are shown in the catalog as the server's own claim, apart from the classification.
- **A read-only tool is slow or chatty**: it still needs no approval; its text output is passed through unchanged. Output size limits are out of scope for this increment.
- **A tool returns an image, audio, or an attached file**: the text and structured parts reach the orchestrator, each other part becomes a note naming its kind, and the run continues; a result made only of such parts is delivered as those notes.
- **The notification placeholder**: unchanged; it stays gated and appears in the catalog like any effectful tool.
- **A server entry gives an address on another machine**: startup fails naming the server; servers reached over the network are out of scope, and the rule is checked from the configuration, not left to the operator's care.
- **A launched server's process exits while the service runs**: its tools return error results and the catalog shows it unavailable until the service is restarted, because the service does not relaunch it. A server reached by address recovers on the next call once the operator has it running again.
- **Configuration changes while the service runs**: not picked up until restart; the catalog reflects what was loaded at startup.

## Requirements *(mandatory)*

### Functional Requirements

**Configuration and startup**

- **FR-001**: Operators MUST be able to name one or more tool servers in the service configuration, each with a unique name, exactly one of two forms of connection, and the list of its tools that are read-only. The two forms are a command the service launches on the local machine, and the local address of a server that is already running. An address that does not refer to the local machine MUST be refused. Everything about a server MUST come from configuration; nothing MAY be hardcoded.
- **FR-002**: Credentials a server needs MUST be supplied through the environment and MUST NOT appear in committed configuration, in logs, in error messages, or in the catalog.
- **FR-003**: When the service starts, it MUST connect to every configured server, obtain its tool list, and make those tools available to the orchestrator and to its subagents through the one construction path the service already has. Tools MUST be loaded once at startup; configuration changes take effect on restart.
- **FR-004**: Startup MUST fail with an error naming the server when a configured server cannot be started or reached, when a server entry gives both forms of connection, neither, or an address that is not local, when the configuration marks as read-only a tool the server does not provide, or when two tools would share a name. The service MUST NOT serve requests in that state.
- **FR-005**: Connections to servers MUST be released when the service stops. A server the service launched MUST be stopped with it; a server reached by address is left running, because the operator owns it.

**Classification and the gate**

- **FR-006**: Every tool MUST have exactly one classification, read-only or effectful, determined by a deterministic rule from configuration alone: a tool is read-only only if the configuration lists it as such for its server; every other tool, including any tool the configuration does not mention, is effectful. Labels published by the server itself and the model's own judgment MUST NOT influence the classification.
- **FR-007**: Every effectful tool MUST be gated by the existing approval interrupt with approve, edit, and reject decisions, on the orchestrator and on every subagent. No code path MAY call an effectful tool without a recorded approve or edit decision for that specific proposal.
- **FR-008**: The pause event and the pending proposal in thread state MUST name the server the tool belongs to, in addition to the tool name and arguments already shown.
- **FR-009**: An edit decision MUST be validated against the tool's declared input before the run resumes; a tool with no declared input MUST NOT accept edits. A refused edit MUST leave the proposal in place.
- **FR-010**: Read-only tools MUST run without approval and MUST stream their call and result like any other step.
- **FR-011**: Freyja's own tools MUST keep their current classification: the file tools are read-only or workspace-confined and need no approval; the notification placeholder stays gated.

**Execution**

- **FR-012**: A failing, timing out, or unavailable server call MUST become an error result delivered to the orchestrator, MUST be logged with the server and tool names, and MUST NOT end the run or affect the thread's run lease. The per-call time limit MUST be configurable with a safe local default. A call to a server whose connection was lost MUST try to connect again within that same limit, so a server that is answering again is usable without a service restart. The service MUST NOT relaunch a server process that exited and MUST NOT probe servers in the background.
- **FR-013**: Calls from concurrent runs to the same server MUST each receive their own result.
- **FR-018**: A tool result MUST reach the orchestrator as its text and structured data. Each part that is not text, such as an image, audio, or an attached file, MUST be replaced by a short note naming the kind of content left out, and MUST NOT be sent to the model or stored in the thread. A result with such parts is a normal result, not an error. This applies to read-only and effectful tools alike.

**Catalog**

- **FR-014**: The service MUST expose a read-only catalog listing every tool the orchestrator can call: name, origin (Freyja itself or the server name), description, declared input, classification, and, for server tools, whether the server is reachable and the hints the server publishes about the tool. Those hints MUST be presented as the server's own claim, separate from the classification, and a tool whose server publishes none MUST show none. Reachability is the outcome of the most recent attempt to use the server, at startup or in a call; reading the catalog MUST NOT contact a server. The catalog MUST reflect exactly the tools given to the orchestrator at startup.

**Local development, documentation, and quality**

- **FR-015**: Every new configurable value MUST be read through settings from the project's environment-variable prefix, MUST have a safe local default unless it carries a credential, and MUST be documented in the example configuration.
- **FR-016**: The README MUST document how to configure a server and its read-only list, how to read the catalog, and how to run the suite, and MUST name one real local server the developer can try.
- **FR-017**: Automated tests MUST cover: tools loaded from a server at startup; read-only calls completing without a pause, directly and through a subagent; effectful and unclassified calls pausing; approve, edit, and reject reaching the server as specified; edit validation against declared input, including a tool with none; server-published labels being ignored for gating and shown in the catalog as the server's claim; the catalog's contents, including each tool's declared input, and server status; a server entry in each of the two forms of connection being accepted; each startup failure in FR-004; a failing or timed-out call; a call succeeding again after its server recovers; a result with a non-text part delivered as text plus a note; concurrent calls; and absence of credentials in output. The suite MUST run without a network and without any external server installed, using a stand-in server inside the test process.

### Key Entities

- **Tool Server**: an external provider of tools, running on the same machine, identified by a configured name and described in one of two forms: launched by the service, which then starts and stops it, or reached at a local address, where the operator owns its lifetime. Has a connection status (reachable or not, as of the most recent attempt to use it) and a set of Tools.
- **Tool**: a callable the orchestrator may use, with a name, a description, an optional declared input, an origin (Freyja itself or a Tool Server), a Classification, and, for a server's tool, any hints the server publishes about it, which are informational only.
- **Classification**: read-only or effectful. Derived from configuration by a fixed rule; effectful tools are gated.
- **Read-only List**: the configured set of tool names, per server, that are exempt from approval. The only input to Classification.
- **Catalog**: the list of every Tool with origin, description, declared input, Classification, server-published hints, and server status. The tools are those loaded at startup; the status is the latest known.
- **Proposal**: an existing entity, extended with the server name of the proposed tool.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the automated suite, 100% of effectful and unclassified delegate tool calls pause for a decision, and zero such calls reach a server without a recorded approve or edit.
- **SC-002**: 100% of read-only delegate tool calls in the suite complete without a pause, directly and through a subagent.
- **SC-003**: The catalog read on a freshly started service lists every tool the orchestrator was given, with the classification the configuration implies, in 100% of automated runs, and responds within 1 second.
- **SC-004**: Startup with an unreachable, misdescribed, or colliding server configuration fails within 30 seconds with an error naming the server, in 100% of automated runs, and the process serves no requests.
- **SC-005**: A failing or timed-out server call ends in an error result and the run continues, in 100% of automated runs; no such call leaves a thread refused or paused.
- **SC-006**: A developer following only the README goes from a clean clone to a completed run that uses a real local server's read-only tool, and an approved run that uses its effectful tool, in under 20 minutes, without editing any credential.
- **SC-007**: The lint, format, and test gates pass on the merged change, and the suite completes with no network access and no external server installed.

## Assumptions

**Scope**

- Out of scope, per the description: delegate agents that carry their own reasoning (A2A), authentication, servers reached over the network rather than on the local machine, and the command-line interface. Also out of scope for this increment: reloading configuration without a restart, relaunching a server process that exited, background health checks, limits on tool output size, and per-user or per-thread differences in which tools are available.
- Servers run on the same machine as the service. A launched server is started and stopped with the service; a server reached by address is started by the operator before the service and is unreachable, a startup failure, if it is not running. Which transport each form uses is a planning decision.
- Subagents receive the same tool set as the orchestrator, with the same classification; there is no narrower tool set per subagent in this increment.

**Behavior choices**

- A server that cannot be reached at startup fails startup, matching how the service already treats an unreachable database. The alternative, running without that server's tools and showing it as unavailable, hides a missing capability behind a model that cannot explain it; the catalog's "unavailable" status is for servers lost after a successful start. Confirmed in clarification on 2026-11-06; there is no degraded start and no per-server opt-out in this increment.
- Silence in the configuration means "gated". Marking a tool read-only is an explicit operator act, and the list is checked against the server's actual tools at startup so a typo fails loudly.
- Labels a server publishes about its own tools are never trusted for classification, because the server is the party whose effects are being gated.
- Tool names are unique across the whole catalog, so a proposal names one tool without ambiguity; a collision is a startup error rather than silently renaming anything.
- Approval stays per proposal, as today; there is no "always allow" memory for a tool.
- The per-call time limit has a local default in the tens of seconds; a single value applies to every server in this increment.

**Environment and dependencies**

- The existing approval gate, thread state, resume validation, run lease, and step limit are reused unchanged; this feature adds tools to the one construction path and extends the proposal with the server name.
- A tool server for the quickstart exists that runs locally with no account and offers both a read-only and an effectful tool; the plan names it. The automated suite uses a stand-in inside the test process.
- The service keeps binding to the local interface with no authentication, so anyone who can reach the port can read the catalog and approve proposals; that remains acceptable for local development and is documented.
