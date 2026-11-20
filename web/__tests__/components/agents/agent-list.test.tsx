import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// A cache of its own for every test, so one test's list never reaches another.
function renderList() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <AgentList />
    </SWRConfig>,
  );
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

  it("offers Add with agents and with none, and nothing on a card that changes an agent", async () => {
    stubFetch(list());
    const { unmount } = renderList();
    await screen.findByRole("heading", { name: "hello" });
    expect(
      screen.getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["Refresh", "Add"]);
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
});
