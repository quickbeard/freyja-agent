import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { AgentCard } from "@/components/agents/agent-card";
import type { Agent, AgentCheck } from "@/lib/agents";

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
  it("shows the agent's name and its own mode, Manual or Auto", () => {
    const { unmount } = render(<AgentCard agent={STUB} />);
    expect(screen.getByRole("heading", { name: "stub" })).toBeTruthy();
    expect(screen.getByText("Manual")).toBeTruthy();
    unmount();

    render(<AgentCard agent={HELLO} />);
    expect(screen.getByRole("heading", { name: "hello" })).toBeTruthy();
    expect(screen.getByText("Auto")).toBeTruthy();
    expect(screen.queryByText("Manual")).toBeNull();
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
    expect(said.queryByText("Auto")).toBeNull();
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

  it("offers Check, and Edit, Remove, and its mode's switch for a saved agent, and nothing in the card itself that changes or removes the agent", () => {
    for (const [shown, buttons, switches] of [
      [HELLO, ["Check"], []],
      [STUB, ["Check", "Edit", "Remove"], ["Auto mode for stub"]],
    ] as const) {
      const { unmount } = render(<AgentCard agent={shown} />);
      expect(
        screen.getAllByRole("button").map((button) => button.textContent),
      ).toEqual(buttons);
      expect(
        screen
          .queryAllByRole("switch")
          .map((found) => found.getAttribute("aria-label")),
      ).toEqual(switches);
      for (const role of ["link", "textbox", "combobox", "radio"]) {
        expect(screen.queryAllByRole(role)).toEqual([]);
      }
      unmount();
    }
  });
});

// Part 2 (T071): checking an agent from its card.

const USABLE: AgentCheck = {
  usable: true,
  reason: null,
  message: null,
  checked_at: "2026-11-19T10:00:00Z",
  recorded: true,
  card_changed: false,
};
const UNUSABLE: AgentCheck = {
  ...USABLE,
  usable: false,
  reason: "no_answer",
  message: "Nothing answered at the address.",
};
const BUSY = "This agent is already being checked. Wait for that check to end.";

function result(check: AgentCheck = USABLE): Reply {
  return { json: check };
}

function refusal(status: number, code: string, message: string): Reply {
  return { status, json: { detail: { code, message } } };
}

const check = () =>
  fireEvent.click(screen.getByRole("button", { name: "Check" }));

// An answer that waits until the test lets it go: the card is still being read.
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
    answer: (json: unknown, status = 200) =>
      answer(
        new Response(JSON.stringify(json), {
          status,
          headers: { "content-type": "application/json" },
        }),
      ),
  };
}

