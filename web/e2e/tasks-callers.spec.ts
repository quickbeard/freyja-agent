import { expect, type Page, test } from "@playwright/test";
import {
  ai,
  byCaller,
  callerTask,
  canceledByCaller,
  continued,
  human,
  notify,
  pausedTask,
  proposal,
  runningTask,
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

// User Story 2: a person decides what a caller's task proposes, as for any task.

const PAUSED_ID = "5d2f0c1e9a7b4c33a1f08e6d2b7c9a40";
const CARD = "Freyja proposes an action that needs your decision";
const SAID =
  "Started by an agent on this machine. No person asked for this action: the agent's task led Freyja to propose it.";

const tasksItem = (page: Page) =>
  page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: /^Tasks/ })
    .locator("xpath=..");

async function pausedByCaller(page: Page): Promise<TaskService> {
  const task = byCaller(
    pausedTask(
      PAUSED_ID,
      "Compile and tell #ops.",
      proposal("int-9", notify()),
    ),
  );
  const service = new TaskService();
  service.tasks.set(PAUSED_ID, task);
  service.answerDecisionsWith(() =>
    continued(task, "Notification to #ops recorded.", "Told them."),
  );
  await service.serve(page);
  await page.goto("/tasks");
  await expect(tasksItem(page)).toContainText("1 to decide");
  await page.goto(`/tasks/${PAUSED_ID}`);
  await expect(page.getByRole("region", { name: CARD })).toContainText(SAID);
  return service;
}

test("a caller's task that needs a decision is counted, says who started it, and is approved", async ({
  page,
}) => {
  const service = await pausedByCaller(page);
  await page.getByRole("button", { name: "Approve send_notification" }).click();
  await expect(page.getByRole("list", { name: "Conversation" })).toContainText(
    "Told them.",
  );
  await expect(page.getByRole("heading", { name: CARD })).toBeHidden();
  expect(service.decided).toEqual([
    {
      threadId: PAUSED_ID,
      interrupt_id: "int-9",
      decisions: [{ type: "approve" }],
    },
  ]);
});

test("a caller's task's proposal is rejected as any", async ({ page }) => {
  const service = await pausedByCaller(page);
  await page.getByRole("button", { name: "Reject send_notification" }).click();
  await page
    .getByRole("button", { name: "Reject send_notification now" })
    .click();
  await expect(page.getByRole("heading", { name: CARD })).toBeHidden();
  expect(service.decided[0].decisions).toEqual([{ type: "reject" }]);
});

test("Stop is offered for a caller's task's run in progress", async ({
  page,
}) => {
  const service = new TaskService();
  service.tasks.set(
    PAUSED_ID,
    byCaller(runningTask(PAUSED_ID, "Read every file.")),
  );
  await service.serve(page);
  await page.goto(`/tasks/${PAUSED_ID}`);
  await page
    .getByRole("button", { name: "Stop the run of the task: Read every file." })
    .click();
  await expect.poll(() => service.stops).toEqual([PAUSED_ID]);
  // And still no box for another message.
  await expect(page.getByRole("textbox")).toHaveCount(0);
});

// User Story 4: the agent that started a task cancels it.

const CANCELED_ID = "7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a21";

test("a caller's task its agent canceled is not counted to decide and says the agent canceled", async ({
  page,
}) => {
  const service = new TaskService();
  const ended = byCaller(
    pausedTask(
      CANCELED_ID,
      "Compile and tell #ops.",
      proposal("int-7", notify()),
    ),
  );
  service.tasks.set(
    CANCELED_ID,
    canceledByCaller({
      row: { ...ended.row, status: "stopped" },
      state: { ...ended.state, pending: null },
    }),
  );
  await service.serve(page);
  await page.goto("/tasks");
  await expect(
    page.getByRole("list", { name: "Tasks" }).getByRole("listitem"),
  ).toContainText("Started by an agent on this machine");
  await expect(tasksItem(page)).not.toContainText("to decide");
  await page.goto(`/tasks/${CANCELED_ID}`);
  await expect(
    page.getByText(
      "The agent that started this task canceled it. Nothing the task still proposed can be approved any more: Freyja rejects it by itself, for that cancel and not as a person's decision. What ran before the cancel is in the conversation.",
    ),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: CARD })).toHaveCount(0);
});

test("an approval refused because the agent canceled is said so, and the proposal is still rejected", async ({
  page,
}) => {
  const service = await pausedByCaller(page);
  const task = service.tasks.get(PAUSED_ID);
  if (task === undefined) {
    throw new Error("the paused task is missing");
  }
  service.answerDecisionsWith((_threadId, body) =>
    body.decisions[0].type === "reject"
      ? continued(task, "Rejected.", "It was not sent.")
      : {
          refusal: {
            status: 409,
            code: "requester_gone",
            message:
              "The agent that asked for this canceled its task, so the action is not carried out. The proposal can only be rejected.",
          },
        },
  );
  await page.getByRole("button", { name: "Approve send_notification" }).click();
  await expect(
    page.getByRole("region", { name: CARD }).getByRole("alert"),
  ).toHaveText(
    "The agent that started this task canceled it, so this action can no longer be approved. Reject it to close the proposal; nothing of it runs.",
  );
  await page.getByRole("button", { name: "Reject send_notification" }).click();
  await page
    .getByRole("button", { name: "Reject send_notification now" })
    .click();
  await expect(page.getByRole("heading", { name: CARD })).toBeHidden();
  expect(service.decided.map((sent) => sent.decisions)).toEqual([
    [{ type: "approve" }],
    [{ type: "reject" }],
  ]);
});

// User Story 3: an agent on another host, named by the deployment.

const NAMED_ID = "3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d";
const GONE_ID = "9f8e7d6c5b4a39281706f5e4d3c2b1a0";

test("a task started by a named agent says so, and so does one whose agent is no longer configured", async ({
  page,
}) => {
  const service = new TaskService();
  callerTask(service, NAMED_ID, "Scan the staging host.", "It is scanned.");
  callerTask(service, GONE_ID, "Scan the old host.", "It was scanned.");
  for (const [id, configured] of [
    [NAMED_ID, true],
    [GONE_ID, false],
  ] as const) {
    const task = service.tasks.get(id);
    if (task === undefined) {
      throw new Error("the task is missing");
    }
    service.tasks.set(id, {
      ...task,
      row: {
        ...task.row,
        caller: { name: "artex", canceled: !configured, configured },
      },
    });
  }
  await service.serve(page);
  await page.goto("/tasks");
  const tasks = page.getByRole("list", { name: "Tasks" });
  // The exact words: "Started by agent artex" is also the start of the other row's.
  await expect(
    tasks
      .getByRole("listitem")
      .filter({ hasText: "Scan the staging host." })
      .getByText("Started by agent artex", { exact: true }),
  ).toBeVisible();
  await expect(
    tasks.getByRole("listitem").filter({ hasText: "Scan the old host." }),
  ).toContainText("Started by agent artex, which is no longer configured");
  await page.goto(`/tasks/${GONE_ID}`);
  await expect(
    page
      .getByText("Started by agent artex, which is no longer configured")
      .filter({ visible: true }),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
