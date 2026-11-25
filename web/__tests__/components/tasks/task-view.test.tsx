import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import useSWR, { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type Answer,
  agentTask,
  ai,
  body,
  chunk,
  END,
  human,
  later,
  openBody,
  refusal,
  START,
  serve,
  sse,
  state,
  summary,
  update,
} from "@/__tests__/tasks-testing";
import { contentText } from "@/components/tasks/message";
import {
  AGENT_SECONDS,
  FOLLOW_SECONDS,
  streamed,
  TaskView,
} from "@/components/tasks/task-view";
import { begin, clear, get } from "@/lib/task-runs";
import { listThreads, type PendingDecision, THREADS_KEY } from "@/lib/tasks";

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
    <SWRConfig
      value={{
        provider: () => new Map(),
        dedupingInterval: 0,
        // The reading library's own wait before it reads a page shown again, five seconds
        // after the page was opened, is left as it is: the console sets it aside itself.
      }}
    >
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

/**
 * Wait, in real time, for the page to have forgotten its run. For a test that fakes
 * `setInterval`: Testing Library's own wait looks again by that timer, and otherwise only
 * when the page changes, which forgetting a run that has been read need not do.
 */
async function forgotten() {
  for (
    let waited = 0;
    get("t-1") !== undefined && waited < 2000;
    waited += 20
  ) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
  expect(get("t-1")).toBeUndefined();
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

  it("says a run failed and why, and points to the Models section", async () => {
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
        .getByRole("link", { name: "Go to the Models section" })
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

  // Phase 14's review: the box that sends a failed first message again says a refusal whose
  // sentence names a thread in the console's words, as the follow-up box does.
  it("says in its own words that a run is in progress when the text is sent again", async () => {
    serve((method) =>
      method === "POST"
        ? refusal(409, "run_active", "A run is already active on this thread.")
        : NOT_USED,
    );
    begin("t-1", "Summarize the week.", body(START, NO_USABLE_MODEL));
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Send again" }));
    expect(
      await screen.findByText(/^A run is already in progress on this task/),
    ).toBeTruthy();
    expect(screen.queryByText(/thread/)).toBeNull();
    expect(
      (screen.getByLabelText("Send the message again") as HTMLTextAreaElement)
        .value,
    ).toBe("Summarize the week.");
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

  it("follows a run it does not read by its timer, also past a read that failed", async () => {
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
      await forgotten();

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

// Phase 5: a task paused on a proposal.

const PROPOSAL = (id: string): PendingDecision => ({
  interrupt_id: id,
  action_requests: [
    {
      name: "send_notification",
      server: null,
      agent: null,
      args: { channel: "#ops", message: "Ready." },
      description: "Send a notification to a channel.",
      allowed_decisions: ["approve", "edit", "reject"],
    },
  ],
  allowed_decisions: ["approve", "edit", "reject"],
});

const ASKED = {
  ...ai("", "ai-1"),
  tool_calls: [
    {
      id: "n1",
      name: "send_notification",
      args: { channel: "#ops", message: "Ready." },
    },
  ],
};

describe("a task's page with a proposal", () => {
  it("shows the proposal's card, and a box for a new message that cannot be sent", async () => {
    service({
      row: () => ({ json: summary({ status: "needs_decision" }) }),
      state: () => ({
        json: state([human("Tell #ops."), ASKED], {
          pending: PROPOSAL("int-1"),
        }),
      }),
    });
    renderView();
    expect(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    ).toBeTruthy();
    expect(screen.getByText("Needs your decision")).toBeTruthy();
    // No message is sent to a task whose proposal is pending (FR-017, FR-020).
    expect(
      (
        screen.getByRole("button", {
          name: "Send message",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      screen.getByText(
        "This task waits for a decision on the proposal above. A message can be sent once it is decided.",
      ),
    ).toBeTruthy();
  });

  it("shows the continuation of a decision as a run, also when it pauses again", async () => {
    let step = 0;
    const continuation = openBody();
    const asked = serve((method, path) => {
      if (method === "POST") {
        step = 1;
        return { stream: continuation.stream };
      }
      const paused = step === 0 ? "int-1" : "int-2";
      if (path.endsWith("/state")) {
        return {
          json: state(
            step === 0
              ? [human("Tell #ops."), ASKED]
              : [
                  human("Tell #ops."),
                  ASKED,
                  {
                    id: "t1",
                    type: "tool",
                    content: "Notification to #ops recorded.",
                    name: "send_notification",
                  },
                  { ...ASKED, id: "ai-2" },
                ],
            {
              pending: PROPOSAL(paused),
            },
          ),
        };
      }
      return { json: summary({ status: "needs_decision" }) };
    });
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    );

    // The continuation streams below the conversation as it was when the person decided.
    continuation.send(START, chunk("Sending it again.", "ai-2"));
    await waitFor(() => expect(conversation()).toContain("Sending it again."));
    expect(conversation()).toContain("Tell #ops.");
    expect(
      screen
        .getByRole("list", { name: "Conversation" })
        .querySelectorAll(":scope > li"),
    ).toHaveLength(3);
    // The focus is not lost with the card: it is on the run's status.
    await waitFor(() => expect(document.activeElement?.tagName).toBe("OUTPUT"));
    expect(document.activeElement?.textContent).toMatch(/The run has started/);
    expect(screen.getByText("Running")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Approve send_notification" }),
    ).toBeNull();

    continuation.send(sse("interrupt", PROPOSAL("int-2")));
    continuation.close();
    await waitFor(() =>
      expect(conversation()).toContain("Notification to #ops recorded."),
    );
    expect(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    ).toBeTruthy();
    expect(asked.filter(({ method }) => method === "POST")).toEqual([
      {
        method: "POST",
        path: "/threads/t-1/resume/unattended",
        body: { interrupt_id: "int-1", decisions: [{ type: "approve" }] },
      },
    ]);
  });

  it("says that a proposal decided elsewhere is no longer the pending one, and reads the task again", async () => {
    let reads = 0;
    serve((method, path) => {
      if (method === "POST") {
        return refusal(
          409,
          "proposal_mismatch",
          "The interrupt_id is not the pending proposal's.",
        );
      }
      if (path.endsWith("/state")) {
        reads += 1;
        return reads === 1
          ? {
              json: state([human("Tell #ops."), ASKED], {
                pending: PROPOSAL("int-1"),
              }),
            }
          : { json: state([human("Tell #ops."), ASKED, ai("Sent.", "ai-2")]) };
      }
      return {
        json: summary({ status: reads > 1 ? "done" : "needs_decision" }),
      };
    });
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    );
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /no longer the pending one/,
    );
    await waitFor(() => expect(conversation()).toContain("Sent."));
    expect(
      screen.queryByRole("button", { name: "Approve send_notification" }),
    ).toBeNull();
  });
});

// FR-018, T107: no answer to a decision. It may or may not have been applied: the page
// says that it is not known, never that it failed, reads the task again, and shows the
// proposal only when it is still pending.
describe("a task's page and a decision that gets no answer", () => {
  const UNKNOWN =
    "No answer came, so whether the decision was applied is not known. The task is read again, and the proposal is shown only if it is still pending.";

  function decided(applied: boolean) {
    const asked = { state: 0, posts: 0 };
    serve((method, path) => {
      if (method === "POST") {
        asked.posts += 1;
        return { fails: "the connection dropped" };
      }
      const after = asked.posts > 0 && applied;
      if (path.endsWith("/state")) {
        asked.state += 1;
        return {
          json: after
            ? state([human("Tell #ops."), ASKED, ai("Sent.", "ai-2")])
            : state([human("Tell #ops."), ASKED], {
                pending: PROPOSAL("int-1"),
              }),
        };
      }
      return { json: summary({ status: after ? "done" : "needs_decision" }) };
    });
    return asked;
  }

  it("says that the outcome is not known, reads the task again, and shows the proposal that is still pending", async () => {
    const asked = decided(false);
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    );

    expect((await screen.findByRole("alert")).textContent).toBe(UNKNOWN);
    expect(screen.queryByText(/failed/i)).toBeNull();
    await waitFor(() => expect(asked.state).toBeGreaterThanOrEqual(2));
    // Still pending, so the card is there to decide again; nothing was sent twice.
    expect(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    ).toBeTruthy();
    expect(screen.getByText("Needs your decision")).toBeTruthy();
    expect(asked.posts).toBe(1);
  });

  it("shows no proposal when the decision had been applied after all", async () => {
    const asked = decided(true);
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    );

    await waitFor(() => expect(conversation()).toContain("Sent."));
    expect(
      screen.queryByRole("button", { name: "Approve send_notification" }),
    ).toBeNull();
    expect(screen.getByText("Done")).toBeTruthy();
    expect(asked.posts).toBe(1);
  });

  // FR-014, T105: values the service refuses; the task is read again and the card offers
  // what the service lists now.
  it("reads the task again when the service refuses a decision's values, and offers what it lists now", async () => {
    let posts = 0;
    serve((method, path) => {
      if (method === "POST") {
        posts += 1;
        return {
          status: 422,
          json: {
            detail: [
              {
                type: "value_error",
                loc: ["body", "decisions", 0, "type"],
                msg: "This action cannot be edited any more.",
              },
            ],
          },
        };
      }
      if (path.endsWith("/state")) {
        const pending = PROPOSAL("int-1");
        return {
          json: state([human("Tell #ops."), ASKED], {
            pending:
              posts === 0
                ? pending
                : {
                    ...pending,
                    action_requests: [
                      {
                        ...pending.action_requests[0],
                        allowed_decisions: ["approve", "reject"],
                      },
                    ],
                  },
          }),
        };
      }
      return { json: summary({ status: "needs_decision" }) };
    });
    renderView();
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Edit the arguments of send_notification",
      }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Approve send_notification with these arguments",
      }),
    );

    // The same proposal, read again: edit is no longer offered, and its form is closed.
    await waitFor(() =>
      expect(
        screen.queryByRole("button", {
          name: "Edit the arguments of send_notification",
        }),
      ).toBeNull(),
    );
    expect(
      screen.queryByLabelText("Arguments for send_notification, as JSON"),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Approve send_notification" }),
    ).toBeTruthy();
    expect(posts).toBe(1);
  });
});

describe("a task's page with a proposal, further", () => {
  const paused = (
    id: string,
    args = { channel: "#ops", message: "Ready." },
  ) => ({
    json: state([human("Tell #ops."), ASKED], {
      pending: {
        ...PROPOSAL(id),
        action_requests: [{ ...PROPOSAL(id).action_requests[0], args }],
      },
    }),
  });

  it("opens a new proposal as a new card, with nothing of the one decided elsewhere", async () => {
    let reads = 0;
    serve((method, path) => {
      if (method === "POST") {
        return refusal(409, "proposal_mismatch", "Not the pending proposal.");
      }
      if (path.endsWith("/state")) {
        reads += 1;
        return reads === 1
          ? paused("int-1")
          : paused("int-2", { channel: "#dev", message: "Other." });
      }
      return { json: summary({ status: "needs_decision" }) };
    });
    renderView();
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Edit the arguments of send_notification",
      }),
    );
    fireEvent.change(
      screen.getByLabelText("Arguments for send_notification, as JSON"),
      { target: { value: '{"channel": "#mine", "message": "Mine."}' } },
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Approve send_notification with these arguments",
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("article", {
          name: "Proposed action send_notification",
        }).textContent,
      ).toContain('"channel": "#dev"'),
    );
    // The new card starts closed: the edit made for the earlier proposal is gone.
    expect(
      screen.queryByLabelText("Arguments for send_notification, as JSON"),
    ).toBeNull();
    expect(screen.getByRole("alert").textContent).toMatch(
      /no longer the pending one/,
    );
  });

  it("drops the notice once the task has moved on from the proposal it was about", async () => {
    let reads = 0;
    let decided = false;
    serve((method, path) => {
      if (method === "POST") {
        return refusal(409, "run_active", "A run is already active.");
      }
      if (path.endsWith("/state")) {
        reads += 1;
        return decided
          ? { json: state([human("Tell #ops."), ASKED, ai("Sent.", "ai-2")]) }
          : paused("int-1");
      }
      return { json: summary({ status: decided ? "done" : "needs_decision" }) };
    });
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    );
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /A run is in progress/,
    );
    await waitFor(() => expect(reads).toBeGreaterThan(1));

    decided = true;
    fireEvent.focus(window);
    await waitFor(() => expect(conversation()).toContain("Sent."));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers no decision while a run is in progress on the task", async () => {
    serve((_method, path) =>
      path.endsWith("/state")
        ? paused("int-1")
        : { json: summary({ status: "running" }) },
    );
    renderView();
    expect(
      await screen.findByText(
        "A run is in progress. This page reads the task again until it ends.",
      ),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Approve send_notification" }),
    ).toBeNull();
  });

  it("keeps the reason a continuation failed beside the proposal that is still pending", async () => {
    serve((method, path) => {
      if (method === "POST") {
        return {
          stream: body(
            START,
            sse("error", {
              code: "approval_now_required",
              message:
                "This step cannot continue: the agent now requires approval.",
            }),
          ),
        };
      }
      return path.endsWith("/state")
        ? paused("int-1")
        : { json: summary({ status: "needs_decision" }) };
    });
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    );
    expect(
      await screen.findByText(
        "This step cannot continue: the agent now requires approval.",
      ),
    ).toBeTruthy();
    // Read again, the task still waits on the proposal; the reason stays beside it.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Approve send_notification" }),
      ).toBeTruthy(),
    );
    expect(
      screen.getByText(
        "This step cannot continue: the agent now requires approval.",
      ),
    ).toBeTruthy();
  });
});

