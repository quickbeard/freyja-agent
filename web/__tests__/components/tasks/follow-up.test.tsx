import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ai,
  body,
  END,
  human,
  openBody,
  refusal,
  START,
  serve,
} from "@/__tests__/tasks-testing";
import { FollowUp } from "@/components/tasks/follow-up";
import { clear, get } from "@/lib/task-runs";

// 007, User Story 4: another message on the same task (FR-020).

const BEFORE = [human("Summarize the week."), ai("Here it is.")];
const LABEL = "Send another message on this task";

afterEach(() => {
  clear("t-1");
});

const box = () => screen.getByLabelText(LABEL) as HTMLTextAreaElement;
const send = () => screen.getByRole("button", { name: "Send message" });

function type(text: string) {
  fireEvent.change(box(), { target: { value: text } });
}

describe("the follow-up box", () => {
  it("sends a message on the same task and holds its run, with the conversation before it", async () => {
    const answer = openBody();
    const asked = serve(() => ({ stream: answer.stream }));
    const onSent = vi.fn();
    render(
      <FollowUp
        threadId="t-1"
        before={BEFORE}
        blocked={null}
        onSent={onSent}
      />,
    );

    type("And the week before?");
    fireEvent.click(send());

    await waitFor(() => expect(get("t-1")).toBeDefined());
    expect(asked).toEqual([
      {
        method: "POST",
        path: "/threads/t-1/runs/unattended",
        body: { message: "And the week before?" },
      },
    ]);
    // The run is this tab's: the task's page shows it as it arrives, below the conversation
    // as it was when the message was sent.
    expect(get("t-1")?.message).toBe("And the week before?");
    expect(get("t-1")?.before).toEqual(BEFORE);
    expect(get("t-1")?.ended).toBeNull();
    // The box is empty again for the next message.
    await waitFor(() => expect(box().value).toBe(""));
    expect(onSent).toHaveBeenCalledTimes(1);
    answer.send(START, END);
    answer.close();
  });

  it("sends once when Send is pressed twice", async () => {
    const answer = openBody();
    const asked = serve(() => ({ stream: answer.stream }));
    render(<FollowUp threadId="t-1" before={BEFORE} blocked={null} />);
    type("Once.");
    const button = send();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(box().closest("form") as HTMLFormElement);
    await waitFor(() => expect(get("t-1")).toBeDefined());
    expect(asked).toHaveLength(1);
    answer.close();
  });

  it("does not send a message that is empty", () => {
    const asked = serve(() => ({ stream: body(START, END) }));
    render(<FollowUp threadId="t-1" before={BEFORE} blocked={null} />);
    type("   ");
    expect((send() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(box().closest("form") as HTMLFormElement);
    expect(asked).toHaveLength(0);
  });

  it("cannot be sent while a run is in progress, and says why", () => {
    const asked = serve(() => ({ stream: body(START, END) }));
    render(<FollowUp threadId="t-1" before={BEFORE} blocked="run" />);
    type("Meanwhile.");

    expect((send() as HTMLButtonElement).disabled).toBe(true);
    const why = screen.getByText(
      "A run is in progress on this task. A message can be sent once it has ended.",
    );
    // The reason is the box's description, for whoever does not see it beside the box.
    expect(box().getAttribute("aria-describedby")).toContain(why.id);
    // Neither the button nor the form sends it.
    fireEvent.submit(box().closest("form") as HTMLFormElement);
    expect(asked).toHaveLength(0);
    // What was typed stays, to be sent once the run has ended.
    expect(box().value).toBe("Meanwhile.");
  });

  it("cannot be sent while a proposal is pending, and says why", () => {
    const asked = serve(() => ({ stream: body(START, END) }));
    render(<FollowUp threadId="t-1" before={BEFORE} blocked="proposal" />);
    type("Do it anyway.");

    expect((send() as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText(
        "This task waits for a decision on the proposal above. A message can be sent once it is decided.",
      ),
    ).toBeTruthy();
    fireEvent.submit(box().closest("form") as HTMLFormElement);
    expect(asked).toHaveLength(0);
  });

  it("keeps what was typed when the task stops being blocked", () => {
    serve(() => ({ stream: body(START, END) }));
    const { rerender } = render(
      <FollowUp threadId="t-1" before={BEFORE} blocked="run" />,
    );
    type("Typed during the run.");
    rerender(<FollowUp threadId="t-1" before={BEFORE} blocked={null} />);
    expect(box().value).toBe("Typed during the run.");
    expect((send() as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows the service's reason for a refusal and keeps the text", async () => {
    serve(() => ({
      status: 422,
      json: {
        detail: [
          {
            loc: ["body", "message"],
            msg: "String should have at most 32000 characters",
          },
        ],
      },
    }));
    render(<FollowUp threadId="t-1" before={BEFORE} blocked={null} />);
    type("Far too long.");
    fireEvent.click(send());

    expect((await screen.findByRole("alert")).textContent).toContain(
      "String should have at most 32000 characters",
    );
    expect(box().value).toBe("Far too long.");
    expect(get("t-1")).toBeUndefined();
  });

  it("does not have the task read again for a refusal of the message itself", async () => {
    serve(() => refusal(422, "invalid", "Not this message."));
    const onMoved = vi.fn();
    render(
      <FollowUp
        threadId="t-1"
        before={BEFORE}
        blocked={null}
        onMoved={onMoved}
      />,
    );
    type("Not this.");
    fireEvent.click(send());
    await screen.findByRole("alert");
    expect(onMoved).not.toHaveBeenCalled();
  });

  it("has the task read again when no answer came, since the service may have accepted the message", async () => {
    serve(() => ({ fails: "refused" }));
    const onMoved = vi.fn();
    render(
      <FollowUp
        threadId="t-1"
        before={BEFORE}
        blocked={null}
        onMoved={onMoved}
      />,
    );
    type("Is anyone there?");
    fireEvent.click(send());
    await screen.findByRole("alert");
    expect(onMoved).toHaveBeenCalledTimes(1);
  });

  it("stops saying a refusal once the reason the box cannot be sent has changed", async () => {
    serve(() => refusal(409, "run_active", "A run is already active."));
    const { rerender } = render(
      <FollowUp threadId="t-1" before={BEFORE} blocked={null} />,
    );
    type("Too late.");
    fireEvent.click(send());
    expect((await screen.findByRole("alert")).textContent).toContain(
      "A run is already in progress on this task",
    );

    // The task was read again: a run is in progress, and then it has ended.
    rerender(<FollowUp threadId="t-1" before={BEFORE} blocked="run" />);
    expect(screen.queryByRole("alert")).toBeNull();
    rerender(<FollowUp threadId="t-1" before={BEFORE} blocked={null} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(box().value).toBe("Too late.");
    expect(box().getAttribute("aria-invalid")).toBeNull();
  });

  it("says the service cannot be reached and keeps the text", async () => {
    serve(() => ({ fails: "refused" }));
    render(<FollowUp threadId="t-1" before={BEFORE} blocked={null} />);
    type("Is anyone there?");
    fireEvent.click(send());
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /cannot be reached/,
    );
    expect(box().value).toBe("Is anyone there?");
  });

  // T123: the service's sentences name a thread; the console says them in its own words.
  it.each([
    [
      "run_active",
      "A run is already active on this thread.",
      "A run is already in progress on this task, so the message was not sent. It can be sent once the run has ended.",
    ],
    [
      "decision_pending",
      "This thread is paused on a proposal; decide it through resume first.",
      "This task waits for a decision on a proposal, so the message was not sent. It can be sent once the proposal is decided.",
    ],
  ])("has the task read again when the service refuses with %s, says why in its own words, and keeps the text", async (code, message, said) => {
    serve(() => refusal(409, code, message));
    const onMoved = vi.fn();
    render(
      <FollowUp
        threadId="t-1"
        before={BEFORE}
        blocked={null}
        onMoved={onMoved}
      />,
    );
    type("Too late.");
    fireEvent.click(send());

    const alert = (await screen.findByRole("alert")).textContent;
    expect(alert).toBe(said);
    expect(alert).not.toContain(message);
    expect(onMoved).toHaveBeenCalledTimes(1);
    expect(box().value).toBe("Too late.");
    expect(get("t-1")).toBeUndefined();
  });

  it("starts with the text it is given", () => {
    serve(() => ({ stream: body(START, END) }));
    render(
      <FollowUp
        threadId="t-1"
        before={BEFORE}
        blocked={null}
        initial="Sent before, and not recorded."
      />,
    );
    expect(box().value).toBe("Sent before, and not recorded.");
  });
});
