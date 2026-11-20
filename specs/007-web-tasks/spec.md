# Feature Specification: Tasks Section of the Web Console

**Feature Branch**: `007-web-tasks`

**Created**: 2026-11-20

**Status**: Draft

**Input**: User description: "Tasks section of the web console. A Tasks entry in the sidebar, beside LLM and Agents, becomes the place where a person enters a task for Freyja, watches it run, decides the actions that need approval, and reads the result. Today Freyja receives work only as HTTP requests (POST /threads/{thread_id}/runs); the console manages LLM profiles and agents and cannot hand Freyja any work. The sidebar entry shows the number of tasks that wait for a decision. The section lists one row per task: its first message, its status (needs your decision, running, waiting for an agent, done, failed), and when it last changed, with tasks that need a decision first. "New task" opens one text box; sending it creates a thread and opens its page. A task's page is the thread: the conversation as it streams, a card for the proposed action when the run pauses (approve, edit the arguments, reject), any agent task still pending, and a box to send a follow-up on the same thread. The service already has the thread routes: start a run with a message, answered as a server-sent event stream (start, update, message, interrupt, error, end), resume with a decision for the pending proposal, and read a thread's state (messages, pending proposal, run_active, updated_at, agent_tasks). Missing today: no route lists threads, no route stops a run, and the thread routes do not turn away requests made by a page of another origin, which the /llm and /agents routes do. The console has never read an event stream; whether one passes the /api forwarding in both browsers is to be proven in the plan's research. Open decisions for clarification, not settled here: the section's name, since "task" already means a delegate agent's task in the code, the API, and the prompts; that the API has no users, so everyone who can open the console sees every task and can approve any pending action; one message per task or follow-ups on the same thread; and whether stopping a run is in the first version. The page offers approve, edit, and reject and adds no way around the approval gate; edits are checked by the service, not the page. The web app stays a client of the API and decides nothing the orchestrator does. Prefer the smallest version that works, and say what is left for later."

## User Scenarios & Testing *(mandatory)*

Three terms are used throughout. A **task** is one conversation with Freyja that a person started by handing it work: what the service calls a thread. An **agent task** is what a delegate agent works on for Freyja (feature 003); a task can have several. A **proposal** is one or more actions Freyja wants to take that need a person's approval; a task with a proposal is paused until a person decides it.

Five things in this draft are open and are asked in clarification, not settled here. Each is written below as a working choice so that the draft can be read as a whole, and is listed under Assumptions, "Open decisions".

### User Story 1 - Hand Freyja a task and read the result (Priority: P1)

A person opens the Tasks section, presses New task, types what they want done into one text box, and sends it. The console opens the task's page. Freyja's reply appears as it is written, with each step Freyja takes shown as it completes. When Freyja has finished, the page shows the whole conversation, and the person reads the result.

**Why this priority**: It is the reason for the feature: today the console cannot hand Freyja any work at all. A task that needs no approval can be entered, watched, and read with this story alone.

**Independent Test**: With the service running and a usable model active, open the console, press New task, send a message that needs no approval, and watch the reply appear before the run has ended. Read the task's page again after reloading it: the same conversation is there. Repeat with no usable model active: the page says that no usable model is active and points to the LLM section, and the text that was typed is still there to send again.

**Acceptance Scenarios**:

1. **Given** the Tasks section, **When** the person presses New task, **Then** one empty text box opens, with nothing left from an earlier use of it.
2. **Given** the text box with a message, **When** the person sends it, **Then** a new task is created, its page opens, and exactly one run starts with that message, also when Send was pressed twice.
3. **Given** a run started from the task's page, **When** Freyja writes its reply, **Then** the reply appears on the page as it is written, before the run ends, and each step Freyja completes is shown as it completes.
4. **Given** a run that finished, **When** the page shows the task, **Then** it shows the conversation as the service holds it: the person's message, Freyja's reply, and each action Freyja took with its arguments and its result, in order, and the status done.
5. **Given** a task's page, **When** it is opened again later, in another browser, or after the service was restarted, **Then** it shows the same conversation.
6. **Given** a run that ends with an error, **When** the page shows it, **Then** it says that the run failed and why, in the service's words, and when no usable model is active it points to the LLM section.
7. **Given** a message the service refuses, for example one that is too long, **When** it is sent, **Then** the page shows the service's reason, keeps the text, and no task appears in the list.
8. **Given** the service cannot be reached, **When** a message is sent, **Then** the page says so, keeps the text, and offers to try again.
9. **Given** text written by the model, by a tool, or by an agent that contains markup or a link, **When** it is shown, **Then** it appears as plain text: nothing in it is rendered as markup, loaded, or followed.

