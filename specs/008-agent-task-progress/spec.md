# Feature Specification: Progress of Pending Agent Tasks

**Feature Branch**: `008-agent-task-progress`

**Created**: 2026-11-21

**Status**: Draft

**Input**: User description: "Progress of pending agent tasks. A task handed to a delegate agent can run for hours. While the hand-off's own call still waits, the agent's progress reaches the run's stream as update events (feature 003). Once the call time limit passes and the task is pending, the service keeps only the task's state: the thread state and the Tasks section of the web console (feature 007) say that the agent is working, and nothing more, until the task ends. A person cannot tell a task that is halfway from one that is stuck, and the orchestrator cannot answer when asked how far a task is. This feature keeps the latest progress an agent reports for a task that has not ended, and shows it. Whenever the service learns a task's status, in a hand-off that follows the task and in each regular check of a pending task, it keeps the text of the status message the agent attached, with the time it was learned, in place of the one kept before; a status with no text leaves the kept one as it is. A hand-off that follows a task by asking for its status also reports a changed text as a progress update, not only a changed state. The kept text is the agent's own words and is treated like any other text an agent supplies: cut to a fixed length, passed through otherwise as an agent's replies and progress updates are (feature 005, FR-028), never rendered as markup, and never trusted to decide anything. It is shown in three places: each entry of the thread state's agent tasks gains the progress text and its time; the Tasks section shows it under a pending agent task, with how long ago it was learned, and refreshes it by the timer the task's view already has; and the orchestrator's read-only check of the thread's agent tasks lists it, so that a person who asks how a task is going gets an answer without anything being sent to the agent. Learning progress never sends an agent anything new: it uses only the status reads the service already makes, at the interval it already has. Progress is never written into the conversation as a message, never starts a run, and never pauses one. When a task ends, its outcome is reported as today; the last progress text is kept with the task as it was. An agent that reports no text is shown as today. Several service instances sharing one database show the same progress. Out of scope: push notifications from an agent, a stream of progress to the console for a task that is pending, a history of progress lines, structured progress such as a percentage or a progress bar, progress parts that are not text, and any change to when a task is checked, given up, or delivered."

## User Scenarios & Testing *(mandatory)*

The terms of the earlier features are used as they are there. An **agent task** is what a delegate agent works on for a thread (feature 003); it is **pending** while it is working or waits for input, and it has **ended** otherwise. A **hand-off** is the orchestrator's message to an agent; it **follows** the task the agent creates until the call time limit, and a task still working then is pending. The service **checks** a pending task by reading its status from the agent, at the interval feature 003 sets. A **task**, in the console, is one conversation with Freyja (feature 007), and its page lists its agent tasks.

Two terms are new. An agent's **progress text** is the text of the status message it attaches to a task that is working: one or a few sentences in the agent's own words, such as how many files it has been through. The **kept progress** of an agent task is the progress text the service last learned for it, together with the time it learned it.

### User Story 1 - Read how far a pending agent task is (Priority: P1)

A person, or a program that calls the service, has handed work to an agent that takes an hour. The hand-off returned long ago with the note that the task is pending. They read the thread's state and see, beside the agent task, what the agent last said about its work and when the service learned it. Reading it again some minutes later shows a later text. They can tell a task that moves from one that has said the same thing for an hour.

**Why this priority**: This is the part every other story reads from. Until the service keeps what an agent reports, nothing can show it. By itself it already serves every client of the service, the console included once the next story is built.

**Independent Test**: With the stand-in agent set to stay working and to report a progress text that it changes on request, hand it a task and let the hand-off end as pending. Read the thread's state: the agent task has the text and a time. Change the stand-in's text, wait one check interval, and read again: the text is the new one and the time is later. Count the requests the stand-in received: they are the ones it would have received without this feature.

**Acceptance Scenarios**:

