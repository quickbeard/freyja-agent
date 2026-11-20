// Helpers for the Tasks section's tests only (feature 007): runs' streams as the service
// sends them, and the shapes it answers with. No test reaches the service.

import { vi } from "vitest";
import type { Message, ThreadState, ThreadSummary } from "@/lib/tasks";

/** A body that arrives in the given pieces, as a network delivers it. */
export function body(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

/** A body the test feeds by hand, and closes when it wants. */
export function openBody() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(made) {
      controller = made;
    },
  });
  return {
    stream,
    send(...events: string[]) {
      for (const event of events) {
        controller.enqueue(encoder.encode(event));
      }
    },
    close() {
      controller.close();
    },
  };
}

/** One event as the service frames it. */
export function sse(event: string, data: unknown): string {
  return `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
}

export const START = sse("start", {
  thread_id: "t-1",
  run_id: "r1",
  mode: "run",
  debug: false,
  profile: { id: "environment", name: "Environment", source: "environment" },
});
export const END = sse("end", { status: "completed" });

/** A piece of Freyja's reply as the stream carries it. */
export function chunk(text: string, id = "ai-1"): string {
  return sse("message", {
    message: { id, type: "ai", content: text, name: null, tool_calls: [] },
    metadata: { langgraph_node: "model", langgraph_step: 3 },
  });
}

export function update(node: string): string {
  return sse("update", { node, data: {} });
}

export function human(content: string, id = "h-1"): Message {
  return { id, type: "human", content, name: null };
}

export function ai(content: string, id = "ai-1", tool_calls = []): Message {
  return { id, type: "ai", content, name: null, tool_calls };
}

export function state(
  messages: Message[],
  values: Partial<ThreadState> = {},
): ThreadState {
  return {
    thread_id: "t-1",
    run_active: false,
    messages,
    pending: null,
    updated_at: "2026-11-20T08:00:04+00:00",
    agent_tasks: [],
    ...values,
  };
}

export function summary(values: Partial<ThreadSummary> = {}): ThreadSummary {
  return {
    thread_id: "t-1",
    first_message: "Summarize the week.",
    status: "done",
    error: null,
    created_at: "2026-11-20T08:00:00Z",
    changed_at: "2026-11-20T08:00:04Z",
    stoppable: false,
    stopping: false,
    ...values,
  };
}

export type Answer =
  | { status?: number; json: unknown }
  | { status?: number; stream: ReadableStream<Uint8Array> }
  | { fails: string };

/**
 * Replace `fetch` with one that answers by the request: `answer` is given the method and the
 * path under `/api`, and says what the service would. Every request is kept.
 */
export function serve(answer: (method: string, path: string) => Answer) {
  const asked: { method: string; path: string; body: unknown }[] = [];
  const fetchStub = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const path = url.replace(/^\/api/, "");
      asked.push({
        method,
        path,
        body:
          typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });
      const reply = answer(method, path);
      if ("fails" in reply) {
        throw new TypeError(reply.fails);
      }
      if ("stream" in reply) {
        return new Response(reply.stream, {
          status: reply.status ?? 200,
          headers: { "content-type": "text/event-stream" },
        });
      }
      return new Response(JSON.stringify(reply.json), {
        status: reply.status ?? 200,
        headers: { "content-type": "application/json" },
      });
    },
  );
  vi.stubGlobal("fetch", fetchStub);
  return asked;
}

/** A refusal as the service sends it. */
export function refusal(status: number, code: string, message: string) {
  return { status, json: { detail: { code, message } } };
}
