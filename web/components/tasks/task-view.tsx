"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import {
  begin,
  clear,
  type LiveRun,
  type RunEnd,
  useRun,
} from "@/lib/task-runs";
import {
  type DecisionSent,
  decide,
  type Message,
  readState,
  readThread,
  type StreamEvent,
  startRun,
  stateKey,
  type ThreadState,
  type ThreadSummary,
  threadKey,
} from "@/lib/tasks";
import { MessageView } from "./message";
import { ProposalCard } from "./proposal-card";
import { SendBox } from "./send-box";
import { TaskStatus } from "./status";

// How often a page that does not read a run's stream reads the task again while that run is
// in progress (FR-013).
export const FOLLOW_SECONDS = 5;

const NO_USABLE_MODEL = "no_usable_model";

type RunError = { code: string; message: string };

function runError(run: LiveRun | undefined): RunError | null {
  const ended = run?.ended;
  if (ended?.kind !== "event" || ended.event.event !== "error") {
    return null;
  }
  const data = ended.event.data as Partial<RunError> | null;
  return {
    code: data?.code ?? "internal",
    message: data?.message ?? "The run failed.",
  };
}

/** Whether an answer says there is no such task: never used, or an id the service refuses. */
function missing(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.status === 404 || error.status === 422)
  );
}

/** A step the run completed, in words; the middleware's own steps are not shown. */
const STEPS: Record<string, string> = {
  model: "Freyja wrote a reply",
  tools: "Freyja ran the actions it asked for",
};

/**
 * What the stream has brought so far: the person's message, then each message as it grows,
 * by its id. While a run streams, a subagent's text cannot be told from Freyja's own
 * (feature 001), so this is work in progress; the conversation read afterwards is what counts.
 */
export function streamed(run: LiveRun): {
  messages: Message[];
  steps: string[];
} {
  const messages: Message[] = [...run.before];
  if (run.message !== null) {
    messages.push({
      id: "live-human",
      type: "human",
      content: run.message,
      name: null,
    });
  }
  const at = new Map<string, number>();
  const steps: string[] = [];
  run.events.forEach((event: StreamEvent, index) => {
    if (event.event === "update") {
      const node = (event.data as { node?: string } | null)?.node ?? "";
      if (node in STEPS) {
        steps.push(STEPS[node]);
      }
      return;
    }
    if (event.event !== "message") {
      return;
    }
    const message = (event.data as { message?: Message } | null)?.message;
    if (message === undefined) {
      return;
    }
    const key = message.id ?? `event-${index}`;
    const found = at.get(key);
    if (found === undefined) {
      at.set(key, messages.length);
      messages.push({ ...message });
      return;
    }
    const earlier = messages[found];
    const grown =
      typeof earlier.content === "string" && typeof message.content === "string"
        ? earlier.content + message.content
        : message.content;
    const calls =
      message.tool_calls && message.tool_calls.length > 0
        ? message.tool_calls
        : earlier.tool_calls;
    messages[found] = { ...earlier, content: grown, tool_calls: calls };
  });
  return { messages, steps };
}

function Failure({ error }: { error: RunError }) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-1 rounded-lg border border-destructive/40 p-4"
    >
      <p className="text-sm font-medium">The run failed.</p>
      <p className="whitespace-pre-wrap break-words text-sm">{error.message}</p>
      {error.code === NO_USABLE_MODEL && (
        <p className="text-sm">
          <Link href="/llm" className="underline underline-offset-4">
            Go to the LLM section
          </Link>{" "}
          to activate a usable profile.
        </p>
      )}
    </div>
  );
}

function Conversation({ messages }: { messages: Message[] }) {
  return (
    <ol aria-label="Conversation" className="flex flex-col gap-3">
      {messages.map((message, index) => (
        <li key={message.id ?? index}>
          <MessageView message={message} />
        </li>
      ))}
    </ol>
  );
}

/**
 * One task: its status, its conversation, and how its last run ended. A run this tab
 * started is shown as it arrives; when it ends, the task is read again and the conversation
 * is the service's (FR-009 to FR-013).
 */
