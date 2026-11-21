import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { refusal, serve } from "@/__tests__/tasks-testing";
import { StopButton } from "@/components/tasks/stop-button";

// 007, User Story 5: stop a run the console started (FR-022, FR-022a).

const ACCEPTED = {
  status: 202,
  json: { thread_id: "t-1", stopping: true, run: 1 },
};
const NOT_STOPPABLE =
  "This run has no Stop: the console did not start it. A run another client started ends when that client stops reading, and a run the service started ends by itself.";

function show(
  run: "stoppable" | "not_stoppable",
  about: string | null = "Summarize the week.",
) {
  const onAsked = vi.fn();
  const onStale = vi.fn();
  const view = render(
    <StopButton
      threadId="t-1"
      about={about}
      run={run}
      onAsked={onAsked}
      onStale={onStale}
    />,
  );
  return { onAsked, onStale, ...view };
}

const stop = () => screen.queryByRole("button", { name: /^Stop/ });

describe("the Stop control", () => {
  it("is a button of its own, outside any form, named for what it stops", () => {
    serve(() => ACCEPTED);
    show("stoppable");
    const button = screen.getByRole("button", {
      name: "Stop the run of the task: Summarize the week.",
    });
    expect(button.textContent).toBe("Stop");
    expect(button.getAttribute("type")).toBe("button");
    expect(button.closest("form")).toBeNull();
  });

  it("names the task by no more than how its first message begins", () => {
    serve(() => ACCEPTED);
    const long = `${"A long first message. ".repeat(12)}The end.`;
    show("stoppable", long);
    expect(stop()?.getAttribute("aria-label")).toBe(
      `Stop the run of the task: ${long.slice(0, 60)}…`,
    );
  });

  it("is named without a message when the task's first message is not known", () => {
    serve(() => ACCEPTED);
    show("stoppable", null);
    expect(
      screen.getByRole("button", { name: "Stop the run of this task" }),
    ).toBeTruthy();
  });

  it("sends one request with no body on a press, and none on a second press that comes before the page has drawn again", async () => {
    const asked = serve(() => ACCEPTED);
    const { onAsked, onStale } = show("stoppable");
    const button = stop() as HTMLButtonElement;
    // Two presses in one turn: the button is not shown as disabled yet at the second.
    act(() => {
      button.click();
      button.click();
    });

    await waitFor(() => expect(onAsked).toHaveBeenCalledTimes(1));
    expect(asked).toEqual([
      { method: "POST", path: "/threads/t-1/stop", body: undefined },
    ]);
    expect(onStale).not.toHaveBeenCalled();
  });

  it("cannot be pressed while its request is on the way", async () => {
    let answer: (value: typeof ACCEPTED) => void = () => {};
    const asked = serve(
      () =>
        new Promise<typeof ACCEPTED>((resolve) => {
          answer = resolve;
        }),
    );
    const { onAsked } = show("stoppable");
    fireEvent.click(stop() as HTMLButtonElement);
    await waitFor(() =>
      expect((stop() as HTMLButtonElement).disabled).toBe(true),
    );
    fireEvent.click(stop() as HTMLButtonElement);
    answer(ACCEPTED);
    await waitFor(() => expect(onAsked).toHaveBeenCalledTimes(1));
    expect(asked).toHaveLength(1);
  });

  it("offers no Stop for a run the console did not start, and says why", () => {
    serve(() => ACCEPTED);
    show("not_stoppable");
    expect(stop()).toBeNull();
    expect(screen.getByText(NOT_STOPPABLE)).toBeTruthy();
  });

  it.each([
    [
      "no_run",
      "No run is in progress on this thread.",
      "No run is in progress on this task any more, so nothing was stopped. The task is shown as it is now.",
    ],
    [
      "not_stoppable",
      "This run cannot be stopped.",
      "The service did not stop this run: the console did not start it, or it has only just started. The task is shown as it is now.",
    ],
  ])("says so when the service answers %s, and has the task read again", async (code, message, said) => {
    serve(() => refusal(409, code, message));
    const { onAsked, onStale } = show("stoppable");
    fireEvent.click(stop() as HTMLButtonElement);
    await waitFor(() => expect(onStale).toHaveBeenCalledTimes(1));
    expect(onStale).toHaveBeenCalledWith(said);
    expect(onAsked).not.toHaveBeenCalled();
    // The control can be pressed again once the service has answered.
    expect((stop() as HTMLButtonElement).disabled).toBe(false);
  });

  it("says that the outcome is not known when no answer came, and has the task read again", async () => {
    serve(() => ({ fails: "refused" }));
    const { onAsked, onStale } = show("stoppable");
    fireEvent.click(stop() as HTMLButtonElement);
    await waitFor(() => expect(onStale).toHaveBeenCalledTimes(1));
    expect(onStale.mock.calls[0][0]).toMatch(
      /No answer came, so whether the stop was asked for is not known/,
    );
    expect(onAsked).not.toHaveBeenCalled();
  });

  it("gives the service's own sentence for any other refusal", async () => {
    serve(() => refusal(404, "thread_not_found", "No such thread is listed."));
    const { onStale } = show("stoppable");
    fireEvent.click(stop() as HTMLButtonElement);
    await waitFor(() =>
      expect(onStale).toHaveBeenCalledWith("No such thread is listed."),
    );
  });
});
