# Feature Specification: Telemetry of Runs

**Feature Branch**: `011-run-telemetry`

**Created**: 2026-11-27

**Status**: Draft

**Input**: User description: "Telemetry of what the orchestrator does: traces and metrics of runs, sent to a collector the deployment configures, and off by default. An operator who runs Freyja today has only the log to learn how the service is doing: how many runs start and how they end, how long a run takes, which model calls and tool calls a run made and which of them failed, and whether the delegation worker keeps up. With this feature an operator names a collector in the deployment's configuration and the service sends it one trace per run, with a span for the run and one for each model call and each tool call under it (a hand-off to an agent included, and a subagent's calls under the call that started the subagent), and counts and durations: runs by how they ended, model calls, tool calls, model errors, a run's duration, and the delegation worker's round time and the number of threads it found waiting. With no collector named, nothing is recorded, nothing is started, and nothing is connected to. What is sent holds names and never content: an id the service made, a name the deployment configured, one of the service's own words, or an error's class name; never a message, a tool's arguments or result, a model's reply, an error's text, an agent's address, an API key, or a caller's token, and a tool a model made up is named as unknown. Recording never changes what a run does: telemetry that fails is left out with a warning and never fails a run, a model call, or a tool call, and never makes an approved action run twice. Nothing that a model, a tool server, or a delegate agent receives changes because telemetry is on."

## Clarifications

### Session 2026-11-27 (asked while the spec was written)

- Q: Should a delegate's own traces join Freyja's, which needs Freyja to send a trace's id with what it sends to tool servers and agents? → A: No. Nothing is ever sent: turning telemetry on changes nothing that a model, a tool server, or an agent receives, and a delegate's traces stay apart from Freyja's (FR-038). Joining them can follow as a change of its own.
- Q: How is a collector that requires a credential given one? → A: Not by Freyja. No setting holds a collector's credential and none is stored (FR-004). The collector runs where the service reaches it without one, or the deployment uses what the collector's client reads from the environment by itself, which Freyja neither reads nor vouches for.
- Q: Beside runs, model calls, tool calls, and the delegation worker's rounds, which of three further things are in scope: the work that waits (threads awaiting a decision, the oldest outcome not yet delivered), the requests the service answers, and the model calls a library makes by itself? → A: All three (User Stories 3 and 4, and FR-010).

### Session 2026-11-27

- Q: When a request starts a run, does the run's span stand inside that request's trace, or is it a trace of its own? → A: Inside the request's. One trace shows the request and, under it, the run with its calls; a background run, which no request starts, has a trace of its own (FR-013, User Story 4).
- Q: When a client sends a trace's id of its own with a request, does Freyja continue that trace or leave the id unused? → A: Unused. Every trace starts in the service, and nothing a client sends reaches the collector (FR-028).
- Q: A thread's id is chosen by its client, so it is the one value in a trace that the service neither made nor configured. Does a run's span carry it? → A: Yes, on the run's span and nowhere else: on no other span and on no metric. An operator finds a run by its thread's id as in the log, which already names it (FR-030, FR-032).
- Q: A profile's test (feature 004) calls the model outside any run. Are those calls recorded? → A: Yes, marked as a test: each has a span under its request's span and is counted with the model calls, so the counts cover every call the service makes to a model, and a test can be told from a run's call (FR-010a).
- Q: Is every run and request recorded in full, or does a setting keep only a share of the traces? → A: Everything is recorded, so the run a person reports is always there. No setting thins traces; a deployment that wants fewer thins them at its collector (FR-006).

### Plan review, 2026-11-27

One requirement was made exact while the plan was written; [research.md](research.md) R10 gives the finding. It was not asked of the owner and is named in the plan's pull request.

- FR-022 said that reading the two waiting values changes nothing. The index of threads settles a thread's row at the first reading after its run ended (feature 007), so a count taken without settling stays too low on a deployment where nobody opens the console. The reading settles those rows, as a reading of the list of threads does, and FR-022 now says what it may not change: no thread, and no lease.

### Checklist review, 2026-11-27

