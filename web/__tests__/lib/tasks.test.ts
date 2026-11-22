import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { body, withoutDigits } from "@/__tests__/tasks-testing";
import { stubFetch } from "@/__tests__/testing";
import { readJson } from "@/lib/json";
import {
  decide,
  events,
  inexact,
  isTerminal,
  LIST_SECONDS,
  listThreads,
  newThreadId,
  parseArguments,
  readState,
  readThread,
  type StreamEvent,
  startRun,
  stateKey,
  stopRun,
  THREADS_KEY,
  threadKey,
} from "@/lib/tasks";

async function all(stream: ReadableStream<Uint8Array>): Promise<StreamEvent[]> {
  const seen: StreamEvent[] = [];
  for await (const event of events(stream)) {
    seen.push(event);
  }
  return seen;
}

// The service's separator is CRLF (sse-starlette's default).
const START = 'event: start\r\ndata: {"run_id":"r1"}\r\n\r\n';
const END = 'event: end\r\ndata: {"status":"completed"}\r\n\r\n';

describe("events", () => {
  it("yields each event with its data read as JSON", async () => {
    expect(await all(body(START, END))).toEqual([
      { event: "start", data: { run_id: "r1" } },
      { event: "end", data: { status: "completed" } },
    ]);
  });

  it("puts together an event split over two pieces, also inside a line ending", async () => {
    const whole = START + END;
    for (let cut = 1; cut < whole.length; cut += 1) {
      expect(await all(body(whole.slice(0, cut), whole.slice(cut)))).toEqual([
        { event: "start", data: { run_id: "r1" } },
        { event: "end", data: { status: "completed" } },
      ]);
    }
  });

  it("reads two events that arrive in one piece", async () => {
    expect(await all(body(START + END))).toHaveLength(2);
  });

  it("takes a ping for no event", async () => {
    expect(await all(body(START, ": ping - 2026-11-20\r\n\r\n", END))).toEqual([
      { event: "start", data: { run_id: "r1" } },
      { event: "end", data: { status: "completed" } },
    ]);
  });

  it("keeps a last event that no blank line follows", async () => {
    expect(await all(body('event: end\ndata: {"status":"completed"}'))).toEqual(
      [{ event: "end", data: { status: "completed" } }],
    );
  });

  it("reads plain line feeds as well, and data that is not JSON as text", async () => {
    expect(await all(body("event: note\ndata: hello\n\n"))).toEqual([
      { event: "note", data: "hello" },
    ]);
  });

  it("puts together a character whose bytes arrive in two pieces", async () => {
    const bytes = new TextEncoder().encode(
      'event: message\r\ndata: {"text":"café ✓"}\r\n\r\n',
    );
    const cut = bytes.indexOf(0xc3) + 1; // inside the two bytes of é
    const split = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut));
        controller.enqueue(bytes.slice(cut));
        controller.close();
      },
    });
    expect(await all(split)).toEqual([
      { event: "message", data: { text: "café ✓" } },
    ]);
  });

  it("joins data on several lines with line feeds", async () => {
    expect(
      await all(body("event: note\r\ndata: first\r\ndata: second\r\n\r\n")),
    ).toEqual([{ event: "note", data: "first\nsecond" }]);
  });

  it("knows the events that end a run", () => {
    expect(
      ["start", "update", "message", "end", "interrupt", "error"].map((event) =>
        isTerminal({ event, data: null }),
      ),
    ).toEqual([false, false, false, true, true, true]);
  });
});

describe("newThreadId", () => {
  it("gives 32 characters the service's rule for a thread id accepts, never the same twice", () => {
    const ids = new Set(Array.from({ length: 200 }, () => newThreadId()));
    expect(ids.size).toBe(200);
    for (const id of ids) {
      expect(id).toMatch(/^[0-9a-f]{32}$/);
    }
  });
});

