import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { AgentList } from "@/components/agents/agent-list";
import type { Agent, AgentList as Agents } from "@/lib/agents";

const HELLO: Agent = {
  id: "env.hello",
  name: "hello",
  source: "environment",
  approval: "none",
  url: null,
  version: null,
  state: "reachable",
  checked_at: "2026-11-19T09:00:00Z",
  reason: null,
  card: {
    name: "Hello World Agent",
    description: "Just a hello world agent",
    version: "1.0.0",
    skills: [],
    cut: false,
  },
  pending_tasks: 0,
  created_at: null,
  updated_at: null,
};

const STUB: Agent = {
  ...HELLO,
  id: "7c9e6679f0a54b3e8d2a1f4b6c8e0a2d",
  name: "stub",
  source: "saved",
  approval: "required",
  url: "http://127.0.0.1:9998",
  version: 1,
  created_at: "2026-11-19T08:50:00Z",
  updated_at: "2026-11-19T08:50:00Z",
};

function list(agents: Agent[] = [HELLO, STUB]): Reply {
  const json: Agents = { agents, mode: "auto", limit: 20, unusable: 0 };
  return { json };
}

// A cache of its own for every test, so one test's list never reaches another. The list is
// read again whenever the window gets the focus, without the wait a browser's page has
// between two such readings.
function renderList() {
  return render(
    <SWRConfig
      value={{
        provider: () => new Map(),
        dedupingInterval: 0,
        focusThrottleInterval: 0,
      }}
    >
      <AgentList />
    </SWRConfig>,
  );
}

/** The operator comes back to the window, and the list is read again behind what is open. */
async function comeBack() {
  await new Promise((resolve) => setTimeout(resolve, 5));
  fireEvent.focus(window);
}

const cards = () =>
  screen.queryAllByRole("heading", { level: 3 }).map((h) => h.textContent);

// While the form is open the page behind it is hidden from assistive technology, so what
// it shows is looked for with that in mind.
const cardsBehindTheForm = () =>
  screen
    .queryAllByRole("heading", { level: 3, hidden: true })
    .map((h) => h.textContent);

