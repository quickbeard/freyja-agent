import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AgentsPage from "./page";

describe("the Agents section", () => {
  it("says agents are not managed from the console yet", () => {
    render(<AgentsPage />);
    expect(screen.getByRole("heading", { name: "Agents" })).toBeTruthy();
    expect(screen.getByText(/not managed from the console yet/i)).toBeTruthy();
  });

  it("offers no control of its own", () => {
    render(<AgentsPage />);
    for (const role of ["button", "link", "textbox", "combobox", "switch"]) {
      expect(screen.queryAllByRole(role)).toEqual([]);
    }
  });
});
