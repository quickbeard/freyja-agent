import { expect, type Page, test } from "@playwright/test";
import {
  alerts,
  card,
  cards,
  environmentProfile,
  LONG_REASON,
  ProfileService,
  REFUSES_EARLIER_TURNS,
  savedProfile,
  TESTED_AT,
  testOf,
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

// 004, User Story 3: test a profile before relying on it.

const NOT_TESTED = "Not tested since its last change.";

test("Test on a card shows that the profile works, and the result is still there after a reload", async ({
  page,
}) => {
  const claude = savedProfile("Claude");
  const service = await new ProfileService(environmentProfile(), claude).serve(
    page,
  );
  await page.goto("/llm");
  await expect(card(page, "Claude").getByText(NOT_TESTED)).toBeVisible();

  await card(page, "Claude").getByRole("button", { name: "Test" }).click();

  await expect(
    card(page, "Claude").getByText(/Last test passed/),
  ).toBeVisible();
  // The time shown is the one the service gave.
  await expect(card(page, "Claude").locator("time")).toHaveAttribute(
    "datetime",
    TESTED_AT,
  );
  await expect(card(page, "Claude").getByText(NOT_TESTED)).toBeHidden();
  // Only that profile was tested, and nothing else changed.
  await expect(
    card(page, "Environment").getByText(
      "Not tested since the service started.",
    ),
  ).toBeVisible();
  expect(service.changes).toEqual([
    {
      method: "POST",
      path: `/llm/profiles/${claude.id}/test`,
      body: undefined,
    },
  ]);

  await page.reload();
  await expect(
    card(page, "Claude").getByText(/Last test passed/),
  ).toBeVisible();
});

test("a failed test names the step that failed and shows the endpoint's reason", async ({
  page,
}) => {
  await new ProfileService(
    environmentProfile(),
    savedProfile("Refuses", { model: "refuse-conversation" }),
    savedProfile("Rejected", { id: "e".repeat(32), model: "reject-key" }),
  ).serve(page);
  await page.goto("/llm");

  await card(page, "Refuses").getByRole("button", { name: "Test" }).click();
  await expect(
    card(page, "Refuses").getByText(/Last test failed at the conversation/),
  ).toBeVisible();
  await expect(
    card(page, "Refuses").getByText(REFUSES_EARLIER_TURNS),
  ).toBeVisible();

  await card(page, "Rejected").getByRole("button", { name: "Test" }).click();
  await expect(
    card(page, "Rejected").getByText(/Last test failed at the first message/),
  ).toBeVisible();
  await expect(
    card(page, "Rejected").getByText("Incorrect API key provided."),
  ).toBeVisible();
});

test("the environment's card can be tested the same way", async ({ page }) => {
  await new ProfileService().serve(page);
  await page.goto("/llm");
  await card(page, "Environment").getByRole("button", { name: "Test" }).click();
  await expect(
    card(page, "Environment").getByText(/Last test passed/),
  ).toBeVisible();
});

test("a card whose profile changed since its test says it has not been tested since", async ({
  page,
}) => {
  const claude = savedProfile("Claude", { last_test: testOf("ok") });
  const service = await new ProfileService(environmentProfile(), claude).serve(
    page,
  );
  await page.goto("/llm");
  await expect(
    card(page, "Claude").getByText(/Last test passed/),
  ).toBeVisible();

  // Its model is changed, as by another operator, and the list is read again.
  service.change(claude.id, { model: "claude-opus-5-5" });
  await page.getByRole("button", { name: "Refresh" }).click();

  await expect(card(page, "Claude").getByText("claude-opus-5-5")).toBeVisible();
  await expect(card(page, "Claude").getByText(NOT_TESTED)).toBeVisible();
  await expect(card(page, "Claude").getByText(/Last test passed/)).toBeHidden();
});

test("the confirmation says when a profile was never tested, tests it first when asked, and then activates", async ({
  page,
}) => {
  const claude = savedProfile("Claude");
  const service = await new ProfileService(environmentProfile(), claude).serve(
    page,
  );
  await page.goto("/llm");

  await card(page, "Claude")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  const warning = confirmation.getByRole("note");
  await expect(warning).toContainText(
    "Claude has not been tested since its last change.",
  );
  await expect(warning).toContainText("You can still set it active.");

  await warning.getByRole("button", { name: "Test first" }).click();
  await expect(confirmation.getByRole("status")).toContainText(
    "Last test passed",
  );
  await expect(warning).toBeHidden();
  // Testing activated nothing. (The page behind the confirmation cannot be looked into by
  // role while it is open, so the service is asked.)
  expect(service.list().active_id).toBe("environment");

  await confirmation.getByRole("button", { name: "Set active" }).click();
  await expect(confirmation).toBeHidden();
  await expect(
    card(page, "Claude").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(
    card(page, "Claude").getByText(/Last test passed/),
  ).toBeVisible();
  expect(service.changes.map(({ path }) => path)).toEqual([
    `/llm/profiles/${claude.id}/test`,
    `/llm/profiles/${claude.id}/activate`,
  ]);
});

test("the confirmation says when the last test failed, and the profile can still be activated", async ({
  page,
}) => {
  const refuses = savedProfile("Refuses", {
    model: "refuse-conversation",
    last_test: testOf("refuse-conversation"),
  });
  const service = await new ProfileService(environmentProfile(), refuses).serve(
    page,
  );
  await page.goto("/llm");

  await card(page, "Refuses")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation.getByRole("note")).toContainText(
    "Refuses's last test failed at the conversation.",
  );
  // Testing again says the same, with the endpoint's reason.
  await confirmation.getByRole("button", { name: "Test first" }).click();
  await expect(confirmation.getByRole("status")).toContainText(
    REFUSES_EARLIER_TURNS,
  );
  await expect(confirmation.getByRole("note")).toBeVisible();

  await confirmation.getByRole("button", { name: "Set active" }).click();
  await expect(confirmation).toBeHidden();
  await expect(
    card(page, "Refuses").getByText("Active", { exact: true }),
  ).toBeVisible();
  expect(service.changes.at(-1)?.path).toBe(
    `/llm/profiles/${refuses.id}/activate`,
  );
});

test("a profile whose last test passed is activated with no warning", async ({
  page,
}) => {
  await new ProfileService(
    environmentProfile(),
    savedProfile("Claude", { last_test: testOf("ok") }),
  ).serve(page);
  await page.goto("/llm");
  await card(page, "Claude")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation).toContainText("Claude");
  await expect(confirmation.getByRole("note")).toHaveCount(0);
  await expect(
    confirmation.getByRole("button", { name: "Test first" }),
  ).toHaveCount(0);
});