---

### User Story 2 - Decide an action that needs approval (Priority: P2)

Freyja wants to take an action with effects outside its workspace. The run pauses, and the task's page shows a card for the proposal: which action, where it goes, and its arguments in full. The person approves it as proposed, changes the arguments and approves, or rejects it with an optional reason. The run continues on the same page and Freyja reports what happened. The person can also come back hours later, or after a restart, and decide then.

**Why this priority**: The constitution makes a person's approval non-negotiable, and until now a decision could only be sent as a hand-written request. This story is what makes the console usable for real work; it needs story 1 to have a task to pause.

**Independent Test**: Send a task that leads Freyja to propose sending a notification. The page shows the card and nothing was sent. On three separate tasks, approve, edit the channel and approve, and reject with a reason: the service's log shows the notification as proposed, as edited, and not at all. Enter arguments the action cannot accept: the service refuses, the page shows why, the edit is kept, and the proposal is still there. Open a paused task in two windows and decide in one: the other is told that the proposal was already decided when it tries.

**Acceptance Scenarios**:

1. **Given** a run that proposes an action needing approval, **When** the run pauses, **Then** the action has not run and the page shows a card naming the action, the tool server or agent it goes to when there is one, its description, and every argument in full.
2. **Given** a card, **When** the person presses Approve, **Then** the action runs once with the proposed arguments and the continuation streams on the same page.
3. **Given** a card, **When** the person changes the arguments and approves, **Then** the action runs with the changed arguments, and the conversation shows both what was proposed and that a person changed it.
4. **Given** a card with changed arguments the action cannot accept, **When** the person approves, **Then** the service refuses, the page shows the service's reason beside the edit, the edit is kept, nothing ran, and the proposal can still be decided.
5. **Given** a card, **When** the person rejects, with or without a reason, **Then** the action never runs and Freyja continues and says that it was not taken, with the reason when one was given.
6. **Given** a proposal with several actions, **When** the card is shown, **Then** each action has its own decision, and nothing is sent until every action has one.
7. **Given** an action for which the service allows only some decisions, **When** its card is shown, **Then** only those are offered.
8. **Given** a paused task, **When** the person opens it after any delay, also after the service and the browser were restarted, **Then** the card shows the same proposal and a decision is accepted.
9. **Given** a card on a page that is no longer current, because the proposal was decided elsewhere or another proposal has taken its place, **When** the person decides, **Then** nothing runs because of it, the page says that the proposal is no longer the pending one, and it shows the task as it is now.
10. **Given** a card, **When** the person presses Enter in one of its fields, presses a button twice, or sends a message, **Then** no decision is sent by the Enter or the message, and at most one by the two presses.
11. **Given** a paused task, **When** the person looks for a way to send it a new message, to approve every later proposal at once, or to stop being asked, **Then** there is none.

---

### User Story 3 - See every task and which ones wait for a decision (Priority: P3)

The person opens the Tasks section and sees one row per task: how its first message begins, its status, and when it last changed. Tasks that need a decision come first. From any section of the console, the Tasks entry of the sidebar shows how many tasks wait for a decision, so a paused task is not missed while the person looks at something else.

**Why this priority**: Without the list a task can only be found again by its address, and a task that pauses while nobody watches, for example when an agent's outcome arrives hours later, would wait unseen. It comes after the first two stories because a single task can be entered and decided without it.

**Independent Test**: Create four tasks so that one waits for a decision, one waits for an agent, one is done, and one failed. Open the Tasks section: four rows, the one that needs a decision first, each with the right status and time. Open the LLM section: the sidebar's Tasks entry shows 1. Decide the proposal; within fifteen seconds, without reloading, the number is gone and the row's status has changed. Stop the service: the section says it cannot be reached and shows no task and no number as if they were current.

**Acceptance Scenarios**:

