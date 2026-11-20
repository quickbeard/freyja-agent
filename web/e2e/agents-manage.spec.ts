import { expect, type Page, test } from "@playwright/test";
import {
  AGENT_ADDRESS_TAKEN,
  AgentService,
  ALREADY_CHECKED,
  agentsBadge,
  agentsWarning,
  alerts,
  card,
  cards,
  ENVIRONMENT_NAME_TAKEN,
  environmentAgent,
  NO_SUCH_AGENT,
  NOT_THIS_MACHINE,
  NOTHING_ANSWERED,
  SAVED_NAME_TAKEN,
  savedAgent,
} from "./api";

// 005, User Story 2: add an agent from the Agents section.

// An address chosen to be recognizable wherever it turns up: its path is no part of
// anything else the page asks for.
const TYPED = "HTTP://LocalHost:9998/agents/Recognizable-Stub/";
const KEPT = "http://localhost:9998/agents/Recognizable-Stub";
const UNREACHABLE = "The Freyja service cannot be reached.";

const form = (page: Page) => page.getByRole("dialog", { name: "Add an agent" });
const input = (page: Page, label: string) =>
  form(page).getByLabel(label, { exact: true });
const save = (page: Page) =>
  form(page).getByRole("button", { name: "Save" }).click();

async function openForm(page: Page) {
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(form(page)).toBeVisible();
}

async function fill(page: Page, name: string, address: string) {
  await input(page, "Name").fill(name);
  await input(page, "Address").fill(address);
}

/** That the form says `sentence` at this field, and marks the field as refused. */
async function expectRefusedAt(page: Page, label: string, sentence: string) {
  const field = input(page, label);
  await expect(field).toHaveAttribute("aria-invalid", "true");
  // The field is described by its hint and then by what was refused: the last of the two
  // is the service's sentence, whole and with nothing added.
  const described = (await field.getAttribute("aria-describedby")) ?? "";
  const refusal = described.split(" ").at(-1) ?? "";
  expect(refusal).not.toBe("");
  await expect(page.locator(`[id="${refusal}"]`)).toHaveText(sentence);
}

test("Add saves an agent, which shows as a new card with its address as the service keeps it, requiring approval", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello", { approval: "none" }),
  ).serve(page);
  await page.goto("/agents");
  await expect(card(page, "hello")).toBeVisible();
  await openForm(page);

  // The form says what adding means for approval, and offers no choice about it.
  await expect(form(page)).toContainText(
    "Messages to this agent will require approval",
  );
  await expect(form(page).getByRole("textbox")).toHaveCount(2);
  await expect(
    form(page).getByRole("switch").or(form(page).getByRole("combobox")),
  ).toHaveCount(0);

  await fill(page, "stub", TYPED);
  await save(page);

  // No confirmation is asked: the card is there once the service has answered.
  await expect(form(page)).toBeHidden();
  await expect(page.getByRole("heading", { level: 3 })).toHaveText([
    "hello",
    "stub",
  ]);
  const added = card(page, "stub");
  await expect(added.getByText("Address")).toBeVisible();
  await expect(added.getByText(KEPT, { exact: true })).toBeVisible();
  await expect(added.getByText("Requires approval")).toBeVisible();
  await expect(added.getByText("From the environment")).toHaveCount(0);
  await expect(added.getByText(/^Reachable/)).toBeVisible();
  // The address is shown as the service keeps it, and nowhere as it was typed.
  await expect(page.getByText(TYPED)).toHaveCount(0);
  await expect(alerts(page)).toHaveCount(0);

  // One request saved it: the two values as typed, and nothing about approval.
  expect(service.changes).toEqual([
    { method: "POST", path: "/agents", body: { name: "stub", url: TYPED } },
  ]);
  // And the list was read again after it.
  expect(service.asked.map(({ method }) => method)).toEqual([
    "GET",
    "POST",
    "GET",
  ]);
});

