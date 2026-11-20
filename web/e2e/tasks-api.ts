// What the Freyja service would answer for the Tasks section, for the end-to-end tests
// (feature 007). A test hands a page these answers in the browser, before its requests leave
// it, so no test needs the service. `api.ts` is the other sections' and is not changed.

import type { Page, Route } from "@playwright/test";
import type {
  ActionRequest,
  AgentTask,
  Message,
  PendingDecision,
  ThreadList,
  ThreadState,
  ThreadSummary,
} from "../lib/tasks";

/** One event as the service frames it. */
export function sse(event: string, data: unknown): string {
  return `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
}

export function start(threadId: string): string {
  return sse("start", {
    thread_id: threadId,
    run_id: "5d2f",
    mode: "run",
    debug: false,
    profile: { id: "environment", name: "Environment", source: "environment" },
  });
}

/** A piece of Freyja's reply. */
export function piece(text: string, id = "ai-1"): string {
  return sse("message", {
    message: { id, type: "ai", content: text, name: null, tool_calls: [] },
    metadata: { langgraph_node: "model", langgraph_step: 3 },
  });
}

export const END = sse("end", { status: "completed" });

export const NO_USABLE_MODEL =
  "No usable model is active. Activate a usable LLM profile, then send the message again.";

export function human(content: string, id = "h-1"): Message {
  return { id, type: "human", content, name: null };
}

export function ai(content: string, id = "ai-1"): Message {
  return { id, type: "ai", content, name: null, tool_calls: [] };
}

export function summary(
  threadId: string,
  values: Partial<ThreadSummary> = {},
): ThreadSummary {
  return {
    thread_id: threadId,
    first_message: null,
    status: "done",
    error: null,
    created_at: "2026-11-20T08:00:00Z",
    changed_at: "2026-11-20T08:00:04Z",
    stoppable: false,
    stopping: false,
    ...values,
  };
}

export function threadState(
  threadId: string,
  messages: Message[],
  values: Partial<ThreadState> = {},
): ThreadState {
  return {
    thread_id: threadId,
    run_active: false,
    messages,
    pending: null,
    updated_at: "2026-11-20T08:00:04+00:00",
    agent_tasks: [],
    ...values,
  };
}

export type Refusal = { status: number; code: string; message: string };

/** What the service does with a run it is sent. */
export type RunAnswer =
  | { refusal: Refusal }
  | { invalid: string; loc?: (string | number)[] }
  | {
      /** The whole stream, sent at once. */
      events: string[];
      /** The task as the service holds it afterwards; none for a run that recorded nothing. */
      after: { row: ThreadSummary; state: ThreadState } | null;
    };

/** A run that answers `reply` and finishes. */
export function finished(
  reply: string,
): (id: string, message: string) => RunAnswer {
  return (id, message) => ({
    events: [start(id), piece(reply), END],
    after: {
      row: summary(id, { first_message: message }),
      state: threadState(id, [human(message), ai(reply)]),
    },
  });
}

// The service's rule for a thread id (001 FR-001a).
const THREAD_ID = /^[A-Za-z0-9._-]*[A-Za-z0-9_-][A-Za-z0-9._-]*$/;

/**
 * The service, as far as the Tasks section's pages reach it: the tasks it holds, and the
 * runs and decisions it is sent. A run is answered by `run` and a decision by `resume`, and
 * what each leaves is kept, so a reload finds it.
 */
export class TaskService {
  readonly tasks = new Map<
    string,
    { row: ThreadSummary; state: ThreadState }
  >();
  /** Every message the pages sent to start a run, in order, also one that was refused. */
  readonly sent: { threadId: string; message: string }[] = [];
  /** Every decision the pages sent, in order. */
  readonly decided: ({ threadId: string } & DecisionBody)[] = [];
  /** Every task the pages asked to stop a run of, in order, also when it was refused. */
  readonly stops: string[] = [];

  constructor(
    private run: (threadId: string, message: string) => RunAnswer = finished(
      "Done.",
    ),
    private resume: (
      threadId: string,
      body: DecisionBody,
    ) => RunAnswer = () => ({
      refusal: {
        status: 409,
        code: "nothing_pending",
        message: "This thread has no pending decision.",
      },
    }),
  ) {}

  answerRunsWith(run: (threadId: string, message: string) => RunAnswer): this {
    this.run = run;
    return this;
  }

  answerDecisionsWith(
    resume: (threadId: string, body: DecisionBody) => RunAnswer,
  ): this {
    this.resume = resume;
    return this;
  }

  /**
   * The list as the service gives it: every task that needs a decision first, then the
   * others with the most recently changed first (Phase 6).
   */
  list(): ThreadList {
    const rows = [...this.tasks.values()].map(({ row }) => row);
    const paused = rows.filter((row) => row.status === "needs_decision");
    const others = rows.filter((row) => row.status !== "needs_decision");
    const newest = (a: ThreadSummary, b: ThreadSummary) =>
      b.changed_at.localeCompare(a.changed_at);
    return {
      threads: [...paused.sort(newest), ...others.sort(newest)],
      needs_decision: paused.length,
      total: rows.length,
      more: false,
    };
  }

  async serve(page: Page): Promise<this> {
    await page.route(/\/api\/threads\/[^?]+$/, (route) => this.answer(route));
    // Phase 6: the list, which the sidebar asks for on every page, from the tasks held.
    await page.route("**/api/threads", (route) =>
      route.fulfill({ json: this.list() }),
    );
    return this;
  }

  private async answer(route: Route): Promise<void> {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(
      /^\/api\/threads\//,
      "",
    );
    const [encoded, ...rest] = path.split("/");
    const threadId = decodeURIComponent(encoded);
    const tail = rest.join("/");
    if (!THREAD_ID.test(threadId) || threadId.length > 128) {
      await route.fulfill({
        status: 422,
        json: {
          detail: [
            { loc: ["path", "thread_id"], msg: "String should match pattern" },
          ],
        },
      });
      return;
    }
    if (request.method() === "POST" && tail === "runs/unattended") {
      const { message } = request.postDataJSON() as { message: string };
      this.sent.push({ threadId, message });
      // Phase 7: as the service, no message is taken by a task whose run is in progress or
      // whose proposal is pending (001 FR-011).
      const held = this.tasks.get(threadId);
      const busy: Refusal | null =
        held?.row.status === "running" || held?.state.run_active
          ? {
              status: 409,
              code: "run_active",
              message: "A run is already active on this thread.",
            }
          : held !== undefined && held.state.pending !== null
            ? {
                status: 409,
                code: "decision_pending",
                message: "This thread has a pending decision.",
              }
            : null;
      await this.respond(
        route,
        threadId,
        busy === null ? this.run(threadId, message) : { refusal: busy },
      );
      return;
    }
    if (request.method() === "POST" && tail === "resume/unattended") {
      const body = request.postDataJSON() as DecisionBody;
      this.decided.push({ threadId, ...body });
      await this.respond(route, threadId, this.resume(threadId, body));
      return;
    }
    if (request.method() === "POST" && tail === "stop") {
      // Phase 8: the stop, as the service rules it. An accepted stop marks the row; the run
      // ends when the test says so, as a step under way takes its time.
      this.stops.push(threadId);
      const running = this.tasks.get(threadId);
      const refused: Refusal | null =
        running === undefined
          ? {
              status: 404,
              code: "thread_not_found",
              message: "No run on this thread is known.",
            }
          : running.row.status !== "running"
            ? {
                status: 409,
                code: "no_run",
                message: "No run is in progress on this thread.",
              }
            : !running.row.stoppable
              ? {
                  status: 409,
                  code: "not_stoppable",
                  message:
                    "This run was not started by the console, so it cannot be stopped from here: a client that reads its own stream stops it by closing the stream, and a run the service started ends by itself.",
                }
              : null;
      if (refused !== null || running === undefined) {
        await this.respond(route, threadId, {
          refusal: refused as Refusal,
        });
        return;
      }
      this.tasks.set(threadId, {
        ...running,
        row: { ...running.row, stopping: true },
      });
      await route.fulfill({
        status: 202,
        json: { thread_id: threadId, stopping: true },
      });
      return;
    }
    const task = this.tasks.get(threadId);
    if (request.method() === "GET" && (tail === "" || tail === "state")) {
      if (task === undefined) {
        await route.fulfill({
          status: 404,
          json: {
            detail: {
              code: "thread_not_found",
              message: "This thread has never been used.",
            },
          },
        });
        return;
      }
      await route.fulfill({ json: tail === "" ? task.row : task.state });
      return;
    }
    await route.fulfill({
      status: 405,
      json: { detail: "Method Not Allowed" },
    });
  }

  private async respond(
    route: Route,
    threadId: string,
    answer: RunAnswer,
  ): Promise<void> {
    if ("refusal" in answer) {
      const { status, code, message } = answer.refusal;
      await route.fulfill({ status, json: { detail: { code, message } } });
      return;
    }
    if ("invalid" in answer) {
      await route.fulfill({
        status: 422,
        json: {
          detail: [
            { loc: answer.loc ?? ["body", "message"], msg: answer.invalid },
          ],
        },
      });
      return;
    }
    if (answer.after !== null) {
      this.tasks.set(threadId, answer.after);
    }
    await route.fulfill({
      status: 200,
      headers: { "cache-control": "no-store, no-transform" },
      contentType: "text/event-stream",
      body: answer.events.join(""),
    });
  }
}

// Phase 5: proposals and decisions.

/** A decision request as the console sends it. */
export type DecisionBody = {
  interrupt_id: string;
  decisions: (
    | { type: "approve" }
    | { type: "edit"; args: Record<string, unknown> }
    | { type: "reject"; message?: string }
  )[];
};

export function notify(
  args: Record<string, unknown> = { channel: "#ops", message: "Ready." },
): ActionRequest {
  return {
    name: "send_notification",
    server: null,
    agent: null,
    args,
    description: "Send a notification to a channel.",
    allowed_decisions: ["approve", "edit", "reject"],
  };
}

export function proposal(
  interruptId: string,
  ...actions: ActionRequest[]
): PendingDecision {
  return {
    interrupt_id: interruptId,
    action_requests: actions,
    allowed_decisions: ["approve", "edit", "reject"],
  };
}

/** Freyja's reply that asks for the proposed actions. */
export function asking(...actions: ActionRequest[]): Message {
  return {
    id: "ai-ask",
    type: "ai",
    content: "",
    name: null,
    tool_calls: actions.map((action, index) => ({
      id: `call-${index}`,
      name: action.name,
      args: action.args,
    })),
  };
}

/** A task paused on `pending`, as the service holds it. */
export function pausedTask(
  threadId: string,
  message: string,
  pending: PendingDecision,
): { row: ThreadSummary; state: ThreadState } {
  return {
    row: summary(threadId, {
      first_message: message,
      status: "needs_decision",
    }),
    state: threadState(
      threadId,
      [human(message), asking(...pending.action_requests)],
      { pending },
    ),
  };
}

/** A decision whose continuation reports `result` and ends. */
export function continued(
  task: { row: ThreadSummary; state: ThreadState },
  result: string,
  reply: string,
): RunAnswer {
  const threadId = task.row.thread_id;
  return {
    events: [
      sse("start", {
        thread_id: threadId,
        run_id: "6e3a",
        mode: "resume",
        debug: false,
        profile: {
          id: "environment",
          name: "Environment",
          source: "environment",
        },
      }),
      piece(reply, "ai-after"),
      END,
    ],
    after: {
      row: { ...task.row, status: "done" },
      state: {
        ...task.state,
        pending: null,
        messages: [
          ...task.state.messages,
          {
            id: "tool-result",
            type: "tool",
            content: result,
            name: "send_notification",
            tool_call_id: "call-0",
          },
          ai(reply, "ai-after"),
        ],
      },
    },
  };
}

export type ListAnswer = { list: ThreadList } | { refusal: Refusal };

/**
 * Answer this page's requests for the list of tasks. Each request takes the next answer, and
 * the last one is given to every request after it. A page given no answer asks the web app's
 * server, as a deployed console does.
 */
export async function answerThreads(
  page: Page,
  ...answers: ListAnswer[]
): Promise<void> {
  let asked = 0;
  await page.route("**/api/threads", async (route) => {
    const answer = answers[Math.min(asked, answers.length - 1)];
    asked += 1;
    if ("list" in answer) {
      await route.fulfill({ json: answer.list });
    } else {
      const { status, code, message } = answer.refusal;
      await route.fulfill({ status, json: { detail: { code, message } } });
    }
  });
}

// Phase 7: follow-ups and agent tasks.

/** A follow-up on `task` that answers `reply` and finishes: the conversation grows by both. */
export function followedUp(
  task: { row: ThreadSummary; state: ThreadState },
  message: string,
  reply: string,
): RunAnswer {
  const threadId = task.row.thread_id;
  const turn = task.state.messages.length;
  return {
    events: [start(threadId), piece(reply, `ai-${turn}`), END],
    after: {
      row: { ...task.row, status: "done", error: null },
      state: {
        ...task.state,
        messages: [
          ...task.state.messages,
          human(message, `h-${turn}`),
          ai(reply, `ai-${turn}`),
        ],
      },
    },
  };
}

/**
 * An agent's outcome as the service records it in a conversation: Freyja's call of
 * `check_agent_tasks`, its result, and what Freyja says of it (feature 003).
 */
export function outcome(result: string, reply: string): Message[] {
  return [
    {
      id: "ai-check",
      type: "ai",
      content: "",
      name: null,
      tool_calls: [{ id: "call-check", name: "check_agent_tasks", args: {} }],
    },
    {
      id: "tool-check",
      type: "tool",
      content: result,
      name: "check_agent_tasks",
      tool_call_id: "call-check",
    },
    ai(reply, "ai-outcome"),
  ];
}

/** An agent task as the service gives it; pending unless the test says otherwise. */
export function agentTask(values: Partial<AgentTask> = {}): AgentTask {
  return {
    agent: "stub",
    task_id: "a-1",
    message: "slow 40",
    status: "working",
    started_at: "2026-11-20T08:00:02Z",
    last_checked_at: "2026-11-20T08:00:03Z",
    ended_at: null,
    outcome_delivered: false,
    ...values,
  };
}

// Phase 8: a run in progress, and a run whose page stops reading it.

/** A task whose run is in progress, as the service holds it; a console run unless said. */
export function runningTask(
  threadId: string,
  message: string,
  written: string | null = null,
  values: Partial<ThreadSummary> = {},
): { row: ThreadSummary; state: ThreadState } {
  return {
    row: summary(threadId, {
      first_message: message,
      status: "running",
      stoppable: true,
      ...values,
    }),
    state: threadState(
      threadId,
      written === null ? [human(message)] : [human(message), ai(written)],
      { run_active: true },
    ),
  };
}

/**
 * A run that is accepted and goes on after its stream stops: the page gets the start of the
 * reply and no last event, as when its connection is lost, and the service holds a run in
 * progress.
 */
export function goesOn(
  written: string,
): (id: string, message: string) => RunAnswer {
  return (id, message) => ({
    events: [start(id), piece(written)],
    after: runningTask(id, message, written),
  });
}
