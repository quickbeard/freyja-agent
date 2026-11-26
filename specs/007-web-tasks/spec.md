# Feature Specification: Tasks Section of the Web Console

**Feature Branch**: `007-web-tasks`

**Created**: 2026-11-20

**Status**: Draft

**Input**: User description: "Tasks section of the web console. A Tasks entry in the sidebar, beside LLM and Agents, becomes the place where a person enters a task for Freyja, watches it run, decides the actions that need approval, and reads the result. Today Freyja receives work only as HTTP requests (POST /threads/{thread_id}/runs); the console manages LLM profiles and agents and cannot hand Freyja any work. The sidebar entry shows the number of tasks that wait for a decision. The section lists one row per task: its first message, its status (needs your decision, running, waiting for an agent, done, failed), and when it last changed, with tasks that need a decision first. "New task" opens one text box; sending it creates a thread and opens its page. A task's page is the thread: the conversation as it streams, a card for the proposed action when the run pauses (approve, edit the arguments, reject), any agent task still pending, and a box to send a follow-up on the same thread. The service already has the thread routes: start a run with a message, answered as a server-sent event stream (start, update, message, interrupt, error, end), resume with a decision for the pending proposal, and read a thread's state (messages, pending proposal, run_active, updated_at, agent_tasks). Missing today: no route lists threads, no route stops a run, and the thread routes do not turn away requests made by a page of another origin, which the /llm and /agents routes do. The console has never read an event stream; whether one passes the /api forwarding in both browsers is to be proven in the plan's research. Open decisions for clarification, not settled here: the section's name, since "task" already means a delegate agent's task in the code, the API, and the prompts; that the API has no users, so everyone who can open the console sees every task and can approve any pending action; one message per task or follow-ups on the same thread; and whether stopping a run is in the first version. The page offers approve, edit, and reject and adds no way around the approval gate; edits are checked by the service, not the page. The web app stays a client of the API and decides nothing the orchestrator does. Prefer the smallest version that works, and say what is left for later."

## Clarifications

### Session 2026-11-20

- Q: What should the new section of the console, and the thing a person enters in it, be called, given that "task" already means a delegate agent's task in the code, the API, and the prompts? → A: Tasks. In the console a delegate agent's task is always written "agent task". The API and the code keep "thread" for a task and `agent_tasks` for the other kind; nothing is renamed.
- Q: The API has no users, so everyone who can open the console sees every task and can decide any pending proposal. What does the first version do about that? → A: It accepts it, as features 001, 004, and 005 did for what they added. The service stays bound to this machine, the section and the README say what anyone who reaches the console can do, and nothing records who decided. Authentication is a later feature.
- Q: Can a person send more messages on a task after the first, or is a task one message and its outcome? → A: Follow-ups. A task's page has a box that sends another message on the same task, usable when no run is in progress and no proposal is pending. It is also how a task whose run failed or stopped is continued.
- Q: The service runs a thread only while a client reads its stream. What happens to a running task when the person closes or leaves its page? → A: It goes on. A run the console starts, with a message or with a decision, runs to its end whether or not any page is reading it, and the page shows where the task stands when the person comes back. A run started the way the API offers today keeps today's behavior. Keeping today's behavior for the console as well, with a warning on the page, was considered and not adopted.
- Q: Since closing the page no longer ends a run, does the first version have a Stop control? → A: Yes, for runs the console started. Stop ends the run after the step under way; completed steps are kept, the task is listed as stopped, and a follow-up continues it. Runs started by other clients and the runs the service starts itself cannot be stopped yet. No Stop control, and a stop for every run, were considered and not adopted.

### Plan, 2026-11-20

Changes made while planning, where the research found that the spec asked for more than can be known or shown ([plan.md](plan.md), "Changes to the spec"). None was asked of the owner; each is named in the plan's pull request.

- The reason of a failed run (FR-003) is given for a run started through the console. How a run ended is known only to whoever read its stream; a run another client or the service itself started, and that failed, is listed as stopped ([research.md](research.md) R7).
- When a task last changed (FR-002) is, while a run is in progress, when that run started (R7).
- That a stream reaches the page as it is produced (FR-028) was shown by hand in both browsers (R1). It holds only when the service's answer says that it must not be transformed and stays active, which automated tests of the service pin; the end-to-end tests did not repeat the proof when this was written, and do since Phase 4 (`web/e2e/stream.spec.ts`).
- Two edge cases were added: a stop applied before an approved action started, and the step limit of a run that goes on without a reader (R4).
- The three things listed as not proven are settled (Assumptions, "Proven in the plan's research").

### Checklist review, 2026-11-20

Changes made while reviewing [checklists/approval-runs.md](checklists/approval-runs.md); its "Review record" gives the finding behind each. None was asked of the owner; each is named in the checklist's pull request.

- A service that is stopped in the ordinary way lets the step under way of each console run complete, as a stop a person asked for does, and cuts no action off (FR-021). The plan had such runs cancelled.
- A stream that ends without the run's last event says nothing about the run, and the page does not show the run as failed (FR-010).
- Keeping the list never fails or changes a run, and a list that cannot be read is answered with an error (FR-023).
- A thread from before this feature is in the list from the moment it is opened in the console (FR-004).
- "Step" and "console run" are defined, and a console run is any run started the new way, by the console or by another client.
- Smaller ones: the list's limit (FR-005), where the notice about no users stands (FR-026a), what an edit must be to be sent (FR-015), a stop that comes too late (FR-022a), keyboard and names (FR-012a), plain text for all of a task's text (FR-012), an id the service does not accept (FR-009), the log (FR-021, FR-022a), SC-011, and a section on the earlier requirements this feature replaces.