1. **Given** tasks exist, **When** the person opens the Tasks section, **Then** it shows one row per task with the beginning of its first message, its status, and when it last changed, and each row opens the task's page.
2. **Given** tasks in several statuses, **When** the list is shown, **Then** the tasks that need a decision come first, and the others follow with the most recently changed first.
3. **Given** no task exists, **When** the person opens the Tasks section, **Then** the page says so and offers New task.
4. **Given** one or more tasks need a decision, **When** the person opens the console on any section, **Then** the Tasks entry of the sidebar shows how many, in a way that does not rest on color alone; with none, it shows no number.
5. **Given** the console is open on any section, **When** a task comes to need a decision or stops needing one, **Then** the number and the list follow within fifteen seconds, without the page being reloaded.
6. **Given** a thread that another client started through the service's API, **When** the list is shown, **Then** it is a row like any other and can be opened and decided.
7. **Given** the service cannot be reached, **When** the Tasks section is opened, **Then** it says so and offers to try again, and neither it nor the sidebar shows tasks or a number as if they were current.
8. **Given** more tasks exist than the list shows, **When** the list is shown, **Then** it says so, every task that needs a decision is in it, and the sidebar's number counts all of them.

---

### User Story 4 - Continue a task (Priority: P4)

A task is not over after one reply. The person sends a follow-up on the same task, and Freyja answers with the whole conversation in mind. When Freyja has handed work to a delegate agent and the agent is still working, the page lists that agent task as pending, and when its outcome arrives, the conversation shows it without the person doing anything.

**Why this priority**: It makes a task a conversation and makes work handed to agents visible. It is last among the stories that add something to see because one message, one decision, and one result already work without it.

**Independent Test**: Finish a task, send a follow-up that refers to the first message, and confirm the reply reflects it. Send a task that hands work to the stand-in agent and stays pending: the page lists the agent task as pending and the task's status is waiting for an agent. Let the agent finish: within fifteen seconds the page shows the outcome in the conversation and the agent task as ended, without a reload.

**Acceptance Scenarios**:

1. **Given** a task with no run in progress and no proposal pending, **When** the person sends a follow-up from its page, **Then** a run starts on the same task and the reply reflects the earlier conversation.
2. **Given** a task with a run in progress or a proposal pending, **When** the person looks at the follow-up box, **Then** it cannot be sent and the page says why.
3. **Given** a task with agent tasks, **When** its page is shown, **Then** each agent task is listed with its agent, its status, and the message it was started with, and the pending ones are marked.
4. **Given** a task that waits for an agent, **When** the agent task's outcome arrives and the service records it, **Then** the page shows the new conversation and the agent task's final status within fifteen seconds, without a reload.
5. **Given** a task whose run is in progress but was not started from this page, **When** the page is shown, **Then** it says that a run is in progress, shows the conversation as far as the service holds it, follows it within fifteen seconds, and offers no way to send a message or a decision until the run has ended.
6. **Given** a run that this page is showing, **When** the person closes or leaves the page, **Then** the run ends at its next step, as it does today for any client that stops reading; the task is then listed as stopped, and a follow-up continues it.

---

### User Story 5 - Another site cannot hand Freyja work or decide for the person (Priority: P5)

The person has the console open and visits another web page in the same browser. That page can neither start a task, nor send a decision, nor read the list of tasks or a conversation.

**Why this priority**: The console turns approving into a press of a button, on a service that has no users. A page of another origin must not be able to press it. The rule adds no capability of its own, so it is listed last; it is still required before the section is released.

**Independent Test**: From a page served from another origin, try to start a run, to send a decision, and to read the list and a task: nothing starts, nothing is decided, and no answer can be read. The same requests made by the console and by a client that is not a web page work as before.

**Acceptance Scenarios**:

1. **Given** a page served from another origin, **When** it tries to start a run or to send a decision, in any form a page can send without asking the service first, **Then** the request is refused and nothing runs.
2. **Given** a page served from another origin, **When** it asks for the list of tasks or for a task, **Then** it can read no answer.
3. **Given** a client that is not a web page, **When** it uses the service's API as before this feature, **Then** it is served as before.

---

### Edge Cases

