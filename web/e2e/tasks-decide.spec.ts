import { expect, type Page, test } from "@playwright/test";
import {
  continued,
  notify,
  pausedTask,
  proposal,
  TaskService,
} from "./tasks-api";

// 007, User Story 2: decide an action that needs approval.

const ID = "0123456789abcdef0123456789abcdef";
const CARD = "Freyja proposes an action that needs your decision";

const conversation = (page: Page) =>
  page.getByRole("list", { name: "Conversation" });
// The page's own alerts: Next.js adds a route announcer, an alert of its own.
const alerts = (page: Page) =>
  page.locator('[role="alert"]:not(#__next-route-announcer__)');

/** A service holding one task paused on a notification, decided as `decided` answers. */
async function paused(page: Page, actions = [notify()]) {
  const task = pausedTask(ID, "Tell #ops.", proposal("int-1", ...actions));
  const service = new TaskService();
  service.tasks.set(ID, task);
  service.answerDecisionsWith(() =>
    continued(task, "Notification to #ops recorded.", "Sent."),
  );
  await service.serve(page);
  await page.goto(`/tasks/${ID}`);
  const heading =
    actions.length === 1
      ? CARD
      : `Freyja proposes ${actions.length} actions that need your decision`;
  await expect(page.getByRole("heading", { name: heading })).toBeVisible();
  await expect(
    page.getByText("Needs your decision", { exact: true }),
  ).toBeVisible();
  return service;
}

test("an action is approved and its continuation is shown on the same page", async ({
  page,
}) => {
  const service = await paused(page);
  await expect(
    page.getByRole("article", { name: "Proposed action send_notification" }),
  ).toContainText('"channel": "#ops"');

  await page.getByRole("button", { name: "Approve send_notification" }).click();

  await expect(conversation(page)).toContainText(
    "Notification to #ops recorded.",
  );
  await expect(conversation(page)).toContainText("Sent.");
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: CARD })).toBeHidden();
  expect(service.decided).toEqual([
    { threadId: ID, interrupt_id: "int-1", decisions: [{ type: "approve" }] },
  ]);
});

test("an action's arguments are edited and approved", async ({ page }) => {
  const service = await paused(page);
  await page
    .getByRole("button", { name: "Edit the arguments of send_notification" })
    .click();
  await page
    .getByLabel("Arguments for send_notification, as JSON")
    .fill('{"channel": "#ops-staging", "message": "Ready."}');
  await page
    .getByRole("button", {
      name: "Approve send_notification with these arguments",
    })
    .click();

  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  expect(service.decided[0].decisions).toEqual([
    { type: "edit", args: { channel: "#ops-staging", message: "Ready." } },
  ]);
});

test("an action is rejected with a reason", async ({ page }) => {
  const service = await paused(page);
  await page.getByRole("button", { name: "Reject send_notification" }).click();
  await page
    .getByLabel("Why send_notification is rejected (optional, Freyja is told)")
    .fill("Not before the review.");
  await page
    .getByRole("button", { name: "Reject send_notification now" })
    .click();

  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  expect(service.decided[0].decisions).toEqual([
    { type: "reject", message: "Not before the review." },
  ]);
});

test("an edit the service refuses keeps its text, with the reason beside it", async ({
  page,
}) => {
  const service = await paused(page);
  service.answerDecisionsWith(() => ({
    invalid: "Field required",
    loc: ["body", "decisions", 0, "args", "message"],
  }));
  await page
    .getByRole("button", { name: "Edit the arguments of send_notification" })
    .click();
  const box = page.getByLabel("Arguments for send_notification, as JSON");
  await box.fill('{"channel": "#ops"}');
  await page
    .getByRole("button", {
      name: "Approve send_notification with these arguments",
    })
    .click();

  await expect(alerts(page)).toHaveText("message: Field required");
  await expect(box).toHaveValue('{"channel": "#ops"}');
  // Nothing ran, and the proposal can still be decided.
  await expect(page.getByRole("heading", { name: CARD })).toBeVisible();
});

test("text that is not a JSON object is not sent", async ({ page }) => {
  const service = await paused(page);
  await page
    .getByRole("button", { name: "Edit the arguments of send_notification" })
    .click();
  await page
    .getByLabel("Arguments for send_notification, as JSON")
    .fill("channel: #ops");
  await expect(alerts(page)).toContainText("not valid JSON");
  await expect(
    page.getByRole("button", {
      name: "Approve send_notification with these arguments",
    }),
  ).toBeDisabled();
  expect(service.decided).toEqual([]);
});

test("a proposal decided elsewhere is said to be no longer pending, and the task is shown as it is", async ({
  page,
}) => {
  const service = await paused(page);
  // Another window decided it first.
  const task = service.tasks.get(ID);
  if (task === undefined) throw new Error("no task");
  service.tasks.set(ID, {
    row: { ...task.row, status: "done" },
    state: { ...task.state, pending: null },
  });
  service.answerDecisionsWith(() => ({
    refusal: {
      status: 409,
      code: "nothing_pending",
      message: "This thread has no pending decision.",
    },
  }));
  await page.getByRole("button", { name: "Approve send_notification" }).click();

  await expect(alerts(page)).toContainText("Nothing is pending");
  await expect(page.getByRole("heading", { name: CARD })).toBeHidden();
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
});

test("two actions in one proposal get a decision each, sent together in order", async ({
  page,
}) => {
  const note = {
    ...notify({ note: "Ready." }),
    name: "record_note",
    server: "notes",
    description: "Record a note.",
  };
  const service = await paused(page, [notify(), note]);
  const send = page.getByRole("button", { name: "Send the 2 decisions" });
  await expect(send).toBeDisabled();

  // Decided out of order: nothing is sent until both have a decision.
  await page.getByRole("button", { name: "Reject record_note" }).click();
  await expect(send).toBeDisabled();
  await page.getByRole("button", { name: "Approve send_notification" }).click();
  expect(service.decided).toEqual([]);
  await expect(send).toBeEnabled();
  await send.click();

  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  expect(service.decided[0].decisions).toEqual([
    { type: "approve" },
    { type: "reject" },
  ]);
});

test("Enter in a field sends no decision", async ({ page }) => {
  const service = await paused(page);
  await page
    .getByRole("button", { name: "Edit the arguments of send_notification" })
    .click();
  await page
    .getByLabel("Arguments for send_notification, as JSON")
    .press("Enter");
  await page.getByRole("button", { name: "Reject send_notification" }).click();
  await page
    .getByLabel("Why send_notification is rejected (optional, Freyja is told)")
    .press("Enter");
  await page.waitForTimeout(300);
  expect(service.decided).toEqual([]);
  await expect(page.getByRole("heading", { name: CARD })).toBeVisible();
});

test("a proposal is decided with the keyboard alone", async ({ page }) => {
  const service = await paused(page);
  const approve = page.getByRole("button", {
    name: "Approve send_notification",
  });
  // From the top of the page, Tab reaches the control; Enter presses it.
  await page.locator("body").focus();
  for (let tabs = 0; tabs < 40; tabs += 1) {
    await page.keyboard.press("Tab");
    if (
      await approve.evaluate((element) => element === document.activeElement)
    ) {
      break;
    }
  }
  await expect(approve).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  expect(service.decided).toHaveLength(1);
});