### Analysis, 2026-11-20

Changes made after `/speckit-analyze` cross-checked the spec, the plan, and the tasks; the plan's "Analysis" section lists every finding. None was asked of the owner; each is named in the pull request.

- An ordinary stop of the service ends an open stream at once, with the installed versions; this was seen in a run. The checklist review had assumed that the service waits for a run whose reader is connected. What it decided stands and matters more: a console run is let finish its step (FR-021), where a run started the earlier way is cut. The sentences that repeated the assumption are corrected.
- FR-008 covers a new task whose first run ends with an error before anything was recorded: the reason is shown and the text is offered again.
- FR-027 names the tests of the sidebar as changed, not only as given more setup.
- FR-022a says which page can tell that a stop came too late: the one that asked for it.
- The limits list gains the three numbers the plan and the checklist review had added.

### Convergence, 2026-11-21

Changes made in Phase 11, after `/speckit-converge` read the code against this document;
the plan's "Phase 11 as built" lists every finding.

- FR-001a holds for what this feature adds to the console: the Tasks section, a task's page, and the sidebar's Tasks entry. The owner decided this on 2026-11-21. The Agents section, which feature 005 built before this feature chose its words, still writes "pending task" for an agent task and "thread" for a task; rewording it is that section's to do, and is recorded as open in the plan.
- FR-013 says what a page that is hidden does: it reads nothing, and reads the task again when it is shown.
- The edge case of a list that cannot be kept says what holds: a run is never failed for the list, and is held up for it by five seconds at most each time the list is written or looked up for it, which is three times before a run started through the console sends its first event.
- That a stream reaches the page as it is produced is shown by an end-to-end test since Phase 4 (`web/e2e/stream.spec.ts`); the note of 2026-11-20 below says that it was not.

Changes made in Phase 12, after the second `/speckit-converge`; the plan's "Phase 12 as
built" lists every finding.

- FR-014 and FR-015 say what holds for a whole number beyond 2^53, such as a 64-bit id. A browser that gives a number's own text when it reads JSON, as the two the console is tested in do, shows such an argument digit for digit and sends it as it was entered. One that does not cannot: it says on the card that such an argument may be shown inexactly, and does not send an edit that holds one. Approving sends no arguments, so it is exact in either.
- FR-006 says, as FR-013 does, that a console that is hidden reads nothing, and reads again when it is shown.
- FR-007 says "blank": the box sends no message that is only spaces and line breaks.
- FR-022a says what a page that follows a run by reading says when the row is already of a later run: nothing of how the earlier one ended.

Changes made in Phase 13, after the third `/speckit-converge`; the plan's "Phase 13 as
built" lists every finding.

- FR-014 and FR-015 keep a number's value, not its spelling. A number written otherwise than the browser writes it, such as `5.0` or `1e2`, is shown and sent back as `5` and `100`, the same numbers. Only a whole number beyond 2^53 is kept as it was written, since its value would change.
- The edge case of two people deciding the same proposal says what the second page says while the first decision's run is in progress: that the proposal was most likely decided in another window.

Changes made in Phase 14, after the fourth `/speckit-converge`; the plan's "Phase 14 as
built" lists every finding.

- FR-002 says what a row first made while a run is in progress shows as the time it changed: its last recorded step, since the service does not know when that run started.
- FR-024 and FR-027 name the one change for a client that is not a web page: a request made for a host name the service does not answer for is refused, whoever sends it.

### Owner's change, 2026-11-26

Made after the feature was finished, at the owner's request: a proposal's card stood between
the conversation and the rest of the page, with the task's agent tasks and the box for
another message under it, so a person who looked at the end of the page did not see that the
task waited for them.

- FR-014 places the card: the last part of the task's page, after the conversation and the
  task's agent tasks, brought into view once when it appears.
- FR-020 says what becomes of the box meanwhile: the card takes its place, and what was typed
  in it is kept for when the proposal is decided. The box still says why it cannot be sent
  when a run is in progress, and when the page knows of a proposal it cannot show.

## User Scenarios & Testing *(mandatory)*

Five terms are used throughout. A **task** is one conversation with Freyja that a person started by handing it work: what the service calls a thread. An **agent task** is what a delegate agent works on for Freyja (feature 003); a task can have several. A **proposal** is one or more actions Freyja wants to take that need a person's approval; a task with a proposal is paused until a person decides it. A **step** is one reply of the model, or one round of the actions a reply asked for; the actions one reply asks for run in one round. A **console run** is a run started in the way FR-021 adds, which goes on without a reader. The console starts every run that way, and any other client may; "a run the console started" below means a console run, whoever sent the request.

Five decisions were the owner's and were taken in clarification on 2026-11-20: the section's name, that the console has no users, follow-ups, what happens to a run when its page is closed, and stopping a run. They are in Clarifications above and under Assumptions, "Decisions taken in clarification".

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

1. **Given** a run that proposes an action needing approval, **When** the run pauses, **Then** the action has not run and the page shows a card naming the action, the tool server or agent it goes to when there is one, its description, and every argument in full, as the last part of the page, brought into view.
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

---

### User Story 5 - Leave a task running, come back to it, or stop it (Priority: P5)

A task can take longer than a person wants to watch. They close the page, or the browser, and Freyja keeps working. When they come back, the task's page shows where it stands: still running, waiting for their decision, finished, or failed. When a task is going wrong, they press Stop: Freyja finishes the step it is on and takes no further one, and the task can be continued later with another message.

