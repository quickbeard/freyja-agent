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

  it("offers nothing that changes an agent", async () => {
    stubFetch(list());
    renderList();
    await screen.findByRole("heading", { name: "hello" });
    expect(
      screen.getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["Refresh"]);
    for (const role of ["link", "textbox", "switch", "combobox"]) {
      expect(screen.queryAllByRole(role)).toEqual([]);
    }
  });
});
