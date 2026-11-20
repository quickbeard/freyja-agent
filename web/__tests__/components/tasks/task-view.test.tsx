import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
import {
  AGENT_SECONDS,
  FOLLOW_SECONDS,
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
        focusThrottleInterval: 0,
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
    // The run is forgotten, and the reason is the service's, with the way to the LLM section.
    await waitFor(() => expect(get("t-1")).toBeUndefined());
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(
      screen
        .getByRole("link", { name: "Go to the LLM section" })
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

      // The row and the state are read again every five seconds.
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
  const WAITING =
    "This page reads the task again every fifteen seconds while an agent task is pending or its outcome is not in the conversation yet.";

  it("lists the agent tasks, and reads the task again every fifteen seconds while one is pending", async () => {
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

      // Not at the pace of a run that is followed, but every fifteen seconds.
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

  it("follows the run that reports an agent's outcome every five seconds", async () => {
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
});

// Phase 7, from the review: what the page holds from before a run says nothing of that run.

describe("a task's page and the periods it reads at", () => {
  it("follows a run every five seconds and an agent task every fifteen", () => {
    expect([FOLLOW_SECONDS, AGENT_SECONDS]).toEqual([5, 15]);
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
      await waitFor(() => expect(get("t-1")).toBeUndefined());
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
  const NO_STOP = /^This run has no Stop: the console did not start it\./;
  const ACCEPTED = { status: 202, json: { thread_id: "t-1", stopping: true } };
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

// Phase 8, from the review: what the page holds from before says nothing of how a run ended.

describe("a task's page, Stop, and readings that come late", () => {
  const STOPPING = "The run is stopping. The step under way completes first.";
  const TOO_LATE =
    "The run ended before the stop took effect. The task is shown as it ended.";
  const NO_STOP = /^This run has no Stop/;
  const ACCEPTED = { status: 202, json: { thread_id: "t-1", stopping: true } };
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
