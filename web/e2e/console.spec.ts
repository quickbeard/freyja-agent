import { expect, type Page, test } from "@playwright/test";
import {
  agentList,
  answerAgents,
  answerProfiles,
  card,
  environmentAgent,
  environmentProfile,
  profileList,
} from "./api";

// 004, User Story 1: the console's shell. The sidebar stays in place while the page changes.

test.beforeEach(async ({ page }) => {
  await answerProfiles(page, { list: profileList(environmentProfile()) });
});

const sections = (page: Page) =>
  page.getByRole("navigation", { name: "Sections" });

test("the console opens on the LLM section", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveURL("/llm");
  await expect(page).toHaveTitle("LLM · Freyja");
  await expect(
    page.getByRole("heading", { level: 1, name: "LLM" }),
  ).toBeVisible();
  const llm = sections(page).getByRole("link", { name: "LLM" });
  const agents = sections(page).getByRole("link", { name: "Agents" });
  await expect(llm).toHaveAttribute("aria-current", "page");
  await expect(agents).not.toHaveAttribute("aria-current");
  // Feature 007: the third section.
  const tasks = sections(page).getByRole("link", { name: "Tasks" });
  await expect(tasks).toHaveAttribute("href", "/tasks");
  await expect(tasks).not.toHaveAttribute("aria-current");
});

test("the sidebar leads to the Agents section, which lists the agents", async ({
  page,
}) => {
  await answerAgents(page, { list: agentList(environmentAgent("hello")) });
  await page.goto("/llm");
  await sections(page).getByRole("link", { name: "Agents" }).click();

  await expect(page).toHaveURL("/agents");
  await expect(page).toHaveTitle("Agents · Freyja");
  await expect(
    page.getByRole("heading", { level: 1, name: "Agents" }),
  ).toBeVisible();
  await expect(card(page, "hello")).toBeVisible();
  await expect(
    sections(page).getByRole("link", { name: "Agents" }),
  ).toHaveAttribute("aria-current", "page");
  await expect(
    sections(page).getByRole("link", { name: "LLM" }),
  ).not.toHaveAttribute("aria-current");

  // And back again: the sidebar is still there.
  await sections(page).getByRole("link", { name: "LLM" }).click();
  await expect(page).toHaveURL("/llm");
});

test("the sidebar is set apart from the page by its color and an edge", async ({
  page,
}) => {
  await page.goto("/llm");
  await expect(sections(page)).toBeVisible();

  const look = await page.evaluate(() => {
    const style = (selector: string) => {
      const element = document.querySelector(selector);
      if (element === null) throw new Error(`nothing matches ${selector}`);
      return getComputedStyle(element);
    };
    // How light a color is, from 0 to 255, as the browser paints it.
    const lightness = (color: string) => {
      const canvas = document.createElement("canvas");
      canvas.width = 1;
      canvas.height = 1;
      const context = canvas.getContext("2d");
      if (context === null) throw new Error("no canvas");
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      const [red, green, blue] = context.getImageData(0, 0, 1, 1).data;
      return (red + green + blue) / 3;
    };
    const edge = style('[data-slot="sidebar-container"]');
    return {
      sidebar: lightness(style('[data-slot="sidebar-inner"]').backgroundColor),
      page: lightness(style("main").backgroundColor),
      edge: lightness(edge.borderRightColor),
      edgeWidth: edge.borderRightWidth,
    };
  });

  // Differences an eye can see. The stock theme's were 5 between the sidebar and the page,
  // and 21 and 26 between the edge and the two, and the sidebar could not be told apart.
  expect(Math.abs(look.sidebar - look.page)).toBeGreaterThanOrEqual(10);
  expect(look.edgeWidth).toBe("1px");
  expect(Math.abs(look.edge - look.sidebar)).toBeGreaterThanOrEqual(30);
  expect(Math.abs(look.edge - look.page)).toBeGreaterThanOrEqual(30);
});

test.describe("in a narrow window", () => {
  // Under the sidebar's 768-pixel breakpoint (FR-001).
  test.use({ viewport: { width: 500, height: 800 } });

  test("the sidebar is hidden until its control opens it", async ({ page }) => {
    await page.goto("/llm");
    await expect(
      page.getByRole("heading", { level: 3, name: "Environment" }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Agents" })).toBeHidden();

    await page.locator('[data-sidebar="trigger"]').click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByRole("link", { name: "LLM" })).toBeVisible();
    await sheet.getByRole("link", { name: "Agents" }).click();

    // Choosing a section closes the sidebar, so the page it leads to can be seen.
    await expect(page).toHaveURL("/agents");
    await expect(sheet).toBeHidden();
    await expect(
      page.getByRole("heading", { level: 1, name: "Agents" }),
    ).toBeVisible();
  });
});

// 005, FR-029: no page of another origin may show the console in a frame.

const FRAME_HEADERS = {
  "content-security-policy": "frame-ancestors 'none'",
  "x-frame-options": "DENY",
};

for (const path of ["/llm", "/agents"]) {
  test(`${path} is answered with the headers that forbid a frame`, async ({
    request,
  }) => {
    const response = await request.get(path);

    expect(response.status()).toBe(200);
    expect(response.headers()).toMatchObject(FRAME_HEADERS);
  });
}

test("a page of another origin that frames the console is shown nothing of it", async ({
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
  // The same server under its other name is another origin to a browser: the console runs
  // on localhost, and this page is served from 127.0.0.1.
  const framedConsole = new URL("/llm", baseURL);
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

  await page.goto(elsewhere);
  await expect(page.getByRole("heading", { name: "Elsewhere" })).toBeVisible();
  await expect(
    page
      .frameLocator('iframe[title="allowed"]')
      .getByText("This frame is shown."),
  ).toBeVisible();

  // Nothing of the console is in its frame: not its title, its heading, or a profile. A
  // frame the browser refused holds the browser's own error page or nothing, and neither
  // answers like a page, so each read gives up after two seconds and counts as empty.
  const others = page
    .frames()
    .filter((frame) => frame !== page.mainFrame() && frame.url() !== allowed);
  expect(others).toHaveLength(1);
  // A frame that is allowed finishes loading; one that is refused may never say so.
  await others[0].waitForLoadState("load", { timeout: 5_000 }).catch(() => {});
  const shown = await Promise.race([
    others[0]
      .evaluate(() => `${document.title}\n${document.body?.innerText ?? ""}`)
      .catch(() => ""),
    new Promise<string>((resolve) => setTimeout(() => resolve(""), 2_000)),
  ]);
  expect(shown).not.toContain("Freyja");
  expect(shown).not.toContain("LLM");
  expect(shown).not.toContain("Environment");

  // The same address opened as a page of its own shows the console, so the frame was
  // empty because of the rule and not because nothing was served.
  await page.goto(framedConsole.href);
  await expect(
    page.getByRole("heading", { level: 1, name: "LLM" }),
  ).toBeVisible();
});