1. **Given** a pending agent task whose agent attaches a text to its working status, **When** the service checks the task, **Then** the thread's state lists the agent task with that text and the time the service learned it.
2. **Given** kept progress for a task, **When** a later check finds another text, **Then** the kept progress is the new text with the later time, and the earlier text is kept nowhere.
3. **Given** kept progress for a task, **When** a later check finds the same text, **Then** the text and its time stay as they were, so the time says since when the agent has reported nothing new.
4. **Given** kept progress for a task, **When** a later check finds a working status with no text, or with text that is only blank space, **Then** the kept progress stays as it was.
5. **Given** a hand-off that follows a task and receives progress from the agent, **When** the call time limit passes and the task is recorded as pending, **Then** the task starts with the progress the hand-off last received, and does not wait for the first check to have one.
6. **Given** an agent that attaches no text to any status, **When** its task is pending, **Then** the agent task is listed as it is today, and its progress is empty.
7. **Given** a pending agent task, **When** the service learns its progress, **Then** the agent received no request it would not have received without this feature: no further status read, no message, and no cancel.
8. **Given** a pending agent task, **When** the service learns its progress, **Then** nothing is added to the thread's conversation, no run starts, and no proposal is created.
9. **Given** several service instances sharing one database, **When** the instance that watches a task learns its progress, **Then** the thread's state read through any instance shows that progress.
10. **Given** a service restart, **When** the thread's state is read afterwards, **Then** each agent task has the progress it had before the restart.

---

### User Story 2 - See an agent's progress on the task's page (Priority: P2)

A person has a task open in the Tasks section of the console. Freyja has handed part of it to an agent, and the page lists that agent task as pending. Under it the page now shows what the agent last reported, and how long ago. The person leaves the page open; the text changes by itself as the agent moves on. When the agent task ends, the progress goes and the outcome appears in the conversation, as it does today.

**Why this priority**: The console is where a person waits for a long agent task. Today it says "The agent is working" for the whole of it. This story is what the person sees of the first one, and it needs the first one.

**Independent Test**: With the stand-in agent working and reporting a progress text, open the task's page: the pending agent task shows the text and how long ago it was learned. Change the stand-in's text and do nothing else: within the time of one check and one reading of the page, the page shows the new text. Let the stand-in finish: the progress is no longer shown, the agent task is shown as ended, and the conversation shows the outcome.

**Acceptance Scenarios**:

1. **Given** a task with an agent task that is working and has kept progress, **When** its page is shown, **Then** the agent task shows the progress text, as text, and how long ago the service learned it.
2. **Given** such a page left open, **When** the kept progress changes, **Then** the page shows the new text and time at its next reading of the task, without a reload and without the person doing anything.
3. **Given** an agent task that is working and has no kept progress, **When** its page is shown, **Then** the agent task is shown as it is today, and nothing stands where the progress would be.
4. **Given** an agent task that has ended or that waits for input, **When** its page is shown, **Then** no progress is shown for it, whatever was kept.
5. **Given** progress text that holds markup, a link, or a script, **When** the page shows it, **Then** it is shown as the characters the agent wrote, and nothing in it is rendered, followed, or run.
6. **Given** progress text of several lines or of the greatest length the service keeps, **When** the page shows it, **Then** the lines are kept apart, the whole text can be read, and the rest of the page stays usable.
7. **Given** a page that is hidden, **When** it is shown again, **Then** it reads the task and shows the progress as it then is.

---

### User Story 3 - Ask Freyja how an agent task is going (Priority: P3)

A person sends a message on a task whose agent is still working: "How far is it?" The orchestrator checks the thread's agent tasks, as it can today, and now finds what the agent last reported and when. It answers in its own voice. Nothing was sent to the agent to find out.

**Why this priority**: A person who works through the conversation should not have to open another view to learn what Freyja already knows. It comes third because the console's page already answers the question, and because it only adds to a check that exists.

**Independent Test**: With a pending agent task that has kept progress, send a message that asks how the task is going. The run's stream shows the orchestrator's check of the thread's agent tasks, and its result holds the progress text, marked as the agent's own words, and its time. The stand-in agent's record shows no request made because of the question.

**Acceptance Scenarios**:

