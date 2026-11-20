import { expect, type Page, test } from "@playwright/test";
import {
  AGENT_ADDRESS_TAKEN,
  AGENT_CHANGED_SINCE,
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

  // The form says what adding means for approval. The choice is set to requiring it, and
  // this test does not touch it.
  await expect(form(page)).toContainText(
    "Messages to this agent will require approval",
  );
  await expect(
    form(page).getByRole("radio", { name: "Requires approval" }),
  ).toBeChecked();
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
    await expect(agentsBadge(page)).toHaveText("1 cannot be used");

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

// 005, User Story 4: the approval choice, when adding and on a saved agent.

const FREE = "Runs without approval";
const REQUIRED = "Requires approval";
const STUB_ID = "0123456789abcdef0123456789abcdef";
const editForm = (page: Page, name = "stub") =>
  page.getByRole("dialog", { name: `Edit ${name}` });
const asks = (page: Page) =>
  page.getByRole("dialog").getByRole("note", { name: "Confirmation" });

async function openEdit(page: Page, name = "stub") {
  await card(page, name).getByRole("button", { name: "Edit" }).click();
  await expect(editForm(page, name)).toBeVisible();
}

/** The operator comes back to the window, and the list is read again behind what is open. */
async function comeBack(page: Page, service: AgentService) {
  const before = service.reads;
  // The page waits a few seconds between two such readings, so it is asked until it reads.
  await expect
    .poll(
      async () => {
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        return service.reads;
      },
      { timeout: 20_000 },
    )
    .toBeGreaterThan(before);
}

test("adding an agent to run without approval asks first, sends nothing before Confirm, and Cancel leaves no card", async ({
  page,
}) => {
  const service = await new AgentService(environmentAgent("hello")).serve(page);
  await page.goto("/agents");
  await openForm(page);
  await fill(page, "stub", TYPED);
  await form(page).getByRole("radio", { name: FREE }).check();
  await expect(form(page)).toContainText(
    "Messages to this agent will be sent without approval",
  );
  await save(page);

  // It names the agent and says what running without approval means.
  await expect(asks(page)).toContainText("Add stub to run without approval?");
  await expect(asks(page)).toContainText(
    "Nothing Freyja sends to stub will be seen by a person first.",
  );
  // Enter in a field asks again and sends nothing.
  await input(page, "Address").press("Enter");
  await expect(asks(page)).toBeVisible();
  expect(service.changes).toEqual([]);

  await form(page).getByRole("button", { name: "Cancel" }).click();
  await expect(form(page)).toBeHidden();
  await page.waitForLoadState("networkidle");
  expect(service.changes).toEqual([]);
  await expect(cards(page)).toHaveCount(1);

  // Asked again and confirmed, the agent is saved with the choice, in one request.
  await openForm(page);
  await expect(form(page).getByRole("radio", { name: REQUIRED })).toBeChecked();
  await fill(page, "stub", TYPED);
  await form(page).getByRole("radio", { name: FREE }).check();
  await save(page);
  await form(page).getByRole("button", { name: "Confirm" }).dblclick();

  await expect(form(page)).toBeHidden();
  await expect(card(page, "stub").getByText(FREE)).toBeVisible();
  await expect(card(page, "stub").getByText(KEPT)).toBeVisible();
  expect(service.changes).toEqual([
    {
      method: "POST",
      path: "/agents",
      body: { name: "stub", url: TYPED, approval: "none" },
    },
  ]);
});

test("switching an agent to run without approval asks first and says what is given up; Cancel changes nothing, Confirm changes the card", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub", { pending_tasks: 2 }),
  ).serve(page);
  await page.goto("/agents");
  await openEdit(page);

  // The form opens with the agent's values; its name cannot be edited. Its address can,
  // since moving an agent was added, and is left as it is here.
  const dialog = editForm(page);
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue("stub");
  await expect(dialog.getByLabel("Name", { exact: true })).not.toBeEditable();
  await expect(dialog.getByLabel("Address", { exact: true })).toHaveValue(
    "http://127.0.0.1:9998",
  );
  await expect(dialog.getByRole("radio", { name: REQUIRED })).toBeChecked();

  // Saved as it is, nothing is asked and nothing is sent.
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog.getByRole("status")).toContainText("Nothing is changed");
  await expect(asks(page)).toHaveCount(0);

  await dialog.getByRole("radio", { name: FREE }).check();
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(asks(page)).toContainText("Let stub run without approval?");
  await expect(asks(page)).toContainText(
    "Every message to stub will be sent without a person seeing it first, from the next run.",
  );
  await expect(asks(page)).toContainText(
    "A proposal for stub that is already waiting for a decision can then only be approved.",
  );
  // The agent as the list has it at this moment.
  await expect(asks(page)).toContainText(
    "As the list has it now: stub, at http://127.0.0.1:9998, requires approval, reachable, 2 pending tasks.",
  );
  expect(service.changes).toEqual([]);

  // Canceled: no request, and the card is as it was.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await page.waitForLoadState("networkidle");
  expect(service.changes).toEqual([]);
  await expect(card(page, "stub").getByText(REQUIRED)).toBeVisible();

  // Asked again and confirmed: one request, with the version the form opened with and
  // only the choice, and the card follows the list.
  await openEdit(page);
  await expect(dialog.getByRole("radio", { name: REQUIRED })).toBeChecked();
  await dialog.getByRole("radio", { name: FREE }).check();
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.getByRole("button", { name: "Confirm" }).click();

  await expect(dialog).toBeHidden();
  await expect(card(page, "stub").getByText(FREE)).toBeVisible();
  await expect(card(page, "stub").getByText(REQUIRED)).toHaveCount(0);
  expect(service.changes).toEqual([
    {
      method: "PATCH",
      path: `/agents/${STUB_ID}`,
      body: { version: 1, approval: "none" },
    },
  ]);
  await expect(alerts(page)).toHaveCount(0);
  // The address is in no path the page asked at.
  expect(service.asked.every(({ path }) => !path.includes("9998"))).toBe(true);
});

