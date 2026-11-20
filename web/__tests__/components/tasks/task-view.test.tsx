import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type Answer,
  ai,
  body,
  chunk,
  END,
  human,
  openBody,
  refusal,
  START,
  serve,
  sse,
  state,
  summary,
  update,
} from "@/__tests__/tasks-testing";
import { FOLLOW_SECONDS, TaskView } from "@/components/tasks/task-view";
import { begin, clear, get } from "@/lib/task-runs";

const NOT_USED = refusal(
  404,
  "thread_not_found",
  "This thread has never been used.",
);
const NO_USABLE_MODEL = sse("error", {
  code: "no_usable_model",
  message:
    "No usable model is active. Activate a usable LLM profile, then send the message again.",
});

afterEach(() => {
  clear("t-1");
});

function renderView(threadId = "t-1") {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <TaskView threadId={threadId} />
    </SWRConfig>,
  );
}

/** The service as a test sets it: what the row and the state answer, now. */
function service(answers: { row: () => Answer; state: () => Answer }) {
  return serve((method, path) => {
    if (method === "POST") {
      return { stream: body(START, END) };
    }
    return path.endsWith("/state") ? answers.state() : answers.row();
  });
}

const conversation = () =>
  screen.queryByRole("list", { name: "Conversation" })?.textContent ?? "";

