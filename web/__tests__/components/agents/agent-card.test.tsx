import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentCard } from "@/components/agents/agent-card";
import type { Agent } from "@/lib/agents";

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
  card: {
    name: "Hello World Agent",
    description: "Just a hello world agent",
    version: "1.0.0",
    skills: [
      {
        id: "hello_world",
        name: "Returns hello world",
        description: "just returns hello world",
        tags: ["hello world", "greeting"],
      },
    ],
    cut: false,
  },
  pending_tasks: 0,
  created_at: null,
  updated_at: null,
};

const STUB: Agent = {
  ...HELLO,
  id: "7c9e6679f0a54b3e8d2a1f4b6c8e0a2d",
  name: "stub",
  source: "saved",
  approval: "required",
  url: "http://127.0.0.1:9998",
  version: 2,
  created_at: "2026-11-19T08:50:00Z",
  updated_at: "2026-11-19T08:58:00Z",
};

function agent(changes: Partial<Agent>): Agent {
  return { ...STUB, ...changes };
}

// The time as the card writes it, in whatever zone the test runs in.
function when(time: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(time));
}

describe("AgentCard", () => {
  it("shows the agent's name and its approval choice in words", () => {
    const { unmount } = render(<AgentCard agent={STUB} />);
    expect(screen.getByRole("heading", { name: "stub" })).toBeTruthy();
    expect(screen.getByText("Requires approval")).toBeTruthy();
    unmount();

    render(<AgentCard agent={HELLO} />);
    expect(screen.getByRole("heading", { name: "hello" })).toBeTruthy();
    expect(screen.getByText("Runs without approval")).toBeTruthy();
    expect(screen.queryByText("Requires approval")).toBeNull();
  });

  it("shows its state with the time of the most recent attempt", () => {
    render(<AgentCard agent={HELLO} />);
    const state = screen.getByText(/^Reachable/).closest("p");
    expect(state?.textContent).toContain(when("2026-11-19T09:00:00Z"));
    expect(state?.querySelector("time")?.getAttribute("dateTime")).toBe(
      "2026-11-19T09:00:00Z",
    );
    expect(screen.queryByText(/Warning/)).toBeNull();
  });

  it("marks an environment agent as such and shows no address", () => {
    render(<AgentCard agent={HELLO} />);
    expect(screen.getByText("From the environment")).toBeTruthy();
    expect(screen.queryByText("Address")).toBeNull();
    expect(document.body.textContent).not.toContain("127.0.0.1");
  });

  it("shows a saved agent's address, and no mark of the environment", () => {
    render(<AgentCard agent={STUB} />);
    expect(screen.getByText("Address")).toBeTruthy();
    expect(screen.getByText("http://127.0.0.1:9998")).toBeTruthy();
    expect(screen.queryByText("From the environment")).toBeNull();
  });

  it("warns in words about an agent that is unreachable, with the reason and the time", () => {
    render(
      <AgentCard
        agent={agent({
          state: "unreachable",
          checked_at: "2026-11-19T09:04:10Z",
          reason: "Nothing answered at the address.",
        })}
      />,
    );
    const warning = screen.getByRole("note", { name: "Warning" });
    expect(warning.textContent).toContain("Warning");
    expect(warning.textContent).toContain("This agent cannot be reached");
    expect(warning.textContent).toContain("Nothing answered at the address.");
    expect(warning.textContent).toContain(when("2026-11-19T09:04:10Z"));
    expect(screen.getByText("Unreachable")).toBeTruthy();
  });

  it("warns in words about a saved agent that is not in use, with the reason", () => {
    render(
      <AgentCard
        agent={agent({
          state: "not_in_use",
          checked_at: null,
          reason: "An agent named in the environment has this name.",
        })}
      />,
    );
    const warning = screen.getByRole("note", { name: "Warning" });
    expect(warning.textContent).toContain("This agent is not in use");
    expect(warning.textContent).toContain(
      "An agent named in the environment has this name.",
    );
    expect(screen.getByText("Not in use")).toBeTruthy();
    // Nothing tried it, so there is no time to show.
    expect(warning.querySelector("time")).toBeNull();
  });

  it("says an agent has not been tried yet, without a warning", () => {
    render(
      <AgentCard
        agent={agent({ state: "not_tried", checked_at: null, reason: null })}
      />,
    );
    expect(
      screen.getByText("This instance has not tried the agent yet."),
    ).toBeTruthy();
    expect(screen.queryByRole("note", { name: "Warning" })).toBeNull();
    expect(screen.queryByText(/Warning/)).toBeNull();
  });

  it("shows what the card says as the agent's own claim", () => {
    render(<AgentCard agent={HELLO} />);
    const claim = screen.getByRole("group", {
      name: "What the agent says of itself",
    });
    const said = within(claim);
    expect(said.getByText("Hello World Agent")).toBeTruthy();
    expect(said.getByText("Just a hello world agent")).toBeTruthy();
    expect(said.getByText("1.0.0")).toBeTruthy();
    expect(said.getByText("Returns hello world")).toBeTruthy();
    expect(said.getByText("just returns hello world")).toBeTruthy();
    expect(said.getByText("hello world, greeting")).toBeTruthy();
    // The approval choice is the operator's, and stands apart from the claim.
    expect(said.queryByText("Runs without approval")).toBeNull();
    expect(screen.queryByText(/was cut/)).toBeNull();
  });

  it("says when the card's details were cut", () => {
    render(
      <AgentCard
        agent={agent({
          card: { ...(STUB.card as NonNullable<Agent["card"]>), cut: true },
        })}
      />,
    );
    expect(screen.getByText(/was cut/)).toBeTruthy();
  });

  it("shows markup and links in a card as the text they are", () => {
    const markup = '<img src=x onerror="window.pwned = true">';
    const link = "[x](http://example.com)";
    const script = "<script>window.pwned = true</script>";
    render(
      <AgentCard
        agent={agent({
          state: "unreachable",
          reason: `<a href="http://example.com">why</a>`,
          card: {
            name: markup,
            description: link,
            version: script,
            skills: [
              {
                id: "s",
                name: "<b>bold</b>",
                description: "http://example.com/clickme",
                tags: ["<i>tag</i>"],
              },
            ],
            cut: false,
          },
        })}
      />,
    );
    // Each is on the page, character for character.
    expect(screen.getByText(markup)).toBeTruthy();
    expect(screen.getByText(link)).toBeTruthy();
    expect(screen.getByText(script)).toBeTruthy();
    expect(screen.getByText("<b>bold</b>")).toBeTruthy();
    expect(screen.getByText("http://example.com/clickme")).toBeTruthy();
    expect(screen.getByText("<i>tag</i>")).toBeTruthy();
    expect(document.body.textContent).toContain(
      '<a href="http://example.com">why</a>',
    );
    // And nothing was made of them: no image, no link, no script, no bold or italic.
    for (const element of ["img", "a", "script", "b", "i", "iframe"]) {
      expect(document.body.querySelector(element)).toBeNull();
    }
    expect(screen.queryAllByRole("link")).toEqual([]);
    expect((window as { pwned?: boolean }).pwned).toBeUndefined();
  });

  it("says an environment agent's card has not been read when there is none", () => {
    render(
      <AgentCard
        agent={{
          ...HELLO,
          state: "unreachable",
          reason: "Nothing answered at the address.",
          card: null,
        }}
      />,
    );
    expect(screen.getByText("Its card has not been read yet.")).toBeTruthy();
    expect(
      screen.queryByRole("group", { name: "What the agent says of itself" }),
    ).toBeNull();
  });

  it("offers nothing to change or remove the agent", () => {
    for (const shown of [HELLO, STUB]) {
      const { unmount } = render(<AgentCard agent={shown} />);
      for (const role of ["button", "link", "textbox", "switch", "combobox"]) {
        expect(screen.queryAllByRole(role)).toEqual([]);
      }
      unmount();
    }
  });
});