test("switching an agent back to requiring approval asks and says that it holds at once", async ({
  page,
}) => {
  const service = await new AgentService(
    savedAgent("stub", { approval: "none", version: 4 }),
  ).serve(page);
  await page.goto("/agents");
  await expect(card(page, "stub").getByText(FREE)).toBeVisible();
  await openEdit(page);
  const dialog = editForm(page);
  await expect(dialog.getByRole("radio", { name: FREE })).toBeChecked();

  await dialog.getByRole("radio", { name: REQUIRED }).check();
  // A double press on Save asks, and confirms nothing.
  await dialog.getByRole("button", { name: "Save" }).dblclick();
  await expect(asks(page)).toContainText("Require approval for stub?");
  await expect(asks(page)).toContainText(
    "This holds at once, also for runs in progress.",
  );
  await expect(asks(page)).toContainText(
    "A paused step that holds a message to stub that needed no approval cannot be continued until the choice is changed back or the agent is removed.",
  );
  await expect(asks(page)).toContainText("runs without approval, reachable");
  expect(service.changes).toEqual([]);

  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog).toBeHidden();
  await expect(card(page, "stub").getByText(REQUIRED)).toBeVisible();
  expect(service.changes).toEqual([
    {
      method: "PATCH",
      path: `/agents/${STUB_ID}`,
      body: { version: 4, approval: "required" },
    },
  ]);
});

test("a change made elsewhere while the form is open takes the confirmation back", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await openEdit(page);
  const dialog = editForm(page);
  await dialog.getByRole("radio", { name: FREE }).check();
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(asks(page)).toBeVisible();

  // Another operator changes the agent, and this page reads the list again.
  service.changeElsewhere("stub", { url: "http://127.0.0.1:9997" });
  await comeBack(page, service);

  await expect(asks(page)).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Confirm" })).toHaveCount(0);
  await expect(dialog.getByRole("alert")).toContainText(
    "stub was changed elsewhere while this form was open",
  );
  await expect(dialog.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(service.changes).toEqual([]);

  // Opened again, the form is of the agent as it is now, and the change goes through at
  // the later version.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await openEdit(page);
  await expect(dialog.getByLabel("Address", { exact: true })).toHaveValue(
    "http://127.0.0.1:9997",
  );
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await dialog.getByRole("radio", { name: FREE }).check();
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog).toBeHidden();
  await expect(card(page, "stub").getByText(FREE)).toBeVisible();
  expect(service.changes).toEqual([
    {
      method: "PATCH",
      path: `/agents/${STUB_ID}`,
      body: { version: 2, approval: "none" },
    },
  ]);
});

