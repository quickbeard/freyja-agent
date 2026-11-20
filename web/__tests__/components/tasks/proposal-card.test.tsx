import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProposalCard } from "@/components/tasks/proposal-card";
import { ApiError } from "@/lib/api";
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
