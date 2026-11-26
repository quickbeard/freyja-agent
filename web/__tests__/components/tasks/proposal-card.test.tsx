import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { withoutDigits } from "@/__tests__/tasks-testing";
import { ProposalCard } from "@/components/tasks/proposal-card";
import { ApiError } from "@/lib/api";
import { readJson } from "@/lib/json";
import type { ActionRequest, PendingDecision } from "@/lib/tasks";

const NOTIFY: ActionRequest = {
  name: "send_notification",
  server: null,
  agent: null,
  args: { channel: "#ops", message: "The report is ready." },
  description: "Send a notification to a channel.",
  allowed_decisions: ["approve", "edit", "reject"],
};

const HANDOFF: ActionRequest = {
  name: "handoff_to_stub",
  server: null,
  agent: "stub",
  args: { message: "Compile the report." },
  description: "Hand the work to the stub agent.",
  allowed_decisions: ["approve", "reject"],
};

const LOOKUP: ActionRequest = {
  name: "record_note",
  server: "notes",
  agent: null,
  args: { note: "<b>x</b>" },
  description: null,
  allowed_decisions: ["approve", "edit", "reject"],
};

function proposal(...actions: ActionRequest[]): PendingDecision {
  return {
    interrupt_id: "int-1",
    action_requests: actions,
    allowed_decisions: ["approve", "edit", "reject"],
  };
}

function renderCard(
  pending: PendingDecision,
  send = vi.fn(async () => {}),
  onStale = vi.fn(),
) {
  render(<ProposalCard pending={pending} send={send} onStale={onStale} />);
  return { send, onStale };
}

const button = (name: string) => screen.getByRole("button", { name });