// Phase 6: the list of tasks, and with it the sidebar's number, follows a run's end at once.

function ListReader() {
  useSWR(THREADS_KEY, listThreads);
  return null;
}

describe("a task's page and the list of tasks", () => {
  it("has the list read again when a run it shows ends", async () => {
    const asked = serve((_method, path) => {
      if (path === "/threads") {
        return {
          json: { threads: [], needs_decision: 0, total: 0, more: false },
        };
      }
      return path.endsWith("/state")
        ? { json: state([human("Hi."), ai("Hello.")]) }
        : { json: summary() };
    });
    const answer = openBody();
    begin("t-1", "Hi.", answer.stream);
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <ListReader />
        <TaskView threadId="t-1" />
      </SWRConfig>,
    );
    const lists = () => asked.filter(({ path }) => path === "/threads").length;
    await waitFor(() => expect(lists()).toBe(1));
    answer.send(START, END);
    answer.close();
    await waitFor(() => expect(lists()).toBe(2));
  });
});

// Phase 7: follow-ups, agent tasks, and following a task by reading it (FR-009, FR-013, FR-020).

const FOLLOW_UP = "Send another message on this task";
const followUp = () => screen.getByLabelText(FOLLOW_UP) as HTMLTextAreaElement;
const sendMessage = () =>
  screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement;
const items = () =>
  screen
    .getByRole("list", { name: "Conversation" })
    .querySelectorAll(":scope > li");

describe("a task's page and another message", () => {
  it("sends a follow-up on the same task and shows its run below the conversation, once", async () => {
    let finished = false;
    const answer = openBody();
    const asked = serve((method, path) => {
      if (method === "POST") {
        return { stream: answer.stream };
      }
      if (path.endsWith("/state")) {
        return {
          json: state(
            finished
              ? [
                  human("Summarize the week."),
                  ai("Here it is."),
                  human("And the week before?", "h-2"),
                  ai("Quieter.", "ai-2"),
                ]
              : [human("Summarize the week."), ai("Here it is.")],
          ),
        };
      }
      return { json: summary() };
    });
    renderView();
    await waitFor(() => expect(conversation()).toContain("Here it is."));
    expect(screen.getByText("Done")).toBeTruthy();

    fireEvent.change(followUp(), { target: { value: "And the week before?" } });
    sendMessage().focus();
    fireEvent.click(sendMessage());

    await waitFor(() =>
      expect(conversation()).toContain("And the week before?"),
    );
    expect(asked.filter(({ method }) => method === "POST")).toEqual([
      {
        method: "POST",
        path: "/threads/t-1/runs/unattended",
        body: { message: "And the week before?" },
      },
    ]);
    answer.send(START, chunk("Quieter", "ai-2"));
    await waitFor(() => expect(conversation()).toContain("Quieter"));
    // The earlier conversation stands above what streams, and is not shown twice.
    expect(items()).toHaveLength(4);
    expect(conversation()).toContain("Here it is.");
    expect(screen.getByText("Running")).toBeTruthy();
    // The box is empty, and takes no message while the run is in progress.
    expect(followUp().value).toBe("");
    fireEvent.change(followUp(), { target: { value: "One more." } });
    expect(sendMessage().disabled).toBe(true);
    expect(
      screen.getByText(
        "A run is in progress on this task. A message can be sent once it has ended.",
      ),
    ).toBeTruthy();
    // The run's status has the focus, which the emptied box gave up.
    const status = document.activeElement;
    expect(status?.tagName).toBe("OUTPUT");
    expect(status?.textContent).toMatch(/The run has started/);

    finished = true;
    answer.send(END);
    answer.close();
    await waitFor(() => expect(conversation()).toContain("Quieter."));
    expect(items()).toHaveLength(4);
    await waitFor(() => expect(sendMessage().disabled).toBe(false));
    // What was typed during the run is still there to send.
    expect(followUp().value).toBe("One more.");
    // The focus stayed where it was, on the status, which now says the run has ended.
    expect(document.activeElement).toBe(status);
    expect(status?.textContent).toBe("The run has ended.");
  });

  it("offers a follow-up's text again when its run failed before the message was recorded", async () => {
    let failed = false;
    const second = openBody();
    serve((method, path) => {
      if (method === "POST") {
        if (failed) {
          return { stream: second.stream };
        }
        failed = true;
        return { stream: body(START, NO_USABLE_MODEL) };
      }
      if (path.endsWith("/state")) {
        return {
          json: state([human("Summarize the week."), ai("Here it is.")]),
        };
      }
      return {
        json: failed
          ? summary({
              status: "failed",
              error: {
                code: "no_usable_model",
                message: "No usable model is active.",
              },
            })
          : summary(),
      };
    });
    renderView();
    await waitFor(() => expect(conversation()).toContain("Here it is."));
    fireEvent.change(followUp(), { target: { value: "And the week before?" } });
    fireEvent.click(sendMessage());

    await waitFor(() => expect(screen.getByText("Failed")).toBeTruthy());
    // The run is forgotten, and the reason is the service's, with the way to the Models section.
    await waitFor(() => expect(get("t-1")).toBeUndefined());
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(
      screen
        .getByRole("link", { name: "Go to the Models section" })
        .getAttribute("href"),
    ).toBe("/llm");
    // Nothing of the message was recorded: it is in the box, to be sent again.
    await waitFor(() => expect(followUp().value).toBe("And the week before?"));
    expect(conversation()).not.toContain("And the week before?");
    expect(sendMessage().disabled).toBe(false);

    // Sent again, it leaves the box for good.
    fireEvent.click(sendMessage());
    await waitFor(() => expect(get("t-1")?.ended).toBeNull());
    expect(followUp().value).toBe("");
    second.send(START, END);
    second.close();
  });

  it("offers it again also when the conversation grew meanwhile by what no person wrote", async () => {
    let failed = false;
    serve((method, path) => {
      if (method === "POST") {
        failed = true;
        return { stream: body(START, NO_USABLE_MODEL) };
      }
      if (path.endsWith("/state")) {
        return {
          json: state(
            failed
              ? [
                  human("Summarize the week."),
                  ai("Here it is."),
                  ai("An agent's outcome, recorded meanwhile.", "ai-2"),
                ]
              : [human("Summarize the week."), ai("Here it is.")],
          ),
        };
      }
      return { json: summary() };
    });
    renderView();
    await waitFor(() => expect(conversation()).toContain("Here it is."));
    fireEvent.change(followUp(), { target: { value: "And the week before?" } });
    fireEvent.click(sendMessage());
    await waitFor(() => expect(get("t-1")).toBeUndefined());
    await waitFor(() => expect(followUp().value).toBe("And the week before?"));
  });

  it("does not offer a follow-up's text again when its message was recorded before the run failed", async () => {
    let failed = false;
    serve((method, path) => {
      if (method === "POST") {
        failed = true;
        return {
          stream: body(
            START,
            sse("error", { code: "model_error", message: "The model failed." }),
          ),
        };
      }
      if (path.endsWith("/state")) {
        return {
          json: state(
            failed
              ? [
                  human("Summarize the week."),
                  ai("Here it is."),
                  human("And the week before?", "h-2"),
                ]
              : [human("Summarize the week."), ai("Here it is.")],
          ),
        };
      }
      return {
        json: failed
          ? summary({
              status: "failed",
              error: { code: "model_error", message: "The model failed." },
            })
          : summary(),
      };
    });
    renderView();
    await waitFor(() => expect(conversation()).toContain("Here it is."));
    fireEvent.change(followUp(), { target: { value: "And the week before?" } });
    fireEvent.click(sendMessage());

    await waitFor(() => expect(get("t-1")).toBeUndefined());
    await waitFor(() => expect(items()).toHaveLength(3));
    expect(screen.getByText("Failed")).toBeTruthy();
    // The message is in the conversation and is not sent again by itself.
    expect(followUp().value).toBe("");
  });

  it("reads the task again when the service says a run is already in progress", async () => {
    let refused = false;
    const asked = serve((method, path) => {
      if (method === "POST") {
        refused = true;
        return refusal(409, "run_active", "A run is already active.");
      }
      if (path.endsWith("/state")) {
        return {
          json: state([human("Summarize the week."), ai("Here it is.")]),
        };
      }
      return { json: summary({ status: refused ? "running" : "done" }) };
    });
    renderView();
    await waitFor(() => expect(conversation()).toContain("Here it is."));
    fireEvent.change(followUp(), { target: { value: "Too late." } });
    fireEvent.click(sendMessage());

    expect(
      await screen.findByText(
        "A run is in progress. This page reads the task again until it ends.",
      ),
    ).toBeTruthy();
    expect(sendMessage().disabled).toBe(true);
    expect(followUp().value).toBe("Too late.");
    // The conversation is read again too, not the row alone.
    await waitFor(() =>
      expect(asked.filter(({ path }) => path.endsWith("/state"))).toHaveLength(
        2,
      ),
    );
  });

  it("offers no box before the task's status has been read", async () => {
    serve((_method, path) =>
      path.endsWith("/state")
        ? { json: state([human("Summarize the week."), ai("Here it is.")]) }
        : { fails: "refused" },
    );
    renderView();
    await screen.findByRole("alert");
    await waitFor(() => expect(conversation()).toContain("Here it is."));
    expect(screen.queryByLabelText(FOLLOW_UP)).toBeNull();
  });
});

