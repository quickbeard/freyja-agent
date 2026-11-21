import { createServer, type Server, type ServerResponse } from "node:http";
import { expect, type Page, test } from "@playwright/test";
import { STAND_IN_PORT } from "../playwright.config";
import {
  ai,
  END,
  human,
  piece,
  sse,
  start,
  summary,
  threadState,
} from "./tasks-api";

// 007, FR-028, research R1: a run's stream reaches the page through the console's own
// forwarding as it is produced, and not all at once when it ends. Every other spec hands a
// page its answers in the browser, whole; this one lets the page's requests through the web
// app's server, to a stand-in for the service on the address the app forwards to. It runs
// in projects of its own, after every other spec, since the others need nothing to answer
// at that address (playwright.config.ts).

const REPLY = "The first words of the reply, before the run has ended.";

let server: Server;
// The run's stream, held open until the test has seen what it sent so far.
let holding: ServerResponse | null = null;
const finished = new Set<string>();
// While true, the stand-in accepts a reading of the list and never answers it.
let silent = false;
// A task whose run ended another way than the first test's: how it rests, which the
// stand-in answers a reading of it with.
const rest = new Map<
  string,
  {
    status: "stopped" | "done";
    first: string;
    messages: ReturnType<typeof ai>[];
  }
>();
// What the web app's server passed on of each request to stop a run: the three headers the
// service reads to tell a page of another origin from the console (api/guards.py).
const stops: {
  thread: string;
  origin: string | undefined;
  site: string | undefined;
  asked: string | undefined;
}[] = [];
const one = (value: string | string[] | undefined) =>
  Array.isArray(value) ? value[0] : value;
// The `Host` each such request reached the stand-in with, by the thread it named.
const hosts = new Map<string, string | undefined>();

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  server = createServer((request, response) => {
    const path = new URL(request.url ?? "/", "http://stand-in").pathname;
    const run = path.match(/^\/threads\/([^/]+)\/runs\/unattended$/);
    if (request.method === "POST" && run !== null) {
      // The service's own answer: event by event, and not to be transformed (R1).
      response.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-store, no-transform",
      });
      response.write(start(run[1]));
      response.write(piece(REPLY));
      holding = response;
      return;
    }
    const stop = path.match(/^\/threads\/([^/]+)\/stop$/);
    if (request.method === "POST" && stop !== null) {
      hosts.set(stop[1], one(request.headers.host));
      stops.push({
        thread: stop[1],
        origin: one(request.headers.origin),
        site: one(request.headers["sec-fetch-site"]),
        asked: one(request.headers["x-forwarded-host"]),
      });
      // The stand-in refuses nothing: it only shows what reached it.
      response.writeHead(202, { "content-type": "application/json" });
      response.end(
        JSON.stringify({ thread_id: stop[1], stopping: true, run: 1 }),
      );
      return;
    }
    if (request.method === "GET" && path === "/threads" && silent) {
      // Accepted, and left without an answer.
      return;
    }
    const thread = path.match(/^\/threads\/([^/]+)(\/state)?$/);
    if (request.method === "GET" && thread !== null) {
      const id = thread[1];
      const rests = rest.get(id);
      if (rests !== undefined) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(
          JSON.stringify(
            thread[2] === undefined
              ? summary(id, {
                  first_message: rests.first,
                  status: rests.status,
                })
              : threadState(id, rests.messages),
          ),
        );
        return;
      }
      if (!finished.has(id)) {
        response.writeHead(404, { "content-type": "application/json" });
        response.end(
          JSON.stringify({
            detail: { code: "thread_not_found", message: "Not yet." },
          }),
        );
        return;
      }
      response.writeHead(200, { "content-type": "application/json" });
      response.end(
        JSON.stringify(
          thread[2] === undefined
            ? summary(id, { first_message: "Count slowly." })
            : threadState(id, [human("Count slowly."), ai(REPLY)]),
        ),
      );
      return;
    }
    response.writeHead(404, { "content-type": "application/json" });
    response.end(JSON.stringify({ detail: "Not Found" }));
  });
  await new Promise<void>((resolve) =>
    server.listen(STAND_IN_PORT, "127.0.0.1", resolve),
  );
});