test("the form tests its values before they are saved, and a test saves nothing", async ({
  page,
}) => {
  const service = await new ProfileService().serve(page);
  await page.goto("/llm");
  await openForm(page);
  await fill(page, { Name: "", "Model id": "refuse-conversation" });

  await form(page).getByRole("button", { name: "Test" }).click();
  const result = form(page).getByRole("status");
  await expect(result).toContainText("The test failed at the conversation");
  await expect(result).toContainText(REFUSES_EARLIER_TURNS);
  // The values went to be tested, with no name; nothing was saved and no card appeared.
  expect(service.changes).toEqual([
    {
      method: "POST",
      path: "/llm/test",
      body: {
        provider: "openai",
        model: "refuse-conversation",
        base_url: "http://10.0.0.5:31000/v1",
        api_key: KEY,
        responses_api: true,
      },
    },
  ]);
  expect(service.profiles).toHaveLength(1);
  await expect(form(page)).toBeVisible();

  // A changed value takes the result back; the new values pass and are then saved.
  await input(page, "Model id").fill("served-model");
  await expect(result).toBeHidden();
  await form(page).getByRole("button", { name: "Test" }).click();
  await expect(result).toContainText("The test passed");
  await input(page, "Name").fill("Local vLLM");
  await save(page);

  await expect(form(page)).toBeHidden();
  await expect(card(page, "Local vLLM")).toBeVisible();
  // A test of unsaved values is not kept with the profile they became.
  await expect(card(page, "Local vLLM").getByText(NOT_TESTED)).toBeVisible();
  await expect(page.getByText(KEY)).toHaveCount(0);
});

// 004, User Story 4: change and remove profiles.

const NEW_KEY = "sk-another-key-for-tests-0002";
const editForm = (page: Page, name: string) =>
  page.getByRole("dialog", { name: `Edit ${name}` });

async function openEdit(page: Page, name: string) {
  await card(page, name).getByRole("button", { name: "Edit" }).click();
  await expect(editForm(page, name)).toBeVisible();
  return editForm(page, name);
}

