// The one way the web app talks to the Freyja service: the browser calls `/api/...` on the web
// app's own origin, and next.config.ts forwards it. Nothing here knows the service's address.

export type FieldError = { field: string; message: string };

/** A request the service refused, or one that never got an answer from it. */
export class ApiError extends Error {
  constructor(
    message: string,
    /** The HTTP status, or 0 when no response came back at all. */
    readonly status: number,
    /**
     * The service's code for a refusal; `invalid` for refused values; `unreachable`; `error`
     * for an answer that is an error in no shape the service's contract has.
     */
    readonly code: string,
    /** For refused values: which field and why. Never the value that was sent. */
    readonly fields: FieldError[] = [],
    /**
     * Feature 007: for refused values, where each one is, in the order of `fields`, as the
     * service gives it: a decision's index and the argument it refused, for one.
     */
    readonly locations: readonly (string | number)[][] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const UNREACHABLE =
  "The Freyja service cannot be reached. Check that it is running, then try again.";

/**
 * How long a request waits for its answer. The web app's server gives up forwarding a
 * request the service does not answer after 30 seconds, and that is "cannot be reached"
 * already; this is just past it, for a request that is not ended on the way either.
 */
export const ANSWER_SECONDS = 35;

function silent(method: string): string {
  const said = `The Freyja service did not answer within ${ANSWER_SECONDS} seconds.`;
  // A request that only reads changed nothing. Any other may have arrived.
  return method === "GET"
    ? `${said} Check that it is running, then try again.`
    : `${said} It may still have taken the request: read again before sending it once more.`;
}

/**
 * Send a request and hand `use` its answer, giving up `ANSWER_SECONDS` after it was sent
 * unless `use` has returned by then. A request that fails, or is given up, is raised as
 * the `ApiError` of a request with no answer.
 */
async function ask<T>(
  path: string,
  init: RequestInit,
  use: (response: Response) => Promise<T>,
): Promise<T> {
  const limit = new AbortController();
  const timer = setTimeout(() => limit.abort(), ANSWER_SECONDS * 1000);
  try {
    let response: Response;
    try {
      response = await fetch(`/api${path}`, { ...init, signal: limit.signal });
    } catch {
      throw new ApiError(
        limit.signal.aborted ? silent(init.method ?? "GET") : UNREACHABLE,
        0,
        "unreachable",
      );
    }
    try {
      return await use(response);
    } catch (failure) {
      // Given up while the answer's body was read: no answer, as above.
      if (limit.signal.aborted && !(failure instanceof ApiError)) {
        throw new ApiError(silent(init.method ?? "GET"), 0, "unreachable");
      }
      throw failure;
    }
  } finally {
    clearTimeout(timer);
  }
}

type Options = { method?: string; body?: unknown };

function refusal(status: number, payload: unknown): ApiError | null {
  if (typeof payload !== "object" || payload === null) {
    return null;
  }
  const detail = (payload as { detail?: unknown }).detail;
  if (Array.isArray(detail)) {
    // FastAPI's shape for refused values, which these routes send without the value itself.
    const fields = detail.map((item: { loc?: unknown[]; msg?: string }) => ({
      field: String(item.loc?.at(-1) ?? ""),
      message: item.msg ?? "This value was refused.",
    }));
    const locations = detail.map((item: { loc?: unknown[] }) =>
      (item.loc ?? []).filter(
        (part): part is string | number =>
          typeof part === "string" || typeof part === "number",
      ),
    );
    return new ApiError(
      "Some values were refused.",
      status,
      "invalid",
      fields,
      locations,
    );
  }
  if (typeof detail === "object" && detail !== null) {
    const { code, message } = detail as { code?: string; message?: string };
    if (code && message) {
      return new ApiError(message, status, code);
    }
  }
  return null;
}

/** Call the service at `path` and return its answer, or raise an `ApiError`. */
export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const { method = "GET", body } = options;
  const init = {
    method,
    headers:
      body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
  return ask(path, init, async (response) => {
    if (response.status === 204) {
      return undefined as T;
    }
    const payload = await read(response);
    if (response.ok) {
      return payload as T;
    }
    throw failure(response.status, payload);
  });
}

/** An answer's body as JSON, or undefined when it is none; a body given up is raised. */
async function read(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch (unread) {
    if (unread instanceof DOMException && unread.name === "AbortError") {
      throw unread;
    }
    return undefined;
  }
}

/** What an answer that is not a success says, as the error a caller is given. */
function failure(status: number, payload: unknown): ApiError {
  const refused = refusal(status, payload);
  if (refused !== null) {
    return refused;
  }
  // A server error with no JSON body is the web app's server saying it could not pass the
  // request on, which is what it answers while the service is down.
  if (payload === undefined && status >= 500) {
    return new ApiError(UNREACHABLE, status, "unreachable");
  }
  // Anything else is an answer: something is there, and it said no in words of its own.
  const detail = (payload as { detail?: unknown } | undefined)?.detail;
  return new ApiError(
    typeof detail === "string" && detail !== ""
      ? `The Freyja service answered with an error (${status}): ${detail}`
      : `The Freyja service answered with an error (${status}).`,
    status,
    "error",
  );
}

// Feature 007: a run's answer is a stream of server-sent events, read as it arrives.

/**
 * Post `body` as JSON to `path` and return the answer's body, unread, for a caller to read
 * as it arrives. A refusal, refused values, and no answer raise the `ApiError` `api` raises.
 * The time limit ends with the beginning of the answer: a run lasts as long as it does.
 */
export async function stream(
  path: string,
  body: unknown,
): Promise<ReadableStream<Uint8Array>> {
  const init = {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  };
  return ask(path, init, async (response) => {
    if (response.ok && response.body !== null) {
      return response.body;
    }
    throw failure(response.status, await read(response));
  });
}

/** Whether a request was refused because the profile or the agent it names no longer exists. */
export function isGone(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.code === "profile_not_found" || error.code === "agent_not_found")
  );
}
