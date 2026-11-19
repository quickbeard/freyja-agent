import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { AgentForm } from "@/components/agents/agent-form";
import type { Agent } from "@/lib/agents";

const ADDRESS = "http://localhost:9998/agents/recognizable-stub";

const STUB: Agent = {
  id: "7c9e6679f0a54b3e8d2a1f4b6c8e0a2d",
  name: "stub",
  source: "saved",
  approval: "required",
  url: ADDRESS,
  version: 1,
  state: "reachable",
  checked_at: "2026-11-19T09:00:00Z",
  reason: null,
  card: {
    name: "Stub agent",
    description: "A stand-in agent for tests.",
    version: "0.1.0",
    skills: [],
    cut: false,
  },
  pending_tasks: 0,
  created_at: "2026-11-19T09:00:00Z",
  updated_at: "2026-11-19T09:00:00Z",
};
const CREATED: Reply = { status: 201, json: STUB };

function refusal(code: string, message: string, more: object = {}): Reply {
  return { status: 409, json: { detail: { code, message, ...more } } };
}

// The form as the list holds it: a control opens it, and it closes itself.
function Harness({
  onSaved,
  onUnknown,
}: {
  onSaved?: (agent: Agent) => void;
  onUnknown?: () => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open the form
      </button>
      <AgentForm
        open={open}
        onOpenChange={setOpen}
        onSaved={onSaved ?? (() => {})}
        onUnknown={onUnknown ?? (() => {})}
      />
    </>
  );
}

const field = (name: string) => screen.getByLabelText(name) as HTMLInputElement;

function type(name: string, value: string) {
  fireEvent.change(field(name), { target: { value } });
}

function fill(values: Record<string, string> = {}) {
  const given = { Name: "stub", Address: ADDRESS, ...values };
  for (const [name, value] of Object.entries(given)) {
    type(name, value);
  }
}

const save = () =>
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

/** What the form says is wrong with a field, or null when it says nothing. */
function errorAt(name: string): string | null {
  const input = field(name);
  if (input.getAttribute("aria-invalid") !== "true") return null;
  const ids = input.getAttribute("aria-describedby")?.split(" ") ?? [];
  const id = ids.find((one) => one.endsWith("-error"));
  return id ? (document.getElementById(id)?.textContent ?? null) : null;
}

function sent(fetchStub: ReturnType<typeof stubFetch>, call = 0) {
  const [path, init] = fetchStub.mock.calls[call];
  return { path, method: init?.method, body: JSON.parse(String(init?.body)) };
}