describe("the calls", () => {
  it("read the list, a task, and its conversation under /api/threads", async () => {
    const fetchStub = stubFetch({ json: {} });
    await listThreads();
    await readThread("t-1");
    await readState("t-1");
    expect(fetchStub.mock.calls.map(([url]) => url)).toEqual([
      "/api/threads",
      "/api/threads/t-1",
      "/api/threads/t-1/state",
    ]);
    expect([THREADS_KEY, threadKey("t-1"), stateKey("t-1")]).toEqual([
      "/threads",
      "/threads/t-1",
      "/threads/t-1/state",
    ]);
  });

  it("start a run that goes on without a reader", async () => {
    const fetchStub = stubFetch({ status: 200, text: START });
    const answer = await startRun("t-1", "Summarize the week.");
    const [url, init] = fetchStub.mock.calls[0];
    expect(url).toBe("/api/threads/t-1/runs/unattended");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"message":"Summarize the week."}');
    expect(await all(answer)).toEqual([
      { event: "start", data: { run_id: "r1" } },
    ]);
  });

  it("put an id the service would refuse into a path as one segment", async () => {
    const fetchStub = stubFetch({ json: {} });
    await readThread("../llm/profiles");
    expect(fetchStub.mock.calls[0][0]).toBe("/api/threads/..%2Fllm%2Fprofiles");
  });

  it("carry no credential", async () => {
    const fetchStub = stubFetch({ json: {} });
    await listThreads();
    await readState("t-1");
    await startRun("t-1", "hi");
    for (const [, init] of fetchStub.mock.calls) {
      const headers = new Headers(init?.headers);
      expect(headers.get("authorization")).toBeNull();
      expect(init?.credentials).toBeUndefined();
    }
  });
});

// Phase 5: deciding a proposal.

describe("decide", () => {
  it("sends the proposal's id and the decisions in order, for a continuation without a reader", async () => {
    const fetchStub = stubFetch({ status: 200, text: ": ping\r\n\r\n" });
    await decide("t-1", "int-7", [
      { type: "approve" },
      { type: "edit", args: { channel: "#ops-staging" } },
      { type: "reject", message: "Not now." },
    ]);
    const [url, init] = fetchStub.mock.calls[0];
    expect(url).toBe("/api/threads/t-1/resume/unattended");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      interrupt_id: "int-7",
      decisions: [
        { type: "approve" },
        { type: "edit", args: { channel: "#ops-staging" } },
        { type: "reject", message: "Not now." },
      ],
    });
    expect(init?.credentials).toBeUndefined();
  });
});

