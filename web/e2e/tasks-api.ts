// What the Freyja service would answer for the Tasks section, for the end-to-end tests
// (feature 007). A test hands a page these answers in the browser, before its requests leave
// it, so no test needs the service. `api.ts` is the other sections' and is not changed.

import type { Page, Route } from "@playwright/test";
import type {
  ActionRequest,
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
  /** Every run the pages started, in order. */
  readonly sent: { threadId: string; message: string }[] = [];
  /** Every decision the pages sent, in order. */
  readonly decided: ({ threadId: string } & DecisionBody)[] = [];

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

  async serve(page: Page): Promise<this> {
    await page.route(/\/api\/threads\/[^?]+$/, (route) => this.answer(route));
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
      await this.respond(route, threadId, this.run(threadId, message));
      return;
    }
    if (request.method() === "POST" && tail === "resume/unattended") {
      const body = request.postDataJSON() as DecisionBody;
      this.decided.push({ threadId, ...body });
      await this.respond(route, threadId, this.resume(threadId, body));
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
