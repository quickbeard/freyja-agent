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

// The form as the list holds it: a control opens it, and it closes itself. Given an agent it
// changes that agent, and `live` is the agent as the list has it now.
function Harness({
  onSaved,
  onUnknown,
  onStale,
  onMoved,
  agent,
  live,
}: {
  onSaved?: (agent: Agent) => void;
  onUnknown?: () => void;
  onStale?: () => void;
  onMoved?: (agent: Agent, givenUp: number) => void;
  agent?: Agent;
  live?: Agent | null;
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
        onStale={onStale}
        onMoved={onMoved}
        agent={agent}
        live={live}
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
    // The one choice is about approval, and there is no other field.
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(screen.getAllByRole("radio")).toHaveLength(2);
    for (const role of ["switch", "combobox", "checkbox"]) {
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

  // The card read can take twenty seconds, so a form may be closed, and opened again, while
  // its request is still under way.
  function held() {
    let answer: (response: Response) => void = () => {};
    let lose: (reason: unknown) => void = () => {};
    const fetchStub = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Promise<Response>((resolve, reject) => {
          answer = resolve;
          lose = reject;
        }),
    );
    vi.stubGlobal("fetch", fetchStub);
    return {
      fetchStub,
      answer: (status: number, json: unknown) =>
        answer(
          new Response(JSON.stringify(json), {
            status,
            headers: { "content-type": "application/json" },
          }),
        ),
      lose: () => lose(new TypeError("Failed to fetch")),
    };
  }

  async function closedWhileSavingThenOpenedAgain() {
    fill();
    save();
    await screen.findByRole("button", { name: "Saving…" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    type("Name", "report");
  }

  it("keeps the form that is open now when an earlier form's request is answered", async () => {
    const request = held();
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    await closedWhileSavingThenOpenedAgain();

    request.answer(201, STUB);
    // The agent was saved, so whoever holds the list is told, and reads it again.
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(STUB));
    // The form that is open now is another one: it stays, with what is being typed in it.
    expect(screen.getByRole("dialog")).toBeDefined();
    expect(field("Name").value).toBe("report");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("says nothing in the form that is open now when an earlier form's request is refused", async () => {
    const request = held();
    const onSaved = vi.fn();
    const onUnknown = vi.fn();
    render(<Harness onSaved={onSaved} onUnknown={onUnknown} />);
    await closedWhileSavingThenOpenedAgain();

    request.answer(409, {
      detail: {
        code: "name_taken",
        message: "Another saved agent has this name.",
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    // The refusal was about what the closed form sent, not about what is typed now.
    expect(field("Name").value).toBe("report");
    expect(field("Name").getAttribute("aria-invalid")).toBeNull();
    expect(document.body.textContent).not.toContain("has this name");
    expect(onSaved).not.toHaveBeenCalled();
    expect(onUnknown).not.toHaveBeenCalled();
  });

  it("has the list read again when an earlier form's request got no answer", async () => {
    const request = held();
    const onUnknown = vi.fn();
    render(<Harness onUnknown={onUnknown} />);
    await closedWhileSavingThenOpenedAgain();

    request.lose();
    // Nobody is left to tell that the outcome is not known: the list says what is so.
    await waitFor(() => expect(onUnknown).toHaveBeenCalledTimes(1));
    expect(field("Name").value).toBe("report");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.body.textContent).not.toContain("not known");
  });
});

// Part 2 (T085): the approval choice, when adding and when changing a saved agent.

const choice = (name: string) =>
  screen.getByRole("radio", { name }) as HTMLInputElement;
const choose = (name: string) => fireEvent.click(choice(name));
const REQUIRED = "Requires approval";
const FREE = "Runs without approval";
const confirmation = () => screen.queryByRole("note", { name: "Confirmation" });
const confirm = (detail = 1) =>
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }), { detail });
const theForm = () => field("Name").closest("form") as HTMLFormElement;

const FREED: Agent = { ...STUB, approval: "none", version: 2 };

describe("AgentForm, adding with the approval choice", () => {
  it("offers the choice, set to requiring approval", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Approval" })).toBeTruthy();
    expect(choice(REQUIRED).checked).toBe(true);
    expect(choice(FREE).checked).toBe(false);
    expect(confirmation()).toBeNull();
  });

  it("says what the other choice means once it is chosen", () => {
    render(<Harness />);
    choose(FREE);
    const dialog = screen.getByRole("dialog");
    expect(choice(FREE).checked).toBe(true);
    expect(dialog.textContent).toContain(
      "Messages to this agent will be sent without approval",
    );
    expect(dialog.textContent).not.toContain("will require approval");
  });

  it("asks nothing when the agent is to require approval", async () => {
    const fetchStub = stubFetch(CREATED);
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    choose(FREE);
    choose(REQUIRED);
    save();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(STUB));
    expect(sent(fetchStub).body).toEqual({ name: "stub", url: ADDRESS });
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("sends nothing on Save with running without approval chosen, and says what confirming means", () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    fill();
    choose(FREE);
    save();

    const note = confirmation() as HTMLElement;
    // It names the agent, and says that nothing sent to it will be seen by a person first.
    expect(note.textContent).toContain("stub");
    expect(note.textContent).toContain(
      "Nothing Freyja sends to stub will be seen by a person first.",
    );
    expect(fetchStub).not.toHaveBeenCalled();
    // What was typed is still there, and Save gave way to a button of its own.
    expect(field("Name").value).toBe("stub");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
  });

  it("sends nothing when the confirmation is canceled, and keeps nothing", async () => {
    const fetchStub = stubFetch(CREATED);
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    choose(FREE);
    save();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(fetchStub).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    expect(field("Name").value).toBe("");
    expect(choice(REQUIRED).checked).toBe(true);
    expect(confirmation()).toBeNull();
  });

  it("sends the choice with the two values once Confirm is pressed, in one request", async () => {
    const fetchStub = stubFetch({ status: 201, json: FREED });
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill({ Name: " stub " });
    choose(FREE);
    save();
    confirm();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(FREED));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(sent(fetchStub)).toEqual({
      path: "/api/agents",
      method: "POST",
      body: { name: "stub", url: ADDRESS, approval: "none" },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("does not confirm on Enter in a field, nor on the second press of a double press", () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    fill();
    choose(FREE);
    const pressed = screen.getByRole("button", { name: "Save" });
    fireEvent.click(pressed, { detail: 1 });
    // The second press of a double press lands where the first one did.
    fireEvent.click(pressed, { detail: 2 });
    expect(confirmation()).not.toBeNull();
    // Nor when it lands on the button that took Save's place.
    confirm(2);
    expect(fetchStub).not.toHaveBeenCalled();

    // Enter in a field asks the form to save, which asks again and sends nothing.
    fireEvent.submit(theForm());
    fireEvent.submit(theForm());
    expect(fetchStub).not.toHaveBeenCalled();
    expect(confirmation()).not.toBeNull();
  });

  it("sends once when Confirm is pressed again while the request is under way", async () => {
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
    choose(FREE);
    save();
    confirm();
    const busy = await screen.findByRole("button", { name: "Saving…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(busy);
    fireEvent.submit(theForm());
    expect(fetchStub).toHaveBeenCalledTimes(1);

    answer(
      new Response(JSON.stringify(FREED), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("takes the confirmation back when a value or the choice is changed", () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    fill();
    choose(FREE);
    save();
    expect(confirmation()).not.toBeNull();

    type("Name", "report");
    expect(confirmation()).toBeNull();
    save();
    expect((confirmation() as HTMLElement).textContent).toContain("report");

    choose(REQUIRED);
    expect(confirmation()).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("says which values are missing before it asks anything", () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    choose(FREE);
    save();

    expect(errorAt("Name")).toMatch(/name/i);
    expect(errorAt("Address")).toMatch(/address/i);
    expect(confirmation()).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("goes back to the fields with the service's reason when a confirmed add is refused", async () => {
    stubFetch(
      refusal("agent_unusable", "Nothing answered at the address.", {
        reason: "no_answer",
      }),
    );
    render(<Harness />);
    fill();
    choose(FREE);
    save();
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Nothing answered at the address.");
    // Saving again is asked about again: nothing is confirmed twice by one press.
    expect(confirmation()).toBeNull();
    expect(choice(FREE).checked).toBe(true);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });
});

function changed(agent: Agent): Reply {
  return { json: { agent, given_up: 0 } };
}

describe("AgentForm, changing a saved agent", () => {
  const SAVED: Agent = { ...STUB, version: 3, pending_tasks: 1 };
  const NOW_FREE: Agent = { ...SAVED, approval: "none", version: 4 };
  const SERVED: Agent = { ...SAVED, approval: "none" };
  const NOW_REQUIRED: Agent = { ...SERVED, approval: "required", version: 4 };

  it("opens with the agent's values, and its name cannot be edited", () => {
    render(<Harness agent={SAVED} live={SAVED} />);
    expect(screen.getByRole("dialog", { name: "Edit stub" })).toBeTruthy();
    expect(field("Name").value).toBe("stub");
    expect(field("Name").readOnly).toBe(true);
    expect(field("Address").value).toBe(ADDRESS);
    // The address can be changed since Phase 8: part 3 below.
    expect(field("Address").readOnly).toBe(false);
    expect(choice(REQUIRED).checked).toBe(true);
    expect(choice(FREE).checked).toBe(false);
    // A name that is typed over all the same stays what it was.
    type("Name", "other");
    expect(field("Name").value).toBe("stub");
  });

  it("opens with the choice the agent has", () => {
    render(<Harness agent={SERVED} live={SERVED} />);
    expect(choice(FREE).checked).toBe(true);
    expect(choice(REQUIRED).checked).toBe(false);
  });

  it("sends nothing when Save is pressed with nothing changed, and says so", () => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    const onSaved = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onSaved={onSaved} />);
    save();
    fireEvent.submit(theForm());

    expect(fetchStub).not.toHaveBeenCalled();
    expect(confirmation()).toBeNull();
    expect(screen.getByRole("status").textContent).toMatch(/nothing/i);
    expect(onSaved).not.toHaveBeenCalled();
    // Chosen and chosen back is nothing changed either.
    choose(FREE);
    expect(screen.queryByRole("status")).toBeNull();
    choose(REQUIRED);
    save();
    expect(fetchStub).not.toHaveBeenCalled();
    expect(confirmation()).toBeNull();
  });

  it("asks before switching to running without approval, and says what is given up", () => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    save();

    const said = (confirmation() as HTMLElement).textContent ?? "";
    expect(said).toContain("stub");
    // Every message is sent unseen, from the next run, and a waiting proposal can then
    // only be approved (FR-011, SC-005).
    expect(said).toContain(
      "Every message to stub will be sent without a person seeing it first, from the next run.",
    );
    expect(said).toContain(
      "A proposal for stub that is already waiting for a decision can then only be approved.",
    );
    expect(said).not.toMatch(/holds at once/);
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("asks before switching to requiring approval, and says that it holds at once", () => {
    const fetchStub = stubFetch(changed(NOW_REQUIRED));
    render(<Harness agent={SERVED} live={SERVED} />);
    choose(REQUIRED);
    save();

    const said = (confirmation() as HTMLElement).textContent ?? "";
    expect(said).toContain("stub");
    expect(said).toContain("This holds at once, also for runs in progress.");
    expect(said).toContain(
      "A paused step that holds a message to stub that needed no approval cannot be continued until the choice is changed back or the agent is removed.",
    );
    expect(said).not.toMatch(/from the next run/);
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("sends the version the form opened with and only the choice, once Confirm is pressed", async () => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    const onSaved = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onSaved={onSaved} />);
    choose(FREE);
    save();
    expect(fetchStub).not.toHaveBeenCalled();
    confirm();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(NOW_FREE));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    // Neither the name nor the address is sent: only what was changed, at that version.
    expect(sent(fetchStub)).toEqual({
      path: `/api/agents/${SAVED.id}`,
      method: "PATCH",
      body: { version: 3, approval: "none" },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("sends the choice to require approval the same way", async () => {
    const fetchStub = stubFetch(changed(NOW_REQUIRED));
    const onSaved = vi.fn();
    render(<Harness agent={SERVED} live={SERVED} onSaved={onSaved} />);
    choose(REQUIRED);
    save();
    confirm();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(NOW_REQUIRED));
    expect(sent(fetchStub).body).toEqual({ version: 3, approval: "required" });
  });

  it("sends nothing when the confirmation is canceled", async () => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    const onSaved = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onSaved={onSaved} />);
    choose(FREE);
    save();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(fetchStub).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("does not confirm on Enter in a field, nor on the second press of a double press", () => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    const pressed = screen.getByRole("button", { name: "Save" });
    fireEvent.click(pressed, { detail: 1 });
    fireEvent.click(pressed, { detail: 2 });
    expect(confirmation()).not.toBeNull();
    confirm(2);
    fireEvent.submit(theForm());
    fireEvent.submit(theForm());

    expect(fetchStub).not.toHaveBeenCalled();
    expect(confirmation()).not.toBeNull();
  });

  it("sends once when Confirm is pressed again while the request is under way", async () => {
    let answer: (response: Response) => void = () => {};
    const fetchStub = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchStub);
    const onSaved = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onSaved={onSaved} />);
    choose(FREE);
    save();
    confirm();
    const busy = await screen.findByRole("button", { name: "Saving…" });
    fireEvent.click(busy);
    fireEvent.submit(theForm());
    expect(fetchStub).toHaveBeenCalledTimes(1);

    answer(
      new Response(JSON.stringify({ agent: NOW_FREE, given_up: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("shows the agent in the confirmation as the list has it at that moment", () => {
    stubFetch(changed(NOW_FREE));
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    save();
    const said = () => (confirmation() as HTMLElement).textContent ?? "";
    expect(said()).toContain(ADDRESS);
    expect(said()).toContain("requires approval");
    expect(said()).toContain("reachable");
    expect(said()).toContain("1 pending task.");

    // The list is read again while the form asks: the same version, and more to tell.
    rerender(
      <Harness
        agent={SAVED}
        live={{
          ...SAVED,
          state: "unreachable",
          reason: "Nothing answered.",
          pending_tasks: 2,
        }}
      />,
    );
    expect(said()).toContain("unreachable");
    expect(said()).toContain("2 pending tasks.");
    expect(said()).not.toContain("1 pending task.");
    // It is still the same question, about the same agent.
    expect(said()).toContain("without a person seeing it first");
  });

  it("sends the version its fields were filled from, whatever it is handed afterwards", async () => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    const onSaved = vi.fn();
    const { rerender } = render(<Harness agent={SAVED} onSaved={onSaved} />);
    choose(FREE);
    // Whoever holds the form hands it the agent again, as it has become, and the list
    // itself cannot be read just now: the form still stands for what it opened with.
    rerender(<Harness agent={{ ...SAVED, version: 9 }} onSaved={onSaved} />);
    save();
    confirm();

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(sent(fetchStub).body).toEqual({ version: 3, approval: "none" });
  });

  it("takes its confirmation back and says the agent was changed elsewhere when the list shows a later version", () => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    save();
    expect(confirmation()).not.toBeNull();

    rerender(
      <Harness
        agent={SAVED}
        live={{ ...SAVED, version: 4, url: "http://localhost:9997" }}
      />,
    );
    expect(confirmation()).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("stub was changed elsewhere");
    // Nothing can be saved from this form any more: its values are of an earlier agent.
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect((saveButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(saveButton);
    fireEvent.submit(theForm());
    expect(confirmation()).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("says so before anything was asked, too, when the list shows a later version", () => {
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    expect(screen.queryByRole("alert")).toBeNull();
    rerender(<Harness agent={SAVED} live={{ ...SAVED, version: 4 }} />);
    expect(screen.getByRole("alert").textContent).toContain(
      "changed elsewhere",
    );
  });

  it("says the agent was removed elsewhere when the list no longer has it", () => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    save();
    rerender(<Harness agent={SAVED} live={null} />);

    expect(confirmation()).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain(
      "stub is no longer in the list",
    );
    fireEvent.submit(theForm());
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it.each([
    ["a later version", { ...SAVED, version: 4 }, "stub was changed elsewhere"],
    ["no such agent", null, "stub is no longer in the list"],
  ])("stays taken back when the list showed %s and then cannot be read", (_what, shown, said) => {
    const fetchStub = stubFetch(changed(NOW_FREE));
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    save();
    rerender(<Harness agent={SAVED} live={shown} />);
    expect(confirmation()).toBeNull();

    // The next reading of the list fails. What the form learned still holds: its values
    // are of an earlier agent, and it asks nothing and saves nothing.
    rerender(<Harness agent={SAVED} live={undefined} />);
    expect(confirmation()).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain(said);
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect((saveButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(theForm());
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("keeps asking while the list cannot be read, and says the agent is shown as the form opened it", () => {
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    save();
    expect((confirmation() as HTMLElement).textContent).toContain(
      "As the list has it now:",
    );
    rerender(<Harness agent={SAVED} live={undefined} />);
    const asked = (confirmation() as HTMLElement).textContent ?? "";
    expect(asked).toContain(ADDRESS);
    // Not called the list's copy when it is not.
    expect(asked).not.toContain("As the list has it now");
    expect(asked).toContain(
      "The list cannot be read now. As it was when this form opened:",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([
    [
      409,
      "agent_changed",
      "The agent was changed since this page read it. Nothing was changed.",
    ],
    [404, "agent_not_found", "There is no agent with this id."],
    [
      409,
      "agent_read_only",
      "This saved agent is not in use, so it can only be removed.",
    ],
  ])("says so and has the list read again when the change is refused: %s %s", async (status, code, message) => {
    const fetchStub = stubFetch({
      status,
      json: { detail: { code, message } },
    });
    const onSaved = vi.fn();
    const onStale = vi.fn();
    const onUnknown = vi.fn();
    render(
      <Harness
        agent={SAVED}
        live={SAVED}
        onSaved={onSaved}
        onStale={onStale}
        onUnknown={onUnknown}
      />,
    );
    choose(FREE);
    save();
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
    expect(onStale).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
    // The service answered: the outcome is known.
    expect(onUnknown).not.toHaveBeenCalled();
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(confirmation()).toBeNull();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("shows any other refusal in the service's words and leaves the list alone", async () => {
    stubFetch(
      refusal(
        "other_origin",
        "Agents can only be changed from the console itself.",
      ),
    );
    const onStale = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onStale={onStale} />);
    choose(FREE);
    save();
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "Agents can only be changed from the console itself.",
    );
    expect(onStale).not.toHaveBeenCalled();
  });

  it("shows a refused value over the form, as the service words it", async () => {
    stubFetch({
      status: 422,
      json: {
        detail: [
          {
            type: "literal_error",
            loc: ["body", "approval"],
            msg: "Input should be 'required' or 'none'",
          },
        ],
      },
    });
    render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    save();
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Input should be 'required' or 'none'");
  });

  it.each<[string, Reply]>([
    ["the request got no answer", { fails: "Failed to fetch" }],
    ["the web app's server could not pass it on", { status: 502, text: "" }],
  ])("says the outcome is not known, never that it failed, when %s", async (_when, reply) => {
    const fetchStub = stubFetch(reply);
    const onSaved = vi.fn();
    const onUnknown = vi.fn();
    const onStale = vi.fn();
    render(
      <Harness
        agent={SAVED}
        live={SAVED}
        onSaved={onSaved}
        onUnknown={onUnknown}
        onStale={onStale}
      />,
    );
    choose(FREE);
    save();
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/not known/);
    expect(alert.textContent).toMatch(/changed/);
    expect(alert.textContent).not.toMatch(/fail|could not|was not|cannot/i);
    expect(onUnknown).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
    expect(onStale).not.toHaveBeenCalled();
    // Nothing is sent again by itself, and nothing is confirmed twice by one press.
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(confirmation()).toBeNull();
    expect(choice(FREE).checked).toBe(true);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("opens with the agent's own choice again after it is closed", async () => {
    render(<Harness agent={SAVED} live={SAVED} />);
    choose(FREE);
    save();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    expect(choice(REQUIRED).checked).toBe(true);
    expect(confirmation()).toBeNull();
  });

  it("leaves the form that is open now alone when an earlier form's change is answered", async () => {
    let answer: (response: Response) => void = () => {};
    const fetchStub = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchStub);
    const onSaved = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onSaved={onSaved} />);
    choose(FREE);
    save();
    confirm();
    await screen.findByRole("button", { name: "Saving…" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    choose(FREE);

    answer(
      new Response(JSON.stringify({ agent: NOW_FREE, given_up: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    // The change was made, so whoever holds the list is told; the form that is open now
    // is another one and stays as it is.
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(NOW_FREE));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(choice(FREE).checked).toBe(true);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });
});

// Part 3 (T100): changing a saved agent's address.

describe("AgentForm, changing a saved agent's address", () => {
  const SAVED: Agent = { ...STUB, version: 3, pending_tasks: 1 };
  const OTHER = "http://localhost:9996/agents/recognizable-other";
  const MOVED: Agent = { ...SAVED, url: OTHER, version: 4, pending_tasks: 0 };
  const moved = (givenUp: number, agent: Agent = MOVED): Reply => ({
    json: { agent, given_up: givenUp },
  });
  const said = () => (confirmation() as HTMLElement).textContent ?? "";

  it("lets the address be typed over, and still not the name", () => {
    render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    expect(field("Address").value).toBe(OTHER);
    type("Name", "other");
    expect(field("Name").value).toBe("stub");
    expect(field("Address").getAttribute("autocomplete")).toBe("off");
  });

  it("asks before moving the agent, and says what the move means", () => {
    const fetchStub = stubFetch(moved(1));
    render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    save();

    // It names the agent and the address it is to have (FR-011).
    expect(said()).toContain(`Move stub to ${OTHER}?`);
    expect(said()).toContain("The change applies from the next run");
    expect(said()).toContain(
      "a run in progress finishes at the earlier address",
    );
    expect(said()).toContain(
      "A proposal for stub that is already waiting for a decision will go to the new address once approved.",
    );
    // How many tasks are pending, as the list has it now, and that they will be given up.
    expect(said()).toContain(
      "stub has 1 pending task now, and it will be given up",
    );
    // Nothing about the approval choice, which is not being changed.
    expect(said()).not.toMatch(/without approval\?|Require approval/);
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it.each([
    [0, "stub has no pending task now, so none will be given up."],
    [1, "stub has 1 pending task now, and it will be given up"],
    [4, "stub has 4 pending tasks now, and they will be given up"],
  ])("says how many tasks are pending as the list has it at that moment: %i", (pending, sentence) => {
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    save();
    expect(said()).toContain("1 pending task now");

    // The list is read again while the form asks, at the same version: a task ended, or
    // more became pending.
    rerender(
      <Harness agent={SAVED} live={{ ...SAVED, pending_tasks: pending }} />,
    );
    expect(said()).toContain(sentence);
    if (pending !== 1) {
      expect(said()).not.toContain("1 pending task now");
    }
  });

  it("says the number is the form's own while the list cannot be read", () => {
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    save();
    rerender(<Harness agent={SAVED} live={undefined} />);
    expect(said()).toContain(
      "stub had 1 pending task when this form opened. Every task that is pending when the change is made will be given up.",
    );
    expect(said()).not.toContain("pending task now");
  });

  it("sends the version the form opened with and only the address, once Confirm is pressed, and hands on how many tasks were given up", async () => {
    const fetchStub = stubFetch(moved(2));
    const onSaved = vi.fn();
    const onMoved = vi.fn();
    render(
      <Harness
        agent={SAVED}
        live={SAVED}
        onSaved={onSaved}
        onMoved={onMoved}
      />,
    );
    // What surrounds a pasted address is not part of it.
    type("Address", `  ${OTHER} `);
    save();
    expect(fetchStub).not.toHaveBeenCalled();
    confirm();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(MOVED));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(sent(fetchStub)).toEqual({
      path: `/api/agents/${SAVED.id}`,
      method: "PATCH",
      body: { version: 3, url: OTHER },
    });
    // The service's number, whatever the form showed before.
    expect(onMoved).toHaveBeenCalledTimes(1);
    expect(onMoved).toHaveBeenCalledWith(MOVED, 2);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("shows both meanings and sends both values when the address and the choice are changed together", async () => {
    const both: Agent = { ...MOVED, approval: "none" };
    const fetchStub = stubFetch(moved(1, both));
    const onMoved = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onMoved={onMoved} />);
    type("Address", OTHER);
    choose(FREE);
    save();

    expect(said()).toContain(`Move stub to ${OTHER}?`);
    expect(said()).toContain(
      "A proposal for stub that is already waiting for a decision will go to the new address once approved.",
    );
    expect(said()).toContain(
      "stub has 1 pending task now, and it will be given up",
    );
    expect(said()).toContain(
      "Every message to stub will be sent without a person seeing it first, from the next run.",
    );
    expect(fetchStub).not.toHaveBeenCalled();
    confirm();

    await waitFor(() => expect(onMoved).toHaveBeenCalledWith(both, 1));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(sent(fetchStub).body).toEqual({
      version: 3,
      url: OTHER,
      approval: "none",
    });
  });

  it("sends nothing when the address is the one the form opened with, however it is spaced", () => {
    const fetchStub = stubFetch(moved(0));
    render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    type("Address", `  ${ADDRESS}  `);
    save();

    expect(fetchStub).not.toHaveBeenCalled();
    expect(confirmation()).toBeNull();
    expect(screen.getByRole("status").textContent).toMatch(/nothing/i);
  });

  it("says the address is missing at its field, and asks nothing", () => {
    const fetchStub = stubFetch(moved(0));
    render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", "   ");
    save();

    expect(errorAt("Address")).toBe("Enter the address.");
    expect(confirmation()).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("takes the confirmation back when the address is changed again", () => {
    render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    save();
    expect(confirmation()).not.toBeNull();

    type("Address", `${OTHER}/elsewhere`);
    expect(confirmation()).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    save();
    expect(said()).toContain(`Move stub to ${OTHER}/elsewhere?`);
  });

  it("does not move on Enter in a field, nor on the second press of a double press", () => {
    const fetchStub = stubFetch(moved(1));
    render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    const pressed = screen.getByRole("button", { name: "Save" });
    fireEvent.click(pressed, { detail: 1 });
    fireEvent.click(pressed, { detail: 2 });
    expect(confirmation()).not.toBeNull();
    confirm(2);
    fireEvent.submit(theForm());
    fireEvent.submit(theForm());

    expect(fetchStub).not.toHaveBeenCalled();
    expect(confirmation()).not.toBeNull();
  });

  it("sends nothing when the confirmation is canceled", async () => {
    const fetchStub = stubFetch(moved(1));
    const onMoved = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onMoved={onMoved} />);
    type("Address", OTHER);
    save();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(fetchStub).not.toHaveBeenCalled();
    expect(onMoved).not.toHaveBeenCalled();
  });

  it.each<[string, Reply, string]>([
    [
      "another agent is at the address",
      refusal("address_taken", "Another saved agent is at this address."),
      "Another saved agent is at this address.",
    ],
    [
      "the address breaks a rule",
      {
        status: 422,
        json: {
          detail: [
            {
              type: "agent_address",
              loc: ["body", "url"],
              msg: "The address must be on this machine: localhost or a loopback IP address.",
            },
          ],
        },
      },
      "The address must be on this machine: localhost or a loopback IP address.",
    ],
  ])("says why at the address, and keeps what was typed, when %s", async (_when, reply, sentence) => {
    stubFetch(reply);
    const onSaved = vi.fn();
    const onMoved = vi.fn();
    const onStale = vi.fn();
    render(
      <Harness
        agent={SAVED}
        live={SAVED}
        onSaved={onSaved}
        onMoved={onMoved}
        onStale={onStale}
      />,
    );
    type("Address", OTHER);
    choose(FREE);
    save();
    confirm();

    await waitFor(() => expect(errorAt("Address")).toBe(sentence));
    expect(screen.queryByRole("alert")).toBeNull();
    // What was typed and chosen stays, and saving again is asked about again.
    expect(field("Address").value).toBe(OTHER);
    expect(choice(FREE).checked).toBe(true);
    expect(confirmation()).toBeNull();
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onMoved).not.toHaveBeenCalled();
    // The agent is as the form has it: the list is left alone.
    expect(onStale).not.toHaveBeenCalled();
  });

  it.each([
    [
      "agent_unusable",
      "Nothing answered at the address.",
      { reason: "no_answer" },
    ],
    [
      "check_busy",
      "Four card reads are under way on this instance. Wait for one to end.",
      {},
    ],
  ])("says why over the form in the service's sentence, and keeps what was typed: %s", async (code, sentence, more) => {
    const fetchStub = stubFetch(refusal(code, sentence, more));
    const onMoved = vi.fn();
    render(<Harness agent={SAVED} live={SAVED} onMoved={onMoved} />);
    type("Address", OTHER);
    save();
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(sentence);
    expect(errorAt("Address")).toBeNull();
    expect(field("Address").value).toBe(OTHER);
    expect(confirmation()).toBeNull();
    expect(onMoved).not.toHaveBeenCalled();
    // Saved again, it is asked about again, and sent again once confirmed.
    save();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(said()).toContain(`Move stub to ${OTHER}?`);
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("takes back what it said at the address once the address is changed", async () => {
    stubFetch(
      refusal("address_taken", "Another saved agent is at this address."),
    );
    render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    save();
    confirm();
    await waitFor(() => expect(errorAt("Address")).not.toBeNull());

    type("Address", `${OTHER}/elsewhere`);
    expect(errorAt("Address")).toBeNull();
  });

  it("says the outcome is not known when a move got no answer, and has the list read again", async () => {
    const fetchStub = stubFetch({ fails: "Failed to fetch" });
    const onUnknown = vi.fn();
    const onMoved = vi.fn();
    render(
      <Harness
        agent={SAVED}
        live={SAVED}
        onUnknown={onUnknown}
        onMoved={onMoved}
      />,
    );
    type("Address", OTHER);
    save();
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/not known/);
    expect(alert.textContent).not.toMatch(/fail|could not|was not|cannot/i);
    expect(onUnknown).toHaveBeenCalledTimes(1);
    expect(onMoved).not.toHaveBeenCalled();
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(field("Address").value).toBe(OTHER);
  });

  it("does not call a change its own unanswered request may have made a change made elsewhere", async () => {
    stubFetch({ fails: "Failed to fetch" });
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    save();
    confirm();
    expect((await screen.findByRole("alert")).textContent).toMatch(/not known/);

    // The list is read again, and shows the agent at a later version: the request that
    // got no answer may be what changed it.
    rerender(
      <Harness agent={SAVED} live={{ ...SAVED, version: 4, url: OTHER }} />,
    );
    const alerts = screen
      .getAllByRole("alert")
      .map((alert) => alert.textContent);
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toContain("stub is no longer as this form has it.");
    // Still that the outcome is not known, and never that the request failed (FR-004).
    expect(alerts[0]).toMatch(/not known/);
    expect(alerts[0]).not.toMatch(/fail|could not|was not|cannot/i);
    expect(alerts[0]).not.toContain("changed elsewhere");
    // Nothing can be saved from this form any more, as for any agent that changed.
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect((saveButton as HTMLButtonElement).disabled).toBe(true);
  });

  it("says nothing of tasks given up after a change of the choice alone, nor when the address the service keeps is the same", async () => {
    const onMoved = vi.fn();
    const onSaved = vi.fn();
    const freed: Agent = { ...SAVED, approval: "none", version: 4 };
    stubFetch(moved(0, freed));
    const { unmount } = render(
      <Harness
        agent={SAVED}
        live={SAVED}
        onSaved={onSaved}
        onMoved={onMoved}
      />,
    );
    choose(FREE);
    save();
    confirm();
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(freed));
    unmount();

    // Another spelling of the address the agent has: the service answers with the agent
    // as it is, and nothing was moved.
    stubFetch(moved(0, SAVED));
    render(
      <Harness
        agent={SAVED}
        live={SAVED}
        onSaved={onSaved}
        onMoved={onMoved}
      />,
    );
    type("Address", ADDRESS.replace("http://localhost", "HTTP://LocalHost"));
    save();
    confirm();
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(SAVED));
    expect(onMoved).not.toHaveBeenCalled();
  });

  it("saves nothing once the list shows the agent at a later version, also for a move", () => {
    const fetchStub = stubFetch(moved(1));
    const { rerender } = render(<Harness agent={SAVED} live={SAVED} />);
    type("Address", OTHER);
    save();
    rerender(<Harness agent={SAVED} live={{ ...SAVED, version: 4 }} />);

    expect(confirmation()).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain(
      "stub was changed elsewhere",
    );
    fireEvent.submit(theForm());
    expect(fetchStub).not.toHaveBeenCalled();
  });
});
