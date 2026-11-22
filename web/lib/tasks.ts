// Tasks as the service describes them (specs/007-web-tasks/contracts/openapi.yaml). The
// console says "task"; the service and its paths say "thread", and the console keeps its
// words in what it calls. What a delegate agent works on is an "agent task", never a task.

import { api, stream } from "./api";
import { changes, keepsDigits, readJson } from "./json";

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
  /** How many runs have started on the task; the status is of the last of them. */
  runs: number;
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

/**
 * How often the list of tasks is read again while it is shown, by the Tasks section and by
 * the sidebar, which reads it under the same key. FR-006 has a change followed within
 * fifteen seconds; a reading takes time of its own, so the period is shorter than that.
 */
export const LIST_SECONDS = 10;

/**
 * How the console reads tasks: the list, a task's row, and its conversation. A reading that
 * fails is not tried again by itself; the timers that follow a task read it again. A page
 * that was hidden is read as soon as it is shown (FR-006, FR-013): the reading library
 * otherwise waits five seconds after a page was opened, and after each such reading, before
 * it reads on being shown. Being shown is told twice, as focus and as visibility; the
 * second finds the first one's reading under way and adds none.
 */
export const READING = {
  shouldRetryOnError: false,
  focusThrottleInterval: 0,
} as const;

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
 * id accepts. It says nothing about the task. It is made from `getRandomValues`, which a
 * browser gives every page: `randomUUID` is given only to a secure origin (https,
 * `localhost`, `127.0.0.1`), not to a console reached over http by a name or another address.
 */
export function newThreadId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return hex.join("");
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
    value = readJson(text);
  } catch {
    return {
      error:
        'This is not valid JSON. Enter the arguments as a JSON object, such as {"name": "value"}.',
    };
  }
  // A plain object: a number kept as it was written is an object of another kind
  // (lib/json.ts), and no set of named arguments.
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  ) {
    return {
      error:
        'The arguments must be a JSON object, with a name for each argument, such as {"name": "value"}.',
    };
  }
  // A browser that keeps a number's own text sends it as it was entered. One that does
  // not would send another number, so the text is not sent (FR-015).
  const changed = keepsDigits() ? null : changedNumber(text);
  if (changed !== null) {
    return {
      error: `This page cannot be sure to send the number ${changed} exactly: a browser holds whole numbers only up to ${Number.MAX_SAFE_INTEGER}, and may send another one. Put it in quotes if the action takes it as text, or send this decision through the service's API.`,
    };
  }
  return { args: value as Record<string, unknown> };
}

// A string, which is passed over, or a number, in JSON text that has already been read.
const STRING_OR_NUMBER = /"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;

/**
 * The first number written in `text`, which is JSON, that a browser may read as another
 * one: a whole number beyond the largest it holds exactly, such as a 64-bit id, however it
 * is written, or a number too large to hold at all. An edit is sent as the person entered
 * it (FR-015), so text that holds one is not sent. A number that is not whole is read here
 * as the service reads it, and is left alone.
 */
function changedNumber(text: string): string | null {
  for (const [written] of text.matchAll(STRING_OR_NUMBER)) {
    if (written.startsWith('"')) {
      continue;
    }
    const read = Number(written);
    // Also when it is written with an exponent, which is how a browser writes a whole
    // number of 22 digits or more: the edit box starts from one that was proposed, and
    // its last digits are gone by then.
    if (changes(read)) {
      return written;
    }
  }
  return null;
}

/**
 * Whether arguments the service sent hold a whole number beyond the largest a browser
 * holds exactly. By the time the page has them, such a number may already be another one,
 * so what the page shows of it is not sure to be what was proposed (FR-014).
 */
export function inexact(value: unknown): boolean {
  if (typeof value === "number") {
    return Number.isInteger(value) && changes(value);
  }
  if (typeof value === "object" && value !== null) {
    return Object.values(value).some(inexact);
  }
  return false;
}

// Phase 8: stopping a run (FR-022).

/** What the service answers when it has accepted a stop. */
export type StopAnswer = {
  thread_id: string;
  stopping: true;
  /** Which of the task's runs the stop was asked for: `runs` of its row at that time. */
  run: number;
};

/**
 * Ask the service to stop the run in progress on the task. The answer says the stop was
 * asked for, not that the run has ended: the step under way completes first, and the task's
 * row says `stopping` until it has. Only a run the console started can be stopped; every
 * refusal is raised as an `ApiError`.
 */
export function stopRun(threadId: string): Promise<StopAnswer> {
  return api<StopAnswer>(`${path(threadId)}/stop`, { method: "POST" });
}

function decode(data: string[]): unknown {
  const text = data.join("\n");
  if (text === "") {
    return null;
  }
  try {
    return readJson(text);
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
