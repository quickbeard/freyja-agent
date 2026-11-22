"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { stopRun } from "@/lib/tasks";

/**
 * What the page knows of the run in progress: one the console started, which can be asked
 * to stop, or one the console did not start, which has no Stop in the first version
 * (FR-022a). A run for which a stop was asked has no control at all: the page says that it
 * is stopping, where it says everything else of the run.
 */
export type StopState = "stoppable" | "not_stoppable";

// The service's refusals of a stop, in the console's words. Each means the page no longer
// shows the task as it is.
const REFUSED: Record<string, string> = {
  no_run:
    "No run is in progress on this task any more, so nothing was stopped. The task is shown as it is now.",
  not_stoppable:
    "The service did not stop this run: the console did not start it, or it has only just started. The task is shown as it is now.",
  // The service's sentences for these two name a thread (FR-001a).
  thread_not_found:
    "The service knows no run of this task, so nothing was stopped. The task is shown as it is now.",
  internal:
    "The service could not read or mark the task, so whether the stop was asked for is not known. The task is read again.",
};

const UNKNOWN =
  "No answer came, so whether the stop was asked for is not known. The task is read again.";

// How much of a task's first message the control's name carries.
const ABOUT_LIMIT = 60;

function named(about: string | null): string {
  if (about === null || about.trim() === "") {
    return "Stop the run of this task";
  }
  const text = about.trim();
  const letters = Array.from(text);
  return `Stop the run of the task: ${
    letters.length > ABOUT_LIMIT
      ? `${letters.slice(0, ABOUT_LIMIT).join("")}…`
      : text
  }`;
}

/**
 * Stop, for a run the console started (FR-022). A stop is asked only by a press of this
 * button, which stands outside any form, and a press while its request is on the way asks
 * nothing more (FR-017's rule for a decision, kept for a stop). What the service answers is
 * told to the page: that the stop was accepted, after which the page says that the run is
 * stopping and shows this control no more, or why nothing was stopped, after which the page
 * reads the task again.
 */
export function StopButton({
  threadId,
  about,
  run,
  onAsked,
  onStale,
}: {
  threadId: string;
  /** How the task's first message begins, for the control's name; null when not known. */
  about: string | null;
  run: StopState;
  /** Called once the service has accepted the stop, with which run it was asked for. */
  onAsked: (run: number) => void;
  /** Told why nothing was stopped, or that it is not known; the task is read again. */
  onStale: (said: string) => void;
}) {
  const [asking, setAsking] = useState(false);
  // Set at once, where the state is set only at the next render: a second press in between
  // finds it set.
  const busy = useRef(false);

  async function stop() {
    if (busy.current) {
      return;
    }
    busy.current = true;
    setAsking(true);
    try {
      const { run: asked } = await stopRun(threadId);
      onAsked(asked);
    } catch (failure) {
      if (failure instanceof ApiError && failure.code in REFUSED) {
        onStale(REFUSED[failure.code]);
      } else if (
        failure instanceof ApiError &&
        failure.code === "unreachable"
      ) {
        onStale(UNKNOWN);
      } else {
        onStale(
          failure instanceof ApiError
            ? failure.message
            : "The stop could not be asked for.",
        );
      }
    } finally {
      busy.current = false;
      setAsking(false);
    }
  }

  if (run === "not_stoppable") {
    return (
      <p className="text-sm text-muted-foreground">
        This run has no Stop: the console did not start it, or the service could
        not mark it as one that can be stopped. A run another client started
        ends when that client stops reading, and a run the service started ends
        by itself.
      </p>
    );
  }
  return (
    <div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-label={named(about)}
        disabled={asking}
        onClick={stop}
      >
        Stop
      </Button>
    </div>
  );
}
