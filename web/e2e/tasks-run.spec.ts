import { expect, type Page, test } from "@playwright/test";
import { answerProfiles, environmentProfile, profileList } from "./api";
import {
  agentTask,
  ai,
  continued,
  finished,
  followedUp,
  goesOn,
  human,
  notify,
  outcome,
  pausedTask,
  proposal,
  runningTask,
  summary,
  TaskService,
  threadState,
} from "./tasks-api";

// 007, User Story 4: continue a task.

const ID = "0123456789abcdef0123456789abcdef";
const RUN_BLOCKS =
  "A run is in progress on this task. A message can be sent once it has ended.";
const PROPOSAL_BLOCKS =
  "This task waits for a decision on the proposal above. A message can be sent once it is decided.";
const IN_PROGRESS =
  "A run is in progress. This page reads the task again until it ends.";

const conversation = (page: Page) =>
  page.getByRole("list", { name: "Conversation" });
const messages = (page: Page) => conversation(page).locator(":scope > li");
const followUp = (page: Page) =>
  page.getByLabel("Send another message on this task");
const sendMessage = (page: Page) =>
  page.getByRole("button", { name: "Send message" });
const agentTasks = (page: Page) =>
  page.getByRole("list", { name: "Agent tasks" }).getByRole("listitem");
// The page's own alerts: Next.js adds a route announcer, an alert of its own.
const alerts = (page: Page) =>
  page.locator('[role="alert"]:not(#__next-route-announcer__)');

/** A service holding one finished task, whose follow-ups are answered with `reply`. */
async function finishedTask(page: Page, reply = "Quieter.") {
  const service = new TaskService();
  service.tasks.set(ID, {
    row: summary(ID, { first_message: "Summarize the week." }),
    state: threadState(ID, [human("Summarize the week."), ai("Here it is.")]),
  });
  service.answerRunsWith((id, message) => {
    const task = service.tasks.get(id);
    if (task === undefined) {
      throw new Error(`no task ${id}`);
    }
    return followedUp(task, message, reply);
  });
  await service.serve(page);
  await page.goto(`/tasks/${ID}`);
  await expect(conversation(page)).toContainText("Here it is.");
  return service;
}

test("a follow-up is sent on a finished task and answered on the same page", async ({
  page,
}) => {
  const service = await finishedTask(page);
  await expect(messages(page)).toHaveCount(2);

  await followUp(page).fill("And the week before?");
  await sendMessage(page).click();

  await expect(conversation(page)).toContainText("Quieter.");
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  // The same task: its conversation grew, with nothing shown twice.
  await expect(messages(page)).toHaveCount(4);
  await expect(messages(page).nth(0)).toContainText("Summarize the week.");
  await expect(messages(page).nth(2)).toContainText("And the week before?");
  expect(service.sent).toEqual([
    { threadId: ID, message: "And the week before?" },
  ]);
  await expect(page).toHaveURL(`/tasks/${ID}`);
  await expect(followUp(page)).toHaveValue("");

  // A second follow-up, with the keyboard alone (FR-012a).
  await followUp(page).focus();
  await page.keyboard.type("And before that?");
  await page.keyboard.press("Tab");
  await expect(sendMessage(page)).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(messages(page)).toHaveCount(6);
  expect(service.sent).toHaveLength(2);

  // A reload finds the whole conversation.
  await page.reload();
  await expect(messages(page)).toHaveCount(6);
});

test("a follow-up the service refuses keeps its text", async ({ page }) => {
  const service = await finishedTask(page);
  service.answerRunsWith(() => ({
    invalid: "String should have at most 32000 characters",
  }));

  await followUp(page).fill("Far too long.");
  await sendMessage(page).click();

  await expect(alerts(page)).toContainText(
    "String should have at most 32000 characters",
  );
  await expect(followUp(page)).toHaveValue("Far too long.");
  await expect(messages(page)).toHaveCount(2);
});

test("the follow-up box cannot be sent on a task that waits for a decision, and says why", async ({
  page,
}) => {
  const service = new TaskService();
  service.tasks.set(
    ID,
    pausedTask(ID, "Tell #ops.", proposal("int-1", notify())),
  );
  await service.serve(page);
  await page.goto(`/tasks/${ID}`);
  await expect(
    page.getByRole("button", { name: "Approve send_notification" }),
  ).toBeVisible();

  await followUp(page).fill("Do it anyway.");
  await expect(sendMessage(page)).toBeDisabled();
  await expect(page.getByText(PROPOSAL_BLOCKS)).toBeVisible();
  await expect(followUp(page)).toHaveAccessibleDescription(PROPOSAL_BLOCKS);
  // Enter in the box sends neither a message nor a decision.
  await followUp(page).press("Enter");
  expect(service.sent).toEqual([]);
  expect(service.decided).toEqual([]);
});

