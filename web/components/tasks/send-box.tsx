"use client";

import { useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";

function reason(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "invalid") {
      const said = error.fields.map((field) => field.message).join(" ");
      return said === "" ? error.message : `The message was refused: ${said}`;
    }
    return error.message;
  }
  return "The message could not be sent.";
}

/**
 * One text box and its Send button. Text that is empty is not sent; two presses send once;
 * a refusal is said with the service's reason beside the box, which keeps the text, and so
 * does a request that got no answer (FR-007, FR-008). With `blocked`, the reason the text
 * cannot be sent for now, nothing is sent and the reason stands beside the box, which still
 * takes text, to be sent once the reason is gone (FR-020).
 */
export function SendBox({
  label,
  initial = "",
  send,
  sendLabel = "Send",
  blocked = null,
}: {
  label: string;
  initial?: string;
  /** Sends the text; a refusal is raised as an `ApiError`. */
  send: (text: string) => Promise<void>;
  sendLabel?: string;
  /** Why the text cannot be sent for now, in words; null when it can. */
  blocked?: string | null;
}) {
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // A refusal is about the task as it was: once the reason the box cannot be sent changes,
  // the task has moved on, and the refusal is no longer said.
  const [blockedBefore, setBlockedBefore] = useState(blocked);
  if (blockedBefore !== blocked) {
    setBlockedBefore(blocked);
    setError(null);
  }
  // Set at once, where the state is set only at the next render: a second press in between
  // finds it set.
  const busy = useRef(false);
  const box = useId();
  const said = useId();
  const why = useId();
  const described = [
    blocked === null ? null : why,
    error === null ? null : said,
  ].filter((id) => id !== null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy.current || blocked !== null || text.trim() === "") {
      return;
    }
    busy.current = true;
    setSending(true);
    setError(null);
    try {
      await send(text);
    } catch (failure) {
      setError(reason(failure));
    } finally {
      busy.current = false;
      setSending(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <Label htmlFor={box}>{label}</Label>
      <textarea
        id={box}
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={4}
        aria-describedby={
          described.length === 0 ? undefined : described.join(" ")
        }
        aria-invalid={error === null ? undefined : true}
        className="min-h-24 w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      />
      {blocked !== null && (
        <p id={why} className="text-sm text-muted-foreground">
          {blocked}
        </p>
      )}
      {error !== null && (
        <p id={said} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex justify-end">
        <Button
          type="submit"
          disabled={sending || blocked !== null || text.trim() === ""}
        >
          {sending ? "Sending…" : sendLabel}
        </Button>
      </div>
    </form>
  );
}
