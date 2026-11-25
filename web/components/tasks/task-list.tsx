"use client";

import Link from "next/link";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useEvery } from "@/hooks/use-every";
import { exactTime } from "@/lib/ago";
import { ApiError } from "@/lib/api";
import {
  LIST_SECONDS,
  listThreads,
  READING,
  THREADS_KEY,
  type ThreadList,
  type ThreadSummary,
} from "@/lib/tasks";
import { StartedBy } from "./started-by";
import { TaskStatus } from "./status";

function Row({ task }: { task: ThreadSummary }) {
  const begins = task.first_message ?? "";
  // The service gives a reason with `failed` only; with any other status it is not shown.
  const reason =
    task.status === "failed" ? (task.error?.message ?? null) : null;
  return (
    <li>
      <Link
        href={`/tasks/${encodeURIComponent(task.thread_id)}`}
        className="flex flex-col gap-1 rounded-lg border p-3 hover:bg-muted/40 focus-visible:outline-2 sm:flex-row sm:items-center sm:gap-4"
      >
        {/* Plain text: how a message begins is never rendered as markup (FR-012). */}
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-sm">
            {begins === "" ? `Task ${task.thread_id}` : begins}
          </span>
          {/* Another agent started it (feature 009): said as text. */}
          <StartedBy
            caller={task.caller ?? null}
            className="text-xs text-muted-foreground"
          />
          {/* Why it failed, in the service's words, as text too (FR-003). */}
          {reason !== null && (
            <span className="line-clamp-2 text-xs text-muted-foreground">
              {reason}
            </span>
          )}
        </span>
        <TaskStatus status={task.status} />
        <time
          dateTime={task.changed_at}
          className="shrink-0 text-xs text-muted-foreground"
        >
          {exactTime(task.changed_at)}
        </time>
      </Link>
    </li>
  );
}

/**
 * What the list leaves out, past the tasks it shows. Every task that needs a decision is
 * listed, unless the service left some out because it could not read them (FR-023): it says
 * `more` then too, and counts them among those that need a decision. A task can also be
 * counted and shown otherwise when it changed while the list was read, as when a run took
 * its lease: the words cover both.
 */
function unlisted(data: ThreadList): string {
  const listed = data.threads.filter(
    (task) => task.status === "needs_decision",
  ).length;
  const left = data.needs_decision - listed;
  if (left <= 0) {
    return "Every task that needs a decision is listed, then the most recently changed of the others.";
  }
  return left === 1
    ? "1 task that needs a decision is not listed as one: it could not be read just now, or it changed while the list was read. It is read again by itself."
    : `${left} tasks that need a decision are not listed as such: they could not be read just now, or they changed while the list was read. They are read again by themselves.`;
}

/**
 * Every task, the ones that need a decision first, as the service orders them (FR-002 to
 * FR-006). A reading that fails is said, with a way to try again, and no row is shown as if
 * it were current.
 */
export function TaskList() {
  const { data, error, isLoading, isValidating, mutate } = useSWR<
    ThreadList,
    unknown
  >(THREADS_KEY, listThreads, READING);
  useEvery(THREADS_KEY, LIST_SECONDS, () => {
    // Not while a reading is under way: a new one would take its place, and with a
    // service slower than the period none would ever be shown.
    if (!isValidating) {
      void mutate();
    }
  });

  if (error !== undefined) {
    return (
      <div
        role="alert"
        className="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 p-4"
      >
        <p className="text-sm">
          {error instanceof ApiError
            ? error.code === "internal"
              ? // The service's sentence speaks of the list of threads (FR-001a).
                "The list of tasks could not be read."
              : error.message
            : "The tasks could not be loaded."}
        </p>
        <Button
          variant="outline"
          size="sm"
          disabled={isValidating}
          onClick={() => mutate()}
        >
          Try again
        </Button>
      </div>
    );
  }
  if (isLoading || data === undefined) {
    return (
      <output className="flex flex-col gap-2">
        <span className="sr-only">Loading tasks</span>
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
      </output>
    );
  }
  if (data.threads.length === 0 && data.total > 0) {
    // The service leaves a row out that it cannot read (FR-023). With every listed row left
    // out the list is empty and tasks exist all the same: "no task yet" would be untrue.
    return (
      <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
        {data.total === 1
          ? "The service counts 1 task, and it cannot be shown just now. It is read again by itself."
          : `The service counts ${data.total} tasks, and none of them can be shown just now. They are read again by themselves.`}
      </p>
    );
  }
  if (data.threads.length === 0) {
    return (
      <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
        There is no task yet. Press New task to hand Freyja one.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <ol aria-label="Tasks" className="flex flex-col gap-2">
        {data.threads.map((task) => (
          <Row key={task.thread_id} task={task} />
        ))}
      </ol>
      {data.more && (
        <p className="text-sm text-muted-foreground">
          {`More tasks exist than are listed here: ${data.total} in all. ${unlisted(data)}`}
        </p>
      )}
    </div>
  );
}