export function TaskView({ threadId }: { threadId: string }) {
  const run = useRun(threadId);
  const streaming = run !== undefined && run.ended === null;
  const row = useSWR<ThreadSummary, unknown>(
    threadKey(threadId),
    () => readThread(threadId),
    { shouldRetryOnError: false },
  );
  const state = useSWR<ThreadState, unknown>(
    stateKey(threadId),
    () => readState(threadId),
    { shouldRetryOnError: false },
  );
  const { mutate: readRowAgain } = row;
  const { mutate: readStateAgain } = state;
  const ended = run?.ended ?? null;

  // The end of a run for which the task has been read again since.
  const [readFor, setReadFor] = useState<RunEnd | null>(null);
  // What the page says of a decision the service did not apply, until the next one.
  // It is about the proposal the task had once it was read again (`about`, null for none),
  // and goes once the task has moved on from it.
  const [notice, setNotice] = useState<{
    text: string;
    about: string | null | undefined;
  } | null>(null);
  // Set when a decision was accepted: the run's status takes the focus once it is shown, so
  // that the focus is not lost with the card the person pressed.
  const [focusStatus, setFocusStatus] = useState(false);
  const statusRef = useRef<HTMLOutputElement>(null);
  // Whether this page's stream was cut before the run's last event. It is said, and the
  // task is followed, until the service says the run is no longer in progress (FR-010).
  const [cut, setCut] = useState(false);

  // When a run this page shows ends, in whichever way, the task is read again once.
  useEffect(() => {
    if (ended === null) {
      return;
    }
    if (ended.kind === "connection_ended") {
      setCut(true);
    }
    let current = true;
    void Promise.allSettled([readRowAgain(), readStateAgain()]).then(() => {
      if (current) {
        setReadFor(ended);
      }
    });
    return () => {
      current = false;
    };
  }, [ended, readRowAgain, readStateAgain]);

  const failed = runError(run);
  const noConversation = missing(state.error);
  const rowMissing = missing(row.error);
  // A read that failed for another reason than that there is no such task.
  const readError =
    (state.error !== undefined && !noConversation ? state.error : undefined) ??
    (row.error !== undefined && !rowMissing ? row.error : undefined);
  const rowRunning = row.error === undefined && row.data?.status === "running";
  // A new task whose first run failed before anything was recorded keeps its run, whose
  // message is offered again (FR-008).
  const offerAgain = failed !== null && noConversation;
  // A continuation that failed while its proposal is still pending, as when an agent came
  // to need approval since: its reason stays beside the card it is about (005 FR-023).
  const failedOnProposal =
    failed !== null && (state.data?.pending ?? null) !== null;
  // What the stream brought stays on the page until the task has been read again after it.
  const showLive =
    run !== undefined && (streaming || (ended !== null && readFor !== ended));
  const settled = run !== undefined && ended !== null && readFor === ended;

  // Once the end of a run has been read, what the service holds is what the page shows: the
  // run is forgotten, so that how it ended is not shown later as if it were current.
  useEffect(() => {
    if (
      settled &&
      !offerAgain &&
      !failedOnProposal &&
      readError === undefined
    ) {
      clear(threadId);
    }
  }, [settled, offerAgain, failedOnProposal, readError, threadId]);

  // A notice about a decision that was not applied goes once the task's proposal is another,
  // or there is none any more, than the one the task had when the notice was given.
  const pendingId = state.data?.pending?.interrupt_id ?? null;
  useEffect(() => {
    if (
      notice !== null &&
      notice.about !== undefined &&
      pendingId !== notice.about
    ) {
      setNotice(null);
    }
  }, [notice, pendingId]);

  useEffect(() => {
    if (focusStatus && streaming && statusRef.current !== null) {
      statusRef.current.focus();
      setFocusStatus(false);
    }
  }, [focusStatus, streaming]);

  // Following: a run this page does not read is in progress, or this page's stream was cut
  // and the service has not said yet where the run stands. The task is read again on a
  // timer of its own, so that a read that fails, as while the service restarts, does not
  // end the following (FR-013).
  // The status last read counts while a read fails: following stops when the service says
  // the run has ended, not when it cannot be reached.
  const following =
    !streaming &&
    (row.data?.status === "running" ||
      (cut && (row.data === undefined || readError !== undefined)));
  useEffect(() => {
    if (!following) {
      return;
    }
    const timer = setInterval(() => {
      void readRowAgain();
      void readStateAgain();
    }, FOLLOW_SECONDS * 1000);
    return () => clearInterval(timer);
  }, [following, readRowAgain, readStateAgain]);
  useEffect(() => {
    if (cut && row.error === undefined && row.data?.status !== undefined) {
      if (row.data.status !== "running") {
        setCut(false);
      }
    }
  }, [cut, row.error, row.data?.status]);

  // A decision goes through the service's own checks; its continuation is shown as a run
  // is, below the conversation as it was when the person decided (FR-019).
  async function decideNow(interruptId: string, decisions: DecisionSent[]) {
    const before = state.data?.messages ?? [];
    const answer = await decide(threadId, interruptId, decisions);
    setNotice(null);
    clear(threadId);
    begin(threadId, null, answer, before);
    setFocusStatus(true);
  }

  async function stale(said: string) {
    setNotice({ text: said, about: undefined });
    const [, latest] = await Promise.allSettled([
      readRowAgain(),
      readStateAgain(),
    ]);
    const read = latest.status === "fulfilled" ? latest.value : undefined;
    setNotice((current) =>
      current?.text === said
        ? {
            text: said,
            about:
              read === undefined
                ? undefined
                : (read.pending?.interrupt_id ?? null),
          }
        : current,
    );
  }

  async function sendAgain(text: string) {
    const answer = await startRun(threadId, text);
    clear(threadId);
    begin(threadId, text, answer);
  }

  if (!showLive && offerAgain) {
    return (
      <section className="flex flex-col gap-4">
        <Header status="failed" />
        <Failure error={failed} />
        <SendBox
          label="Send the message again"
          initial={run?.message ?? ""}
          send={sendAgain}
          sendLabel="Send again"
        />
      </section>
    );
  }

  // A run whose first step is not recorded yet has a row and no conversation: it is in
  // progress, not missing.
  if (!showLive && (noConversation || rowMissing) && !rowRunning) {
    return (
      <section className="flex flex-col gap-2">
        <h1 className="font-heading text-2xl font-semibold">Task not found</h1>
        <p className="text-sm text-muted-foreground">
          There is no task at this address.{" "}
          <Link href="/tasks" className="underline underline-offset-4">
            Go to the tasks
          </Link>
          .
        </p>
      </section>
    );
  }

  const unread = readError !== undefined && (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 p-4"
    >
      <p className="text-sm">
        {readError instanceof ApiError
          ? readError.message
          : "The task could not be loaded."}
        {following && " This page reads the task again until it can."}
      </p>
      <Button
        variant="outline"
        size="sm"
        disabled={row.isValidating || state.isValidating}
        onClick={() => {
          void readRowAgain();
          void readStateAgain();
        }}
      >
        Try again
      </Button>
    </div>
  );

  // Nothing to show but that the task cannot be read: no conversation was read, and no
  // run of this page is shown.
  if (!showLive && unread && state.data === undefined) {
    return (
      <section className="flex flex-col gap-4">
        <Header />
        {cut && <CutNotice />}
        {unread}
      </section>
    );
  }

  const started = run?.events.some((event) => event.event === "start") ?? false;
  const status = streaming
    ? "running"
    : row.error === undefined
      ? row.data?.status
      : undefined;
  const live = showLive && run !== undefined ? streamed(run) : null;
  const rowFailure =
    failed === null && row.error === undefined && row.data?.status === "failed"
      ? row.data.error
      : null;
  // A proposal is decided only on a page that is not showing a run in progress.
  const pending =
    !showLive && !rowRunning && state.error === undefined
      ? (state.data?.pending ?? null)
      : null;

  return (
    <section className="flex flex-col gap-4">
      <Header status={status} />
      {streaming && (
        <output
          ref={statusRef}
          tabIndex={-1}
          className="block text-sm text-muted-foreground"
        >
          {started
            ? "The run has started. Freyja's reply appears as it is written."
            : "Starting the run…"}
        </output>
      )}
      {!streaming && rowRunning && (
        <output className="block text-sm text-muted-foreground">
          A run is in progress. This page reads the task again until it ends.
        </output>
      )}
      {cut && <CutNotice />}
      {unread}
      {notice !== null && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {notice.text}
        </p>
      )}
      {failed !== null && <Failure error={failed} />}
      {rowFailure !== null && <Failure error={rowFailure} />}
      {live !== null ? (
        <>
          <Conversation messages={live.messages} />
          {live.steps.length > 0 && (
            <ol aria-label="Steps completed" className="flex flex-col gap-1">
              {live.steps.map((step, index) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: the steps only grow, in order.
                <li key={index} className="text-xs text-muted-foreground">
                  {step}
                </li>
              ))}
            </ol>
          )}
        </>
      ) : state.data !== undefined && !noConversation ? (
        <Conversation messages={state.data.messages} />
      ) : noConversation ? (
        <p className="text-sm text-muted-foreground">
          Nothing of the run has been recorded yet.
        </p>
      ) : (
        <output className="flex flex-col gap-3">
          <span className="sr-only">Loading the task</span>
          <Skeleton className="h-16" />
          <Skeleton className="h-24" />
        </output>
      )}
      {pending !== null && (
        <ProposalCard
          // Another proposal is another card: nothing of a decision on the earlier one stays.
          key={pending.interrupt_id}
          pending={pending}
          send={(decisions) => decideNow(pending.interrupt_id, decisions)}
          onStale={stale}
        />
      )}
    </section>
  );
}

function CutNotice() {
  return (
    <output className="block text-sm">
      The connection to the run ended before the run did. What the run did is
      read from the service.
    </output>
  );
}

function Header({ status }: { status?: ThreadSummary["status"] }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <h1 className="font-heading text-2xl font-semibold">Task</h1>
      {status !== undefined && <TaskStatus status={status} />}
    </div>
  );
}