describe("AgentCard, checking", () => {
  it("has Check on every card of an agent that is in use, saved or from the environment", () => {
    const inUse = [
      HELLO,
      STUB,
      agent({ state: "unreachable", reason: "Nothing answered." }),
      agent({ state: "not_tried", checked_at: null }),
      { ...HELLO, state: "unreachable" as const, card: null },
    ];
    for (const shown of inUse) {
      const { unmount } = render(<AgentCard agent={shown} />);
      expect(screen.getByRole("button", { name: "Check" })).toBeTruthy();
      unmount();
    }
  });

  it("has no Check on a card of a saved agent that is not in use", () => {
    render(
      <AgentCard
        agent={agent({
          state: "not_in_use",
          checked_at: null,
          reason: "An agent named in the environment has this name.",
        })}
      />,
    );
    expect(screen.queryByRole("button", { name: "Check" })).toBeNull();
  });

  it("asks the service to check this agent, by its id and with nothing else", async () => {
    for (const shown of [STUB, HELLO]) {
      const fetchStub = stubFetch(result());
      const { unmount } = render(<AgentCard agent={shown} />);
      check();
      await screen.findByRole("status");
      expect(fetchStub).toHaveBeenCalledTimes(1);
      const [path, init] = fetchStub.mock.calls[0];
      expect(path).toBe(`/api/agents/${shown.id}/check`);
      expect(init?.method).toBe("POST");
      expect(init?.body).toBeUndefined();
      unmount();
    }
  });

  it("says that it is checking, and takes no second press while it does", async () => {
    const request = held();
    const onChecked = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} />);
    const button = screen.getByRole("button", { name: "Check" });

    // Two presses before the card has shown that it is checking: a double click.
    act(() => {
      button.click();
      button.click();
    });
    const busy = await screen.findByRole("button", { name: "Checking…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    expect(busy.getAttribute("aria-busy")).toBe("true");
    fireEvent.click(busy);
    expect(request.fetchStub).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("status")).toBeNull();

    request.answer(USABLE);
    await screen.findByRole("status");
    const again = screen.getByRole("button", { name: "Check" });
    expect((again as HTMLButtonElement).disabled).toBe(false);
    expect(request.fetchStub).toHaveBeenCalledTimes(1);
    expect(onChecked).toHaveBeenCalledTimes(1);
  });

  it("says the agent can be used, and has the list read again", async () => {
    stubFetch(result());
    const onChecked = vi.fn();
    const onGone = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} onGone={onGone} />);
    check();
    const said = await screen.findByRole("status");
    expect(said.textContent).toContain(
      "The check found that this agent can be used",
    );
    expect(said.textContent).toContain(when("2026-11-19T10:00:00Z"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(onChecked).toHaveBeenCalledTimes(1);
    expect(onGone).not.toHaveBeenCalled();
  });

  it("says why the agent cannot be used, in the service's sentence, and has the list read again", async () => {
    stubFetch(result(UNUSABLE));
    const onChecked = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} />);
    check();
    const said = await screen.findByRole("status");
    expect(said.textContent).toContain(
      "The check found that this agent cannot be used",
    );
    expect(
      within(said).getByText("Nothing answered at the address."),
    ).toBeTruthy();
    expect(onChecked).toHaveBeenCalledTimes(1);
  });

  it("shows the service's sentence as text, whatever it holds", async () => {
    const message = '<img src="x" onerror="alert(1)">';
    stubFetch(result({ ...UNUSABLE, message }));
    render(<AgentCard agent={STUB} />);
    check();
    const said = await screen.findByRole("status");
    expect(within(said).getByText(message)).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
  });

  it("says a result was not recorded, and has the list read again all the same", async () => {
    stubFetch(result({ ...USABLE, recorded: false }));
    const onChecked = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} />);
    check();
    const said = await screen.findByRole("status");
    expect(said.textContent).toContain("This result was not recorded");
    expect(onChecked).toHaveBeenCalledTimes(1);
  });

  it("says the agent's card changed, and that its tools follow from the next run", async () => {
    stubFetch(result({ ...USABLE, card_changed: true }));
    render(<AgentCard agent={STUB} />);
    check();
    const said = await screen.findByRole("status");
    expect(said.textContent).toContain("The agent's card changed.");
    expect(said.textContent).toContain(
      "Its tools are described from the new details from the next run.",
    );
  });

  it("shows a busy refusal as the service words it, and leaves the list alone", async () => {
    stubFetch(refusal(409, "check_busy", BUSY));
    const onChecked = vi.fn();
    const onGone = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} onGone={onGone} />);
    check();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(BUSY);
    expect(screen.queryByRole("status")).toBeNull();
    expect(onChecked).not.toHaveBeenCalled();
    expect(onGone).not.toHaveBeenCalled();
    // The card can be checked again once that check has ended.
    expect(
      (screen.getByRole("button", { name: "Check" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("shows that the agent is gone as the service words it, and has the list read again", async () => {
    stubFetch(
      refusal(404, "agent_not_found", "There is no agent with this id."),
    );
    const onChecked = vi.fn();
    const onGone = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} onGone={onGone} />);
    check();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("There is no agent with this id.");
    expect(onGone).toHaveBeenCalledTimes(1);
    expect(onChecked).not.toHaveBeenCalled();
  });

  it("says the outcome is not known when a check got no answer, and has the list read again", async () => {
    stubFetch({ fails: "Failed to fetch" });
    const onChecked = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} />);
    check();
    const alert = await screen.findByRole("alert");
    // A check may record what it read, so it never says that it failed (FR-004).
    expect(alert.textContent).toBe(
      "The request got no answer, so it is not known whether the agent was checked. The list is being read again: it shows the agent as the service has it now.",
    );
    // Whether the check was carried out is not known: the list says what is so.
    expect(onChecked).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("takes back what the last check said when the next one starts", async () => {
    const fetchStub = stubFetch(
      result(UNUSABLE),
      refusal(409, "check_busy", BUSY),
      result(),
    );
    render(<AgentCard agent={STUB} />);
    check();
    await screen.findByText("Nothing answered at the address.");

    check();
    await screen.findByRole("alert");
    expect(screen.queryByText("Nothing answered at the address.")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();

    check();
    const said = await screen.findByRole("status");
    expect(said.textContent).toContain("can be used");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetchStub).toHaveBeenCalledTimes(3);
  });

  it("keeps what the check said when the list hands the card its agent anew", async () => {
    stubFetch(result({ ...USABLE, card_changed: true }));
    const { rerender } = render(
      <AgentCard agent={agent({ state: "unreachable", reason: "Down." })} />,
    );
    check();
    await screen.findByRole("status");

    // The list was read again, and says what the check found.
    rerender(
      <AgentCard
        agent={agent({ state: "reachable", checked_at: USABLE.checked_at })}
      />,
    );
    expect(screen.queryByRole("note", { name: "Warning" })).toBeNull();
    expect(screen.getByRole("status").textContent).toContain(
      "The agent's card changed.",
    );
  });

  it("has the list read again when the answer comes after the card is gone, and nothing breaks", async () => {
    const request = held();
    const onChecked = vi.fn();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount } = render(
      <AgentCard agent={STUB} onChecked={onChecked} />,
    );
    check();
    await screen.findByRole("button", { name: "Checking…" });
    unmount();

    request.answer(UNUSABLE);
    await waitFor(() => expect(onChecked).toHaveBeenCalledTimes(1));
    expect(document.body.textContent).toBe("");
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it("has the list read again when the service says the agent is not in use", async () => {
    // Another instance's environment took the agent's name: this card does not show it yet.
    stubFetch({
      status: 409,
      json: {
        detail: {
          code: "agent_read_only",
          message: "This saved agent is not in use, so it cannot be checked.",
        },
      },
    });
    const onChecked = vi.fn();
    const onGone = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} onGone={onGone} />);
    check();
    const said = await screen.findByRole("alert");
    expect(said.textContent).toBe(
      "This saved agent is not in use, so it cannot be checked.",
    );
    expect(onChecked).toHaveBeenCalledTimes(1);
    expect(onGone).not.toHaveBeenCalled();
  });
});

