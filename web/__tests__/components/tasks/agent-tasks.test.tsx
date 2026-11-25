import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { agentTask } from "@/__tests__/tasks-testing";
import {
  AGENT_TASK_STATUS,
  AgentTasks,
  awaited,
} from "@/components/tasks/agent-tasks";
import { exactTime } from "@/lib/ago";
import type { AgentTask } from "@/lib/tasks";

// 007, User Story 4: what Freyja handed to delegate agents for a task (FR-009).

const items = () =>
  within(screen.getByRole("list", { name: "Agent tasks" })).getAllByRole(
    "listitem",
  );

describe("a task's agent tasks", () => {
  it("lists each with its agent, its status in words, and the message it was started with", () => {
    render(
      <AgentTasks
        tasks={[
          agentTask({
            agent: "research",
            task_id: "a-1",
            message: "Find the report.",
            status: "completed",
            ended_at: "2026-11-20T08:01:00Z",
            outcome_delivered: true,
          }),
          agentTask({
            agent: "stub",
            task_id: "a-2",
            message: "slow 40",
            status: "working",
          }),
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Agent tasks" })).toBeTruthy();
    const [first, second] = items();
    expect(first.textContent).toContain("research");
    expect(first.textContent).toContain("Completed");
    expect(first.textContent).toContain("Find the report.");
    expect(second.textContent).toContain("stub");
    expect(second.textContent).toContain("The agent is working");
    expect(second.textContent).toContain("slow 40");
  });

  it("marks the pending ones, in words", () => {
    render(
      <AgentTasks
        tasks={[
          agentTask({ task_id: "a-1", status: "working" }),
          agentTask({ task_id: "a-2", status: "input_required" }),
          agentTask({
            task_id: "a-3",
            status: "failed",
            ended_at: "2026-11-20T08:01:00Z",
            outcome_delivered: true,
          }),
        ]}
      />,
    );
    const [working, asking, failed] = items();
    expect(within(working).getByText("Pending")).toBeTruthy();
    expect(within(asking).getByText("Pending")).toBeTruthy();
    expect(within(failed).queryByText("Pending")).toBeNull();
    expect(working.getAttribute("data-pending")).toBe("true");
    expect(failed.getAttribute("data-pending")).toBe("false");
    expect(screen.getByText("2 agent tasks are pending.")).toBeTruthy();
  });

  it("says one pending agent task in the singular, and none when all ended", () => {
    const { rerender } = render(
      <AgentTasks tasks={[agentTask({ status: "working" })]} />,
    );
    expect(screen.getByText("1 agent task is pending.")).toBeTruthy();
    rerender(
      <AgentTasks
        tasks={[
          agentTask({
            status: "completed",
            ended_at: "2026-11-20T08:01:00Z",
            outcome_delivered: true,
          }),
        ]}
      />,
    );
    expect(screen.queryByText(/pending/i)).toBeNull();
  });

  it("has words for every status the service has", () => {
    const statuses: AgentTask["status"][] = [
      "working",
      "input_required",
      "completed",
      "failed",
      "rejected",
      "canceled",
      "given_up",
    ];
    expect(Object.keys(AGENT_TASK_STATUS).sort()).toEqual([...statuses].sort());
    for (const status of statuses) {
      const { unmount } = render(
        <AgentTasks
          tasks={[
            agentTask({
              status,
              outcome_delivered: true,
              ended_at:
                status === "working" || status === "input_required"
                  ? null
                  : "2026-11-20T08:01:00Z",
            }),
          ]}
        />,
      );
      expect(items()[0].textContent).toContain(AGENT_TASK_STATUS[status]);
      unmount();
    }
  });

  it("says when an ended agent task's outcome is not in the conversation yet", () => {
    render(
      <AgentTasks
        tasks={[
          agentTask({
            status: "completed",
            ended_at: "2026-11-20T08:01:00Z",
            outcome_delivered: false,
          }),
        ]}
      />,
    );
    expect(items()[0].textContent).toContain(
      "Its outcome is not in the conversation yet.",
    );
  });

  it("does not say so of an agent task that is still pending", () => {
    render(
      <AgentTasks
        tasks={[agentTask({ status: "working", outcome_delivered: false })]}
      />,
    );
    expect(items()[0].textContent).not.toContain("outcome");
  });

  it("shows an agent's name and a message as text, never as markup", () => {
    const markup = '<b>bold</b> <a href="https://example.test/">click</a>';
    render(
      <AgentTasks
        tasks={[agentTask({ agent: "<i>stub</i>", message: markup })]}
      />,
    );
    const [item] = items();
    expect(item.textContent).toContain(markup);
    expect(item.textContent).toContain("<i>stub</i>");
    expect(item.querySelectorAll("b, a, i")).toHaveLength(0);
  });

  it('says "agent task", never "task" alone', () => {
    const { container } = render(
      <AgentTasks
        tasks={[
          agentTask({ agent: "stub", message: "slow 40", status: "working" }),
          agentTask({
            agent: "stub",
            task_id: "a-2",
            message: "slow 1",
            status: "given_up",
            ended_at: "2026-11-20T08:01:00Z",
            outcome_delivered: false,
          }),
        ]}
      />,
    );
    const text = container.textContent ?? "";
    const every = text.match(/tasks?/gi) ?? [];
    const named = text.match(/agent tasks?/gi) ?? [];
    expect(every.length).toBeGreaterThan(0);
    expect(named).toHaveLength(every.length);
  });

  it("shows nothing for a task that handed nothing to an agent", () => {
    const { container } = render(<AgentTasks tasks={[]} />);
    expect(container.textContent).toBe("");
  });
});

describe("whether a task still waits for an agent", () => {
  it("is so while an agent task is pending or its outcome is not in the conversation", () => {
    expect(awaited(agentTask({ status: "working" }))).toBe(true);
    expect(
      awaited(agentTask({ status: "input_required", outcome_delivered: true })),
    ).toBe(true);
    expect(
      awaited(
        agentTask({
          status: "completed",
          ended_at: "2026-11-20T08:01:00Z",
          outcome_delivered: false,
        }),
      ),
    ).toBe(true);
    expect(
      awaited(
        agentTask({
          status: "completed",
          ended_at: "2026-11-20T08:01:00Z",
          outcome_delivered: true,
        }),
      ),
    ).toBe(false);
  });
});

// Feature 008, User Story 2: what a working agent's progress shows (FR-010, FR-012, R8).

const LEARNED = "2026-11-25T08:00:00Z";
const progress = (text: string) => ({ text, learned_at: LEARNED });
const region = (agent = "stub") =>
  screen.getByRole("region", { name: `Progress reported by ${agent}` });

describe("an agent task's progress", () => {
  it("shows what a working agent last reported, how long ago, and the exact time", () => {
    render(
      <AgentTasks
        tasks={[agentTask({ progress: progress("Step 2 of 6") })]}
        now={Date.parse(LEARNED) + 150_000}
      />,
    );

    const [item] = items();
    expect(item.textContent).toContain(
      "Last reported by the agent 2 minutes ago:",
    );
    const time = item.querySelector("time");
    expect(time?.textContent).toBe("2 minutes ago");
    expect(time?.getAttribute("dateTime")).toBe(LEARNED);
    expect(time?.getAttribute("title")).toBe(exactTime(LEARNED));
    expect(region().textContent).toBe("Step 2 of 6");
    // The text comes after the line that says what it is.
    expect(
      item.textContent?.indexOf("Last reported by the agent") ?? -1,
    ).toBeLessThan(item.textContent?.indexOf("Step 2 of 6") ?? -1);
  });

  it("shows the text without its age until the page has a time to compare with", () => {
    render(
      <AgentTasks tasks={[agentTask({ progress: progress("Half done") })]} />,
    );

    const [item] = items();
    expect(item.textContent).toContain("Last reported by the agent:");
    expect(item.querySelector("time")).toBeNull();
    expect(region().textContent).toBe("Half done");
  });

  it("shows a working agent task with no progress exactly as before", () => {
    render(
      <AgentTasks tasks={[agentTask({ progress: null })]} now={Date.now()} />,
    );

    // What feature 007 shows: the agent, the mark, the status, and the message, and no more.
    const [item] = items();
    expect(Array.from(item.children, (child) => child.tagName)).toEqual([
      "P",
      "P",
      "P",
    ]);
    expect(item.textContent).toBe(
      "stubPendingThe agent is workingStarted with:slow 40",
    );
    expect(screen.queryAllByText(/Last reported/)).toEqual([]);
    expect(
      screen.queryAllByRole("region", { name: /^Progress reported by/ }),
    ).toEqual([]);
  });

  it.each([
    "input_required",
    "completed",
    "failed",
    "rejected",
    "canceled",
    "given_up",
  ] as const)("shows no progress for an agent task that is %s, whatever it holds", (status) => {
    render(
      <AgentTasks
        tasks={[
          agentTask({
            status,
            ended_at:
              status === "input_required" ? null : "2026-11-25T08:05:00Z",
            progress: progress("Step 5 of 6"),
          }),
        ]}
        now={Date.parse(LEARNED) + 60_000}
      />,
    );

    const [item] = items();
    expect(item.textContent).not.toContain("Step 5 of 6");
    expect(item.textContent).not.toContain("Last reported");
    expect(
      screen.queryAllByRole("region", { name: /^Progress reported by/ }),
    ).toEqual([]);
  });

  it("shows a text that holds markup and a link as those characters", () => {
    const text =
      '<b>bold</b><script>alert(1)</script> see <a href="https://example.com">here</a>';
    render(
      <AgentTasks
        tasks={[agentTask({ progress: progress(text) })]}
        now={Date.now()}
      />,
    );

    const box = region();
    expect(box.textContent).toBe(text);
    expect(box.querySelector("b, script, a")).toBeNull();
    expect(box.children).toHaveLength(0);
  });

  it("keeps the line breaks of a text of several lines", () => {
    render(
      <AgentTasks
        tasks={[
          agentTask({ progress: progress("Step 2 of 3\n\nCharts next") }),
        ]}
        now={Date.now()}
      />,
    );

    const box = region();
    expect(box.textContent).toBe("Step 2 of 3\n\nCharts next");
    expect(box.className).toContain("whitespace-pre-wrap");
    // A word longer than the box, such as a long address, is broken too.
    expect(box.className).toContain("wrap-anywhere");
  });

  it("shows the longest text whole, in a box that scrolls inside itself", () => {
    const text = "0123456789".repeat(200);
    render(
      <AgentTasks
        tasks={[agentTask({ progress: progress(text) })]}
        now={Date.now()}
      />,
    );

    const box = region();
    expect(box.textContent).toBe(text);
    expect(box.className).toMatch(/max-h-/);
    expect(box.className).toContain("overflow-y-auto");
  });

  it("is described by the line that says how long ago its text was learned", () => {
    render(
      <AgentTasks
        tasks={[agentTask({ progress: progress("Half done") })]}
        now={Date.parse(LEARNED) + 150_000}
      />,
    );

    const described = region().getAttribute("aria-describedby");
    expect(described).toBeTruthy();
    expect(document.getElementById(described ?? "")?.textContent).toBe(
      "Last reported by the agent 2 minutes ago:",
    );
  });

  it("can be reached by keyboard, is named, takes its direction from its text, and is no live region", () => {
    render(
      <AgentTasks
        tasks={[
          agentTask({
            agent: "research",
            progress: progress("שלב 2 מתוך 6"),
          }),
        ]}
        now={Date.now()}
      />,
    );

    const box = region("research");
    expect(box.tabIndex).toBe(0);
    expect(box.getAttribute("dir")).toBe("auto");
    expect(box.getAttribute("aria-live")).toBeNull();
    expect(box.closest("[aria-live]")).toBeNull();
    expect(box.querySelector("[aria-live]")).toBeNull();
  });
});
