import { render, screen } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it } from "vitest";
import { stubFetch } from "@/__tests__/testing";
import AgentsPage from "@/app/agents/page";

describe("the Agents section", () => {
  it("lists the agents under its heading", async () => {
    const fetchStub = stubFetch({
      json: {
        agents: [
          {
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
          },
        ],
        limit: 20,
        unusable: 0,
      },
    });
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <AgentsPage />
      </SWRConfig>,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Agents" }),
    ).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "hello" })).toBeTruthy();
    expect(fetchStub.mock.calls[0][0]).toBe("/api/agents");
    // The placeholder of feature 004 is gone.
    expect(screen.queryByText(/not managed from the console yet/i)).toBeNull();
  });
});
