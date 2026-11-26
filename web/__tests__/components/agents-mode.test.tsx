import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { AgentsModeSelect, autoAgents } from "@/components/agents-mode";
import type { Agent, AgentList, AgentsMode } from "@/lib/agents";

// Feature 010: the agents' approval mode, beside the Tasks section's Send buttons (FR-008).

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
  card: null,
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
};

function list(mode: AgentsMode, ...agents: Agent[]): Reply {
  const json: AgentList = { agents, mode, limit: 20, unusable: 0 };
  return { json };
}

function renderSelect() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <AgentsModeSelect />
    </SWRConfig>,
  );
}

const trigger = () => screen.getByRole("combobox", { name: "Agent approvals" });

// The dropdown is a listbox: it is opened and chosen from with the keyboard.
async function choose(label: string) {
  const opened = trigger();
  opened.focus();
  fireEvent.keyDown(opened, { key: "ArrowDown" });
  const option = await screen.findByRole("option", { name: label });
  fireEvent.keyDown(option, { key: "Enter" });
}

function sent(fetchStub: ReturnType<typeof stubFetch>) {
  return fetchStub.mock.calls.map(([input, init]) => [
    String(input),
    init?.method ?? "GET",
    init?.body === undefined ? undefined : JSON.parse(String(init.body)),
  ]);
}

describe("AgentsModeSelect", () => {
  it("shows the mode the list was read with, and what it means", async () => {
    stubFetch(list("auto", HELLO, STUB));
    renderSelect();
    await waitFor(() => expect(trigger().textContent).toContain("Auto"));
    expect(
      screen.getByText(
        "For every task: agents set to Auto run without approval; the others ask.",
      ),
    ).toBeTruthy();
  });

  it("sets Manual at once, and reads the list again", async () => {
    const fetchStub = stubFetch(
      list("auto", HELLO),
      { json: { mode: "manual" } },
      list("manual", HELLO),
    );
    renderSelect();
    await waitFor(() => expect(trigger().textContent).toContain("Auto"));
    await choose("Manual");
    await waitFor(() => expect(trigger().textContent).toContain("Manual"));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(sent(fetchStub)).toEqual([
      ["/api/agents", "GET", undefined],
      ["/api/agents/mode", "PUT", { mode: "manual" }],
      ["/api/agents", "GET", undefined],
    ]);
    expect(
      screen.getByText(
        "For every task: every message to an agent waits for your approval.",
      ),
    ).toBeTruthy();
  });

  it("asks before Auto, saying how many agents it lets run, and sends nothing when canceled", async () => {
    const fetchStub = stubFetch(list("manual", HELLO, STUB));
    renderSelect();
    await waitFor(() => expect(trigger().textContent).toContain("Manual"));
    await choose("Auto");
    const asked = await screen.findByRole("alertdialog", {
      name: "Set agent approvals to Auto?",
    });
    expect(asked.textContent).toContain("1 agent is set to Auto now");
    fireEvent.click(within(asked).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(sent(fetchStub)).toEqual([["/api/agents", "GET", undefined]]);
    expect(trigger().textContent).toContain("Manual");
  });

  it("sets Auto once confirmed, and not on the second press of a double press", async () => {
    const fetchStub = stubFetch(
      list("manual", HELLO),
      { json: { mode: "auto" } },
      list("auto", HELLO),
    );
    renderSelect();
    await waitFor(() => expect(trigger().textContent).toContain("Manual"));
    await choose("Auto");
    const asked = await screen.findByRole("alertdialog");
    const confirm = within(asked).getByRole("button", { name: "Set to Auto" });
    fireEvent.click(confirm, { detail: 2 });
    expect(sent(fetchStub)).toHaveLength(1);
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await waitFor(() => expect(trigger().textContent).toContain("Auto"));
    expect(sent(fetchStub)).toEqual([
      ["/api/agents", "GET", undefined],
      ["/api/agents/mode", "PUT", { mode: "auto" }],
      ["/api/agents", "GET", undefined],
    ]);
  });

  it("cannot be changed while the list of agents cannot be read, and says the mode is not known", async () => {
    stubFetch({
      status: 500,
      json: {
        detail: {
          code: "internal",
          message: "The saved agents could not be read.",
        },
      },
    });
    renderSelect();
    await screen.findByText("Not known: the list of agents could not be read.");
    expect(trigger().getAttribute("data-disabled")).not.toBeNull();
    expect(trigger().textContent).toContain("Not known");
  });

  it("shows the service's refusal in its words", async () => {
    stubFetch(list("auto", HELLO), {
      status: 403,
      json: {
        detail: {
          code: "other_origin",
          message: "A page of another origin cannot change the Agents.",
        },
      },
    });
    renderSelect();
    await waitFor(() => expect(trigger().textContent).toContain("Auto"));
    await choose("Manual");
    expect((await screen.findByRole("alert")).textContent).toBe(
      "A page of another origin cannot change the Agents.",
    );
  });

  it("says that the outcome is not known when no answer came", async () => {
    const replies: Reply[] = [list("auto", HELLO), { fails: "network down" }];
    const fetchStub = vi.fn(async () => {
      const reply = replies.shift() ?? list("auto", HELLO);
      if ("fails" in reply) throw new TypeError(reply.fails);
      return new Response(JSON.stringify((reply as { json: unknown }).json), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchStub);
    renderSelect();
    await waitFor(() => expect(trigger().textContent).toContain("Auto"));
    await choose("Manual");
    expect((await screen.findByRole("alert")).textContent).toContain(
      "it is not known whether the mode changed",
    );
  });
});

describe("autoAgents", () => {
  it("counts the agents in use whose own mode is Auto", () => {
    const json = list(
      "manual",
      HELLO,
      STUB,
      { ...STUB, id: "b", name: "b", approval: "none" },
      { ...STUB, id: "c", name: "c", approval: "none", state: "not_in_use" },
    ) as { json: AgentList };
    expect(autoAgents(json.json)).toBe(2);
  });
});
