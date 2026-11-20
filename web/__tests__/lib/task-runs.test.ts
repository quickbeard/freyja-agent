import { waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  body,
  chunk,
  END,
  openBody,
  START,
  sse,
} from "@/__tests__/tasks-testing";
import {
  begin,
  clear,
  get,
  RunInProgressError,
  subscribe,
} from "@/lib/task-runs";

afterEach(() => {
  clear("t-1");
});

describe("the runs this tab started", () => {
  it("holds the message a run was started with and its events as they arrive", async () => {
    const answer = openBody();
    begin("t-1", "Summarize the week.", answer.stream);
    let told = 0;
    const stop = subscribe(() => {
      told += 1;
    });

    expect(get("t-1")?.message).toBe("Summarize the week.");
    expect(get("t-1")?.events).toEqual([]);
    answer.send(START);
    await waitFor(() => expect(get("t-1")?.events).toHaveLength(1));
    answer.send(chunk("Here"));
    await waitFor(() => expect(get("t-1")?.events).toHaveLength(2));
    expect(get("t-1")?.ended).toBeNull();
    expect(told).toBeGreaterThanOrEqual(2);
    stop();
  });

  it("ends a run with its last event", async () => {
    begin("t-1", "hi", body(START, chunk("Done."), END));
    await waitFor(() => expect(get("t-1")?.ended).not.toBeNull());
    expect(get("t-1")?.ended).toEqual({
      kind: "event",
      event: { event: "end", data: { status: "completed" } },
    });
  });

  it("ends a run at its last event, also when the connection is held open after it", async () => {
    const answer = openBody();
    begin("t-1", "hi", answer.stream);
    answer.send(START, END);
    await waitFor(() =>
      expect(get("t-1")?.ended).toEqual({
        kind: "event",
        event: { event: "end", data: { status: "completed" } },
      }),
    );
  });

  it("ends a run with an error as that error", async () => {
    const failed = sse("error", {
      code: "no_usable_model",
      message: "No usable model.",
    });
    begin("t-1", "hi", body(START, failed));
    await waitFor(() => expect(get("t-1")?.ended).not.toBeNull());
    expect(get("t-1")?.ended).toEqual({
      kind: "event",
      event: {
        event: "error",
        data: { code: "no_usable_model", message: "No usable model." },
      },
    });
  });

  it("says the connection ended when the stream ends without its last event", async () => {
    begin("t-1", "hi", body(START, chunk("Half")));
    await waitFor(() => expect(get("t-1")?.ended).not.toBeNull());
    expect(get("t-1")?.ended).toEqual({ kind: "connection_ended" });
  });

  it("says so too when the connection breaks", async () => {
    const broken = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(START));
        controller.error(new TypeError("network error"));
      },
    });
    begin("t-1", "hi", broken);
    await waitFor(() => expect(get("t-1")?.ended).not.toBeNull());
    expect(get("t-1")?.ended).toEqual({ kind: "connection_ended" });
  });

  it("gives nothing for a task it started no run on, and forgets a run on request", async () => {
    expect(get("t-1")).toBeUndefined();
    begin("t-1", "hi", body(START, END));
    await waitFor(() => expect(get("t-1")?.ended).not.toBeNull());
    clear("t-1");
    expect(get("t-1")).toBeUndefined();
  });

  it("refuses a second run on a task whose run still streams", async () => {
    const answer = openBody();
    begin("t-1", "hi", answer.stream);
    expect(() => begin("t-1", "again", body(START, END))).toThrow(
      RunInProgressError,
    );
    answer.send(END);
    answer.close();
    await waitFor(() => expect(get("t-1")?.ended).not.toBeNull());
    // Once it has ended, another may begin.
    begin("t-1", "again", body(START, END));
    expect(get("t-1")?.message).toBe("again");
  });
});