Changes made while reviewing [checklists/privacy-reliability.md](checklists/privacy-reliability.md); its "Review record" gives the finding behind each. None was asked of the owner; each is named in the pull request.

- What is never sent also names an agent's reply and progress text and what its card says (FR-031), and every value that is text is cut to 256 characters (FR-030), which bounds a tool's name that its server chose.
- The service's own lines never say where telemetry is sent; the component that sends may name the collector's address when it fails, and that address holds no credential (FR-005).
- A failure to record is said in the log once an instance, at its first occurrence (FR-034).
- `unmatched` also names a request whose method its route does not take (FR-024), and a request the application fails to answer is recorded with 500 and the error's class (Edge Cases).
- SC-005 is measured against the time limit of one attempt to send, and SC-006 names the three points at which recording is made to fail.
- Sending goes on without a restart when the collector answers again (Edge Cases), and an address with `http` is taken without a warning, since nothing sent is content (Assumptions).

### Analysis, 2026-11-27

`/speckit-analyze` found no critical or high issue. One finding changed a requirement; the others changed tasks and the plan, and are named in the pull request.

- FR-022 said "one line in the log" for a reading of the waiting values that failed, without saying how often. It is said once, and again only after a reading has succeeded, so a database that is down for an hour is one line.

## User Scenarios & Testing *(mandatory)*

The terms of the earlier features are used as they are there. A **run** is one stretch of the orchestrator's work on a thread: the answer to a message, the continuation after a person's decision (a resume), or the background run that delivers an agent task's outcome (feature 003). A run ends, pauses with a proposal for a person to decide, fails, is stopped at a boundary (feature 007), or is cancelled when its reader leaves or the service stops it. A **model call** is one request to the model, and a **tool call** one use of a tool: one of Freyja's own, a tool server's (feature 002), or an agent's hand-off or cancel (feature 003). The **delegation worker** is what watches pending agent tasks and delivers their outcomes, one **round** at a time.

Five terms are new. **Telemetry** is the record this feature makes of what the service does, for an operator. A **collector** is the program a deployment runs to receive it, at an address the deployment names. A **span** is the record of one piece of work with its start, its end, and a few named values; a **trace** is the spans of one piece of work, each under the one that caused it. A **metric** is a count, a duration, or a present value, such as how many runs ended in an error, with a few named values to tell them apart.

### User Story 1 - Follow one run in a trace (Priority: P1)

An operator has named a collector in the deployment's configuration. A person reports that a task took four minutes and ended oddly. The operator finds the run in the collector by the thread's id and sees it as one trace: the run, each call of the model with how long it took, each tool call with the tool's name and where it went (a tool server, an agent), and which of them failed with which kind of error. A call a subagent made stands under the call that started the subagent. The operator learns where the four minutes went without reading the log and without seeing a word of the conversation.

**Why this priority**: A trace of a run is what the log cannot give: the order, the nesting, and the time of every step of one run in one view. Everything else in this feature is a sum or a neighbor of it.

**Independent Test**: With a collector stand-in that keeps what it is sent, start a run that calls the model twice, a tool server's tool once, and hands work to a stand-in agent. Read what the stand-in received: one trace with one span for the run and one span for each call, each under the right parent, each with the names this spec lists and with nothing of the message, the tool's arguments, or the replies.

**Acceptance Scenarios**:

1. **Given** a collector is configured, **When** a run ends, **Then** the collector receives one span for the run that names the run, its thread, the kind of run, the LLM profile and the model it ran on, and how it ended.
2. **Given** a run that calls the model, **When** it ends, **Then** each model call has a span under the run's that names the provider and the model and says whether the call succeeded, failed, or was cancelled.
3. **Given** a run that calls a tool, **When** it ends, **Then** each tool call has a span that names the tool, its classification, and the tool server or the agent it went to, and says how it ended.
4. **Given** a run in which the orchestrator starts a subagent, **When** the subagent calls the model or a tool, **Then** those spans stand under the span of the call that started the subagent.
5. **Given** a run that pauses with a proposal, **When** a person approves it and the resume runs the action, **Then** the first run's span says it paused, and the action's span belongs to the resume's run and appears once.
6. **Given** a proposal a person rejects, **When** the resume runs, **Then** no span exists for the rejected action.
7. **Given** a model that asks for a tool the orchestrator does not have, **When** the call is recorded, **Then** the tool is named `unknown`, and what the model wrote appears nowhere.
8. **Given** a long conversation that a library shortens by asking the model for a summary, **When** the run ends, **Then** that call of the model has a span in the run's trace, marked as one the orchestrator did not make itself.
9. **Given** a run that fails because the model failed, **When** it ends, **Then** the run's span and the failed call's span each say so with the service's error code or the error's class name, and with none of the error's text.
10. **Given** no collector is configured, **When** the service starts and runs, **Then** nothing is recorded, no background activity is started for telemetry, and no connection is made for it.

---

### User Story 2 - Know how the service is doing from counts and durations (Priority: P2)

An operator keeps a dashboard on the collector. It shows how many runs started and how they ended, how long runs take, how often the model is called and how often it fails, which tools are called and how those calls end, and how long the delegation worker's rounds take. When runs start to end in `model_error`, or the worker's rounds grow from a second to a minute, the operator sees it on the dashboard before a person reports it.

**Why this priority**: Counts are how an operator notices a problem; a trace is how one is then understood. It comes second because a count without a trace says that something is wrong and not what.

**Independent Test**: With a collector stand-in, drive runs that end, pause, fail, and are stopped, with model calls and tool calls that succeed and fail, and let the delegation worker make rounds. Read the metrics the stand-in received: each count equals what was driven, each is told apart by the names this spec lists, and none names a run or a thread.

**Acceptance Scenarios**:

1. **Given** a collector is configured, **When** runs start and end, **Then** the collector receives how many started and how many ended, told apart by the kind of run and by how each ended, and how long each took.
2. **Given** runs that end because the model failed or because no usable model is active, **When** they end, **Then** a count of model errors rises, told apart by the service's error code.
3. **Given** model calls and tool calls, **When** they end, **Then** the collector receives their counts, told apart by provider and model, and by tool, classification, tool server or agent, and by how each ended.
4. **Given** the delegation worker makes a round, **When** it ends, **Then** the collector receives how long the round took and how many threads the round found with an outcome to deliver.
5. **Given** two instances of the service, **When** each sends its counts, **Then** each count is of that instance alone, and the collector adds them up.
6. **Given** any metric this feature sends, **When** it is read, **Then** none of its named values is a run's id or a thread's id.

---

### User Story 3 - See the work that waits (Priority: P3)

An operator wants to know what is waiting on somebody or something: how many threads have a proposal that no person has decided, and how long the oldest finished agent task has waited for its outcome to be delivered to its thread. A number that only grows says that nobody is deciding, or that deliveries are stuck.

**Why this priority**: What waits is what an operator can act on, and the service already knows both numbers. It comes third because it is two values beside the counts of the second story, read on a timer and not from a run.

**Independent Test**: With a collector stand-in, pause three threads with a proposal and decide one; hold one agent task's outcome ready and undelivered. Read the metrics: the number of threads awaiting a decision is two, and the age of the oldest undelivered outcome is the time since it became ready. Deliver the outcome and decide the rest: both values return to zero.

**Acceptance Scenarios**:

1. **Given** threads whose last run paused with a proposal that is not decided, **When** the metrics are sent, **Then** the collector receives their number.
2. **Given** an agent task whose outcome is ready and has not been delivered to its thread, **When** the metrics are sent, **Then** the collector receives the age of the oldest such outcome, and zero when there is none.
3. **Given** several instances sharing one database, **When** each sends these two values, **Then** each sends what the database says, so the values are the same from every instance and are not to be added up.
4. **Given** the database cannot be read when the values are due, **When** the metrics are sent, **Then** the two values are left out, the log says so once until a reading succeeds again, and no run and no other metric is affected.

---

