import { expect, type Page, test } from "@playwright/test";
import {
  AgentService,
  card,
  environmentAgent,
  savedAgent,
  section,
} from "./api";

// Feature 010: the agents' approval mode under Tasks in the sidebar, and each agent's own
// mode, Manual or Auto, on its card in the Agents section.

const STUB_ID = "0123456789abcdef0123456789abcdef";

const dropdown = (page: Page) =>
  page.getByRole("combobox", { name: "Agent approvals" });
const asked = (page: Page) => page.getByRole("alertdialog");

test("the dropdown under Tasks sets Manual at once, and Auto only once confirmed", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello", { approval: "none" }),
    savedAgent("stub", { approval: "none" }),
  ).serve(page);
  await page.goto("/tasks");

  // Under the Tasks entry of the sidebar, on every page.
  await expect(section(page, "Tasks")).toBeVisible();
  await expect(dropdown(page)).toContainText("Auto");
  await expect(
    page.getByText("Agents set to Auto run without approval; the others ask."),
  ).toBeVisible();

  await dropdown(page).click();
  await page.getByRole("option", { name: "Manual" }).click();
  await expect(dropdown(page)).toContainText("Manual");
  await expect(asked(page)).toHaveCount(0);
  expect(service.changes).toEqual([
    { method: "PUT", path: "/agents/mode", body: { mode: "manual" } },
  ]);

  // Auto lets every agent set to Auto run without approval: it is asked first.
  await dropdown(page).click();
  await page.getByRole("option", { name: "Auto" }).click();
  await expect(asked(page)).toContainText("2 agents are set to Auto now");
  await asked(page).getByRole("button", { name: "Cancel" }).click();
  await expect(asked(page)).toHaveCount(0);
  await expect(dropdown(page)).toContainText("Manual");
  expect(service.changes).toHaveLength(1);

  await dropdown(page).click();
  await page.getByRole("option", { name: "Auto" }).click();
  await asked(page).getByRole("button", { name: "Set to Auto" }).click();
  await expect(asked(page)).toHaveCount(0);
  await expect(dropdown(page)).toContainText("Auto");
  expect(service.changes).toEqual([
    { method: "PUT", path: "/agents/mode", body: { mode: "manual" } },
    { method: "PUT", path: "/agents/mode", body: { mode: "auto" } },
  ]);
});

test("a saved agent's card switches its own mode behind a confirmation, and says when Manual holds it", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello", { approval: "none" }),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");

  const stub = card(page, "stub");
  const toggle = stub.getByRole("switch", { name: "Auto mode for stub" });
  await expect(toggle).not.toBeChecked();
  await expect(stub.getByText("Manual", { exact: true })).toBeVisible();
  // An environment agent's mode is shown, and is not changed here.
  await expect(card(page, "hello").getByRole("switch")).toHaveCount(0);
  await expect(
    card(page, "hello").getByText("Auto", { exact: true }),
  ).toBeVisible();

  await toggle.click();
  await expect(asked(page)).toContainText("Set stub to Auto?");
  expect(service.changes).toEqual([]);
  await asked(page).getByRole("button", { name: "Set to Auto" }).click();
  await expect(asked(page)).toHaveCount(0);
  await expect(toggle).toBeChecked();
  await expect(stub.getByText("Auto", { exact: true })).toBeVisible();
  expect(service.changes).toEqual([
    {
      method: "PATCH",
      path: `/agents/${STUB_ID}`,
      body: { version: 1, approval: "none" },
    },
  ]);

  // With the agents' approvals on Manual, an Auto agent is held to Manual, and its card
  // says so.
  await dropdown(page).click();
  await page.getByRole("option", { name: "Manual" }).click();
  await expect(stub.getByText(/^Held to Manual/)).toBeVisible();
  await expect(card(page, "hello").getByText(/^Held to Manual/)).toBeVisible();
});
