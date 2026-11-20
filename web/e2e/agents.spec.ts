import { expect, test } from "@playwright/test";
import {
  agentList,
  alerts,
  answerAgents,
  card,
  cards,
  environmentAgent,
  savedAgent,
} from "./api";

// 005, User Story 1: the Agents section shows which agents Freyja can hand work to.

const UNREACHABLE = "The Freyja service cannot be reached.";

test("two agents from the environment show as two cards, with nothing to change them by", async ({
  page,
}) => {
  await answerAgents(page, {
    list: agentList(
      environmentAgent("hello", { approval: "none" }),
      environmentAgent("stub"),
    ),
  });
  await page.goto("/agents");

  await expect(page.getByRole("heading", { level: 3 })).toHaveText([
    "hello",
    "stub",
  ]);
  await expect(cards(page)).toHaveCount(2);

  const hello = card(page, "hello");
  await expect(hello.getByText("From the environment")).toBeVisible();
  await expect(hello.getByText("Runs without approval")).toBeVisible();
  await expect(hello.getByText(/^Reachable/)).toBeVisible();
  // What its card says, as the agent's own claim.
  const said = hello.getByRole("group", {
    name: "What the agent says of itself",
  });
  await expect(said.getByText("hello agent", { exact: true })).toBeVisible();
  await expect(
    said.getByText("What the hello agent says it does."),
  ).toBeVisible();
  await expect(said.getByText("1.0.0")).toBeVisible();
  await expect(said.getByText("Writes a short report.")).toBeVisible();

  const stub = card(page, "stub");
  await expect(stub.getByText("From the environment")).toBeVisible();
  await expect(stub.getByText("Requires approval")).toBeVisible();

  // Nothing on a card changes or removes an agent, and no address is on the page.
  for (const shown of [hello, stub]) {
    await expect(shown.getByRole("button")).toHaveCount(0);
    await expect(shown.getByRole("link")).toHaveCount(0);
    await expect(shown.getByText("Address")).toHaveCount(0);
  }
  await expect(page.getByRole("main")).not.toContainText("127.0.0.1");
  // The section's own controls read the list again and add an agent; neither is a card's.
  await expect(page.getByRole("main").getByRole("button")).toHaveText([
    /sidebar/i,
    "Refresh",
    "Add",
  ]);
  await expect(alerts(page)).toHaveCount(0);
});

test("a saved agent's card shows its address, after the environment's agents", async ({
  page,
}) => {
  await answerAgents(page, {
    list: agentList(
      environmentAgent("hello"),
      savedAgent("stub", { url: "http://127.0.0.1:9998/agents/stub" }),
    ),
  });
  await page.goto("/agents");

  await expect(page.getByRole("heading", { level: 3 })).toHaveText([
    "hello",
    "stub",
  ]);
  const stub = card(page, "stub");
  await expect(stub.getByText("Address")).toBeVisible();
  await expect(
    stub.getByText("http://127.0.0.1:9998/agents/stub"),
  ).toBeVisible();
  await expect(stub.getByText("From the environment")).toHaveCount(0);
  await expect(card(page, "hello").getByText("Address")).toHaveCount(0);
});

test("an agent that cannot be reached carries a warning with the reason", async ({
  page,
}) => {
  await answerAgents(page, {
    list: agentList(
      environmentAgent("hello"),
      environmentAgent("stub", {
        state: "unreachable",
        checked_at: "2026-11-19T09:04:10Z",
        reason: "Nothing answered at the address.",
        card: null,
      }),
      savedAgent("later", {
        state: "not_tried",
        checked_at: null,
      }),
      savedAgent("shadowed", {
        id: "fedcba9876543210fedcba9876543210",
        state: "not_in_use",
        checked_at: null,
        reason: "An agent named in the environment has this name.",
      }),
    ),
  });
  await page.goto("/agents");

  const stub = card(page, "stub");
  const warning = stub.getByRole("note", { name: "Warning" });
  await expect(warning).toContainText("Warning");
  await expect(warning).toContainText("This agent cannot be reached.");
  await expect(warning).toContainText("Nothing answered at the address.");
  await expect(warning.locator("time")).toHaveAttribute(
    "datetime",
    "2026-11-19T09:04:10Z",
  );
  await expect(stub.getByText("Unreachable")).toBeVisible();
  await expect(stub.getByText("Its card has not been read yet.")).toBeVisible();

  const unused = card(page, "shadowed").getByRole("note", { name: "Warning" });
  await expect(unused).toContainText("This agent is not in use.");
  await expect(unused).toContainText(
    "An agent named in the environment has this name.",
  );
  // Nothing tried it, so there is no time to give.
  await expect(unused.locator("time")).toHaveCount(0);

  // An agent nothing is known against carries none.
  await expect(
    card(page, "hello").getByRole("note", { name: "Warning" }),
  ).toHaveCount(0);
  const later = card(page, "later");
  await expect(
    later.getByText("This instance has not tried the agent yet."),
  ).toBeVisible();
  await expect(later.getByRole("note", { name: "Warning" })).toHaveCount(0);
});

