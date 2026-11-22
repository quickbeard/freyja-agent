import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";
import { type Answer, serve, summary } from "@/__tests__/tasks-testing";
import { AppSidebar } from "@/components/app-sidebar";
import { TaskList } from "@/components/tasks/task-list";
import { SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LIST_SECONDS, type ThreadList, type ThreadSummary } from "@/lib/tasks";

vi.mock("next/navigation", () => ({ usePathname: () => "/tasks" }));

function list(
  threads: ThreadSummary[],
  values: Partial<ThreadList> = {},
): Answer {
  return {
    json: {
      threads,
      needs_decision: threads.filter((t) => t.status === "needs_decision")
        .length,
      total: threads.length,
      more: false,
      ...values,
    },
  };
}

const FOUR = [
  summary({
    thread_id: "paused",
    first_message: "Tell #ops that the report is ready.",
    status: "needs_decision",
    changed_at: "2026-11-20T08:00:04Z",
  }),
  summary({
    thread_id: "running",
    first_message: "Summarize the week.",
    status: "running",
    changed_at: "2026-11-20T09:10:00Z",
  }),
  summary({
    thread_id: "failed",
    first_message: "<b>Markup</b> stays text.",
    status: "failed",
    changed_at: "2026-11-20T07:00:00Z",
  }),
  summary({
    thread_id: "no-message",
    first_message: null,
    status: "done",
    changed_at: "2026-11-19T16:00:00Z",
  }),
];

function renderList(withSidebar = false) {
  return render(
    <SWRConfig
      value={{
        provider: () => new Map(),
        dedupingInterval: 0,
        // The reading library's own wait before it reads a page shown again, five seconds
        // after the page was opened, is left as it is: the console sets it aside itself.
      }}
    >
      {withSidebar && (
        <TooltipProvider>
          <SidebarProvider>
            <AppSidebar />
          </SidebarProvider>
        </TooltipProvider>
      )}
      <TaskList />
    </SWRConfig>,
  );
}

const rows = () =>
  screen.queryAllByRole("listitem").map((item) => item.textContent ?? "");

