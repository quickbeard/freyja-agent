import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "@/__tests__/testing";
import { ANSWER_SECONDS, ApiError, api, isGone, stream } from "@/lib/api";

async function refusal(run: Promise<unknown>): Promise<ApiError> {
  try {
    await run;
  } catch (error) {
    if (error instanceof ApiError) {
      return error;
    }
    throw error;
  }
  throw new Error("the request was not refused");
}

describe("api", () => {
  it("asks the web app's own origin, under /api", async () => {
    const fetchStub = stubFetch({ json: { status: "ok" } });
    await api("/healthz");
    expect(fetchStub).toHaveBeenCalledTimes(1);
    const [url, init] = fetchStub.mock.calls[0];
    expect(url).toBe("/api/healthz");
    expect(init?.method).toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("returns the parsed body of an answer", async () => {
    stubFetch({ json: { limit: 50, profiles: [] } });
    expect(await api("/llm/profiles")).toEqual({ limit: 50, profiles: [] });
  });

  it("sends a body as JSON", async () => {
    const fetchStub = stubFetch({ status: 201, json: { id: "a" } });
    await api("/llm/profiles", { method: "POST", body: { name: "Local" } });
    const [, init] = fetchStub.mock.calls[0];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"name":"Local"}');
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
  });

  it("returns nothing for an answer with no content", async () => {
    stubFetch({ status: 204, text: "" });
    expect(await api("/llm/profiles/a", { method: "DELETE" })).toBeUndefined();
  });

  it("raises a refusal with the service's code and message", async () => {
    stubFetch({
      status: 409,
      json: {
        detail: {
          code: "profile_active",
          message: "Activate another profile first.",
        },
      },
    });
    const error = await refusal(api("/llm/profiles/a", { method: "DELETE" }));
    expect(error.status).toBe(409);
    expect(error.code).toBe("profile_active");
    expect(error.message).toBe("Activate another profile first.");
    expect(error.fields).toEqual([]);
  });

  it("raises a refused value with each field's message", async () => {
    stubFetch({
      status: 422,
      json: {
        detail: [
          {
            type: "string_too_long",
            loc: ["body", "name"],
            msg: "String should have at most 64 characters",
          },
          { type: "missing", loc: ["body", "model"], msg: "Field required" },
        ],
      },
    });
    const error = await refusal(
      api("/llm/profiles", { method: "POST", body: {} }),
    );
    expect(error.status).toBe(422);
    expect(error.code).toBe("invalid");
    expect(error.fields).toEqual([
      { field: "name", message: "String should have at most 64 characters" },
      { field: "model", message: "Field required" },
    ]);
  });

  it("says the service cannot be reached when the request fails", async () => {
    stubFetch({ fails: "Failed to fetch" });
    const error = await refusal(api("/llm/profiles"));
    expect(error.code).toBe("unreachable");
    expect(error.status).toBe(0);
    expect(error.message).toMatch(/cannot be reached/);
  });

  it("says so too when the web app's server could not pass the request on", async () => {
    // What the rewrite answers while the service is down: an error with no JSON body.
    stubFetch({ status: 500, text: "Internal Server Error" });
    const error = await refusal(api("/llm/profiles"));
    expect(error.code).toBe("unreachable");
    expect(error.status).toBe(500);
    expect(error.message).toMatch(/cannot be reached/);
  });

  it("says the service answered with an error when it did, and passes on its words", async () => {
    // The service is there: a path it does not have is answered by it, in its own shape.
    stubFetch({ status: 404, json: { detail: "Not Found" } });
    const error = await refusal(api("/llm/nothing"));
    expect(error.code).toBe("error");
    expect(error.status).toBe(404);
    expect(error.message).toBe(
      "The Freyja service answered with an error (404): Not Found",
    );
    expect(error.message).not.toMatch(/cannot be reached/);
  });

  it("says so too for an error it cannot read the reason of", async () => {
    stubFetch({ status: 400, json: { unexpected: true } });
    const error = await refusal(api("/llm/profiles"));
    expect(error.code).toBe("error");
    expect(error.message).toBe(
      "The Freyja service answered with an error (400).",
    );

    // An error below 500 with no JSON at all is still an answer, not a silence.
    stubFetch({ status: 404, text: "Not Found" });
    const plain = await refusal(api("/llm/profiles"));
    expect(plain.code).toBe("error");
    expect(plain.status).toBe(404);
  });
});

describe("isGone", () => {
  it.each([
    "profile_not_found",
    "agent_not_found",
  ])("is true for a refusal that says what was named no longer exists: %s", (code) => {
    expect(isGone(new ApiError("There is none with this id.", 404, code))).toBe(
      true,
    );
  });

  it.each([
    "agent_changed",
    "agent_read_only",
    "check_busy",
    "unreachable",
    "error",
  ])("is false for any other refusal: %s", (code) => {
    expect(isGone(new ApiError("Refused.", 409, code))).toBe(false);
  });

  it("is false for what is no refusal of the service at all", () => {
    expect(isGone(new Error("agent_not_found"))).toBe(false);
    expect(isGone(undefined)).toBe(false);
  });
});

// Feature 007: a run's answer is a stream of events, read as it arrives.

describe("stream", () => {
  it("posts JSON to the web app's own origin and returns the answer's body", async () => {
    const fetchStub = stubFetch({
      status: 200,
      text: "event: start\r\ndata: {}\r\n\r\n",
    });
    const body = await stream("/threads/t-1/runs/unattended", {
      message: "hi",
    });
    const [url, init] = fetchStub.mock.calls[0];
    expect(url).toBe("/api/threads/t-1/runs/unattended");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"message":"hi"}');
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(await new Response(body).text()).toBe(
      "event: start\r\ndata: {}\r\n\r\n",
    );
  });

  it("raises a refusal as api does", async () => {
    stubFetch({
      status: 409,
      json: {
        detail: {
          code: "run_active",
          message: "A run is already active on this thread.",
        },
      },
    });
    const error = await refusal(
      stream("/threads/t-1/runs/unattended", { message: "hi" }),
    );
    expect([error.status, error.code, error.message]).toEqual([
      409,
      "run_active",
      "A run is already active on this thread.",
    ]);
  });

  it("raises refused values with each field's message", async () => {
    stubFetch({
      status: 422,
      json: {
        detail: [
          {
            loc: ["body", "message"],
            msg: "String should have at least 1 character",
          },
        ],
      },
    });
    const error = await refusal(
      stream("/threads/t-1/runs/unattended", { message: "" }),
    );
    expect(error.code).toBe("invalid");
    expect(error.fields).toEqual([
      { field: "message", message: "String should have at least 1 character" },
    ]);
  });

  it("says the service cannot be reached when no answer comes", async () => {
    stubFetch({ fails: "connection refused" });
    const error = await refusal(
      stream("/threads/t-1/runs/unattended", { message: "hi" }),
    );
    expect([error.status, error.code]).toEqual([0, "unreachable"]);
  });

  it("says so too when the web app's server could not pass the request on", async () => {
    stubFetch({ status: 500, text: "Internal Server Error" });
    const error = await refusal(
      stream("/threads/t-1/runs/unattended", { message: "hi" }),
    );
    expect([error.status, error.code]).toEqual([500, "unreachable"]);
  });
});