1. **Given** an agent task that is working and has kept progress, **When** the orchestrator checks the thread's agent tasks, **Then** the result lists the task with its progress text and the time it was learned, and says that the text is what the agent reported.
2. **Given** an agent task with no kept progress, or one that has ended or waits for input, **When** the orchestrator checks the thread's agent tasks, **Then** the task is listed as it is today.
3. **Given** the orchestrator checks the thread's agent tasks, **When** the result holds progress, **Then** no request was made to any agent to produce it.
4. **Given** a run on a thread with pending agent tasks, **When** the orchestrator is told at the start which tasks are pending (feature 003, FR-018), **Then** what it is told holds no progress text: progress reaches the orchestrator only as the result of its own check.
5. **Given** progress text that reads like an instruction to the orchestrator, **When** it reaches the orchestrator in the result of a check, **Then** it has no more effect than an agent's reply has today: every action with an effect outside the workspace still pauses for a person.

---

### User Story 4 - Progress in a run's stream from an agent that does not stream (Priority: P4)

A person watches a run in which the orchestrator hands work to an agent that does not stream. The hand-off follows the task by asking for its status. Today the stream shows a progress update only when the task's state changes, so an agent that goes on working and reports "one quarter done", then "half done", shows nothing between the start and the call time limit. With this feature each changed text appears in the stream as a progress update.

**Why this priority**: It closes the same gap for the first seconds of a task that the other stories close for the hours after them. It comes last because that window is short, and because an agent that streams already shows its progress there.

**Independent Test**: With the stand-in agent set not to stream, to stay working, and to change its progress text between two status reads, start a run that hands it a task and read the stream until the hand-off returns. The stream holds one progress update for each text the hand-off saw, in order, and none for a status read that found the same state and the same text.

**Acceptance Scenarios**:

1. **Given** a hand-off that follows a task by asking for its status, **When** a status read finds the same state and another text, **Then** the run's stream shows a progress update with the new text.
2. **Given** such a hand-off, **When** a status read finds the same state and the same text, or no text, **Then** the stream shows nothing for that read.
3. **Given** such a hand-off, **When** a status read finds another state, **Then** the stream shows the progress update it shows today.
4. **Given** an agent that streams, **When** a hand-off follows its task, **Then** the stream shows what it shows today.

---

### Edge Cases

- **An agent reports a very long text.** The kept progress is its first 2,000 characters. Nothing marks the cut; an agent that wants its progress read keeps it short.
- **An agent's text holds characters that cannot be kept**, such as a null character. They are dropped, as they are from other text an agent supplies. A text that is empty afterwards counts as no text.
- **An agent's status message has parts that are not text**, such as structured data or a file. Only its text parts make the progress text, joined as an agent's reply is; the other parts are left out, with no note in their place.
- **An agent's text names an address, its own or another agent's.** It passes through, as an agent's replies and progress updates do (feature 005, FR-028). What an agent writes is the agent's to answer for.
- **An agent cannot be reached at a check.** Nothing new is learned. The kept progress stays, with the time it was learned, and the time the task was last checked says what it says today.
- **An agent no longer knows the task.** The task ends as failed, as today. The kept progress stays as it was.
- **A task ends between two checks.** Its outcome is reported as today. The kept progress is the last one learned while the task was working; what the agent attached to the status that ended the task is part of the outcome and does not become progress.
- **A task asks for input.** The agent's question is the outcome reported to the orchestrator, as today, and does not become progress. The kept progress stays as it was and is not shown while the task waits for input. When the task is working again after an answer, progress is kept and shown again.
- **A task is given up**, because it reached the maximum age or because its agent was moved or removed. Nothing is learned afterwards. The kept progress stays as it was.
- **Progress is learned while a run is in progress on the thread, or while a proposal waits for a decision.** It is kept all the same. Progress writes nothing to the conversation, so it never waits for the thread to be free and never holds anything up.
- **A check and a hand-off that follows the same task learn progress at nearly the same time.** This can happen while an answer to a task's question is under way. Each keeps what it learned; the one that learned last is what is kept.
- **Two tasks of one thread, or of two agents, report progress.** Each agent task has its own kept progress.
- **A thread's agent task was recorded before this feature.** It has no kept progress, and is shown as today, until a check learns one.
- **The person's page is hidden for a long time.** It reads nothing while hidden (feature 007). When it is shown again it reads the task and shows the progress as it then is.
- **The agent's text says the task is finished while its state says working.** The state decides, as today. The text is shown as what the agent said.
- **An agent reports progress in a language or a script the console does not use.** It is shown as written.