test.afterAll(async () => {
  holding?.end();
  // A request left without an answer is still open.
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

test("a run's reply reaches the page through the console's forwarding before the run ends", async ({
  page,
}) => {
  await page.goto("/tasks");
  await page.getByRole("button", { name: "New task" }).click();
  await page.getByLabel("What should Freyja do?").fill("Count slowly.");
  await page.getByRole("button", { name: "Send" }).click();

  // The stand-in has not ended the stream: what the page shows arrived as it was sent.
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f]{32}$/);
  const conversation = page.getByRole("list", { name: "Conversation" });
  await expect(conversation).toContainText(REPLY, { timeout: 10_000 });
  await expect(page.getByText("Running", { exact: true })).toBeVisible();

  // Now the run ends, and the page reads the task as the service holds it.
  const id = new URL(page.url()).pathname.split("/").at(-1) ?? "";
  finished.add(id);
  expect(holding).not.toBeNull();
  holding?.end(END);
  holding = null;
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  await expect(conversation).toContainText(REPLY);
});

// 007, FR-022, FR-022a, User Story 5: Stop on the page that started the run and is reading
// its stream, which is where a person most often presses it. The specs that hand a page
// its answers whole can only show Stop on a run the page follows by reading
// (tasks-run.spec.ts); here the stream is open while Stop is pressed, and the page learns
// how the run ended from the run's last event.
const STOPPING = "The run is stopping. The step under way completes first.";
const TOO_LATE =
  "The run ended before the stop took effect. The task is shown as it ended.";
const conversation = (page: Page) =>
  page.getByRole("list", { name: "Conversation" });
const words = (page: Page, text: string) =>
  page.getByText(text, { exact: true });

/** Start a task from the section and wait for its reply to arrive; the task's id. */
async function startTask(page: Page, text: string): Promise<string> {
  await page.goto("/tasks");
  await page.getByRole("button", { name: "New task" }).click();
  await page.getByLabel("What should Freyja do?").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f]{32}$/);
  await expect(conversation(page)).toContainText(REPLY, { timeout: 10_000 });
  return new URL(page.url()).pathname.split("/").at(-1) ?? "";
}

test("Stop on the page that reads the run's stream says stopping, and stopped once the run's last event does", async ({
  page,
}) => {
  const id = await startTask(page, "Read every file.");

  await page
    .getByRole("button", { name: "Stop the run of the task: Read every file." })
    .click();

  // Stopping, in words, and no second Stop. The stream is still open: nothing has told
  // the page how the run ended, so it does not say stopped.
  await expect(page.getByText(STOPPING)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Stop/ })).toHaveCount(0);
  await expect
    .poll(() => stops.filter(({ thread }) => thread === id))
    .toHaveLength(1);
  await expect(words(page, "Stopped")).toBeHidden();

  // The step under way completes, and the run's last event says it ended as stopped.
  rest.set(id, {
    status: "stopped",
    first: "Read every file.",
    messages: [human("Read every file."), ai(REPLY)],
  });
  expect(holding).not.toBeNull();
  holding?.end(sse("end", { status: "stopped" }));
  holding = null;

  await expect(words(page, "Stopped")).toBeVisible();
  await expect(page.getByText(STOPPING)).toBeHidden();
  await expect(page.getByText(TOO_LATE)).toBeHidden();
  await expect(conversation(page)).toContainText(REPLY);
  // One press sent one stop, and a follow-up can continue the task.
  expect(stops.filter(({ thread }) => thread === id)).toHaveLength(1);
  await expect(
    page.getByLabel("Send another message on this task"),
  ).toBeEnabled();
});

test("a run that ends by itself after Stop was pressed is shown as it ended, and the page says the stop came too late", async ({
  page,
}) => {
  const id = await startTask(page, "Say hello.");

  await page.getByRole("button", { name: /^Stop/ }).click();
  await expect(page.getByText(STOPPING)).toBeVisible();
  await expect
    .poll(() => stops.filter(({ thread }) => thread === id))
    .toHaveLength(1);

  // The reply under way asked for nothing more: the run finished before a stop applied.
  rest.set(id, {
    status: "done",
    first: "Say hello.",
    messages: [human("Say hello."), ai(REPLY)],
  });
  holding?.end(END);
  holding = null;

  await expect(words(page, "Done")).toBeVisible();
  await expect(page.getByText(TOO_LATE)).toBeVisible();
  await expect(words(page, "Stopped")).toBeHidden();
  await expect(page.getByText(STOPPING)).toBeHidden();
});