test("the first agent can be added where none is configured", async ({
  page,
}) => {
  await new AgentService().serve(page);
  await page.goto("/agents");
  await expect(page.getByText("No agent is configured.")).toBeVisible();

  await openForm(page);
  await fill(page, "stub", "http://127.0.0.1:9998");
  await save(page);

  await expect(card(page, "stub")).toBeVisible();
  await expect(page.getByText("No agent is configured.")).toHaveCount(0);
});

test("the form opens empty the second time, after an add and after it was closed", async ({
  page,
}) => {
  const service = await new AgentService().serve(page);
  await page.goto("/agents");
  await openForm(page);
  await fill(page, "stub", TYPED);
  await save(page);
  await expect(card(page, "stub")).toBeVisible();

  await openForm(page);
  await expect(input(page, "Name")).toHaveValue("");
  await expect(input(page, "Address")).toHaveValue("");

  // Typed and then closed without saving: nothing is sent, and nothing is kept.
  await fill(page, "report", "http://localhost:9997");
  await form(page).getByRole("button", { name: "Cancel" }).click();
  await expect(form(page)).toBeHidden();
  await openForm(page);
  await expect(input(page, "Name")).toHaveValue("");
  await expect(input(page, "Address")).toHaveValue("");
  expect(service.changes).toHaveLength(1);
  await expect(cards(page)).toHaveCount(1);
  // Nothing of it was kept in the browser either.
  const stored = await page.evaluate(() => ({
    local: localStorage.length,
    session: sessionStorage.length,
    cookies: document.cookie,
  }));
  expect(stored).toEqual({ local: 0, session: 0, cookies: "" });
});

test("an address where nothing answers is refused with the service's sentence, and what was typed stays", async ({
  page,
}) => {
  const service = await new AgentService(environmentAgent("hello")).serve(page);
  service.answersAt(9998, false);
  await page.goto("/agents");
  await openForm(page);
  await fill(page, "stub", TYPED);
  await save(page);

  await expect(form(page).getByRole("alert")).toHaveText(NOTHING_ANSWERED);
  await expect(input(page, "Name")).toHaveValue("stub");
  await expect(input(page, "Address")).toHaveValue(TYPED);
  // The service answered: nothing was saved, and the form does not say otherwise.
  await expect(form(page)).not.toContainText("not known");
  expect(service.agents.map(({ name }) => name)).toEqual(["hello"]);

  // Once the agent answers, the same values are saved from the same form.
  service.answersAt(9998, true);
  await save(page);
  await expect(form(page)).toBeHidden();
  await expect(card(page, "stub").getByText(KEPT)).toBeVisible();
  await expect(cards(page)).toHaveCount(2);
  expect(service.changes).toHaveLength(2);
});

test("a name that is taken is refused at the name, by an agent of either kind", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await openForm(page);

  // The service says which kind of agent has the name.
  for (const [taken, sentence] of [
    ["hello", ENVIRONMENT_NAME_TAKEN],
    ["stub", SAVED_NAME_TAKEN],
  ]) {
    await fill(page, taken, "http://localhost:9997");
    await save(page);
    await expectRefusedAt(page, "Name", sentence);
    await expect(input(page, "Address")).not.toHaveAttribute("aria-invalid");
    await expect(form(page).getByRole("alert")).toHaveCount(0);
    await expect(input(page, "Address")).toHaveValue("http://localhost:9997");
  }
  await expect(cards(page)).toHaveCount(2);

  // What was said goes once the name is changed, and a free name is saved.
  await input(page, "Name").fill("report");
  await expect(input(page, "Name")).not.toHaveAttribute("aria-invalid");
  await expect(form(page)).not.toContainText(SAVED_NAME_TAKEN);
  await save(page);
  await expect(card(page, "report")).toBeVisible();
  expect(service.changes).toHaveLength(3);
});

