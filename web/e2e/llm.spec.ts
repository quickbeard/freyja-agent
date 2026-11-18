import { expect, test } from "@playwright/test";
import {
  alerts,
  answerProfiles,
  card,
  cards,
  environmentProfile,
  profileList,
  savedProfile,
} from "./api";

// 004, User Story 1: the LLM section shows which model Freyja runs on.

const UNREACHABLE = "The Freyja service cannot be reached.";

test("a service configured through its environment shows one card, active and read-only", async ({
  page,
}) => {
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  await page.goto("/llm");

  const environment = card(page, "Environment");
  await expect(environment).toBeVisible();
  await expect(cards(page)).toHaveCount(1);
  await expect(environment.getByText("Active", { exact: true })).toBeVisible();
  await expect(environment.getByText("From the environment")).toBeVisible();
  await expect(environment.getByText("OpenAI-compatible")).toBeVisible();
  await expect(environment.getByText("env-model")).toBeVisible();
  await expect(environment.getByText("http://10.0.0.5:31000/v1")).toBeVisible();
  await expect(environment.getByText("Chat completions")).toBeVisible();
  await expect(environment.getByText("Ends in 9f2c")).toBeVisible();
  await expect(environment.getByText("Not usable")).toBeHidden();
  // Nothing on the card changes or removes the profile: it can be tested, and that is all.
  await expect(environment.getByRole("button")).toHaveText(["Test"]);
  await expect(environment.getByRole("link")).toHaveCount(0);
  await expect(alerts(page)).toHaveCount(0);
});

test("saved profiles follow the environment's, and only the active one is marked", async ({
  page,
}) => {
  await answerProfiles(page, {
    list: profileList(
      environmentProfile({ active: false }),
      savedProfile("Claude", { active: true }),
      savedProfile("Spare", {
        id: "fedcba9876543210fedcba9876543210",
        usable: false,
        unusable_reason: "key_unreadable",
      }),
    ),
  });
  await page.goto("/llm");

  await expect(page.getByRole("heading", { level: 3 })).toHaveText([
    "Environment",
    "Claude",
    "Spare",
  ]);
  await expect(
    card(page, "Claude").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(card(page, "Claude").getByText("Anthropic")).toBeVisible();
  await expect(card(page, "Claude").getByText("Ends in 0001")).toBeVisible();
  await expect(
    card(page, "Claude").getByText("From the environment"),
  ).toBeHidden();
  await expect(
    card(page, "Environment").getByText("Active", { exact: true }),
  ).toBeHidden();
  // A profile whose key cannot be read says so, and is not the active one.
  await expect(card(page, "Spare").getByText("Not usable")).toBeVisible();
  await expect(
    card(page, "Spare").getByText(/stored key cannot be read/),
  ).toBeVisible();
  await expect(
    card(page, "Spare").getByText("Active", { exact: true }),
  ).toBeHidden();
  // A usable model is active, so there is nothing to warn about.
  await expect(alerts(page)).toHaveCount(0);
});

test("the section says so when no usable model is active", async ({ page }) => {
  await answerProfiles(page, {
    list: profileList(
      environmentProfile({
        key_hint: "",
        usable: false,
        unusable_reason: "not_configured",
      }),
    ),
  });
  await page.goto("/llm");

  await expect(alerts(page)).toContainText("No usable model is active");
  const environment = card(page, "Environment");
  await expect(environment.getByText("Not usable")).toBeVisible();
  await expect(environment.getByText("Not shown")).toBeVisible();
  await expect(
    environment.getByText("The environment names no key for this provider."),
  ).toBeVisible();
});

test("a refusal shows the service's reason and no card", async ({ page }) => {
  await answerProfiles(page, {
    refusal: {
      status: 503,
      code: "internal",
      message: "The profiles cannot be read.",
    },
  });
  await page.goto("/llm");

  await expect(alerts(page)).toContainText("The profiles cannot be read.");
  await expect(cards(page)).toHaveCount(0);
});

test("Refresh reads the list again", async ({ page }) => {
  await answerProfiles(
    page,
    { list: profileList(environmentProfile()) },
    {
      list: profileList(
        environmentProfile({ active: false }),
        savedProfile("Claude", { active: true }),
      ),
    },
  );
  await page.goto("/llm");
  await expect(card(page, "Environment")).toBeVisible();
  await expect(card(page, "Claude")).toHaveCount(0);

  await page.getByRole("button", { name: "Refresh" }).click();

  await expect(
    card(page, "Claude").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(
    card(page, "Environment").getByText("Active", { exact: true }),
  ).toBeHidden();
});

test("with the service stopped the section says so, and recovers when asked to try again", async ({
  page,
}) => {
  // No answer is handed to the page, so its request goes to the web app's server, which
  // forwards it to a service that is not there (playwright.config.ts).
  await page.goto("/llm");

  const alert = alerts(page);
  await expect(alert).toContainText(UNREACHABLE);
  await expect(cards(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh" })).toHaveCount(0);

  // The service is back.
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  await alert.getByRole("button", { name: "Try again" }).click();

  await expect(card(page, "Environment")).toBeVisible();
  await expect(alerts(page)).toHaveCount(0);
});

test("cards shown before are not shown as current once the service stops answering", async ({
  page,
}) => {
  await answerProfiles(page, { list: profileList(environmentProfile()) });
  await page.goto("/llm");
  await expect(card(page, "Environment")).toBeVisible();

  // From here the page's requests reach the web app's server and the absent service.
  await page.unroute("**/api/llm/profiles");
  await page.getByRole("button", { name: "Refresh" }).click();

  await expect(alerts(page)).toContainText(UNREACHABLE);
  await expect(cards(page)).toHaveCount(0);
});