// 007, FR-024: the service turns a page of another origin away by what the browser says of
// the request, and the console's forwarding stands between the two. This shows that what
// the browser says reaches the service through it: the origin, the site, and the host the
// browser asked for. The service's tests show that it refuses on exactly these
// (tests/test_threads_other_origins.py).
test("the console's forwarding passes on what a browser says of a request, its own and another origin's", async ({
  page,
  context,
  browserName,
  baseURL,
}) => {
  const consoleOrigin = new URL(baseURL ?? "").origin;
  const asked = new URL(consoleOrigin).host;

  // The console's own page asks.
  await page.goto("/tasks");
  const own = await page.evaluate(async () => {
    const answer = await fetch("/api/threads/own-request/stop", {
      method: "POST",
    });
    return answer.status;
  });
  expect(own).toBe(202);
  expect(stops.find(({ thread }) => thread === "own-request")).toEqual({
    thread: "own-request",
    origin: consoleOrigin,
    site: "same-origin",
    asked,
  });

  // A page of another origin asks, in a form a browser sends without asking first.
  if (browserName === "chromium") {
    await context.grantPermissions(["local-network-access"]);
  }
  expect(new URL(consoleOrigin).hostname).toBe("localhost");
  const otherOrigin = `http://127.0.0.1:${new URL(consoleOrigin).port}`;
  const elsewhere = `${otherOrigin}/a-page-of-another-origin`;
  await page.route(elsewhere, (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>Elsewhere</title><h1>Elsewhere</h1>",
    }),
  );
  await page.goto(elsewhere);
  await page.evaluate(async (address) => {
    await fetch(address, { method: "POST", mode: "no-cors" });
  }, `${consoleOrigin}/api/threads/other-request/stop`);

  await expect
    .poll(() => stops.find(({ thread }) => thread === "other-request"))
    .toBeDefined();
  const theirs = stops.find(({ thread }) => thread === "other-request");
  // Each of the two would be enough for the service to refuse: a site that is not the
  // console's own, and an origin that is not the host the browser asked for.
  expect(theirs?.site).toBe("cross-site");
  expect(theirs?.origin).toBe(otherOrigin);
  expect(theirs?.asked).toBe(asked);
  expect(new URL(theirs?.origin ?? "").host).not.toBe(theirs?.asked);
});

// The service also refuses a request made for a host name it does not answer for
// (api/hosts.py): it reads `Host`, and the name the console was asked under. A page whose
// own name was pointed at this machine (DNS rebinding) is a page of the console's origin to
// the browser, and such a page may write `X-Forwarded-Host` on its request. This shows that
// the console's server replaces what the page wrote with the name it was itself asked
// under, and that `Host` names the service: the page chooses neither. Were a later version
// of the forwarding to keep the page's value, the service would be told a name of the
// page's choosing, and this test is what fails.
test("a page cannot choose the name the console tells the service it was asked under", async ({
  page,
  baseURL,
}) => {
  const asked = new URL(baseURL ?? "").host;

  await page.goto("/tasks");
  const status = await page.evaluate(async () => {
    const answer = await fetch("/api/threads/forged-name/stop", {
      method: "POST",
      headers: { "X-Forwarded-Host": "forged.example" },
    });
    return answer.status;
  });
  expect(status).toBe(202);

  // Exactly the name the browser asked the console for: not the page's, and not both.
  const seen = stops.find(({ thread }) => thread === "forged-name");
  expect(seen?.asked).toBe(asked);
  expect(hosts.get("forged-name")).toBe(`127.0.0.1:${STAND_IN_PORT}`);
});

// 007, FR-006 (T083): the service accepts a reading and never answers it. The web app's
// server gives up forwarding it after 30 seconds, and the page then says what it says of a
// service that is down: nothing is left shown as if it were current. The test waits those
// 30 seconds in each browser; nothing shorter shows what the forwarding does.
test("the section says the service cannot be reached when a reading of the list gets no answer", async ({
  page,
}) => {
  test.setTimeout(90_000);
  silent = true;
  try {
    await page.goto("/tasks");
    const section = page.getByRole("main");
    // Until then the list is being read: no row, and no word that there is no task.
    await expect(section.getByText("Loading tasks")).toBeAttached();
    await expect(
      section.getByText("The Freyja service cannot be reached."),
    ).toBeVisible({ timeout: 60_000 });
    await expect(
      section.getByRole("button", { name: "Try again" }),
    ).toBeVisible();
    await expect(section.getByRole("list", { name: "Tasks" })).toHaveCount(0);
    await expect(section.getByText(/There is no task yet/)).toHaveCount(0);
  } finally {
    silent = false;
  }
});