test("an address another agent has, and one on another machine, are refused at the address", async ({
  page,
}) => {
  const service = await new AgentService(
    savedAgent("stub", { url: "http://127.0.0.1:9998" }),
  ).serve(page);
  await page.goto("/agents");
  await openForm(page);

  // The same port, with this machine written another way.
  await fill(page, "report", "http://localhost:9998/");
  await save(page);
  await expectRefusedAt(page, "Address", AGENT_ADDRESS_TAKEN);
  await expect(input(page, "Name")).not.toHaveAttribute("aria-invalid");

  await input(page, "Address").fill("http://agents.example:9998");
  await save(page);
  await expectRefusedAt(page, "Address", NOT_THIS_MACHINE);
  await expect(input(page, "Address")).toHaveValue(
    "http://agents.example:9998",
  );
  await expect(form(page).getByRole("alert")).toHaveCount(0);
  expect(service.agents).toHaveLength(1);
});

test("missing values are said at their fields and nothing is sent", async ({
  page,
}) => {
  const service = await new AgentService().serve(page);
  await page.goto("/agents");
  await openForm(page);
  await save(page);

  await expectRefusedAt(page, "Name", "Enter a name.");
  await expectRefusedAt(page, "Address", "Enter the address.");
  expect(service.changes).toEqual([]);
});

test("a request that gets no answer says the outcome is not known, and the list is read again", async ({
  page,
}) => {
  const service = await new AgentService(environmentAgent("hello")).serve(page);
  await page.goto("/agents");
  await openForm(page);
  await fill(page, "stub", TYPED);
  const readBefore = service.reads;

  // The service saves the agent, and its answer never arrives.
  service.loseNextAnswer();
  await save(page);

  const said = form(page).getByRole("alert");
  await expect(said).toContainText("not known");
  await expect(said).not.toContainText(/fail|could not|cannot/i);
  // The form stays, with what was typed, and sends nothing again by itself.
  await expect(input(page, "Name")).toHaveValue("stub");
  await expect(input(page, "Address")).toHaveValue(TYPED);
  expect(service.changes).toHaveLength(1);
  // The list was read again, and it is the list that says the agent is there.
  await expect.poll(() => service.reads).toBe(readBefore + 1);
  await form(page).getByRole("button", { name: "Cancel" }).click();
  await expect(form(page)).toBeHidden();
  await expect(card(page, "stub").getByText(KEPT)).toBeVisible();
  await expect(cards(page)).toHaveCount(2);
});

