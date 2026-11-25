import { expect, test } from "@playwright/test";
import {
  ai,
  callerTask,
  human,
  summary,
  TaskService,
  threadState,
} from "./tasks-api";

// 009: tasks other agents hand Freyja over A2A, as the console shows them.

const CALLER_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0";
const PERSON_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90";

function both(): TaskService {
  const service = new TaskService();
  callerTask(
    service,
    CALLER_ID,
    "Compile the weekly report.",
    "It is compiled.",
  );
  service.tasks.set(PERSON_ID, {
    row: summary(PERSON_ID, {
      first_message: "Summarize the week.",
      changed_at: "2026-11-20T09:00:00Z",
    }),
    state: threadState(PERSON_ID, [
      human("Summarize the week."),
      ai("Summarized."),
    ]),
  });
  return service;
}

// User Story 1: a caller's task is listed and read, and says who started it.

test("a task another agent started is listed and opened as started by it", async ({
  page,
}) => {
  await both().serve(page);
  await page.goto("/tasks");
  const tasks = page.getByRole("list", { name: "Tasks" });
  const fromAgent = tasks.getByRole("listitem").filter({
    hasText: "Compile the weekly report.",
  });
  await expect(fromAgent).toContainText("Started by an agent on this machine");
  // A task a person started is not marked.
  await expect(
    tasks.getByRole("listitem").filter({ hasText: "Summarize the week." }),
  ).not.toContainText("Started by");

  await fromAgent.getByRole("link").click();
  await expect(page).toHaveURL(`/tasks/${CALLER_ID}`);
  // The list the page came from stays in the document, hidden, after the navigation.
  await expect(
    page
      .getByText("Started by an agent on this machine")
      .filter({ visible: true }),
  ).toBeVisible();
  await expect(page.getByRole("list", { name: "Conversation" })).toContainText(
    "It is compiled.",
  );
});

// User Story 2, the part request 2 builds: no box on a caller's task, and why.

test("a task another agent started has no box for another message, and says why", async ({
  page,
}) => {
  const service = await both().serve(page);
  await page.goto(`/tasks/${CALLER_ID}`);
  await expect(
    page.getByText(/only that agent sends messages on it/),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
  expect(service.sent).toHaveLength(0);

  // A task a person started keeps its box, and says nothing of an agent.
  await page.goto(`/tasks/${PERSON_ID}`);
  await expect(page.getByRole("textbox")).toBeVisible();
  await expect(page.getByText(/Started by/)).toHaveCount(0);
  await expect(page.getByText(/only that agent sends messages/)).toHaveCount(0);
});
