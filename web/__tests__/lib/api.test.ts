import { describe, expect, it } from "vitest";
import { stubFetch } from "@/__tests__/testing";
import { ApiError, api, isGone } from "@/lib/api";

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
