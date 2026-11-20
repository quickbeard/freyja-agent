import { expect, type Page, test } from "@playwright/test";
import { answerProfiles, environmentProfile, profileList } from "./api";
import {
  ai,
  answerThreads,
  continued,
  finished,
  human,
  NO_USABLE_MODEL,
  notify,
  pausedTask,
  proposal,
  sse,
  start,
  summary,
  TaskService,
  threadState,
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
  await page.keyboard.press("Escape");
  await expect(page.getByText(/There is no task yet/)).toBeVisible();
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
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    UNREACHABLE,
  );
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

// 007, User Story 3: see every task and which ones wait for a decision.

const sections = (page: Page) =>
  page.getByRole("navigation", { name: "Sections" });
const tasksEntry = (page: Page) =>
  sections(page).getByRole("link", { name: "Tasks" });
// The entry with its number, which stands beside the link.
const tasksItem = (page: Page) => tasksEntry(page).locator("xpath=..");
const rows = (page: Page) =>
  page.getByRole("list", { name: "Tasks" }).getByRole("listitem");

/** What the sidebar's Tasks entry says of its number, for assistive technology. */
async function said(page: Page): Promise<string | null> {
  const id = await tasksEntry(page).getAttribute("aria-describedby");
  return id === null ? null : page.locator(`[id="${id}"]`).textContent();
}

test("the list shows one row per task, the ones that need a decision first", async ({
  page,
}) => {
  await answerThreads(page, {
    list: {
      threads: [
        summary("paused", {
          first_message: "Tell #ops.",
          status: "needs_decision",
          changed_at: "2026-11-20T07:00:00Z",
        }),
        summary("running", {
          first_message: "Summarize the week.",
          status: "running",
          changed_at: "2026-11-20T09:00:00Z",
        }),
        summary("failed", {
          first_message: "List the files.",
          status: "failed",
          changed_at: "2026-11-20T08:30:00Z",
        }),
        summary("done", {
          first_message: "Say hello.",
          status: "done",
          changed_at: "2026-11-20T08:00:00Z",
        }),
      ],
      needs_decision: 1,
      total: 4,
      more: false,
    },
  });
  await page.goto("/tasks");

  await expect(rows(page)).toHaveCount(4);
  await expect(rows(page).nth(0)).toContainText("Tell #ops.");
  await expect(rows(page).nth(0)).toContainText("Needs your decision");
  await expect(rows(page).nth(1)).toContainText("Running");
  await expect(rows(page).nth(2)).toContainText("Failed");
  await expect(rows(page).nth(3)).toContainText("Done");
  await expect(rows(page).nth(0).getByRole("link")).toHaveAttribute(
    "href",
    "/tasks/paused",
  );
});

test("the sidebar says how many tasks need a decision, on another section", async ({
  page,
}) => {
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  await answerThreads(page, {
    list: { threads: [], needs_decision: 3, total: 3, more: false },
  });
  await page.goto("/llm");

  await expect(tasksItem(page)).toContainText("3 to decide");
  expect(await said(page)).toBe("3 tasks need a decision");
});

test("the number goes once the proposal is decided, without a reload", async ({
  page,
}) => {
  const ID = "0123456789abcdef0123456789abcdef";
  const task = pausedTask(ID, "Tell #ops.", proposal("int-1", notify()));
  const service = new TaskService();
  service.tasks.set(ID, task);
  service.answerDecisionsWith(() =>
    continued(task, "Notification to #ops recorded.", "Sent."),
  );
  await service.serve(page);
  await answerProfiles(page, { list: profileList(environmentProfile()) });

  await page.goto("/llm");
  await expect(tasksItem(page)).toContainText("1 to decide");
  await tasksEntry(page).click();
  await expect(rows(page)).toHaveCount(1);
  await rows(page).first().getByRole("link").click();
  await page.getByRole("button", { name: "Approve send_notification" }).click();

  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  // The page was not reloaded: the number followed the decision.
  await expect(tasksItem(page)).not.toContainText("to decide");
  expect(await said(page)).toBeNull();
});

test("with the service stopped, the list says so and the sidebar shows no number", async ({
  page,
}) => {
  await page.goto("/tasks");
  await expect(alerts(page)).toContainText(UNREACHABLE);
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Tasks" })).toBeHidden();
  await expect(tasksItem(page)).not.toContainText("to decide");
});

test("a thread another client started is a row like any other, and opens", async ({
  page,
}) => {
  const service = new TaskService();
  service.tasks.set("by-curl", {
    row: summary("by-curl", { first_message: "Say hello." }),
    state: threadState("by-curl", [human("Say hello."), ai("Hello!")]),
  });
  await service.serve(page);
  await page.goto("/tasks");

  await expect(rows(page)).toHaveCount(1);
  await rows(page).first().getByRole("link").click();
  await expect(page).toHaveURL("/tasks/by-curl");
  await expect(conversation(page)).toContainText("Hello!");
});
