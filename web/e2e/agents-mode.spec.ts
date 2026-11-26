import { expect, type Page, test } from "@playwright/test";
import {
  AgentService,
  card,
  environmentAgent,
  savedAgent,
  section,
} from "./api";
import {
  ai,
  finished,
  human,
  notify,
  pausedTask,
  proposal,
  summary,
  TaskService,
  threadState,
} from "./tasks-api";

// Feature 010: the agents' approval mode beside the Tasks section's Send buttons (FR-008, as
// the owner placed it on 2026-11-26), and each agent's own mode, Manual or Auto, on its card
// in the Agents section.

const STUB_ID = "0123456789abcdef0123456789abcdef";
const TASK_ID = "fedcba9876543210fedcba9876543210";

const dropdown = (page: Page) =>
  page.getByRole("combobox", { name: "Agent approvals" });
const asked = (page: Page) => page.getByRole("alertdialog");
const newTask = (page: Page) => page.getByRole("dialog", { name: "New task" });

test("the dropdown beside New task's Send sets Manual at once, and Auto only once confirmed", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello", { approval: "none" }),
    savedAgent("stub", { approval: "none" }),
  ).serve(page);
  await page.goto("/tasks");

  // Beside a Send button only: not in the sidebar, and not on the list of tasks.
  await expect(section(page, "Tasks")).toBeVisible();
  await expect(dropdown(page)).toHaveCount(0);
  await page.getByRole("button", { name: "New task" }).click();
  await newTask(page)
    .getByLabel("What should Freyja do?")
    .fill("Summarize the week.");
  await expect(dropdown(page)).toContainText("Auto");
  await expect(
    newTask(page).getByText(
      "For every task: agents set to Auto run without approval; the others ask.",
    ),
  ).toBeVisible();

  await dropdown(page).click();
  await page.getByRole("option", { name: "Manual" }).click();
  await expect(dropdown(page)).toContainText("Manual");
  await expect(asked(page)).toHaveCount(0);
  expect(service.changes).toEqual([
    { method: "PUT", path: "/agents/mode", body: { mode: "manual" } },
  ]);

  // Auto lets every agent set to Auto run without approval: it is asked first.
  await dropdown(page).click();
  await page.getByRole("option", { name: "Auto" }).click();
  await expect(asked(page)).toContainText("2 agents are set to Auto now");
  await asked(page).getByRole("button", { name: "Cancel" }).click();
  await expect(asked(page)).toHaveCount(0);
  await expect(dropdown(page)).toContainText("Manual");
  expect(service.changes).toHaveLength(1);

  await dropdown(page).click();
  await page.getByRole("option", { name: "Auto" }).click();
  await asked(page).getByRole("button", { name: "Set to Auto" }).click();
  await expect(asked(page)).toHaveCount(0);
  await expect(dropdown(page)).toContainText("Auto");
  expect(service.changes).toEqual([
    { method: "PUT", path: "/agents/mode", body: { mode: "manual" } },
    { method: "PUT", path: "/agents/mode", body: { mode: "auto" } },
  ]);
  // The task being written stays as it was, to be sent by its own button.
  await expect(newTask(page)).toBeVisible();
  await expect(newTask(page).getByLabel("What should Freyja do?")).toHaveValue(
    "Summarize the week.",
  );
});

test("a task's page has the dropdown beside Send message, and not while a proposal waits", async ({
  page,
}) => {
  const agents = await new AgentService(
    environmentAgent("hello", { approval: "none" }),
  ).serve(page);
  const tasks = new TaskService();
  tasks.tasks.set(TASK_ID, {
    row: summary(TASK_ID, { first_message: "Summarize the week." }),
    state: threadState(TASK_ID, [
      human("Summarize the week."),
      ai("Here it is."),
    ]),
  });
  tasks.answerRunsWith(finished("Quieter."));
  await tasks.serve(page);
  await page.goto(`/tasks/${TASK_ID}`);

  const box = page.getByLabel("Send another message on this task");
  await box.fill("And the week before?");
  await expect(dropdown(page)).toContainText("Auto");
  await dropdown(page).click();
  await page.getByRole("option", { name: "Manual" }).click();
  await expect(dropdown(page)).toContainText("Manual");
  expect(agents.changes).toEqual([
    { method: "PUT", path: "/agents/mode", body: { mode: "manual" } },
  ]);
  // Choosing a mode sent no message: that is the button's.
  await expect(box).toHaveValue("And the week before?");
  expect(tasks.sent).toEqual([]);

  // With the keyboard alone: it stands left of the button, so Tab reaches it first, and the
  // message is still sent from the box (007 FR-012a).
  await box.focus();
  await page.keyboard.press("Tab");
  await expect(dropdown(page)).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("button", { name: "Send message" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Quieter.")).toBeVisible();
  expect(tasks.sent).toEqual([
    { threadId: TASK_ID, message: "And the week before?" },
  ]);

  // While a proposal waits, its card takes the box's place, the dropdown with it.
  tasks.tasks.set(
    TASK_ID,
    pausedTask(TASK_ID, "Tell #ops.", proposal("int-1", notify())),
  );
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Approve send_notification" }),
  ).toBeVisible();
  await expect(dropdown(page)).toBeHidden();
});

test("a saved agent's card switches its own mode behind a confirmation, and says when Manual holds it", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello", { approval: "none" }),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");

  const stub = card(page, "stub");
  const toggle = stub.getByRole("switch", { name: "Auto mode for stub" });
  await expect(toggle).not.toBeChecked();
  await expect(stub.getByText("Manual", { exact: true })).toBeVisible();
  // An environment agent's mode is shown, and is not changed here.
  await expect(card(page, "hello").getByRole("switch")).toHaveCount(0);
  await expect(
    card(page, "hello").getByText("Auto", { exact: true }),
  ).toBeVisible();

  await toggle.click();
  await expect(asked(page)).toContainText("Set stub to Auto?");
  expect(service.changes).toEqual([]);
  await asked(page).getByRole("button", { name: "Set to Auto" }).click();
  await expect(asked(page)).toHaveCount(0);
  await expect(toggle).toBeChecked();
  await expect(stub.getByText("Auto", { exact: true })).toBeVisible();
  expect(service.changes).toEqual([
    {
      method: "PATCH",
      path: `/agents/${STUB_ID}`,
      body: { version: 1, approval: "none" },
    },
  ]);

  // With the agents' approvals set to Manual beside New task's Send, an Auto agent is held
  // to Manual, and its card says so.
  await expect(dropdown(page)).toHaveCount(0);
  await section(page, "Tasks").click();
  await page.getByRole("button", { name: "New task" }).click();
  await dropdown(page).click();
  await page.getByRole("option", { name: "Manual" }).click();
  await expect(dropdown(page)).toContainText("Manual");
  await page.keyboard.press("Escape");
  await expect(newTask(page)).toHaveCount(0);
  await section(page, "Agents").click();
  await expect(stub.getByText(/^Held to Manual/)).toBeVisible();
  await expect(card(page, "hello").getByText(/^Held to Manual/)).toBeVisible();
});