## Requirements *(mandatory)*

### Functional Requirements

**Keeping progress**

- **FR-001**: For every agent task that is working, the service MUST keep the progress text it last learned and the time it learned it. One agent task has at most one kept progress; a new one replaces the one before it, and no earlier text is kept anywhere.
- **FR-002**: The service learns a task's progress in two places and in no other: in a hand-off that follows the task, from each status the agent sends or the hand-off reads, and in each regular check of a pending task (feature 003, FR-019). In both, the progress text is the text of the status message the agent attached to a status that says the task is working. A status that ends the task, or that asks for input or for authentication, is an outcome and MUST NOT change the kept progress.
- **FR-003**: A status that carries no status message, or one whose text is empty or only blank space, MUST leave the kept progress as it was. When the text learned equals the text kept, the kept progress, its time included, MUST stay as it was, so that the time says since when the agent has reported nothing new.
- **FR-004**: The progress text is made of the text parts of the status message, joined as the text of an agent's reply is. Parts that are not text MUST be left out. The text MUST be cut to its first 2,000 characters, and characters that cannot be kept MUST be dropped; a text that is empty afterwards counts as no text. Otherwise the text MUST be kept as the agent wrote it: it is not searched for an address, as an agent's replies and progress updates are not (feature 005, FR-028).
- **FR-005**: The time kept with a progress text MUST be when the service learned that text, read from the clock the service's instances share, and never a time the agent supplied.
- **FR-006**: An agent task that is recorded while a hand-off follows it, as pending or as ended, MUST be recorded with the progress the hand-off last received for it, when there is one. A pending task MUST NOT wait for its first check to have progress the hand-off already saw.
- **FR-007**: When a task ends, is given up, or comes to wait for input, the kept progress MUST stay with the task as it was. Nothing clears it, and nothing replaces it until the task is working again and a new text is learned.
- **FR-008**: Kept progress MUST be the same for every service instance sharing the database, and MUST survive a restart. It is kept as long as the agent task it belongs to.

**Showing progress**

- **FR-009**: Each agent task in the thread's state MUST carry its kept progress: the text and the time it was learned, or nothing for a task that has none. This holds for a pending task and for an ended one. Everything the thread's state says of an agent task today (feature 003, FR-017) MUST stay as it is.
- **FR-010**: In the Tasks section of the console, a task's page MUST show, for each agent task that is working and has kept progress, the progress text and how long ago the service learned it. It MUST NOT show progress for an agent task that has ended or that waits for input. An agent task with no kept progress MUST be shown as it is today.
- **FR-011**: The page MUST show a change of the kept progress without a reload and without an action of the person, at the readings of the task it already makes while an agent task is awaited (feature 007). This feature adds no reading and no timer, and the page still reads nothing while it is hidden.
- **FR-012**: The page MUST put progress text in the page as text and never render it as markup, follow a link in it, or run anything in it (feature 007, FR-012). It MUST keep the text's line breaks, MUST let the whole of it be read, and MUST stay usable with a text of the greatest length the service keeps. The console MUST call the thing an agent works on an agent task here as everywhere (feature 007, FR-001a).
- **FR-013**: The orchestrator's check of the thread's agent tasks (feature 003, FR-018) MUST list, for each agent task that is working and has kept progress, the progress text and the time it was learned, and MUST say that the text is what the agent reported. Every other task MUST be listed as today. The check stays one of Freyja's own read-only tools, and producing its result MUST NOT contact any agent.
- **FR-014**: Progress text MUST reach the orchestrator only in the result of that check. What the orchestrator is told about pending tasks at the start of a run, and on every later step, MUST NOT hold it.

**What progress never does**

