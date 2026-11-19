import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig, useSWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { AppSidebar } from "@/components/app-sidebar";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AGENTS_KEY, type Agent, type AgentList } from "@/lib/agents";

const navigation = vi.hoisted(() => ({ pathname: "/llm" }));
vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
}));

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
  card: null,
  pending_tasks: 0,
  created_at: null,
  updated_at: null,
};

function agent(name: string, state: Agent["state"]): Agent {
  return { ...HELLO, id: `env.${name}`, name, state };
}

function list(...agents: Agent[]): Reply {
  const json: AgentList = { agents, limit: 20, unusable: 0 };
  return { json };
}

// Stands for whatever else reads the list again: the Agents section, or the window
// coming back into focus.
function ReadAgain() {
  const { mutate } = useSWRConfig();
  return (
    <button type="button" onClick={() => mutate(AGENTS_KEY)}>
      Read the list again
    </button>
  );
}

// A cache of its own for every test, so one test's list never reaches another.
function renderSidebar() {
  return render(
    // A retry, where one is made, comes within some thirty milliseconds, not seconds: a test
    // that waits for one not to come can then fail.
    <SWRConfig
      value={{
        provider: () => new Map(),
        dedupingInterval: 0,
        errorRetryInterval: 10,
      }}
    >
      <TooltipProvider>
        <SidebarProvider>
          <AppSidebar />
          <SidebarTrigger />
          <ReadAgain />
        </SidebarProvider>
      </TooltipProvider>
    </SWRConfig>,
  );
}

const agentsLink = () => screen.getByRole("link", { name: "Agents" });
const badge = () => document.querySelector('[data-slot="sidebar-menu-badge"]');

/** What the Agents entry is described by, for assistive technology. */
function described(): string | null {
  const id = agentsLink().getAttribute("aria-describedby");
  return id === null ? null : (document.getElementById(id)?.textContent ?? "");
}

describe("AppSidebar", () => {
  beforeEach(() => {
    navigation.pathname = "/llm";
    // The sidebar asks for the list of agents wherever it is shown.
    stubFetch(list(HELLO));
  });

  it("lists the console's sections as links", () => {
    renderSidebar();
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["LLM", "Agents"]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/llm",
      "/agents",
    ]);
  });

  it("marks the section in use and no other", () => {
    renderSidebar();
    expect(
      screen.getByRole("link", { name: "LLM" }).getAttribute("aria-current"),
    ).toBe("page");
    expect(
      screen.getByRole("link", { name: "Agents" }).getAttribute("aria-current"),
    ).toBeNull();
  });

  it("follows the address", () => {
    navigation.pathname = "/agents";
    renderSidebar();
    expect(
      screen.getByRole("link", { name: "Agents" }).getAttribute("aria-current"),
    ).toBe("page");
    expect(
      screen.getByRole("link", { name: "LLM" }).getAttribute("aria-current"),
    ).toBeNull();
  });

  it("closes over a narrow window once a section is chosen", async () => {
    // Under the sidebar's breakpoint it is a sheet over the page, closed at first.
    vi.stubGlobal("innerWidth", 500);
    renderSidebar();
    expect(screen.queryByRole("link", { name: "Agents" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }));
    const agents = await screen.findByRole("link", { name: "Agents" });
    // The link's own navigation needs a router, which a unit test has none of.
    agents.addEventListener("click", (event) => event.preventDefault());
    fireEvent.click(agents);

    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Agents" })).toBeNull(),
    );
  });

  it("marks a section for a page inside it", () => {
    navigation.pathname = "/llm/anything";
    renderSidebar();
    expect(
      screen.getByRole("link", { name: "LLM" }).getAttribute("aria-current"),
    ).toBe("page");
  });

  // T073: the number of agents that cannot be used, on the Agents entry.

  it("asks for the list of agents once, at the path the Agents section reads", async () => {
    const fetchStub = stubFetch(list(HELLO));
    renderSidebar();
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(fetchStub.mock.calls[0][0]).toBe("/api/agents");
    expect(fetchStub.mock.calls[0][1]?.method ?? "GET").toBe("GET");
  });

  it("carries the number of agents that cannot be used, and says what the number means", async () => {
    stubFetch(
      list(
        HELLO,
        agent("stub", "unreachable"),
        agent("report", "not_in_use"),
        agent("later", "not_tried"),
      ),
    );
    renderSidebar();
    await waitFor(() => expect(badge()).not.toBeNull());
    // The number is for the eye; the words are for whoever cannot see where it stands.
    expect(badge()?.textContent).toBe("22 agents cannot be used");
    expect(badge()?.querySelector('[aria-hidden="true"]')?.textContent).toBe(
      "2",
    );
    expect(described()).toBe("22 agents cannot be used");
    // The entry is still named Agents, and still leads to the section.
    expect(agentsLink().getAttribute("href")).toBe("/agents");
    expect(screen.getByRole("link", { name: "LLM" })).toBeTruthy();
  });

  it("says one agent in the singular", async () => {
    stubFetch(list(HELLO, agent("stub", "unreachable")));
    renderSidebar();
    await waitFor(() => expect(badge()).not.toBeNull());
    expect(badge()?.textContent).toBe("11 agent cannot be used");
  });

  it("carries no badge when every agent is reachable or has not been tried", async () => {
    const fetchStub = stubFetch(list(HELLO, agent("later", "not_tried")));
    renderSidebar();
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(badge()).toBeNull();
    expect(agentsLink().getAttribute("aria-describedby")).toBeNull();
    expect(document.body.textContent).not.toContain("cannot be used");
  });

  it("carries no badge when the list cannot be read", async () => {
    const fetchStub = stubFetch({ fails: "Failed to fetch" });
    renderSidebar();
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(badge()).toBeNull();
    // And says nothing about it: the section itself says the service cannot be reached.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.body.textContent).not.toContain("cannot be");
  });

  it("drops the badge once a later reading fails: an earlier number is not shown as current", async () => {
    const fetchStub = stubFetch(list(agent("stub", "unreachable")), {
      status: 500,
      json: {
        detail: {
          code: "internal",
          message: "The saved agents could not be read.",
        },
      },
    });
    renderSidebar();
    await waitFor(() => expect(badge()).not.toBeNull());

    fireEvent.click(
      screen.getByRole("button", { name: "Read the list again" }),
    );
    await waitFor(() => expect(badge()).toBeNull());
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("follows the list when it is read again: the badge goes once the agent is reachable", async () => {
    stubFetch(
      list(agent("stub", "unreachable"), agent("report", "unreachable")),
      list(agent("stub", "reachable"), agent("report", "unreachable")),
      list(agent("stub", "reachable"), agent("report", "reachable")),
    );
    renderSidebar();
    await waitFor(() => expect(badge()?.textContent).toContain("2 agents"));

    const again = screen.getByRole("button", { name: "Read the list again" });
    fireEvent.click(again);
    await waitFor(() => expect(badge()?.textContent).toContain("1 agent "));
    fireEvent.click(again);
    await waitFor(() => expect(badge()).toBeNull());
  });

  it("does not ask again behind the operator's back after a reading failed", async () => {
    const fetchStub = stubFetch({ fails: "Failed to fetch" });
    renderSidebar();
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("shows the badge on the LLM section and on the Agents section alike", async () => {
    for (const pathname of ["/llm", "/agents"]) {
      navigation.pathname = pathname;
      stubFetch(list(agent("stub", "unreachable")));
      const { unmount } = renderSidebar();
      await waitFor(() => expect(badge()).not.toBeNull());
      expect(described()).toBe("11 agent cannot be used");
      unmount();
    }
  });
});
