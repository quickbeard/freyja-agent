import { createServer, type Server, type ServerResponse } from "node:http";
import { expect, test } from "@playwright/test";
import { STAND_IN_PORT } from "../playwright.config";
import {
  ai,
  END,
  human,
  piece,
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
      stops.push({
        thread: stop[1],
        origin: one(request.headers.origin),
        site: one(request.headers["sec-fetch-site"]),
        asked: one(request.headers["x-forwarded-host"]),
      });
      // The stand-in refuses nothing: it only shows what reached it.
      response.writeHead(202, { "content-type": "application/json" });
      response.end(JSON.stringify({ thread_id: stop[1], stopping: true }));
      return;
    }
    const thread = path.match(/^\/threads\/([^/]+)(\/state)?$/);
    if (request.method === "GET" && thread !== null) {
      const id = thread[1];
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