test("Edit opens with the profile's values and no key, and a save without one keeps the stored key", async ({
  page,
}) => {
  const vllm = savedProfile("Local vLLM", {
    provider: "openai",
    model: "served-model",
    base_url: "http://10.0.0.5:31000/v1",
    responses_api: false,
    last_test: testOf("ok"),
  });
  const service = await new ProfileService(environmentProfile(), vllm).serve(
    page,
  );
  await page.goto("/llm");
  const edit = await openEdit(page, "Local vLLM");

  await expect(edit.getByLabel("Name", { exact: true })).toHaveValue(
    "Local vLLM",
  );
  await expect(edit.getByLabel("Model id", { exact: true })).toHaveValue(
    "served-model",
  );
  await expect(edit.getByLabel("Base URL", { exact: true })).toHaveValue(
    "http://10.0.0.5:31000/v1",
  );
  await expect(edit.getByRole("combobox", { name: "Provider" })).toContainText(
    "OpenAI-compatible",
  );
  await expect(
    edit.getByRole("switch", { name: /Responses API/ }),
  ).not.toBeChecked();
  await expect(edit.getByLabel("API key", { exact: true })).toHaveValue("");

  await edit.getByLabel("Name", { exact: true }).fill("Renamed");
  await edit.getByRole("button", { name: "Save" }).click();

  await expect(edit).toBeHidden();
  const renamed = card(page, "Renamed");
  await expect(renamed).toBeVisible();
  await expect(card(page, "Local vLLM")).toHaveCount(0);
  // The same key, and a change of name keeps the test result.
  await expect(renamed.getByText("Ends in 0001")).toBeVisible();
  await expect(renamed.getByText(/Last test passed/)).toBeVisible();
  // No key went with the change.
  expect(service.changes).toEqual([
    {
      method: "PATCH",
      path: `/llm/profiles/${vllm.id}`,
      body: {
        name: "Renamed",
        provider: "openai",
        model: "served-model",
        base_url: "http://10.0.0.5:31000/v1",
        responses_api: false,
      },
    },
  ]);
});

test("a key typed in the Edit form replaces the stored one, and the card says it is untested", async ({
  page,
}) => {
  const claude = savedProfile("Claude", { last_test: testOf("ok") });
  const service = await new ProfileService(environmentProfile(), claude).serve(
    page,
  );
  await page.goto("/llm");
  const edit = await openEdit(page, "Claude");

  await edit.getByLabel("API key", { exact: true }).fill(NEW_KEY);
  await edit.getByRole("button", { name: "Save" }).click();

  await expect(edit).toBeHidden();
  await expect(card(page, "Claude").getByText("Ends in 0002")).toBeVisible();
  await expect(card(page, "Claude").getByText(NOT_TESTED)).toBeVisible();
  await expect(page.getByText(NEW_KEY)).toHaveCount(0);
  expect((service.changes[0].body as { api_key: string }).api_key).toBe(
    NEW_KEY,
  );
});

