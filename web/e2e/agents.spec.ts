import { expect, test } from "@playwright/test";
import {
  AgentService,
  agentList,
  agentsBadge,
  agentsWarning,
  alerts,
  answerAgents,
  answerProfiles,
  card,
  cards,
  environmentAgent,
  environmentProfile,
  profileList,
  savedAgent,
  section,
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

  // Nothing on a card changes or removes an agent, and no address is on the page. A card's
  // one control has the service read the agent's card again.
  for (const shown of [hello, stub]) {
    await expect(shown.getByRole("button")).toHaveText(["Check"]);
    await expect(shown.getByRole("link")).toHaveCount(0);
    await expect(shown.getByText("Address")).toHaveCount(0);
  }
  await expect(page.getByRole("main")).not.toContainText("127.0.0.1");
  // The section's own controls read the list again and add an agent; neither is a card's.
  await expect(page.getByRole("main").getByRole("button")).toHaveText([
    /sidebar/i,
    "Refresh",
    "Add",
    "Check",
    "Check",
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

// 005, User Story 3: the console warns about every agent that cannot be used, on its card,
// above the cards, and on the sidebar from every section, all from one reading of the list.

const DOWN = {
  state: "unreachable",
  checked_at: "2026-11-19T09:04:10Z",
  reason: "Nothing answered at the address.",
} as const;

test("an agent that cannot be used shows on the sidebar from the LLM section, before the Agents section is opened", async ({
  page,
}) => {
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub", DOWN),
  ).serve(page);
  await page.goto("/llm");

  await expect(card(page, "Environment")).toBeVisible();
  // The number, and in words what it means, on the entry that leads to the agents.
  await expect(agentsBadge(page)).toHaveText("1");
  await expect(section(page, "Agents")).toHaveAccessibleDescription(
    "1 agent cannot be used",
  );
  await expect(section(page, "LLM")).toHaveAccessibleDescription("");
  // The LLM section itself says nothing of agents.
  await expect(agentsWarning(page)).toHaveCount(0);
  await expect(alerts(page)).toHaveCount(0);

  // The console asked for the list once when it opened, and for nothing else of agents.
  await page.waitForLoadState("networkidle");
  expect(page.url()).toContain("/llm");
  expect(service.asked).toEqual([
    { method: "GET", path: "/agents", body: undefined },
  ]);
});

test("in the Agents section the warning above the cards names the agent, and all three warnings go once a reading finds it reachable", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub", DOWN),
  ).serve(page);
  await page.goto("/agents");

  // Above the cards, naming the agent and saying in words that it cannot be used.
  const above = agentsWarning(page);
  await expect(above).toHaveText(
    "Warning: 1 agent cannot be used: stub. Its card says why.",
  );
  const [warningBox, firstCard] = [
    await above.boundingBox(),
    await cards(page).first().boundingBox(),
  ];
  if (warningBox === null || firstCard === null) throw new Error("no box");
  expect(warningBox.y + warningBox.height).toBeLessThanOrEqual(firstCard.y);
  // On the agent's own card, with the reason.
  const onCard = card(page, "stub").getByRole("note", { name: "Warning" });
  await expect(onCard).toContainText("Nothing answered at the address.");
  await expect(
    card(page, "hello").getByRole("note", { name: "Warning" }),
  ).toHaveCount(0);
  // And on the sidebar.
  await expect(agentsBadge(page)).toHaveText("1");

  // Something else found the agent since: a run handed it work. The next reading says so.
  service.found("stub", {
    state: "reachable",
    checked_at: "2026-11-19T09:30:00Z",
    reason: null,
  });
  await page.getByRole("button", { name: "Refresh" }).click();

  await expect(above).toHaveCount(0);
  await expect(onCard).toHaveCount(0);
  await expect(agentsBadge(page)).toHaveCount(0);
  await expect(section(page, "Agents")).toHaveAccessibleDescription("");
  await expect(card(page, "stub").getByText(/^Reachable/)).toBeVisible();
});

