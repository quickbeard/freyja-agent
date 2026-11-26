# Web console

The web app under `web/` is the console: a client of [the API](api.md) that holds no key and calls no model itself. Start the service, then:

```bash
cd web
pnpm dev                       # http://localhost:3000
```

Open <http://localhost:3000>, by that name: in development Next.js serves its dev-only assets to `localhost` only, so the page does not load fully when opened as `127.0.0.1`. The address changes to `/tasks`. A sidebar on the left lists the sections, Tasks, Agents, and Models; on a screen narrower than 768 pixels it opens from a button. Like the API, the console has no authentication.

When the service cannot be reached, a section says so and offers to try again.

## Tasks

The Tasks section is where a person hands Freyja work (feature 007). The console calls a thread a task: it reads tasks with the routes under `/threads` described in [Use the API](api.md), starts runs with `POST /threads/{thread_id}/runs/unattended`, and continues them with `POST /threads/{thread_id}/resume/unattended`. Everything a task holds is shown as plain text.

The console has no users: everyone who can open it sees every task and can decide every proposal, and nothing records who decided. The section says so above its list.

### The list of tasks

The section lists one row per task: how its first message begins, its status in words (Needs your decision, Running, Failed, Stopped, Waiting for an agent, or Done), the service's reason under a task that failed, and when it last changed. The tasks that need a decision come first, then the most recently changed. A row opens the task.

When more tasks exist than are listed, the section says so. Every task that needs a decision is listed, and when the service could not read some of them just now, the section says how many it does not show as needing one. When the service counts tasks and can show none of them just now, the section says that, not that there is none.

On every section, the sidebar's Tasks entry shows how many tasks need a decision, in words as well as in a number, and none when no task does.

Under the Tasks entry, the **Agent approvals** dropdown shows the agents' approval mode (see [Auto mode](agents.md#auto-mode)) and changes it. **Manual** applies at once: every message to every agent waits for a person. **Auto** lets the agents set to Auto run without approval, so it asks for confirmation first, saying how many agents are set to Auto and that a proposal already waiting for one of them can then only be approved. The dropdown is read with the list of agents; while that list cannot be read it says that the mode is not known and cannot be changed.

The list and the number are one reading of `GET /threads`, read again every ten seconds, so that a change made elsewhere is followed within fifteen, and at once when a hidden page is shown or a run the page shows ends. When the service cannot be reached, the section says so, offers to try again, and shows no row, and the sidebar says that the number is not known. A request the service takes and does not answer ends the same way: the console's server gives up forwarding it after 30 seconds, and the page gives up by itself after 35.

### A new task

**New task** opens one text box. Sending it starts a run on a new task and opens the task's page at `/tasks/<id>`. There Freyja's reply appears as it is written, in plain text or in the blocks a model sends it in, and each step is listed as it completes. Each action the reply asks for is named as soon as the reply names it; in a reply of Freyja's own, its arguments are shown once the reply is complete.

When the run ends, the page shows the conversation as the service holds it, with the task's status, and a reload or another browser shows the same. The run goes on if the page is closed.

A message the service refuses stays in the box with the service's reason. When no usable model is active, the page says so, points to the Models section, and offers the text again.

### Deciding a proposal

When the run pauses on an action that needs approval, the task's page shows a card for the proposal: each action's name, the tool server or agent it goes to, its description, and its arguments in full. The card is the last part of the page, after the conversation and the task's agent tasks, and the page scrolls to it when it appears, once for each proposal. Each action offers the decisions the service allows for it:

- **Approve**.
- **Edit the arguments**, which opens them as JSON to change before approving. Text that is not a JSON object is not sent, and an edit the service refuses keeps its text with the service's reason beside it.
- **Reject**, with an optional reason of up to 4,000 characters.

With one action, a decision is sent when its button is pressed. With several, each action is decided first and **Send the decisions** sends them together, in the service's order. Where two proposed actions have the same name, each one's controls are named by its place as well.

A decision is sent only by a press of its button, never by Enter in a field, and a button pressed twice sends once. The continuation is shown on the same page as a run is, also when it pauses on another proposal. Decisions go through `POST /threads/{thread_id}/resume/unattended`, so every check of a decision is the service's.

