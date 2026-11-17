import { expect, test } from "@playwright/test";

// Holds whatever the first page shows, and needs neither the Freyja service nor the network:
// the built app is served, answers, and runs its first page without an uncaught error.
test("the web app serves its first page without an error", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  const response = await page.goto("/");
  // Give the page's scripts time to run, so an error of theirs is seen.
  await page.waitForLoadState("networkidle");

  expect(response?.ok()).toBe(true);
  await expect(page.locator("body")).toBeVisible();
  expect(errors).toEqual([]);
});