describe("a task's page and a run it does not read", () => {
  it("says a run is in progress, offers no message and no decision, and reads until the run has ended", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let running = true;
      const asked = serve((_method, path) =>
        path.endsWith("/state")
          ? {
              json: state([human("Tell #ops."), ASKED], {
                pending: running ? PROPOSAL("int-1") : null,
              }),
            }
          : { json: summary({ status: running ? "running" : "done" }) },
      );
      const reads = (tail: string) =>
        asked.filter(({ path }) => path === `/threads/t-1${tail}`).length;
      renderView();
      expect(
        await screen.findByText(
          "A run is in progress. This page reads the task again until it ends.",
        ),
      ).toBeTruthy();
      await waitFor(() => expect(conversation()).toContain("Tell #ops."));
      fireEvent.change(followUp(), { target: { value: "And then?" } });
      expect(sendMessage().disabled).toBe(true);
      expect(
        screen.getByText(
          "A run is in progress on this task. A message can be sent once it has ended.",
        ),
      ).toBeTruthy();
      expect(
        screen.queryByRole("button", { name: "Approve send_notification" }),
      ).toBeNull();
      expect([reads(""), reads("/state")]).toEqual([1, 1]);

      // The row and the state are read again by the timer.
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect([reads(""), reads("/state")]).toEqual([2, 2]));
      running = false;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
      await waitFor(() => expect(sendMessage().disabled).toBe(false));
      // The conversation is read once more after the reading that showed the run's end.
      await waitFor(() => expect([reads(""), reads("/state")]).toEqual([3, 4]));

      // Nothing is pending: the page reads no more.
      vi.advanceTimersByTime(10 * AGENT_SECONDS * 1000);
      expect([reads(""), reads("/state")]).toEqual([3, 4]);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a task's page and the end of a run it followed", () => {
  it("reads the conversation once more, since the reading that showed the end may hold less", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let ended = false;
      let states = 0;
      serve((_method, path) => {
        if (path.endsWith("/state")) {
          states += 1;
          // The reading asked for together with the row's is answered from before the run's
          // last step; only a later one holds the reply.
          return {
            json: state(
              states > 2
                ? [human("Count slowly."), ai("One, two, three.")]
                : [human("Count slowly.")],
            ),
          };
        }
        return { json: summary({ status: ended ? "done" : "running" }) };
      });
      renderView();
      expect(
        await screen.findByText(
          "A run is in progress. This page reads the task again until it ends.",
        ),
      ).toBeTruthy();

      ended = true;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
      // No timer brings the reply: the page reads the conversation again by itself.
      await waitFor(() => expect(conversation()).toContain("One, two, three."));
      expect(states).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a task's page and its agent tasks", () => {
  const HANDED = [
    human("Hand this to the stub agent: slow 40."),
    ai("Handed."),
  ];
  const OUTCOME = [...HANDED, ai("The stub agent finished: done.", "ai-2")];
  const ENDED = agentTask({
    status: "completed",
    ended_at: "2026-11-20T08:01:00Z",
    outcome_delivered: true,
  });

  function waiting(
    now: () => {
      status?: "waiting_for_agent" | "done" | "running";
      messages?: ReturnType<typeof human>[];
      task?: ReturnType<typeof agentTask>;
      down?: boolean;
    },
  ) {
    const asked = serve((_method, path) => {
      const {
        status = "waiting_for_agent",
        messages = HANDED,
        task = agentTask(),
        down = false,
      } = now();
      if (down) {
        return { fails: "refused" };
      }
      return path.endsWith("/state")
        ? { json: state(messages, { agent_tasks: [task] }) }
        : { json: summary({ status }) };
    });
    return (tail: string) =>
      asked.filter(({ path }) => path === `/threads/t-1${tail}`).length;
  }

  const agentTasks = () =>
    screen.queryByRole("list", { name: "Agent tasks" })?.textContent ?? "";
  const WAITING = `This page reads the task again every ${AGENT_SECONDS} seconds while an agent task is pending or its outcome is not in the conversation yet.`;

  it("lists the agent tasks, and reads the task again by the slower timer while one is pending", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let arrived = false;
      const reads = waiting(() =>
        arrived ? { status: "done", messages: OUTCOME, task: ENDED } : {},
      );
      renderView();
      expect(await screen.findByText("Waiting for an agent")).toBeTruthy();
      await waitFor(() => expect(agentTasks()).toContain("slow 40"));
      expect(agentTasks()).toContain("stub");
      expect(agentTasks()).toContain("Pending");
      expect(screen.getByText(WAITING)).toBeTruthy();
      // A task that waits for an agent takes another message (FR-020).
      expect(sendMessage().disabled).toBe(true);
      fireEvent.change(followUp(), { target: { value: "Any news?" } });
      expect(sendMessage().disabled).toBe(false);
      expect([reads(""), reads("/state")]).toEqual([1, 1]);

      // Not at the pace of a run that is followed, but at the slower one.
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      expect([reads(""), reads("/state")]).toEqual([1, 1]);
      vi.advanceTimersByTime((AGENT_SECONDS - FOLLOW_SECONDS) * 1000);
      await waitFor(() => expect([reads(""), reads("/state")]).toEqual([2, 2]));

      // The outcome arrives: the page shows it without a reload.
      arrived = true;
      vi.advanceTimersByTime(AGENT_SECONDS * 1000);
      await waitFor(() =>
        expect(conversation()).toContain("The stub agent finished: done."),
      );
      expect(screen.getByText("Done")).toBeTruthy();
      expect(agentTasks()).toContain("Completed");
      expect(agentTasks()).not.toContain("Pending");
      expect(screen.queryByText(WAITING)).toBeNull();
      expect([reads(""), reads("/state")]).toEqual([3, 3]);
      // What the person typed meanwhile is still in the box.
      expect(followUp().value).toBe("Any news?");

      // Nothing is pending: the page reads no more.
      vi.advanceTimersByTime(10 * AGENT_SECONDS * 1000);
      expect([reads(""), reads("/state")]).toEqual([3, 3]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("goes on reading while an ended agent task's outcome is not in the conversation yet", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let delivered = false;
      const reads = waiting(() => ({
        // The agent task ended; no run has recorded its outcome yet, so the task reads done.
        status: "done",
        messages: delivered ? OUTCOME : HANDED,
        task: { ...ENDED, outcome_delivered: delivered },
      }));
      renderView();
      await waitFor(() =>
        expect(agentTasks()).toContain(
          "Its outcome is not in the conversation yet.",
        ),
      );
      vi.advanceTimersByTime(AGENT_SECONDS * 1000);
      await waitFor(() => expect(reads("/state")).toBe(2));

      delivered = true;
      vi.advanceTimersByTime(AGENT_SECONDS * 1000);
      await waitFor(() =>
        expect(conversation()).toContain("The stub agent finished: done."),
      );
      vi.advanceTimersByTime(10 * AGENT_SECONDS * 1000);
      expect(reads("/state")).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("goes on reading past a reading that failed", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let step = 0;
      const reads = waiting(() =>
        step === 1
          ? { down: true }
          : step === 2
            ? { status: "done", messages: OUTCOME, task: ENDED }
            : {},
      );
      renderView();
      await waitFor(() => expect(agentTasks()).toContain("slow 40"));

      step = 1;
      vi.advanceTimersByTime(AGENT_SECONDS * 1000);
      expect((await screen.findByRole("alert")).textContent).toMatch(
        /cannot be reached/,
      );
      // What was read before stays on the page.
      expect(conversation()).toContain("Handed.");

      step = 2;
      vi.advanceTimersByTime(AGENT_SECONDS * 1000);
      await waitFor(() =>
        expect(conversation()).toContain("The stub agent finished: done."),
      );
      expect(screen.queryByRole("alert")).toBeNull();
      expect(reads("")).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("follows the run that reports an agent's outcome at the pace of a run", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let step = 0;
      const reads = waiting(() =>
        step === 0
          ? {}
          : step === 1
            ? {
                status: "running",
                task: { ...ENDED, outcome_delivered: false },
              }
            : { status: "done", messages: OUTCOME, task: ENDED },
      );
      renderView();
      await waitFor(() => expect(agentTasks()).toContain("slow 40"));

      step = 1;
      vi.advanceTimersByTime(AGENT_SECONDS * 1000);
      expect(
        await screen.findByText(
          "A run is in progress. This page reads the task again until it ends.",
        ),
      ).toBeTruthy();
      expect(sendMessage().disabled).toBe(true);
      // One thing is said of the task at a time: the run, not the agent.
      expect(screen.queryByText(WAITING)).toBeNull();

      step = 2;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(conversation()).toContain("The stub agent finished: done."),
      );
      expect(reads("")).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads nothing while the page is hidden", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const visibility = vi.spyOn(document, "visibilityState", "get");
    try {
      const reads = waiting(() => ({}));
      renderView();
      await waitFor(() => expect(agentTasks()).toContain("slow 40"));

      visibility.mockReturnValue("hidden");
      vi.advanceTimersByTime(3 * AGENT_SECONDS * 1000);
      expect(reads("")).toBe(1);

      visibility.mockReturnValue("visible");
      vi.advanceTimersByTime(AGENT_SECONDS * 1000);
      await waitFor(() => expect(reads("")).toBe(2));
    } finally {
      visibility.mockRestore();
      vi.useRealTimers();
    }
  });

  // Feature 008 (T025): what a working agent last reported, and how long ago (FR-010,
  // FR-011, research R8). The clock is faked with the timer, so the age moves with it.

  const LEARNED = "2026-11-25T08:00:00Z";
  const reported = (text: string) =>
    agentTask({ progress: { text, learned_at: LEARNED } });
  const lastReported = () =>
    screen.queryByText(/^Last reported by the agent/)?.textContent ?? "";
  const progressText = () =>
    screen.queryByRole("region", { name: "Progress reported by stub" })
      ?.textContent ?? null;

  /**
   * Wait, in real time, until the task's state has been asked for `count` times. Testing
   * Library's own wait looks again by the faked `setInterval`, or when the page changes,
   * which a reading that answers the same task does not do.
   */
  async function readsOf(reads: (tail: string) => number, count: number) {
    for (
      let waited = 0;
      reads("/state") < count && waited < 2000;
      waited += 10
    ) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
    expect(reads("/state")).toBe(count);
    // The count moves when the request is made; the reading is answered a moment later, and
    // the view asks for no other while one is under way.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }

  /** One reading of the view's timer, answered. */
  async function oneReading(reads: (tail: string) => number) {
    const before = reads("/state");
    vi.advanceTimersByTime(AGENT_SECONDS * 1000);
    await readsOf(reads, before + 1);
  }

  it("shows how long ago the agent reported, and the age moves at each reading though the task did not", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(Date.parse(LEARNED) + 30_000);
    try {
      const reads = waiting(() => ({ task: reported("Half done") }));
      renderView();
      await waitFor(() => expect(lastReported()).toContain("just now"));
      expect(progressText()).toBe("Half done");

      // Five readings that answer the same task: 90 seconds after the text was learned.
      for (let n = 0; n < 5; n += 1) {
        await oneReading(reads);
      }
      await waitFor(() => expect(lastReported()).toContain("1 minute ago"));
      // Eight: 126 seconds.
      for (let n = 0; n < 3; n += 1) {
        await oneReading(reads);
      }
      await waitFor(() => expect(lastReported()).toContain("2 minutes ago"));
      expect(progressText()).toBe("Half done");
    } finally {
      vi.useRealTimers();
    }
  });

  it("neither reads nor moves the age while the page is hidden, and reads once it is shown", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(Date.parse(LEARNED) + 30_000);
    const visibility = vi.spyOn(document, "visibilityState", "get");
    try {
      const reads = waiting(() => ({ task: reported("Half done") }));
      renderView();
      await waitFor(() => expect(lastReported()).toContain("just now"));
      const read = reads("/state");

      visibility.mockReturnValue("hidden");
      document.dispatchEvent(new Event("visibilitychange"));
      vi.advanceTimersByTime(10 * AGENT_SECONDS * 1000);
      // What the timer's callbacks changed is rendered before the age is looked at.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
      expect(reads("/state")).toBe(read);
      expect(lastReported()).toContain("just now");

      visibility.mockReturnValue("visible");
      document.dispatchEvent(new Event("visibilitychange"));
      await readsOf(reads, read + 1);
      // Shown again, the page words the age from the browser's time at once (T045); that the
      // reading made then does too is the test of the age moving at each reading.
      expect(lastReported()).toContain("2 minutes ago");
    } finally {
      visibility.mockRestore();
      vi.useRealTimers();
    }
  });

  it("shows another text when a reading brings one", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let text = "Step 1 of 3";
      const reads = waiting(() => ({ task: reported(text) }));
      renderView();
      await waitFor(() => expect(progressText()).toBe("Step 1 of 3"));

      text = "Step 2 of 3";
      await oneReading(reads);
      await waitFor(() => expect(progressText()).toBe("Step 2 of 3"));
    } finally {
      vi.useRealTimers();
    }
  });

  it("starts no timer more for a task whose agent reports progress", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const timers = vi.spyOn(globalThis, "setInterval");
    try {
      const started = async (task: ReturnType<typeof agentTask>) => {
        timers.mockClear();
        const reads = waiting(() => ({ task }));
        const { unmount } = renderView();
        await waitFor(() => expect(agentTasks()).toContain("slow 40"));
        await oneReading(reads);
        const count = timers.mock.calls.length;
        unmount();
        return count;
      };
      const without = await started(agentTask());
      const withProgress = await started(reported("Half done"));
      expect(withProgress).toBe(without);
      expect(without).toBeGreaterThan(0);
    } finally {
      timers.mockRestore();
      vi.useRealTimers();
    }
  });

  // Phase 8 (T045, T046): the age is worded at once when the page is shown or opened again.

  it("words the age as soon as a hidden page is shown again, also when the service cannot be reached", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(Date.parse(LEARNED) + 30_000);
    const visibility = vi.spyOn(document, "visibilityState", "get");
    try {
      let down = false;
      waiting(() => (down ? { down: true } : { task: reported("Half done") }));
      renderView();
      await waitFor(() => expect(lastReported()).toContain("just now"));

      visibility.mockReturnValue("hidden");
      document.dispatchEvent(new Event("visibilitychange"));
      vi.advanceTimersByTime(10 * AGENT_SECONDS * 1000);
      // Shown again while the service cannot be reached: the reading made then fails, and
      // no tick of the timer has come yet.
      down = true;
      visibility.mockReturnValue("visible");
      document.dispatchEvent(new Event("visibilitychange"));
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
      expect(lastReported()).toContain("2 minutes ago");
      expect(progressText()).toBe("Half done");
    } finally {
      visibility.mockRestore();
      vi.useRealTimers();
    }
  });

  it("words the age as soon as the page is opened again, though its reading is one already answered", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(Date.parse(LEARNED) + 30_000);
    try {
      waiting(() => ({ task: reported("Half done") }));
      // One cache for both pages, and a reading made within two seconds of another taken
      // as that one, as the reading library does by default.
      const cache = new Map();
      const page = () => (
        <SWRConfig value={{ provider: () => cache, dedupingInterval: 2000 }}>
          <TaskView threadId="t-1" />
        </SWRConfig>
      );
      const first = render(page());
      await waitFor(() => expect(lastReported()).toContain("just now"));
      first.unmount();

      vi.setSystemTime(Date.parse(LEARNED) + 90_000);
      render(page());
      await waitFor(() => expect(progressText()).toBe("Half done"));
      expect(lastReported()).toContain("1 minute ago");
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the progress it last read when readings fail, under feature 007's notice, and its age goes on", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(Date.parse(LEARNED) + 30_000);
    try {
      let down = false;
      const reads = waiting(() =>
        down ? { down: true } : { task: reported("Half done") },
      );
      renderView();
      await waitFor(() => expect(lastReported()).toContain("just now"));

      down = true;
      for (let n = 0; n < 5; n += 1) {
        await oneReading(reads);
      }
      expect((await screen.findByRole("alert")).textContent).toMatch(
        /cannot be reached/,
      );
      expect(progressText()).toBe("Half done");
      // The age is the browser's: it never claims the text is newer than it is.
      expect(lastReported()).toContain("1 minute ago");
    } finally {
      vi.useRealTimers();
    }
  });
});

