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

// FR-014, FR-015 (T099): a whole number beyond 2^53, such as a 64-bit id. Both browsers the
// console is tested in keep a number's own text when they read JSON, so the card shows the
// id digit for digit and an edit sends it as it was. The service's answer is handed to the
// page as text here: built as a value in this test, the id would already be another.
test("a proposed 64-bit id is shown in full and sent back as it was when another argument is edited", async ({
  page,
}) => {
  const task = pausedTask(
    ID,
    "Close the record.",
    proposal("int-1", {
      ...notify(),
      name: "close_record",
      args: { id: "THE-ID", note: "Closed." },
    }),
  );
  const service = new TaskService();
  service.tasks.set(ID, task);
  service.answerDecisionsWith(() =>
    continued(task, "Record closed.", "Closed."),
  );
  await service.serve(page);
  const exact = (value: unknown) =>
    JSON.stringify(value).replaceAll('"THE-ID"', "1234567890123456789");
  await page.route(`**/api/threads/${ID}/state`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: exact(service.tasks.get(ID)?.state),
    });
  });
  await page.goto(`/tasks/${ID}`);

  const card = page.getByRole("article", {
    name: "Proposed action close_record",
  });
  await expect(card).toContainText('"id": 1234567890123456789,');
  await expect(card).not.toContainText("too large for a browser");
  // In the conversation too, where Freyja's message asks for the action.
  await expect(conversation(page)).toContainText('"id": 1234567890123456789');

  await page
    .getByRole("button", { name: "Edit the arguments of close_record" })
    .click();
  const box = page.getByLabel("Arguments for close_record, as JSON");
  await expect(box).toHaveValue(/"id": 1234567890123456789,/);
  await box.fill((await box.inputValue()).replace("Closed.", "Closed today."));
  const sent = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/api/threads/${ID}/resume/unattended`),
  );
  await page
    .getByRole("button", { name: "Approve close_record with these arguments" })
    .click();

  // What left the browser, as text: the id as it was proposed, the note as it was edited.
  expect((await sent).postData()).toBe(
    '{"interrupt_id":"int-1","decisions":[{"type":"edit","args":{"id":1234567890123456789,"note":"Closed today."}}]}',
  );
});

// FR-018, Edge Cases "The answer to a decision never arrives" (T107): the decision may or
// may not have been applied. The page says that it is not known, never that it failed,
// reads the task again, and shows the proposal only when it is still pending.
test("a decision that gets no answer is said to be of unknown outcome, and its proposal is shown again while it is pending", async ({
  page,
}) => {
  const service = await paused(page);
  // The connection drops: the request leaves the page and no answer comes back.
  await page.route(`**/api/threads/${ID}/resume/unattended`, (route) =>
    route.abort("connectionreset"),
  );

  await page.getByRole("button", { name: "Approve send_notification" }).click();

  await expect(alerts(page)).toContainText(
    "No answer came, so whether the decision was applied is not known. The task is read again, and the proposal is shown only if it is still pending.",
  );
  await expect(alerts(page)).not.toContainText("failed");
  // It was not applied: the task was read again, and the proposal is there to decide.
  await expect(
    page.getByRole("button", { name: "Approve send_notification" }),
  ).toBeVisible();
  await expect(
    page.getByText("Needs your decision", { exact: true }),
  ).toBeVisible();
  expect(service.decided).toEqual([]);
});

test("a decision that was applied though its answer never came leaves no proposal on the page", async ({
  page,
}) => {
  const service = await paused(page);
  const task = service.tasks.get(ID);
  if (task === undefined) {
    throw new Error("no task");
  }
  const carried = continued(task, "Notification to #ops recorded.", "Sent.");
  if (!("after" in carried) || carried.after === null) {
    throw new Error("the continuation was refused");
  }
  const after = carried.after;
  await page.route(`**/api/threads/${ID}/resume/unattended`, async (route) => {
    // The service took the decision and carried it out; its answer is lost on the way.
    service.tasks.set(ID, after);
    await route.abort("connectionreset");
  });

  await page.getByRole("button", { name: "Approve send_notification" }).click();

  await expect(alerts(page)).toContainText("is not known");
  await expect(conversation(page)).toContainText("Sent.");
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: CARD })).toBeHidden();
});