**Why this priority**: A person hands over work so as not to sit beside it, and without a way to stop it a run that goes wrong would end only at the step limit. It is listed after the stories above because each of them can be shown with the page left open; it is still part of the first version, and it decides how the console starts every run.

**Independent Test**: Send a task that takes several steps, with one that proposes an action. Close the page as soon as the run has started. Wait, then open the Tasks section: the task needs a decision, and the sidebar says so. Open it and approve; close the page again at once. Open it later: the action ran once and the task is done, with the same conversation a task has whose page stayed open. Send a task that takes many steps and press Stop: the step under way completes and is in the conversation, no further step starts, the task is listed as stopped, and a follow-up continues it.

**Acceptance Scenarios**:

1. **Given** a run the console started, with a message or with a decision, **When** the person closes, leaves, or reloads the page, or the connection is lost, **Then** the run goes on and ends as it would have with the page open: finished, paused on a proposal, or failed.
2. **Given** a task whose run is in progress and whose page was just opened, in this window or another, **When** the page is shown, **Then** it says that a run is in progress, shows the conversation as far as the service holds it, follows it within five seconds without a reload, and offers no way to send a message or a decision until the run has ended.
3. **Given** a run that went on without a page and paused on a proposal, **When** the person opens the console on any section, **Then** the sidebar counts the task among those that need a decision, and its page shows the card.
4. **Given** a run the console started that is in progress, **When** the person presses Stop on the task's page, **Then** the step under way completes and is recorded, no further step starts, the page shows the task as stopped with everything the run completed, and the follow-up box can be used.
5. **Given** a run that is under way on an action a person approved, **When** Stop is pressed, **Then** the action is not cut off: it completes and its result is in the conversation, and it is not proposed or run a second time because of the stop.
6. **Given** a task that was stopped, **When** the person sends a follow-up, **Then** a run starts on the same task with the conversation as it was left.
7. **Given** a run in progress that the console did not start, by another client or by the service when it reports an agent's outcome, **When** the task's page is shown, **Then** it offers no Stop and says why.
8. **Given** a task with no run in progress, also one that is paused on a proposal, **When** its page is shown, **Then** there is no Stop; a proposal is declined by rejecting it.
9. **Given** Stop was pressed just as the run ended by itself, or was pressed twice, or by two people, **When** the service answers, **Then** nothing else changes: the page shows the task as it is, and never as stopped when its run finished.

---

### User Story 6 - Another site cannot hand Freyja work or decide for the person (Priority: P6)

The person has the console open and visits another web page in the same browser. That page can neither start a task, nor send a decision, nor stop a run, nor read the list of tasks or a conversation.

**Why this priority**: The console turns approving into a press of a button, on a service that has no users. A page of another origin must not be able to press it. The rule adds no capability of its own, so it is listed last; it is still required before the section is released.

**Independent Test**: From a page served from another origin, try to start a run, to send a decision, to stop a run, and to read the list and a task: nothing starts, nothing is decided, nothing stops, and no answer can be read. The same requests made by the console and by a client that is not a web page work as before.

**Acceptance Scenarios**:

1. **Given** a page served from another origin, **When** it tries to start a run, to send a decision, or to stop a run, in any form a page can send without asking the service first, **Then** the request is refused, nothing runs, and nothing stops.
2. **Given** a page served from another origin, **When** it asks for the list of tasks or for a task, **Then** it can read no answer.
3. **Given** a client that is not a web page, **When** it uses the service's API as before this feature, **Then** it is served as before.

---

### Edge Cases