### User Story 4 - See the requests the service answers (Priority: P4)

An operator wants to see the service as its clients see it: which routes are asked, how often, how long an answer takes, and which requests are refused. A request that starts a run and the run it starts are one trace, so the operator can go from a slow answer to the run behind it.

**Why this priority**: Requests are the outermost view and the most common thing to measure, but the service's work is the run, which the first two stories already cover. It comes last because it adds the frame around them.

**Independent Test**: With a collector stand-in, send a request that starts a run, a request that lists threads, a request for a path the service does not have, and a request under a host name the service does not answer for. Read what the stand-in received: one span and one count for each request, named by the route as the service declares it, with the method and the status code; the run's span stands under the span of the request that started it; the path that does not exist appears nowhere.

**Acceptance Scenarios**:

1. **Given** a collector is configured, **When** the service answers a request, **Then** the collector receives a span for it with the method, the route as the service declares it, and the status code, and counts and durations told apart by the same three.
2. **Given** a request that starts a run, **When** both end, **Then** the run's span stands under the request's span in one trace.
3. **Given** a background run that the delegation worker starts, **When** it ends, **Then** its span starts a trace of its own.
4. **Given** a request for a path the service has no route for, **When** it is recorded, **Then** the route is named `unmatched`, and the path as it was written appears nowhere.
5. **Given** a request the service refuses, for its host name, its origin, or its credential, **When** it is recorded, **Then** its span and its count carry the status code of the refusal and nothing of what was refused.
6. **Given** a request to a route that says whether the service is up or ready, **When** it is answered, **Then** nothing is recorded for it.
7. **Given** a request that carries a trace's id of the client's own, **When** it is recorded, **Then** that id is not used: every trace starts in the service.
8. **Given** a request whose answer is a stream, **When** the stream ends or its reader leaves, **Then** the request's span ends then, and a run that goes on without a reader ends its own span when it ends.

---

### Edge Cases

- **The collector cannot be reached, or answers slowly.** No run waits for it. What cannot be sent in time is dropped, the memory telemetry holds stays bounded, and the log says that sending failed, in the words of the component that sends. When the collector answers again, sending goes on without a restart; what was dropped is not sent later.
- **The collector's address is wrong in form**, such as one with a user name and a password in it. The service does not start, and the error does not repeat the address.
- **Recording a span or a count fails**, for any reason. It is left out, the log says so once with the kind of failure, and the run, the model call, or the tool call it was for goes on as it would with telemetry off.
- **Recording the end of an approved action fails.** The action has run once and does not run again.
- **A run is cancelled because its reader left, or the service stops.** Its span ends as cancelled, and so do the spans of the calls under way.
- **A run is stopped at a boundary** (feature 007). Its span says stopped, and no call is cut off to end it.
- **The service stops while it still holds telemetry.** It sends what it holds after the last run has ended, within a time limit, and does not wait longer than that.
- **A run on a thread another agent started** (feature 009). It is recorded like any other, marked as such, and named by its thread's id as the log names it; the caller's name and token are not sent, and telemetry goes to the operator and not to the caller.
- **A thread's id is one a client chose.** It is the one value in a span that the service did not make or configure. It is held to the characters and the length feature 001 allows, appears on the run's span and nowhere else, and the log already names it.
- **A tool server's tool has a name its server chose.** It is named as the orchestrator was built with it, which is the name the catalog already lists (feature 002), cut to the length a value may have; a name a model made up never is.
- **A model call fails with an error whose text holds a key or an address.** The span says the error's class name and nothing of its text.
- **A profile's test** (feature 004) calls a model outside any run. Its request is recorded like any request, and each of its calls of the model has a span under the request's, marked as a test. A test of values that are not saved names the provider and not the model.
- **A request never reaches a route**, because it is refused for its host name. It is recorded with the route `unmatched` and the status of the refusal.
- **The application fails before it answers a request.** The request is recorded with the status 500 and the error's class name, and with none of the error's text.
- **The same instance restarts.** Counts start again from zero, as a collector expects of a restarted source.
- **A deployment turns on the telemetry of a library the service uses**, through that library's own configuration. That telemetry is the library's: this feature neither sends it nor answers for what it holds.