describe("the proposal card", () => {
  it("on a task another agent started, says who started the run and that no person asked for it", () => {
    render(
      <ProposalCard
        pending={proposal(HANDOFF)}
        send={vi.fn(async () => {})}
        onStale={vi.fn()}
        caller={{ name: "local", canceled: false, configured: true }}
      />,
    );
    const card = screen.getByRole("region", {
      name: "Freyja proposes an action that needs your decision",
    });
    expect(card.textContent).toContain(
      "Started by an agent on this machine. No person asked for this action: the agent's task led Freyja to propose it.",
    );
  });

  it("says the same of several actions a caller's task led to", () => {
    render(
      <ProposalCard
        pending={proposal(NOTIFY, HANDOFF)}
        send={vi.fn(async () => {})}
        onStale={vi.fn()}
        caller={{ name: "local", canceled: false, configured: true }}
      />,
    );
    expect(
      screen.getByRole("region", {
        name: "Freyja proposes 2 actions that need your decision",
      }).textContent,
    ).toContain(
      "Started by an agent on this machine. No person asked for these actions: the agent's task led Freyja to propose them.",
    );
  });

  it("on a task a person started, says nothing of who started it", () => {
    renderCard(proposal(NOTIFY, HANDOFF));
    expect(screen.queryByText(/Started by|No person asked/)).toBeNull();
  });

  it("shows each action's name, where it goes, its description, and its arguments in full", () => {
    renderCard(proposal(NOTIFY, HANDOFF, LOOKUP));
    const notify = screen.getByRole("article", {
      name: "Proposed action send_notification",
    });
    expect(notify.textContent).toContain("Send a notification to a channel.");
    expect(notify.textContent).toContain('"channel": "#ops"');
    expect(notify.textContent).toContain('"message": "The report is ready."');
    expect(
      screen.getByRole("article", { name: "Proposed action handoff_to_stub" })
        .textContent,
    ).toContain("Sent to agent stub");
    const lookup = screen.getByRole("article", {
      name: "Proposed action record_note",
    });
    expect(lookup.textContent).toContain("Runs on tool server notes");
    // Text, never markup.
    expect(lookup.querySelector("b")).toBeNull();
    expect(lookup.textContent).toContain('"note": "<b>x</b>"');
  });

  it("offers only the decisions the service allows for each action", () => {
    renderCard(proposal(HANDOFF));
    expect(button("Approve handoff_to_stub")).toBeTruthy();
    expect(button("Reject handoff_to_stub")).toBeTruthy();
    expect(
      screen.queryByRole("button", {
        name: "Edit the arguments of handoff_to_stub",
      }),
    ).toBeNull();
  });

  it("sends one approval for one press, also pressed twice", async () => {
    let finish: () => void = () => {};
    const send = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    renderCard(proposal(NOTIFY), send);
    const approve = button("Approve send_notification");
    act(() => {
      fireEvent.click(approve);
      fireEvent.click(approve);
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith([{ type: "approve" }]);
    await act(async () => finish());
  });

  it("sends an edit as entered, and does not send text that is not a JSON object", async () => {
    const { send } = renderCard(proposal(NOTIFY));
    fireEvent.click(button("Edit the arguments of send_notification"));
    const box = screen.getByLabelText(
      "Arguments for send_notification, as JSON",
    ) as HTMLTextAreaElement;
    // It starts from the proposed arguments.
    expect(JSON.parse(box.value)).toEqual(NOTIFY.args);

    fireEvent.change(box, { target: { value: '["#ops"]' } });
    expect(screen.getByRole("alert").textContent).toMatch(/JSON object/);
    const approve = button("Approve send_notification with these arguments");
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(approve);
    expect(send).not.toHaveBeenCalled();

    const edited =
      '{"channel": "#ops-staging", "message": "The report is ready."}';
    fireEvent.change(box, { target: { value: edited } });
    fireEvent.click(button("Approve send_notification with these arguments"));
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith([
        {
          type: "edit",
          args: { channel: "#ops-staging", message: "The report is ready." },
        },
      ]),
    );
  });

  it("sends a rejection with its reason, or with none", async () => {
    const { send } = renderCard(proposal(NOTIFY));
    fireEvent.click(button("Reject send_notification"));
    fireEvent.click(button("Reject send_notification now"));
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith([{ type: "reject" }]),
    );

    fireEvent.change(
      screen.getByLabelText(
        "Why send_notification is rejected (optional, Freyja is told)",
      ),
      { target: { value: "Not before the review." } },
    );
    fireEvent.click(button("Reject send_notification now"));
    await waitFor(() =>
      expect(send).toHaveBeenLastCalledWith([
        { type: "reject", message: "Not before the review." },
      ]),
    );
  });

  it("does not send a reason of more than 4,000 characters", () => {
    const { send } = renderCard(proposal(NOTIFY));
    fireEvent.click(button("Reject send_notification"));
    fireEvent.change(
      screen.getByLabelText(
        "Why send_notification is rejected (optional, Freyja is told)",
      ),
      { target: { value: "x".repeat(4001) } },
    );
    expect(screen.getByText("4001 of 4000 characters")).toBeTruthy();
    const reject = button("Reject send_notification now");
    expect((reject as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(reject);
    expect(send).not.toHaveBeenCalled();
  });

  it("with two actions sends nothing until each has a decision, in the service's order", async () => {
    const { send } = renderCard(proposal(NOTIFY, HANDOFF));
    const all = button("Send the 2 decisions");
    expect((all as HTMLButtonElement).disabled).toBe(true);

    // Decided out of order; sent in the service's.
    fireEvent.click(button("Reject handoff_to_stub"));
    expect((all as HTMLButtonElement).disabled).toBe(true);
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(button("Approve send_notification"));
    expect(
      button("Approve send_notification").getAttribute("aria-pressed"),
    ).toBe("true");
    expect((all as HTMLButtonElement).disabled).toBe(false);
    expect(send).not.toHaveBeenCalled();

    fireEvent.click(all);
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith([
        { type: "approve" },
        { type: "reject" },
      ]),
    );
  });

  it("sends nothing for Enter in a field", () => {
    const { send } = renderCard(proposal(NOTIFY));
    fireEvent.click(button("Edit the arguments of send_notification"));
    const box = screen.getByLabelText(
      "Arguments for send_notification, as JSON",
    );
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.keyUp(box, { key: "Enter" });
    fireEvent.click(button("Reject send_notification"));
    const reason = screen.getByLabelText(
      "Why send_notification is rejected (optional, Freyja is told)",
    );
    fireEvent.keyDown(reason, { key: "Enter" });
    expect(box.closest("form")).toBeNull();
    expect(send).not.toHaveBeenCalled();
  });

  it("shows the service's reason beside a refused edit, which keeps its text", async () => {
    const send = vi.fn(async () => {
      throw new ApiError(
        "Some values were refused.",
        422,
        "invalid",
        [{ field: "message", message: "Field required" }],
        [["body", "decisions", 0, "args", "message"]],
      );
    });
    renderCard(proposal(NOTIFY), send);
    fireEvent.click(button("Edit the arguments of send_notification"));
    const box = screen.getByLabelText(
      "Arguments for send_notification, as JSON",
    ) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: '{"channel": "#ops"}' } });
    fireEvent.click(button("Approve send_notification with these arguments"));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "message: Field required",
    );
    expect(box.value).toBe('{"channel": "#ops"}');
    expect(box.getAttribute("aria-invalid")).toBe("true");
  });

  it.each([
    ["proposal_mismatch", /no longer the pending one/],
    ["nothing_pending", /Nothing is pending/],
    ["run_active", /A run is in progress/],
  ])("says a %s refusal and has the task read again", async (code, said) => {
    const send = vi.fn(async () => {
      throw new ApiError("Refused.", 409, code);
    });
    const { onStale } = renderCard(proposal(NOTIFY), send);
    fireEvent.click(button("Approve send_notification"));
    await waitFor(() => expect(onStale).toHaveBeenCalledTimes(1));
    expect(onStale.mock.calls[0][0]).toMatch(said);
  });

  // Edge Cases, "Two people decide the same proposal" (T115): a run starts on a proposal
  // only when it is decided, so one in progress most likely carries out another window's.
  it("says that a proposal refused for a run in progress was most likely decided in another window", async () => {
    const send = vi.fn(async () => {
      throw new ApiError(
        "A run is already active on this thread.",
        409,
        "run_active",
      );
    });
    const { onStale } = renderCard(proposal(NOTIFY), send);
    fireEvent.click(button("Approve send_notification"));
    await waitFor(() => expect(onStale).toHaveBeenCalledTimes(1));
    expect(onStale.mock.calls[0][0]).toBe(
      "A run is in progress on this task, so the decision was not applied: the proposal was most likely decided in another window. Nothing ran because of this decision. The task is shown as it is now.",
    );
  });

  // Feature 009, request 4 (T069): the agent that asked for the action canceled its task.
  it("says in the console's words that an approval was refused because the requesting agent canceled, and still offers rejecting", async () => {
    const send = vi.fn(async () => {
      throw new ApiError(
        "The agent that asked for this canceled its task, so the action is not carried out. The proposal can only be rejected.",
        409,
        "requester_gone",
      );
    });
    const onRefused = vi.fn();
    const onStale = vi.fn();
    render(
      <ProposalCard
        pending={proposal(NOTIFY)}
        send={send}
        onStale={onStale}
        onRefused={onRefused}
      />,
    );
    fireEvent.click(button("Approve send_notification"));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The agent that started this task canceled it, so this action can no longer be approved. Reject it to close the proposal; nothing of it runs.",
    );
    expect(onRefused).toHaveBeenCalledTimes(1);
    expect(onStale).not.toHaveBeenCalled();
    fireEvent.click(button("Reject send_notification"));
    fireEvent.click(button("Reject send_notification now"));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1]).toEqual([[{ type: "reject" }]]);
  });

  it("says that an action which no longer needs approval can be rejected only once the agents' mode is Manual", async () => {
    const send = vi.fn(async () => {
      throw new ApiError(
        "The agent that asked for this canceled its task, so the action is not carried out. The proposal can only be rejected.",
        409,
        "requester_gone",
      );
    });
    render(
      <ProposalCard
        pending={proposal({ ...HANDOFF, allowed_decisions: ["approve"] })}
        caller={{ name: "local", canceled: true, configured: true }}
        send={send}
        onStale={vi.fn()}
      />,
    );
    const said =
      "The agent that started this task canceled it, so this action can no longer be approved. It no longer needs approval, so it can be rejected only once the agents' approval mode is Manual again.";
    expect(screen.getByText(said)).toBeTruthy();
    fireEvent.click(button("Approve handoff_to_stub"));
    expect((await screen.findByRole("alert")).textContent).toBe(said);
  });

  it("says before any press that the requesting agent canceled, on the card of a canceled caller's task", () => {
    render(
      <ProposalCard
        pending={proposal(NOTIFY)}
        caller={{ name: "local", canceled: true, configured: true }}
        send={vi.fn(async () => {})}
        onStale={vi.fn()}
      />,
    );
    expect(
      screen.getByText(
        "The agent that started this task canceled it, so this action can no longer be approved. Reject it to close the proposal; nothing of it runs.",
      ),
    ).toBeTruthy();
    expect(button("Reject send_notification")).toBeTruthy();
  });

  it("says that the outcome is not known when no answer came", async () => {
    const send = vi.fn(async () => {
      throw new ApiError(
        "The Freyja service cannot be reached.",
        0,
        "unreachable",
      );
    });
    const { onStale } = renderCard(proposal(NOTIFY), send);
    fireEvent.click(button("Approve send_notification"));
    await waitFor(() => expect(onStale).toHaveBeenCalledTimes(1));
    expect(onStale.mock.calls[0][0]).toMatch(/not known/);
    expect(onStale.mock.calls[0][0]).not.toMatch(/failed/);
  });

  it("names every control by what it does and on which action", () => {
    renderCard(proposal(NOTIFY, HANDOFF));
    const names = screen.getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual([
      "Approve send_notification",
      "Edit the arguments of send_notification",
      "Reject send_notification",
      "Approve handoff_to_stub",
      "Reject handoff_to_stub",
      "Send the 2 decisions",
    ]);
  });
});