describe("AgentForm, adding", () => {
  it("opens with an empty name and an empty address, and says approval will be required", () => {
    render(<Harness />);
    const dialog = screen.getByRole("dialog", { name: "Add an agent" });
    expect(field("Name").value).toBe("");
    expect(field("Address").value).toBe("");
    // The browser is not asked to remember either.
    expect(field("Name").getAttribute("autocomplete")).toBe("off");
    expect(field("Address").getAttribute("autocomplete")).toBe("off");
    expect(dialog.textContent).toContain(
      "Messages to this agent will require approval",
    );
    // There is no choice to make about approval here, and no other field.
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    for (const role of ["switch", "combobox", "checkbox", "radio"]) {
      expect(screen.queryAllByRole(role)).toEqual([]);
    }
  });

  it("says which values are missing and sends nothing", () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    save();

    expect(errorAt("Name")).toMatch(/name/i);
    expect(errorAt("Address")).toMatch(/address/i);
    expect(fetchStub).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("sends the two values in one request, asks for no confirmation, and hands back the agent", async () => {
    const fetchStub = stubFetch(CREATED);
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill({ Name: "  stub ", Address: `  ${ADDRESS} ` });
    save();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(STUB));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    // What surrounds a value is not part of it; nothing else is sent, no approval choice.
    expect(sent(fetchStub)).toEqual({
      path: "/api/agents",
      method: "POST",
      body: { name: "stub", url: ADDRESS },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("sends the request when Enter is pressed in a field", async () => {
    const fetchStub = stubFetch(CREATED);
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    fireEvent.submit(field("Address").closest("form") as HTMLFormElement);

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("sends nothing more when Save is pressed again while the request is under way", async () => {
    // The answer waits until the test lets it go: the card is still being read.
    let answer: (response: Response) => void = () => {};
    const fetchStub = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchStub);
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    const form = field("Name").closest("form") as HTMLFormElement;

    save();
    // A second press, and Enter in a field, before the first one was answered.
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    // The form says it is busy, and its button takes no press.
    const busy = await screen.findByRole("button", { name: "Saving…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    expect(busy.getAttribute("aria-busy")).toBe("true");
    fireEvent.click(busy);
    expect(fetchStub).toHaveBeenCalledTimes(1);

    answer(
      new Response(JSON.stringify(STUB), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["name", "Name", "The name must start with a lowercase letter."],
    ["url", "Address", "The address must not carry a query."],
  ])("shows a value the service refuses at its field: %s", async (loc, label, msg) => {
    const fetchStub = stubFetch({
      status: 422,
      json: { detail: [{ type: "value_error", loc: ["body", loc], msg }] },
    });
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    save();

    await waitFor(() => expect(errorAt(label)).toBe(msg));
    // Nothing is said anywhere else, and what was typed is kept.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field("Name").value).toBe("stub");
    expect(field("Address").value).toBe(ADDRESS);
    expect(onSaved).not.toHaveBeenCalled();
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("shows a refused value the form has no field for over the form", async () => {
    stubFetch({
      status: 422,
      json: {
        detail: [
          {
            type: "extra_forbidden",
            loc: ["body", "approval"],
            msg: "Extra inputs are not permitted",
          },
        ],
      },
    });
    render(<Harness />);
    fill();
    save();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Extra inputs are not permitted");
    expect(errorAt("Name")).toBeNull();
    expect(errorAt("Address")).toBeNull();
  });

  it("shows a name that is taken at the name", async () => {
    const taken = "An agent named in the environment has this name.";
    stubFetch(refusal("name_taken", taken));
    render(<Harness />);
    fill();
    save();

    await waitFor(() => expect(errorAt("Name")).toBe(taken));
    expect(errorAt("Address")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field("Name").value).toBe("stub");
    expect(field("Address").value).toBe(ADDRESS);
  });

  it("shows an address that is taken at the address", async () => {
    const taken = "Another saved agent is at this address.";
    stubFetch(refusal("address_taken", taken));
    render(<Harness />);
    fill();
    save();

    await waitFor(() => expect(errorAt("Address")).toBe(taken));
    expect(errorAt("Name")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field("Address").value).toBe(ADDRESS);
  });

  it("shows the service's sentence when the agent cannot be used, and keeps what was typed", async () => {
    stubFetch(
      refusal("agent_unusable", "Nothing answered at the address.", {
        reason: "no_answer",
      }),
    );
    const onSaved = vi.fn();
    const onUnknown = vi.fn();
    render(<Harness onSaved={onSaved} onUnknown={onUnknown} />);
    fill();
    save();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Nothing answered at the address.");
    expect(field("Name").value).toBe("stub");
    expect(field("Address").value).toBe(ADDRESS);
    expect(onSaved).not.toHaveBeenCalled();
    // The service answered: the outcome is known, and nothing was saved.
    expect(onUnknown).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it.each([
    ["agent_limit", "Twenty agents are saved, which is the most there can be."],
    ["check_busy", "Four agent cards are being read. Try again in a moment."],
    ["other_origin", "Agents can only be changed from the console itself."],
  ])("shows the service's reason over the form: %s", async (code, message) => {
    stubFetch(refusal(code, message));
    render(<Harness />);
    fill();
    save();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
    expect(errorAt("Name")).toBeNull();
    expect(errorAt("Address")).toBeNull();
  });

  it.each<[string, Reply]>([
    ["the request got no answer", { fails: "Failed to fetch" }],
    ["the web app's server could not pass it on", { status: 502, text: "" }],
  ])("says the outcome is not known, never that it failed, when %s", async (_when, reply) => {
    const fetchStub = stubFetch(reply);
    const onSaved = vi.fn();
    const onUnknown = vi.fn();
    render(<Harness onSaved={onSaved} onUnknown={onUnknown} />);
    fill();
    save();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/not known/);
    expect(alert.textContent).not.toMatch(/fail|could not|was not|cannot/i);
    // The list is read again, once: it is what says whether the agent is there.
    expect(onUnknown).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
    // The form stays as it was, and nothing is sent again by itself.
    expect(field("Name").value).toBe("stub");
    expect(field("Address").value).toBe(ADDRESS);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("clears what it said about a field once the field is changed", async () => {
    stubFetch(refusal("name_taken", "Another agent has this name."));
    render(<Harness />);
    fill();
    save();
    await waitFor(() => expect(errorAt("Name")).not.toBeNull());

    type("Name", "other");
    expect(errorAt("Name")).toBeNull();
  });

  it("takes back what it said over the form when it is saved again", async () => {
    const fetchStub = stubFetch(
      refusal("agent_unusable", "Nothing answered at the address.", {
        reason: "no_answer",
      }),
      CREATED,
    );
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    save();
    await screen.findByRole("alert");

    save();
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(STUB));
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("shows what the service says as text, whatever it holds", async () => {
    const markup = '<img src="http://elsewhere.example/p.png" onerror="x()">';
    stubFetch(refusal("agent_unusable", markup, { reason: "unreadable" }));
    render(<Harness />);
    fill();
    save();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(markup);
    expect(document.querySelector("img")).toBeNull();
  });

  it("opens empty again after it is closed", async () => {
    render(<Harness />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    expect(field("Name").value).toBe("");
    expect(field("Address").value).toBe("");
  });

  it("keeps nothing of a refusal once it is closed", async () => {
    stubFetch(
      refusal("agent_unusable", "Nothing answered at the address.", {
        reason: "no_answer",
      }),
    );
    render(<Harness />);
    fill();
    save();
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field("Name").value).toBe("");
    expect(field("Address").value).toBe("");
  });

  it("opens empty again after it is sent, and writes nothing to the browser's storage", async () => {
    stubFetch(CREATED);
    render(<Harness />);
    fill();
    save();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.body.textContent).not.toContain("recognizable-stub");

    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    expect(field("Name").value).toBe("");
    expect(field("Address").value).toBe("");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