test("a taken name is said at the name in the Edit form, and nothing changes", async ({
  page,
}) => {
  const service = await new ProfileService(
    environmentProfile(),
    savedProfile("Claude"),
    savedProfile("Spare", { id: "f".repeat(32) }),
  ).serve(page);
  await page.goto("/llm");
  const edit = await openEdit(page, "Spare");

  await edit.getByLabel("Name", { exact: true }).fill("claude");
  await edit.getByRole("button", { name: "Save" }).click();

  await expect(edit.getByLabel("Name", { exact: true })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  await expect(edit).toContainText("Another profile has this name.");
  expect(service.profiles.map((profile) => profile.name)).toEqual([
    "Environment",
    "Claude",
    "Spare",
  ]);
});

test("changing the active profile asks first, and nothing is sent until it is confirmed", async ({
  page,
}) => {
  const claude = savedProfile("Claude", { active: true });
  const service = await new ProfileService(
    environmentProfile({ active: false }),
    claude,
  ).serve(page);
  await page.goto("/llm");
  const edit = await openEdit(page, "Claude");

  await edit.getByLabel("Model id", { exact: true }).fill("claude-opus-5-5");
  await edit.getByRole("button", { name: "Save" }).click();

  const note = edit.getByRole("note");
  await expect(note).toContainText("Claude is the active profile.");
  await expect(note).toContainText("This change applies from the next run");
  expect(service.changes).toEqual([]);

  // Going back sends nothing; confirming sends the change.
  await edit.getByRole("button", { name: "Back" }).click();
  await expect(note).toBeHidden();
  expect(service.changes).toEqual([]);
  await edit.getByRole("button", { name: "Save" }).click();
  await edit.getByRole("button", { name: "Confirm" }).click();

  await expect(edit).toBeHidden();
  await expect(card(page, "Claude").getByText("claude-opus-5-5")).toBeVisible();
  await expect(
    card(page, "Claude").getByText("Active", { exact: true }),
  ).toBeVisible();
  expect(service.changes.map(({ method }) => method)).toEqual(["PATCH"]);
});

test("the Edit form tests its values with the stored key when the key is left empty", async ({
  page,
}) => {
  const claude = savedProfile("Claude");
  const service = await new ProfileService(environmentProfile(), claude).serve(
    page,
  );
  await page.goto("/llm");
  const edit = await openEdit(page, "Claude");

  await edit
    .getByLabel("Model id", { exact: true })
    .fill("refuse-conversation");
  await edit.getByRole("button", { name: "Test" }).click();

  await expect(edit.getByRole("status")).toContainText(
    "The test failed at the conversation",
  );
  // The profile's id went in place of a key, and nothing was saved.
  expect(service.changes).toEqual([
    {
      method: "POST",
      path: "/llm/test",
      body: {
        provider: "anthropic",
        model: "refuse-conversation",
        base_url: null,
        profile_id: claude.id,
      },
    },
  ]);
  await edit.getByRole("button", { name: "Cancel" }).click();
  await expect(
    card(page, "Claude").getByText("claude-sonnet-5-5"),
  ).toBeVisible();
});

test("Delete asks first, and confirming removes the card", async ({ page }) => {
  const claude = savedProfile("Claude");
  const service = await new ProfileService(
    environmentProfile(),
    claude,
    savedProfile("Spare", { id: "f".repeat(32) }),
  ).serve(page);
  await page.goto("/llm");
  await expect(cards(page)).toHaveCount(3);

  // Cancelling leaves it.
  await card(page, "Claude").getByRole("button", { name: "Delete" }).click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation).toContainText("Delete Claude?");
  await expect(confirmation).toContainText("This cannot be undone.");
  await confirmation.getByRole("button", { name: "Cancel" }).click();
  await expect(confirmation).toBeHidden();
  await expect(cards(page)).toHaveCount(3);
  expect(service.changes).toEqual([]);

  await card(page, "Claude").getByRole("button", { name: "Delete" }).click();
  await confirmation.getByRole("button", { name: "Delete" }).click();

  await expect(confirmation).toBeHidden();
  await expect(card(page, "Claude")).toHaveCount(0);
  await expect(cards(page)).toHaveCount(2);
  expect(service.changes).toEqual([
    { method: "DELETE", path: `/llm/profiles/${claude.id}`, body: undefined },
  ]);
  // Still gone when the page is loaded again.
  await page.reload();
  await expect(card(page, "Spare")).toBeVisible();
  await expect(card(page, "Claude")).toHaveCount(0);
});

test("the active profile is not deleted: the service's refusal is shown and the card stays", async ({
  page,
}) => {
  await new ProfileService(
    environmentProfile({ active: false }),
    savedProfile("Claude", { active: true }),
  ).serve(page);
  await page.goto("/llm");

  await card(page, "Claude").getByRole("button", { name: "Delete" }).click();
  const confirmation = page.getByRole("alertdialog");
  await confirmation.getByRole("button", { name: "Delete" }).click();

  await expect(confirmation.getByRole("alert")).toHaveText(
    "Profile Claude is the active one and cannot be deleted. Activate another profile first.",
  );
  await confirmation.getByRole("button", { name: "Cancel" }).click();
  await expect(
    card(page, "Claude").getByText("Active", { exact: true }),
  ).toBeVisible();
  await expect(cards(page)).toHaveCount(2);
});

test("the environment's card offers neither Edit nor Delete, and a saved card offers both", async ({
  page,
}) => {
  await new ProfileService(environmentProfile(), savedProfile("Claude")).serve(
    page,
  );
  await page.goto("/llm");

  await expect(card(page, "Claude").getByRole("button")).toHaveText([
    "Test",
    "Set active",
    "Edit",
    "Delete",
  ]);
  await expect(card(page, "Environment").getByRole("button")).toHaveText([
    "Test",
  ]);
});

// 004, User Story 5: API keys stay secret.

