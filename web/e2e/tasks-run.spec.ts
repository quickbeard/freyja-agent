import { expect, type Page, test } from "@playwright/test";
import {
  agentTask,
  ai,
  followedUp,
  human,
  notify,
  outcome,
  pausedTask,
  proposal,
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