describe("the list of tasks", () => {
  it("shows one row per task in the service's order, each opening the task", async () => {
    serve(() => list(FOUR));
    const { container } = renderList();
    await waitFor(() => expect(rows()).toHaveLength(4));

    expect(rows()[0]).toContain("Tell #ops that the report is ready.");
    expect(rows()[0]).toContain("Needs your decision");
    expect(rows()[1]).toContain("Running");
    expect(rows()[2]).toContain("Failed");
    expect(rows()[3]).toContain("Task no-message");
    expect(
      screen.getAllByRole("link").map((link) => link.getAttribute("href")),
    ).toEqual([
      "/tasks/paused",
      "/tasks/running",
      "/tasks/failed",
      "/tasks/no-message",
    ]);
    // When each last changed, as a time a machine can read too.
    expect(
      [...container.querySelectorAll("time")].map((time) =>
        time.getAttribute("datetime"),
      ),
    ).toEqual(FOUR.map((task) => task.changed_at));
    // And as a person reads it, on the row.
    expect(
      [...container.querySelectorAll("time")].every(
        (time) => (time.textContent ?? "").match(/2026/) !== null,
      ),
    ).toBe(true);
    // The beginning of a message is text, never markup.
    expect(container.querySelector("li b")).toBeNull();
    expect(rows()[2]).toContain("<b>Markup</b> stays text.");
  });

  it("gives the service's reason on a failed task's row, as text, and on no other row", async () => {
    const reason =
      "No usable model is active. <i>Activate</i> a usable LLM profile, then send the message again.";
    serve(() =>
      list([
        summary({
          thread_id: "failed",
          first_message: "Summarize the week.",
          status: "failed",
          error: { code: "no_usable_model", message: reason },
        }),
        // The service gives a reason with `failed` only; one that came with another
        // status would not be the reason of what the row says.
        summary({
          thread_id: "done",
          first_message: "Say hello.",
          status: "done",
          error: { code: "internal", message: "An earlier reason." },
        }),
        summary({
          thread_id: "bare",
          first_message: "List the files.",
          status: "failed",
          error: null,
        }),
      ]),
    );
    const { container } = renderList();
    await waitFor(() => expect(rows()).toHaveLength(3));

    expect(rows()[0]).toContain("Failed");
    expect(rows()[0]).toContain(reason);
    expect(container.querySelector("li i")).toBeNull();
    expect(rows()[1]).not.toContain("An earlier reason.");
    // A failed row the service gave no reason for says that it failed, and no more.
    expect(rows()[2]).toContain("Failed");
    // The reason is part of the row, so of what the row's link is called.
    expect(
      screen.getByRole("link", { name: /Summarize the week\./ }).textContent,
    ).toContain(reason);
  });

  it("shows a reading that took longer than the timer's period, and asks for no other meanwhile", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      // The service is slow: the first reading is answered only after two periods.
      let answer: (reply: Answer) => void = () => {};
      let asked = 0;
      serve(() => {
        asked += 1;
        return new Promise<Answer>((resolve) => {
          answer = resolve;
        });
      });
      renderList();
      await waitFor(() => expect(asked).toBe(1));

      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      // A reading asked for while one is under way would take its place, and with a
      // service this slow none would ever be shown.
      expect(asked).toBe(1);
      answer(list(FOUR));
      await waitFor(() => expect(rows()).toHaveLength(4));

      // Once it is answered, the next period reads again.
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      await waitFor(() => expect(asked).toBe(2));
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows no row once a later reading failed, and shows them again once one is answered", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let down = false;
      serve(() => (down ? { fails: "refused" } : list(FOUR)));
      renderList();
      await waitFor(() => expect(rows()).toHaveLength(4));

      // The rows the page holds are not shown as if they were current (FR-006).
      down = true;
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      expect((await screen.findByRole("alert")).textContent).toMatch(
        /cannot be reached/,
      );
      expect(rows()).toHaveLength(0);
      expect(screen.queryByRole("list", { name: "Tasks" })).toBeNull();

      down = false;
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      await waitFor(() => expect(rows()).toHaveLength(4));
      expect(screen.queryByRole("alert")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  // T113: within five seconds of the page being opened, as here, too.
  it("is read again at once when the page is shown again, by no timer", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const visibility = vi.spyOn(document, "visibilityState", "get");
    try {
      let asked = 0;
      let tasks = FOUR.slice(0, 1);
      serve(() => {
        asked += 1;
        return list(tasks);
      });
      renderList();
      await waitFor(() => expect(rows()).toHaveLength(1));

      visibility.mockReturnValue("hidden");
      document.dispatchEvent(new Event("visibilitychange"));
      tasks = FOUR;
      vi.advanceTimersByTime(3 * LIST_SECONDS * 1000);
      expect(asked).toBe(1);

      visibility.mockReturnValue("visible");
      document.dispatchEvent(new Event("visibilitychange"));
      await waitFor(() => expect(rows()).toHaveLength(4));
      expect(asked).toBe(2);
    } finally {
      visibility.mockRestore();
      vi.useRealTimers();
    }
  });

  // FR-023, T103: every listed row can be left out as unreadable. The list is then empty
  // and the service still counts tasks: "no task yet" would be untrue.
  it("does not say that there is no task when the service counts tasks and lists none", async () => {
    serve(() => list([], { total: 3, more: true }));
    renderList();
    expect(
      (await screen.findByText(/The service counts 3 tasks/)).textContent,
    ).toBe(
      "The service counts 3 tasks, and none of them can be shown just now. They are read again by themselves.",
    );
    expect(screen.queryByText(/There is no task yet/)).toBeNull();
    expect(
      screen.queryByText(/More tasks exist than are listed here/),
    ).toBeNull();
  });

  it("says one task in the singular when it cannot be shown", async () => {
    serve(() => list([], { total: 1, more: true }));
    renderList();
    expect((await screen.findByText(/cannot be shown/)).textContent).toBe(
      "The service counts 1 task, and it cannot be shown just now. It is read again by itself.",
    );
  });

  it("says so when there is no task", async () => {
    serve(() => list([]));
    renderList();
    expect(await screen.findByText(/There is no task yet/)).toBeTruthy();
  });

  it("says that more tasks exist than it lists", async () => {
    serve(() => list(FOUR.slice(0, 2), { total: 130, more: true }));
    renderList();
    expect(
      (await screen.findByText(/More tasks exist than are listed here/))
        .textContent,
    ).toBe(
      "More tasks exist than are listed here: 130 in all. Every task that needs a decision is listed, then the most recently changed of the others.",
    );
  });

  it("says the service cannot be reached, shows no row, and reads again on request", async () => {
    let down = true;
    serve(() => (down ? { fails: "refused" } : list(FOUR)));
    renderList();
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /cannot be reached/,
    );
    expect(rows()).toEqual([]);

    down = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(rows()).toHaveLength(4));
  });

  it("is read again by its timer, also after a reading failed", async () => {
    let reads = 0;
    serve(() => {
      reads += 1;
      if (reads === 2) {
        return { fails: "refused" };
      }
      return list(reads === 1 ? FOUR.slice(0, 1) : FOUR);
    });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderList();
      await waitFor(() => expect(rows()).toHaveLength(1));
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      await waitFor(() => expect(rows()).toHaveLength(4));
    } finally {
      vi.useRealTimers();
    }
  });

  it("is one reading with the sidebar's number", async () => {
    const asked = serve((_method, path) =>
      path === "/threads"
        ? list(FOUR)
        : { json: { agents: [], limit: 20, unusable: 0 } },
    );
    renderList(true);
    await waitFor(() =>
      expect(
        screen
          .getByRole("list", { name: "Tasks" })
          .querySelectorAll(":scope > li"),
      ).toHaveLength(4),
    );
    expect(
      screen
        .getByRole("link", { name: "Tasks" })
        .getAttribute("aria-describedby"),
    ).not.toBeNull();
    expect(asked.filter(({ path }) => path === "/threads")).toHaveLength(1);
  });
});