- **The person leaves a task's page while its run streams.** The run ends at its next step, because the service runs a task only while a client reads it (feature 001). Steps already completed are kept. The task is listed as stopped, not as done, and the page says that sending a follow-up continues it. While a run streams, the page says that leaving ends it. This is the working choice of open decision 5.
- **A run is in progress that this page did not start**: in another window, by another person, or by the service itself when it reports an agent's outcome. The page cannot join it. It shows what the service holds, says that a run is in progress, reads the task again until the run has ended, and offers nothing to send meanwhile.
- **Two people decide the same proposal.** The first decision is applied. The second is refused by the service, and that person's page says the proposal was already decided and shows the task as it is. Who decided is not recorded, since the service has no users.
- **The answer to a decision never arrives**, for example because the connection dropped. The decision may or may not have been applied. The page says that the outcome is not known, never that it failed, reads the task again, and shows the proposal only when it is still pending.
- **A run fails after the person's message was recorded.** The message is in the conversation and is not sent again by itself. The task is listed as failed with the reason, and the person may send another message.
- **A new task's first run fails before anything was recorded**, as when no usable model is active. There is no conversation to show. The page shows the reason and keeps the text so that it can be sent again, and the list shows no task without a message.
- **The step limit is reached.** The task is listed as failed with the service's reason, which names the limit; a follow-up continues it.
- **A very long message or very large arguments.** The list shows only the beginning of a first message. A task's page shows everything, and stays usable.
- **A proposal's action is no longer configured, or its agent was removed.** The card shows what the service says of it, and offers the decisions the service allows; what happens to the action then is the service's rule (features 002, 003, and 005).
- **The service is restarted while a run streams.** The run ends without a result. The page says the run ended without one, reads the task again, and lists it as stopped.
- **A thread from before this feature that has had no run since.** It may be missing from the list until its next run. It can still be opened by its address in the console and decided there.
- **The same task is open in two windows.** Each shows what the service holds. A message sent from one while the other's run is in progress is refused by the service, and the page says a run is in progress.
- **The Tasks section is opened in a narrow window.** Every control stays reachable, as in the LLM and Agents sections.

## Requirements *(mandatory)*

### Functional Requirements

**The section and the list**

- **FR-001**: The console MUST have a Tasks section, with an entry in the sidebar beside LLM and Agents. The entry MUST show the number of tasks that need a decision, in every section of the console, in words as well as in a number and never by color alone, and MUST show no number when there are none. The number counts every task of the service, not only the rows the list shows.
- **FR-002**: The section MUST list one row per task, with the beginning of the task's first message, its status (FR-003), and when it last changed, which is when the service last recorded a step of it. Tasks that need a decision MUST come first, then the others with the most recently changed first. A row MUST open the task's page. With no task, the section MUST say so. The section MUST have a New task control.
- **FR-003**: A task has exactly one status, the first of these that holds:
  1. **Running**: a run is in progress on it, on any service instance.
  2. **Needs your decision**: a proposal is pending.
  3. **Failed**: its most recent run ended with an error. The row and the page give the service's reason.
  4. **Stopped**: its most recent run ended before Freyja finished and without an error, because whoever was reading it left or the service stopped.
  5. **Waiting for an agent**: its most recent run finished and one of its agent tasks is pending.
  6. **Done**: its most recent run finished and nothing is pending.

  The status follows the most recent run whoever started it: this console, another client, or the service itself when it reports an agent's outcome.
- **FR-004**: Every thread of the service that has had a run since this feature was installed MUST be a task in the list, whichever client started the run. A thread whose last run was earlier MAY be missing from the list until its next run, and MUST still open by its address in the console.
- **FR-005**: The list MUST show at most 100 rows: every task that needs a decision, however many, and beyond those the most recently changed. When more tasks exist than it shows, it MUST say so. The list MUST carry at most the first 200 characters of a first message.
- **FR-006**: While the console is open, the list and the sidebar's number MUST follow a change made anywhere within fifteen seconds, without the page being reloaded. When the service cannot be reached, the section MUST say so and offer to try again, and neither the list nor the number MUST be shown as if it were current.

**Starting a task**

- **FR-007**: New task MUST open one text box, empty each time. Sending MUST create a task under an id the console chooses for it and has not used before, open the task's page, and start exactly one run with the text, also when the person presses Send more than once. The box MUST NOT send a message that is empty. Whether a message is acceptable is otherwise the service's decision.
- **FR-008**: When the run cannot be started, because the service cannot be reached or refuses the message, the page MUST say why with the service's reason, MUST keep the text, and MUST NOT list a task for it.

