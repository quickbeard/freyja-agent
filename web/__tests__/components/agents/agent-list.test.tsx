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
  const json: Agents = { agents, limit: 20, unusable: 0 };
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

  it("offers Add with agents and with none, and Edit on a saved agent's card only", async () => {
    stubFetch(list());
    const { unmount } = renderList();
    await screen.findByRole("heading", { name: "hello" });
    // The section's own two, Check on each card, and Edit where an agent can be changed.
    expect(
      screen.getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["Refresh", "Add", "Check", "Check", "Edit"]);
    for (const role of ["link", "textbox", "switch", "combobox", "dialog"]) {
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
    fireEvent.click(
      screen.getByRole("radio", { name: "Runs without approval" }),
    );
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
    fireEvent.click(
      screen.getByRole("radio", { name: "Runs without approval" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    // Nothing was sent yet: the form asks first.
    expect(asked(fetchStub)).toEqual([["GET", "/api/agents"]]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The card is the list's own, read again.
    const heading = await screen.findByRole("heading", { name: "stub" });
    const shown = heading.closest('[data-slot="card"]') as HTMLElement;
    await waitFor(() =>
      expect(within(shown).getByText("Runs without approval")).toBeTruthy(),
    );
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
    expect(screen.getByText("Requires approval")).toBeTruthy();
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

    // The change was made and its answer lost: the list shows a later version.
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    const said = screen.getAllByRole("alert").map((one) => one.textContent);
    expect(said[0]).toMatch(/not known/);
    expect(said[1]).toContain("changed elsewhere");
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
          name: "Runs without approval",
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
          name: "Requires approval",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });
});