test("a change refused because the agent was changed since shows the agent as it is now", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await openEdit(page);
  const dialog = editForm(page);
  await dialog.getByRole("radio", { name: FREE }).check();
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(asks(page)).toBeVisible();

  // Changed elsewhere, and this page has not read the list since.
  service.changeElsewhere("stub", { url: "http://127.0.0.1:9997" });
  const readBefore = service.reads;
  const refused = page.waitForResponse((response) => response.status() === 409);
  await dialog.getByRole("button", { name: "Confirm" }).click();
  expect(await (await refused).json()).toEqual({
    detail: { code: "agent_changed", message: AGENT_CHANGED_SINCE },
  });

  // The form says so, the list was read again, and nothing was changed by this page.
  await expect.poll(() => service.reads).toBe(readBefore + 1);
  await expect(dialog.getByRole("alert")).toHaveCount(1);
  await expect(dialog.getByRole("alert")).toContainText("changed elsewhere");
  await expect(asks(page)).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(service.agents.find(({ name }) => name === "stub")?.approval).toBe(
    "required",
  );

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  const shown = card(page, "stub");
  await expect(
    shown.getByText("http://127.0.0.1:9997", { exact: true }),
  ).toBeVisible();
  await expect(shown.getByText(REQUIRED)).toBeVisible();
  await expect(shown.getByText(FREE)).toHaveCount(0);
});

test("a change of an agent that was removed meanwhile has the list read again, and its card goes", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await openEdit(page);
  const dialog = editForm(page);
  await dialog.getByRole("radio", { name: FREE }).check();
  await dialog.getByRole("button", { name: "Save" }).click();

  service.removeElsewhere("stub");
  await dialog.getByRole("button", { name: "Confirm" }).click();

  await expect(dialog.getByRole("alert")).toContainText(
    "stub is no longer in the list",
  );
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(cards(page)).toHaveCount(1);
  await expect(card(page, "hello")).toBeVisible();
});

test("a change that gets no answer says the outcome is not known, and the list is read again", async ({
  page,
}) => {
  const service = await new AgentService(savedAgent("stub")).serve(page);
  await page.goto("/agents");
  await openEdit(page);
  const dialog = editForm(page);
  await dialog.getByRole("radio", { name: FREE }).check();
  await dialog.getByRole("button", { name: "Save" }).click();
  const readBefore = service.reads;

  // The service makes the change, and its answer never arrives.
  service.loseNextAnswer();
  await dialog.getByRole("button", { name: "Confirm" }).click();

  const said = dialog.getByRole("alert").first();
  await expect(said).toContainText("not known");
  await expect(said).not.toContainText(/fail|could not|cannot/i);
  await expect.poll(() => service.reads).toBe(readBefore + 1);
  // Nothing is sent again by itself; the list says what is so.
  expect(service.changes).toHaveLength(1);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await expect(card(page, "stub").getByText(FREE)).toBeVisible();
});

test("an environment agent's card offers no way to change its approval choice, and neither does a saved agent's that is not in use", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello", { approval: "none" }),
    savedAgent("shadowed", {
      id: "2".repeat(32),
      state: "not_in_use",
      checked_at: null,
      reason: "An agent named in the environment has this name.",
    }),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await expect(card(page, "stub")).toBeVisible();

  await expect(card(page, "hello").getByRole("button")).toHaveText(["Check"]);
  await expect(card(page, "shadowed").getByRole("button")).toHaveText([
    "Remove",
  ]);
  await expect(card(page, "stub").getByRole("button")).toHaveText([
    "Check",
    "Edit",
    "Remove",
  ]);
  // Nothing on a card is a choice by itself: no radio, switch, or field.
  for (const role of ["radio", "switch", "textbox", "combobox"] as const) {
    await expect(page.getByRole("main").getByRole(role)).toHaveCount(0);
  }
  expect(service.changes).toEqual([]);
});

