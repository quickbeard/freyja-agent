"use client";

import { useState } from "react";
import { AgentsModeSelect } from "@/components/agents-mode";
import { ApiError } from "@/lib/api";
import { begin } from "@/lib/task-runs";
import { type Message, startRun } from "@/lib/tasks";
import { SendBox } from "./send-box";

/** Why a task takes no message for now: a run is in progress, or a proposal is pending. */
export type Blocked = "run" | "proposal" | null;

const WHY: Record<Exclude<Blocked, null>, string> = {
  run: "A run is in progress on this task. A message can be sent once it has ended.",
  proposal:
    "This task waits for a decision on a proposal. A message can be sent once it is decided.",
};

// After these the task is read again: the service's refusals that say the task has moved
// on since the page read it, and a request that got no answer, which the service may have
// accepted all the same.
const MOVED = ["run_active", "decision_pending", "unreachable"];

// The service's sentences for the two refusals name a thread; these are the console's
// (FR-001a), as the proposal card has its own.
const SAID: Record<string, string> = {
  run_active:
    "A run is already in progress on this task, so the message was not sent. It can be sent once the run has ended.",
  decision_pending:
    "This task waits for a decision on a proposal, so the message was not sent. It can be sent once the proposal is decided.",
};

/** A refusal of a message, in the console's words where the service's name a thread. */
export function inWords(refused: unknown): unknown {
  return refused instanceof ApiError && refused.code in SAID
    ? new ApiError(SAID[refused.code], refused.status, refused.code)
    : refused;
}

/**
 * The box that sends another message on the same task (FR-020). The run it starts is held
 * as this tab's, with the conversation as it was (`before`), and the task's page shows it as
 * it arrives. While the task is `blocked` nothing can be sent, and the box says why; a
 * message is never a decision (FR-017). Whether a message is accepted is the service's
 * decision: its refusal is shown beside the box, which keeps the text.
 */
export function FollowUp({
  threadId,
  before,
  blocked,
  initial = "",
  onSent,
  onMoved,
}: {
  threadId: string;
  /** The conversation as the page shows it now, shown above the run while it streams. */
  before: readonly Message[];
  blocked: Blocked;
  /** Text the first box starts with. */
  initial?: string;
  /** Called once the service accepted a message and its run is held. */
  onSent?: () => void;
  /** Called when the task may no longer be as the page shows it, to have it read again. */
  onMoved?: () => void;
}) {
  // A message that was sent leaves an empty box: each one sent is a new box.
  const [sent, setSent] = useState(0);

  async function send(text: string) {
    let answer: ReadableStream<Uint8Array>;
    try {
      answer = await startRun(threadId, text);
    } catch (refused) {
      if (refused instanceof ApiError && MOVED.includes(refused.code)) {
        onMoved?.();
      }
      throw inWords(refused);
    }
    begin(threadId, text, answer, before);
    setSent((count) => count + 1);
    onSent?.();
  }

  return (
    <SendBox
      key={sent}
      label="Send another message on this task"
      initial={sent === 0 ? initial : ""}
      send={send}
      sendLabel="Send message"
      blocked={blocked === null ? null : WHY[blocked]}
      // Feature 010: the agents' approval mode, beside the button that sends (FR-008).
      beside={<AgentsModeSelect />}
    />
  );
}
