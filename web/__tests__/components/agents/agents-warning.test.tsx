import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentsWarning } from "@/components/agents/agents-warning";
import type { Agent } from "@/lib/agents";

const HELLO: Agent = {
  id: "env.hello",
  name: "hello",
  source: "environment",
  approval: "none",
  url: null,
  version: null,
  state: "unreachable",
  checked_at: "2026-11-19T09:00:00Z",
  reason: "Nothing answered at the address.",
  card: null,
  pending_tasks: 0,
  created_at: null,
  updated_at: null,
};

function agent(changes: Partial<Agent>): Agent {
  return { ...HELLO, ...changes };
}

const warning = () =>
  screen.queryByRole("note", { name: "Agents that cannot be used" });

describe("AgentsWarning", () => {
  it("names the one agent that cannot be used, and says so in words", () => {
    render(<AgentsWarning agents={[HELLO]} />);
    expect(warning()?.textContent).toBe(
      "Warning: 1 agent cannot be used: hello. Its card says why.",
    );
  });

  it("names each agent that cannot be used, unreachable or not in use, in the list's order", () => {
    render(
      <AgentsWarning
        agents={[
          HELLO,
          agent({ id: "a".repeat(32), name: "stub", state: "not_in_use" }),
          agent({ id: "env.report", name: "report" }),
        ]}
      />,
    );
    expect(warning()?.textContent).toBe(
      "Warning: 3 agents cannot be used: hello, stub, report. Each one's card says why.",
    );
  });

  it("shows nothing when no agent is handed to it", () => {
    const { container } = render(<AgentsWarning agents={[]} />);
    expect(warning()).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("shows a name as text, whatever it holds", () => {
    render(<AgentsWarning agents={[agent({ name: "<b>x</b>" })]} />);
    expect(warning()?.textContent).toContain("<b>x</b>");
    expect(document.querySelector("b")).toBeNull();
  });
});
