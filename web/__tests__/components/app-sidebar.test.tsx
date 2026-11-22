import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig, useSWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch as stubAll } from "@/__tests__/testing";
import { AppSidebar } from "@/components/app-sidebar";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AGENTS_KEY, type Agent, type AgentList } from "@/lib/agents";
import { LIST_SECONDS, type ThreadList } from "@/lib/tasks";

// Feature 007: the sidebar also reads the list of tasks, wherever it is shown. Its requests
// are answered here, from `threadReplies` in order, the last one again and again, and they
// are kept apart from the requests for the list of agents, which each test counts.
const NO_TASKS: ThreadList = {
  threads: [],
  needs_decision: 0,
  total: 0,
  more: false,
};
let threadReplies: Reply[] = [{ json: NO_TASKS }];
let threadAsks = 0;

function answer(reply: Reply): Response {
  if ("fails" in reply) {
    throw new TypeError(reply.fails);
  }
  return "json" in reply
    ? new Response(JSON.stringify(reply.json), {
        status: reply.status ?? 200,
        headers: { "content-type": "application/json" },
      })
    : new Response(reply.text || null, { status: reply.status });
}

/** The shared stub for the list of agents, with the list of tasks answered beside it. */
function stubFetch(...replies: Reply[]) {
  const agents = stubAll(...replies);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/threads") {
        const reply =
          threadReplies[Math.min(threadAsks, threadReplies.length - 1)];
        threadAsks += 1;
        return answer(reply);
      }
      return agents(input, init);
    }),
  );
  return agents;
}

function waiting(count: number): Reply {
  const json: ThreadList = { ...NO_TASKS, needs_decision: count, total: count };
  return { json };
}

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
    threadReplies = [{ json: NO_TASKS }];
    threadAsks = 0;
    // The sidebar asks for the list of agents wherever it is shown.
    stubFetch(list(HELLO));
  });

  it("lists the console's sections as links", () => {
    renderSidebar();
    const links = screen.getAllByRole("link");
    // Feature 007 adds the Tasks section (its FR-027 names this change).
    expect(links.map((link) => link.textContent)).toEqual([
      "LLM",
      "Agents",
      "Tasks",
    ]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/llm",
      "/agents",
      "/tasks",
    ]);
  });

  it.each([
    "/tasks",
    "/tasks/5f1c0a9e",
  ])("marks Tasks as the section in use on %s", (path) => {
    navigation.pathname = path;
    renderSidebar();
    const current = screen
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page")
      .map((link) => link.textContent);
    expect(current).toEqual(["Tasks"]);
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
    // The eye is told in words what the number is about, not by its colour alone; the
    // entry's description says it in a sentence for whoever cannot see where it stands.
    expect(badge()?.querySelector('[aria-hidden="true"]')?.textContent).toBe(
      "2 cannot be used",
    );
    expect(described()).toBe("2 agents cannot be used");
    // The entry is still named Agents, and still leads to the section.
    expect(agentsLink().getAttribute("href")).toBe("/agents");
    expect(screen.getByRole("link", { name: "LLM" })).toBeTruthy();
  });

  it("says one agent in the singular", async () => {
    stubFetch(list(HELLO, agent("stub", "unreachable")));
    renderSidebar();
    await waitFor(() => expect(badge()).not.toBeNull());
    expect(badge()?.querySelector('[aria-hidden="true"]')?.textContent).toBe(
      "1 cannot be used",
    );
    expect(described()).toBe("1 agent cannot be used");
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
      expect(described()).toBe("1 agent cannot be used");
      unmount();
    }
  });
});

// Feature 007: the number of tasks that need a decision, on every section.