// Part 3 (T086): Edit, for a saved agent that is in use.

describe("AgentCard, Edit", () => {
  it("has Edit on the card of a saved agent that is in use, whatever was last found of it", () => {
    const inUse = [
      STUB,
      agent({ approval: "none" }),
      agent({ state: "unreachable", reason: "Nothing answered." }),
      agent({ state: "not_tried", checked_at: null }),
    ];
    for (const shown of inUse) {
      const { unmount } = render(<AgentCard agent={shown} />);
      expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
      unmount();
    }
  });

  it("has no Edit on an environment agent's card", () => {
    for (const shown of [
      HELLO,
      { ...HELLO, approval: "required" as const },
      { ...HELLO, state: "unreachable" as const, card: null },
    ]) {
      const { unmount } = render(<AgentCard agent={shown} />);
      expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
      unmount();
    }
  });

  it("has no Edit on the card of a saved agent that is not in use", () => {
    render(
      <AgentCard
        agent={agent({
          state: "not_in_use",
          checked_at: null,
          reason: "An agent named in the environment has this name.",
        })}
      />,
    );
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("hands its agent to whoever opens the form, and asks the service nothing", () => {
    const fetchStub = stubFetch(result());
    const onEdit = vi.fn();
    const shown = agent({ approval: "none", version: 5 });
    render(<AgentCard agent={shown} onEdit={onEdit} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit).toHaveBeenCalledWith(shown);
    // Nothing on the card itself changes the agent: the form does, behind a confirmation.
    expect(fetchStub).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

// Part 4 (T102): Remove, and the number of pending tasks.

describe("AgentCard, Remove and pending tasks", () => {
  const NOT_IN_USE = agent({
    state: "not_in_use",
    checked_at: null,
    reason: "An agent named in the environment has this name.",
  });
  const buttons = () =>
    screen.queryAllByRole("button").map((button) => button.textContent);

  it("has Remove on a saved agent's card, whatever was last found of it", () => {
    for (const shown of [
      STUB,
      agent({ approval: "none" }),
      agent({ state: "unreachable", reason: "Nothing answered." }),
      agent({ state: "not_tried", checked_at: null }),
    ]) {
      const { unmount } = render(<AgentCard agent={shown} />);
      expect(buttons()).toEqual(["Check", "Edit", "Remove"]);
      unmount();
    }
  });

  it("has Remove and nothing else on the card of a saved agent that is not in use", () => {
    render(<AgentCard agent={NOT_IN_USE} />);
    expect(buttons()).toEqual(["Remove"]);
  });

  it("has no Remove on an environment agent's card", () => {
    for (const shown of [
      HELLO,
      { ...HELLO, approval: "required" as const },
      { ...HELLO, state: "unreachable" as const, card: null },
    ]) {
      const { unmount } = render(<AgentCard agent={shown} />);
      expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
      expect(buttons()).toEqual(["Check"]);
      unmount();
    }
  });

  it("hands its agent to whoever asks to confirm the removal, and asks the service nothing", () => {
    const fetchStub = stubFetch(result());
    const onRemove = vi.fn();
    for (const shown of [agent({ version: 5, pending_tasks: 2 }), NOT_IN_USE]) {
      const { unmount } = render(
        <AgentCard agent={shown} onRemove={onRemove} />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Remove" }));
      expect(onRemove).toHaveBeenLastCalledWith(shown);
      // Nothing on the card itself removes the agent: a dialog asks first.
      expect(screen.queryByRole("alertdialog")).toBeNull();
      unmount();
    }
    expect(onRemove).toHaveBeenCalledTimes(2);
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it.each([
    [1, "1"],
    [3, "3"],
    [12, "12"],
  ])("shows a saved agent's number of pending tasks when it is not zero: %i", (pending, shown) => {
    render(<AgentCard agent={agent({ pending_tasks: pending })} />);
    const label = screen.getByText("Pending tasks");
    expect(label.nextElementSibling?.textContent).toBe(shown);
  });

  it("shows the number for a saved agent that is not in use, too", () => {
    render(<AgentCard agent={{ ...NOT_IN_USE, pending_tasks: 2 }} />);
    expect(
      screen.getByText("Pending tasks").nextElementSibling?.textContent,
    ).toBe("2");
  });

  it("says nothing of pending tasks when a saved agent has none, nor on an environment agent's card", () => {
    for (const shown of [
      STUB,
      agent({ pending_tasks: 0, approval: "none" }),
      { ...HELLO, pending_tasks: 4 },
    ]) {
      const { unmount } = render(<AgentCard agent={shown} />);
      expect(screen.queryByText("Pending tasks")).toBeNull();
      unmount();
    }
  });
});

// Feature 010: the agent's own mode, Manual or Auto, switched from its card.

const autoSwitch = () =>
  screen.getByRole("switch", { name: "Auto mode for stub" });

function sentBodies(fetchStub: ReturnType<typeof stubFetch>) {
  return fetchStub.mock.calls.map(([input, init]) => [
    String(input),
    init?.method ?? "GET",
    init?.body === undefined ? undefined : JSON.parse(String(init.body)),
  ]);
}

describe("AgentCard, its own mode", () => {
  it("shows an environment agent's mode and a not-in-use agent's without a switch", () => {
    for (const shown of [
      HELLO,
      { ...HELLO, approval: "required" as const },
      agent({
        state: "not_in_use",
        reason: "An agent named in the environment has this name.",
      }),
    ]) {
      const { unmount } = render(<AgentCard agent={shown} />);
      expect(screen.queryAllByRole("switch")).toEqual([]);
      expect(
        screen.getByText(shown.approval === "none" ? "Auto" : "Manual"),
      ).toBeTruthy();
      unmount();
    }
  });

  it("asks first when the switch is pressed, and sends nothing when the question is canceled", async () => {
    const fetchStub = stubFetch({ json: {} });
    render(<AgentCard agent={STUB} />);
    expect((autoSwitch() as HTMLElement).getAttribute("aria-checked")).toBe(
      "false",
    );
    fireEvent.click(autoSwitch());
    const asked = await screen.findByRole("alertdialog", {
      name: "Set stub to Auto?",
    });
    expect(asked.textContent).toContain(
      "every message to stub will be sent without approval",
    );
    expect(fetchStub).not.toHaveBeenCalled();
    fireEvent.click(within(asked).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("sends the version the card showed and the new choice once confirmed, and has the list read again", async () => {
    const fetchStub = stubFetch({
      json: { agent: { ...STUB, approval: "none", version: 3 }, given_up: 0 },
    });
    const onChecked = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} />);
    fireEvent.click(autoSwitch());
    const asked = await screen.findByRole("alertdialog");
    // The second press of a double press is not a decision.
    fireEvent.click(
      within(asked).getByRole("button", { name: "Set to Auto" }),
      {
        detail: 2,
      },
    );
    expect(fetchStub).not.toHaveBeenCalled();
    fireEvent.click(within(asked).getByRole("button", { name: "Set to Auto" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(sentBodies(fetchStub)).toEqual([
      [`/api/agents/${STUB.id}`, "PATCH", { version: 2, approval: "none" }],
    ]);
    expect(onChecked).toHaveBeenCalled();
  });

  it("sets an Auto agent back to Manual, saying that it holds at once", async () => {
    const fetchStub = stubFetch({
      json: { agent: { ...STUB, version: 4 }, given_up: 0 },
    });
    render(<AgentCard agent={agent({ approval: "none", version: 3 })} />);
    expect(autoSwitch().getAttribute("aria-checked")).toBe("true");
    fireEvent.click(autoSwitch());
    const asked = await screen.findByRole("alertdialog", {
      name: "Set stub to Manual?",
    });
    expect(asked.textContent).toContain("This holds at once");
    fireEvent.click(
      within(asked).getByRole("button", { name: "Set to Manual" }),
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(sentBodies(fetchStub)).toEqual([
      [`/api/agents/${STUB.id}`, "PATCH", { version: 3, approval: "required" }],
    ]);
  });

  it("sends nothing once the list shows the agent at a later version, and says so", async () => {
    const fetchStub = stubFetch({ json: {} });
    const { rerender } = render(<AgentCard agent={STUB} />);
    fireEvent.click(autoSwitch());
    await screen.findByRole("alertdialog");
    rerender(<AgentCard agent={agent({ version: 3 })} />);
    const asked = screen.getByRole("alertdialog");
    expect(within(asked).getByRole("alert").textContent).toContain(
      "was changed elsewhere while this was open",
    );
    const confirm = within(asked).getByRole("button", {
      name: "Set to Auto",
    }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(confirm);
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("shows the service's refusal, and has the list read again", async () => {
    stubFetch(
      refusal(
        409,
        "agent_changed",
        "The agent was changed since this page read it. Nothing was changed.",
      ),
    );
    const onChecked = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} />);
    fireEvent.click(autoSwitch());
    const asked = await screen.findByRole("alertdialog");
    fireEvent.click(within(asked).getByRole("button", { name: "Set to Auto" }));
    expect((await within(asked).findByRole("alert")).textContent).toBe(
      "The agent was changed since this page read it. Nothing was changed.",
    );
    expect(onChecked).toHaveBeenCalled();
  });

  it("says that the outcome is not known when no answer came, never that it failed", async () => {
    stubFetch({ fails: "network down" });
    const onChecked = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} />);
    fireEvent.click(autoSwitch());
    const asked = await screen.findByRole("alertdialog");
    fireEvent.click(within(asked).getByRole("button", { name: "Set to Auto" }));
    expect((await within(asked).findByRole("alert")).textContent).toContain(
      "it is not known whether the agent was changed",
    );
    expect(onChecked).toHaveBeenCalled();
  });

  it("closes when the service says the agent is gone, and has the list read again", async () => {
    stubFetch(
      refusal(404, "agent_not_found", "There is no agent with this id."),
    );
    const onChecked = vi.fn();
    render(<AgentCard agent={STUB} onChecked={onChecked} />);
    fireEvent.click(autoSwitch());
    const asked = await screen.findByRole("alertdialog");
    fireEvent.click(within(asked).getByRole("button", { name: "Set to Auto" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(onChecked).toHaveBeenCalled();
  });

  it("says that an Auto agent is held to Manual while agent approvals are Manual", () => {
    const { unmount } = render(
      <AgentCard agent={agent({ approval: "none" })} mode="manual" />,
    );
    expect(screen.getByText(/^Held to Manual/)).toBeTruthy();
    unmount();
    for (const [shown, mode] of [
      [agent({ approval: "none" }), "auto"],
      [STUB, "manual"],
      [agent({ approval: "none" }), undefined],
    ] as const) {
      const { unmount: gone } = render(<AgentCard agent={shown} mode={mode} />);
      expect(screen.queryByText(/^Held to Manual/)).toBeNull();
      gone();
    }
  });
});
