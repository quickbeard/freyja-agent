// Helpers for tests only: a stand-in for `fetch`, so no test reaches the service.

import { vi } from "vitest";

export type Reply =
  | { status?: number; json: unknown }
  | { status: number; text: string }
  | { fails: string };

function response(reply: Exclude<Reply, { fails: string }>): Response {
  if ("json" in reply) {
    return new Response(JSON.stringify(reply.json), {
      status: reply.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  }
  // A response with no content, such as 204, may not be given a body, not even an empty one.
  return new Response(reply.text || null, { status: reply.status });
}

/** Replace `fetch` with one that answers the given replies in order, the last one forever. */
export function stubFetch(...replies: Reply[]) {
  let call = 0;
  const fetchStub = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) => {
      const reply = replies[Math.min(call, replies.length - 1)];
      call += 1;
      if ("fails" in reply) {
        throw new TypeError(reply.fails);
      }
      return response(reply);
    },
  );
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}