## Requirements *(mandatory)*

### Functional Requirements

**Configuration**

- **FR-001**: Telemetry MUST be off unless the deployment names a collector. Off, the service MUST record nothing, start nothing for telemetry, and connect to nothing for it, and MUST behave in every other way as it does with telemetry on.
- **FR-002**: The deployment's configuration MUST take the collector's address and the name the service is given in what is sent, each through a setting of the service with the safe default: no address, and the service's own name.
- **FR-003**: The collector's address MUST be checked at startup: a scheme of `http` or `https`, a host, and at most a port and a path. An address with a user name, a password, a query, or a fragment MUST stop startup with an error that does not repeat the address.
- **FR-004**: Freyja MUST hold no credential for a collector: no setting takes one and none is stored. The documentation MUST say so, and MUST say what the collector's client reads from the environment by itself, which Freyja neither reads nor vouches for.
- **FR-005**: At startup the service MUST say in its log whether telemetry is on, and no line the service writes MUST say where it is sent. The component that sends says a failure in its own words, which may name the collector's address; that address holds no credential (FR-003).
- **FR-006**: Telemetry MUST be sent in the OpenTelemetry protocol over HTTP, so that any collector that takes it can receive it. Every run, call, and request this spec names MUST be recorded: no setting keeps only a share of them.

**Traces of runs**

- **FR-007**: Every run MUST have one span, from its start to its end, that names the run's id, the thread's id, the kind of run (a message, a resume, or a background run), whether it goes on without a reader, whether another agent started its thread, the LLM profile's id and source, the model's provider and name, how it ended (ended, paused for a decision, failed, stopped, cancelled), and, when it failed, the service's error code.
- **FR-008**: Every call of the model made within a run MUST have a span under the run's that names the provider and the model, says whether it succeeded, failed, or was cancelled, and, when it failed, names the error's class.
- **FR-009**: Every tool call that runs within a run MUST have a span that names the tool, its classification (feature 002), the tool server it belongs to or the agent it hands to, how it ended (succeeded, failed, paused inside for a decision, cancelled), and, when it failed, the error's class. A tool's name MUST come from the tools the orchestrator was built with; a call of any other name MUST be named `unknown`.
- **FR-010**: A call of the model that a library makes by itself within a run, such as the one that summarizes a long conversation, MUST have a span like any other model call, marked so that it can be told from the orchestrator's own, and MUST be counted with the model calls under the same mark.
- **FR-010a**: A call of the model that a profile's test makes (feature 004) MUST have a span under the span of the test's request, marked as a test, and MUST be counted with the model calls under the same mark. It names the provider, and the model when the test is of a saved profile or of the environment's; a test of values that are not saved names the provider alone, because a model's name that nobody saved is what a request wrote. The key under test MUST NOT be sent.
- **FR-011**: A span of a call a subagent makes MUST stand under the span of the call that started the subagent.
- **FR-012**: A gated action MUST be recorded when it runs, which is after a person approved it, and in the run that ran it. An action a person rejected MUST have no span.
- **FR-013**: A run's span MUST stand under the span of the request that started it, when a request did. A background run MUST start a trace of its own.

**Counts and durations**