// Review of Phase 5.

describe("the proposal card, further", () => {
  it("shows a refused edit beside the action it is about, and only there", async () => {
    const send = vi.fn(async () => {
      throw new ApiError(
        "Some values were refused.",
        422,
        "invalid",
        [{ field: "channel", message: "Input should be a valid string" }],
        [["body", "decisions", 1, "args", "channel"]],
      );
    });
    const second = { ...NOTIFY, name: "send_digest" };
    renderCard(proposal(NOTIFY, second), send);
    fireEvent.click(button("Edit the arguments of send_notification"));
    fireEvent.click(button("Edit the arguments of send_digest"));
    fireEvent.click(button("Send the 2 decisions"));

    const digest = screen.getByRole("article", {
      name: "Proposed action send_digest",
    });
    await waitFor(() =>
      expect(within(digest).getByRole("alert").textContent).toBe(
        "channel: Input should be a valid string",
      ),
    );
    const first = screen.getByRole("article", {
      name: "Proposed action send_notification",
    });
    expect(within(first).queryByRole("alert")).toBeNull();
  });

  it("offers nothing but reject when the service allows only that", () => {
    renderCard(proposal({ ...NOTIFY, allowed_decisions: ["reject"] }));
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Reject send_notification",
    ]);
  });

  it("sends a reason as it was entered", async () => {
    const { send } = renderCard(proposal(NOTIFY));
    fireEvent.click(button("Reject send_notification"));
    fireEvent.change(
      screen.getByLabelText(
        "Why send_notification is rejected (optional, Freyja is told)",
      ),
      { target: { value: "  Not now.\n  " } },
    );
    fireEvent.click(button("Reject send_notification now"));
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith([
        { type: "reject", message: "  Not now.\n  " },
      ]),
    );
  });

  it("counts a reason's characters as the service does", () => {
    renderCard(proposal(NOTIFY));
    fireEvent.click(button("Reject send_notification"));
    // 4,000 characters, each two UTF-16 units: the service takes it.
    fireEvent.change(
      screen.getByLabelText(
        "Why send_notification is rejected (optional, Freyja is told)",
      ),
      { target: { value: "😀".repeat(4000) } },
    );
    expect(screen.getByText("4000 of 4000 characters")).toBeTruthy();
    expect(
      (button("Reject send_notification now") as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("says that the plain Approve sends the arguments as proposed while they are edited", () => {
    renderCard(proposal(NOTIFY));
    fireEvent.click(button("Edit the arguments of send_notification"));
    expect(button("Approve send_notification as proposed")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Approve send_notification" }),
    ).toBeNull();
  });

  it("says each control's state once: open for one action, chosen for several", () => {
    renderCard(proposal(NOTIFY));
    const edit = button("Edit the arguments of send_notification");
    expect(edit.getAttribute("aria-expanded")).toBe("false");
    expect(edit.hasAttribute("aria-pressed")).toBe(false);
  });

  it("says each control's state once for several actions", () => {
    renderCard(proposal(NOTIFY, { ...NOTIFY, name: "send_digest" }));
    const edit = button("Edit the arguments of send_digest");
    expect(edit.getAttribute("aria-pressed")).toBe("false");
    expect(edit.hasAttribute("aria-expanded")).toBe(false);
  });
});

// FR-014, FR-015 (T099): a whole number beyond 2^53, such as a 64-bit id. A browser that
// keeps a number's own text shows it as it was proposed and sends it as it was entered.
describe("the proposal card and a number beyond what a browser holds", () => {
  const record = (text: string): ActionRequest => ({
    name: "close_record",
    server: "records",
    agent: null,
    // As the page has the arguments from the service's answer.
    args: readJson(text) as ActionRequest["args"],
    description: null,
    allowed_decisions: ["approve", "edit", "reject"],
  });
  const INEXACT = /too large for a browser to hold exactly/;

  it("shows a proposed 64-bit id in full, digit for digit, and says nothing of rounding", () => {
    renderCard(
      proposal(record('{"id": 1234567890123456789, "note": "Closed."}')),
    );
    const shown = screen.getByRole("article", {
      name: "Proposed action close_record",
    });
    expect(shown.textContent).toContain('"id": 1234567890123456789,');
    expect(screen.queryByText(INEXACT)).toBeNull();
  });

  it("starts the edit from the proposed digits, and sends the other arguments changed and the id as it was", async () => {
    const { send } = renderCard(
      proposal(record('{"id": 1234567890123456789012, "note": "Closed."}')),
    );
    fireEvent.click(button("Edit the arguments of close_record"));
    const box = screen.getByLabelText(
      "Arguments for close_record, as JSON",
    ) as HTMLTextAreaElement;
    expect(box.value).toContain('"id": 1234567890123456789012,');

    fireEvent.change(box, {
      target: { value: box.value.replace("Closed.", "Closed today.") },
    });
    fireEvent.click(button("Approve close_record with these arguments"));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const [[decisions]] = send.mock.calls as unknown as [[unknown]];
    expect(JSON.stringify(decisions)).toBe(
      '[{"type":"edit","args":{"id":1234567890123456789012,"note":"Closed today."}}]',
    );
  });

  it("sends a 64-bit id a person entered as it was entered", async () => {
    const { send } = renderCard(proposal(NOTIFY));
    fireEvent.click(button("Edit the arguments of send_notification"));
    fireEvent.change(
      screen.getByLabelText("Arguments for send_notification, as JSON"),
      {
        target: { value: '{"channel": "#ops", "ticket": 1234567890123456789}' },
      },
    );
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(button("Approve send_notification with these arguments"));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const [[decisions]] = send.mock.calls as unknown as [[unknown]];
    expect(JSON.stringify(decisions)).toBe(
      '[{"type":"edit","args":{"channel":"#ops","ticket":1234567890123456789}}]',
    );
  });
});

// The same in a browser that reads JSON without giving a number's own text: the page can
// neither show such an argument as it was proposed nor send one as it was entered, so it
// says so, sends no such edit, and still approves as proposed.
describe("the proposal card where a number's digits are not kept", () => {
  let restore = () => {};
  beforeEach(() => {
    restore = withoutDigits();
  });
  afterEach(() => restore());

  const record = (text: string): ActionRequest => ({
    name: "close_record",
    server: "records",
    agent: null,
    // What such a browser has left of the id once it has read the service's answer.
    args: JSON.parse(text),
    description: null,
    allowed_decisions: ["approve", "edit", "reject"],
  });
  const RECORD = record('{"id": 1234567890123456789, "note": "Closed."}');
  const INEXACT = /too large for a browser to hold exactly/;

  it("says that such an argument may not be shown as proposed", () => {
    renderCard(proposal(RECORD));
    expect(
      within(
        screen.getByRole("article", { name: "Proposed action close_record" }),
      ).getByText(INEXACT),
    ).toBeTruthy();
  });

  it("says nothing of the kind for ordinary arguments", () => {
    renderCard(proposal(NOTIFY, LOOKUP));
    expect(screen.queryByText(INEXACT)).toBeNull();
  });

  it("does not send an edit that holds such a number, and names the number beside the edit", () => {
    const { send } = renderCard(proposal(NOTIFY));
    fireEvent.click(button("Edit the arguments of send_notification"));
    const box = screen.getByLabelText(
      "Arguments for send_notification, as JSON",
    );
    fireEvent.change(box, {
      target: { value: '{"channel": "#ops", "ticket": 1234567890123456789}' },
    });

    expect(screen.getByRole("alert").textContent).toContain(
      "1234567890123456789",
    );
    const approve = button("Approve send_notification with these arguments");
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(approve);
    expect(send).not.toHaveBeenCalled();
  });

  it("sends the same number as text, which a browser holds as entered", async () => {
    const { send } = renderCard(proposal(NOTIFY));
    fireEvent.click(button("Edit the arguments of send_notification"));
    fireEvent.change(
      screen.getByLabelText("Arguments for send_notification, as JSON"),
      {
        target: {
          value: '{"channel": "#ops", "ticket": "1234567890123456789"}',
        },
      },
    );
    fireEvent.click(button("Approve send_notification with these arguments"));
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith([
        {
          type: "edit",
          args: { channel: "#ops", ticket: "1234567890123456789" },
        },
      ]),
    );
  });

  it("opens the edit of a proposed number of 22 digits already refused, which a browser writes with an exponent", () => {
    const { send } = renderCard(
      proposal(record('{"id": 1234567890123456789012, "note": "Closed."}')),
    );
    expect(screen.getByText(INEXACT)).toBeTruthy();
    fireEvent.click(button("Edit the arguments of close_record"));
    const box = screen.getByLabelText(
      "Arguments for close_record, as JSON",
    ) as HTMLTextAreaElement;
    // What the box starts from is no longer the number that was proposed.
    expect(box.value).toContain("e+21");
    const withThese = button("Approve close_record with these arguments");
    expect((withThese as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toMatch(/exactly/);
    fireEvent.click(withThese);
    expect(send).not.toHaveBeenCalled();
  });

  it("opens the edit of such a proposal already refused, and still approves it as proposed", async () => {
    const { send } = renderCard(proposal(RECORD));
    fireEvent.click(button("Edit the arguments of close_record"));
    // The box starts from what the browser holds, which is not what was proposed: sent
    // back, it would run the action with a number nobody proposed or entered.
    const withThese = button("Approve close_record with these arguments");
    expect((withThese as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toMatch(/exactly/);

    // Approving sends no arguments: the service runs the action with its own.
    fireEvent.click(button("Approve close_record as proposed"));
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith([{ type: "approve" }]),
    );
  });
});

// FR-014: a decision is offered only while the service lists it for the action. The list
// is worked out from what is configured now, so a later reading of the same proposal can
// list fewer.
describe("the proposal card and a decision the service no longer lists", () => {
  function card(pending: PendingDecision, send = vi.fn(async () => {})) {
    return <ProposalCard pending={pending} send={send} onStale={vi.fn()} />;
  }
  const without = (action: ActionRequest, gone: string): ActionRequest => ({
    ...action,
    allowed_decisions: action.allowed_decisions.filter((d) => d !== gone),
  });

  it("closes an open edit when a later reading no longer lists edit", () => {
    const view = render(card(proposal(NOTIFY)));
    fireEvent.click(button("Edit the arguments of send_notification"));
    expect(
      screen.getByLabelText("Arguments for send_notification, as JSON"),
    ).toBeTruthy();

    view.rerender(card(proposal(without(NOTIFY, "edit"))));

    expect(
      screen.queryByLabelText("Arguments for send_notification, as JSON"),
    ).toBeNull();
    expect(
      screen.queryByRole("button", {
        name: "Approve send_notification with these arguments",
      }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", {
        name: "Edit the arguments of send_notification",
      }),
    ).toBeNull();
    // The plain Approve is the one way to approve again, and says so plainly.
    expect(button("Approve send_notification")).toBeTruthy();
  });

  it("does not open the edit again, nor choose it, when a yet later reading lists edit once more", () => {
    const send = vi.fn(async () => {});
    const view = render(card(proposal(NOTIFY), send));
    fireEvent.click(button("Edit the arguments of send_notification"));
    view.rerender(card(proposal(without(NOTIFY, "edit")), send));
    view.rerender(card(proposal(NOTIFY), send));

    // Edit is offered again, and it takes a press of its own to open it (FR-017).
    expect(button("Edit the arguments of send_notification")).toBeTruthy();
    expect(
      screen.queryByLabelText("Arguments for send_notification, as JSON"),
    ).toBeNull();
    expect(
      screen.queryByRole("button", {
        name: "Approve send_notification with these arguments",
      }),
    ).toBeNull();
    expect(button("Approve send_notification")).toBeTruthy();
    expect(send).not.toHaveBeenCalled();
  });

  it("with several actions, leaves a decision unchosen that was dropped and is listed again", () => {
    const send = vi.fn(async () => {});
    const view = render(card(proposal(NOTIFY, LOOKUP), send));
    fireEvent.click(button("Reject send_notification"));
    fireEvent.click(button("Approve record_note"));
    view.rerender(card(proposal(without(NOTIFY, "reject"), LOOKUP), send));
    view.rerender(card(proposal(NOTIFY, LOOKUP), send));

    expect((button("Send the 2 decisions") as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(
      button("Reject send_notification").getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("closes an open rejection when a later reading no longer lists reject", () => {
    const send = vi.fn(async () => {});
    const view = render(card(proposal(NOTIFY), send));
    fireEvent.click(button("Reject send_notification"));
    expect(screen.getByLabelText(/^Why/)).toBeTruthy();

    view.rerender(card(proposal(without(NOTIFY, "reject")), send));

    expect(screen.queryByLabelText(/^Why/)).toBeNull();
    expect(
      screen.queryByRole("button", { name: /^Reject send_notification/ }),
    ).toBeNull();
    expect(send).not.toHaveBeenCalled();
  });

  it("with several actions, takes a chosen decision for not chosen once it is no longer listed", () => {
    const send = vi.fn(async () => {});
    const view = render(card(proposal(NOTIFY, LOOKUP), send));
    fireEvent.click(button("Reject send_notification"));
    fireEvent.click(button("Approve record_note"));
    const sendAll = () => button("Send the 2 decisions") as HTMLButtonElement;
    expect(sendAll().disabled).toBe(false);

    view.rerender(card(proposal(without(NOTIFY, "reject"), LOOKUP), send));

    // The first action has no decision any more: nothing can be sent until it has one.
    expect(sendAll().disabled).toBe(true);
    expect(
      screen.getByText("Each action needs a decision before anything is sent."),
    ).toBeTruthy();
    fireEvent.click(sendAll());
    expect(send).not.toHaveBeenCalled();
  });
});

// Edge Cases, "A proposal's action is no longer configured, or its agent was removed": the
// service then names neither a tool server nor an agent for it, lists approve and reject,
// and runs nothing whatever is decided. The page cannot tell such an action from one of
// Freyja's own, so it claims no more than holds for both.
describe("the proposal card and an action that may no longer be set up", () => {
  const GONE: ActionRequest = {
    name: "close_ticket",
    server: null,
    agent: null,
    args: { ticket: "T-1" },
    description: null,
    allowed_decisions: ["approve", "reject"],
  };

  it("shows the action as the service gives it, with the decisions it lists and no other", () => {
    renderCard(proposal(GONE));
    const shown = screen.getByRole("article", {
      name: "Proposed action close_ticket",
    });
    expect(shown.textContent).toContain("close_ticket");
    expect(shown.textContent).toContain('"ticket": "T-1"');
    expect(
      within(shown)
        .getAllByRole("button")
        .map((control) => control.textContent),
    ).toEqual(["Approve close_ticket", "Reject close_ticket"]);
  });

  it("does not say that an approved action runs, only that it runs once at most", () => {
    renderCard(proposal(GONE));
    const said = screen.getByText(/^Nothing has run\./).textContent ?? "";
    expect(said).toBe(
      "Nothing has run. An approved action runs once at most, and not at all when it is no longer set up on the service; a rejected one never runs.",
    );
    expect(said).not.toMatch(/runs once;/);
  });
});

// FR-014, T105: values the service refuses can mean that a decision is no longer allowed
// for the action. The reason is shown beside the action, and the task is read again, so
// that the card offers what the service lists now.
describe("the proposal card and values the service refused", () => {
  const INVALID = new ApiError(
    "Some values were refused.",
    422,
    "invalid",
    [{ field: "type", message: "This action cannot be edited any more." }],
    [["body", "decisions", 0, "type"]],
  );

  it("asks for the task to be read again, once, and keeps the edit and the reason", async () => {
    const send = vi.fn(async () => {
      throw INVALID;
    });
    const onRefused = vi.fn();
    render(
      <ProposalCard
        pending={proposal(NOTIFY)}
        send={send}
        onStale={vi.fn()}
        onRefused={onRefused}
      />,
    );
    fireEvent.click(button("Edit the arguments of send_notification"));
    const box = screen.getByLabelText(
      "Arguments for send_notification, as JSON",
    ) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: '{"channel": "#staging"}' } });
    fireEvent.click(button("Approve send_notification with these arguments"));

    await waitFor(() => expect(onRefused).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alert").textContent).toContain(
      "This action cannot be edited any more.",
    );
    expect(box.value).toBe('{"channel": "#staging"}');
  });

  it("asks for no reading when the decision was sent, or was refused another way", async () => {
    const onRefused = vi.fn();
    const refusedOtherwise = vi.fn(async () => {
      throw new ApiError("Not the pending proposal.", 409, "proposal_mismatch");
    });
    const onStale = vi.fn();
    render(
      <ProposalCard
        pending={proposal(NOTIFY)}
        send={refusedOtherwise}
        onStale={onStale}
        onRefused={onRefused}
      />,
    );
    fireEvent.click(button("Approve send_notification"));
    await waitFor(() => expect(onStale).toHaveBeenCalledTimes(1));
    expect(onRefused).not.toHaveBeenCalled();
  });
});

// FR-012a, T106: a control's name says on which action. Two actions of one name in one
// proposal would otherwise have controls of the same name.
describe("the proposal card and two actions of the same name", () => {
  const second: ActionRequest = {
    ...NOTIFY,
    args: { channel: "#dev", message: "The report is ready." },
  };

  it("names each one's controls by its place as well", () => {
    renderCard(proposal(NOTIFY, second, LOOKUP));
    const names = screen
      .getAllByRole("button")
      .map(
        (control) => control.getAttribute("aria-label") ?? control.textContent,
      );
    expect(names).toEqual([
      "Approve send_notification (action 1 of 3)",
      "Edit the arguments of send_notification (action 1 of 3)",
      "Reject send_notification (action 1 of 3)",
      "Approve send_notification (action 2 of 3)",
      "Edit the arguments of send_notification (action 2 of 3)",
      "Reject send_notification (action 2 of 3)",
      // An action whose name no other has keeps its name alone.
      "Approve record_note",
      "Edit the arguments of record_note",
      "Reject record_note",
      "Send the 3 decisions",
    ]);
    expect(new Set(names).size).toBe(names.length);
    expect(
      screen
        .getAllByRole("article")
        .map((action) => action.getAttribute("aria-label")),
    ).toEqual([
      "Proposed action send_notification (action 1 of 3)",
      "Proposed action send_notification (action 2 of 3)",
      "Proposed action record_note",
    ]);
  });

  it("names the fields of each by its place too, and decides the one that was pressed", async () => {
    const { send } = renderCard(proposal(NOTIFY, second));
    fireEvent.click(button("Approve send_notification (action 1 of 2)"));
    fireEvent.click(button("Reject send_notification (action 2 of 2)"));
    fireEvent.change(
      screen.getByLabelText(
        "Why send_notification (action 2 of 2) is rejected (optional, Freyja is told)",
      ),
      { target: { value: "Not #dev." } },
    );
    fireEvent.click(button("Send the 2 decisions"));
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith([
        { type: "approve" },
        { type: "reject", message: "Not #dev." },
      ]),
    );
  });
});