describe("AgentList", () => {
  it("shows a placeholder and no card while it loads, then one card per agent in order", async () => {
    const fetchStub = stubFetch(list());
    renderList();
    expect(screen.getByRole("status").textContent).toMatch(/loading agents/i);
    expect(cards()).toEqual([]);

    expect(await screen.findByRole("heading", { name: "hello" })).toBeTruthy();
    expect(cards()).toEqual(["hello", "stub"]);
    expect(screen.queryByRole("status")).toBeNull();
    expect(fetchStub.mock.calls[0][0]).toBe("/api/agents");
  });

  it("keeps the order the service gives, whatever the names", async () => {
    stubFetch(
      list([STUB, { ...HELLO, id: "env.alpha", name: "alpha" }, HELLO]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    expect(cards()).toEqual(["stub", "alpha", "hello"]);
  });

  it("says that no agent is configured when there is none", async () => {
    stubFetch(list([]));
    renderList();
    expect(await screen.findByText("No agent is configured.")).toBeTruthy();
    expect(cards()).toEqual([]);
  });

  it("says the service cannot be reached, shows no card, and reads the list when asked to try again", async () => {
    const fetchStub = stubFetch({ fails: "Failed to fetch" }, list());
    renderList();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(
      "The Freyja service cannot be reached.",
    );
    expect(cards()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "hello" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("says why with the service's own reason, and shows no earlier list as current", async () => {
    const fetchStub = stubFetch(list(), {
      status: 500,
      json: {
        detail: {
          code: "internal",
          message: "The saved agents could not be read.",
        },
      },
    });
    renderList();
    await screen.findByRole("heading", { name: "hello" });

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The saved agents could not be read.");
    // What was loaded before may no longer be true: it is not left on the page.
    await waitFor(() => expect(cards()).toEqual([]));
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("offers Add with agents and with none, and Edit and Remove on a saved agent's card only", async () => {
    stubFetch(list());
    const { unmount } = renderList();
    await screen.findByRole("heading", { name: "hello" });
    // The section's own two, Check on each card, and Edit and Remove where an agent can
    // be changed and removed.
    expect(
      screen.getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["Refresh", "Add", "Check", "Check", "Edit", "Remove"]);
    // The saved agent's own mode has a switch (feature 010), which only asks.
    expect(
      screen
        .getAllByRole("switch")
        .map((found) => found.getAttribute("aria-label")),
    ).toEqual(["Auto mode for stub"]);
    for (const role of [
      "link",
      "textbox",
      "combobox",
      "dialog",
      "alertdialog",
    ]) {
      expect(screen.queryAllByRole(role)).toEqual([]);
    }
    unmount();

    stubFetch(list([]));
    renderList();
    await screen.findByText("No agent is configured.");
    expect(
      screen.getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["Refresh", "Add"]);
  });

  it("opens the form from Add, and reads the list again once an agent is saved", async () => {
    const added: Agent = {
      ...STUB,
      id: "f".repeat(32),
      name: "report",
      url: "http://localhost:9997",
    };
    const fetchStub = stubFetch(
      list([HELLO]),
      { status: 201, json: added },
      list([HELLO, added]),
    );
    renderList();
    await screen.findByRole("heading", { name: "hello" });
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await screen.findByRole("dialog", { name: "Add an agent" });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "report" },
    });
    fireEvent.change(screen.getByLabelText("Address"), {
      target: { value: "HTTP://LocalHost:9997/" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // The new card is the list's own, read again: the address is in the form the service
    // keeps, not as it was typed.
    expect(await screen.findByRole("heading", { name: "report" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(cards()).toEqual(["hello", "report"]);
    expect(screen.getByText("http://localhost:9997")).toBeTruthy();
    expect(document.body.textContent).not.toContain("HTTP://LocalHost:9997/");
    expect(
      fetchStub.mock.calls.map(([path, init]) => [init?.method ?? "GET", path]),
    ).toEqual([
      ["GET", "/api/agents"],
      ["POST", "/api/agents"],
      ["GET", "/api/agents"],
    ]);
  });

  it("opens the form from Add when no agent is configured", async () => {
    stubFetch(list([]));
    renderList();
    await screen.findByText("No agent is configured.");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(
      await screen.findByRole("dialog", { name: "Add an agent" }),
    ).toBeTruthy();
  });

  it("reads the list again when an add got no answer, and the form stays to say the outcome is not known", async () => {
    // The agent was saved, and the answer was lost on its way back.
    const fetchStub = stubFetch(
      list([HELLO]),
      { fails: "Failed to fetch" },
      list([HELLO, STUB]),
    );
    renderList();
    await screen.findByRole("heading", { name: "hello" });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await screen.findByRole("dialog");
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "stub" },
    });
    fireEvent.change(screen.getByLabelText("Address"), {
      target: { value: "http://127.0.0.1:9998" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const notice = await screen.findByRole("alert");
    expect(notice.textContent).toMatch(/not known/);
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(3));
    expect(
      fetchStub.mock.calls.map(([, init]) => init?.method ?? "GET"),
    ).toEqual(["GET", "POST", "GET"]);
    // The list says what is so, under the form that still says what it does not know.
    await waitFor(() =>
      expect(cardsBehindTheForm()).toEqual(["hello", "stub"]),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/not known/);
  });

  it("keeps the form and what it says when the list cannot be read after an add that got no answer", async () => {
    stubFetch(list([HELLO]), { fails: "Failed to fetch" });
    renderList();
    await screen.findByRole("heading", { name: "hello" });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await screen.findByRole("dialog");
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "stub" },
    });
    fireEvent.change(screen.getByLabelText("Address"), {
      target: { value: "http://127.0.0.1:9998" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Two things are said: the form's, and the list's, which could not be read either.
    const alerts = () => screen.getAllByRole("alert", { hidden: true });
    await waitFor(() => expect(alerts()).toHaveLength(2));
    const said = alerts().map((alert) => alert.textContent);
    expect(said.some((text) => /not known/.test(text ?? ""))).toBe(true);
    expect(
      said.some((text) =>
        (text ?? "").includes("The Freyja service cannot be reached."),
      ),
    ).toBe(true);
    // No earlier card is shown as current, and what was typed is still in the form.
    expect(cardsBehindTheForm()).toEqual([]);
    expect(screen.getByRole("alert").textContent).toMatch(/not known/);
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "stub",
    );
    expect((screen.getByLabelText("Address") as HTMLInputElement).value).toBe(
      "http://127.0.0.1:9998",
    );
  });

  it("offers nothing to add while the list could not be read", async () => {
    stubFetch({ fails: "Failed to fetch" });
    renderList();
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  // Part 3 (T072): the warning above the cards, and checking from a card.

  const DOWN: Agent = {
    ...STUB,
    state: "unreachable",
    checked_at: "2026-11-19T09:04:10Z",
    reason: "Nothing answered at the address.",
  };
  const UNUSED: Agent = {
    ...STUB,
    id: "0123456789abcdef0123456789abcdef",
    name: "report",
    state: "not_in_use",
    checked_at: null,
    reason: "An agent named in the environment has this name.",
  };
  const UNTRIED: Agent = {
    ...STUB,
    id: "fedcba9876543210fedcba9876543210",
    name: "later",
    state: "not_tried",
    checked_at: null,
  };
  const warning = () =>
    screen.queryByRole("note", { name: "Agents that cannot be used" });

  it("warns above the cards about each agent that cannot be used, by name", async () => {
    stubFetch(list([HELLO, UNTRIED, UNUSED, DOWN]));
    renderList();
    await screen.findByRole("heading", { name: "hello" });
    expect(warning()?.textContent).toBe(
      "Warning: 2 agents cannot be used: report, stub. Each one's card says why.",
    );
    // Above the cards: it comes before the first of them.
    const first = screen.getByRole("heading", { name: "hello" });
    expect(
      (warning() as HTMLElement).compareDocumentPosition(first) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("warns about nothing when every agent is reachable or has not been tried", async () => {
    stubFetch(list([HELLO, STUB, UNTRIED]));
    renderList();
    await screen.findByRole("heading", { name: "later" });
    expect(warning()).toBeNull();
    expect(screen.queryByText(/cannot be used/)).toBeNull();
  });

  it("warns about no agent while the service cannot be reached", async () => {
    stubFetch(list([HELLO, DOWN]), { fails: "Failed to fetch" });
    renderList();
    await screen.findByRole("heading", { name: "hello" });
    expect(warning()).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByRole("alert");
    // What was read before is not shown as if it were so now.
    expect(warning()).toBeNull();
    expect(screen.queryByText(/cannot be used/)).toBeNull();
  });

  it("reads the list again after a check, and the card and the warning follow it", async () => {
    const usable = {
      usable: true,
      reason: null,
      message: null,
      checked_at: "2026-11-19T10:00:00Z",
      recorded: true,
      card_changed: false,
    };
    const fetchStub = stubFetch(
      list([DOWN]),
      { json: usable },
      list([{ ...STUB, checked_at: usable.checked_at }]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    expect(warning()).not.toBeNull();
    expect(screen.getByRole("note", { name: "Warning" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    const said = await screen.findByRole("status");
    expect(said.textContent).toContain("this agent can be used");
    // One reading after the check, and what it says is what is shown.
    await waitFor(() => expect(warning()).toBeNull());
    expect(screen.queryByRole("note", { name: "Warning" })).toBeNull();
    expect(fetchStub.mock.calls.map(([path]) => path)).toEqual([
      "/api/agents",
      `/api/agents/${STUB.id}/check`,
      "/api/agents",
    ]);
    // The check's own result stays on the card.
    expect(screen.getByRole("status").textContent).toContain(
      "this agent can be used",
    );
  });

  it("reads the list again when the service says the checked agent is gone", async () => {
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      {
        status: 404,
        json: {
          detail: {
            code: "agent_not_found",
            message: "There is no agent with this id.",
          },
        },
      },
      list([HELLO]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    fireEvent.click(screen.getAllByRole("button", { name: "Check" })[1]);
    await waitFor(() => expect(cards()).toEqual(["hello"]));
    expect(fetchStub).toHaveBeenCalledTimes(3);
  });

  it("leaves the list alone when a check is refused as busy", async () => {
    const fetchStub = stubFetch(list([STUB]), {
      status: 409,
      json: {
        detail: {
          code: "check_busy",
          message:
            "This agent is already being checked. Wait for that check to end.",
        },
      },
    });
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "already being checked",
    );
    expect(cards()).toEqual(["stub"]);
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });
});

// Part 3 (T092, T093): changing a saved agent's approval choice from its card.

describe("AgentList, changing an agent", () => {
  const FREED: Agent = {
    ...STUB,
    approval: "none",
    version: 2,
    updated_at: "2026-11-19T09:30:00Z",
  };
  const form = () => screen.findByRole("dialog", { name: "Edit stub" });
  const confirmation = () =>
    screen.queryByRole("note", { name: "Confirmation" });
  const asked = (fetchStub: ReturnType<typeof stubFetch>) =>
    fetchStub.mock.calls.map(([path, init]) => [init?.method ?? "GET", path]);

  async function askToFree() {
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await form();
    fireEvent.click(screen.getByRole("radio", { name: "Auto" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(confirmation()).not.toBeNull();
  }

  it("opens the form from Edit with that agent's values, and reads the list again once the change is confirmed", async () => {
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      { json: { agent: FREED, given_up: 0 } },
      list([HELLO, FREED]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await form();
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "stub",
    );
    expect((screen.getByLabelText("Address") as HTMLInputElement).value).toBe(
      STUB.url,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Auto" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    // Nothing was sent yet: the form asks first.
    expect(asked(fetchStub)).toEqual([["GET", "/api/agents"]]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The card is the list's own, read again.
    const heading = await screen.findByRole("heading", { name: "stub" });
    const shown = heading.closest('[data-slot="card"]') as HTMLElement;
    await waitFor(() => expect(within(shown).getByText("Auto")).toBeTruthy());
    expect(asked(fetchStub)).toEqual([
      ["GET", "/api/agents"],
      ["PATCH", `/api/agents/${STUB.id}`],
      ["GET", "/api/agents"],
    ]);
    expect(JSON.parse(String(fetchStub.mock.calls[1][1]?.body))).toEqual({
      version: 1,
      approval: "none",
    });
  });

  it("takes the confirmation back when the list is read again and shows a later version", async () => {
    const moved = { ...STUB, version: 2, url: "http://127.0.0.1:9997" };
    const fetchStub = stubFetch(list([HELLO, STUB]), list([HELLO, moved]));
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await askToFree();

    // The window gets the focus again, and the list is read again behind the form.
    await comeBack();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("stub was changed elsewhere");
    expect(confirmation()).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(asked(fetchStub)).toEqual([
      ["GET", "/api/agents"],
      ["GET", "/api/agents"],
    ]);
  });

  it("shows the agent in the confirmation as the list has it once it is read again", async () => {
    stubFetch(
      list([HELLO, STUB]),
      list([HELLO, { ...STUB, pending_tasks: 3 }]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await askToFree();
    expect(confirmation()?.textContent).toContain("0 pending tasks.");

    await comeBack();
    await waitFor(() =>
      expect(confirmation()?.textContent).toContain("3 pending tasks."),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps asking, with the agent as the form has it, while the list cannot be read", async () => {
    const fetchStub = stubFetch(list([HELLO, STUB]), {
      fails: "Failed to fetch",
    });
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await askToFree();

    await comeBack();
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(2));
    // The list behind the form says it could not be read; the form does not take that for
    // a change of its agent, and still shows the agent it opened with.
    await waitFor(() =>
      expect(screen.getByRole("alert", { hidden: true })).toBeTruthy(),
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(confirmation()?.textContent).toContain(String(STUB.url));
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
  });

  it("reads the list again when the change is refused because the agent was changed since", async () => {
    const moved = { ...STUB, version: 2, url: "http://127.0.0.1:9997" };
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      {
        status: 409,
        json: {
          detail: {
            code: "agent_changed",
            message:
              "The agent was changed since this page read it. Nothing was changed.",
          },
        },
      },
      list([HELLO, moved]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await askToFree();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    // The form says so, once: in its own words once the list shows the later version.
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "changed elsewhere",
      ),
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(asked(fetchStub)).toEqual([
      ["GET", "/api/agents"],
      ["PATCH", `/api/agents/${STUB.id}`],
      ["GET", "/api/agents"],
    ]);
    // Closed, the card shows the agent as it is now.
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("http://127.0.0.1:9997")).toBeTruthy();
    expect(screen.getByText("Manual")).toBeTruthy();
  });

  it("reads the list again when the service says the agent is gone, and its card goes", async () => {
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      {
        status: 404,
        json: {
          detail: {
            code: "agent_not_found",
            message: "There is no agent with this id.",
          },
        },
      },
      list([HELLO]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await askToFree();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "no longer in the list",
      ),
    );
    expect(fetchStub).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(cards()).toEqual(["hello"]));
  });

  it("reads the list again when a change got no answer, and the form stays to say the outcome is not known", async () => {
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      { fails: "Failed to fetch" },
      list([HELLO, FREED]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await askToFree();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    // The change was made and its answer lost: the list shows a later version, and the
    // form says its own request may be why, not that the agent was changed elsewhere.
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(3));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "stub is no longer as this form has it.",
      ),
    );
    expect(screen.getByRole("alert").textContent).toMatch(/not known/);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("alert").textContent).not.toContain(
      "changed elsewhere",
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("opens each agent's own form, filled from that agent", async () => {
    const report: Agent = {
      ...STUB,
      id: "f".repeat(32),
      name: "report",
      approval: "none",
      url: "http://localhost:9997",
    };
    stubFetch(list([STUB, report]));
    renderList();
    await screen.findByRole("heading", { name: "report" });

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    await screen.findByRole("dialog", { name: "Edit report" });
    expect(
      (
        screen.getByRole("radio", {
          name: "Auto",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await screen.findByRole("dialog", { name: "Edit stub" });
    expect((screen.getByLabelText("Address") as HTMLInputElement).value).toBe(
      STUB.url,
    );
    expect(
      (
        screen.getByRole("radio", {
          name: "Manual",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });
});

// Part 4 (T108, T109): moving a saved agent, and removing one, from its card.

describe("AgentList, moving and removing an agent", () => {
  const BUSY: Agent = { ...STUB, pending_tasks: 2 };
  const ELSEWHERE = "http://127.0.0.1:9996";
  const MOVED: Agent = {
    ...BUSY,
    url: ELSEWHERE,
    version: 2,
    pending_tasks: 0,
  };
  const asked = (fetchStub: ReturnType<typeof stubFetch>) =>
    fetchStub.mock.calls.map(([path, init]) => [init?.method ?? "GET", path]);
  const removal = () => screen.findByRole("alertdialog");
  const said = () => screen.queryByRole("status")?.textContent ?? null;

  async function askToRemove() {
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    return removal();
  }

  const confirmRemoval = (dialog: HTMLElement) =>
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }), {
      detail: 1,
    });

  it("asks before removing, removes at the version the card had, and says how many tasks were given up", async () => {
    const fetchStub = stubFetch(
      list([HELLO, BUSY]),
      { json: { given_up: 2 } },
      list([HELLO]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    expect(screen.queryByRole("alertdialog")).toBeNull();

    const dialog = await askToRemove();
    expect(dialog.textContent).toContain("Remove stub?");
    expect(dialog.textContent).toContain(
      "stub has 2 pending tasks now, and they will be given up",
    );
    // Nothing was sent yet: the dialog asks first.
    expect(asked(fetchStub)).toEqual([["GET", "/api/agents"]]);
    confirmRemoval(dialog);

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await waitFor(() => expect(cards()).toEqual(["hello"]));
    expect(asked(fetchStub)).toEqual([
      ["GET", "/api/agents"],
      ["DELETE", `/api/agents/${STUB.id}?version=1`],
      ["GET", "/api/agents"],
    ]);
    // The console says how many tasks were given up (FR-024).
    expect(said()).toBe(
      "stub was removed. 2 pending tasks were given up: their threads will be told.",
    );
  });

  it.each([
    [0, "stub was removed. It had no pending task, so none was given up."],
    [
      1,
      "stub was removed. 1 pending task was given up: its thread will be told.",
    ],
  ])("says so for a removal that gave up %i", async (givenUp, sentence) => {
    stubFetch(
      list([HELLO, STUB]),
      { json: { given_up: givenUp } },
      list([HELLO]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    confirmRemoval(await askToRemove());
    await waitFor(() => expect(said()).toBe(sentence));
  });

  it("sends nothing when the removal is cancelled, and says nothing", async () => {
    const fetchStub = stubFetch(list([HELLO, BUSY]));
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    const dialog = await askToRemove();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(asked(fetchStub)).toEqual([["GET", "/api/agents"]]);
    expect(cards()).toEqual(["hello", "stub"]);
    expect(said()).toBeNull();
  });

  it("shows the pending tasks in the dialog as the list has them once it is read again", async () => {
    stubFetch(
      list([HELLO, STUB]),
      list([HELLO, { ...STUB, pending_tasks: 3 }]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    const dialog = await askToRemove();
    expect(dialog.textContent).toContain("stub has no pending task now");

    await comeBack();
    await waitFor(() =>
      expect(dialog.textContent).toContain("stub has 3 pending tasks now"),
    );
  });

  it("closes the dialog and says the agent is gone when the list no longer has it", async () => {
    const fetchStub = stubFetch(list([HELLO, STUB]), list([HELLO]));
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await askToRemove();

    // Removed elsewhere, and the list is read again behind the dialog.
    await comeBack();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(cards()).toEqual(["hello"]);
    expect(said()).toBe(
      "stub is no longer in the list: it was removed elsewhere.",
    );
    expect(asked(fetchStub)).toEqual([
      ["GET", "/api/agents"],
      ["GET", "/api/agents"],
    ]);
  });

  it("reads the list again when the agent to remove is already gone, and reports it as gone", async () => {
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      {
        status: 404,
        json: {
          detail: {
            code: "agent_not_found",
            message: "There is no agent with this id.",
          },
        },
      },
      list([HELLO]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    confirmRemoval(await askToRemove());

    await waitFor(() => expect(cards()).toEqual(["hello"]));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(said()).toBe(
      "stub is no longer in the list: it was removed elsewhere.",
    );
    expect(asked(fetchStub)).toEqual([
      ["GET", "/api/agents"],
      ["DELETE", `/api/agents/${STUB.id}?version=1`],
      ["GET", "/api/agents"],
    ]);
  });

  it("reads the list again when the removal is refused because the agent was changed since, and removes nothing", async () => {
    const later = { ...STUB, version: 2, approval: "none" as const };
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      {
        status: 409,
        json: {
          detail: {
            code: "agent_changed",
            message:
              "The agent was changed since this page read it. Nothing was changed.",
          },
        },
      },
      list([HELLO, later]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    const dialog = await askToRemove();
    confirmRemoval(dialog);

    await waitFor(() =>
      expect(within(dialog).getByRole("alert").textContent).toContain(
        "stub was changed elsewhere",
      ),
    );
    expect(
      (
        within(dialog).getByRole("button", {
          name: "Remove",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(asked(fetchStub)).toEqual([
      ["GET", "/api/agents"],
      ["DELETE", `/api/agents/${STUB.id}?version=1`],
      ["GET", "/api/agents"],
    ]);
    // Closed and asked again, it is the agent as it is now, and goes at the later version.
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(cardsBehindTheForm()).toEqual(["hello", "stub"]);
  });

  it("reads the list again when a removal got no answer, and the dialog stays to say the outcome is not known", async () => {
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      { fails: "Failed to fetch" },
      list([HELLO, STUB]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    const dialog = await askToRemove();
    confirmRemoval(dialog);

    const alert = await within(dialog).findByRole("alert");
    expect(alert.textContent).toMatch(/not known/);
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(3));
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(said()).toBeNull();
  });

  it("says what is known when a removal got no answer and the list then no longer has the agent", async () => {
    const fetchStub = stubFetch(
      list([HELLO, STUB]),
      { fails: "Failed to fetch" },
      list([HELLO]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    confirmRemoval(await askToRemove());

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(cards()).toEqual(["hello"]);
    // Not that it was removed elsewhere, and not how many tasks were given up.
    expect(said()).toBe(
      "stub is no longer in the list. The request to remove it got no answer, so how many of its pending tasks were given up is not known here.",
    );
  });

  it("removes a saved agent that is not in use, from the one button its card has", async () => {
    const unused: Agent = {
      ...STUB,
      state: "not_in_use",
      checked_at: null,
      reason: "An agent named in the environment has this name.",
    };
    const fetchStub = stubFetch(
      list([HELLO, unused]),
      { json: { given_up: 0 } },
      list([HELLO]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    expect(
      screen.getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["Refresh", "Add", "Check", "Remove"]);
    confirmRemoval(await askToRemove());

    await waitFor(() => expect(cards()).toEqual(["hello"]));
    expect(asked(fetchStub)[1]).toEqual([
      "DELETE",
      `/api/agents/${STUB.id}?version=1`,
    ]);
  });

  async function move() {
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit stub" });
    fireEvent.change(screen.getByLabelText("Address"), {
      target: { value: ELSEWHERE },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  }

  it("asks before moving, moves at the version the card had, and says how many tasks were given up", async () => {
    const fetchStub = stubFetch(
      list([HELLO, BUSY]),
      { json: { agent: MOVED, given_up: 2 } },
      list([HELLO, MOVED]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await move();

    const asks = screen.getByRole("note", { name: "Confirmation" });
    expect(asks.textContent).toContain(`Move stub to ${ELSEWHERE}?`);
    expect(asks.textContent).toContain(
      "stub has 2 pending tasks now, and they will be given up",
    );
    expect(asked(fetchStub)).toEqual([["GET", "/api/agents"]]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const heading = await screen.findByRole("heading", { name: "stub" });
    const shown = heading.closest('[data-slot="card"]') as HTMLElement;
    await waitFor(() =>
      expect(within(shown).getByText(ELSEWHERE)).toBeTruthy(),
    );
    expect(asked(fetchStub)).toEqual([
      ["GET", "/api/agents"],
      ["PATCH", `/api/agents/${STUB.id}`],
      ["GET", "/api/agents"],
    ]);
    expect(JSON.parse(String(fetchStub.mock.calls[1][1]?.body))).toEqual({
      version: 1,
      url: ELSEWHERE,
    });
    expect(said()).toBe(
      "stub was moved. 2 pending tasks were given up: their threads will be told.",
    );
  });

  it("says a move gave no task up when none was pending", async () => {
    stubFetch(
      list([HELLO, STUB]),
      { json: { agent: { ...MOVED, pending_tasks: 0 }, given_up: 0 } },
      list([HELLO, MOVED]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await move();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(said()).toBe(
        "stub was moved. It had no pending task, so none was given up.",
      ),
    );
  });

  it("says nothing of tasks after a change of the approval choice alone", async () => {
    const freed: Agent = { ...STUB, approval: "none", version: 2 };
    stubFetch(
      list([HELLO, STUB]),
      { json: { agent: freed, given_up: 0 } },
      list([HELLO, freed]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit stub" });
    fireEvent.click(screen.getByRole("radio", { name: "Auto" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const heading = await screen.findByRole("heading", { name: "stub" });
    const shown = heading.closest('[data-slot="card"]') as HTMLElement;
    await waitFor(() => expect(within(shown).getByText("Auto")).toBeTruthy());
    expect(said()).toBeNull();
  });

  it("takes back what it said of the last change once another one is started", async () => {
    stubFetch(
      list([HELLO, BUSY]),
      { json: { agent: MOVED, given_up: 2 } },
      list([HELLO, MOVED]),
    );
    renderList();
    await screen.findByRole("heading", { name: "stub" });
    await move();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(said()).toContain("stub was moved."));

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await screen.findByRole("dialog", { name: "Add an agent" });
    expect(screen.queryByRole("status", { hidden: true })).toBeNull();
  });
});
