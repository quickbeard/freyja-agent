import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { RemoveDialog } from "@/components/agents/remove-dialog";
import type { Agent } from "@/lib/agents";

// 005 User Story 5 (T101): the confirmation before a saved agent is removed.

const STUB: Agent = {
  id: "7c9e6679f0a54b3e8d2a1f4b6c8e0a2d",
  name: "stub",
  source: "saved",
  approval: "required",
  url: "http://localhost:9998/agents/recognizable-stub",
  version: 3,
  state: "reachable",
  checked_at: "2026-11-19T09:00:00Z",
  reason: null,
  card: null,
  pending_tasks: 2,
  created_at: "2026-11-19T08:50:00Z",
  updated_at: "2026-11-19T08:58:00Z",
};

const OTHER: Agent = {
  ...STUB,
  id: "1b2c3d4e5f60718293a4b5c6d7e8f901",
  name: "other",
  version: 1,
  pending_tasks: 0,
};

const CHANGED =
  "The agent was changed since this page read it. Nothing was changed.";
const NO_SUCH = "There is no agent with this id.";

const removed = (givenUp: number): Reply => ({ json: { given_up: givenUp } });

function refusal(status: number, code: string, message: string): Reply {
  return { status, json: { detail: { code, message } } };
}

const dialog = () => screen.getByRole("alertdialog");
const theButton = () =>
  within(dialog()).getByRole("button", { name: /^Remov/ }) as HTMLButtonElement;
const confirm = (detail = 1) => fireEvent.click(theButton(), { detail });

function calls() {
  return {
    onClose: vi.fn(),
    onRemoved: vi.fn(),
    onStale: vi.fn(),
    onUnknown: vi.fn(),
    onVanished: vi.fn(),
  };
}

/**
 * The dialog for `agent`, with the list's copy of it: the agent itself unless a third value
 * is given, which may be `undefined` for a list that cannot be read.
 */
function show(
  handlers: ReturnType<typeof calls>,
  agent: Agent | null = STUB,
  ...listed: [live?: Agent | null]
) {
  const live = listed.length > 0 ? listed[0] : agent;
  return <RemoveDialog agent={agent} live={live} {...handlers} />;
}