describe("a task's page", () => {
  it("shows a run as it arrives, then the conversation the service holds", async () => {
    let finished = false;
    service({
      row: () => (finished ? { json: summary() } : NOT_USED),
      state: () =>
        finished
          ? { json: state([human("Summarize the week."), ai("Hello there.")]) }
          : NOT_USED,
    });
    const answer = openBody();
    begin("t-1", "Summarize the week.", answer.stream);
    renderView();

    expect(screen.getByText("Starting the run…")).toBeTruthy();
    answer.send(START);
    expect(
      await screen.findByText(
        "The run has started. Freyja's reply appears as it is written.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Running")).toBeTruthy();
    expect(conversation()).toContain("Summarize the week.");

    // The reply grows as it is written, before the run has ended.
    answer.send(chunk("Hello"));
    await waitFor(() => expect(conversation()).toContain("Hello"));
    answer.send(chunk(" there"), update("model"));
    await waitFor(() => expect(conversation()).toContain("Hello there"));
    expect(screen.getByText("Freyja wrote a reply")).toBeTruthy();

    finished = true;
    answer.send(END);
    answer.close();
    await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
    expect(conversation()).toContain("Hello there.");
    expect(screen.queryByText("Freyja wrote a reply")).toBeNull();
  });

  it("says a run failed and why, and points to the LLM section", async () => {
    service({
      row: () => ({
        json: summary({
          status: "failed",
          error: { code: "no_usable_model", message: "No usable model." },
        }),
      }),
      state: () => ({ json: state([human("Earlier.")]) }),
    });
    begin("t-1", "Again.", body(START, NO_USABLE_MODEL));
    renderView();

    // The run's own error, as the stream said it; one notice, not two.
    await screen.findByText(/^No usable model is active\./);
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0].textContent).toContain("The run failed.");
    expect(
      screen
        .getByRole("link", { name: "Go to the LLM section" })
        .getAttribute("href"),
    ).toBe("/llm");
    await waitFor(() => expect(conversation()).toContain("Earlier."));
  });

  it("offers a new task's text again when its first run failed before anything was recorded", async () => {
    const asked = service({ row: () => NOT_USED, state: () => NOT_USED });
    begin("t-1", "Summarize the week.", body(START, NO_USABLE_MODEL));
    renderView();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "No usable model is active.",
    );
    expect(screen.queryByText("Task not found")).toBeNull();
    const box = screen.getByLabelText(
      "Send the message again",
    ) as HTMLTextAreaElement;
    expect(box.value).toBe("Summarize the week.");

    fireEvent.click(screen.getByRole("button", { name: "Send again" }));
    await waitFor(() =>
      expect(asked.filter(({ method }) => method === "POST")).toEqual([
        {
          method: "POST",
          path: "/threads/t-1/runs/unattended",
          body: { message: "Summarize the week." },
        },
      ]),
    );
    await waitFor(() => expect(get("t-1")?.ended?.kind).toBe("event"));
  });

  it("says the connection ended without saying the run failed", async () => {
    service({
      row: () => ({ json: summary({ status: "running" }) }),
      state: () => ({ json: state([human("Summarize the week.")]) }),
    });
    begin("t-1", "Summarize the week.", body(START, chunk("Half")));
    renderView();

    expect(
      await screen.findByText(
        /The connection to the run ended before the run did/,
      ),
    ).toBeTruthy();
    expect(screen.queryByText("The run failed.")).toBeNull();
    await waitFor(() =>
      expect(
        screen.getByText(
          "A run is in progress. This page reads the task again until it ends.",
        ),
      ).toBeTruthy(),
    );
  });

  it("shows a task with no conversation and no run of this page as not found", async () => {
    service({ row: () => NOT_USED, state: () => NOT_USED });
    renderView();
    expect(
      await screen.findByRole("heading", { name: "Task not found" }),
    ).toBeTruthy();
  });

  it("shows an id the service does not accept as not found", async () => {
    const invalid = {
      status: 422,
      json: { detail: [{ loc: ["path", "thread_id"], msg: "bad" }] },
    };
    service({ row: () => invalid, state: () => invalid });
    renderView("...");
    expect(
      await screen.findByRole("heading", { name: "Task not found" }),
    ).toBeTruthy();
  });

  it("says the service cannot be reached and reads again on request", async () => {
    let down = true;
    service({
      row: () => (down ? { fails: "refused" } : { json: summary() }),
      state: () =>
        down ? { fails: "refused" } : { json: state([human("Back again.")]) },
    });
    renderView();
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /cannot be reached/,
    );
    expect(conversation()).toBe("");

    down = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(conversation()).toContain("Back again."));
  });

  it("follows a run it does not read every five seconds, also past a read that failed", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let reads = 0;
      service({
        row: () => {
          reads += 1;
          // The second reading fails, as while the service restarts.
          if (reads === 2) {
            return { fails: "refused" };
          }
          return reads < 3
            ? { json: summary({ status: "running" }) }
            : { json: summary({ status: "done" }) };
        },
        state: () => ({ json: state([human("Started elsewhere.")]) }),
      });
      renderView();
      expect(
        await screen.findByText(
          "A run is in progress. This page reads the task again until it ends.",
        ),
      ).toBeTruthy();

      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      expect((await screen.findByRole("alert")).textContent).toMatch(
        /cannot be reached.*reads the task again until it can/,
      );
      // What was read before stays on the page.
      expect(conversation()).toContain("Started elsewhere.");

      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
      expect(screen.queryByRole("alert")).toBeNull();
      expect(reads).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("forgets how a run ended once the task has been read again", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let done = false;
      service({
        row: () => ({ json: summary({ status: done ? "done" : "running" }) }),
        state: () => ({ json: state([human("Summarize the week.")]) }),
      });
      begin("t-1", "Summarize the week.", body(START, chunk("Half")));
      renderView();
      expect(
        await screen.findByText(
          /The connection to the run ended before the run did/,
        ),
      ).toBeTruthy();
      await waitFor(() => expect(get("t-1")).toBeUndefined());

      // The run ended by itself after all: the page says nothing more of the cut.
      done = true;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
      expect(
        screen.queryByText(
          /The connection to the run ended before the run did/,
        ),
      ).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("follows a run whose stream was cut before anything was recorded, and says it is not missing", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let recorded = false;
      service({
        row: () => ({
          json: summary({ status: recorded ? "done" : "running" }),
        }),
        state: () =>
          recorded
            ? { json: state([human("Summarize the week."), ai("Here it is.")]) }
            : NOT_USED,
      });
      begin("t-1", "Summarize the week.", body(START));
      renderView();
      expect(
        await screen.findByText("Nothing of the run has been recorded yet."),
      ).toBeTruthy();
      expect(screen.queryByText("Task not found")).toBeNull();

      recorded = true;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(conversation()).toContain("Here it is."));
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a task read from the service with its status", async () => {
    service({
      row: () => ({ json: summary({ status: "stopped" }) }),
      state: () => ({
        json: state([
          human("List the files."),
          {
            ...ai("", "ai-1"),
            tool_calls: [{ id: "l1", name: "ls", args: { path: "/" } }],
          },
        ]),
      }),
    });
    renderView();
    expect(await screen.findByText("Stopped")).toBeTruthy();
    await waitFor(() =>
      expect(conversation()).toContain("Freyja asked to run ls"),
    );
  });
});
