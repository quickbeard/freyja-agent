import { render, screen } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";
import { serve, summary } from "@/__tests__/tasks-testing";
import TasksPage from "@/app/tasks/page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

describe("the Tasks section", () => {
  it("has its heading, the notice that the console has no users, New task, and the list under the notice", async () => {
    serve(() => ({
      json: {
        threads: [summary()],
        needs_decision: 0,
        total: 1,
        more: false,
      },
    }));
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <TasksPage />
      </SWRConfig>,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Tasks" }),
    ).toBeTruthy();
    const note = screen.getByRole("note");
    expect(note.textContent).toBe(
      "The console has no users: everyone who can open it sees every task and can decide every proposal, and nothing records who decided.",
    );
    expect(screen.getByRole("button", { name: "New task" })).toBeTruthy();

    const list = await screen.findByRole("list", { name: "Tasks" });
    // The list stands under the notice (FR-026a).
    expect(
      note.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(list.textContent).toContain("Summarize the week.");
  });
});