// Phase 12 (T100), FR-013: a page that was hidden reads the task again when it is shown,
// without waiting for its timer. The reading library does that by default; nothing else
// held the page to it, and a setting that turned it off would have left a page that comes
// back showing what it held when it was left.

describe("a task's page that is shown again", () => {
  // T113: within five seconds of the page being opened, as here, too.
  it("reads the task again at once, by no timer", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const visibility = vi.spyOn(document, "visibilityState", "get");
    try {
      let status: "running" | "done" = "running";
      const asked = { row: 0, state: 0 };
      serve((_method, path) => {
        const what = path.endsWith("/state") ? "state" : "row";
        asked[what] += 1;
        return what === "state"
          ? {
              json: state(
                status === "done"
                  ? [human("Count slowly."), ai("One, two, three.")]
                  : [human("Count slowly.")],
                { run_active: status === "running" },
              ),
            }
          : { json: summary({ first_message: "Count slowly.", status }) };
      });
      renderView();
      expect(await screen.findByText("Running")).toBeTruthy();
      expect(asked).toEqual({ row: 1, state: 1 });

      // The tab goes to the background; the run ends meanwhile.
      visibility.mockReturnValue("hidden");
      document.dispatchEvent(new Event("visibilitychange"));
      status = "done";
      vi.advanceTimersByTime(3 * FOLLOW_SECONDS * 1000);
      expect(asked).toEqual({ row: 1, state: 1 });

      // It is shown again: no timer is advanced from here on.
      visibility.mockReturnValue("visible");
      document.dispatchEvent(new Event("visibilitychange"));
      await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
      await waitFor(() => expect(conversation()).toContain("One, two, three."));
      expect(asked.row).toBeGreaterThanOrEqual(2);
      expect(asked.state).toBeGreaterThanOrEqual(2);
    } finally {
      visibility.mockRestore();
      vi.useRealTimers();
    }
  });
});

// Phase 7, from the review: what the page holds from before a run says nothing of that run.

describe("a task's page and the periods it reads at", () => {
  it("follows a run and an agent task more often than the five and fifteen seconds the spec gives as the most", () => {
    // A reading takes time of its own: a period equal to the bound would exceed it.
    expect(FOLLOW_SECONDS).toBeLessThan(5);
    expect(AGENT_SECONDS).toBeLessThan(15);
    expect(FOLLOW_SECONDS).toBeLessThan(AGENT_SECONDS);
  });
});

describe("a task's page and a follow-up whose end it cannot read at once", () => {
  const BEFORE = [human("Summarize the week."), ai("Here it is.")];
  const AFTER = [
    ...BEFORE,
    human("And the week before?", "h-2"),
    ai("Quieter.", "ai-2"),
  ];
  const CUT = /The connection to the run ended before the run did/;
  const IN_PROGRESS =
    "A run is in progress. This page reads the task again until it ends.";
  const pause = (ms: number) =>
    new Promise((resolve) => setTimeout(resolve, ms));

  /**
   * A finished task on which a follow-up is sent. The follow-up's stream is `sent`; every
   * reading after it takes 30 ms, as over a network, and is answered by `now`.
   */
  async function followedUp(
    sent: string[],
    now: () => {
      status?: "running" | "done";
      messages?: ReturnType<typeof human>[];
      down?: boolean;
    },
  ) {
    let posted = false;
    serve((method, path) => {
      if (method === "POST") {
        posted = true;
        return { stream: body(...sent) };
      }
      if (!posted) {
        return path.endsWith("/state")
          ? { json: state(BEFORE) }
          : { json: summary() };
      }
      const { status = "running", messages = AFTER, down = false } = now();
      const answer: Answer = down
        ? { fails: "refused" }
        : path.endsWith("/state")
          ? { json: state(messages) }
          : { json: summary({ status }) };
      return later(30, answer);
    });
    renderView();
    await waitFor(() => expect(conversation()).toContain("Here it is."));
    fireEvent.change(followUp(), { target: { value: "And the week before?" } });
    fireEvent.click(sendMessage());
    await waitFor(() => expect(get("t-1")?.ended ?? null).not.toBeNull());
    fireEvent.change(followUp(), { target: { value: "One more." } });
  }

  it("says that the connection ended and follows the run, whatever the row it held said", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let done = false;
      await followedUp([START, chunk("Quiet", "ai-2")], () => ({
        status: done ? "done" : "running",
        messages: done ? AFTER : AFTER.slice(0, 3),
      }));

      // The row the page held said done, of the task before this run: it ends nothing.
      await pause(120);
      expect(screen.getByText(CUT)).toBeTruthy();
      expect(screen.getByText(IN_PROGRESS)).toBeTruthy();
      expect(screen.queryByText("The run failed.")).toBeNull();
      expect(sendMessage().disabled).toBe(true);

      done = true;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(conversation()).toContain("Quieter."));
      await waitFor(() => expect(screen.queryByText(CUT)).toBeNull());
      expect(screen.getByText("Done")).toBeTruthy();
      await waitFor(() => expect(sendMessage().disabled).toBe(false));
    } finally {
      vi.useRealTimers();
    }
  });

  it("goes on following a cut follow-up while the service cannot be reached", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let step = 0;
      await followedUp([START, chunk("Quiet", "ai-2")], () =>
        step === 0 ? { down: true } : { status: "done" },
      );

      await pause(120);
      expect(screen.getByText(CUT)).toBeTruthy();
      expect(screen.getByRole("alert").textContent).toMatch(
        /cannot be reached.*reads the task again until it can/,
      );
      // What the stream brought stays, and no message is taken meanwhile.
      expect(conversation()).toContain("And the week before?");
      expect(sendMessage().disabled).toBe(true);

      step = 1;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(conversation()).toContain("Quieter."));
      await waitFor(() => expect(screen.queryByText(CUT)).toBeNull());
      expect(screen.queryByRole("alert")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a follow-up and its reply on the page when the task cannot be read after the run", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let down = true;
      await followedUp([START, chunk("Quieter.", "ai-2"), END], () => ({
        down,
        status: "done",
      }));

      await pause(120);
      expect(screen.getByRole("alert").textContent).toMatch(
        /cannot be reached.*reads the task again until it can/,
      );
      // Not the conversation from before the run, as if nothing had been sent.
      expect(conversation()).toContain("And the week before?");
      expect(conversation()).toContain("Quieter.");
      expect(items()).toHaveLength(4);
      expect(sendMessage().disabled).toBe(true);
      expect(get("t-1")).toBeDefined();

      // The service is back: the page reads by itself, and shows what the service holds.
      down = false;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await forgotten();
      expect(screen.queryByRole("alert")).toBeNull();
      expect(items()).toHaveLength(4);
      await waitFor(() => expect(sendMessage().disabled).toBe(false));
    } finally {
      vi.useRealTimers();
    }
  });

  it("takes no message between a run's end and the reading after it", async () => {
    await followedUp([START, chunk("Quieter.", "ai-2"), END], () => ({
      status: "done",
    }));
    // The run has ended; its reading is on the way.
    expect(sendMessage().disabled).toBe(true);
    await waitFor(() => expect(get("t-1")).toBeUndefined());
    await waitFor(() => expect(sendMessage().disabled).toBe(false));
  });
});

describe("a task's page and two readings that disagree", () => {
  it("follows a run the conversation's reading says is in progress when the row's does not yet", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let active = true;
      const delivered = agentTask({
        status: "completed",
        ended_at: "2026-11-20T08:01:00Z",
        outcome_delivered: true,
      });
      const asked = serve((_method, path) =>
        path.endsWith("/state")
          ? {
              json: state(
                active
                  ? [human("Hand it over."), ai("Handed.")]
                  : [
                      human("Hand it over."),
                      ai("Handed."),
                      ai("The agent finished.", "ai-2"),
                    ],
                { run_active: active, agent_tasks: [delivered] },
              ),
            }
          : // The row was answered before the service's own run took the task.
            { json: summary({ status: "done" }) },
      );
      const states = () =>
        asked.filter(({ path }) => path.endsWith("/state")).length;
      renderView();
      expect(
        await screen.findByText(
          "A run is in progress. This page reads the task again until it ends.",
        ),
      ).toBeTruthy();
      fireEvent.change(followUp(), { target: { value: "And then?" } });
      expect(sendMessage().disabled).toBe(true);

      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(states()).toBe(2));
      active = false;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(conversation()).toContain("The agent finished."),
      );
      await waitFor(() => expect(sendMessage().disabled).toBe(false));
    } finally {
      vi.useRealTimers();
    }
  });

  it("takes no message when only the row says a proposal is pending", async () => {
    let paused = false;
    serve((_method, path) => {
      if (path.endsWith("/state")) {
        return paused
          ? { fails: "refused" }
          : { json: state([human("Tell #ops."), ai("On it.")]) };
      }
      return { json: summary({ status: paused ? "needs_decision" : "done" }) };
    });
    renderView();
    await waitFor(() => expect(conversation()).toContain("On it."));
    fireEvent.change(followUp(), { target: { value: "Do it anyway." } });
    expect(sendMessage().disabled).toBe(false);

    paused = true;
    fireEvent.focus(window);
    await waitFor(() =>
      expect(screen.getByText("Needs your decision")).toBeTruthy(),
    );
    expect(sendMessage().disabled).toBe(true);
    expect(
      screen.getByText(
        "This task waits for a decision on the proposal above. A message can be sent once it is decided.",
      ),
    ).toBeTruthy();
  });
});

describe("a task's page, a decision that was not applied, and another message", () => {
  it("stops saying that a decision was not applied once a message is sent", async () => {
    let decided = false;
    const answer = openBody();
    serve((method, path) => {
      if (method === "POST" && path.endsWith("/resume/unattended")) {
        decided = true;
        return refusal(
          409,
          "nothing_pending",
          "This thread has no pending decision.",
        );
      }
      if (method === "POST") {
        return { stream: answer.stream };
      }
      if (path.endsWith("/state")) {
        return {
          json: decided
            ? state([human("Tell #ops."), ASKED, ai("Sent.", "ai-2")])
            : state([human("Tell #ops."), ASKED], {
                pending: PROPOSAL("int-1"),
              }),
        };
      }
      return { json: summary({ status: decided ? "done" : "needs_decision" }) };
    });
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve send_notification" }),
    );
    await waitFor(() => expect(conversation()).toContain("Sent."));
    expect(screen.getByRole("alert")).toBeTruthy();

    fireEvent.change(followUp(), { target: { value: "Thanks." } });
    fireEvent.click(sendMessage());
    await waitFor(() => expect(conversation()).toContain("Thanks."));
    expect(screen.queryByRole("alert")).toBeNull();
    answer.send(START, END);
    answer.close();
  });
});

// Phase 8: leaving a run, coming back to it, and Stop (FR-021, FR-022, FR-022a).