test("the warnings name every agent that cannot be used, unreachable or not in use, and no other", async ({
  page,
}) => {
  await new AgentService(
    environmentAgent("hello", DOWN),
    savedAgent("later", {
      id: "1".repeat(32),
      state: "not_tried",
      checked_at: null,
    }),
    savedAgent("shadowed", {
      id: "2".repeat(32),
      state: "not_in_use",
      checked_at: null,
      reason: "An agent named in the environment has this name.",
    }),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");

  await expect(agentsWarning(page)).toHaveText(
    "Warning: 2 agents cannot be used: hello, shadowed. Each one's card says why.",
  );
  await expect(agentsBadge(page)).toHaveText("2");
  await expect(section(page, "Agents")).toHaveAccessibleDescription(
    "2 agents cannot be used",
  );
  // An agent that is not in use can be neither checked nor changed; every other can be
  // checked, and a saved one changed.
  await expect(card(page, "shadowed").getByRole("button")).toHaveCount(0);
  await expect(card(page, "hello").getByRole("button")).toHaveText(["Check"]);
  for (const name of ["later", "stub"]) {
    await expect(card(page, name).getByRole("button")).toHaveText([
      "Check",
      "Edit",
    ]);
  }
});

test("with no answer for the list the sidebar shows no number, and a number shown before is not left as current", async ({
  page,
}) => {
  // The LLM section with a service that answers for profiles only: the request for the
  // agents reaches the web app's server, which finds nothing at the service's address.
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  await page.goto("/llm");
  await expect(card(page, "Environment")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expect(agentsBadge(page)).toHaveCount(0);
  await expect(section(page, "Agents")).toHaveAccessibleDescription("");
  // The section the operator is on says nothing about it either.
  await expect(alerts(page)).toHaveCount(0);

  // A service that answered, and then stops.
  const service = await new AgentService(savedAgent("stub", DOWN)).serve(page);
  await page.goto("/agents");
  await expect(agentsBadge(page)).toHaveText("1");
  service.stop();
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(alerts(page)).toContainText(UNREACHABLE);
  await expect(agentsBadge(page)).toHaveCount(0);
  await expect(agentsWarning(page)).toHaveCount(0);
});

test("opening the Agents section asks for the list once: the sidebar and the list are one reading", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub", DOWN),
  ).serve(page);
  await page.goto("/agents");

  // Both show what the list says, the sidebar outside the page and the cards inside it.
  await expect(card(page, "stub")).toBeVisible();
  await expect(agentsBadge(page)).toHaveText("1");
  await expect(agentsWarning(page)).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(service.reads).toBe(1);

  // Reading the list again is one request too, and both follow it.
  service.found("hello", DOWN);
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(agentsBadge(page)).toHaveText("2");
  await expect(agentsWarning(page)).toContainText("hello, stub");
  await page.waitForLoadState("networkidle");
  expect(service.reads).toBe(2);
  expect(service.changes).toEqual([]);
});

test("the list read on the LLM section is the one the Agents section starts from", async ({
  page,
}) => {
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  const service = await new AgentService(savedAgent("stub", DOWN)).serve(page);
  await page.goto("/llm");
  await expect(agentsBadge(page)).toHaveText("1");
  await page.waitForLoadState("networkidle");
  expect(service.reads).toBe(1);

  await section(page, "Agents").click();
  await expect(page).toHaveURL("/agents");
  // The card is there from the reading the sidebar already had; the section may read the
  // list again as it opens, and never more than once.
  await expect(card(page, "stub")).toBeVisible();
  await expect(agentsWarning(page)).toContainText("stub");
  await page.waitForLoadState("networkidle");
  expect(service.reads).toBeLessThanOrEqual(2);
  await expect(agentsBadge(page)).toHaveText("1");
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