test("a task that waits for an agent shows the agent task, then its outcome without a reload", async ({
  page,
}) => {
  const handed = [
    human("Hand this to the stub agent: slow 40"),
    ai("Handed to the stub agent."),
  ];
  const service = new TaskService();
  service.tasks.set(ID, {
    row: summary(ID, {
      first_message: "Hand this to the stub agent: slow 40",
      status: "waiting_for_agent",
    }),
    state: threadState(ID, handed, { agent_tasks: [agentTask()] }),
  });
  await service.serve(page);
  await page.clock.install();
  await page.goto(`/tasks/${ID}`);

  await expect(
    page.getByText("Waiting for an agent", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Agent tasks" }),
  ).toBeVisible();
  await expect(agentTasks(page)).toHaveCount(1);
  await expect(agentTasks(page).first()).toContainText("stub");
  await expect(agentTasks(page).first()).toContainText("Pending");
  await expect(agentTasks(page).first()).toContainText("slow 40");
  await expect(page.getByText("1 agent task is pending.")).toBeVisible();

  // The agent finishes. Until a run of the service's own has recorded the outcome, the task
  // reads done and the page goes on reading.
  const ended = agentTask({
    status: "completed",
    ended_at: "2026-11-20T08:00:42Z",
    outcome_delivered: false,
  });
  const row = summary(ID, {
    first_message: "Hand this to the stub agent: slow 40",
    status: "done",
  });
  service.tasks.set(ID, {
    row,
    state: threadState(ID, handed, { agent_tasks: [ended] }),
  });
  await page.clock.fastForward(15_000);
  await expect(agentTasks(page).first()).toContainText(
    "Completed. Its outcome is not in the conversation yet.",
  );
  await expect(page.getByText("1 agent task is pending.")).toBeHidden();

  // The service's run that records the outcome is in progress: it is followed.
  service.tasks.set(ID, {
    row: { ...row, status: "running" },
    state: threadState(ID, handed, {
      run_active: true,
      agent_tasks: [ended],
    }),
  });
  await page.clock.fastForward(15_000);
  await expect(page.getByText(IN_PROGRESS)).toBeVisible();
  await expect(sendMessage(page)).toBeDisabled();

  // Within five seconds of its end, without a reload, the outcome is in the conversation.
  service.tasks.set(ID, {
    row,
    state: threadState(
      ID,
      [
        ...handed,
        ...outcome("stub: completed. slept 40.", "The stub agent slept 40."),
      ],
      { agent_tasks: [{ ...ended, outcome_delivered: true }] },
    ),
  });
  await page.clock.fastForward(5_000);

  await expect(conversation(page)).toContainText("stub: completed. slept 40.");
  await expect(conversation(page)).toContainText("The stub agent slept 40.");
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  await expect(page.getByText(IN_PROGRESS)).toBeHidden();
  await expect(agentTasks(page).first()).toContainText("Completed");
  await expect(agentTasks(page).first()).not.toContainText("Pending");
  await expect(agentTasks(page).first()).not.toContainText("outcome");
});

test("a task whose run is in progress elsewhere is followed, and takes no message meanwhile", async ({
  page,
}) => {
  const service = new TaskService();
  service.tasks.set(ID, {
    row: summary(ID, { first_message: "Count slowly.", status: "running" }),
    state: threadState(ID, [human("Count slowly.")], { run_active: true }),
  });
  await service.serve(page);
  await page.clock.install();
  await page.goto(`/tasks/${ID}`);

  await expect(page.getByText("Running", { exact: true })).toBeVisible();
  await expect(page.getByText(IN_PROGRESS)).toBeVisible();
  await followUp(page).fill("And then?");
  await expect(sendMessage(page)).toBeDisabled();
  await expect(page.getByText(RUN_BLOCKS)).toBeVisible();
  // No decision either, and no Enter that sends.
  await followUp(page).press("Enter");
  expect(service.sent).toEqual([]);

  // The run ends where it was started; this page reads it within five seconds.
  service.tasks.set(ID, {
    row: summary(ID, { first_message: "Count slowly." }),
    state: threadState(ID, [human("Count slowly."), ai("One, two, three.")]),
  });
  await page.clock.fastForward(5_000);

  await expect(conversation(page)).toContainText("One, two, three.");
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  await expect(page.getByText(IN_PROGRESS)).toBeHidden();
  // What was typed meanwhile can now be sent.
  await expect(followUp(page)).toHaveValue("And then?\n");
  await expect(sendMessage(page)).toBeEnabled();
  expect(service.sent).toEqual([]);
});

// 007, User Story 5: leave a task running, come back to it, or stop it.

const STOPPING = "The run is stopping. The step under way completes first.";
const TOO_LATE =
  "The run ended before the stop took effect. The task is shown as it ended.";
const CUT = "The connection to the run ended before the run did.";

const stopButton = (page: Page) => page.getByRole("button", { name: /^Stop/ });
const status = (page: Page, words: string) =>
  page.getByText(words, { exact: true });

/** A new task sent from the Tasks section; the id the console chose for it. */
async function sendNewTask(page: Page, service: TaskService, text: string) {
  await page.goto("/tasks");
  await page.getByRole("button", { name: "New task" }).click();
  await page.getByLabel("What should Freyja do?").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f]{32}$/);
  return service.sent[0].threadId;
}