**A task's page**

- **FR-009**: A task's page MUST show: the conversation in order, with the person's messages, Freyja's replies, and each action Freyja took with its arguments and its result; the task's status; the pending proposal when there is one (FR-014); and the task's agent tasks, each with its agent, its status, and the message it was started with, the pending ones marked. A task that does not exist MUST be shown as not found.
- **FR-010**: While a run that the page started is in progress, the page MUST show Freyja's reply as it is written and each step as it completes. When the run ends, in whichever way, the page MUST read the task again and show the conversation as the service holds it; what was shown while the run streamed is replaced by it.
- **FR-011**: When a run ends with an error, the page MUST say that it failed and give the service's reason. When the reason is that no usable model is active, it MUST point to the LLM section. The task MUST stay usable: the person can send another message.
- **FR-012**: All text that comes from the model, a tool, an agent, or a tool server, including action names, descriptions, arguments, results, and reasons, MUST be shown as plain text: nothing in it is rendered as markup, loaded, or followed as a link.
- **FR-013**: A run that the page did not start cannot be joined. While one is in progress the page MUST say so, MUST show the conversation as far as the service holds it, MUST read the task again at least every fifteen seconds until the run has ended, and MUST offer no way to send a message or a decision meanwhile. A page that shows a task with a pending agent task MUST follow the task in the same way.

**Deciding a proposal**

- **FR-014**: When a task has a pending proposal, its page MUST show a card with every proposed action: its name, the tool server or the agent it goes to when the service names one, its description, and all of its arguments in full. For each action it MUST offer the decisions the service lists as allowed for that action, and no other.
- **FR-015**: The decisions are the service's three: approve, which runs the action as proposed; edit, which runs it with arguments the person entered in place of the proposed ones; and reject, with an optional reason of at most 4,000 characters. The page MUST send an edit as the person entered it. It MAY refuse to send arguments that are not well-formed; whether they are acceptable to the action MUST be decided by the service alone, and the service's refusal MUST be shown beside the edit, which is kept.
- **FR-016**: A proposal with several actions MUST get one decision per action, in the order the service lists them, sent together; nothing is sent until every action has one.
- **FR-017**: A decision MUST be sent only by a press of the control that names it. Pressing Enter in a field, opening or reloading a page, and sending a message MUST NOT send one, and a control pressed twice MUST send at most one. The console MUST NOT decide anything by itself and MUST offer no way to approve later proposals in advance, to stop being asked, or to send a new message to a task whose proposal is pending. It adds no way around the approval gate: every decision goes through the service's existing rules.
- **FR-018**: A decision MUST name the proposal the page showed when the person decided. When the service refuses it because that proposal is no longer the pending one, because nothing is pending, or because a run is in progress, the page MUST say so, MUST NOT send it again, and MUST show the task as it is now. When no answer to a decision arrives, the page MUST say that the outcome is not known and read the task again.
- **FR-019**: After a decision is accepted, the continuation MUST be shown on the same page as a run is (FR-010, FR-011), including when it pauses on another proposal.

**Continuing, leaving, and stopping**

- **FR-020**: A task's page MUST have a box to send another message on the same task. It MUST be usable when no run is in progress and no proposal is pending; otherwise it cannot be sent and the page says why. (Working choice of open decision 3.)
- **FR-021**: A run lasts only while the page that started it is open, as the service's API already behaves for any client. While a run streams, the page MUST say that leaving ends it. A run that ended this way MUST be shown as stopped, with the steps it completed, and the page MUST say that another message continues the task. (Working choice of open decision 5.)
- **FR-022**: The first version has no control that stops a run. (Working choice of open decision 4.)

**The service**