describe("a task's page and Stop", () => {
  const STOPPING = "The run is stopping. The step under way completes first.";
  const TOO_LATE =
    "The run ended before the stop took effect. The task is shown as it ended.";
  const NO_STOP = /^This run has no Stop: the console did not start it, or/;
  const ACCEPTED = {
    status: 202,
    json: { thread_id: "t-1", stopping: true, run: 1 },
  };
  const STOPPED = sse("end", { status: "stopped" });
  const stop = () => screen.queryByRole("button", { name: /^Stop/ });
  const pause = (ms: number) =>
    new Promise((resolve) => setTimeout(resolve, ms));
  const stops = (asked: { method: string; path: string }[]) =>
    asked.filter(({ path }) => path === "/threads/t-1/stop");

  /**
   * A run of this page on a new task: what it streams is the test's, and what the service
   * holds once it has ended is `after`.
   */
  function ownRun(
    after: () => {
      row: Parameters<typeof summary>[0];
      messages: ReturnType<typeof human>[];
      pending?: PendingDecision;
    },
  ) {
    const answer = openBody();
    let over = false;
    const asked = serve((method, path) => {
      if (method === "POST") {
        return ACCEPTED;
      }
      if (!over) {
        return NOT_USED;
      }
      const { row, messages, pending } = after();
      return path.endsWith("/state")
        ? { json: state(messages, { pending: pending ?? null }) }
        : { json: summary(row) };
    });
    begin("t-1", "List the files.", answer.stream);
    renderView();
    return {
      asked,
      answer,
      end(...events: string[]) {
        over = true;
        answer.send(...events);
        answer.close();
      },
    };
  }

  it("offers Stop for a run of this page once it has started, and shows the task as stopped when the run ends so", async () => {
    const { asked, answer, end } = ownRun(() => ({
      row: { status: "stopped" },
      messages: [human("List the files."), ai("Here is the first")],
    }));
    // Not before the service has said that the run started.
    expect(stop()).toBeNull();
    answer.send(START, chunk("Here is the first"));
    const button = await screen.findByRole("button", {
      name: "Stop the run of the task: List the files.",
    });
    button.focus();
    fireEvent.click(button);

    expect(await screen.findByText(STOPPING)).toBeTruthy();
    expect(stop()).toBeNull();
    // The button is gone; the focus is on what the page says of the run.
    expect(document.activeElement?.tagName).toBe("OUTPUT");
    expect(document.activeElement?.textContent).toBe(STOPPING);
    expect(stops(asked)).toEqual([
      { method: "POST", path: "/threads/t-1/stop", body: undefined },
    ]);
    // Stopping, not stopped: the service has not said that the run has ended.
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.queryByText("Stopped")).toBeNull();

    end(STOPPED);
    await waitFor(() => expect(screen.getByText("Stopped")).toBeTruthy());
    await waitFor(() => expect(screen.queryByText(STOPPING)).toBeNull());
    // What the run completed is kept, and the task takes another message.
    expect(conversation()).toContain("Here is the first");
    expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.change(followUp(), { target: { value: "Go on." } });
    expect(sendMessage().disabled).toBe(false);
    expect(stop()).toBeNull();
  });

  it.each([
    [
      "paused on a proposal",
      sse("interrupt", PROPOSAL("int-1")),
      {
        row: { status: "needs_decision" as const },
        messages: [human("List the files."), ASKED],
        pending: PROPOSAL("int-1"),
      },
      "Needs your decision",
    ],
    [
      "finished",
      END,
      {
        row: { status: "done" as const },
        messages: [human("List the files."), ai("All of them.")],
      },
      "Done",
    ],
    [
      "failed",
      sse("error", { code: "model_error", message: "The model failed." }),
      {
        row: {
          status: "failed" as const,
          error: { code: "model_error", message: "The model failed." },
        },
        messages: [human("List the files.")],
      },
      "Failed",
    ],
  ])("shows a run that %s after a stop was asked as it ended, and says the stop came too late", async (_how, last, after, status) => {
    const { answer, end } = ownRun(() => after);
    answer.send(START);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Stop the run of the task: List the files.",
      }),
    );
    expect(await screen.findByText(STOPPING)).toBeTruthy();

    end(last);
    await waitFor(() => expect(screen.getByText(status)).toBeTruthy());
    expect(await screen.findByText(new RegExp(TOO_LATE))).toBeTruthy();
    // Never as stopped, and no longer as stopping.
    expect(screen.queryByText("Stopped")).toBeNull();
    expect(screen.queryByText(STOPPING)).toBeNull();
    expect(stop()).toBeNull();
  });

  it("stops saying that a stop came too late once another message is sent", async () => {
    const { answer, end } = ownRun(() => ({
      row: { status: "done" },
      messages: [human("List the files."), ai("All of them.")],
    }));
    answer.send(START);
    fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
    await screen.findByText(STOPPING);
    end(END);
    await screen.findByText(new RegExp(TOO_LATE));

    // The next message's run is answered as a stream that stays open.
    const next = openBody();
    serve((method, path) =>
      method === "POST"
        ? { stream: next.stream }
        : path.endsWith("/state")
          ? { json: state([human("List the files."), ai("All of them.")]) }
          : { json: summary() },
    );
    fireEvent.change(followUp(), { target: { value: "Thanks." } });
    fireEvent.click(sendMessage());
    await waitFor(() =>
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull(),
    );
    next.send(START, END);
    next.close();
  });

  it("offers Stop for a console run it does not read, says stopping, and shows it stopped when the service does", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let step = 0;
      const asked = serve((method, path) => {
        if (method === "POST") {
          step = 1;
          return ACCEPTED;
        }
        if (path.endsWith("/state")) {
          return { json: state([human("Count slowly."), ai("One")]) };
        }
        const answer = {
          json: summary({
            first_message: "Count slowly.",
            status: step < 2 ? "running" : "stopped",
            stoppable: step < 2,
            stopping: step === 1,
          }),
        };
        // The reading made after the stop takes a moment to come.
        return step === 1 ? later(60, answer) : answer;
      });
      const rows = () =>
        asked.filter(({ path }) => path === "/threads/t-1").length;
      renderView();
      fireEvent.click(
        await screen.findByRole("button", {
          name: "Stop the run of the task: Count slowly.",
        }),
      );
      await waitFor(() => expect(rows()).toBe(2));
      // Stopping from the press on, before the row says so: no Stop to press meanwhile.
      expect(screen.getByText(STOPPING)).toBeTruthy();
      expect(stop()).toBeNull();
      expect(stops(asked)).toHaveLength(1);

      // The step under way takes its time: the page still says stopping at the next reading.
      await pause(100);
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(rows()).toBe(3));
      await pause(100);
      expect(screen.getByText(STOPPING)).toBeTruthy();
      expect(screen.getByText("Running")).toBeTruthy();

      step = 2;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(screen.getByText("Stopped")).toBeTruthy());
      await waitFor(() => expect(screen.queryByText(STOPPING)).toBeNull());
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
      expect(stop()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  // Phase 14's review: a stop asked while the page read its own stream, and then the stream
  // was cut before the run's last event. How the run ends is the row's to say, also once the
  // page has forgotten its run.
  it("says that a stop came too late when its own stream was cut and the run then paused", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let phase: "streaming" | "cut" | "paused" = "streaming";
      serve((method, path) => {
        if (method === "POST") {
          return ACCEPTED;
        }
        if (phase === "streaming") {
          return NOT_USED;
        }
        if (phase === "cut") {
          return path.endsWith("/state")
            ? { json: state([human("List the files.")], { run_active: true }) }
            : {
                json: summary({
                  status: "running",
                  stoppable: true,
                  stopping: true,
                }),
              };
        }
        return path.endsWith("/state")
          ? {
              json: state([human("List the files."), ASKED], {
                pending: PROPOSAL("int-1"),
              }),
            }
          : { json: summary({ status: "needs_decision" }) };
      });
      const answer = openBody();
      begin("t-1", "List the files.", answer.stream);
      renderView();
      answer.send(START);
      fireEvent.click(
        await screen.findByRole("button", {
          name: "Stop the run of the task: List the files.",
        }),
      );
      expect(await screen.findByText(STOPPING)).toBeTruthy();

      phase = "cut";
      answer.close();
      await forgotten();
      expect(screen.getByText(STOPPING)).toBeTruthy();

      phase = "paused";
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(screen.getByText("Needs your decision")).toBeTruthy(),
      );
      expect(await screen.findByText(new RegExp(TOO_LATE))).toBeTruthy();
      expect(screen.queryByText(STOPPING)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says that a stop came too late for a run it followed by reading, which paused on a proposal", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let step = 0;
      serve((method, path) => {
        if (method === "POST") {
          step = 1;
          return ACCEPTED;
        }
        if (path.endsWith("/state")) {
          // While the run is in progress, the conversation's reading says so too. Once it
          // has ended, the conversation's reading is answered after the row's, so the page
          // holds for a moment a row that says paused beside a conversation that says a
          // run is active.
          const answer = {
            json: state([human("Tell #ops."), ASKED], {
              run_active: step < 2,
              pending: step < 2 ? null : PROPOSAL("int-1"),
            }),
          };
          return step < 2 ? answer : later(30, answer);
        }
        return {
          json: summary({
            first_message: "Tell #ops.",
            status: step < 2 ? "running" : "needs_decision",
            stoppable: step < 2,
            stopping: step === 1,
          }),
        };
      });
      renderView();
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      expect(await screen.findByText(STOPPING)).toBeTruthy();

      step = 2;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(screen.getByText("Needs your decision")).toBeTruthy(),
      );
      expect(await screen.findByText(new RegExp(TOO_LATE))).toBeTruthy();
      expect(screen.queryByText(STOPPING)).toBeNull();
      expect(screen.queryByText("Stopped")).toBeNull();
      expect(
        await screen.findByRole("button", {
          name: "Approve send_notification",
        }),
      ).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says that a run is stopping when the stop was asked elsewhere", async () => {
    serve((_method, path) =>
      path.endsWith("/state")
        ? { json: state([human("Count slowly.")]) }
        : {
            json: summary({
              status: "running",
              stoppable: true,
              stopping: true,
            }),
          },
    );
    renderView();
    expect(await screen.findByText(STOPPING)).toBeTruthy();
    expect(stop()).toBeNull();
  });

  // Phase 13 (T114): a page that reads its own run's stream reads the row too, on the same
  // timer, so that a stop asked in another window is said there as well (FR-022).
  it("says that its own run is stopping when the stop was asked in another window, and offers Stop no more", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const answer = openBody();
      let elsewhere = false;
      const asked = serve((_method, path) =>
        path.endsWith("/state")
          ? { json: state([human("List the files.")], { run_active: true }) }
          : {
              json: summary({
                status: "running",
                stoppable: true,
                stopping: elsewhere,
              }),
            },
      );
      begin("t-1", "List the files.", answer.stream);
      renderView();
      answer.send(START, chunk("Here is the first"));
      expect(await screen.findByRole("button", { name: /^Stop/ })).toBeTruthy();
      const readings = (end: string) =>
        asked.filter(({ path }) => path.endsWith(end)).length;
      const states = readings("/state");
      const rows = readings("/t-1");

      // Stop is pressed in another window.
      elsewhere = true;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      expect(await screen.findByText(STOPPING)).toBeTruthy();
      expect(stop()).toBeNull();
      expect(readings("/t-1")).toBe(rows + 1);
      // Only the row: the stream says the rest.
      expect(readings("/state")).toBe(states);
      expect(stops(asked)).toEqual([]);
      answer.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it("learns how its own run ended from the stream, not from a reading of the row made while it streams", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const answer = openBody();
      let ended = false;
      let pressed = false;
      const asked = serve((method, path) => {
        if (method === "POST") {
          pressed = true;
          return ACCEPTED;
        }
        return path.endsWith("/state")
          ? {
              json: state(
                ended
                  ? [human("List the files."), ai("Here they are.")]
                  : [human("List the files.")],
                { run_active: !ended },
              ),
            }
          : {
              json: summary({
                status: ended ? "done" : "running",
                stoppable: !ended,
                stopping: pressed && !ended,
              }),
            };
      });
      begin("t-1", "List the files.", answer.stream);
      renderView();
      answer.send(START, chunk("Here they"));
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      expect(await screen.findByText(STOPPING)).toBeTruthy();
      expect(stops(asked)).toHaveLength(1);

      // The run finishes before the stop takes effect. The row says so before the stream's
      // last event arrives; the page goes on saying stopping until that event.
      ended = true;
      const rows = asked.filter(({ path }) => path === "/threads/t-1").length;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(
          asked.filter(({ path }) => path === "/threads/t-1").length,
        ).toBeGreaterThan(rows),
      );
      expect(screen.getByText(STOPPING)).toBeTruthy();
      answer.send(chunk(" are."), END);
      answer.close();

      expect(await screen.findByText(new RegExp(TOO_LATE))).toBeTruthy();
      await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
      expect(screen.queryByText(STOPPING)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  // From the review of Phase 13: three things a reading of the row made while the page
  // reads its own run's stream must not do.
  it("learns that its own run stopped from the stream, also when a row read while it streamed says otherwise", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const answer = openBody();
      let pressed = false;
      const asked = serve((method, path) => {
        if (method === "POST") {
          pressed = true;
          return ACCEPTED;
        }
        return path.endsWith("/state")
          ? {
              json: state([human("List the files."), ai("Here")], {
                run_active: !pressed,
              }),
            }
          : // Once the stop is asked, the row says done: its note of the stop was lost.
            {
              json: summary({
                status: pressed ? "done" : "running",
                stoppable: !pressed,
              }),
            };
      });
      begin("t-1", "List the files.", answer.stream);
      renderView();
      answer.send(START, chunk("Here"));
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      expect(await screen.findByText(STOPPING)).toBeTruthy();
      const rows = asked.filter(({ path }) => path === "/threads/t-1").length;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(
          asked.filter(({ path }) => path === "/threads/t-1").length,
        ).toBeGreaterThan(rows),
      );

      answer.send(STOPPED);
      answer.close();
      await forgotten();
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
      expect(screen.queryByText(STOPPING)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says nothing of a reading of the row that failed while it reads its own run's stream", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const answer = openBody();
      let fail = false;
      const asked = serve((_method, path) =>
        path.endsWith("/state")
          ? { json: state([human("List the files.")], { run_active: true }) }
          : fail
            ? { fails: "Failed to fetch" }
            : { json: summary({ status: "running", stoppable: true }) },
      );
      begin("t-1", "List the files.", answer.stream);
      renderView();
      answer.send(START, chunk("Here"));
      expect(await screen.findByRole("button", { name: /^Stop/ })).toBeTruthy();

      fail = true;
      const rows = asked.filter(({ path }) => path === "/threads/t-1").length;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(asked.filter(({ path }) => path === "/threads/t-1").length).toBe(
          rows + 1,
        ),
      );
      // The failed answer has been taken in by the time the next piece is shown.
      await pause(50);
      answer.send(chunk(" they are."));
      await waitFor(() => expect(conversation()).toContain("Here they are."));
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.queryByText(/cannot be reached/)).toBeNull();
      answer.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says that its own run has ended, not that one is in progress, until the task is read after it, and reads the conversation once", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const answer = openBody();
      let over = false;
      let release: () => void = () => {};
      const released = new Promise<void>((resolve) => {
        release = resolve;
      });
      const asked = serve(async (_method, path) => {
        if (over) {
          await released;
        }
        if (path.endsWith("/state")) {
          return {
            json: state(
              over
                ? [human("List the files."), ai("Here they are.")]
                : [human("List the files.")],
              { run_active: !over },
            ),
          };
        }
        return { json: summary({ status: over ? "done" : "running" }) };
      });
      begin("t-1", "List the files.", answer.stream);
      renderView();
      answer.send(START, chunk("Here"));
      expect(await screen.findByRole("button", { name: /^Stop/ })).toBeTruthy();
      // The row is read while the page streams, and says that the run is in progress.
      const rows = asked.filter(({ path }) => path === "/threads/t-1").length;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(asked.filter(({ path }) => path === "/threads/t-1").length).toBe(
          rows + 1,
        ),
      );
      await pause(50);

      over = true;
      const states = asked.filter(({ path }) => path.endsWith("/state")).length;
      answer.send(chunk(" they are."), END);
      answer.close();
      await waitFor(() => expect(get("t-1")?.ended?.kind).toBe("event"));
      const status = () => document.querySelector("section > output");
      await waitFor(() =>
        expect(status()?.textContent).toBe("The run has ended."),
      );

      release();
      await forgotten();
      await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
      await pause(50);
      expect(status()?.textContent).toBe("The run has ended.");
      expect(
        asked.filter(({ path }) => path.endsWith("/state")).length - states,
      ).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers no Stop for a run in progress that the console did not start, and says why", async () => {
    serve((_method, path) =>
      path.endsWith("/state")
        ? { json: state([human("Count slowly.")]) }
        : { json: summary({ status: "running", stoppable: false }) },
    );
    renderView();
    expect(await screen.findByText(NO_STOP)).toBeTruthy();
    expect(stop()).toBeNull();
  });

  it.each([
    ["done", {}],
    ["stopped", {}],
    ["needs_decision", { pending: PROPOSAL("int-1") }],
  ] as const)("offers no Stop, and says nothing of one, for a task that is %s", async (status, values) => {
    serve((_method, path) =>
      path.endsWith("/state")
        ? { json: state([human("Tell #ops."), ASKED], values) }
        : // A row that says stoppable with no run in progress is not believed.
          { json: summary({ status, stoppable: true }) },
    );
    renderView();
    await waitFor(() => expect(conversation()).toContain("Tell #ops."));
    expect(stop()).toBeNull();
    expect(screen.queryByText(NO_STOP)).toBeNull();
    expect(screen.queryByText(STOPPING)).toBeNull();
  });

  it("says that no run is in progress when the run ended as Stop was pressed, and shows the task as it is", async () => {
    let pressed = false;
    const asked = serve((method, path) => {
      if (method === "POST") {
        pressed = true;
        return refusal(409, "no_run", "No run is in progress on this thread.");
      }
      if (path.endsWith("/state")) {
        return {
          json: state(
            pressed
              ? [human("Count slowly."), ai("One, two, three.")]
              : [human("Count slowly.")],
          ),
        };
      }
      return {
        json: summary({
          status: pressed ? "done" : "running",
          stoppable: !pressed,
        }),
      };
    });
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));

    expect((await screen.findByRole("alert")).textContent).toMatch(
      /No run is in progress on this task any more, so nothing was stopped/,
    );
    await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
    await waitFor(() => expect(conversation()).toContain("One, two, three."));
    expect(screen.queryByText("Stopped")).toBeNull();
    expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
    expect(screen.queryByText(STOPPING)).toBeNull();
    expect(stop()).toBeNull();
    expect(stops(asked)).toHaveLength(1);
  });

  it("offers Stop again when the service says another run is in progress, not the one that was stopping", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let pressed = false;
      serve((method, path) => {
        if (method === "POST") {
          pressed = true;
          return ACCEPTED;
        }
        if (path.endsWith("/state")) {
          return { json: state([human("Count slowly.")]) };
        }
        // After the stop the row is another run's: in progress, and nobody asked it to stop.
        return {
          json: summary({
            status: "running",
            stoppable: true,
            stopping: false,
            runs: pressed ? 2 : 1,
            changed_at: pressed
              ? "2026-11-20T08:05:00Z"
              : "2026-11-20T08:00:04Z",
          }),
        };
      });
      renderView();
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      // The reading after the stop says the run in progress is not stopping.
      await waitFor(() => expect(stop()).not.toBeNull());
      expect(screen.queryByText(STOPPING)).toBeNull();
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

// Phase 11 (T082): the row names its run, so a page that follows a run by reading knows
// whether a row still speaks of the run it asked to stop (FR-022a).

describe("a task's page that follows a run on a slow service", () => {
  it("shows a reading that took longer than the timer's period, and asks for no other meanwhile", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      // The first reading of each is answered at once; the second only when the test
      // lets it, which is two periods after it was asked for.
      const held: (() => void)[] = [];
      const asked = { row: 0, state: 0 };
      let ended = false;
      serve((_method, path) => {
        const what = path.endsWith("/state") ? "state" : "row";
        asked[what] += 1;
        const reply = () =>
          what === "state"
            ? {
                json: state(
                  ended
                    ? [human("Count slowly."), ai("One, two, three.")]
                    : [human("Count slowly.")],
                  { run_active: !ended },
                ),
              }
            : {
                json: summary({
                  first_message: "Count slowly.",
                  status: ended ? "done" : "running",
                }),
              };
        if (asked[what] === 1) {
          return reply();
        }
        return new Promise((resolve) => {
          held.push(() => resolve(reply()));
        });
      });
      renderView();
      expect(await screen.findByText("Running")).toBeTruthy();

      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(asked).toEqual({ row: 2, state: 2 }));
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      expect(asked).toEqual({ row: 2, state: 2 });

      ended = true;
      for (const answer of held.splice(0)) {
        answer();
      }
      await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
      await waitFor(() => expect(conversation()).toContain("One, two, three."));
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a task's page, Stop, and the run a row is of", () => {
  const STOPPING = "The run is stopping. The step under way completes first.";
  const TOO_LATE =
    "The run ended before the stop took effect. The task is shown as it ended.";
  const stop = () => screen.queryByRole("button", { name: /^Stop/ });
  const pause = (ms: number) =>
    new Promise((resolve) => setTimeout(resolve, ms));

  it.each([
    ["finished", "done" as const],
    ["failed", "failed" as const],
    ["was stopped in its turn", "stopped" as const],
  ])("says nothing of how the run it stopped ended once the row is of a later run, which %s", async (_how, status) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let step = 0;
      serve((method, path) => {
        if (method === "POST") {
          step = 1;
          // The stop was asked for the thread's fourth run.
          return {
            status: 202,
            json: { thread_id: "t-1", stopping: true, run: 4 },
          };
        }
        if (path.endsWith("/state")) {
          return {
            json: state([human("Count slowly."), ai("One, two.")], {
              run_active: step < 2,
            }),
          };
        }
        // Between two readings the fourth run ended, nobody here knows how, and a fifth
        // started and ended: the row is the fifth run's.
        return {
          json: summary({
            first_message: "Count slowly.",
            status: step < 2 ? "running" : status,
            stoppable: step < 2,
            stopping: step === 1,
            runs: step < 2 ? 4 : 5,
          }),
        };
      });
      renderView();
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      expect(await screen.findByText(STOPPING)).toBeTruthy();

      step = 2;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(screen.queryByText(STOPPING)).toBeNull());
      await pause(50);
      // How the fifth run ended is not how the fourth did: the page claims neither.
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
      expect(stop()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("still says too late when the row is of the run it stopped and that run ended another way", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let step = 0;
      serve((method, path) => {
        if (method === "POST") {
          step = 1;
          return {
            status: 202,
            json: { thread_id: "t-1", stopping: true, run: 4 },
          };
        }
        if (path.endsWith("/state")) {
          return {
            json: state([human("Count slowly."), ai("One, two.")], {
              run_active: step < 2,
            }),
          };
        }
        return {
          json: summary({
            first_message: "Count slowly.",
            status: step < 2 ? "running" : "done",
            stoppable: step < 2,
            stopping: step === 1,
            runs: 4,
          }),
        };
      });
      renderView();
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      expect(await screen.findByText(STOPPING)).toBeTruthy();

      step = 2;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      expect(await screen.findByText(new RegExp(TOO_LATE))).toBeTruthy();
      expect(screen.queryByText(STOPPING)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("goes on saying stopping past a reading that failed, which says nothing of how the run ended", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      // 0: in progress. 1: stop accepted, the row says stopping. 2: the row cannot be
      // read. 3: the run ended as stopped.
      let step = 0;
      serve((method, path) => {
        if (method === "POST") {
          step = 1;
          return {
            status: 202,
            json: { thread_id: "t-1", stopping: true, run: 2 },
          };
        }
        if (path.endsWith("/state")) {
          return {
            json: state([human("Count slowly.")], { run_active: step < 3 }),
          };
        }
        if (step === 2) {
          return refusal(
            500,
            "internal",
            "The list of threads could not be read.",
          );
        }
        return {
          json: summary({
            first_message: "Count slowly.",
            status: step < 3 ? "running" : "stopped",
            stoppable: step < 3,
            stopping: step === 1,
            runs: 2,
          }),
        };
      });
      renderView();
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      expect(await screen.findByText(STOPPING)).toBeTruthy();
      await pause(100);

      step = 2;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      expect(
        await screen.findByText("The task could not be read.", {
          exact: false,
        }),
      ).toBeTruthy();
      // A reading that failed is no word of the service about the run.
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
      expect(screen.getByText(STOPPING)).toBeTruthy();

      step = 3;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(screen.getByText("Stopped")).toBeTruthy());
      await waitFor(() => expect(screen.queryByText(STOPPING)).toBeNull());
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("goes on saying stopping after a reading of the same run that was made before the stop", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      // 0: in progress. 1: stop accepted; the reading answered next was made before the
      // mark, so it says the same run is in progress and not stopping. 2: stopped.
      let step = 0;
      serve((method, path) => {
        if (method === "POST") {
          step = 1;
          return {
            status: 202,
            json: { thread_id: "t-1", stopping: true, run: 2 },
          };
        }
        if (path.endsWith("/state")) {
          return {
            json: state([human("Count slowly.")], { run_active: step < 2 }),
          };
        }
        return {
          json: summary({
            first_message: "Count slowly.",
            status: step < 2 ? "running" : "stopped",
            stoppable: step < 2,
            stopping: false,
            runs: 2,
          }),
        };
      });
      renderView();
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      expect(await screen.findByText(STOPPING)).toBeTruthy();
      await pause(100);
      // The run is the one this page stopped: it is stopping, whatever that reading said.
      expect(screen.getByText(STOPPING)).toBeTruthy();
      expect(stop()).toBeNull();

      step = 2;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() => expect(screen.getByText("Stopped")).toBeTruthy());
      await waitFor(() => expect(screen.queryByText(STOPPING)).toBeNull());
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

// Phase 8, from the review: what the page holds from before says nothing of how a run ended.

describe("a task's page, Stop, and readings that come late", () => {
  const STOPPING = "The run is stopping. The step under way completes first.";
  const TOO_LATE =
    "The run ended before the stop took effect. The task is shown as it ended.";
  const NO_STOP = /^This run has no Stop/;
  const ACCEPTED = {
    status: 202,
    json: { thread_id: "t-1", stopping: true, run: 1 },
  };
  const stop = () => screen.queryByRole("button", { name: /^Stop/ });
  const pause = (ms: number) =>
    new Promise((resolve) => setTimeout(resolve, ms));

  it.each([
    [
      "as stopped",
      sse("end", { status: "stopped" }),
      { status: "stopped" as const, pending: null },
      false,
    ],
    [
      "by pausing on a proposal",
      sse("interrupt", PROPOSAL("int-1")),
      { status: "needs_decision" as const, pending: PROPOSAL("int-1") },
      true,
    ],
  ])("settles a stop on a run it reads by the run's last event, when the run ends %s and the row says running a moment longer", async (_how, last, after, late) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      // 0: the run is in progress. 1: a stop was accepted. 2: the run's last event was
      // sent, and the row still says running. 3: the row says how the run ended. 4: another
      // run is in progress, which nobody asked to stop.
      let phase = 0;
      const answer = openBody();
      serve((method, path) => {
        if (method === "POST") {
          phase = 1;
          return ACCEPTED;
        }
        if (path.endsWith("/state")) {
          return {
            json: state([human("List the files."), ASKED], {
              run_active: phase < 3 || phase === 4,
              pending: phase === 3 ? after.pending : null,
            }),
          };
        }
        return {
          json: summary({
            first_message: "List the files.",
            status: phase === 3 ? after.status : "running",
            stoppable: phase !== 3,
            stopping: phase === 1 || phase === 2,
          }),
        };
      });
      begin("t-1", "List the files.", answer.stream);
      renderView();
      answer.send(START);
      fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
      expect(await screen.findByText(STOPPING)).toBeTruthy();

      phase = 2;
      answer.send(last);
      answer.close();
      // With the timer faked, a wait is woken by a change of the page, not by the store.
      await pause(100);
      expect(get("t-1")).toBeUndefined();
      // The event said how this run ended; the row's "running" changes nothing of it.
      expect(screen.queryByText(new RegExp(TOO_LATE)) !== null).toBe(late);

      phase = 3;
      vi.advanceTimersByTime(FOLLOW_SECONDS * 1000);
      await waitFor(() =>
        expect(
          screen.getByText(late ? "Needs your decision" : "Stopped"),
        ).toBeTruthy(),
      );
      await pause(50);
      expect(screen.queryByText(STOPPING)).toBeNull();
      expect(screen.queryByText(new RegExp(TOO_LATE)) !== null).toBe(late);
      expect(stop()).toBeNull();

      // Another run starts elsewhere: what was said of the earlier one goes.
      phase = 4;
      fireEvent.focus(window);
      await waitFor(() => expect(stop()).not.toBeNull());
      expect(screen.queryByText(new RegExp(TOO_LATE))).toBeNull();
      expect(screen.queryByText(STOPPING)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers no Stop from the row it held during its own run, between that run's end and the reading after it", async () => {
    let over = false;
    const answer = openBody();
    serve((_method, path) => {
      const reply = path.endsWith("/state")
        ? {
            json: state(
              over
                ? [human("List the files."), ai("All of them.")]
                : [human("List the files.")],
              { run_active: !over },
            ),
          }
        : {
            json: summary({
              first_message: "List the files.",
              status: over ? "done" : "running",
              stoppable: !over,
            }),
          };
      return over ? later(80, reply) : reply;
    });
    begin("t-1", "List the files.", answer.stream);
    renderView();
    answer.send(START);
    expect(await screen.findByRole("button", { name: /^Stop/ })).toBeTruthy();

    over = true;
    answer.send(END);
    answer.close();
    await waitFor(() => expect(get("t-1")?.ended ?? null).not.toBeNull());
    await pause(10);
    // The run has ended; the row that says "running" is from before.
    expect(stop()).toBeNull();
    expect(screen.queryByText(NO_STOP)).toBeNull();
    await waitFor(() => expect(screen.getByText("Done")).toBeTruthy());
    expect(stop()).toBeNull();
  });

  it("does not offer a recorded message again when a refused stop's readings took the place of the readings after the run", async () => {
    // A follow-up runs; Stop is pressed; the run fails before the stop is answered, and the
    // stop is refused, since no run is in progress any more. The readings asked for at the
    // run's end are replaced by the ones asked for after the refusal, and are answered with
    // what the page held: the conversation from before the run.
    const NO_RUN = refusal(
      409,
      "no_run",
      "No run is in progress on this thread.",
    );
    let posted = false;
    let failed = false;
    const run = openBody();
    let accept: (value: typeof NO_RUN) => void = () => {};
    serve((method, path) => {
      if (method === "POST" && path.endsWith("/stop")) {
        return new Promise<typeof NO_RUN>((resolve) => {
          accept = resolve;
        });
      }
      if (method === "POST") {
        posted = true;
        return { stream: run.stream };
      }
      const reply = path.endsWith("/state")
        ? {
            json: state(
              failed
                ? [
                    human("Summarize the week."),
                    ai("Here it is."),
                    human("And the week before?", "h-2"),
                  ]
                : [human("Summarize the week."), ai("Here it is.")],
            ),
          }
        : {
            json: failed
              ? summary({
                  status: "failed",
                  error: { code: "model_error", message: "The model failed." },
                })
              : summary({ status: posted ? "running" : "done" }),
          };
      return failed ? later(60, reply) : reply;
    });
    renderView();
    await waitFor(() => expect(conversation()).toContain("Here it is."));
    fireEvent.change(followUp(), { target: { value: "And the week before?" } });
    fireEvent.click(sendMessage());
    run.send(START);
    fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));

    failed = true;
    run.send(
      sse("error", { code: "model_error", message: "The model failed." }),
    );
    run.close();
    await waitFor(() => expect(get("t-1")?.ended ?? null).not.toBeNull());
    // The stop is answered while the readings made at the run's end are on their way.
    await pause(10);
    accept(NO_RUN);

    await waitFor(() => expect(get("t-1")).toBeUndefined());
    await waitFor(() => expect(screen.getByText("Failed")).toBeTruthy());
    // The message is in the conversation, and is not put back in the box to be sent twice.
    expect(conversation()).toContain("And the week before?");
    expect(followUp().value).toBe("");
  });

  it("says nothing of Stop for a run only the conversation's reading knows of", async () => {
    serve((_method, path) =>
      path.endsWith("/state")
        ? { json: state([human("Hand it over.")], { run_active: true }) }
        : { json: summary({ status: "done", stoppable: false }) },
    );
    renderView();
    expect(
      await screen.findByText(
        "A run is in progress. This page reads the task again until it ends.",
      ),
    ).toBeTruthy();
    expect(stop()).toBeNull();
    expect(screen.queryByText(NO_STOP)).toBeNull();
  });

  it("stops saying that a stop was refused once one is accepted", async () => {
    let presses = 0;
    serve((method, path) => {
      if (method === "POST") {
        presses += 1;
        return presses === 1
          ? refusal(409, "not_stoppable", "This run cannot be stopped yet.")
          : ACCEPTED;
      }
      if (path.endsWith("/state")) {
        return { json: state([human("Count slowly.")]) };
      }
      return {
        json: summary({
          status: "running",
          stoppable: true,
          stopping: presses > 1,
        }),
      };
    });
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /The service did not stop this run/,
    );

    fireEvent.click(await screen.findByRole("button", { name: /^Stop/ }));
    expect(await screen.findByText(STOPPING)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("a task's page that is left while its run streams", () => {
  it("sends nothing, and leaves the run going", async () => {
    const answer = openBody();
    const asked = serve(() => NOT_USED);
    begin("t-1", "List the files.", answer.stream);
    const { unmount } = renderView();
    answer.send(START, chunk("Here"));
    await waitFor(() => expect(conversation()).toContain("Here"));
    const before = asked.length;

    unmount();
    // No stop, no decision, no message: leaving is not a request (FR-021).
    expect(asked.filter(({ method }) => method !== "GET")).toEqual([]);
    expect(asked).toHaveLength(before);
    // The run's stream is still read in this tab, for the page that shows it next.
    expect(get("t-1")?.ended).toBeNull();
    answer.send(chunk(" you are"), END);
    answer.close();
    await waitFor(() => expect(get("t-1")?.ended?.kind).toBe("event"));
    expect(asked.filter(({ method }) => method !== "GET")).toEqual([]);
  });
});

// Phase 12 (T098): a reply's pieces are not always plain text. The default model and an
// `openai:` model through the Responses API send each piece as a list of blocks, and an
// empty list last. The pieces below are the ones the service sends for each, as they were
// read from a run (FR-010, US1/AC3).

describe("a reply that streams in blocks", () => {
  const blocks = (id: string, content: unknown[], tool_calls: unknown[] = []) =>
    sse("message", {
      message: { id, type: "ai", content, name: "freyja", tool_calls },
      metadata: { langgraph_node: "model", langgraph_step: 3 },
    });
  const RUN = "lc_run--01a1fe7d-5ce0-70f8-8d62-d4d99b58e056";
  // An `openai:` model through the Responses API: the first piece under the response's
  // id, the others under the run's, the text's own id on a piece of its own.
  const RESPONSES = [
    blocks("resp_1", []),
    blocks(RUN, []),
    blocks(RUN, [{ type: "text", text: "One, ", index: 0 }]),
    blocks(RUN, [{ type: "text", text: "two, ", index: 0 }]),
    blocks(RUN, [{ type: "text", text: "three.", index: 0 }]),
    blocks(RUN, [{ type: "text", text: "", id: "msg_1", index: 0 }]),
    blocks(RUN, []),
  ];
  // An `anthropic:` model: text in one block, then the action it asks for in another,
  // whose arguments arrive in pieces. With each piece, the action as that piece alone
  // makes it: its name only with the first, and part of its arguments with the next. These
  // are what the service's serializer makes of the library's own pieces.
  const ANTHROPIC = [
    blocks(RUN, []),
    blocks(RUN, [{ text: "I will ", type: "text", index: 0 }]),
    blocks(RUN, [{ text: "tell #ops.", type: "text", index: 0 }]),
    blocks(
      RUN,
      [
        {
          id: "toolu_1",
          input: {},
          name: "send_notification",
          type: "tool_use",
          index: 1,
        },
      ],
      [{ id: "toolu_1", name: "send_notification", args: {} }],
    ),
    blocks(
      RUN,
      [{ partial_json: '{"channel": "#o', type: "input_json_delta", index: 1 }],
      [{ id: null, name: "", args: { channel: "#o" } }],
    ),
    blocks(RUN, [
      {
        partial_json: 'ps", "text": "hi"}',
        type: "input_json_delta",
        index: 1,
      },
    ]),
    blocks(RUN, []),
  ];

  function joined(...pieces: string[]) {
    const events = pieces.map((piece) => {
      const [, kind, data] =
        /^event: (\w+)\r\ndata: ([\s\S]*)\r\n\r\n$/.exec(piece) ?? [];
      return { event: kind, data: JSON.parse(data) };
    });
    return streamed({
      threadId: "t-1",
      message: "Count to three.",
      before: [],
      events,
      ended: null,
    }).messages;
  }

  it("joins the pieces of the Responses API into one reply, and shows no message that holds nothing", () => {
    const messages = joined(...RESPONSES);
    expect(messages.map((message) => message.type)).toEqual(["human", "ai"]);
    expect(contentText(messages[1].content)).toBe("One, two, three.");
    // As the service holds the reply once the run has ended.
    expect(messages[1].content).toEqual([
      { type: "text", text: "One, two, three.", index: 0, id: "msg_1" },
    ]);
  });

  it("shows what has arrived so far, piece by piece", () => {
    expect(joined(...RESPONSES.slice(0, 2))).toHaveLength(1);
    expect(contentText(joined(...RESPONSES.slice(0, 3))[1].content)).toBe(
      "One, ",
    );
    expect(contentText(joined(...RESPONSES.slice(0, 4))[1].content)).toBe(
      "One, two, ",
    );
    // The empty piece that ends the reply takes nothing away.
    expect(contentText(joined(...RESPONSES)[1].content)).toBe(
      contentText(joined(...RESPONSES.slice(0, 5))[1].content),
    );
  });

  it("joins an Anthropic reply as the service holds it once the run has ended", () => {
    const [, reply] = joined(...ANTHROPIC);
    // As the library joins these pieces (`AIMessageChunk.__add__`), read from it.
    expect(reply.content).toEqual([
      { text: "I will tell #ops.", type: "text", index: 0 },
      {
        id: "toolu_1",
        input: {},
        name: "send_notification",
        type: "tool_use",
        index: 1,
        partial_json: '{"channel": "#ops", "text": "hi"}',
      },
    ]);
    expect(contentText(reply.content).startsWith("I will tell #ops.\n")).toBe(
      true,
    );
  });

  it("shows the action an Anthropic reply asks for by its name while it streams, never half of it", () => {
    for (let end = 4; end <= ANTHROPIC.length; end += 1) {
      const [, reply] = joined(...ANTHROPIC.slice(0, end));
      // From the piece that names it on, the action is the named one, whole as far as the
      // stream has said: no piece that names nothing takes its place.
      expect(reply.tool_calls).toEqual([
        { id: "toolu_1", name: "send_notification", args: {} },
      ]);
    }
  });

  // Phase 13 (T112): the arguments arrive in pieces that name nothing. Once the model's step
  // is done, its update holds the reply whole, as the service sends it, and the page shows
  // each action with all of its arguments, before the run has ended (FR-010).
  const modelUpdate = (calls: unknown[], node = "model") =>
    sse("update", {
      node,
      data: {
        messages: [
          {
            id: RUN,
            type: "ai",
            content: [],
            name: "freyja",
            tool_calls: calls,
          },
        ],
        _skill_tools_disclosed: {},
      },
    });
  const WHOLE = {
    id: "toolu_1",
    name: "send_notification",
    args: { channel: "#ops", text: "hi" },
  };

  it("shows an action's arguments once the model's step is done, before the run has ended", () => {
    const [, reply] = joined(...ANTHROPIC, modelUpdate([WHOLE]));
    expect(reply.tool_calls).toEqual([WHOLE]);
    expect(contentText(reply.content).startsWith("I will tell #ops.\n")).toBe(
      true,
    );
  });

  it("shows every action of a reply that asks for two, each from the piece that names it", () => {
    const first = blocks(RUN, [], [{ id: "call_1", name: "ls", args: {} }]);
    const part = blocks(RUN, [], [{ id: null, name: "", args: { path: "/" } }]);
    const second = blocks(
      RUN,
      [],
      [{ id: "call_2", name: "read_file", args: {} }],
    );
    expect(joined(first, part, second)[1].tool_calls).toEqual([
      { id: "call_1", name: "ls", args: {} },
      { id: "call_2", name: "read_file", args: {} },
    ]);
    const whole = [
      { id: "call_1", name: "ls", args: { path: "/" } },
      { id: "call_2", name: "read_file", args: { file_path: "/a" } },
    ];
    expect(
      joined(first, part, second, modelUpdate(whole))[1].tool_calls,
    ).toEqual(whole);
  });

  // From the review of Phase 13: an empty id is no id, and a step's update is about the
  // reply of that step, since a provider may give the actions of two replies the same ids.
  it("shows both actions of a reply whose actions have empty ids", () => {
    const first = blocks(RUN, [], [{ id: "", name: "ls", args: {} }]);
    const second = blocks(RUN, [], [{ id: "", name: "read_file", args: {} }]);
    expect(joined(first, second)[1].tool_calls).toEqual([
      { id: "", name: "ls", args: {} },
      { id: "", name: "read_file", args: {} },
    ]);
  });

  it("gives each reply the arguments of its own step's update, when two replies' actions share an id", () => {
    const ls = (path?: string) => [
      { id: "call_0", name: "ls", args: path === undefined ? {} : { path } },
    ];
    const messages = joined(
      blocks("reply-1", [], ls()),
      modelUpdate(ls("/")),
      sse("message", {
        message: {
          id: "result-1",
          type: "tool",
          content: "a b",
          name: "ls",
          tool_call_id: "call_0",
        },
        metadata: {},
      }),
      blocks("reply-2", [], ls()),
      modelUpdate(ls("/tmp")),
    );
    expect(messages.map((message) => message.tool_calls)).toEqual([
      undefined,
      ls("/"),
      undefined,
      ls("/tmp"),
    ]);
  });

  it("takes from an update only the actions the page shows, and only from the model's step", () => {
    const shown = [{ id: "toolu_1", name: "send_notification", args: {} }];
    // Another step's update says nothing of the reply.
    expect(
      joined(...ANTHROPIC, modelUpdate([WHOLE], "tools"))[1].tool_calls,
    ).toEqual(shown);
    // An action the page does not show is not added from an update.
    expect(
      joined(...ANTHROPIC, modelUpdate([{ ...WHOLE, id: "toolu_2" }]))[1]
        .tool_calls,
    ).toEqual(shown);
    // An update that holds no messages changes nothing.
    expect(
      joined(...ANTHROPIC, sse("update", { node: "model", data: {} }))[1]
        .tool_calls,
    ).toEqual(shown);
    expect(
      joined(...ANTHROPIC, sse("update", { node: "model", data: null }))[1]
        .tool_calls,
    ).toEqual(shown);
  });

  it("joins plain text and blocks as the service's library does", () => {
    // Read from `merge_content`: text then blocks puts the text first in the list; blocks
    // then text adds the text after them, or to a text that ends the list.
    const mixed = joined(
      chunk("One, ", RUN),
      blocks(RUN, [{ type: "text", text: "two.", index: 0 }]),
    );
    expect(mixed[1].content).toEqual([
      "One, ",
      { type: "text", text: "two.", index: 0 },
    ]);
    expect(contentText(mixed[1].content)).toBe("One, \ntwo.");
    const other = joined(
      blocks(RUN, [{ type: "text", text: "One, ", index: 0 }]),
      chunk("two.", RUN),
      chunk("", RUN),
    );
    expect(other[1].content).toEqual([
      { type: "text", text: "One, ", index: 0 },
      "two.",
    ]);
  });

  it("joins lists and numbers inside a block, and a block's place given as text only when it is the library's", () => {
    const [, reply] = joined(
      blocks(RUN, [
        {
          type: "reasoning",
          index: 0,
          summary: [{ index: 0, type: "summary_text", text: "Looking " }],
          tokens: 2,
        },
      ]),
      blocks(RUN, [
        {
          type: "reasoning",
          index: 0,
          summary: [{ index: 0, type: "summary_text", text: "it up." }],
          tokens: 3,
        },
      ]),
      blocks(RUN, [{ type: "text", text: "A", index: "lc_txt_0" }]),
      blocks(RUN, [{ type: "text", text: "B", index: "lc_txt_0" }]),
      blocks(RUN, [{ type: "text", text: "C", index: "other" }]),
      blocks(RUN, [{ type: "text", text: "D", index: "other" }]),
    );
    expect(reply.content).toEqual([
      {
        type: "reasoning",
        index: 0,
        summary: [{ index: 0, type: "summary_text", text: "Looking it up." }],
        tokens: 5,
      },
      { type: "text", text: "AB", index: "lc_txt_0" },
      { type: "text", text: "C", index: "other" },
      { type: "text", text: "D", index: "other" },
    ]);
  });

  it("keeps two blocks of one place apart when their ids differ", () => {
    const [, reply] = joined(
      blocks(RUN, [{ type: "text", text: "A", index: 0, id: "one" }]),
      blocks(RUN, [{ type: "text", text: "B", index: 0, id: "two" }]),
    );
    expect(reply.content).toEqual([
      { type: "text", text: "A", index: 0, id: "one" },
      { type: "text", text: "B", index: 0, id: "two" },
    ]);
  });

  it("shows a streamed action's result that holds no text", () => {
    const messages = joined(
      blocks(RUN, [], [{ id: "c1", name: "ls", args: { path: "/" } }]),
      sse("message", {
        message: {
          id: "tool-1",
          type: "tool",
          content: "",
          name: "ls",
          tool_call_id: "c1",
        },
        metadata: { langgraph_node: "tools", langgraph_step: 4 },
      }),
    );
    expect(messages.map((message) => message.type)).toEqual([
      "human",
      "ai",
      "tool",
    ]);
  });

  it("keeps a block's id once when it comes again, as the service holds it", () => {
    const [, reply] = joined(
      blocks(RUN, [{ type: "text", text: "One, ", id: "msg_1", index: 0 }]),
      blocks(RUN, [{ type: "text", text: "two.", id: "msg_1", index: 0 }]),
    );
    expect(reply.content).toEqual([
      { type: "text", text: "One, two.", id: "msg_1", index: 0 },
    ]);
  });

  it("keeps blocks apart that have another place or none, and a piece that is no block", () => {
    const [, reply] = joined(
      blocks(RUN, [{ type: "text", text: "First.", index: 0 }]),
      blocks(RUN, [{ type: "text", text: "Second.", index: 1 }]),
      blocks(RUN, [{ type: "text", text: "No place." }]),
      blocks(RUN, ["a bare piece"]),
    );
    expect(reply.content).toEqual([
      { type: "text", text: "First.", index: 0 },
      { type: "text", text: "Second.", index: 1 },
      { type: "text", text: "No place." },
      "a bare piece",
    ]);
  });

  it("still joins plain text as before, and keeps a message that holds only an action", () => {
    const messages = joined(
      chunk("Half", "ai-1"),
      chunk(" and half.", "ai-1"),
      blocks("ai-2", [], [{ id: "c1", name: "ls", args: { path: "/" } }]),
    );
    expect(messages[1].content).toBe("Half and half.");
    expect(messages[2].tool_calls).toHaveLength(1);
  });

  it.each([
    ["the Responses API", RESPONSES, "One, two, three."],
    ["Anthropic", ANTHROPIC, "I will tell #ops."],
  ])("shows a reply of %s on the page as it is written, before the run has ended", async (_which, pieces, text) => {
    serve(() => NOT_USED);
    const answer = openBody();
    begin("t-1", "Count to three.", answer.stream);
    renderView();
    answer.send(START, ...pieces.slice(0, -2));
    await waitFor(() => expect(conversation()).toContain(text.slice(0, 8)));
    answer.send(...pieces.slice(-2));
    await waitFor(() => expect(conversation()).toContain(text));
    // One reply of Freyja's, not one for each piece or each id.
    expect(screen.getAllByRole("article", { name: "Freyja" })).toHaveLength(1);
    expect(screen.getByText("Running")).toBeTruthy();
  });
});

describe("a task's page and the end of its own run", () => {
  // T121: the service sends a run's last event before it gives the run's lease back, so the
  // reading made at the end of this page's own run can still say a run is in progress.
  // The page reads the task again shortly, not at the follow timer's next turn.
  // Either reading can say it: each is asked for at the end, and each is answered by itself.
  it.each([
    ["the row", true, false],
    ["the conversation", false, true],
    ["both", true, true],
  ])("reads the task again shortly when %s, read at the end of its own run, still says running", async (_which, rowSays, stateSays) => {
    let ended = false;
    let rowsAfter = 0;
    let statesAfter = 0;
    service({
      row: () => {
        if (!ended) {
          return { json: summary({ status: "running", stoppable: true }) };
        }
        rowsAfter += 1;
        return rowsAfter === 1 && rowSays
          ? { json: summary({ status: "running", stoppable: true }) }
          : { json: summary({ status: "done" }) };
      },
      state: () => {
        const messages = [human("Summarize the week."), ai("Hello there.")];
        if (!ended) {
          return { json: state(messages.slice(0, 1), { run_active: true }) };
        }
        statesAfter += 1;
        return {
          json: state(messages, { run_active: statesAfter === 1 && stateSays }),
        };
      },
    });
    const answer = openBody();
    begin("t-1", "Summarize the week.", answer.stream);
    renderView();
    answer.send(START, chunk("Hello there."));
    await waitFor(() => expect(conversation()).toContain("Hello there."));

    ended = true;
    answer.send(END);
    answer.close();
    // Well before the follow timer's period: only a reading made sooner shows the end, and
    // opens the follow-up box, which a run in progress keeps shut.
    await waitFor(
      () => {
        expect(screen.getByText("Done")).toBeTruthy();
        expect(
          screen.queryByText(/A message can be sent once it has ended/),
        ).toBeNull();
      },
      { timeout: (FOLLOW_SECONDS * 1000) / 2 },
    );
    expect(rowsAfter).toBeGreaterThanOrEqual(2);
    expect(statesAfter).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  // T122: the row the page held from before says the earlier run failed until it is read
  // again; while the page shows its own run, that failure is not shown.
  it("shows no earlier run's failure while it streams a follow-up", async () => {
    service({
      row: () => ({
        json: summary({
          status: "failed",
          error: { code: "model_error", message: "The provider gave up." },
        }),
      }),
      state: () => ({ json: state([human("First.")]) }),
    });
    renderView();
    expect(await screen.findByText("The provider gave up.")).toBeTruthy();

    const answer = openBody();
    act(() => {
      begin("t-1", "Again.", answer.stream, [human("First.")]);
    });
    answer.send(START);
    expect(
      await screen.findByText(
        "The run has started. Freyja's reply appears as it is written.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("The provider gave up.")).toBeNull();
    expect(screen.queryByText("The run failed.")).toBeNull();
    answer.close();
  });

  // Phase 14's review: once the row has been read after the page's own run, its failure is
  // this run's, and is shown also while the page still shows the run as it streamed, as when
  // the stream was cut and the conversation cannot be read.
  it("shows the row's failure read after its own run, when the stream was cut and the conversation cannot be read", async () => {
    let ended = false;
    service({
      row: () =>
        ended
          ? {
              json: summary({
                status: "failed",
                error: {
                  code: "model_error",
                  message: "The provider gave up.",
                },
              }),
            }
          : { json: summary({ status: "running" }) },
      state: () =>
        ended
          ? refusal(500, "internal", "The state could not be read.")
          : { json: state([human("First.")]) },
    });
    const answer = openBody();
    begin("t-1", "Again.", answer.stream, [human("First.")]);
    renderView();
    answer.send(START);
    expect(
      await screen.findByText(
        "The run has started. Freyja's reply appears as it is written.",
      ),
    ).toBeTruthy();

    ended = true;
    answer.close();
    expect(await screen.findByText("The provider gave up.")).toBeTruthy();
  });

  // T123: the service's sentence for a row it could not read names the list of threads.
  it("says in its own words that the task could not be read", async () => {
    service({
      row: () => ({
        status: 500,
        json: {
          detail: {
            code: "internal",
            message: "The list of threads could not be read.",
          },
        },
      }),
      state: () => ({ json: state([human("First.")]) }),
    });
    renderView();
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /^The task could not be read\./,
    );
    expect(screen.queryByText(/thread/)).toBeNull();
  });
});