- **The person leaves a task's page while its run is in progress.** The run goes on (FR-021). Nothing about the task depends on a page being open: it finishes, pauses, or fails as it would have, and the list and the sidebar show where it stands.
- **A run is in progress whose stream this page is not reading**: the page was opened or reloaded after the run started, or the run was started in another window, by another person, by another client, or by the service itself when it reports an agent's outcome. The page cannot join the stream. It shows what the service holds, says that a run is in progress, reads the task again until the run has ended, and offers nothing to send meanwhile. It offers Stop when the console started the run.
- **Stop is pressed while an action is under way.** The step completes and is recorded before the run ends (FR-022). An action a person approved is therefore never left having taken effect without its result in the conversation, and never shown as still waiting for a decision after it ran.
- **Stop is applied before an action that was just approved has started.** The action does not start. The decision is spent, no proposal is pending, and the conversation shows the action as not carried out once the task is continued. Nothing ran without approval and nothing ran twice; the person who pressed Stop gets what Stop says.
- **Stop is pressed while a step takes long**, such as a model that is slow to answer or an agent that is working. The page says that the run is stopping and shows it as stopped only once the service says the run has ended, which can take as long as that step is allowed to.
- **A run the console did not start is in progress.** It has no Stop in the first version: a run another client started ends when that client stops reading, as today, and a run the service started to report an agent's outcome ends by itself.
- **The service is stopped while a run goes on without a page.** Stopped in the ordinary way, the service treats the run as if a person had asked to stop it: the step under way completes and is recorded, no further step starts, and the task is listed as stopped (FR-021). Stopping the service can therefore take as long as one step is allowed to. A page that was reading the run's stream sees it end at once, without its last event, and follows the task by reading once the service is back (FR-010). Nothing starts the run again by itself; a follow-up continues it.
- **The service is killed or fails while a run goes on.** The step under way is lost, as feature 001 accepts for any run: an action in it can have taken effect without its result being recorded, and its proposal can show as pending again. Steps already completed are kept. Once the run's marker expires, the task is listed as stopped, and a follow-up continues it.
- **Stop is pressed and the run then pauses on a proposal**, because the reply that was under way asked for an action that needs approval. Nothing ran without approval. The task needs a decision, is not shown as stopped, and the page says that the run ended before the stop took effect (FR-022a).
- **The list cannot be kept or read.** A run is never failed for it, and is held up for it by five seconds at most each time the list is written or looked up for it (FR-023). A thread whose run could not be recorded is missing from the list until its next run or until it is opened. While the list cannot be read, the section and the sidebar say so and show nothing as current.
- **A page does not keep up with a run's stream**, because its connection is slow. The service disconnects it from the stream; the run goes on, and the page follows the task by reading it again (FR-010, FR-013).
- **Many tasks are started at once.** Each runs to its end without anyone watching. No limit on how many run at the same time is added in the first version; each is bounded by the step limit, as today.
- **Two people decide the same proposal.** The first decision is applied. The second is refused by the service, and that person's page says the proposal was already decided, or, while the first decision's run is in progress, that it was most likely decided in another window, and shows the task as it is. Who decided is not recorded, since the service has no users.
- **The answer to a decision never arrives**, for example because the connection dropped. The decision may or may not have been applied. The page says that the outcome is not known, never that it failed, reads the task again, and shows the proposal only when it is still pending.
- **A run fails after the person's message was recorded.** The message is in the conversation and is not sent again by itself. The task is listed as failed with the reason, and the person may send another message.
- **A new task's first run fails before anything was recorded**, as when no usable model is active. There is no conversation to show. The page shows the reason and keeps the text so that it can be sent again, and the list shows no task without a message.
- **The step limit is reached.** The task is listed as failed with the service's reason, which names the limit; a follow-up continues it. A run that goes on without a reader reaches the limit at a pause between two steps, which can be up to two steps earlier than the same run through the API as it is today; the limit is never exceeded.
- **A very long message or very large arguments.** The list shows only the beginning of a first message. A task's page shows everything, and stays usable.
- **A proposal's action is no longer configured, or its agent was removed.** The card shows what the service says of it, and offers the decisions the service allows; what happens to the action then is the service's rule (features 002, 003, and 005).
- **The service is restarted while a page reads a run's stream.** The stream ends without a result. The page says so, reads the task again, and shows it as the service has it: stopped, once no run is in progress.
- **A thread from before this feature that has had no run since.** It may be missing from the list until its next run. It can still be opened by its address in the console and decided there.
- **The same task is open in two windows.** Each shows what the service holds. A message sent from one while the other's run is in progress is refused by the service, and the page says a run is in progress.
- **The Tasks section is opened in a narrow window.** Every control stays reachable, as in the LLM and Agents sections.

## Requirements *(mandatory)*

### Functional Requirements

**The section and the list**

- **FR-001**: The console MUST have a Tasks section, with an entry in the sidebar beside LLM and Agents. The entry MUST show the number of tasks that need a decision, in every section of the console, in words as well as in a number and never by color alone, and MUST show no number when there are none. The number counts every task of the service, not only the rows the list shows.
- **FR-001a**: In what this feature adds to it, the Tasks section, a task's page, and the sidebar's Tasks entry, the console MUST call what a person hands Freyja a task, and what a delegate agent works on an agent task, in every place it names either: a heading, a status, a count, a message of its own. It MUST NOT call an agent task a task. What Freyja and the agents write themselves is shown as written. The service's API and its existing names are not changed by the console's choice of word: a task is a thread there.
- **FR-002**: The section MUST list one row per task, with the beginning of the task's first message, its status (FR-003), and when it last changed, which is when the service last recorded a step of it and, while a run is in progress, when that run started. A row the service first makes while a run is in progress, for a task from before this feature or one whose row could not be written, shows its last recorded step instead: the service does not know when that run started (data-model.md, "Opened"). Tasks that need a decision MUST come first, then the others with the most recently changed first. A row MUST open the task's page. With no task, the section MUST say so. The section MUST have a New task control.
- **FR-003**: A task has exactly one status, the first of these that holds:
  1. **Running**: a run is in progress on it, on any service instance.
  2. **Needs your decision**: a proposal is pending.
  3. **Failed**: its most recent run was started through the console and ended with an error. The row and the page give the service's reason.
  4. **Stopped**: its most recent run ended before Freyja finished: a person stopped it (FR-022), the service stopped while it ran, or a run the console did not start, by another client or by the service itself, ended with steps still to take, because its client stopped reading or because it failed. The reason such a run failed went to whoever read its stream and is not known to the list.
  5. **Waiting for an agent**: its most recent run finished and one of its agent tasks is pending.
  6. **Done**: its most recent run finished and nothing is pending.

  The status follows the most recent run whoever started it: this console, another client, or the service itself when it reports an agent's outcome.
- **FR-004**: Every thread of the service that has had a run since this feature was installed MUST be a task in the list, whichever client started the run. A thread whose last run was earlier MAY be missing from the list at first. It MUST still open by its address in the console, and MUST be in the list from then on, or from its next run, whichever comes first.
- **FR-005**: The list MUST show every task that needs a decision, however many there are, and after them the most recently changed of the others, as many as bring the list to 100 rows; with 100 or more that need a decision it shows those and no other. When more tasks exist than it shows, it MUST say so. The list MUST carry at most the first 200 characters of a first message.
- **FR-006**: While the console is open, the list and the sidebar's number MUST follow a change made anywhere within fifteen seconds, without the page being reloaded. When the service cannot be reached, the section MUST say so and offer to try again, and neither the list nor the number MUST be shown as if it were current. A console that is hidden, as a tab in the background is, reads nothing, and MUST read the list again when it is shown.