describe("AppSidebar, the Tasks entry", () => {
  const tasksLink = () => screen.getByRole("link", { name: "Tasks" });
  const tasksBadge = () =>
    tasksLink().parentElement?.querySelector(
      '[data-slot="sidebar-menu-badge"]',
    ) ?? null;
  const NOT_KNOWN =
    "How many tasks need a decision is not known: the list of tasks could not be read.";
  const tasksSaid = () => {
    const id = tasksLink().getAttribute("aria-describedby");
    return id === null
      ? null
      : (document.getElementById(id)?.textContent ?? null);
  };

  beforeEach(() => {
    navigation.pathname = "/llm";
    threadAsks = 0;
    stubAll(list(HELLO));
  });

  it("says how many tasks need a decision, in words as well as in a number", async () => {
    threadReplies = [waiting(3)];
    stubFetch(list(HELLO));
    renderSidebar();
    await waitFor(() => expect(tasksBadge()?.textContent).toContain("3"));
    expect(tasksBadge()?.textContent).toContain("3 to decide");
    expect(tasksSaid()).toBe("3 tasks need a decision");
  });

  it("says one task in the singular", async () => {
    threadReplies = [waiting(1)];
    stubFetch(list(HELLO));
    renderSidebar();
    await waitFor(() => expect(tasksSaid()).toBe("1 task needs a decision"));
  });

  it("shows no number before the first answer and for none, and says that it is not known after a failed reading", async () => {
    let answer: (reply: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input) === "/api/threads"
          ? new Promise<Response>((resolve) => {
              answer = resolve;
            })
          : new Response(
              JSON.stringify({ agents: [], limit: 20, unusable: 0 }),
            ),
      ),
    );
    const first = renderSidebar();
    // Before the first answer.
    expect(tasksBadge()).toBeNull();
    expect(tasksLink().getAttribute("aria-describedby")).toBeNull();
    answer(new Response(JSON.stringify(NO_TASKS)));
    first.unmount();

    threadReplies = [waiting(0)];
    threadAsks = 0;
    stubFetch(list(HELLO));
    const none = renderSidebar();
    await waitFor(() => expect(threadAsks).toBe(1));
    expect(tasksBadge()).toBeNull();
    expect(tasksLink().getAttribute("aria-describedby")).toBeNull();
    none.unmount();

    // A list that could not be read is not "no task needs a decision": the entry says, in
    // words, that the number is not known (Edge Cases, "The list cannot be kept or read").
    threadReplies = [{ fails: "Failed to fetch" } as Reply];
    threadAsks = 0;
    stubFetch(list(HELLO));
    renderSidebar();
    await waitFor(() => expect(tasksSaid()).toBe(NOT_KNOWN));
    expect(tasksBadge()?.textContent).toContain("not known");
    expect(tasksBadge()?.textContent).not.toMatch(/\d/);
  });

  it("drops the number once a later reading fails, and shows the next one", async () => {
    threadReplies = [waiting(2), { fails: "Failed to fetch" }, waiting(1)];
    stubFetch(list(HELLO));
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderSidebar();
      await waitFor(() => expect(tasksSaid()).toBe("2 tasks need a decision"));
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      // The earlier number is not shown as if it were current.
      await waitFor(() => expect(tasksSaid()).toBe(NOT_KNOWN));
      expect(tasksBadge()?.textContent).not.toMatch(/\d/);
      // The reading that failed did not end the following.
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      await waitFor(() => expect(tasksSaid()).toBe("1 task needs a decision"));
    } finally {
      vi.useRealTimers();
    }
  });

  // T113: the reading library waits five seconds after a page was opened before it reads
  // on the page being shown; the console sets that wait aside.
  it("reads the list again at once when the page is shown, also within five seconds of it being opened", async () => {
    threadReplies = [waiting(0), waiting(2)];
    stubFetch(list(HELLO));
    renderSidebar();
    await waitFor(() => expect(threadAsks).toBe(1));
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(tasksSaid()).toBe("2 tasks need a decision"));
    expect(threadAsks).toBe(2);
  });

  it("reads the list again by itself, by its timer, on another section", async () => {
    threadReplies = [waiting(0), waiting(1)];
    stubFetch(list(HELLO));
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderSidebar();
      await waitFor(() => expect(threadAsks).toBe(1));
      vi.advanceTimersByTime(LIST_SECONDS * 1000 - 1);
      expect(threadAsks).toBe(1);
      vi.advanceTimersByTime(1);
      await waitFor(() => expect(tasksSaid()).toBe("1 task needs a decision"));
      expect(threadAsks).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