/** A request that waits for the test to answer it. */
function held() {
  let answer: (response: Response) => void = () => {};
  const fetchStub = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Promise<Response>((resolve) => {
        answer = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetchStub);
  return {
    fetchStub,
    answer: (givenUp: number) =>
      answer(
        new Response(JSON.stringify({ given_up: givenUp }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
  };
}

describe("RemoveDialog", () => {
  it("names the agent, and says the removal holds at once and gives its pending tasks up", () => {
    const fetchStub = stubFetch(removed(2));
    render(show(calls()));
    const said = dialog().textContent ?? "";
    expect(within(dialog()).getByRole("heading").textContent).toBe(
      "Remove stub?",
    );
    // It holds at once, also for runs in progress (FR-011).
    expect(said).toContain(
      "The removal holds at once, also for runs in progress",
    );
    // How many tasks are pending, as the list has it now, and that they will be given up.
    expect(said).toContain(
      "stub has 2 pending tasks now, and they will be given up",
    );
    expect(within(dialog()).getAllByRole("button")).toHaveLength(2);
    // Nothing is sent by opening it.
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("shows nothing when no agent is being removed", () => {
    render(show(calls(), null));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it.each([
    [0, "stub has no pending task now, so none will be given up."],
    [1, "stub has 1 pending task now, and it will be given up"],
    [5, "stub has 5 pending tasks now, and they will be given up"],
  ])("says how many tasks are pending as the list has it at that moment: %i", (pending, sentence) => {
    const handlers = calls();
    const { rerender } = render(show(handlers));
    expect(dialog().textContent).toContain("2 pending tasks now");

    // The list is read again behind the dialog, at the same version.
    rerender(show(handlers, STUB, { ...STUB, pending_tasks: pending }));
    expect(dialog().textContent).toContain(sentence);
    expect(dialog().textContent).not.toContain("2 pending tasks now");
  });

  it("says the number is its own while the list cannot be read", () => {
    const handlers = calls();
    const { rerender } = render(show(handlers));
    rerender(show(handlers, STUB, undefined));
    expect(dialog().textContent).toContain(
      "stub had 2 pending tasks when this was opened. Every task that is pending when the agent is removed will be given up.",
    );
    expect(dialog().textContent).not.toContain("pending tasks now");
    expect(theButton().disabled).toBe(false);
  });

  it("sends nothing when cancelled", async () => {
    const fetchStub = stubFetch(removed(2));
    const handlers = calls();
    render(show(handlers));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(handlers.onClose).toHaveBeenCalled());
    expect(fetchStub).not.toHaveBeenCalled();
    expect(handlers.onRemoved).not.toHaveBeenCalled();
  });

  it("removes once when confirmed, however often it is pressed, and says how many tasks were given up", async () => {
    const fetchStub = stubFetch(removed(2));
    const handlers = calls();
    render(show(handlers));
    expect(fetchStub).not.toHaveBeenCalled();
    confirm();
    confirm();

    await waitFor(() => expect(handlers.onRemoved).toHaveBeenCalledTimes(1));
    // The service's number, and the agent that was removed.
    expect(handlers.onRemoved).toHaveBeenCalledWith(STUB, 2);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe(`/api/agents/${STUB.id}?version=3`);
    expect(init?.method).toBe("DELETE");
    expect(init?.body).toBeUndefined();
    expect(handlers.onClose).toHaveBeenCalledTimes(1);
  });

  it("ignores the second press of a double press", () => {
    const fetchStub = stubFetch(removed(0));
    const handlers = calls();
    render(show(handlers));
    // The press that opened the dialog, pressed twice, lands here the second time.
    confirm(2);
    confirm(3);

    expect(fetchStub).not.toHaveBeenCalled();
    expect(handlers.onRemoved).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
  });

  it("says that it is removing, and takes no press while it does", async () => {
    const request = held();
    const handlers = calls();
    render(show(handlers));
    confirm();

    await waitFor(() => expect(theButton().textContent).toBe("Removing…"));
    expect(theButton().disabled).toBe(true);
    confirm();
    expect(request.fetchStub).toHaveBeenCalledTimes(1);
    request.answer(1);
    await waitFor(() =>
      expect(handlers.onRemoved).toHaveBeenCalledWith(STUB, 1),
    );
  });

  it("carries the version it opened with, and shows the agent as the list has it", async () => {
    const fetchStub = stubFetch(removed(4));
    const handlers = calls();
    // Opened at version 3. The list still has that version, with more tasks pending.
    render(show(handlers, STUB, { ...STUB, pending_tasks: 4 }));
    expect(dialog().textContent).toContain("4 pending tasks now");
    confirm();

    await waitFor(() =>
      expect(handlers.onRemoved).toHaveBeenCalledWith(STUB, 4),
    );
    expect(fetchStub.mock.calls[0][0]).toBe(`/api/agents/${STUB.id}?version=3`);
  });

  it("removes nothing once the list shows the agent at a later version, and says so", () => {
    const fetchStub = stubFetch(removed(2));
    const handlers = calls();
    const { rerender } = render(show(handlers));
    rerender(show(handlers, STUB, { ...STUB, version: 4, approval: "none" }));

    const alert = within(dialog()).getByRole("alert");
    expect(alert.textContent).toContain("stub was changed elsewhere");
    expect(theButton().disabled).toBe(true);
    confirm();
    expect(fetchStub).not.toHaveBeenCalled();

    // What it learned holds when the list then cannot be read.
    rerender(show(handlers, STUB, undefined));
    expect(within(dialog()).getByRole("alert").textContent).toContain(
      "stub was changed elsewhere",
    );
    expect(theButton().disabled).toBe(true);
    confirm();
    expect(fetchStub).not.toHaveBeenCalled();
    expect(handlers.onClose).not.toHaveBeenCalled();
  });

  it("closes when the agent is gone from the list, and says so to whoever holds the list", async () => {
    const fetchStub = stubFetch(removed(2));
    const handlers = calls();
    const { rerender } = render(show(handlers));
    rerender(show(handlers, STUB, null));

    await waitFor(() => expect(handlers.onClose).toHaveBeenCalledTimes(1));
    expect(handlers.onVanished).toHaveBeenCalledTimes(1);
    // Nothing was asked from here: it was removed elsewhere.
    expect(handlers.onVanished).toHaveBeenCalledWith(STUB, false);
    expect(fetchStub).not.toHaveBeenCalled();
    expect(handlers.onRemoved).not.toHaveBeenCalled();
  });

  it("says that its own removal got no answer when the agent then goes from the list", async () => {
    stubFetch({ fails: "Failed to fetch" });
    const handlers = calls();
    const { rerender } = render(show(handlers));
    confirm();
    await screen.findByRole("alert");
    expect(handlers.onVanished).not.toHaveBeenCalled();

    // The list is read again and no longer has the agent: the removal was carried out.
    rerender(show(handlers, STUB, null));
    await waitFor(() => expect(handlers.onClose).toHaveBeenCalledTimes(1));
    expect(handlers.onVanished).toHaveBeenCalledTimes(1);
    expect(handlers.onVanished).toHaveBeenCalledWith(STUB, true);
    expect(handlers.onRemoved).not.toHaveBeenCalled();
  });

  it.each([
    ["agent_changed", 409, CHANGED],
    ["agent_not_found", 404, NO_SUCH],
    [
      "agent_read_only",
      409,
      "An agent named in the environment cannot be removed here.",
    ],
  ])("shows the refusal as the service words it and has the list read again: %s", async (code, status, message) => {
    stubFetch(refusal(status, code, message));
    const handlers = calls();
    render(show(handlers));
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
    expect(handlers.onStale).toHaveBeenCalledTimes(1);
    expect(handlers.onUnknown).not.toHaveBeenCalled();
    expect(handlers.onRemoved).not.toHaveBeenCalled();
    // It stays, to say so; the list decides what becomes of it.
    expect(handlers.onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
  });

  it("says once that the agent was changed elsewhere, when the service refused and the list then shows it", async () => {
    stubFetch(refusal(409, "agent_changed", CHANGED));
    const handlers = calls();
    const { rerender } = render(show(handlers));
    confirm();
    await screen.findByRole("alert");

    rerender(show(handlers, STUB, { ...STUB, version: 4 }));
    const alerts = within(dialog()).getAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0].textContent).toContain("stub was changed elsewhere");
    expect(theButton().disabled).toBe(true);
  });

  it.each<[string, Reply]>([
    ["the request got no answer", { fails: "Failed to fetch" }],
    ["the web app's server could not pass it on", { status: 502, text: "" }],
  ])("says the outcome is not known, never that it failed, when %s", async (_when, reply) => {
    const fetchStub = stubFetch(reply);
    const handlers = calls();
    render(show(handlers));
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/not known/);
    expect(alert.textContent).toMatch(/removed/);
    expect(alert.textContent).not.toMatch(/fail|could not|was not|cannot/i);
    // The list says whether the agent is still there.
    expect(handlers.onUnknown).toHaveBeenCalledTimes(1);
    expect(handlers.onStale).not.toHaveBeenCalled();
    expect(handlers.onRemoved).not.toHaveBeenCalled();
    expect(handlers.onClose).not.toHaveBeenCalled();
    // Nothing is sent again by itself.
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("shows any other refusal in the service's words, as text, and leaves the list alone", async () => {
    const markup = '<img src=x onerror="alert(1)"> [link](http://elsewhere)';
    stubFetch(refusal(500, "internal", markup));
    const handlers = calls();
    render(show(handlers));
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(markup);
    expect(alert.querySelector("img, a")).toBeNull();
    expect(handlers.onStale).not.toHaveBeenCalled();
    expect(handlers.onUnknown).not.toHaveBeenCalled();
  });

  it("can be confirmed again after a refusal that left the agent as it is", async () => {
    const fetchStub = stubFetch(
      refusal(500, "internal", "The agent could not be removed."),
      removed(2),
    );
    const handlers = calls();
    render(show(handlers));
    confirm();
    await screen.findByRole("alert");
    confirm();

    await waitFor(() =>
      expect(handlers.onRemoved).toHaveBeenCalledWith(STUB, 2),
    );
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("tells whoever holds the list of a removal answered after it was closed, and closes no other dialog", async () => {
    const request = held();
    const handlers = calls();
    const { rerender } = render(show(handlers));
    confirm();
    await waitFor(() => expect(theButton().textContent).toBe("Removing…"));
    // Closed while the request is under way, and opened for another agent.
    rerender(show(handlers, null));
    rerender(show(handlers, OTHER));
    expect(dialog().textContent).toContain("Remove other?");

    request.answer(2);
    await waitFor(() =>
      expect(handlers.onRemoved).toHaveBeenCalledWith(STUB, 2),
    );
    // The dialog that is open now is another agent's, and stays.
    expect(handlers.onClose).not.toHaveBeenCalled();
    expect(dialog().textContent).toContain("Remove other?");
    expect(theButton().disabled).toBe(false);
  });

  it("starts anew for another agent: nothing of the one before is left", async () => {
    stubFetch(refusal(409, "agent_changed", CHANGED));
    const handlers = calls();
    const { rerender } = render(show(handlers));
    confirm();
    await screen.findByRole("alert");

    rerender(show(handlers, OTHER));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(dialog().textContent).toContain(
      "other has no pending task now, so none will be given up.",
    );
  });
});