**Starting a task**

- **FR-007**: New task MUST open one text box, empty each time. Sending MUST create a task under an id the console chooses for it and has not used before, open the task's page, and start exactly one run with the text, also when the person presses Send more than once. The box MUST NOT send a message that is blank: empty, or only spaces and line breaks. Whether a message is acceptable is otherwise the service's decision.
- **FR-008**: When the run cannot be started, because the service cannot be reached or refuses the message, the page MUST say why with the service's reason, MUST keep the text, and MUST NOT list a task for it. The same holds when a new task's first run ends with an error before anything of it was recorded, as when no usable model is active: the reason is shown where the person then is, the text is offered again to be sent, and no task is listed.

**A task's page**

- **FR-009**: A task's page MUST show: the conversation in order, with the person's messages, Freyja's replies, and each action Freyja took with its arguments and its result; the task's status; the pending proposal when there is one (FR-014); and the task's agent tasks, each with its agent, its status, and the message it was started with, the pending ones marked. A task that does not exist MUST be shown as not found, also when the address holds an id the service does not accept.
- **FR-010**: A page that starts a run, with a message or with a decision, MUST show Freyja's reply as it is written and each step as it completes, for as long as the page stays open. When the run ends, in whichever way, the page MUST read the task again and show the conversation as the service holds it; what was shown while the run streamed is replaced by it. A stream that ends without the run's last event, because the connection was lost or something on the way closed it, says nothing about the run: the page MUST say that the connection ended, MUST NOT show the run as failed or as ended, and MUST follow the task as FR-013 says.
- **FR-011**: When a run ends with an error, the page MUST say that it failed and give the service's reason. When the reason is that no usable model is active, it MUST point to the LLM section. The task MUST stay usable: the person can send another message.
- **FR-012**: All text of a task MUST be shown as plain text, whether it comes from the model, a tool, an agent, a tool server, the service, or a person: messages, action names, descriptions, arguments, results, reasons, and the beginning of a first message in the list. Nothing in it is rendered as markup, loaded, or followed as a link.
- **FR-012a**: Every control of the section MUST be reachable and usable with the keyboard and MUST carry a name that says what it does and, for a decision, on which action. A status, the number on the sidebar, and that a run is in progress or stopping MUST be given in words and never by color or an icon alone.
- **FR-013**: A page that is not reading a run's stream, because it was opened or reloaded after the run started or because the run was started elsewhere, cannot join it in the first version. While such a run is in progress the page MUST say so, MUST show the conversation as far as the service holds it, MUST read the task again at least every five seconds until the run has ended, and MUST offer no way to send a message or a decision meanwhile. A page that shows a task with a pending agent task MUST read the task again at least every fifteen seconds. A page that is hidden, as a tab in the background is, reads nothing, and MUST read the task again when it is shown.

**Deciding a proposal**

- **FR-014**: When a task has a pending proposal, its page MUST show a card with every proposed action: its name, the tool server or the agent it goes to when the service names one, its description, and all of its arguments in full, each number by its value (`5.0` is shown as `5`); a browser that cannot hold a whole number beyond 2^53 as it was written says so beside the arguments (see the Convergence note of Phase 12). For each action it MUST offer the decisions the service lists as allowed for that action, and no other. The card MUST be the last part of the task's page, after the conversation and the task's agent tasks, and the page MUST bring it into view once when it appears, and not again for the same proposal.
- **FR-015**: The decisions are the service's three: approve, which runs the action as proposed; edit, which runs it with arguments the person entered in place of the proposed ones; and reject, with an optional reason of at most 4,000 characters. The page MUST send an edit as the person entered it, a number by its value (`5.0` is sent as `5`, the same number), and MUST NOT send text it cannot send so, as a whole number beyond 2^53 is in a browser that cannot hold it as it was written. It MUST NOT send text that cannot be read as a set of named arguments, the structured form the proposal shows them in, and MUST say so beside the edit; whether arguments are acceptable to the action MUST be decided by the service alone, and the service's refusal MUST be shown beside the edit, which is kept.
- **FR-016**: A proposal with several actions MUST get one decision per action, in the order the service lists them, sent together; nothing is sent until every action has one.
- **FR-017**: A decision MUST be sent only by a press of the control that names it. Pressing Enter in a field, opening or reloading a page, and sending a message MUST NOT send one, and a control pressed twice MUST send at most one. The console MUST NOT decide anything by itself and MUST offer no way to approve later proposals in advance, to stop being asked, or to send a new message to a task whose proposal is pending. It adds no way around the approval gate: every decision goes through the service's existing rules.
- **FR-018**: A decision MUST name the proposal the page showed when the person decided. When the service refuses it because that proposal is no longer the pending one, because nothing is pending, or because a run is in progress, the page MUST say so, MUST NOT send it again, and MUST show the task as it is now. When no answer to a decision arrives, the page MUST say that the outcome is not known and read the task again.
- **FR-019**: After a decision is accepted, the continuation MUST be shown on the same page as a run is (FR-010, FR-011), including when it pauses on another proposal.

**Continuing, leaving, and stopping**

