import { expect, type Page, test } from "@playwright/test";
import {
  finished,
  NO_USABLE_MODEL,
  sse,
  start,
  TaskService,
} from "./tasks-api";

// 007, User Story 1: hand Freyja a task and read the result.

const UNREACHABLE = "The Freyja service cannot be reached.";

// The page's own alerts: Next.js adds a route announcer, an alert of its own, after a
// navigation.
const alerts = (page: Page) =>
  page.locator('[role="alert"]:not(#__next-route-announcer__)');

const conversation = (page: Page) =>
  page.getByRole("list", { name: "Conversation" });

async function newTask(page: Page, text: string) {
  await page.goto("/tasks");
  await page.getByRole("button", { name: "New task" }).click();
  await page.getByLabel("What should Freyja do?").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
}

test("a new task is sent from the Tasks section and its result is read", async ({
  page,
}) => {
  const service = await new TaskService().serve(page);
  await page.goto("/tasks");
  await expect(
    page.getByRole("heading", { level: 1, name: "Tasks" }),
  ).toBeVisible();
  await expect(page.getByRole("note")).toContainText(
    "The console has no users",
  );
  await expect(
    page
      .getByRole("navigation", { name: "Sections" })
      .getByRole("link", { name: "Tasks" }),
  ).toHaveAttribute("aria-current", "page");

  await newTask(page, "Summarize what you can do.");

  await expect(page).toHaveURL(/\/tasks\/[0-9a-f]{32}$/);
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  await expect(conversation(page)).toContainText("Summarize what you can do.");
  await expect(conversation(page)).toContainText("Done.");
  expect(service.sent).toHaveLength(1);
  const [{ threadId }] = service.sent;
  expect(page.url()).toContain(`/tasks/${threadId}`);

  // A reload, and another browser, find the same conversation at the same address.
  await page.reload();
  await expect(conversation(page)).toContainText("Summarize what you can do.");
  await expect(conversation(page)).toContainText("Done.");
  expect(service.sent).toHaveLength(1);
});

test("a message the service refuses keeps its text and lists no task", async ({
  page,
}) => {
  const service = await new TaskService(() => ({
    invalid: "String should have at most 32000 characters",
  })).serve(page);
  await newTask(page, "Far too long.");

  await expect(alerts(page)).toHaveText(
    "The message was refused: String should have at most 32000 characters",
  );
  await expect(page.getByLabel("What should Freyja do?")).toHaveValue(
    "Far too long.",
  );
  await expect(page).toHaveURL("/tasks");
  expect(service.tasks.size).toBe(0);
});

test("a new task sent with no usable model says why and offers its text again", async ({
  page,
}) => {
  const service = await new TaskService((id) => ({
    events: [
      start(id),
      sse("error", { code: "no_usable_model", message: NO_USABLE_MODEL }),
    ],
    after: null,
  })).serve(page);
  await newTask(page, "Summarize the week.");

  await expect(page).toHaveURL(/\/tasks\/[0-9a-f]{32}$/);
  await expect(alerts(page)).toContainText(NO_USABLE_MODEL);
  await expect(
    page.getByRole("link", { name: "Go to the LLM section" }),
  ).toHaveAttribute("href", "/llm");
  await expect(page.getByText("Task not found")).toBeHidden();
  const again = page.getByLabel("Send the message again");
  await expect(again).toHaveValue("Summarize the week.");

  // Once a model is usable, the same text is sent again on the same task.
  service.answerRunsWith(finished("Here is the week."));
  await page.getByRole("button", { name: "Send again" }).click();
  await expect(conversation(page)).toContainText("Here is the week.");
  expect(service.sent.map(({ threadId }) => threadId)).toEqual([
    service.sent[0].threadId,
    service.sent[0].threadId,
  ]);
});

test("an address whose id the service does not accept shows that no task is there", async ({
  page,
}) => {
  await new TaskService().serve(page);
  await page.goto("/tasks/not%20an%20id");
  await expect(
    page.getByRole("heading", { level: 1, name: "Task not found" }),
  ).toBeVisible();
  await page.goto("/tasks/0123456789abcdef0123456789abcdef");
  await expect(
    page.getByRole("heading", { level: 1, name: "Task not found" }),
  ).toBeVisible();
});

test("with the service stopped, a new task keeps its text and a task cannot be read", async ({
  page,
}) => {
  // No answer is handed to the page: its requests reach the web app's server, which finds
  // nothing at the service's address.
  await newTask(page, "Summarize the week.");
  await expect(alerts(page)).toContainText(UNREACHABLE);
  await expect(page.getByLabel("What should Freyja do?")).toHaveValue(
    "Summarize the week.",
  );

  await page.goto("/tasks/0123456789abcdef0123456789abcdef");
  await expect(alerts(page)).toContainText(UNREACHABLE);
  await expect(conversation(page)).toBeHidden();
});

test("markup and links in a task's text are shown as characters", async ({
  page,
}) => {
  const markup =
    '<b>bold</b> <a href="https://example.test/">click here</a> <img src="https://example.test/i.png">';
  await new TaskService(finished(markup)).serve(page);
  await newTask(page, markup);

  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  await expect(conversation(page)).toContainText("<b>bold</b>");
  await expect(conversation(page)).toContainText(
    '<a href="https://example.test/">',
  );
  await expect(conversation(page).locator("b, a, img")).toHaveCount(0);
});