- **FR-023**: The service MUST give the console what it lacks today: the list of tasks with what FR-002 to FR-005 need, and the number of tasks that need a decision. Answering either MUST contact no model, no agent, and no tool server, and MUST change no thread. The list MUST be the same from every service instance that shares the database and MUST survive a restart.
- **FR-024**: A page served from another origin MUST NOT be able to start a run or send a decision, in any form of request a page can send without the service's permission, and MUST NOT be able to read the list, a task, or a run. The rule and its refusal are the ones the LLM and Agents routes already apply (feature 004, FR-003a; feature 005, FR-029). A client that is not a web page MUST be served as before.
- **FR-025**: What the service returns for the list holds text from threads. A stored API key MUST NOT appear in it beyond its last four characters, and an agent's address MUST NOT appear in it, as in every other answer of the service (feature 004, FR-020; feature 005, FR-028). The searches that prove this for the existing routes MUST cover the new ones.
- **FR-026**: The web app MUST reach tasks only through the service's API, on the origin it is served from, as the LLM and Agents sections do. It MUST hold no credential, call no model, agent, or tool itself, and decide nothing the orchestrator does.

**Compatibility and quality**

- **FR-027**: Starting a run, reading a thread's state, and deciding a proposal MUST behave for a client that is not a web page exactly as before this feature, and every existing automated test MUST pass unchanged, apart from tests that gain setup because every page of the console now asks for the number of tasks that need a decision.
- **FR-028**: The service's part and the web app's part MUST each be covered by automated tests that need no network, no model provider, and no agent installed or running. The console's part of every user story MUST also be covered by an end-to-end test in a browser, beside its unit tests. That a run's stream reaches the page through the console's own origin as it is produced, and not all at once when it ends, MUST be shown in both browsers the end-to-end tests use.

### Key Entities

- **Task**: One conversation with Freyja, started by a person's message; the service's thread, under the name the console gives it. It has an id the client chose, a first message, a status, the time it last changed, a conversation, at most one pending proposal, and any number of agent tasks.
- **Status**: One of six: running, needs your decision, failed, stopped, waiting for an agent, done (FR-003). It is read from what the service holds; the console keeps none.
- **Run**: One turn of work on a task, started by a message or by a decision, or by the service when it reports an agent's outcome. It ends finished, paused on a proposal, failed, or stopped. As in feature 001; new here is that how the most recent run ended is part of a task's status.
- **Proposal**: As in feature 001: one or more actions waiting for a person, each with a name, where it goes, arguments, and the decisions allowed for it.
- **Decision**: As in feature 001: approve, edit with replacement arguments, or reject with an optional reason, one per proposed action. Who decided is not recorded.
- **Agent task**: As in feature 003: what a delegate agent works on for a task. Shown on the task's page; not changed by this feature.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A person with the console open can hand Freyja a task and see the beginning of its reply in under one minute, without leaving the console and without writing a request by hand. The page shows that the run started within two seconds of sending.
- **SC-002**: In the automated suite, 100% of actions that need approval and are proposed in a task started from the console pause and show a card, and zero run without an approve or edit that a person's press sent.
- **SC-003**: Each of approve, edit, and reject, sent from the console, has the effect the service defines for it in 100% of automated runs, and an edit the action cannot accept changes nothing in 100% of them.
- **SC-004**: While the console is open on any section, a task that comes to need a decision is announced on the sidebar within fifteen seconds, in 100% of automated runs.
- **SC-005**: A decision sent from a page that no longer shows the pending proposal, or sent twice, runs nothing a second time, in 100% of automated runs.
- **SC-006**: A task paused before the service and the browser were restarted can be found from the list alone and decided, with the same outcome as without the restart.
- **SC-007**: A page served from another origin starts zero runs, sends zero decisions that are applied, and reads zero answers about tasks.
- **SC-008**: A recognizable stored API key and a recognizable agent address appear in nothing the service returns for the list of tasks, through tasks that finish, pause, and fail.
- **SC-009**: With 100 tasks, the Tasks section shows its list within two seconds on the machine the service runs on.
- **SC-010**: A deployment used only through the API behaves as before: every automated test that existed before this feature passes, changed only as FR-027 says.

## Assumptions

**Open decisions**

Each is written into the draft as a working choice and is to be confirmed or changed in clarification.