test("with no agent the section says that none is configured", async ({
  page,
}) => {
  await answerAgents(page, { list: agentList() });
  await page.goto("/agents");

  await expect(page.getByText("No agent is configured.")).toBeVisible();
  await expect(cards(page)).toHaveCount(0);
  await expect(alerts(page)).toHaveCount(0);
});

test("with the service stopped the section says so, and Try again reads the list once it answers", async ({
  page,
}) => {
  // No answer is handed to the page: its request reaches the web app's server, which finds
  // nothing at the service's address.
  await page.goto("/agents");

  const alert = alerts(page);
  await expect(alert).toContainText(UNREACHABLE);
  await expect(cards(page)).toHaveCount(0);

  await answerAgents(page, { list: agentList(environmentAgent("hello")) });
  await alert.getByRole("button", { name: "Try again" }).click();

  await expect(card(page, "hello")).toBeVisible();
  await expect(alerts(page)).toHaveCount(0);
});

test("a refusal shows the service's reason, and cards shown before are not left as current", async ({
  page,
}) => {
  await answerAgents(
    page,
    { list: agentList(environmentAgent("hello")) },
    {
      refusal: {
        status: 500,
        code: "internal",
        message: "The saved agents could not be read.",
      },
    },
  );
  await page.goto("/agents");
  await expect(card(page, "hello")).toBeVisible();

  await page.getByRole("button", { name: "Refresh" }).click();

  await expect(alerts(page)).toContainText(
    "The saved agents could not be read.",
  );
  await expect(cards(page)).toHaveCount(0);
});

test("card text with markup is shown as text, and the page asks for nothing because of it", async ({
  page,
}) => {
  const image =
    '<img src="http://elsewhere.example/pixel.png" onerror="window.pwned = true">';
  const link = "[click](http://elsewhere.example/phish)";
  const script = "<script>window.pwned = true</script>";
  const asked: string[] = [];
  page.on("request", (request) => asked.push(request.url()));
  await answerAgents(page, {
    list: agentList(
      savedAgent("stub", {
        state: "unreachable",
        reason: '<a href="http://elsewhere.example/why">why</a>',
        card: {
          name: image,
          description: link,
          version: script,
          skills: [
            {
              id: "s",
              name: '<iframe src="http://elsewhere.example/frame"></iframe>',
              description: "http://elsewhere.example/plain",
              tags: ["<b>tag</b>"],
            },
          ],
          cut: true,
        },
      }),
    ),
  });
  await page.goto("/agents");

  const stub = card(page, "stub");
  await expect(stub.getByText(image)).toBeVisible();
  await expect(stub.getByText(link)).toBeVisible();
  // The script tag is the test's own, looked for as text on the page: it is the proof
  // that the page made nothing of it. Semgrep takes it for a value written into a page.
  // nosemgrep: javascript.lang.security.audit.unknown-value-with-script-tag.unknown-value-with-script-tag
  await expect(stub.getByText(script)).toBeVisible();
  await expect(
    stub.getByText('<iframe src="http://elsewhere.example/frame"></iframe>'),
  ).toBeVisible();
  await expect(
    stub.getByText('<a href="http://elsewhere.example/why">why</a>'),
  ).toBeVisible();
  await expect(stub.getByText(/was cut/)).toBeVisible();
  // Nothing was made of any of it.
  await expect(stub.locator("img, a, iframe, script, b")).toHaveCount(0);
  expect(
    await page.evaluate(() => (window as { pwned?: boolean }).pwned),
  ).toBeUndefined();
  // And nothing was asked of the address the text names.
  await page.waitForLoadState("networkidle");
  expect(asked.filter((url) => url.includes("elsewhere.example"))).toEqual([]);
});

test.describe("in a narrow window", () => {
  // Under the sidebar's 768-pixel breakpoint (004 FR-001).
  test.use({ viewport: { width: 400, height: 800 } });

  test("every card can be reached, one below the other", async ({ page }) => {
    const names = ["hello", "stub", "third", "fourth"];
    await answerAgents(page, {
      list: agentList(
        ...names.map((name, index) =>
          savedAgent(name, {
            id: `${index}`.repeat(32),
            url: `http://127.0.0.1:${9100 + index}/a/rather/long/path/that/has/to/wrap`,
          }),
        ),
      ),
    });
    await page.goto("/agents");

    await expect(cards(page)).toHaveCount(4);
    const viewport = page.viewportSize();
    let below = 0;
    for (const name of names) {
      const shown = card(page, name);
      await shown.scrollIntoViewIfNeeded();
      await expect(shown).toBeInViewport();
      const box = await shown.boundingBox();
      if (box === null || viewport === null) throw new Error("no box");
      // Inside the window from side to side, and under the card before it.
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
      const top = box.y + (await page.evaluate(() => window.scrollY));
      expect(top).toBeGreaterThanOrEqual(below);
      below = top + box.height;
    }
    // The page does not scroll sideways to show them.
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  });
});