- **FR-020**: A task's page MUST have a box to send another message on the same task. It MUST be usable when no run is in progress and no proposal is pending; otherwise it cannot be sent and the page says why. While a proposal's card is shown, the card takes the box's place, and what was typed in the box MUST be kept for when the proposal is decided.
- **FR-021**: A run the console starts, with a message or with a decision, MUST go on to its end whether or not any page is reading it. Closing, leaving, or reloading the page, and losing the connection, MUST NOT end the run and MUST NOT change how it ends: finished, paused on a proposal, failed, or stopped by a person. Everything that holds for a run today MUST hold for it without a reader: one run per task on every service instance for as long as it runs, the step limit, which such a run can reach up to two steps early (Edge Cases), the approval gate, and the record of each completed step. The service MUST offer this way of starting a run and of continuing one with a decision beside the way that exists today, which keeps its behavior (FR-027), and MUST log each run started this way as one that goes on without a reader. When the service is stopped in the ordinary way, it MUST treat each such run in progress as if a stop had been asked for it (FR-022) and wait for the step under way, so that stopping the service cuts no action off; only a service that is killed or fails ends such a run in the middle of a step.
- **FR-022**: While a run the console started is in progress, the task's page MUST offer Stop, whether or not the page is reading the run's stream. Stopping MUST let the step under way complete and be recorded, and MUST start no further step. In particular an action that a person approved, and that is under way when the run is stopped, MUST complete and have its result recorded: a stop MUST never leave an action having taken effect while its proposal still shows as pending, and MUST never cause an action to run a second time. The run then ends as stopped, the steps it completed are kept, and a follow-up continues the task (FR-020). While the step under way completes, the page MUST say that the run is stopping, and MUST show it as stopped only once the service says the run has ended. A stop MUST work whichever service instance runs the task.
- **FR-022a**: A stop asked when no run is in progress, or when the run has just ended by itself, MUST change nothing, and the page MUST show the task as it is. When a stop was accepted and the run then ended another way before the stop was applied, by pausing on a proposal, finishing, or failing, the task MUST be shown as it ended, never as stopped, and the page that asked for the stop MUST say that the run ended before the stop took effect. A page that follows the run by reading learns how it ended from a reading of the task; when that reading is already of a later run, it says nothing of how the earlier one ended, since nothing keeps that. A run the console did not start, by another client or by the service itself, MUST NOT have a Stop in the first version: the page says why, and a request to stop one sent straight to the service is refused. Stop MUST take a press of its own, like a decision (FR-017). The service MUST log each stop it accepts and each run that ended because of one, naming the task.

**The service**

- **FR-023**: The service MUST give the console what it lacks today: the list of tasks with what FR-002 to FR-005 need, the number of tasks that need a decision, a run that goes on without a reader (FR-021), and the stop (FR-022). Answering the list or the number MUST contact no model, no agent, and no tool server, and MUST change no thread. The list MUST be the same from every service instance that shares the database and MUST survive a restart. Keeping the list MUST NOT fail or change a run: when the service cannot record a run for the list, the run goes on as if it had, the failure is logged, and the thread is in the list again from its next run or from when it is opened. When the list cannot be read, the service MUST answer with an error and never with an earlier list, and the console says so (FR-006).
- **FR-024**: A page served from another origin MUST NOT be able to start a run, send a decision, or stop a run, in any form of request a page can send without the service's permission, and MUST NOT be able to read the list, a task, or a run. The rule and its refusal are the ones the LLM and Agents routes already apply (feature 004, FR-003a; feature 005, FR-029). A client that is not a web page MUST be served as before, with one exception that holds for every client: a request made for a host name the service does not answer for, one that is not `localhost`, an IP address, or a name the deployment lists, or whose `Host` names no host, is refused before anything else (`unknown_host`, contracts/openapi.yaml), which closes the gap of a page served under a name its author controls (Assumptions). The pages of this section cannot be shown inside a page of another origin, by the rule feature 005 set for every page of the console (its FR-029).
- **FR-025**: What the service returns for the list holds text from threads. A stored API key MUST NOT appear in it beyond its last four characters, and an agent's address MUST NOT appear in it, as in every other answer of the service (feature 004, FR-020; feature 005, FR-028). The searches that prove this for the existing routes MUST cover the new ones.
- **FR-026**: The web app MUST reach tasks only through the service's API, on the origin it is served from, as the LLM and Agents sections do. It MUST hold no credential, call no model, agent, or tool itself, and decide nothing the orchestrator does.
- **FR-026a**: The Tasks section MUST say, above the list of tasks and whenever the list is shown, that the console has no users: everyone who can open it sees every task and can decide every proposal. The README MUST say the same where it describes the section. The service records no name with a decision.

**Compatibility and quality**

- **FR-027**: Starting a run, reading a thread's state, and deciding a proposal, in the way the API offers them today, MUST behave for a client that is not a web page exactly as before this feature, including that such a run ends when its client stops reading, apart from the refusal of a request made for a host name the service does not answer for (FR-024), which every route of the service but `/healthz` applies. Every existing automated test MUST pass unchanged, apart from the tests of the sidebar, whose entries and whose requests this feature adds to, tests that gain setup because every page of the console now asks for the number of tasks that need a decision, and the one test of feature 004 that sends the thread operations a body that is not JSON from another origin: it expected the refusal the body earned, and now meets the refusal of FR-024, which comes first.
- **FR-028**: The service's part and the web app's part MUST each be covered by automated tests that need no network, no model provider, and no agent installed or running. The console's part of every user story MUST also be covered by an end-to-end test in a browser, beside its unit tests. That a run's stream reaches the page through the console's own origin as it is produced, and not all at once when it ends, was shown in the plan's research in both browsers the end-to-end tests use; the two properties of the service's answer that it rests on MUST be pinned by automated tests of the service. The service's tests MUST show that a run goes on to the same end after its reader leaves, and that a stop pressed while an approved action is under way leaves that action run once and recorded.