/** Everything the page holds that could be read back: its markup and the browser's storage. */
async function whatThePageHolds(page: Page) {
  const stored = await page.evaluate(() => ({
    local: Object.entries(localStorage),
    session: Object.entries(sessionStorage),
    cookies: document.cookie,
  }));
  return { markup: await page.content(), stored };
}

test("a key is nowhere on the page or in the browser's storage after adding, testing, and editing", async ({
  page,
}) => {
  const service = await new ProfileService().serve(page);
  await page.goto("/llm");

  // Added through the form, after testing the values there.
  await openForm(page);
  await fill(page);
  await form(page).getByRole("button", { name: "Test" }).click();
  await expect(form(page).getByRole("status")).toContainText("The test passed");
  await save(page);
  await expect(
    card(page, "Local vLLM").getByText("Ends in 0001"),
  ).toBeVisible();

  // Tested from its card, then given another key in the Edit form.
  await card(page, "Local vLLM").getByRole("button", { name: "Test" }).click();
  await expect(
    card(page, "Local vLLM").getByText(/Last test passed/),
  ).toBeVisible();
  const edit = await openEdit(page, "Local vLLM");
  await expect(edit.getByLabel("API key", { exact: true })).toHaveValue("");
  await edit.getByLabel("API key", { exact: true }).fill(NEW_KEY);
  await edit.getByRole("button", { name: "Save" }).click();
  await expect(
    card(page, "Local vLLM").getByText("Ends in 0002"),
  ).toBeVisible();

  // Both keys reached the service, and neither is anywhere a reader of the page could look.
  const sentKeys = service.changes
    .map(({ body }) => (body as { api_key?: string } | undefined)?.api_key)
    .filter(Boolean);
  expect(sentKeys).toEqual([KEY, KEY, NEW_KEY]);
  const held = await whatThePageHolds(page);
  for (const key of [KEY, NEW_KEY]) {
    expect(held.markup).not.toContain(key);
    expect(JSON.stringify(held.stored)).not.toContain(key);
    await expect(page.getByText(key)).toHaveCount(0);
  }
  expect(held.stored).toEqual({ local: [], session: [], cookies: "" });
});

test("a save that is refused leaves the key out of what the page shows", async ({
  page,
}) => {
  await new ProfileService(environmentProfile(), savedProfile("Claude")).serve(
    page,
  );
  await page.goto("/llm");
  await openForm(page);
  await fill(page, { Name: "Claude" });
  await save(page);

  await expect(form(page)).toContainText("Another profile has this name.");
  // The key is still in its field, unseen. The field is the one place in the page that
  // holds it: it is in no text, and nowhere else in the markup.
  await expect(input(page, "API key")).toHaveValue(KEY);
  await expect(input(page, "API key")).toHaveAttribute("type", "password");
  await expect(page.getByText(KEY)).toHaveCount(0);
  const open = await whatThePageHolds(page);
  expect(open.markup.split(KEY)).toHaveLength(2);
  expect(open.markup).toMatch(new RegExp(`type="password"[^>]*value="${KEY}"`));
  expect(open.stored).toEqual({ local: [], session: [], cookies: "" });

  // Once the form is closed the page holds it nowhere.
  await form(page).getByRole("button", { name: "Cancel" }).click();
  await expect(form(page)).toBeHidden();
  const closed = await whatThePageHolds(page);
  expect(closed.markup).not.toContain(KEY);
  expect(closed.stored).toEqual({ local: [], session: [], cookies: "" });
});

