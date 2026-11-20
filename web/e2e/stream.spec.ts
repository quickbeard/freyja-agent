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
