import { expect, type Page, test } from "@playwright/test";
import {
  agentTask,
  ai,
  human,
  summary,
  TaskService,
  threadState,
} from "./tasks-api";

// Feature 008, User Story 2: a working agent's progress on the task's page (FR-010 to
// FR-012). The page's clock is installed before it is opened, so its readings and the age
// of a text move when the test moves the clock, not with real time.

const ID = "0123456789abcdef0123456789abcdef";
const LEARNED = "2026-11-25T08:00:00Z";
const AGENT_SECONDS = 12;
const HANDED = [
  human("Hand this to the stub agent: steps 6 25"),
  ai("Handed to the stub agent."),
];

const agentTasks = (page: Page) =>
  page.getByRole("list", { name: "Agent tasks" }).getByRole("listitem");
const box = (page: Page) =>
  page.getByRole("region", { name: "Progress reported by stub" });
const lastReported = (page: Page) =>
  agentTasks(page)
    .first()
    .getByText(/^Last reported by the agent/);
const followUp = (page: Page) =>
  page.getByLabel("Send another message on this task");

/** Hold one task whose agent task is `task`, as the service would answer it. */
function hold(service: TaskService, task: ReturnType<typeof agentTask>) {
  service.tasks.set(ID, {
    row: summary(ID, {
      first_message: "Hand this to the stub agent: steps 6 25",
      status: task.status === "working" ? "waiting_for_agent" : "done",
    }),
    state: threadState(ID, HANDED, { agent_tasks: [task] }),
  });
}

const working = (text: string, learnedAt = LEARNED) =>
  agentTask({ progress: { text, learned_at: learnedAt } });

/** Whether Tab alone, from the agent task's message, reaches the progress box. */
async function tabReaches(page: Page): Promise<boolean> {
  await page.getByText("Started with:").click();
  for (let presses = 0; presses < 40; presses += 1) {
    await page.keyboard.press("Tab");
    if (
      await box(page).evaluate((element) => element === document.activeElement)
    ) {
      return true;
    }
  }
  return false;
}

/** Open the task's page with its clock `seconds` after the first text was learned. */
async function open(page: Page, service: TaskService, seconds = 30) {
  await service.serve(page);
  await page.clock.install({
    time: new Date(Date.parse(LEARNED) + seconds * 1000),
  });
  await page.goto(`/tasks/${ID}`);
  await expect(agentTasks(page)).toHaveCount(1);
}

test("a working agent task shows what its agent last reported, how long ago, and a later text without a reload", async ({
  page,
}) => {
  const service = new TaskService();
  hold(service, working("Step 1 of 6"));
  await open(page, service);

  await expect(box(page)).toHaveText("Step 1 of 6");
  await expect(lastReported(page)).toHaveText(
    "Last reported by the agent just now:",
  );
  const time = agentTasks(page).first().locator("time");
  await expect(time).toHaveAttribute("datetime", LEARNED);
  await expect(time).toHaveAttribute("title", /2026/);
  // A short text does not scroll, which alone would not make its box a stop of Tab.
  expect(await tabReaches(page)).toBe(true);

  // The agent moves on; the next reading of the page shows it.
  hold(service, working("Step 2 of 6", "2026-11-25T08:00:40Z"));
  await page.clock.fastForward(AGENT_SECONDS * 1000);
  await expect(box(page)).toHaveText("Step 2 of 6");
  await expect(lastReported(page)).toHaveText(
    "Last reported by the agent just now:",
  );

  // Nothing new for a minute: the age grows with the page's own readings.
  await page.clock.fastForward(60_000);
  await expect(lastReported(page)).toHaveText(
    "Last reported by the agent 1 minute ago:",
  );
  await expect(box(page)).toHaveText("Step 2 of 6");
});

test("a text that holds markup is shown as its characters", async ({
  page,
}) => {
  const text =
    '<b>bold</b><script>window.injected = true</script> see <a href="https://example.com">here</a>';
  const service = new TaskService();
  hold(service, working(text));
  await open(page, service);

  await expect(box(page)).toHaveText(text);
  await expect(box(page).locator("b, script, a")).toHaveCount(0);
  expect(
    await page.evaluate(() => (window as { injected?: boolean }).injected),
  ).toBeUndefined();
});