test("a card whose key cannot be read says why, and its form asks for the key again", async ({
  page,
}) => {
  const unreadable = savedProfile("Claude", {
    usable: false,
    unusable_reason: "key_unreadable",
  });
  const service = await new ProfileService(
    environmentProfile(),
    unreadable,
  ).serve(page);
  await page.goto("/llm");

  const claude = card(page, "Claude");
  await expect(claude.getByText("Not usable")).toBeVisible();
  await expect(claude.getByText(/stored key cannot be read/)).toBeVisible();
  // As it is it can be neither tested nor activated.
  await expect(claude.getByRole("button")).toHaveText(["Edit", "Delete"]);

  const edit = await openEdit(page, "Claude");
  await expect(edit.getByRole("note")).toContainText("Enter the key again");
  // There is no stored key to keep, so saving without one is not sent.
  await edit.getByRole("button", { name: "Save" }).click();
  await expect(edit.getByLabel("API key", { exact: true })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(service.changes).toEqual([]);

  await edit.getByLabel("API key", { exact: true }).fill(NEW_KEY);
  await edit.getByRole("button", { name: "Save" }).click();

  await expect(edit).toBeHidden();
  await expect(claude.getByText("Not usable")).toBeHidden();
  await expect(claude.getByText("Ends in 0002")).toBeVisible();
  await expect(claude.getByRole("button")).toHaveText([
    "Test",
    "Set active",
    "Edit",
    "Delete",
  ]);
  await expect(page.getByText(NEW_KEY)).toHaveCount(0);
});

// 004, convergence: what a second read of the spec against the code found.

test("pressing Save twice on the active profile does not change it: confirming takes a press of its own", async ({
  page,
}) => {
  const service = await new ProfileService(
    environmentProfile({ active: false }),
    savedProfile("Claude", { active: true }),
  ).serve(page);
  await page.goto("/llm");
  const edit = await openEdit(page, "Claude");
  const model = edit.getByLabel("Model id", { exact: true });
  await model.fill("claude-opus-5-5");

  // A double press: its second half lands where Save was, on the button that confirms.
  await edit.getByRole("button", { name: "Save" }).dblclick();
  const note = edit.getByRole("note");
  await expect(note).toContainText("Claude is the active profile.");
  expect(service.changes).toEqual([]);

  // Enter twice in a field does no more.
  await edit.getByRole("button", { name: "Back" }).click();
  await model.press("Enter");
  await expect(note).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(note).toBeVisible();
  expect(service.changes).toEqual([]);

  await edit.getByRole("button", { name: "Confirm" }).click();
  await expect(edit).toBeHidden();
  expect(service.changes.map(({ method }) => method)).toEqual(["PATCH"]);
});

test("a profile another operator deleted goes from the list when the service says it is gone", async ({
  page,
}) => {
  const claude = savedProfile("Claude");
  const service = await new ProfileService(
    environmentProfile(),
    claude,
    savedProfile("Spare", { id: "f".repeat(32) }),
  ).serve(page);
  await page.goto("/llm");
  await expect(cards(page)).toHaveCount(3);

  // Deleted elsewhere: this page still shows its card.
  service.deleteElsewhere(claude.id);
  await card(page, "Claude")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await confirmation.getByRole("button", { name: "Set active" }).click();

  // The refusal is the service's, and the list behind it is the current one, with no
  // Refresh pressed.
  await expect(confirmation.getByRole("alert")).toHaveText(
    "There is no profile with this id.",
  );
  await expect(cards(page)).toHaveCount(2);
  // Found by its markup: the dialog in front hides the cards from a lookup by role.
  await expect(cards(page).locator("h3", { hasText: /^Claude$/ })).toHaveCount(
    0,
  );
  await confirmation.getByRole("button", { name: "Cancel" }).click();
  await expect(card(page, "Spare")).toBeVisible();
  await expect(
    card(page, "Environment").getByText("Active", { exact: true }),
  ).toBeVisible();
});

test("in a short, narrow window the confirmation's buttons can be reached under a long reason", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 560 });
  const service = await new ProfileService(
    environmentProfile(),
    savedProfile("A profile with a rather long name", { model: "long-reason" }),
  ).serve(page);
  await page.goto("/llm");

  await card(page, "A profile with a rather long name")
    .getByRole("button", { name: "Set active" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await confirmation.getByRole("button", { name: "Test first" }).click();
  await expect(confirmation.getByText(LONG_REASON)).toBeVisible();

  // The dialog stays inside the window and scrolls in itself.
  const box = await confirmation.boundingBox();
  expect(box).not.toBeNull();
  expect(box?.y).toBeGreaterThanOrEqual(0);
  expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(560);
  for (const name of ["Set active", "Cancel"]) {
    const button = confirmation.getByRole("button", { name, exact: true });
    await button.scrollIntoViewIfNeeded();
    await expect(button).toBeInViewport({ ratio: 1 });
  }
  const title = confirmation.getByRole("heading");
  await title.scrollIntoViewIfNeeded();
  await expect(title).toBeInViewport({ ratio: 1 });

  await confirmation.getByRole("button", { name: "Cancel" }).click();
  await expect(confirmation).toBeHidden();
  expect(service.changes.map(({ path }) => path)).toEqual([
    expect.stringMatching(/\/test$/),
  ]);
});