### Key Entities

- **Task**: One conversation with Freyja, started by a person's message; the service's thread, under the name the console gives it. It has an id the client chose, a first message, a status, the time it last changed, a conversation, at most one pending proposal, and any number of agent tasks.
- **Status**: One of six: running, needs your decision, failed, stopped, waiting for an agent, done (FR-003). It is read from what the service holds; the console keeps none.
- **Run**: One turn of work on a task, started by a message or by a decision, or by the service when it reports an agent's outcome. It ends finished, paused on a proposal, failed, or stopped. As in feature 001; new here is that how the most recent run ended is part of a task's status, that a run the console starts goes on without a reader, and that a person can stop such a run.
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
- **SC-007**: A page served from another origin starts zero runs, sends zero decisions that are applied, stops zero runs, and reads zero answers about tasks.
- **SC-008**: A recognizable stored API key and a recognizable agent address appear in nothing the service returns for the list of tasks, through tasks that finish, pause, and fail.
- **SC-009**: With 100 tasks, the Tasks section shows its list within two seconds on the machine the service runs on.
- **SC-010**: A deployment used only through the API behaves as before: every automated test that existed before this feature passes, changed only as FR-027 says.
- **SC-011**: A task whose page is closed as soon as its run has started reaches the same end, with the same conversation, as one whose page stays open, in 100% of automated runs. The order of the results of actions that ran at the same time is not compared: it differs between any two runs.
- **SC-012**: After a stop is accepted, the step under way is recorded and zero further steps start, in 100% of automated runs; an approved action that was under way has run exactly once and is in the conversation with its result.

## Assumptions

**Decisions taken in clarification**

Five decisions were the owner's. Each was asked on 2026-11-20; the first three confirmed the draft, and the last two changed it.

1. **The section's name.** Decided on 2026-11-20: Tasks. "Task" already means a delegate agent's task in the code, the API, and the prompts (`agent_tasks`, `check_agent_tasks`), and Freyja's own replies speak of an agent's "task". The console keeps the two apart by always saying "agent task" for the second (FR-001a); the API and the code keep "thread" for the first, and nothing is renamed. Threads, Requests, and Conversations were considered and not adopted.
2. **No users.** Decided on 2026-11-20: accepted for the first version. The service has no authentication, so everyone who can open the console sees every task with its conversation and can approve any pending action, and nothing records who decided, as in features 001, 004, and 005 for what they added, with the service bound to this machine. New with this feature: tasks can be listed, where until now a thread could only be read by someone who knew its id. The section and the README say so (FR-026a). Recording a name a person types with each decision, and waiting for authentication, were considered and not adopted.
3. **Follow-ups.** Decided on 2026-11-20: a person can send more messages on the same task (FR-020), as the description's sketch has it and as the service already works. One message per task was considered and not adopted: a task that failed or stopped could then only be entered again.
4. **A run and its page.** Not among the description's four, and found while writing: the service runs a thread only while a client reads its stream, so a task entered in the console would have stopped when its page was closed. Decided on 2026-11-20: a run the console starts goes on without a reader (FR-021). The draft had kept today's behavior, with a warning on the page; that was not adopted. It is the largest addition to the service in this feature.
5. **Stopping a run.** Decided on 2026-11-20, after the decision above: the first version has Stop for runs the console started (FR-022). The draft had none, when closing the page still ended a run. A stop for every run, whoever started it, was considered and left for later.

**Scope**

- Out of scope for the first version, and left for later: authentication and a record of who decided; stopping a run that another client or the service itself started; reading the live stream of a run that is already in progress, from a page opened after it started, which follows the run by reading the task again instead (FR-013); a limit on how many tasks run at once; deleting, archiving, renaming, searching, filtering, and paging tasks; attachments and anything but plain text in a message; showing Freyja's text as formatted text; a form built from an action's own description of its arguments, in place of editing them as text; the debug stream; notifications outside the console; showing the files Freyja wrote in its workspace; and choosing a model or a set of agents per task.
- Left open by feature 004 on purpose, carried by feature 005, and not addressed here: a request that reaches the service under a name an attacker controls (DNS rebinding), which the refusal of other origins does not cover. FR-024 takes that rule as those features have it. With this feature the gap also covers starting a run, deciding a proposal, stopping a run, and reading tasks: a page served under such a name could read a pending proposal and approve it. Closing it needs the service to know the names it answers to, which is a setting no feature had added when this was written. Closed after the feature's last phase, for the LLM and Agents routes as well: the service refuses a request made for a host name that is not `localhost`, an IP address, or a name in `FREYJA_ALLOWED_HOSTS` (`src/freyja_agent/api/hosts.py`).
- Not changed: the approval gate, its three decisions and their rules; what a run does and what its stream carries; the three thread operations as the API offers them today; the thread's state; agent tasks and how their outcomes are reported; LLM profiles and the Agents section.
- The console is in English and is used with a current desktop browser, as in features 004 and 005.

**Behavior choices**

