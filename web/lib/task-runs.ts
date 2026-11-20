"use client";

// The runs this browser tab started (research R12). A run is started from one page, New task
// on the list's, and shown on another, the task's: its events are held here, outside React,
// for as long as the tab is open, and the task's page reads them as they arrive. Nothing is
// stored; a reload forgets every run, and the page then follows the task by reading it.

import { useSyncExternalStore } from "react";
import { events, isTerminal, type Message, type StreamEvent } from "./tasks";

/** How a run's stream ended: with its last event, or cut before it came. */
export type RunEnd =
  | { kind: "event"; event: StreamEvent }
  | { kind: "connection_ended" };

export type LiveRun = {
  threadId: string;
  /**
   * The message the run was started with, to be offered again when it failed at once; null
   * for a continuation after a decision, which no message of a person's started.
   */
  message: string | null;
  /** The conversation as it was when the run was started, shown above what streams. */
  before: readonly Message[];
  /** Every event received so far, in order. */
  events: readonly StreamEvent[];
  /** Null while the stream is open. */
  ended: RunEnd | null;
};

const runs = new Map<string, LiveRun>();
const listeners = new Set<() => void>();

function tell(): void {
  for (const listener of listeners) {
    listener();
  }
}

function set(run: LiveRun): void {
  runs.set(run.threadId, run);
  tell();
}

/** Thrown when a run of this tab is still streaming on the task. */
export class RunInProgressError extends Error {
  constructor(threadId: string) {
    super(`A run of this page is still in progress on task ${threadId}.`);
    this.name = "RunInProgressError";
  }
}

async function read(
  threadId: string,
  body: ReadableStream<Uint8Array>,
): Promise<void> {
  let last: StreamEvent | null = null;
  try {
    for await (const event of events(body)) {
      const current = runs.get(threadId);
      if (current === undefined) {
        return; // forgotten while it streamed
      }
      set({ ...current, events: [...current.events, event] });
      if (isTerminal(event)) {
        // The run has ended with this event, also when something between the page and the
        // service holds the connection open after it.
        last = event;
        break;
      }
    }
  } catch {
    // The connection was lost; what the run did is read from the service.
  }
  // Nothing more is read of it; the connection is let go.
  void body.cancel().catch(() => {});
  const current = runs.get(threadId);
  if (current !== undefined) {
    set({
      ...current,
      ended:
        last !== null
          ? { kind: "event", event: last }
          : { kind: "connection_ended" },
    });
  }
}

/**
 * Hold the run started on `threadId` with `message`, whose answer is `body`, and read its
 * events as they arrive. Refused while a run of this tab is still streaming on the task.
 */
export function begin(
  threadId: string,
  message: string | null,
  body: ReadableStream<Uint8Array>,
  before: readonly Message[] = [],
): LiveRun {
  const current = runs.get(threadId);
  if (current !== undefined && current.ended === null) {
    throw new RunInProgressError(threadId);
  }
  const run: LiveRun = { threadId, message, before, events: [], ended: null };
  set(run);
  void read(threadId, body);
  return run;
}

/** The run this tab started on the task, as far as it has arrived; undefined for none. */
export function get(threadId: string): LiveRun | undefined {
  return runs.get(threadId);
}

/** Forget the run this tab started on the task. */
export function clear(threadId: string): void {
  if (runs.delete(threadId)) {
    tell();
  }
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The run this tab started on the task, for a component, kept up to date. */
export function useRun(threadId: string): LiveRun | undefined {
  return useSyncExternalStore(
    subscribe,
    () => get(threadId),
    () => undefined,
  );
}