// 005, User Story 5: move a saved agent, and remove one.

const OTHER_TYPED = "HTTP://LocalHost:9996/agents/Recognizable-Other/";
const OTHER_KEPT = "http://localhost:9996/agents/Recognizable-Other";
const removal = (page: Page) => page.getByRole("alertdialog");
const confirmRemoval = (page: Page) =>
  removal(page).getByRole("button", { name: "Remove" }).click();
const address = (page: Page, name = "stub") =>
  editForm(page, name).getByLabel("Address", { exact: true });

async function askToRemove(page: Page, name = "stub") {
  await card(page, name).getByRole("button", { name: "Remove" }).click();
  await expect(removal(page)).toBeVisible();
}

test("moving an agent asks first with its pending tasks, and once confirmed the card shows the new address and the console says how many were given up", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub", { pending_tasks: 2 }),
  ).serve(page);
  await page.goto("/agents");
  const shown = card(page, "stub");
  // The card says how many tasks a move or a removal would give up.
  await expect(shown.getByText("Pending tasks")).toBeVisible();
  await openEdit(page);
  const dialog = editForm(page);
  await expect(address(page)).toHaveValue("http://127.0.0.1:9998");

  await address(page).fill(OTHER_TYPED);
  await dialog.getByRole("button", { name: "Save" }).click();

  // Nothing is sent: the form says what the move means, with the tasks as the list has them.
  await expect(asks(page)).toContainText(`Move stub to ${OTHER_TYPED}?`);
  await expect(asks(page)).toContainText(
    "The change applies from the next run: a run in progress finishes at the earlier address.",
  );
  await expect(asks(page)).toContainText(
    "A proposal for stub that is already waiting for a decision will go to the new address once approved.",
  );
  await expect(asks(page)).toContainText(
    "stub has 2 pending tasks now, and they will be given up",
  );
  expect(service.changes).toEqual([]);

  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog).toBeHidden();
  // The card shows the address as the service keeps it, and the console says how many
  // tasks were given up.
  await expect(shown.getByText(OTHER_KEPT, { exact: true })).toBeVisible();
  await expect(shown.getByText("Pending tasks")).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText(
    "stub was moved. 2 pending tasks were given up: their threads will be told.",
  );
  // One request, at the version the form opened with, with only the address, in its body.
  expect(service.changes).toEqual([
    {
      method: "PATCH",
      path: `/agents/${STUB_ID}`,
      body: { version: 1, url: OTHER_TYPED.trim() },
    },
  ]);
  expect(
    service.asked.every(
      ({ path }) => !path.toLowerCase().includes("recognizable"),
    ),
  ).toBe(true);
  await expect(alerts(page)).toHaveCount(0);
});

test("a move to an address where nothing answers, or where another agent is, is refused and the card keeps its address", async ({
  page,
}) => {
  const service = await new AgentService(
    savedAgent("other", { id: "3".repeat(32), url: "http://127.0.0.1:9996" }),
    savedAgent("stub", { pending_tasks: 1 }),
  ).serve(page);
  service.answersAt(9995, false);
  await page.goto("/agents");
  await openEdit(page);
  const dialog = editForm(page);

  // Nothing answers there: the service's sentence, over the form, and what was typed stays.
  await address(page).fill("http://127.0.0.1:9995");
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("alert")).toHaveText(NOTHING_ANSWERED);
  await expect(address(page)).toHaveValue("http://127.0.0.1:9995");
  await expect(asks(page)).toHaveCount(0);

  // Another agent is there: said at the address.
  await address(page).fill("http://localhost:9996/");
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(address(page)).toHaveAttribute("aria-invalid", "true");
  const described =
    (await address(page).getAttribute("aria-describedby")) ?? "";
  await expect(
    page.locator(`[id="${described.split(" ").at(-1)}"]`),
  ).toHaveText(AGENT_ADDRESS_TAKEN);
  await expect(dialog.getByRole("alert")).toHaveCount(0);

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  // Refused twice: the agent is where it was, with its task, and nothing was given up.
  const shown = card(page, "stub");
  await expect(
    shown.getByText("http://127.0.0.1:9998", { exact: true }),
  ).toBeVisible();
  await expect(shown.getByText("Pending tasks")).toBeVisible();
  await expect(page.getByRole("status")).toHaveCount(0);
  expect(service.changes).toHaveLength(2);
  expect(service.agents.find(({ name }) => name === "stub")?.version).toBe(1);
});