- "Stopped" is a sixth status the description's sketch does not have. Without it, a task whose run a person stopped, or whose run ended because the service stopped, would be listed as done, which it is not, or as failed, which blames an error that did not happen.
- A stop waits for the step under way (FR-022); the owner chose Stop, and this rule for it is the specification's. When the service ends a run today because its client stopped reading, the step under way is dropped, and feature 001 accepted that an action can then have taken effect while its proposal still shows as pending, because its one action only wrote a log line. Actions now reach tool servers and agents, and Stop would let a person cause that by pressing a button right after Approve. So a stop asked through the console is held to more than a client that goes away: it never cuts an action off (principle I). The page can therefore show "stopping" for as long as one step is allowed to take.
- A run that the console starts goes on; a run another client starts the way the API offers today still ends when that client stops reading (FR-021, FR-027). Changing it for every client would change what feature 001 specifies and what its tests check.
- No limit on how many tasks run at once is added. Before, a run needed an open connection; now a person can start many and leave. Each is bounded by the step limit, and anyone who can reach the service could already start as many as they kept connections open.
- The order of FR-003 puts failed and stopped above waiting for an agent. A task whose last run did not finish needs the person; one that only waits for an agent does not.
- A thread from before this feature may be missing from the list until its next run (FR-004). How a run ended was not recorded until now, so the service does not know such a thread until it runs again or is opened, and it is in the list from then on.
- Edited arguments are entered as text, in the structured form the proposal shows them in (FR-015). The page checks at most that the text is well-formed and that it can be sent as it was entered, which decides nothing about the action. Everything else is the service's check, as today.
- An approval is not confirmed a second time. The card shows the whole action and Approve is a press of its own (FR-017); a confirmation on top would be pressed without being read.
- The console chooses a task's id (FR-007), since the service leaves a thread's id to its client. It is random and long enough that two tasks do not share one, and it says nothing about the task.
- The page shows the service's state as the truth once a run has ended (FR-010). While a run streams, text written by a subagent cannot be told from Freyja's own (feature 001), so what streams is shown as work in progress, and the conversation read afterwards is what counts.
- Limits chosen here: 100 rows in the list, 200 characters of a first message in a row, 2,000 characters of a failed run's reason in a row, 1,000 events held for a reader that does not keep up, a sign of life on a run's stream every ten seconds, fifteen seconds for the list, the number, and a page that follows a task waiting for an agent, and five seconds for a page that follows a run whose stream it is not reading.
- A stopped task has one status whatever ended its run (FR-003). The console does not say which cause it was: for a run the console did not start it does not know.
- A task's page reads the whole conversation each time it reads the task. There is no paging of a conversation in the first version, so a very long one is read in full every five or fifteen seconds while its page follows it (FR-013).
- A message is never a decision, and a task whose proposal is pending takes no new message, as the service already rules (feature 001, FR-011).

**Proven in the plan's research**

The three things this section listed as not proven when the spec was written were settled on 2026-11-20, before anything was designed on them ([research.md](research.md)):

- A run's stream does not pass the console's forwarding of `/api/...` as it is produced when the service answers as it does today: the forwarding compresses it and delivers it whole at the end, in both browsers. It passes event by event when the answer says it must not be transformed, and the forwarding ends a stream that is silent for 30 seconds unless the service keeps it active. The routes the console uses do both (R1). A stream that is cut anyway does not end the run (FR-021); the page then follows the task by reading it again (FR-013).
- The service can end a run after the step under way and not in the middle of it (FR-022): a run the console starts is run in parts that end before each model call and each tool step, and a stop is applied only between two parts. Such a run was shown to leave the same thread as a run in one piece (R4).
- FR-021 and FR-022 are built without changing a file that the session building features 005 and 006 is working in (R16).

**Earlier requirements this feature replaces**

- Feature 001, the edge case "Client disconnects mid-stream" and the assumption that a client disconnect stops the run: for a console run a disconnect ends nothing (FR-021). A run started the way feature 001 defines keeps that behavior (FR-027).
- Feature 001, Assumptions, "Scope": listing threads, cancelling a run, and continuing a run after the client disconnects were out of scope. This feature adds each, as specified here.
- Feature 001, FR-001, "three thread operations": there are five more.
- Feature 001 let a page of another origin reach the thread operations whenever its browser let the request through. They now refuse such a request (FR-024).
- Feature 001's README note that an ordinary stop of the service waits for runs in progress to finish. With the versions installed today it does not: an ordinary stop ends every open stream at once, and with it a run started the way feature 001 defines, in the middle of its step (seen in a run; [research.md](research.md) R17). A console run is not tied to its stream, and the service waits for its step under way (FR-021).

**Environment and dependencies**

- The constitution needs no amendment. Principle I: the page offers the service's three decisions and adds no way around the gate; an edit is checked by the service; a run that goes on without a reader pauses for a decision exactly as one with a reader does, and stays resumable after any delay; and a stop never cuts off an approved action (FR-022). Principle III: the web app is a client of the API and decides nothing the orchestrator does. Principle VI: nothing here asks a model whether an action needs approval.
- The service keeps binding to the local interface with no authentication. With this feature, anyone who can reach the console can read every task and decide every pending proposal with a press, where before they needed a thread's id and a hand-written request. That is acceptable for local use, is documented, and must be revisited before the service is bound more widely (decision 2 above; FR-026a). They can also stop a run the console started.
- Several service instances may share one database, as before. The list and the number are the same from each (FR-023). They are assumed to all run a version of the service that has this feature. An instance on an earlier version runs threads without recording them for the list, so a thread it ran is listed only once an instance with this feature runs it or opens it; nothing else differs, and going back to the earlier version leaves the list's records unused and harms nothing.
- The existing thread operations, the approval gate, the run lease, the step limit, agent tasks, and the delegation worker are reused. This feature adds the list of tasks, the number, a run that goes on without a reader, the stop, the refusal of other origins on the thread operations, and the Tasks section.
- Feature 005 is still being built and feature 006 follows it, by another session, on the same `main`. This feature changes nothing they specify.
- The stand-in model and the stand-in agent play the model and every agent in the automated tests, as today.
