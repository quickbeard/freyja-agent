import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CheckResult } from "@/components/agents/check-result";
import type { AgentCheck } from "@/lib/agents";

const USABLE: AgentCheck = {
  usable: true,
  reason: null,
  message: null,
  checked_at: "2026-11-19T10:00:00Z",
  recorded: true,
  card_changed: false,
};

function check(changes: Partial<AgentCheck>): AgentCheck {
  return { ...USABLE, ...changes };
}

// The time as the result writes it, in whatever zone the test runs in.
function when(time: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(time));
}

const said = () => document.body.textContent ?? "";

describe("CheckResult", () => {
  it("says the agent can be used, and when the service said so", () => {
    render(<CheckResult check={USABLE} />);
    expect(said()).toContain("The check found that this agent can be used");
    expect(said()).toContain(when("2026-11-19T10:00:00Z"));
    expect(document.querySelector("time")?.getAttribute("dateTime")).toBe(
      "2026-11-19T10:00:00Z",
    );
    // Nothing more is said of a check that changed nothing and was recorded.
    expect(said()).not.toMatch(/not recorded|card changed|cannot/i);
  });

  it("says the agent cannot be used and why, in the service's sentence", () => {
    render(
      <CheckResult
        check={check({
          usable: false,
          reason: "no_answer",
          message: "Nothing answered at the address.",
        })}
      />,
    );
    expect(said()).toContain("The check found that this agent cannot be used");
    expect(screen.getByText("Nothing answered at the address.")).toBeTruthy();
    expect(said()).not.toContain("can be used");
  });

  it("shows the service's sentence as text, whatever it holds", () => {
    const message = '<img src="x" onerror="alert(1)"> see http://x.example/';
    render(
      <CheckResult
        check={check({ usable: false, reason: "unreadable", message })}
      />,
    );
    expect(screen.getByText(message)).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("a")).toBeNull();
  });

  it("says a result was not recorded when the agent was moved or removed meanwhile", () => {
    render(<CheckResult check={check({ recorded: false })} />);
    expect(said()).toContain("The check found that this agent can be used");
    expect(said()).toContain(
      "This result was not recorded: the agent was moved or removed while it was checked.",
    );
  });

  it("says the agent's card changed, and from when its tools follow", () => {
    render(<CheckResult check={check({ card_changed: true })} />);
    expect(said()).toContain("The agent's card changed.");
    expect(said()).toContain(
      "Its tools are described from the new details from the next run.",
    );
  });

  it("says neither when the card is as it was and the result was recorded", () => {
    render(
      <CheckResult
        check={check({
          usable: false,
          reason: "timeout",
          message: "The agent did not answer within 5 seconds.",
        })}
      />,
    );
    expect(said()).not.toMatch(/not recorded|card changed/i);
  });
});