test("a removal names the agent and its pending tasks, sends nothing before it is confirmed, and once confirmed the card is gone", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub", { pending_tasks: 1, version: 3 }),
  ).serve(page);
  await page.goto("/agents");
  await expect(cards(page)).toHaveCount(2);

  // A double press on Remove asks, and removes nothing.
  await card(page, "stub").getByRole("button", { name: "Remove" }).dblclick();
  const dialog = removal(page);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading")).toHaveText("Remove stub?");
  await expect(dialog).toContainText(
    "The removal holds at once, also for runs in progress",
  );
  await expect(dialog).toContainText(
    "stub has 1 pending task now, and it will be given up",
  );
  expect(service.changes).toEqual([]);

  // Canceled: no request, and the card is as it was.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(service.changes).toEqual([]);
  await expect(cards(page)).toHaveCount(2);

  // Asked again and confirmed: one request, at the version the card had.
  await askToRemove(page);
  await confirmRemoval(page);
  await expect(dialog).toBeHidden();
  await expect(cards(page)).toHaveCount(1);
  await expect(card(page, "hello")).toBeVisible();
  await expect(page.getByRole("status")).toHaveText(
    "stub was removed. 1 pending task was given up: its thread will be told.",
  );
  expect(service.changes).toEqual([
    {
      method: "DELETE",
      path: `/agents/${STUB_ID}?version=3`,
      body: undefined,
    },
  ]);
  await expect(alerts(page)).toHaveCount(0);
});

test("a saved agent that is not in use can be removed, from the one control its card has", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("shadowed", {
      id: "2".repeat(32),
      state: "not_in_use",
      checked_at: null,
      reason: "An agent named in the environment has this name.",
    }),
  ).serve(page);
  await page.goto("/agents");
  await expect(card(page, "shadowed").getByRole("button")).toHaveText([
    "Remove",
  ]);
  await askToRemove(page, "shadowed");
  await expect(removal(page)).toContainText(
    "shadowed has no pending task now, so none will be given up.",
  );
  await confirmRemoval(page);

  await expect(cards(page)).toHaveCount(1);
  await expect(agentsWarning(page)).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText(
    "shadowed was removed. It had no pending task, so none was given up.",
  );
  expect(service.changes.map(({ method, path }) => [method, path])).toEqual([
    ["DELETE", `/agents/${"2".repeat(32)}?version=1`],
  ]);
});

test("an agent removed elsewhere is reported as gone, and the list is read again", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await askToRemove(page);

  // Removed by another operator, and this page has not read the list since.
  service.removeElsewhere("stub");
  const readBefore = service.reads;
  const refused = page.waitForResponse((response) => response.status() === 404);
  await confirmRemoval(page);
  expect(await (await refused).json()).toEqual({
    detail: { code: "agent_not_found", message: NO_SUCH_AGENT },
  });

  // The list was read again, the dialog went with its agent, and the console says so.
  await expect.poll(() => service.reads).toBe(readBefore + 1);
  await expect(removal(page)).toBeHidden();
  await expect(cards(page)).toHaveCount(1);
  await expect(page.getByRole("status")).toHaveText(
    "stub is no longer in the list: it was removed elsewhere.",
  );
});

