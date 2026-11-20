// Tasks as the service describes them (specs/007-web-tasks/contracts/openapi.yaml). The
// console says "task"; the service and its paths say "thread", and the console keeps its
// words in what it calls. What a delegate agent works on is an "agent task", never a task.

import { api, stream } from "./api";

/** One status per task, worked out by the service (FR-003). */
export type ThreadStatus =
  | "running"
  | "needs_decision"
  | "failed"
  | "stopped"
  | "waiting_for_agent"
  | "done";

/** A task as the list has it. */
export type ThreadSummary = {
  thread_id: string;
  /** The beginning of the task's first message; null until it is known. */
  first_message: string | null;
  status: ThreadStatus;
  /** With `failed`, the run's error as the service sent it. */
  error: { code: string; message: string } | null;
  created_at: string;
  /** While a run is in progress, when it started; otherwise the last recorded step. */
  changed_at: string;
  /** Whether the run in progress can be asked to stop: one the console started. */
  stoppable: boolean;
  /** Whether a stop was asked for the run in progress and it has not ended yet. */
  stopping: boolean;
};

export type ThreadList = {
  threads: ThreadSummary[];
  /** How many tasks need a decision, among all tasks, listed or not. */
  needs_decision: number;
  total: number;
  more: boolean;
};

export type ToolCall = {
  id: string | null;
  name: string;
  args: Record<string, unknown>;
};

/** One message of a conversation, as the service gives it (001 contracts). */
export type Message = {
  id: string | null;
  type: "human" | "ai" | "tool" | "system";
  /** Text, or a list of content blocks. */
  content: string | unknown[];
  name: string | null;
  tool_calls?: ToolCall[] | null;
  tool_call_id?: string | null;
};

export type Decision = "approve" | "edit" | "reject";

export type ActionRequest = {
  name: string;
  /** The tool server the action goes to, when the service names one. */
  server: string | null;
  /** The agent the action goes to, when the service names one. */
  agent: string | null;
  args: Record<string, unknown>;
  description: string | null;
  allowed_decisions: Decision[];
};

export type PendingDecision = {
  interrupt_id: string;
  action_requests: ActionRequest[];
  allowed_decisions: Decision[];
};

/** What a delegate agent works on for a task: an agent task. */
export type AgentTask = {
  agent: string;
  task_id: string;
  message: string;
  status:
    | "working"
    | "input_required"
    | "completed"
    | "failed"
    | "rejected"
    | "canceled"
    | "given_up";
  started_at: string;
  last_checked_at: string;
  ended_at: string | null;
  outcome_delivered: boolean;
};

/** The conversation and what waits in it, from `GET /threads/{id}/state`. */
export type ThreadState = {
  thread_id: string;
  run_active: boolean;
  messages: Message[];
  pending: PendingDecision | null;
  updated_at: string | null;
  agent_tasks: AgentTask[];
};

/** One event of a run's stream (001 contracts/sse-events.md). */
export type StreamEvent = { event: string; data: unknown };

/** The events that end a run's stream. */
export const TERMINAL_EVENTS = ["end", "interrupt", "error"] as const;

export function isTerminal(event: StreamEvent): boolean {
  return (TERMINAL_EVENTS as readonly string[]).includes(event.event);
}

/** The path of the list of tasks, and the one key its copy in the browser is kept under. */
export const THREADS_KEY = "/threads";

function path(threadId: string): string {
  return `/threads/${encodeURIComponent(threadId)}`;
}

/** The key a task's row is kept under in the browser. */
export function threadKey(threadId: string): string {
  return path(threadId);
}

/** The key a task's conversation is kept under in the browser. */
export function stateKey(threadId: string): string {
  return `${path(threadId)}/state`;
}

/**
 * A new task's id: random, 32 hexadecimal characters, which the service's rule for a thread
 * id accepts. It says nothing about the task.
 */
export function newThreadId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

export function listThreads(): Promise<ThreadList> {
  return api<ThreadList>(THREADS_KEY);
}

export function readThread(threadId: string): Promise<ThreadSummary> {
  return api<ThreadSummary>(threadKey(threadId));
}

export function readState(threadId: string): Promise<ThreadState> {
  return api<ThreadState>(stateKey(threadId));
}

/**
 * Start a run on the task with `message`. The run goes on whether or not its stream is read;
 * the answer's body is the run's events, read with `events`.
 */
export function startRun(
  threadId: string,
  message: string,
): Promise<ReadableStream<Uint8Array>> {
  return stream(`${path(threadId)}/runs/unattended`, { message });
}

// Phase 5: deciding a proposal (FR-014 to FR-019).

/** One decision, as the service takes it, one per proposed action and in its order. */
export type DecisionSent =
  | { type: "approve" }
  | { type: "edit"; args: Record<string, unknown> }
  | { type: "reject"; message?: string };

/** The most a reason for a rejection may hold (001 contracts). */
export const REJECT_REASON_LIMIT = 4000;

/**
 * Send the decisions for the proposal `interruptId` names. The continuation goes on whether
 * or not its stream is read; the answer's body is its events. Every check of a decision is
 * the service's: a refusal is raised as an `ApiError`.
 */
export function decide(
  threadId: string,
  interruptId: string,
  decisions: DecisionSent[],
): Promise<ReadableStream<Uint8Array>> {
  return stream(`${path(threadId)}/resume/unattended`, {
    interrupt_id: interruptId,
    decisions,
  });
}

/**
 * Edited arguments, read from the text a person entered: a set of named arguments, the
 * structured form a proposal shows them in. Nothing else is checked here; whether the
 * action accepts them is the service's decision (FR-015).
 */
export function parseArguments(
  text: string,
): { args: Record<string, unknown> } | { error: string } {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return {
      error:
        'This is not valid JSON. Enter the arguments as a JSON object, such as {"name": "value"}.',
    };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {
      error:
        'The arguments must be a JSON object, with a name for each argument, such as {"name": "value"}.',
    };
  }
  return { args: value as Record<string, unknown> };
}

function decode(data: string[]): unknown {
  const text = data.join("\n");
  if (text === "") {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * The events of a run's stream as they arrive. A comment line, such as the service's
 * `: ping`, is no event. The last event counts also when no blank line follows it.
 */
export async function* events(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let kind: string | null = null;
  let data: string[] = [];

  function* lines(final: boolean): Generator<StreamEvent> {
    for (;;) {
      const end = buffer.search(/\r\n|\r|\n/);
      if (end === -1) {
        break;
      }
      // A carriage return at the very end may be the first half of a CRLF still to come.
      if (!final && buffer[end] === "\r" && end === buffer.length - 1) {
        break;
      }
      const line = buffer.slice(0, end);
      buffer = buffer.slice(buffer.startsWith("\r\n", end) ? end + 2 : end + 1);
      if (line === "") {
        if (kind !== null) {
          yield { event: kind, data: decode(data) };
        }
        kind = null;
        data = [];
      } else if (line.startsWith(":")) {
        // A comment, such as a ping.
      } else if (line.startsWith("event:")) {
        kind = line.slice("event:".length).trim();
      } else if (line.startsWith("data:")) {
        data.push(line.slice("data:".length).replace(/^ /, ""));
      }
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      yield* lines(false);
    }
    buffer += decoder.decode();
    if (buffer !== "") {
      buffer += "\n";
    }
    yield* lines(true);
    if (kind !== null) {
      yield { event: kind, data: decode(data) };
    }
  } finally {
    reader.releaseLock();
  }
}