1. **The section's name.** The draft says Tasks and task, as the description does. "Task" already means a delegate agent's task in the code, the API, and the prompts (`agent_tasks`, `check_agent_tasks`), and Freyja's own replies speak of an agent's "task". The draft keeps the two apart by always saying "agent task" for the second.
2. **No users.** The service has no authentication, so everyone who can open the console sees every task with its conversation and can approve any pending action, and nothing records who decided. The draft accepts this for the first version, as features 001, 004, and 005 did for what they added, with the service bound to this machine. New with this feature: tasks can be listed, where until now a thread could only be read by someone who knew its id.
3. **Follow-ups.** The draft lets a person send more messages on the same task (FR-020), as the description's sketch does. The alternative is one message per task.
4. **Stopping a run.** The draft has no Stop control (FR-022). The service has no way to stop a run on request; a run does end at its next step when its reader leaves.
5. **A run and its page.** Not among the description's four, and found while writing: the service runs a task only while a client reads it, so a task entered in the console stops when its page is closed or left (FR-021). The draft keeps that and says so on the page, because letting a run go on without a reader is a change to how the service runs every task. It is asked because a person who expects a task to keep going after they close the tab will be surprised.

**Scope**

- Out of scope for the first version, and left for later: authentication and a record of who decided; stopping a run from the console (open decision 4); a run that goes on after its page is closed, and joining a run that is already in progress (open decision 5); deleting, archiving, renaming, searching, filtering, and paging tasks; attachments and anything but plain text in a message; showing Freyja's text as formatted text; a form built from an action's own description of its arguments, in place of editing them as text; the debug stream; notifications outside the console; showing the files Freyja wrote in its workspace; and choosing a model or a set of agents per task.
- Not changed: the approval gate, its three decisions and their rules; the run and its stream; the thread's state; agent tasks and how their outcomes are reported; LLM profiles and the Agents section.
- The console is in English and is used with a current desktop browser, as in features 004 and 005.

**Behavior choices**

- "Stopped" is a sixth status the description's sketch does not have. Without it, a task whose run ended because its page was closed would be listed as done, which it is not, or as failed, which blames an error that did not happen.
- The order of FR-003 puts failed and stopped above waiting for an agent. A task whose last run did not finish needs the person; one that only waits for an agent does not.
- A thread from before this feature may be missing from the list until its next run (FR-004). How a run ended was not recorded until now, so its status cannot be told. The plan may do better; the specification does not require it.
- Edited arguments are entered as text, in the structured form the proposal shows them in (FR-015). The page checks at most that the text is well-formed, which decides nothing about the action. Everything else is the service's check, as today.
- An approval is not confirmed a second time. The card shows the whole action and Approve is a press of its own (FR-017); a confirmation on top would be pressed without being read.
- The console chooses a task's id (FR-007), since the service leaves a thread's id to its client. It is random and long enough that two tasks do not share one, and it says nothing about the task.
- The page shows the service's state as the truth once a run has ended (FR-010). While a run streams, text written by a subagent cannot be told from Freyja's own (feature 001), so what streams is shown as work in progress, and the conversation read afterwards is what counts.
- Limits chosen here: 100 rows in the list, 200 characters of a first message in a row, and fifteen seconds for the list, the number, and a page that follows a task it is not streaming.
- A message is never a decision, and a task whose proposal is pending takes no new message, as the service already rules (feature 001, FR-011).

**Not proven**

- The console has never read an event stream. That a run's stream passes the console's forwarding of `/api/...` as it is produced, in both browsers, and that a page which leaves ends the run through that forwarding as it does for a direct client, are to be proven in the plan's research before anything is designed on them. Also to be measured there: how long the forwarding keeps a quiet stream open, since a run can spend a long time in one step. If either does not hold, FR-010 and FR-021 are revisited before the plan goes on.

**Environment and dependencies**

- The constitution needs no amendment. Principle I: the page offers the service's three decisions and adds no way around the gate; an edit is checked by the service. Principle III: the web app is a client of the API and decides nothing the orchestrator does. Principle VI: nothing here asks a model whether an action needs approval.
- The service keeps binding to the local interface with no authentication. With this feature, anyone who can reach the console can read every task and decide every pending proposal with a press, where before they needed a thread's id and a hand-written request. That is acceptable for local use, is documented, and must be revisited before the service is bound more widely (open decision 2).
- Several service instances may share one database, as before. The list and the number are the same from each (FR-023).
- The existing thread operations, the approval gate, the run lease, the step limit, agent tasks, and the delegation worker are reused. This feature adds the list of tasks, the number, the refusal of other origins on the thread operations, and the Tasks section.
- Feature 005 is still being built and feature 006 follows it, by another session, on the same `main`. This feature changes nothing they specify.
- The stand-in model and the stand-in agent play the model and every agent in the automated tests, as today.