When the service refuses values, they are shown beside the action, and the task is read again, so the card offers what the service allows now. A decision the service stops allowing is dropped, and it takes a press to choose it again. A decision the service turns away because the proposal was decided elsewhere, nothing is pending, or a run is in progress, most likely one a decision in another window started, is said, and the task is shown as it is now. When no answer comes, the page says the outcome is not known and reads the task again.

**Large whole numbers.** A whole number beyond 2^53, such as a 64-bit id, is shown digit for digit and sent as it was entered in a browser that gives a number's own text when it reads JSON, as current Chromium and Firefox do. A browser that does not would show and send another number: there the card says that such an argument may be shown inexactly, and an edit that holds one is not sent, with the number named beside it. Approving sends no arguments, so it is exact in either.

### Tasks another agent started

A task that another agent handed Freyja over A2A (see [Receive tasks from agents](a2a-server.md)) says who started it, on its row and on its page: "Started by an agent on this machine" for an agent that called the service's own address. Its proposals are decided, and its run stopped, as for any task; the proposal's card says who started the run and that no person asked for the action. A message to a delegate agent from such a task follows the agents' approval mode, as on any task: it runs without a decision only when the mode is Auto and the agent is set to Auto. Only the agent that started it sends messages on it, so its page has no box for another message and says why; the service refuses such a message with `409` `caller_thread` all the same. When that agent cancels its task, its page says so: nothing the task still proposed can be approved any more, and Freyja rejects it by itself, for that cancel and not as a person's decision; what ran before the cancel, such as an action a person approved, stays in the conversation. Once the proposal is rejected, the task is no longer counted among those that need a decision. A proposal still shown on such a task can only be rejected: its card says so, and an approval or an edit is answered that the agent canceled, with Reject still offered. An action that no longer needs approval, as after the agents' approval mode turned Auto, is offered for approval only, so it cannot be rejected either: the card says to set the mode to Manual again, after which it can be rejected.

### Another message

Under the conversation, a box sends **another message** on the same task. Freyja answers with the whole conversation in mind, and the reply streams below the conversation as it was. It is how a task that failed or stopped is continued.

While a proposal is pending, its card takes the box's place at the end of the page, and the box comes back once the proposal is decided. The box cannot be sent while a run is in progress, or while the page knows of a proposal it cannot show, and says which; what was typed stays in it in every case. A follow-up the service refuses keeps its text with the reason, said in the console's words when a run is in progress or a proposal is pending. One whose run failed before the message was recorded, as when no usable model is active, has its text put back in the box.

### Agent tasks

A task that handed work to delegate agents lists its **agent tasks**: each with its agent, its status in words, and the message it was started with, the pending ones marked.

Under an agent task that is working, the page shows what the agent last reported about its work and how long ago the service learned it, with the exact time when the age is pointed at. The text is the agent's own words, shown as plain text in a box that scrolls when it is long, and the age moves at each of the page's readings. An agent task in any other state shows no progress.

While an agent task is pending, or has ended with its outcome not yet in the conversation, the page reads the task again every twelve seconds, so the outcome appears without a reload.

### Runs the page does not read

A run the console starts goes on when its page is closed, left, or reloaded: closing sends nothing, and a page opened later shows where the task stands, so a task that paused meanwhile is counted on the sidebar and decided from its page.

While a run is in progress that the page does not read, started in another window, by another client, or by the service to report an agent's outcome, the page says so, reads the task every four seconds, and offers no message and no decision until the run has ended. Neither this reading nor the one for agent tasks happens while the page is hidden.

### Stopping a run

While a run the console started is in progress, the task's page offers **Stop**, whether or not it reads the run's stream. A press asks the service through `POST /threads/{thread_id}/stop`. The page says that the run is stopping while the step under way completes, and shows the task as stopped only once the service says the run has ended. A stop asked in another window is said too, also on a page that reads the run's stream, which then offers Stop no more. A follow-up continues a stopped task.

A run that paused on a proposal, finished, or failed before the stop took effect is shown as it ended, never as stopped, and the page that asked says that the stop came too late. A page that follows the run by reading says so only of the run it stopped: when the row is already of a later run, it says nothing of how the earlier one ended. A stop the service refuses, because no run is in progress any more or the run cannot be stopped, is said, and the task is read again.