test("with the service stopped, saving says the outcome is not known and no earlier card is left as current", async ({
  page,
}) => {
  const service = await new AgentService(environmentAgent("hello")).serve(page);
  await page.goto("/agents");
  await expect(card(page, "hello")).toBeVisible();
  await openForm(page);
  await fill(page, "stub", TYPED);

  // From here the page's requests reach the web app's server, which finds no service.
  service.stop();
  await save(page);

  await expect(form(page).getByRole("alert")).toContainText("not known");
  await expect(input(page, "Address")).toHaveValue(TYPED);
  // The list was read again and could not be: the page says so under the form.
  await expect.poll(() => service.reads).toBe(2);
  await form(page).getByRole("button", { name: "Cancel" }).click();
  await expect(alerts(page)).toContainText(UNREACHABLE);
  await expect(cards(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add" })).toHaveCount(0);
});

test("the address is sent in a request body and is in no address the page asked for", async ({
  page,
}) => {
  const asked: string[] = [];
  page.on("request", (request) => asked.push(request.url()));
  const service = await new AgentService(
    savedAgent("other", { url: "http://127.0.0.1:9996" }),
  ).serve(page);
  service.answersAt(9998, false);
  await page.goto("/agents");
  await openForm(page);

  // Refused, then saved, then refused as taken: every answer the form can get for it.
  await fill(page, "stub", TYPED);
  await save(page);
  await expect(form(page).getByRole("alert")).toHaveText(NOTHING_ANSWERED);
  service.answersAt(9998, true);
  await save(page);
  await expect(card(page, "stub").getByText(KEPT)).toBeVisible();
  await openForm(page);
  await fill(page, "again", TYPED);
  await save(page);
  await expectRefusedAt(page, "Address", AGENT_ADDRESS_TAKEN);
  await page.waitForLoadState("networkidle");

  // It reached the service three times, each time in the body.
  expect(
    service.changes.map(({ body }) => (body as { url: string }).url),
  ).toEqual([TYPED, TYPED, TYPED]);
  // No address the page asked for holds it, written plainly or encoded: neither the
  // address's own path, nor a host with its port. The port alone is not looked for, since
  // four digits can turn up in the name of a file the app is built into.
  expect(asked.length).toBeGreaterThan(3);
  for (const url of [...asked, page.url()]) {
    const plain = decodeURIComponent(url).toLowerCase();
    expect(plain).not.toContain("recognizable-stub");
    expect(plain).not.toMatch(/(localhost|127\.0\.0\.1|\[::1\]):999[68]/);
    // A request to the service carries nothing in a query at all.
    if (new URL(url).pathname.startsWith("/api/")) {
      expect(new URL(url).search).toBe("");
    }
  }
  // And the paths the Agents routes were asked at are the list's and no other.
  expect([...new Set(service.asked.map(({ path }) => path))]).toEqual([
    "/agents",
  ]);
});

// 005, User Story 3: check an agent from its card.

const CAN = "The check found that this agent can be used";
const CANNOT = "The check found that this agent cannot be used";
const check = (page: Page, name: string) =>
  card(page, name).getByRole("button", { name: "Check" });
const found = (page: Page, name: string) =>
  card(page, name).getByRole("status");

test("Check on a saved card and on an environment card says the agent can be used, then that it cannot and why, and the card follows", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  const ids = { hello: "env.hello", stub: "0123456789abcdef0123456789abcdef" };

  for (const name of ["stub", "hello"] as const) {
    const shown = card(page, name);
    await check(page, name).click();
    await expect(found(page, name)).toContainText(CAN);
    await expect(shown.getByText(/^Reachable/)).toBeVisible();
    await expect(shown.getByRole("note", { name: "Warning" })).toHaveCount(0);
    await expect(agentsWarning(page)).toHaveCount(0);

    // The agent stops answering, and is checked again.
    service.answers(name, false);
    await check(page, name).click();
    await expect(found(page, name)).toContainText(CANNOT);
    await expect(found(page, name)).toContainText(NOTHING_ANSWERED);
    await expect(found(page, name)).not.toContainText(CAN);
    // The card follows: its own warning, the one above the cards, and the sidebar's number.
    const warning = shown.getByRole("note", { name: "Warning" });
    await expect(warning).toContainText("This agent cannot be reached.");
    await expect(warning).toContainText(NOTHING_ANSWERED);
    await expect(shown.getByText("Unreachable")).toBeVisible();
    await expect(agentsWarning(page)).toContainText(name);
    await expect(agentsBadge(page)).toHaveText("1");

    // It answers again: one more check, and every warning about it is gone.
    service.answers(name, true);
    await check(page, name).click();
    await expect(found(page, name)).toContainText(CAN);
    await expect(warning).toHaveCount(0);
    await expect(agentsWarning(page)).toHaveCount(0);
    await expect(agentsBadge(page)).toHaveCount(0);
  }
  await expect(alerts(page)).toHaveCount(0);

  // Each check named its agent by its id, in the path, and sent nothing with it.
  expect(service.changes).toEqual(
    [ids.stub, ids.stub, ids.stub, ids.hello, ids.hello, ids.hello].map(
      (id) => ({
        method: "POST",
        path: `/agents/${id}/check`,
        body: undefined,
      }),
    ),
  );
  // And the list was read once on opening and once after each check.
  expect(service.reads).toBe(7);
  // A check changes nothing else of an agent.
  await expect(card(page, "stub").getByText("Requires approval")).toBeVisible();
  await expect(
    card(page, "stub").getByText("http://127.0.0.1:9998", { exact: true }),
  ).toBeVisible();
});

test("a check that finds other card details says that the agent's card changed, and the card shows them", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  const claim = card(page, "stub").getByRole("group", {
    name: "What the agent says of itself",
  });
  await expect(claim).toContainText("What the stub agent says it does.");

  // Nothing differs: the check says so by saying nothing of the card.
  await check(page, "stub").click();
  await expect(found(page, "stub")).toContainText(CAN);
  await expect(found(page, "stub")).not.toContainText("card changed");

  service.rewritesCard("stub", {
    description: "It now also translates reports.",
  });
  await check(page, "stub").click();
  await expect(found(page, "stub")).toContainText("The agent's card changed.");
  await expect(found(page, "stub")).toContainText(
    "Its tools are described from the new details from the next run.",
  );
  // The card shows what was just read, as the agent's own claim.
  await expect(claim).toContainText("It now also translates reports.");
  await expect(claim).not.toContainText("What the stub agent says it does.");
  // The other agent's card says nothing of it.
  await expect(card(page, "hello").getByRole("status")).toHaveCount(0);
});