test("the longest text scrolls inside its box, Tab reaches the box, and the follow-up box can still be used", async ({
  page,
}) => {
  const text = "0123456789 ".repeat(182).slice(0, 2000);
  const service = new TaskService();
  hold(service, working(text));
  await open(page, service);

  await expect(box(page)).toHaveText(text);
  const sizes = await box(page).evaluate((element) => ({
    scroll: element.scrollHeight,
    client: element.clientHeight,
  }));
  expect(sizes.scroll).toBeGreaterThan(sizes.client);
  await box(page).evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  expect(
    await box(page).evaluate((element) => element.scrollTop),
  ).toBeGreaterThan(0);

  // The box keeps to its height, and what is below it is not covered by it: a click
  // reaches the follow-up box, which Playwright refuses when anything else would take it.
  expect(sizes.client).toBeLessThanOrEqual(200);
  await followUp(page).click();
  await page.keyboard.type("Any news?");
  await expect(followUp(page)).toHaveValue("Any news?");
});

for (const width of [1280, 390]) {
  test(`a long word with no space in it widens nothing, ${width} pixels wide`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 800 });
    const text = `https://files.example/${"a".repeat(1977)}`;
    const service = new TaskService();
    hold(service, working(text));
    await open(page, service);

    await expect(box(page)).toHaveText(text);
    const page_ = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(page_.scroll).toBeLessThanOrEqual(page_.client);
    const sizes = await box(page).evaluate((element) => ({
      width: element.getBoundingClientRect().width,
      scroll: element.scrollWidth,
      client: element.clientWidth,
    }));
    expect(sizes.width).toBeLessThanOrEqual(width);
    expect(sizes.scroll).toBeLessThanOrEqual(sizes.client);
  });
}

// Phase 8 (T048): what the service really sends, read in both browsers.

test("a time written with microseconds, as the service writes it, gives its age and its exact time", async ({
  page,
}) => {
  const service = new TaskService();
  hold(service, working("Step 1 of 6", "2026-11-25T08:00:00.259558Z"));
  await open(page, service, 90);

  await expect(lastReported(page)).toHaveText(
    "Last reported by the agent 1 minute ago:",
  );
  const time = agentTasks(page).first().locator("time");
  await expect(time).toHaveAttribute("datetime", "2026-11-25T08:00:00.259558Z");
  // The exact time is in the browser's time zone: 08:00 UTC is still November 24 far west.
  await expect(time).toHaveAttribute(
    "title",
    /^Nov 2[45], 2026, \d{1,2}:\d{2}/,
  );
});

test("a text holding a right-to-left override is shown as written, in a box the browser isolates", async ({
  page,
}) => {
  const override = String.fromCodePoint(0x202e);
  const text = `${override}Step 2 of 6, then the charts`;
  const service = new TaskService();
  hold(service, working(text));
  await open(page, service);

  await expect(box(page)).toHaveText(text);
  // The box isolates its text: an override in it reaches nothing outside it.
  expect(
    await box(page).evaluate(
      (element) => getComputedStyle(element).unicodeBidi,
    ),
  ).toBe("isolate");
  await expect(lastReported(page)).toHaveText(
    "Last reported by the agent just now:",
  );
  // The agent task's lines hold what they held before, the box last. (This is their text,
  // not their order on screen: each is a block of its own, which an override in another
  // cannot reach, and the box's own isolation is what the check above shows.)
  const lines = await agentTasks(page)
    .first()
    .evaluate((element) =>
      Array.from(element.children, (child) => child.textContent),
    );
  expect(lines).toEqual([
    "stubPendingThe agent is working",
    "Started with:",
    "slow 40",
    "Last reported by the agent just now:",
    text,
  ]);
});

test("an agent task that completed shows no progress", async ({ page }) => {
  const service = new TaskService();
  hold(
    service,
    agentTask({
      status: "completed",
      ended_at: "2026-11-25T08:05:00Z",
      outcome_delivered: true,
      progress: { text: "Step 5 of 6", learned_at: LEARNED },
    }),
  );
  await open(page, service);

  await expect(agentTasks(page).first()).toContainText("Completed");
  await expect(agentTasks(page).first()).not.toContainText("Step 5 of 6");
  await expect(box(page)).toHaveCount(0);
  await expect(agentTasks(page).first()).not.toContainText("Last reported");
});
