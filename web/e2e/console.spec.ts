import { expect, type Page, test } from "@playwright/test";
import { answerProfiles, environmentProfile, profileList } from "./api";

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
});

test("the sidebar leads to the Agents section, which offers nothing to do yet", async ({
  page,
}) => {
  await page.goto("/llm");
  await sections(page).getByRole("link", { name: "Agents" }).click();

  await expect(page).toHaveURL("/agents");
  await expect(page).toHaveTitle("Agents · Freyja");
  await expect(
    page.getByRole("heading", { level: 1, name: "Agents" }),
  ).toBeVisible();
  await expect(
    page.getByText("Agents are not managed from the console yet."),
  ).toBeVisible();
  await expect(
    sections(page).getByRole("link", { name: "Agents" }),
  ).toHaveAttribute("aria-current", "page");
  await expect(
    sections(page).getByRole("link", { name: "LLM" }),
  ).not.toHaveAttribute("aria-current");
  // The only control to be seen in the page is the shell's own, which shows and hides the
  // sidebar. (The section left behind stays in the document, hidden.)
  const controls = page
    .getByRole("main")
    .locator("button:visible, a:visible, input:visible, select:visible");
  await expect(controls).toHaveCount(1);
  await expect(controls).toHaveAttribute("data-sidebar", "trigger");

  // And back again: the sidebar is still there.
  await sections(page).getByRole("link", { name: "LLM" }).click();
  await expect(page).toHaveURL("/llm");
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