- **FR-015**: Learning, keeping, and showing progress MUST NOT cause any request to an agent that the service would not have made without this feature. The interval at which a pending task is checked, the single instance that watches it, the maximum age at which it is given up, and when and how its outcome is delivered (feature 003, FR-019 to FR-021) MUST NOT change.
- **FR-016**: Progress MUST NOT be written into a thread's conversation, MUST NOT start a run, MUST NOT end, pause, or delay one, and MUST NOT create or decide a proposal. It MUST be kept whether or not a run is in progress on the thread and whether or not a proposal waits for a decision.
- **FR-017**: Progress text MUST NOT decide anything in the service. The task's state alone says whether the task is working, has ended, or waits for input. Whether a message to an agent pauses for a person stays decided by the agent's approval choice alone (feature 005, FR-022).
- **FR-018**: This feature MUST add no route that changes something. The routes that return progress are routes that exist today, and their rules about pages of other origins (feature 007) are unchanged.

**A hand-off that asks for a task's status**

- **FR-019**: A hand-off that follows a task by asking for its status, because the agent does not stream, MUST put a progress update in the run's stream when a status read finds another state, as today, and also when it finds the same state with a progress text other than the one it last saw. A read that finds the same state and the same text, or no text, MUST put nothing in the stream. This extends feature 003, FR-007, which shows such an agent's changes of state only. Progress between two reads is still not seen.
- **FR-020**: For an agent that streams, what a run's stream shows of a hand-off MUST NOT change.

**Compatibility and quality**

- **FR-021**: An agent task recorded before this feature MUST be read as a task with no kept progress, and every client that does not read progress MUST be served as before: nothing is removed from, or renamed in, the thread's state.
- **FR-022**: Every behavior of the service that does not concern progress MUST be what it was, and every automated test that existed before this feature MUST pass, changed only where it pins the exact shape of an agent task in the thread's state, the exact result of the orchestrator's check, or the progress updates of a hand-off to an agent that does not stream.
- **FR-023**: The service's behavior and the web app's behavior MUST each be covered by automated tests that need no network and no agent installed or running, with the stand-in agent in the agent's place, able to report a progress text and to change it. The console's part MUST also be covered by an end-to-end test that drives the console in a browser. The tests MUST include: the count of requests an agent receives with progress and without; progress learned while a run is in progress and while a proposal is pending; a text longer than the limit; a text that holds markup; and a text that reads like an instruction.

### Key Entities

- **Agent task** (feature 003): gains its kept progress. Its agent, its message, its status, and its times are as before.
- **Kept progress**: The progress text the service last learned for one agent task and the time it learned it. At most one per agent task. It is the agent's own claim: it is shown, and it decides nothing.
- **Progress update** (feature 003): What a run's stream shows of a hand-off while it follows a task. Its form is unchanged; a hand-off that asks for a task's status now produces one for a changed text as well as for a changed state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the settings a new deployment has, a progress text an agent starts to report for a pending task is in the thread's state within 15 seconds, and on an open page of the console within 30 seconds, without a reload.
- **SC-002**: In 100% of automated runs, an agent receives exactly the requests it would have received without this feature: learning, keeping, and showing progress adds none.
- **SC-003**: In 100% of automated runs, learning progress adds no message to a thread's conversation, starts no run, and creates no proposal, including while a run is in progress and while a proposal waits for a decision.
- **SC-004**: A person looking at a task's page can tell, for every agent task that is working and whose agent reports progress, what the agent last reported and how long ago, without opening anything else.
- **SC-005**: A person who asks Freyja how a pending agent task is going gets an answer that reflects the agent's latest kept progress, in 100% of automated runs with zero requests to the agent caused by the question.
- **SC-006**: A progress text that holds markup, a script, or a link is shown as its characters in 100% of automated runs: nothing in it is rendered or run.
- **SC-007**: In 100% of automated runs, a progress text that reads like an instruction changes no decision: every action with an effect outside the workspace pauses for a person exactly as it does without the text.
- **SC-008**: After a restart, and through every instance sharing the database, each agent task shows the progress it had, in 100% of automated runs.
- **SC-009**: An agent that reports no progress text, and every client that reads no progress, see no difference: every automated test that existed before this feature passes, changed only as FR-022 lists.
- **SC-010**: In a run that hands a task to an agent that does not stream, each change of the agent's progress text that a status read sees appears in the stream as one progress update, and a read that sees no change adds none, in 100% of automated runs.

## Assumptions

**Scope**

