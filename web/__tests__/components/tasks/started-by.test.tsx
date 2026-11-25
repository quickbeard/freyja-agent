import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StartedBy, startedBy } from "@/components/tasks/started-by";

describe("who started a task", () => {
  it("says an agent on this machine for the local caller", () => {
    render(
      <StartedBy
        caller={{ name: "local", canceled: false, configured: true }}
      />,
    );
    expect(
      screen.getByText("Started by an agent on this machine"),
    ).toBeTruthy();
  });

  it("names a named caller, as text", () => {
    const { container } = render(
      <StartedBy
        caller={{ name: "<b>artex</b>", canceled: false, configured: true }}
      />,
    );
    expect(container.textContent).toBe("Started by agent <b>artex</b>");
    expect(container.querySelector("b")).toBeNull();
  });

  it("says nothing of a task a person started", () => {
    const { container } = render(<StartedBy caller={null} />);
    expect(container.innerHTML).toBe("");
    expect(startedBy(null)).toBeNull();
  });
});