- **FR-014**: The service MUST count the runs that started and the runs that ended, told apart by the kind of run, whether it goes on without a reader, whether another agent started its thread, and, for those that ended, how they ended and the error code; and MUST record how long each run took.
- **FR-015**: The service MUST count model calls, told apart by provider, model, who made the call (the orchestrator or a subagent, a library by itself, a profile's test), and how each ended, and MUST count the runs that ended because the model failed or because no usable model was active, told apart by the error code.
- **FR-016**: The service MUST count tool calls, told apart by tool, classification, tool server or agent, and how each ended.
- **FR-017**: The service MUST record how long each round of the delegation worker took and how many threads the round found with an outcome to deliver or a delivery to try again.
- **FR-018**: No metric MUST name a run or a thread, and every value that tells a metric's counts apart MUST come from a set the deployment bounds: the service's own words, and the names of the models, tools, tool servers, agents, and routes it was built with.
- **FR-019**: Every count and duration of FR-014 to FR-017 and FR-026 MUST be of one instance alone.

**The work that waits**

- **FR-020**: The service MUST report the number of threads whose last run paused with a proposal that no person has decided.
- **FR-021**: The service MUST report the age of the oldest agent task's outcome that is ready and has not been delivered to its thread, and zero when there is none.
- **FR-022**: The two values MUST be read from what every instance shares, no more often than metrics are sent, and never on the path of a run. Reading them MUST take no thread's lease, change no thread, and contact no model, tool server, or agent; it MAY settle a thread's row in the index of threads, as a reading of the list of threads does (feature 007). A reading that fails MUST leave both values out and MUST affect no run and no other metric. The first failure MUST be said in the log in one line, and the next not before a reading has succeeded again.
- **FR-023**: The documentation MUST say that the two values are the same from every instance and are not to be added up.

**Requests**

- **FR-024**: Every request the service answers, on its own address and on the address other agents call (feature 009), MUST have a span with the method, the route as the service declares it, and the status code. The path as it was written MUST NOT be sent; a request for which the service has no route, one with a method its route does not take, and one that is refused before a route is found MUST each be named `unmatched`.
- **FR-025**: A request's span MUST hold nothing of the query, the headers, the body, the host name asked for, the client's address, or a credential.
- **FR-026**: The service MUST count requests and record how long each took, told apart by method, route, and status code.
- **FR-027**: A request to a route that says whether the service is up or ready MUST NOT be recorded.
- **FR-028**: A trace's id that a client sends with a request MUST NOT be used. Every trace starts in the service.
- **FR-029**: The span of a request whose answer is a stream MUST end when the stream ends or its reader leaves.

**Names, never content**

- **FR-030**: Every value this feature sends MUST be one of: an id the service made (a run's, an LLM profile's); a thread's id; a name the deployment or an operator configured (a model's provider and name, a tool server's, an agent's, the service's); the name of a tool the orchestrator was built with; a route as the service declares it; a method; a status code; one of the service's own words (a kind of run, an outcome, a classification, an error code); an error's class name; a number, a time, or a duration. A value that is text MUST be cut to 256 characters.
- **FR-031**: The service MUST NOT send a message, a tool's arguments or result, a model's reply, an agent's reply or progress text, what an agent's card says, an error's text, an agent's address, an API key, a caller's name or token, the name an operator gave an LLM profile, or anything a model wrote.
- **FR-032**: A thread's id MUST appear on the span of a run and nowhere else: on no other span, a request's included, and on no metric.
- **FR-033**: A proof in the test suite MUST drive a recognizable key, caller's token, agent address, and text through every kind of run, call, and request this feature records, and MUST show that none appears in anything sent and that every named value is one this spec allows. A new span, metric, or named value MUST be added to it.

**Telemetry never changes what the service does**

- **FR-034**: Recording MUST never fail a run, a model call, a tool call, a request, or a round of the delegation worker. What cannot be recorded MUST be left out, and the work it was for MUST go on as it would with telemetry off. The first such failure of an instance MUST be said in the log, in one line that names the kind of failure and says that later ones are not logged.
- **FR-035**: Recording MUST never make a step of a run happen again. An approved action MUST run once, also when recording its start or its end fails.
- **FR-036**: Sending MUST happen off the path of every run and request. A collector that is slow or cannot be reached MUST delay no run and no answer, and the memory telemetry holds MUST stay bounded: what cannot be sent is dropped.
- **FR-037**: When the service stops, it MUST send what it still holds after the last run has ended, within a time limit, and MUST NOT wait longer than that limit.
- **FR-038**: Turning telemetry on MUST change nothing that a model, a tool server, or an agent receives: no trace's id and no other header or field is added to anything the service sends them. A delegate's own traces are therefore not joined to Freyja's.
- **FR-039**: Turning telemetry on MUST change nothing that a client of the service receives: no header, no field of an answer, and no event of a stream.
- **FR-040**: The orchestrator MUST be built the same way with telemetry on and off, so that the two differ by configuration alone.
- **FR-041**: This feature MUST NOT turn on, configure, or send the telemetry of a library the service uses, and MUST NOT make its own telemetry the process's shared one.

**Documentation**

- **FR-042**: The two settings MUST be documented where the service's settings are, and one document MUST list every span, every metric, and every named value this feature sends, what it never sends, and what it leaves alone.

### Key Entities

- **Run span**: the record of one run: its ids, its kind, its model, and how it ended. The parent of the run's call spans, and the child of the request's span when a request started the run.
- **Model call span**: the record of one call of the model: the orchestrator's own, a subagent's, or a library's within a run, or a profile's test's under its request.
- **Tool call span**: the record of one tool call that ran, with the tool's name, its classification, and where it went.
- **Request span**: the record of one request the service answered, by method, route, and status code.
- **Counts and durations**: per instance: runs started and ended, a run's duration, model calls, model errors, tool calls, requests and their duration, and the delegation worker's round time and the threads a round found waiting.
- **Waiting values**: read from what every instance shares: the number of threads awaiting a decision, and the age of the oldest outcome not yet delivered.
- **Collector settings**: the collector's address and the service's name in what is sent.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any run recorded while telemetry is on, an operator can say from the collector alone, within one minute of the run's end, which model calls and tool calls it made, in what order, how long each took, and which failed.
- **SC-002**: With telemetry on, every run has exactly one run span, and every model call and every tool call that ran has exactly one span, under the right parent.
- **SC-003**: With no collector configured, the service makes no connection and starts no background activity for telemetry, and every existing test of the service passes unchanged.
- **SC-004**: In the proof of FR-033, a recognizable key, caller's token, agent address, and text appear zero times in everything sent.
- **SC-005**: With a collector that takes a connection and never answers, a run produces the same events in the same order as with telemetry off, and ends without waiting for the collector: in less than the time limit of one attempt to send.
- **SC-006**: With recording made to fail at each point where it records (the start of a span, the end of a span, and a count), every run produces the same events in the same order as with telemetry off, and every approved action runs exactly once.
- **SC-007**: What a model, a tool server, and an agent receive is the same, header for header and field for field, with telemetry on and off.
- **SC-008**: The two waiting values a collector holds are no older than two intervals at which metrics are sent.
- **SC-009**: Every request but those that ask whether the service is up or ready has exactly one span and is counted once, and the path of a request for a route that does not exist appears zero times in everything sent.
- **SC-010**: An operator can turn telemetry on with two settings and no change to anything else the deployment configures.

## Assumptions

- An implementation of User Stories 1 and 2 was written before this spec, without the model calls a library makes by itself (FR-010) and without the run's span standing under a request's (FR-013). It is brought to this spec at the implement step and is not what decides a requirement.
- Telemetry is for the operator of a deployment. No person who uses the console, no client of the API, and no agent that calls Freyja is shown any of it.
- The deployment runs and secures its collector. What a collector keeps, for how long, and who reads it is outside this feature. What is sent holds names and no content, so an address with `http` is taken without a warning; a deployment that wants the names kept from the network path uses `https`.
- Everything is recorded. A deployment that wants fewer traces thins them at its collector.
- Counts are sent about once a minute and spans within seconds; the plan sets the intervals and the time limits.
- A thread's id is treated as the log treats it: it names a thread to the operator. A deployment whose clients put something private into a thread's id has that in its log already.
- The names of models, tools, tool servers, and agents are configuration, which an operator may read. An agent's address and an LLM profile's key are not names and are never sent.
- A profile's test (feature 004) is not a run: it starts no run span and counts as no run. Its calls of the model are recorded and marked as a test's.
- The service's log stays as it is. This feature adds the lines its requirements name and removes none.
- Joining a delegate's traces to Freyja's, a credential for a collector held by Freyja, and sampling are each left for a change of their own.