describe("parseArguments", () => {
  it("gives the object for text that is a JSON object", () => {
    expect(parseArguments('{"channel": "#ops", "n": [1, 2]}')).toEqual({
      args: { channel: "#ops", n: [1, 2] },
    });
    expect(parseArguments("{}")).toEqual({ args: {} });
  });

  it.each([
    ["not JSON", "channel: #ops"],
    ["an array", '["#ops"]'],
    ["a number", "42"],
    ["a string", '"#ops"'],
    ["null", "null"],
    ["nothing", ""],
    ["a bare whole number beyond what a browser holds", "12345678901234567890"],
    ["a bare number that cannot be held at all", "1e999"],
  ])("gives a reason for %s", (_what, text) => {
    const parsed = parseArguments(text);
    expect("error" in parsed && parsed.error).toMatch(/JSON/);
  });

  // FR-015: an edit is sent as the person entered it. A browser reads a whole number
  // beyond 2^53 as the nearest one it can hold; one that keeps a number's own text sends
  // it as it was entered (T099).
  it.each([
    ["a 64-bit id", '{"id":1234567890123456789}'],
    ["the first number a browser changes", '{"n":9007199254740993}'],
    ["a negative one", '{"n":-9007199254740993}'],
    ["one inside a list", '{"ids":[1,2,12345678901234567890]}'],
    ["one inside an object", '{"a":{"b":99999999999999999999}}'],
    ["a number too large to hold at all", '{"n":1e999}'],
    [
      "a whole number written with an exponent",
      '{"id":1.2345678901234568e+21}',
    ],
    ["a power of ten beyond what a browser holds", '{"n":1e30}'],
  ])("takes %s, and what is sent holds it as it was entered", (_what, text) => {
    const parsed = parseArguments(text);
    expect("args" in parsed).toBe(true);
    // As `decide` sends it: written out again, digit for digit.
    expect("args" in parsed && JSON.stringify(parsed.args)).toBe(text);
  });

  it.each([
    ["the largest whole number a browser holds", '{"n": 9007199254740991}'],
    ["the smallest", '{"n": -9007199254740991}'],
    ["a long number inside text", '{"id": "1234567890123456789"}'],
    [
      "a quote and a long number inside text",
      '{"id": "say \\"1234567890123456789\\""}',
    ],
    ["a long number as a name", '{"1234567890123456789": 1}'],
    ["a fraction", '{"n": 0.1}'],
    ["a number with an exponent", '{"n": 1.5e3}'],
    ["a small fraction with an exponent", '{"n": 2.5e-30}'],
    ["zero and small numbers", '{"a": 0, "b": -1, "c": [10, 20]}'],
  ])("takes %s", (_what, text) => {
    const parsed = parseArguments(text);
    expect("args" in parsed).toBe(true);
    expect("args" in parsed && parsed.args).toEqual(JSON.parse(text));
  });
});

// A browser that reads JSON without giving a number's own text cannot send such a number
// as it was entered, so the page does not send it and says why.
describe("parseArguments where a number's digits are not kept", () => {
  let restore = () => {};
  beforeEach(() => {
    restore = withoutDigits();
  });
  afterEach(() => restore());

  it.each([
    ["a 64-bit id", '{"id": 1234567890123456789}', "1234567890123456789"],
    [
      "the first number a browser changes",
      '{"n": 9007199254740993}',
      "9007199254740993",
    ],
    ["a negative one", '{"n": -9007199254740993}', "-9007199254740993"],
    [
      "one inside a list",
      '{"ids": [1, 2, 12345678901234567890]}',
      "12345678901234567890",
    ],
    [
      "one inside an object",
      '{"a": {"b": 99999999999999999999}}',
      "99999999999999999999",
    ],
    ["a number too large to hold at all", '{"n": 1e999}', "1e999"],
    // How a browser writes a whole number of 22 digits or more, as the edit box starts
    // from one that was proposed: its last digits are already gone.
    [
      "a whole number written with an exponent",
      '{"id": 1.2345678901234568e+21}',
      "1.2345678901234568e+21",
    ],
    ["a power of ten beyond what a browser holds", '{"n": 1e30}', "1e30"],
  ])("refuses %s, which a browser would change, and names it", (_what, text, number) => {
    const parsed = parseArguments(text);
    expect("error" in parsed).toBe(true);
    expect("error" in parsed && parsed.error).toContain(number);
    expect("error" in parsed && parsed.error).toMatch(/exactly/);
  });

  it.each([
    ["the largest whole number a browser holds", '{"n": 9007199254740991}'],
    ["a long number inside text", '{"id": "1234567890123456789"}'],
    ["a long number as a name", '{"1234567890123456789": 1}'],
    ["a fraction", '{"n": 0.1}'],
    ["a number with an exponent", '{"n": 1.5e3}'],
  ])("takes %s", (_what, text) => {
    const parsed = parseArguments(text);
    expect("args" in parsed && parsed.args).toEqual(JSON.parse(text));
  });
});

