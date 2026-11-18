import { expect, type Page, test } from "@playwright/test";
import {
  alerts,
  card,
  cards,
  environmentProfile,
  ProfileService,
  savedProfile,
} from "./api";

// 004, User Story 2: add a profile and switch Freyja to it.

const KEY = "sk-recognizable-key-0001";
const WHERE_CONVERSATIONS_GO =
  "Existing conversations will be sent to its endpoint from their next run.";

const form = (page: Page) =>
  page.getByRole("dialog", { name: "Add a profile" });
const input = (page: Page, label: string) =>
  form(page).getByLabel(label, { exact: true });
const activeMarks = (page: Page) =>
  cards(page).getByText("Active", { exact: true });

async function openForm(page: Page) {
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(form(page)).toBeVisible();
}

async function fill(page: Page, values: Record<string, string> = {}) {
  const given = {
    Name: "Local vLLM",
    "Model id": "served-model",
    "Base URL": "http://10.0.0.5:31000/v1",
    "API key": KEY,
    ...values,
  };
  for (const [label, value] of Object.entries(given)) {
    await input(page, label).fill(value);
  }
}

async function choose(page: Page, provider: string) {
  await form(page).getByRole("combobox", { name: "Provider" }).click();
  await page.getByRole("option", { name: provider, exact: true }).click();
}

const save = (page: Page) =>
  form(page).getByRole("button", { name: "Save" }).click();

test("Add saves a profile, which shows as a new card that is not active", async ({
  page,
}) => {
  const service = await new ProfileService().serve(page);
  await page.goto("/llm");
  await openForm(page);

  await fill(page);
  await form(page)
    .getByRole("switch", { name: /Responses API/ })
    .click();
  await save(page);

  await expect(form(page)).toBeHidden();
  const added = card(page, "Local vLLM");
  await expect(added).toBeVisible();
  await expect(added.getByText("OpenAI-compatible")).toBeVisible();
  await expect(added.getByText("served-model")).toBeVisible();
  await expect(added.getByText("http://10.0.0.5:31000/v1")).toBeVisible();
  await expect(added.getByText("Chat completions")).toBeVisible();
  // Of the key, its last four characters and no more.
  await expect(added.getByText("Ends in 0001")).toBeVisible();
  await expect(page.getByText(KEY)).toHaveCount(0);
  // What was active still is.
  await expect(added.getByText("Active", { exact: true })).toBeHidden();
  await expect(activeMarks(page)).toHaveCount(1);
  await expect(
    card(page, "Environment").getByText("Active", { exact: true }),
  ).toBeVisible();

  // One request saved it, with the values as typed.
  expect(service.changes).toEqual([
    {
      method: "POST",
      path: "/llm/profiles",
      body: {
        name: "Local vLLM",
        provider: "openai",
        model: "served-model",
        base_url: "http://10.0.0.5:31000/v1",
        api_key: KEY,
        responses_api: false,
      },
    },
  ]);
});

test("the choice of API is offered for an OpenAI-compatible endpoint only", async ({
  page,
}) => {
  const service = await new ProfileService().serve(page);
  await page.goto("/llm");
  await openForm(page);
  const choice = form(page).getByRole("switch", { name: /Responses API/ });
  await expect(choice).toBeChecked();

  await choose(page, "Anthropic");
  await expect(choice).toBeHidden();
  await fill(page, {
    Name: "Claude",
    "Model id": "claude-sonnet-5-5",
    "Base URL": "",
  });
  await save(page);

  const added = card(page, "Claude");
  await expect(added.getByText("Anthropic")).toBeVisible();
  await expect(added.getByText("Base URL")).toBeHidden();
  await expect(added.getByText("Responses API")).toBeHidden();
  await expect(added.getByText("Chat completions")).toBeHidden();
  expect(service.changes[0].body).toEqual({
    name: "Claude",
    provider: "anthropic",
    model: "claude-sonnet-5-5",
    base_url: null,
    api_key: KEY,
  });
});

test("a name that is taken is said at the name, and nothing is saved until it changes", async ({
  page,
}) => {
  const service = await new ProfileService(
    environmentProfile(),
    savedProfile("Claude"),
  ).serve(page);
  await page.goto("/llm");
  await expect(cards(page)).toHaveCount(2);
  await openForm(page);

  await fill(page, { Name: "claude" });
  await save(page);

  await expect(input(page, "Name")).toHaveAttribute("aria-invalid", "true");
  await expect(form(page)).toContainText("Another profile has this name.");
  await expect(input(page, "Model id")).not.toHaveAttribute("aria-invalid");
  await expect(form(page)).toBeVisible();
  expect(service.profiles).toHaveLength(2);

  // The rest is still there: only the name is changed and it is sent again.
  await expect(input(page, "API key")).toHaveValue(KEY);
  await input(page, "Name").fill("Local vLLM");
  await expect(input(page, "Name")).not.toHaveAttribute("aria-invalid");
  await save(page);

  await expect(form(page)).toBeHidden();
  await expect(cards(page)).toHaveCount(3);
  await expect(card(page, "Local vLLM")).toBeVisible();
  expect(service.changes).toHaveLength(2);
});

