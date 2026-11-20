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
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const UNREACHABLE =
  "The Freyja service cannot be reached. Check that it is running, then try again.";

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
    return new ApiError("Some values were refused.", status, "invalid", fields);
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
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers:
        body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(UNREACHABLE, 0, "unreachable");
  }
  if (response.status === 204) {
    return undefined as T;
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }
  if (response.ok) {
    return payload as T;
  }
  throw failure(response.status, payload);
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
 */
export async function stream(
  path: string,
  body: unknown,
): Promise<ReadableStream<Uint8Array>> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(UNREACHABLE, 0, "unreachable");
  }
  if (response.ok && response.body !== null) {
    return response.body;
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }
  throw failure(response.status, payload);
}

/** Whether a request was refused because the profile or the agent it names no longer exists. */
export function isGone(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.code === "profile_not_found" || error.code === "agent_not_found")
  );
}