test("a run goes on when its page is closed, and the task is found finished later", async ({
  page,
  context,
}) => {
  const service = await new TaskService(goesOn("Counting")).serve(page);
  const id = await sendNewTask(page, service, "Count slowly.");
  // The page no longer reads the run; it says so, and that the run is in progress.
  await expect(page.getByText(CUT)).toBeVisible();
  await expect(page.getByText(IN_PROGRESS)).toBeVisible();

  await page.close();
  // The run ends with nobody watching.
  service.tasks.set(id, {
    row: summary(id, { first_message: "Count slowly." }),
    state: threadState(id, [
      human("Count slowly."),
      ai("Counting: one, two, three."),
    ]),
  });

  const later = await context.newPage();
  await service.serve(later);
  await later.goto(`/tasks/${id}`);
  await expect(status(later, "Done")).toBeVisible();
  await expect(conversation(later)).toContainText("Counting: one, two, three.");
  await expect(later.getByText(CUT)).toBeHidden();
  await expect(stopButton(later)).toHaveCount(0);
  // Leaving and coming back sent nothing: one run, no stop, no decision.
  expect(service.sent).toHaveLength(1);
  expect(service.stops).toEqual([]);
  expect(service.decided).toEqual([]);
});

test("a task that paused after its page was left is found from the sidebar and decided", async ({
  page,
}) => {
  const service = await new TaskService(goesOn("Working on it")).serve(page);
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  const id = await sendNewTask(page, service, "Tell #ops.");
  await expect(page.getByText(IN_PROGRESS)).toBeVisible();

  // The run pauses on a proposal while the person looks at another section.
  const task = pausedTask(id, "Tell #ops.", proposal("int-1", notify()));
  service.tasks.set(id, task);
  service.answerDecisionsWith(() =>
    continued(task, "Notification to #ops recorded.", "Sent."),
  );
  await page.goto("/llm");
  const tasksEntry = page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: "Tasks" });
  await expect(tasksEntry.locator("xpath=..")).toContainText("1 to decide");

  await tasksEntry.click();
  const row = page.getByRole("list", { name: "Tasks" }).getByRole("listitem");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("Needs your decision");
  await row.getByRole("link").click();
  await expect(page).toHaveURL(`/tasks/${id}`);
  // A paused task has no run in progress, so no Stop: a proposal is declined by rejecting it.
  await expect(stopButton(page)).toHaveCount(0);

  await page.getByRole("button", { name: "Approve send_notification" }).click();
  await expect(conversation(page)).toContainText(
    "Notification to #ops recorded.",
  );
  await expect(status(page, "Done")).toBeVisible();
  expect(service.decided).toHaveLength(1);
  expect(service.sent).toHaveLength(1);
});