A run the console did not start, by another client or by the service to report an agent's outcome, has no Stop, and the page says why. A task with no run in progress has none either, and a proposal is declined by rejecting it.

### Pages of other origins

A page of another origin, open in the same browser, can do none of this:

- The service refuses a run, a decision, and a stop that the browser marks as made by such a page, with `403` and code `other_origin`, before a lease is taken or anything of the task is read, whether the request is sent to the service or through the console's own address.
- It gives such a page no permission to read the list of tasks, a task, or a run's stream.
- The console answers every path with the headers that forbid showing it in a frame, so no page can put a task's Approve or Stop under a person's click.

A page that reaches the service or the console under a name its own author controls (DNS rebinding) is, to the browser, a page of that same origin. The service refuses it by the name it asks for, which is answered only when `FREYJA_ALLOWED_HOSTS` lists it (see **Host names** in [Use the API](api.md)). Keep the service and the console bound to an address only their operators reach.

## Agents

The Agents section shows every agent Freyja can hand work to as a card: its name, its own mode, **Manual** (its messages require approval) or **Auto** (they run without approval while the agents' approval mode is Auto), whether it comes from the environment, and whether the service could reach it at its most recent attempt, with the time. An agent that cannot be reached, or a saved agent the environment overrides, carries a warning with the reason. Below that is what the agent's card says of itself, shown as plain text and marked as the agent's own claim. **Add** saves a new agent by its name and address: the service reads the agent's card first and refuses an agent it cannot use, with the reason shown in the form. An agent added here is Manual unless the form's choice is set to Auto, which the form asks to confirm, saying that nothing sent to the agent will be seen by a person first. A saved agent's card shows its address. The card of a saved agent in use has a **Manual/Auto switch**, which asks for confirmation before it sends the change at the version the card showed, saying what the new mode means; an environment agent's card shows its mode without a switch. While the agents' approval mode is Manual, an Auto agent's card says that it is held to Manual. **Edit** on the card of a saved agent that is in use changes its address, its approval choice, or both; its name cannot be changed. Nothing is sent before **Confirm**, which says what the change means: running without approval holds from the next run, requiring approval holds at once, also for runs in progress, and a new address applies from the next run and gives up the agent's pending tasks, whose number it shows. **Remove** on a saved agent's card asks the same way, saying that the removal holds at once and how many pending tasks will be given up. After a move or a removal the section says how many tasks were given up. A form or a dialog that was opened before the agent was changed elsewhere does nothing and says so. A saved agent's card shows its number of pending tasks when it has any. **Check** on a card has the service read the agent's card again and says whether the agent can be used, with the reason when it cannot, and whether the agent's card changed. Every agent that cannot be used is warned about three times from one reading of the list: on its card, in a warning above the cards that names it, and as a number on the sidebar's Agents entry, which shows on every section. An agent named in the environment cannot be changed or removed, and a saved agent that is not in use can only be removed. The list is `GET /agents`, which contacts no agent and never shows the address of an agent named in the environment.

## Models

The Models section, at `/models`, shows every profile as a card: its name, provider, model, base URL, the last four characters of its key, whether it is active, whether it can be used and why not, and its last test. **Add** saves a new profile, and its form can test the values before they are saved. A card has **Test**, **Set active**, **Edit**, and **Delete**; the environment's card has Test and Set active only. Activating a profile, deleting one, and changing the active one each ask for confirmation first, and activating one that is untested or failed its last test says so and offers to test it. A key typed into a form is sent once and kept nowhere in the browser.

## Where the console sends requests

The browser only ever calls the web app's own origin. The web app's server forwards `/api/...` to the service at `FREYJA_API_URL`, `http://127.0.0.1:8000` by default; set the variable before `pnpm dev` when the service listens elsewhere. `pnpm build` writes the address into the build and `pnpm start` does not read it again, so a built app is built again to change it. The variable is documented in `web/.env.example` and read in `web/next.config.ts`.

This is the last of the docs; the [README](../README.md#docs) lists them all.
