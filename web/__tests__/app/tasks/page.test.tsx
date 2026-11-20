import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TasksPage from "@/app/tasks/page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

describe("the Tasks section", () => {
  it("has its heading, the notice that the console has no users, and New task", () => {
    render(<TasksPage />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Tasks" }),
    ).toBeTruthy();
    expect(screen.getByRole("note").textContent).toBe(
      "The console has no users: everyone who can open it sees every task and can decide every proposal, and nothing records who decided.",
    );
    expect(screen.getByRole("button", { name: "New task" })).toBeTruthy();
  });
});