- Out of scope, per the description: push notifications from an agent to the service; a stream of progress to the console for a task that is pending, which the page reads at intervals instead; a history of progress lines; structured progress such as a percentage or a progress bar; progress parts that are not text; and any change to when a task is checked, given up, or delivered.
- Also out of scope: progress in the list of tasks, which shows a task's status and not its agent tasks; progress in the Agents section; progress of a hand-off whose agent answered with a message and created no task; telling a client that progress changed; a setting for the length kept; removing kept progress, which lives as long as its agent task; and what an agent puts in its progress, which is the agent's own work.
- The console is in English and is used with a current desktop browser, as before.

**Behavior choices**

- The time kept is when the service first learned the text it keeps, and it does not move while the agent repeats that text (FR-003). The description says the text is kept "with the time it was learned". A time that moved at every check would say only that the service is still checking, which the time of the last check already says. A time that stays tells a task that moves from one that has reported the same thing for an hour, which is what the description asks a person to be able to tell. An agent that puts a running clock in its text reports a new text at every check, and its time then follows the checks.
- Only a working status gives progress (FR-002). What an agent attaches to a status that ends a task, or that asks for input, is the outcome feature 003 already reports to the orchestrator. Keeping it as progress as well would show an agent's question under "working", or show a final sentence as if the task went on.
- Progress is shown in the console only for an agent task that is working (FR-010), while the thread's state carries it for every agent task (FR-009). The page of a task whose agent task ended shows the outcome in the conversation; a last progress line beside it would read as the result. The thread's state keeps it because the description says the last progress stays with the task, and a client may want it.
- The progress text reaches the orchestrator in the result of its own check and nowhere else (FR-014). Text in such a result is treated as an agent's reply is. What the orchestrator is told at the start of a run is the service's own words, and an agent's text does not belong among them: it would be read on every step, by a model that then has less reason to tell it from an instruction.
- The text is not searched for an agent's address (FR-004). The description lists it among text an agent supplies, and feature 005, FR-028, lets an agent's replies and progress updates pass as written. Progress shown in a run's stream today already passes that way; keeping it does not change whose words they are.
- 2,000 characters are kept (FR-004), the length feature 005 keeps of each text of an agent's card. A progress line is a sentence or a few; the limit is there for the agent that sends a page. Nothing marks a cut, since a second field to say so would be read by nobody.
- A hand-off's last progress goes with the task when it is recorded (FR-006). Without it a task that just became pending would show nothing for up to one check interval, although the service had the text a moment before.
- Trust is unchanged. An agent is trusted with the words it reports and with nothing more: its progress is shown as its claim and decides nothing (FR-017). The model is not trusted to decide what needs approval, with or without an agent's text before it.

**Earlier requirements this feature changes**

- Feature 003, FR-007: for an agent that does not stream, a hand-off's progress updates now cover each change of the progress text a status read sees, beside each change of state (FR-019).
- Feature 003, FR-017: what the thread's state lists for an agent task gains its kept progress (FR-009). Nothing is removed.
- Feature 003, FR-018: the orchestrator's check of the thread's agent tasks lists kept progress for a working task (FR-013). What the orchestrator is told at the start of a run is unchanged (FR-014).
- Feature 007, FR-009: a task's page shows, for an agent task that is working, its kept progress beside its agent, its status, and its message (FR-010).

**Environment and dependencies**

- This specification is written with features 003, 005, and 007 as they are specified. Feature 007's last phases are being built while this is written; if its task page is finished differently from its specification, User Story 2 is read again before it is planned.
- The constitution needs no amendment. Principle I is untouched: progress proposes nothing and decides nothing, and the gate, its decisions, and its rules do not change. Principle II is served as before: progress is read over the protocol the agents are reached by, in the status reads it defines. Principle III holds: the console shows what the service returns and decides nothing with it. Principle VI is why FR-017 exists: whether a task is working is read from its state, never from what an agent says.
- Several service instances sharing one database are assumed to all run a version that knows kept progress. What an instance on an earlier version does with it is not specified.
- The stand-in agent of feature 003 plays every agent in the automated tests, extended so that it can attach a progress text to a working status and change it.