describe("inexact", () => {
  it("is false for a number whose digits were kept", () => {
    expect(inexact(readJson('{"id": 1234567890123456789}'))).toBe(false);
    expect(inexact(readJson('{"a": [{"b": 1e30}]}'))).toBe(false);
  });

  it("says whether arguments the service sent hold a whole number a browser cannot hold exactly", () => {
    expect(inexact(JSON.parse('{"id": 1234567890123456789}'))).toBe(true);
    expect(inexact({ a: [{ b: 2 ** 53 + 2 }] })).toBe(true);
    expect(inexact({ a: { b: [1, "x", null, true, -(2 ** 60)] } })).toBe(true);
    expect(inexact({ id: "1234567890123456789", n: 2 ** 53 - 1, f: 0.5 })).toBe(
      false,
    );
    expect(inexact({})).toBe(false);
  });
});

// Phase 8: stopping a run (FR-022).

describe("stopRun", () => {
  it("asks the service to stop the task's run, with no body", async () => {
    const fetchStub = stubFetch({
      status: 202,
      json: { thread_id: "t-1", stopping: true, run: 3 },
    });
    expect(await stopRun("t-1")).toEqual({
      thread_id: "t-1",
      stopping: true,
      run: 3,
    });
    const [url, init] = fetchStub.mock.calls[0];
    expect(url).toBe("/api/threads/t-1/stop");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
    expect(init?.headers).toBeUndefined();
    expect(init?.credentials).toBeUndefined();
  });

  it("raises the service's refusal", async () => {
    stubFetch({
      status: 409,
      json: {
        detail: {
          code: "no_run",
          message: "No run is in progress on this thread.",
        },
      },
    });
    await expect(stopRun("t-1")).rejects.toMatchObject({
      status: 409,
      code: "no_run",
    });
  });

  it("puts an id the service would refuse into the path as one segment", async () => {
    const fetchStub = stubFetch({ status: 202, json: {} });
    await stopRun("a/b");
    expect(fetchStub.mock.calls[0][0]).toBe("/api/threads/a%2Fb/stop");
  });
});

describe("LIST_SECONDS", () => {
  it("is shorter than the fifteen seconds within which a change is followed", () => {
    // SC-004, FR-006: a reading takes time of its own, so a period of fifteen seconds
    // would follow a change later than that.
    expect(LIST_SECONDS).toBeLessThan(15);
    expect(LIST_SECONDS).toBeGreaterThan(0);
  });
});

describe("a number as it was written, through the service's answers", () => {
  it("keeps a proposal's 64-bit id through a run's stream", async () => {
    const text =
      'event: interrupt\ndata: {"interrupt_id": "i1", "action_requests": [{"name": "close_record", "args": {"id": 1234567890123456789}}]}\n\n';
    const [event] = await all(body(text));
    const { action_requests } = event.data as {
      action_requests: { args: unknown }[];
    };
    expect(JSON.stringify(action_requests[0].args)).toBe(
      '{"id":1234567890123456789}',
    );
  });

  it("keeps it through a reading of the task's state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            '{"thread_id": "t-1", "messages": [], "pending": {"interrupt_id": "i1", "action_requests": [{"name": "close_record", "args": {"id": 1234567890123456789}}]}}',
            { status: 200, headers: { "content-type": "application/json" } },
          ),
      ),
    );
    const state = (await readState("t-1")) as unknown as {
      pending: { action_requests: { args: unknown }[] };
    };
    expect(JSON.stringify(state.pending.action_requests[0].args)).toBe(
      '{"id":1234567890123456789}',
    );
    vi.unstubAllGlobals();
  });

  it("sends an edit's 64-bit id as it was entered", async () => {
    const fetchStub = stubFetch({ status: 200, text: "" });
    const parsed = parseArguments('{"id": 1234567890123456789, "note": "x"}');
    if (!("args" in parsed)) {
      throw new Error("the arguments were refused");
    }
    await decide("t-1", "i1", [{ type: "edit", args: parsed.args }]).catch(
      () => undefined,
    );
    expect(fetchStub.mock.calls[0][1]?.body).toBe(
      '{"interrupt_id":"i1","decisions":[{"type":"edit","args":{"id":1234567890123456789,"note":"x"}}]}',
    );
  });
});