test("while a check is under way the button says so, and a second press sends nothing", async ({
  page,
}) => {
  const service = await new AgentService(savedAgent("stub"))
    .holdChecks()
    .serve(page);
  await page.goto("/agents");

  await check(page, "stub").click();
  const busy = card(page, "stub").getByRole("button", { name: "Checking…" });
  await expect(busy).toBeDisabled();
  await busy.click({ force: true });
  await busy.press("Enter");
  await expect(found(page, "stub")).toHaveCount(0);

  service.answerChecks();
  await expect(found(page, "stub")).toContainText(CAN);
  await expect(check(page, "stub")).toBeEnabled();
  expect(service.changes).toHaveLength(1);
});

test("a check that is refused as busy shows the service's sentence, and the card stays as it was", async ({
  page,
}) => {
  const service = await new AgentService(savedAgent("stub"))
    .checkedElsewhere("stub", true)
    .serve(page);
  await page.goto("/agents");
  await check(page, "stub").click();

  await expect(card(page, "stub").getByRole("alert")).toHaveText(
    ALREADY_CHECKED,
  );
  await expect(found(page, "stub")).toHaveCount(0);
  await expect(card(page, "stub").getByText(/^Reachable/)).toBeVisible();
  // The refusal is not a result: the list was not read again for it.
  await page.waitForLoadState("networkidle");
  expect(service.reads).toBe(1);

  // Once that check has ended, this one goes through, and the refusal is taken back.
  service.checkedElsewhere("stub", false);
  await check(page, "stub").click();
  await expect(found(page, "stub")).toContainText(CAN);
  await expect(card(page, "stub").getByRole("alert")).toHaveCount(0);
});

test("checking an agent that was removed meanwhile has the list read again, and its card goes", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await expect(cards(page)).toHaveCount(2);

  service.removeElsewhere("stub");
  const refused = page.waitForResponse((response) => response.status() === 404);
  await check(page, "stub").click();
  expect((await refused).status()).toBe(404);
  expect(await (await refused).json()).toEqual({
    detail: { code: "agent_not_found", message: NO_SUCH_AGENT },
  });

  // The service says what is so: the agent is not there any more.
  await expect(cards(page)).toHaveCount(1);
  await expect(card(page, "hello")).toBeVisible();
  expect(service.reads).toBe(2);
});

test.describe("in a short, narrow window", () => {
  test.use({ viewport: { width: 400, height: 480 } });

  test("the form's buttons can be reached once it says what is wrong", async ({
    page,
  }) => {
    await new AgentService().serve(page);
    await page.goto("/agents");
    await openForm(page);
    await save(page);
    await expectRefusedAt(page, "Name", "Enter a name.");
    await expectRefusedAt(page, "Address", "Enter the address.");

    for (const name of ["Save", "Cancel"]) {
      const button = form(page).getByRole("button", { name });
      await button.scrollIntoViewIfNeeded();
      await expect(button).toBeInViewport();
    }
    await form(page).getByRole("button", { name: "Cancel" }).click();
    await expect(form(page)).toBeHidden();
  });
});