test("Stop ends a run after the step under way, and a follow-up continues the task", async ({
  page,
}) => {
  const service = new TaskService();
  service.tasks.set(
    ID,
    runningTask(ID, "Read every file.", "Reading the first"),
  );
  service.answerRunsWith((id, message) => {
    const task = service.tasks.get(id);
    if (task === undefined) {
      throw new Error(`no task ${id}`);
    }
    return followedUp(task, message, "The second file says more.");
  });
  await service.serve(page);
  await page.clock.install();
  await page.goto(`/tasks/${ID}`);

  await expect(status(page, "Running")).toBeVisible();
  await page
    .getByRole("button", { name: "Stop the run of the task: Read every file." })
    .click();

  // Stopping, in words, and no second Stop; not stopped until the service says so.
  await expect(page.getByText(STOPPING)).toBeVisible();
  await expect(stopButton(page)).toHaveCount(0);
  await expect(status(page, "Stopped")).toBeHidden();
  expect(service.stops).toEqual([ID]);

  // The step under way completes and is recorded; then the run has ended.
  service.tasks.set(ID, {
    row: summary(ID, { first_message: "Read every file.", status: "stopped" }),
    state: threadState(ID, [
      human("Read every file."),
      ai("Reading the first file: it says little."),
    ]),
  });
  await page.clock.fastForward(5_000);

  await expect(status(page, "Stopped")).toBeVisible();
  await expect(page.getByText(STOPPING)).toBeHidden();
  await expect(page.getByText(TOO_LATE)).toBeHidden();
  await expect(conversation(page)).toContainText(
    "Reading the first file: it says little.",
  );
  await expect(stopButton(page)).toHaveCount(0);

  // The task is continued with another message.
  await followUp(page).fill("Go on.");
  await sendMessage(page).click();
  await expect(conversation(page)).toContainText("The second file says more.");
  await expect(status(page, "Done")).toBeVisible();
  expect(service.sent).toEqual([{ threadId: ID, message: "Go on." }]);
  expect(service.stops).toEqual([ID]);
});

test("Stop is pressed with the keyboard alone", async ({ page }) => {
  const service = new TaskService();
  service.tasks.set(ID, runningTask(ID, "Read every file."));
  await service.serve(page);
  await page.goto(`/tasks/${ID}`);
  const stop = stopButton(page);
  await expect(stop).toBeVisible();

  // From the top of the page, Tab reaches the control; Enter presses it.
  await page.locator("body").focus();
  for (let tabs = 0; tabs < 40; tabs += 1) {
    await page.keyboard.press("Tab");
    if (await stop.evaluate((element) => element === document.activeElement)) {
      break;
    }
  }
  await expect(stop).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page.getByText(STOPPING)).toBeVisible();
  // The button is gone; the focus is on what the page says of the run, not lost.
  await expect(page.getByText(STOPPING)).toBeFocused();
  expect(service.stops).toEqual([ID]);
});

test("a run the console did not start has no Stop, and the page says why", async ({
  page,
}) => {
  const service = new TaskService();
  service.tasks.set(
    ID,
    runningTask(ID, "Say hello.", null, { stoppable: false }),
  );
  await service.serve(page);
  await page.goto(`/tasks/${ID}`);

  await expect(page.getByText(IN_PROGRESS)).toBeVisible();
  await expect(
    page.getByText("This run has no Stop: the console did not start it."),
  ).toBeVisible();
  await expect(stopButton(page)).toHaveCount(0);
  expect(service.stops).toEqual([]);
});

test("a run that pauses before the stop takes effect is shown as it ended, never as stopped", async ({
  page,
}) => {
  const service = new TaskService();
  service.tasks.set(ID, runningTask(ID, "Tell #ops."));
  await service.serve(page);
  await page.clock.install();
  await page.goto(`/tasks/${ID}`);
  await stopButton(page).click();
  await expect(page.getByText(STOPPING)).toBeVisible();

  // The reply under way asked for an action that needs approval: the run paused.
  service.tasks.set(
    ID,
    pausedTask(ID, "Tell #ops.", proposal("int-1", notify())),
  );
  await page.clock.fastForward(5_000);

  await expect(status(page, "Needs your decision")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve send_notification" }),
  ).toBeVisible();
  await expect(page.getByText(TOO_LATE)).toBeVisible();
  await expect(status(page, "Stopped")).toBeHidden();
  await expect(page.getByText(STOPPING)).toBeHidden();
  // Nothing was decided by the stop.
  expect(service.decided).toEqual([]);
});

test("Stop pressed just as the run ended changes nothing, and the page shows the task as it is", async ({
  page,
}) => {
  const service = new TaskService(finished("unused"));
  service.tasks.set(ID, runningTask(ID, "Count slowly."));
  await service.serve(page);
  await page.goto(`/tasks/${ID}`);
  await expect(stopButton(page)).toBeVisible();

  // The run finishes by itself before the press reaches the service.
  service.tasks.set(ID, {
    row: summary(ID, { first_message: "Count slowly." }),
    state: threadState(ID, [human("Count slowly."), ai("One, two, three.")]),
  });
  await stopButton(page).click();

  await expect(alerts(page)).toContainText(
    "No run is in progress on this task any more, so nothing was stopped.",
  );
  await expect(status(page, "Done")).toBeVisible();
  await expect(conversation(page)).toContainText("One, two, three.");
  await expect(status(page, "Stopped")).toBeHidden();
  await expect(stopButton(page)).toHaveCount(0);
  expect(service.stops).toEqual([ID]);
});
