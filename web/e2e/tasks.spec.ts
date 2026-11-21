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
          error: {
            code: "step_limit",
            message: "The run reached the limit of 50 steps and was ended.",
          },
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
  // A failed task's row gives the service's reason (FR-003).
  await expect(rows(page).nth(2)).toContainText(
    "The run reached the limit of 50 steps and was ended.",
  );
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

// SC-004, FR-006 (T084): a task that comes to need a decision elsewhere is announced
// within fifteen seconds, on any section, by the list's timer alone: nothing on this page
// is pressed and nothing is reloaded. The change is made just after a reading, which is
// when the next one is furthest away, and the page's clock is paused, so that no time
// passes but what the test lets pass: thirteen seconds, which leave the reading its own.
test("the sidebar's number appears within fifteen seconds of a change made elsewhere, by the timer alone", async ({
  page,
}) => {
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  let waiting = 0;
  let readings = 0;
  await page.route("**/api/threads", async (route) => {
    readings += 1;
    await route.fulfill({
      json: {
        threads: [],
        needs_decision: waiting,
        total: waiting,
        more: false,
      },
    });
  });
  await page.clock.install();
  await page.goto("/llm");
  await expect(tasksEntry(page)).toBeVisible();
  await expect.poll(() => readings).toBeGreaterThan(0);
  await expect(tasksItem(page)).not.toContainText("to decide");
  await page.clock.pauseAt(Date.now() + 2_000);

  // On to the next reading the timer makes, a second at a time.
  const first = readings;
  for (let second = 0; readings === first && second < 30; second += 1) {
    await page.clock.runFor(1_000);
    await page.waitForTimeout(100);
  }
  expect(readings).toBe(first + 1);

  waiting = 1;
  await page.clock.runFor(13_000);

  await expect(tasksItem(page)).toContainText("1 to decide");
  expect(await said(page)).toBe("1 task needs a decision");
  expect(readings).toBe(first + 2);
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

test("with the service stopped, the list says so and the sidebar says the number is not known", async ({
  page,
}) => {
  await page.goto("/tasks");
  await expect(alerts(page)).toContainText(UNREACHABLE);
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Tasks" })).toBeHidden();
  // No number, and not the look of "none need a decision" either (T085).
  await expect(tasksItem(page)).not.toContainText("to decide");
  await expect(tasksItem(page)).toContainText("not known");
  expect(await said(page)).toBe(
    "How many tasks need a decision is not known: the list of tasks could not be read.",
  );
});

// Edge Cases, "The Tasks section is opened in a narrow window": every control stays
// reachable, as in the LLM and Agents sections (T094).
test.describe("the Tasks section in a narrow window", () => {
  // Under the sidebar's 768-pixel breakpoint, and narrower than a phone held upright.
  test.use({ viewport: { width: 360, height: 740 } });

  const NARROW = "0123456789abcdef0123456789abcdef";
  /**
   * What reaches past the window's right edge, so that part of the page is out of reach:
   * nothing, when the page fits. Each is named by its tag and how its text begins.
   */
  const overflows = (page: Page) =>
    page.evaluate(() => {
      const width = document.documentElement.clientWidth;
      if (document.documentElement.scrollWidth <= width) {
        return [];
      }
      return [...document.querySelectorAll("main *")]
        .filter((element) => element.getBoundingClientRect().right > width + 1)
        .map(
          (element) =>
            `${element.tagName.toLowerCase()}: ${(element.textContent ?? "").slice(0, 40)}`,
        );
    });
  /** Whether a control lies within the window's width, once it is scrolled to. */
  async function reachable(page: Page, name: string | RegExp) {
    const control = page.getByRole("button", { name });
    await control.scrollIntoViewIfNeeded();
    await expect(control).toBeVisible();
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(360);
  }

  test("the list, New task, and a task with a proposal keep every control within reach", async ({
    page,
  }) => {
    const long =
      "Tell #ops that the quarterly report is ready, and name every team that still has to read it before Friday.";
    const task = pausedTask(NARROW, long, proposal("int-1", notify()));
    const service = new TaskService();
    service.tasks.set(NARROW, task);
    service.answerDecisionsWith(() =>
      continued(task, "Notification to #ops recorded.", "Sent."),
    );
    await service.serve(page);

    // The list: the notice, New task, and the row, none wider than the window.
    await page.goto("/tasks");
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("Needs your decision");
    expect(await overflows(page)).toEqual([]);
    await reachable(page, "New task");

    // The sidebar is a sheet here; its Tasks entry says the number in words.
    await page.locator('[data-sidebar="trigger"]').click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByRole("link", { name: "Tasks" })).toBeVisible();
    await expect(sheet).toContainText("1 to decide");
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();

    // New task: the box and the button that sends.
    await page.getByRole("button", { name: "New task" }).click();
    await expect(page.getByLabel("What should Freyja do?")).toBeVisible();
    await reachable(page, "Send");
    expect(await overflows(page)).toEqual([]);

    // A task's page with a proposal, reached from its row: each decision.
    await page.goto("/tasks");
    await rows(page).first().getByRole("link").click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${NARROW}$`));
    await expect(
      page.getByRole("article", { name: "Proposed action send_notification" }),
    ).toBeVisible();
    expect(await overflows(page)).toEqual([]);
    await reachable(page, "Approve send_notification");
    await reachable(page, "Edit the arguments of send_notification");
    await reachable(page, "Reject send_notification");

    // And a decision can be sent from there.
    await page
      .getByRole("button", { name: "Edit the arguments of send_notification" })
      .click();
    await expect(
      page.getByLabel("Arguments for send_notification, as JSON"),
    ).toBeVisible();
    await reachable(page, "Approve send_notification with these arguments");
    expect(await overflows(page)).toEqual([]);
    await page
      .getByRole("button", { name: "Approve send_notification as proposed" })
      .click();
    await expect(page.getByText("Done", { exact: true })).toBeVisible();
    expect(service.decided).toHaveLength(1);
    expect(await overflows(page)).toEqual([]);
  });
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

// 007, User Story 6: a page of another origin cannot show the Tasks section in a frame.
// A frame is how such a page would get a person to press Approve, or Stop, on a control
// they cannot see. The rule is feature 005's (FR-029), for every path of the console.

const FRAME_HEADERS = {
  "content-security-policy": "frame-ancestors 'none'",
  "x-frame-options": "DENY",
};
const FRAMED = "0123456789abcdef0123456789abcdef";
const FRAMED_PATHS = ["/tasks", `/tasks/${FRAMED}`];

for (const path of FRAMED_PATHS) {
  test(`${path} is answered with the headers that forbid a frame`, async ({
    request,
  }) => {
    const response = await request.get(path);

    expect(response.status()).toBe(200);
    expect(response.headers()).toMatchObject(FRAME_HEADERS);
  });

  test(`a page of another origin that frames ${path} is shown nothing of the console`, async ({
    page,
    context,
    browserName,
    baseURL,
  }) => {
    // Chromium asks before a page may reach an address on this machine, and refuses the
    // frame's request unasked. With the permission given, the request is made, so that what
    // keeps the frame empty is the console's answer and not the browser's own caution.
    if (browserName === "chromium") {
      await context.grantPermissions(["local-network-access"]);
    }
    // A task that waits for a decision: what a frame would be after is its Approve.
    const service = new TaskService();
    service.tasks.set(
      FRAMED,
      pausedTask(FRAMED, "Tell #ops.", proposal("int-1", notify())),
    );
    await service.serve(page);
    const asked: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/")) {
        asked.push(request.url());
      }
    });

    // The same server under its other name is another origin to a browser: the console
    // runs on localhost, and this page is served from 127.0.0.1.
    const framedConsole = new URL(path, baseURL);
    expect(framedConsole.hostname).toBe("localhost");
    const origin = `http://127.0.0.1:${framedConsole.port}`;
    const elsewhere = `${origin}/a-page-of-another-origin`;
    // A second frame, of a page that forbids nothing, to know when frames have been drawn.
    const allowed = `${origin}/a-frame-that-may-be-shown`;
    await page.route(allowed, (route) =>
      route.fulfill({
        contentType: "text/html",
        body: "<!doctype html><title>Allowed</title><p>This frame is shown.</p>",
      }),
    );
    await page.route(elsewhere, (route) =>
      route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><title>Elsewhere</title><h1>Elsewhere</h1>
          <iframe title="console" src="${framedConsole.href}" width="900" height="400"></iframe>
          <iframe title="allowed" src="${allowed}" width="900" height="100"></iframe>`,
      }),
    );

    // The frame's request for the console's page, and the console's answer to it: what
    // keeps the frame empty is seen to be that answer, not a request that never left.
    const answered = page.waitForResponse(
      (response) => response.url() === framedConsole.href,
    );
    await page.goto(elsewhere);
    const answer = await answered;
    expect(answer.status()).toBe(200);
    expect(answer.headers()).toMatchObject(FRAME_HEADERS);
    await expect(
      page.getByRole("heading", { name: "Elsewhere" }),
    ).toBeVisible();
    await expect(
      page
        .frameLocator('iframe[title="allowed"]')
        .getByText("This frame is shown."),
    ).toBeVisible();

    // Nothing of the console is in its frame: not its title, the section, the task, or a
    // control. A frame the browser refused holds the browser's own error page or nothing,
    // and neither answers like a page, so the read gives up after two seconds and counts
    // as empty.
    const others = page
      .frames()
      .filter((frame) => frame !== page.mainFrame() && frame.url() !== allowed);
    expect(others).toHaveLength(1);
    // A frame that is allowed finishes loading; one that is refused may never say so.
    await others[0]
      .waitForLoadState("load", { timeout: 5_000 })
      .catch(() => {});
    const shown = await Promise.race([
      others[0]
        .evaluate(() => `${document.title}\n${document.body?.innerText ?? ""}`)
        .catch(() => ""),
      new Promise<string>((resolve) => setTimeout(() => resolve(""), 2_000)),
    ]);
    for (const words of ["Freyja", "Tasks", "Tell #ops.", "Approve", "Stop"]) {
      expect(shown).not.toContain(words);
    }
    // The console never ran in the frame: it asked the service for nothing, and sent it
    // nothing, also a moment later, when a console that did run would have asked for its
    // list of tasks.
    await page.waitForTimeout(1_000);
    expect(asked).toEqual([]);
    expect(service.sent).toEqual([]);
    expect(service.decided).toEqual([]);
    expect(service.stops).toEqual([]);

    // The same address opened as a page of its own shows the console, so the frame was
    // empty because of the rule and not because nothing was served.
    await page.goto(framedConsole.href);
    await expect(
      path === "/tasks"
        ? page.getByRole("heading", { level: 1, name: "Tasks" })
        : page.getByRole("button", { name: "Approve send_notification" }),
    ).toBeVisible();
    await expect(page.getByText("Tell #ops.").first()).toBeVisible();
    expect(asked.length).toBeGreaterThan(0);
  });
}