describe("the list of tasks, further", () => {
  it("opens a task whose id another client chose, whatever its characters", async () => {
    serve(() =>
      list([summary({ thread_id: "a.b_c-1", first_message: "From curl." })]),
    );
    renderList();
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("link").getAttribute("href")).toBe(
      "/tasks/a.b_c-1",
    );
  });

  it("puts an id that is no path segment into the address as one", async () => {
    serve(() => list([summary({ thread_id: "a/b?c", first_message: "Odd." })]));
    renderList();
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("link").getAttribute("href")).toBe(
      "/tasks/a%2Fb%3Fc",
    );
  });

  it("stops reading the list once it is no longer shown", async () => {
    let reads = 0;
    serve(() => {
      reads += 1;
      return list([]);
    });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const shown = renderList();
      await waitFor(() => expect(reads).toBe(1));
      shown.unmount();
      vi.advanceTimersByTime(LIST_SECONDS * 3000);
      expect(reads).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads the list once a period with the sidebar beside it", async () => {
    const asked = serve((_method, path) =>
      path === "/threads"
        ? list(FOUR)
        : { json: { agents: [], limit: 20, unusable: 0 } },
    );
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderList(true);
      const lists = () =>
        asked.filter(({ path }) => path === "/threads").length;
      await waitFor(() => expect(lists()).toBe(1));
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      await waitFor(() => expect(lists()).toBe(2));
      vi.advanceTimersByTime(LIST_SECONDS * 1000);
      await waitFor(() => expect(lists()).toBe(3));
    } finally {
      vi.useRealTimers();
    }
  });
});