// Phase 11 (T083): a request the service accepts and never answers. The web app's own
// forwarding gives up after 30 seconds, which is "cannot be reached" above; this is the
// limit of the function itself, for what the forwarding does not end.
describe("a request that gets no answer", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  /** A `fetch` that answers nothing, and fails as a browser's does once it is given up. */
  function silentFetch() {
    const stub = vi.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("The request was given up.", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", stub);
    return stub;
  }

  it("is given up after the limit, and said to have had no answer", async () => {
    vi.useFakeTimers();
    silentFetch();
    const asked = refusal(api("/threads"));
    let settled = false;
    void asked.then(() => {
      settled = true;
    });

    await vi.advanceTimersByTimeAsync(ANSWER_SECONDS * 1000 - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const error = await asked;

    // The code a page already handles: no answer came, so nothing is known.
    expect(error.status).toBe(0);
    expect(error.code).toBe("unreachable");
    expect(error.message).toBe(
      `The Freyja service did not answer within ${ANSWER_SECONDS} seconds. Check that it is running, then try again.`,
    );
  });

  it("says of a request that changes something that it may have been taken", async () => {
    vi.useFakeTimers();
    silentFetch();
    const asked = refusal(api("/threads/t-1/stop", { method: "POST" }));
    await vi.advanceTimersByTimeAsync(ANSWER_SECONDS * 1000);
    const error = await asked;
    expect(error.code).toBe("unreachable");
    expect(error.message).toBe(
      `The Freyja service did not answer within ${ANSWER_SECONDS} seconds. It may still have taken the request: read again before sending it once more.`,
    );
  });

  it("is given up too when the answer began and its body never ended", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (_input: RequestInfo | URL, init?: RequestInit) =>
          ({
            ok: true,
            status: 200,
            json: () =>
              new Promise((_resolve, reject) => {
                init?.signal?.addEventListener("abort", () =>
                  reject(new DOMException("Given up.", "AbortError")),
                );
              }),
          }) as unknown as Response,
      ),
    );
    const asked = refusal(api("/threads"));
    await vi.advanceTimersByTimeAsync(ANSWER_SECONDS * 1000);
    expect((await asked).code).toBe("unreachable");
  });

  it("leaves no timer behind once an answer came", async () => {
    vi.useFakeTimers();
    stubFetch({ json: { status: "ok" } });
    await api("/healthz");
    expect(vi.getTimerCount()).toBe(0);
    stubFetch({ fails: "refused" });
    await refusal(api("/healthz"));
    expect(vi.getTimerCount()).toBe(0);
  });

  it("gives a run's stream the same limit until its answer begins, and none after", async () => {
    vi.useFakeTimers();
    silentFetch();
    const asked = refusal(stream("/threads/t-1/runs/unattended", {}));
    await vi.advanceTimersByTimeAsync(ANSWER_SECONDS * 1000);
    const error = await asked;
    expect(error.code).toBe("unreachable");
    expect(error.message).toMatch(/It may still have taken the request/);

    // Once the answer has begun, the run may last as long as it does: the body is the
    // caller's to read, and no timer is left to end it.
    let signal: AbortSignal | null | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        signal = init?.signal;
        return new Response("event: end\n\n", {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }),
    );
    const body = await stream("/threads/t-1/runs/unattended", {});
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(ANSWER_SECONDS * 10_000);
    expect(signal?.aborted).toBe(false);
    expect(await new Response(body).text()).toBe("event: end\n\n");
  });
});