test("a base URL that is not a web address, and missing values, are said at their fields and nothing is sent", async ({
  page,
}) => {
  const service = await new ProfileService().serve(page);
  await page.goto("/llm");
  await openForm(page);

  await save(page);
  for (const label of ["Name", "Model id", "API key"]) {
    await expect(input(page, label)).toHaveAttribute("aria-invalid", "true");
  }
  await expect(input(page, "Base URL")).not.toHaveAttribute("aria-invalid");

  await fill(page, { "Base URL": "10.0.0.5:31000/v1" });
  await save(page);
  await expect(input(page, "Base URL")).toHaveAttribute("aria-invalid", "true");
  await expect(form(page)).toContainText("Enter an http or https address");
  await expect(input(page, "Name")).not.toHaveAttribute("aria-invalid");

  await expect(form(page)).toBeVisible();
  expect(service.changes).toEqual([]);
  await expect(cards(page)).toHaveCount(1);
});

test("a form that was closed opens again empty, the key included", async ({
  page,
}) => {
  const service = await new ProfileService().serve(page);
  await page.goto("/llm");
  await openForm(page);
  await fill(page);

  await form(page).getByRole("button", { name: "Cancel" }).click();
  await expect(form(page)).toBeHidden();
  await openForm(page);

  for (const label of ["Name", "Model id", "Base URL", "API key"]) {
    await expect(input(page, label)).toHaveValue("");
  }
  expect(service.changes).toEqual([]);
  // Nothing of it was kept in the browser either.
  const stored = await page.evaluate(() => ({
    local: localStorage.length,
    session: sessionStorage.length,
  }));
  expect(stored).toEqual({ local: 0, session: 0 });
});

test("Set active asks first, and confirming moves the Active mark to that profile alone", async ({
  page,
}) => {
  const claude = savedProfile("Claude");
  const service = await new ProfileService(
    environmentProfile(),
    claude,
    savedProfile("Spare", { id: "f".repeat(32) }),
  ).serve(page);
  await page.goto("/llm");
  // The active profile offers nothing to activate; the others do.
  await expect(
    card(page, "Environment").getByRole("button", { name: "Set active" }),
  ).toHaveCount(0);

  await card(page, "Claude")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation).toContainText("Claude");
  await expect(confirmation).toContainText(WHERE_CONVERSATIONS_GO);
  // Nothing is sent until it is confirmed.
  expect(service.changes).toEqual([]);

  await confirmation.getByRole("button", { name: "Set active" }).click();

  await expect(confirmation).toBeHidden();
  await expect(
    card(page, "Claude").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(activeMarks(page)).toHaveCount(1);
  await expect(
    card(page, "Claude").getByRole("button", { name: "Set active" }),
  ).toHaveCount(0);
  await expect(
    card(page, "Environment").getByRole("button", { name: "Set active" }),
  ).toBeVisible();
  await expect(alerts(page)).toHaveCount(0);
  expect(service.changes).toEqual([
    {
      method: "POST",
      path: `/llm/profiles/${claude.id}/activate`,
      body: undefined,
    },
  ]);
});

test("cancelling the confirmation changes nothing", async ({ page }) => {
  const service = await new ProfileService(
    environmentProfile(),
    savedProfile("Claude"),
  ).serve(page);
  await page.goto("/llm");

  await card(page, "Claude")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await confirmation.getByRole("button", { name: "Cancel" }).click();

  await expect(confirmation).toBeHidden();
  await expect(
    card(page, "Environment").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(activeMarks(page)).toHaveCount(1);
  expect(service.changes).toEqual([]);
});

test("a profile added through the form can be activated, and the environment's activated again", async ({
  page,
}) => {
  const service = await new ProfileService().serve(page);
  await page.goto("/llm");
  await openForm(page);
  await fill(page);
  await save(page);
  await expect(form(page)).toBeHidden();

  await card(page, "Local vLLM")
    .getByRole("button", { name: "Set active" })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Set active" })
    .click();
  await expect(
    card(page, "Local vLLM").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(activeMarks(page)).toHaveCount(1);

  await card(page, "Environment")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation).toContainText("Environment");
  await confirmation.getByRole("button", { name: "Set active" }).click();

  await expect(
    card(page, "Environment").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(activeMarks(page)).toHaveCount(1);
  expect(service.changes.map(({ path }) => path)).toEqual([
    "/llm/profiles",
    `/llm/profiles/${service.profiles.find((p) => p.name === "Local vLLM")?.id}/activate`,
    "/llm/profiles/environment/activate",
  ]);
});

test("a refused activation is shown in the confirmation, and the mark stays where it was", async ({
  page,
}) => {
  // The page shows the profile as usable; the service has since found that it is not.
  const stale = savedProfile("Claude");
  const service = await new ProfileService(environmentProfile(), stale).serve(
    page,
  );
  await page.goto("/llm");
  await expect(card(page, "Claude")).toBeVisible();
  service.profiles = service.profiles.map((profile) =>
    profile.id === stale.id ? { ...profile, usable: false } : profile,
  );

  await card(page, "Claude")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await confirmation.getByRole("button", { name: "Set active" }).click();

  await expect(confirmation.getByRole("alert")).toContainText(
    "Profile Claude cannot be used.",
  );
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole("button", { name: "Cancel" }).click();
  await expect(
    card(page, "Environment").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(activeMarks(page)).toHaveCount(1);
});