test("a removal, and a move, from values that are no longer current is refused, and the card shows the agent as it is now", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");

  // A removal asked of the agent as it was.
  await askToRemove(page);
  service.changeElsewhere("stub", { approval: "none" });
  let readBefore = service.reads;
  let refused = page.waitForResponse((response) => response.status() === 409);
  await confirmRemoval(page);
  expect(await (await refused).json()).toEqual({
    detail: { code: "agent_changed", message: AGENT_CHANGED_SINCE },
  });
  await expect.poll(() => service.reads).toBe(readBefore + 1);
  const dialog = removal(page);
  await expect(dialog.getByRole("alert")).toHaveCount(1);
  await expect(dialog.getByRole("alert")).toContainText(
    "stub was changed elsewhere while this was open",
  );
  await expect(dialog.getByRole("button", { name: "Remove" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  // Nothing was removed, and the card is the agent as it is now.
  await expect(card(page, "stub").getByText(FREE)).toBeVisible();
  expect(service.agents.some(({ name }) => name === "stub")).toBe(true);

  // A move asked of the agent as it was.
  await openEdit(page);
  const form = editForm(page);
  await address(page).fill(OTHER_TYPED);
  await form.getByRole("button", { name: "Save" }).click();
  await expect(asks(page)).toBeVisible();
  service.changeElsewhere("stub", { approval: "required" });
  readBefore = service.reads;
  refused = page.waitForResponse((response) => response.status() === 409);
  await form.getByRole("button", { name: "Confirm" }).click();
  expect((await (await refused).json()).detail.code).toBe("agent_changed");
  await expect.poll(() => service.reads).toBe(readBefore + 1);
  await expect(form.getByRole("alert")).toContainText("changed elsewhere");
  await expect(form.getByRole("button", { name: "Save" })).toBeDisabled();
  await form.getByRole("button", { name: "Cancel" }).click();
  await expect(form).toBeHidden();
  const shown = card(page, "stub");
  await expect(
    shown.getByText("http://127.0.0.1:9998", { exact: true }),
  ).toBeVisible();
  await expect(shown.getByText(REQUIRED)).toBeVisible();
  await expect(page.getByRole("status")).toHaveCount(0);
});

test("a removal that gets no answer says the outcome is not known, and the list is read again", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello"),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await askToRemove(page);
  const readBefore = service.reads;

  // The service removes the agent, and its answer never arrives.
  service.loseNextAnswer();
  await confirmRemoval(page);

  // The list is read again and no longer has the agent: the dialog goes with it, and the
  // console says what is known, never that the removal failed.
  await expect.poll(() => service.reads).toBe(readBefore + 1);
  await expect(removal(page)).toBeHidden();
  await expect(cards(page)).toHaveCount(1);
  await expect(page.getByRole("status")).toHaveText(
    "stub is no longer in the list. The request to remove it got no answer, so how many of its pending tasks were given up is not known here.",
  );
  await expect(page.getByRole("status")).not.toContainText(
    /fail|could not|elsewhere/i,
  );
  expect(service.changes).toHaveLength(1);
});

test("there is no way to change a name, and none to move or remove an environment agent", async ({
  page,
}) => {
  const service = await new AgentService(
    environmentAgent("hello", { approval: "none" }),
    savedAgent("stub"),
  ).serve(page);
  await page.goto("/agents");
  await expect(card(page, "stub")).toBeVisible();

  // An environment agent's card has Check and nothing else, and shows no address.
  await expect(card(page, "hello").getByRole("button")).toHaveText(["Check"]);
  await expect(card(page, "hello").getByText("Address")).toHaveCount(0);
  // A saved agent's name is shown in its form and cannot be typed over; its address can.
  await openEdit(page);
  const dialog = editForm(page);
  const name = dialog.getByLabel("Name", { exact: true });
  await expect(name).toHaveValue("stub");
  await expect(name).not.toBeEditable();
  await expect(address(page)).toBeEditable();
  await expect(dialog).toContainText("A name cannot be changed.");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  expect(service.changes).toEqual([]);
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

  test("the removal's buttons can be reached, with its pending tasks said", async ({
    page,
  }) => {
    await new AgentService(savedAgent("stub", { pending_tasks: 12 })).serve(
      page,
    );
    await page.goto("/agents");
    await askToRemove(page);
    await expect(removal(page)).toContainText("stub has 12 pending tasks now");
    for (const name of ["Remove", "Cancel"]) {
      const button = removal(page).getByRole("button", { name });
      await button.scrollIntoViewIfNeeded();
      await expect(button).toBeInViewport();
    }
    await removal(page).getByRole("button", { name: "Cancel" }).click();
    await expect(removal(page)).toBeHidden();
  });
});
