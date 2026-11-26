"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { mergeContent } from "@/lib/merge";
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
  READING,
  readState,
  readThread,
  type StreamEvent,
  startRun,
  stateKey,
  THREADS_KEY,
  type ThreadState,
  type ThreadSummary,
  type ToolCall,
  threadKey,
} from "@/lib/tasks";
import { AgentTasks, awaited } from "./agent-tasks";
import { type Blocked, FollowUp, inWords } from "./follow-up";
import { contentText, MessageView } from "./message";
import { ProposalCard } from "./proposal-card";
import { SendBox } from "./send-box";
import { StartedBy } from "./started-by";
import { TaskStatus } from "./status";
import { StopButton, type StopState } from "./stop-button";

// How often a page that does not read a run's stream reads the task again while that run is
// in progress. FR-013 gives five seconds as the most; a reading takes time of its own, so
// the period is shorter than the bound.
export const FOLLOW_SECONDS = 4;

// How often a page reads a task again while one of its agent tasks is pending, or has ended
// with its outcome not yet in the conversation. FR-013 gives fifteen seconds as the most.
export const AGENT_SECONDS = 12;

// How soon a task is read again when the reading made at the end of this page's own run
// still says a run is in progress: the service sends the run's last event before it gives
// the run's lease back, which takes it a moment more.
export const AGAIN_SECONDS = 1;

/** Whether a reading made at the end of a run still says a run is in progress. */
function stillRunning(
  row: PromiseSettledResult<ThreadSummary | undefined>,
  state: PromiseSettledResult<ThreadState | undefined>,
): boolean {
  return (
    (row.status === "fulfilled" && row.value?.status === "running") ||
    (state.status === "fulfilled" && state.value?.run_active === true)
  );
}

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

/** How many of the messages a person sent. */
function fromPeople(messages: readonly Message[]): number {
  return messages.filter((message) => message.type === "human").length;
}

/** A step the run completed, in words; the middleware's own steps are not shown. */
const STEPS: Record<string, string> = {
  model: "Freyja wrote a reply",
  tools: "Freyja ran the actions it asked for",
};

/**
 * The actions a reply shows once a piece of it arrived. Each piece carries the actions as
 * that piece alone makes them: the first piece of an action names it, under its id, and the
 * next ones hold parts of its arguments under no name. A named action is added, or takes
 * the place of the one of its id; one that names nothing is left out, so that no half of
 * an action is shown (FR-010). An empty id is no id.
 */
function withNamed(
  shown: ToolCall[] | null | undefined,
  piece: ToolCall[] | null | undefined,
): ToolCall[] | null | undefined {
  const named = (piece ?? []).filter((call) => call.name !== "");
  if (named.length === 0) {
    return shown;
  }
  const calls = [...(shown ?? [])];
  for (const call of named) {
    const at = calls.findIndex(
      (earlier) => Boolean(call.id) && earlier.id === call.id,
    );
    if (at === -1) {
      calls.push(call);
    } else {
      calls[at] = call;
    }
  }
  return calls;
}

/** The actions of the replies a model step's update holds: each whole, with its arguments. */
function wholeCalls(data: unknown): Map<string, ToolCall> {
  const whole = new Map<string, ToolCall>();
  const replies = (data as { messages?: unknown } | null)?.messages;
  if (!Array.isArray(replies)) {
    return whole;
  }
  for (const reply of replies as Partial<Message>[]) {
    for (const call of reply?.tool_calls ?? []) {
      if (call.id && call.name !== "") {
        whole.set(call.id, call);
      }
    }
  }
  return whole;
}

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
  // The messages above are held, not streamed: they are shown whatever they hold.
  const held = messages.length;
  // Where the messages streamed since the model's last step was done begin: a step's
  // update is about the reply of that step, and a provider may give the actions of two
  // replies the same ids.
  let since = held;
  const at = new Map<string, number>();
  const steps: string[] = [];
  run.events.forEach((event: StreamEvent, index) => {
    if (event.event === "update") {
      const update = event.data as { node?: string; data?: unknown } | null;
      const node = update?.node ?? "";
      if (node in STEPS) {
        steps.push(STEPS[node]);
      }
      // Once the model's step is done, its update holds the reply whole: each action it
      // asks for with all of its arguments, which the pieces gave in parts or not at all.
      // An action shown is matched by its id, which every piece of it and the update share.
      if (node === "model") {
        const whole = wholeCalls(update?.data);
        for (let index = since; index < messages.length; index += 1) {
          const calls = messages[index].tool_calls;
          if (calls?.some((call) => call.id && whole.has(call.id))) {
            messages[index] = {
              ...messages[index],
              tool_calls: calls.map(
                (call) => (call.id && whole.get(call.id)) || call,
              ),
            };
          }
        }
        since = messages.length;
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
    messages[found] = {
      ...earlier,
      content: mergeContent(earlier.content, message.content),
      tool_calls: withNamed(earlier.tool_calls, message.tool_calls),
    };
  });
  // A reply of Freyja's that holds nothing yet is not shown: some models open a reply with
  // an empty piece, and one under an id of its own that nothing follows. Every other
  // message is, an action's result that holds no text among them.
  const shown = messages.filter(
    (message, index) =>
      index < held ||
      message.type !== "ai" ||
      contentText(message.content) !== "" ||
      (message.tool_calls ?? []).length > 0,
  );
  return { messages: shown, steps };
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
          <Link href="/models" className="underline underline-offset-4">
            Go to the Models section
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
  // How many readings of the row have been answered, so that a reading can be told from
  // the one the page already held when something happened.
  const [rowReads, setRowReads] = useState(0);
  const rowReadsNow = useRef(0);
  const row = useSWR<ThreadSummary, unknown>(
    threadKey(threadId),
    () => readThread(threadId),
    {
      ...READING,
      onSuccess: () => {
        rowReadsNow.current += 1;
        setRowReads(rowReadsNow.current);
      },
    },
  );
  // The same for the conversation's readings.
  const [stateReads, setStateReads] = useState(0);
  const stateReadsNow = useRef(0);
  const state = useSWR<ThreadState, unknown>(
    stateKey(threadId),
    () => readState(threadId),
    {
      ...READING,
      onSuccess: () => {
        stateReadsNow.current += 1;
        setStateReads(stateReadsNow.current);
      },
    },
  );
  const { mutate: readListAgain } = useSWRConfig();
  const { mutate: readRowAgain } = row;
  const { mutate: readStateAgain } = state;
  const ended = run?.ended ?? null;

  // The end of a run for which the task was asked for again and that request was answered,
  // with the number of readings of the conversation answered before it was asked.
  const [readFor, setReadFor] = useState<{
    end: RunEnd;
    states: number;
  } | null>(null);
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
  // Set when this page's stream was cut before the run's last event, to the number of
  // readings of the row answered until then. The cut is said, and the task is followed,
  // until a reading made after it says the run is no longer in progress (FR-010): the row
  // the page held before, as for a follow-up, says nothing of this run.
  const [cut, setCut] = useState<number | null>(null);
  // Set when the service accepted a stop this page asked for: `at` is the number of
  // readings of the row answered until then, since how the run ended is told by a reading
  // made after it, and `run` which of the task's runs the service said the stop was for.
  // `own` says whether the page read the run's stream when the stop was asked.
  const [stopAsked, setStopAsked] = useState<{
    at: number;
    run: number;
    own: boolean;
  } | null>(null);
  // Set when a run this page asked to stop ended another way before the stop took effect:
  // it is said until the task's next run (FR-022a). `at` is the number of readings of the
  // row answered until then, and `rested` whether a reading since has said that no run is
  // in progress: only after that does a run in progress mean another run.
  const [late, setLate] = useState<{ at: number; rested: boolean } | null>(
    null,
  );
  // The follow-up box: another box, holding `text`, each time `box` changes. It changes when
  // a follow-up's run failed before its message was recorded, so that the text is offered
  // again (FR-008's rule for a new task, kept for a follow-up).
  const [draft, setDraft] = useState({ box: 0, text: "" });
  // The proposal the page last brought into view: each one is brought into view once, when
  // its card appears, and not again on a later reading while the person reads above it.
  const broughtIntoView = useRef<string | null>(null);

  // When a run this page shows ends, in whichever way, the task is read again once, and once
  // more `AGAIN_SECONDS` later when that reading still says a run is in progress (FR-010,
  // FR-019, FR-020). Until then the page would show the run in progress, offer Stop, hide a
  // proposal the run paused on, and keep the follow-up box shut. The second reading's timer
  // outlives the run, which the page forgets once the first is answered.
  const again = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(again.current), []);
  useEffect(() => {
    if (ended === null) {
      return;
    }
    if (ended.kind === "connection_ended") {
      setCut(rowReadsNow.current);
      // No last event will say how the run ended: a stop asked while the page read the
      // stream is settled by the row, as for a run the page follows, also once the page has
      // forgotten the run.
      setStopAsked((asked) =>
        asked === null ? null : { ...asked, own: false },
      );
    }
    let current = true;
    const states = stateReadsNow.current;
    void Promise.allSettled([readRowAgain(), readStateAgain()]).then(
      ([rowAnswer, stateAnswer]) => {
        if (!current) {
          return;
        }
        setReadFor({ end: ended, states });
        if (stillRunning(rowAnswer, stateAnswer)) {
          clearTimeout(again.current);
          again.current = setTimeout(() => {
            void readRowAgain().catch(() => undefined);
            void readStateAgain().catch(() => undefined);
          }, AGAIN_SECONDS * 1000);
        }
      },
    );
    // The list and the sidebar's number follow at once, not at their next reading.
    void readListAgain(THREADS_KEY);
    return () => {
      current = false;
    };
  }, [ended, readRowAgain, readStateAgain, readListAgain]);

  const failed = runError(run);
  const noConversation = missing(state.error);
  const rowMissing = missing(row.error);
  // A read that failed for another reason than that there is no such task. While the page
  // reads its own run's stream, a reading of the row that failed is not said: the stream
  // says how the run goes, and the row is read again a few seconds later.
  const readError =
    (state.error !== undefined && !noConversation ? state.error : undefined) ??
    (row.error !== undefined && !rowMissing && !streaming
      ? row.error
      : undefined);
  const rowRunning = row.error === undefined && row.data?.status === "running";
  // A new task whose first run failed before anything was recorded keeps its run, whose
  // message is offered again (FR-008).
  const offerAgain = failed !== null && noConversation;
  // A continuation that failed while its proposal is still pending, as when an agent came
  // to need approval since: its reason stays beside the card it is about (005 FR-023).
  const failedOnProposal =
    failed !== null && (state.data?.pending ?? null) !== null;
  // What the stream brought stays on the page until the task has been read again after it,
  // also when that reading failed: the conversation the page held before lacks what the run
  // added, and would look as if the message had never been sent.
  // "Read again" is: the request made at the run's end was answered, and the conversation
  // the page holds comes from a reading answered after that end, or there is none to hold.
  // The second part matters when another reading was asked for meanwhile, as after a stop:
  // it takes the place of the one made at the end, which is then answered with what the
  // page already held, the conversation from before the run.
  const readAfter =
    readFor !== null &&
    readFor.end === ended &&
    (stateReads > readFor.states || state.error !== undefined);
  const showLive =
    run !== undefined &&
    (streaming || (ended !== null && (!readAfter || readError !== undefined)));
  const settled = run !== undefined && ended !== null && readAfter;
  const failedCode = failed?.code ?? null;
  const recorded = state.data?.messages;

  // Once the end of a run has been read, what the service holds is what the page shows: the
  // run is forgotten, so that how it ended is not shown later as if it were current.
  useEffect(() => {
    if (
      settled &&
      !offerAgain &&
      !failedOnProposal &&
      readError === undefined
    ) {
      // A follow-up that failed and left no message of a person's in the conversation, as
      // when no usable model is active: nothing of it was recorded, and its text goes back
      // into the box. A message that was recorded is never offered again.
      const text = run?.message ?? null;
      if (
        failedCode !== null &&
        text !== null &&
        recorded !== undefined &&
        fromPeople(recorded) <= fromPeople(run?.before ?? [])
      ) {
        setDraft((current) => ({ box: current.box + 1, text }));
      }
      clear(threadId);
    }
  }, [
    settled,
    offerAgain,
    failedOnProposal,
    readError,
    threadId,
    failedCode,
    recorded,
    run,
  ]);

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
  // The conversation's own reading can say a run is in progress when the row's, asked for
  // at the same time and answered first, does not yet: both count.
  const runActive =
    state.error === undefined && state.data?.run_active === true;
  const inProgress = rowRunning || runActive;
  const cutUnread =
    cut !== null && (row.data === undefined || readError !== undefined);
  // A run of this page ended and the task could not be read after it.
  const endUnread =
    run !== undefined && ended !== null && readError !== undefined;
  const following =
    !streaming &&
    (row.data?.status === "running" || runActive || cutUnread || endUnread);
  // Waiting for an agent: one of the task's agent tasks is pending, or ended with its
  // outcome not yet in the conversation. The service records the outcome in a run of its
  // own, which this page does not read, once no proposal is pending; the task is read
  // again until it has (FR-013).
  const waiting =
    !streaming &&
    (row.data?.status === "waiting_for_agent" ||
      (state.data?.agent_tasks ?? []).some(awaited));
  // While the page reads its own run's stream, the row is read on the same timer, and only
  // the row: a stop asked in another window is then said, and Stop no longer offered
  // (FR-022). The stream says the rest.
  const period =
    streaming || following ? FOLLOW_SECONDS : waiting ? AGENT_SECONDS : null;
  // Whether a reading the timer below asked for is still under way. Kept here and not
  // taken from SWR's `isValidating`: a component that reads that is rendered again each
  // time a reading begins or ends, which this view's effects are not written for.
  const reading = useRef({ row: false, state: false });
  // Feature 008: the browser's time, which how long ago an agent reported is worded from. It
  // is read in the timer below, when a reading of the task's state is answered, when the
  // view mounts, and when a hidden page is shown again, never while rendering, and is null
  // until the first of them (research R8). SWR hands the view the
  // same data when a reading equals the one before, so an age worked out from the data
  // alone would stand still: the count of answered readings moves at each one.
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    if (stateReads > 0) {
      setNow(Date.now());
    }
  }, [stateReads]);
  // Also when the page is opened: a reading the library takes for one already answered for
  // another page counts no answered reading here (feature 008, T046).
  useEffect(() => {
    setNow(Date.now());
  }, []);
  // And when a hidden page is shown again, whether or not the reading made then is answered,
  // so that its age is never the one it had when it was hidden (T045).
  useEffect(() => {
    const shown = () => {
      if (document.visibilityState !== "hidden") {
        setNow(Date.now());
      }
    };
    document.addEventListener("visibilitychange", shown);
    return () => document.removeEventListener("visibilitychange", shown);
  }, []);
  useEffect(() => {
    if (period === null) {
      return;
    }
    const timer = setInterval(() => {
      // Nothing is read while the page is hidden: SWR reads again when it is shown.
      if (document.visibilityState === "hidden") {
        return;
      }
      setNow(Date.now());
      // A reading asked for while one is under way takes its place, and the earlier one's
      // answer is dropped: with a service slower than the period, none would ever be
      // shown. A reading that gets no answer is given up by itself (`ANSWER_SECONDS`).
      const under = reading.current;
      if (!under.row) {
        under.row = true;
        void readRowAgain()
          .catch(() => undefined)
          .finally(() => {
            under.row = false;
          });
      }
      if (!streaming && !under.state) {
        under.state = true;
        void readStateAgain()
          .catch(() => undefined)
          .finally(() => {
            under.state = false;
          });
      }
    }, period * 1000);
    return () => clearInterval(timer);
  }, [period, streaming, readRowAgain, readStateAgain]);
  // A run this page followed has ended: the conversation is read once more. The reading that
  // showed the end was asked for together with the conversation's, and the service may have
  // answered that one before the run recorded its last step.
  const followed = useRef(false);
  const rowRead = row.data !== undefined && row.error === undefined;
  // The page's own run, shown as it streamed until the task is read after it, is not one
  // the page follows: the end of that run is read once already.
  useEffect(() => {
    if (inProgress) {
      if (!showLive) {
        followed.current = true;
      }
    } else if (followed.current && rowRead) {
      followed.current = false;
      void readStateAgain();
    }
  }, [inProgress, showLive, rowRead, readStateAgain]);
  useEffect(() => {
    if (cut !== null && rowReads > cut && rowRead && !inProgress) {
      setCut(null);
    }
  }, [cut, rowReads, rowRead, inProgress]);

  // A stop this page asked for is settled by how the run ended. A run that ended as stopped
  // needs no more words, since the status says it; one that ended another way, by pausing
  // on a proposal, finishing, or failing, is shown as it ended, and the page says that the
  // stop came too late (FR-022a).
  // A page that reads the run's stream is told by the run's last event, which is about this
  // run and no other. The row may go on saying "running" for a moment after that event.
  useEffect(() => {
    if (stopAsked === null || ended?.kind !== "event") {
      return;
    }
    const { event, data } = ended.event;
    const stopped =
      event === "end" &&
      (data as { status?: string } | null)?.status === "stopped";
    setStopAsked(null);
    if (!stopped) {
      setLate({ at: rowReadsNow.current, rested: false });
    }
  }, [stopAsked, ended]);
  // A page that follows the run by reading is told by a reading of the row made after the
  // stop. The row names its run. One that is of a later run says nothing of how the run
  // this page stopped ended, as stopped or another way: the page then claims neither. One
  // of the same run that says the run is in progress goes on waiting, also when it does
  // not say "stopping": it was made before the stop was marked.
  const rowStatus = row.data?.status;
  const rowStopping = rowRunning && row.data?.stopping === true;
  const rowRuns = row.data?.runs;
  const rowUnread = row.error !== undefined;
  useEffect(() => {
    // A reading that failed says nothing of the run: the row held from before is not the
    // service's word on how it ended. Nor does any reading of a run whose stream the page
    // read when the stop was asked: the stream's last event says, also on the render it
    // arrives in. Only a stream that was cut leaves it to the row.
    if (
      stopAsked === null ||
      (stopAsked.own && ended?.kind !== "connection_ended") ||
      rowReads <= stopAsked.at ||
      rowRuns === undefined ||
      rowUnread
    ) {
      return;
    }
    if (rowRuns !== stopAsked.run) {
      setStopAsked(null);
      return;
    }
    if (rowRunning) {
      return;
    }
    // The row says the run has ended, and how: its status is the service's word.
    setStopAsked(null);
    if (rowStatus !== "stopped") {
      setLate({ at: rowReadsNow.current, rested: true });
    }
  }, [ended, stopAsked, rowReads, rowRuns, rowRunning, rowStatus, rowUnread]);
  // That a stop came too late is about the run that ended: it goes with the next run, one
  // of this page or one the row shows once it has said that the earlier run was over.
  useEffect(() => {
    if (late === null) {
      return;
    }
    if (streaming || (late.rested && rowRunning)) {
      setLate(null);
    } else if (!late.rested && rowReads > late.at && !rowRunning) {
      setLate({ at: late.at, rested: true });
    }
  }, [late, streaming, rowReads, rowRunning]);

  // A decision goes through the service's own checks; its continuation is shown as a run
  // is, below the conversation as it was when the person decided (FR-019).
  async function decideNow(interruptId: string, decisions: DecisionSent[]) {
    const before = state.data?.messages ?? [];
    const answer = await decide(threadId, interruptId, decisions);
    setNotice(null);
    setStopAsked(null);
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
    let answer: ReadableStream<Uint8Array>;
    try {
      answer = await startRun(threadId, text);
    } catch (refused) {
      throw inWords(refused);
    }
    setStopAsked(null);
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
          ? readError === row.error && readError.code === "internal"
            ? // The service's sentence speaks of the list of threads (FR-001a).
              "The task could not be read."
            : readError.message
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
        {cut !== null && <CutNotice />}
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
  // Not while the row the page holds may be from before its own run, and say how an earlier
  // one failed: from the run's start until the reading made at its end was answered.
  const rowFromBefore = streaming || (ended !== null && readFor?.end !== ended);
  const rowFailure =
    failed === null &&
    !rowFromBefore &&
    row.error === undefined &&
    row.data?.status === "failed"
      ? row.data.error
      : null;
  // A proposal is decided only on a page that is not showing a run in progress.
  const pending =
    !showLive && !inProgress && state.error === undefined
      ? (state.data?.pending ?? null)
      : null;
  // Another message is sent only when no run is in progress and no proposal is pending
  // (FR-020). The service rules the same; this is what the page knows of it, said beside
  // the box. A proposal counts also when it is read from an earlier, older reading, and
  // when only the row says so.
  const blocked: Blocked =
    showLive || following
      ? "run"
      : (state.data?.pending ?? null) !== null ||
          row.data?.status === "needs_decision"
        ? "proposal"
        : null;
  // What the page says of the run, in one place that is always there: it takes the focus
  // when a message, a decision, or a stop was accepted, and keeps it when the run ends, where an
  // element that went away would drop the focus to the top of the page. That a run of this
  // page has ended is said for whoever does not see the status change (FR-012a).
  // A run in progress that the page shows as one: its own while it streams, or one the
  // readings say is in progress. Not the moment between the end of its own run and the
  // reading after it, when the row the page holds is still from during the run.
  const runShown = streaming || (!showLive && inProgress);
  // The step under way completes first: until the run has ended the page says "stopping",
  // from its own stop once the service accepted it, and from the row, which also knows of
  // a stop asked in another window (FR-022).
  const stopping = runShown && (stopAsked !== null || rowStopping);
  const last = ended ?? readFor?.end ?? null;
  const said = stopping
    ? "The run is stopping. The step under way completes first."
    : streaming
      ? started
        ? "The run has started. Freyja's reply appears as it is written."
        : "Starting the run…"
      : inProgress && !showLive
        ? "A run is in progress. This page reads the task again until it ends."
        : waiting
          ? `This page reads the task again every ${AGENT_SECONDS} seconds while an agent task is pending or its outcome is not in the conversation yet.`
          : null;
  const words = [
    late !== null
      ? "The run ended before the stop took effect. The task is shown as it ended."
      : null,
    said,
  ]
    .filter((sentence) => sentence !== null)
    .join(" ");
  const over =
    last?.kind !== "event"
      ? ""
      : last.event.event === "interrupt"
        ? "The run has paused: a proposal waits for a decision."
        : "The run has ended.";
  // Stop is offered for a run the console started, whether or not this page reads its
  // stream (FR-022): a run of this page once the service has said that it started, and a
  // run this page follows when the row says it can be stopped. With no run in progress
  // there is no Stop, whatever the row says; a proposal is declined by rejecting it.
  const stopState: StopState | null =
    !runShown || stopping
      ? null
      : streaming
        ? started
          ? "stoppable"
          : null
        : rowRunning
          ? row.data?.stoppable === true
            ? "stoppable"
            : "not_stoppable"
          : null;
  const agentTasks = noConversation ? [] : (state.data?.agent_tasks ?? []);
  // The box stands under a conversation, once the task has been read.
  const conversed =
    live !== null ||
    (state.data !== undefined && !noConversation && row.data !== undefined);
  // Another agent started this task (feature 009): only it sends messages on it, so the
  // page offers no box for one (FR-024). Its proposals are decided here as any.
  const caller = row.data?.caller ?? null;
  // A proposal's card is the last part of the page; when one appears, the page scrolls to
  // it, so that a person who reads the conversation sees that the task waits for them.
  const proposalRef = (element: HTMLDivElement | null) => {
    if (
      element === null ||
      pending === null ||
      broughtIntoView.current === pending.interrupt_id
    ) {
      return;
    }
    broughtIntoView.current = pending.interrupt_id;
    // jsdom, where the unit tests run, has no scrolling.
    element.scrollIntoView?.({ block: "start" });
  };

  return (
    <section className="flex flex-col gap-4">
      <Header status={status} caller={caller} />
      <output
        ref={statusRef}
        tabIndex={-1}
        className={
          words === "" ? "sr-only" : "block text-sm text-muted-foreground"
        }
      >
        {words === "" ? over : words}
      </output>
      {stopState !== null && (
        <StopButton
          threadId={threadId}
          about={row.data?.first_message ?? run?.message ?? null}
          run={stopState}
          onAsked={(asked) => {
            setStopAsked({
              at: rowReadsNow.current,
              run: asked,
              own: streaming,
            });
            setNotice(null);
            // The button goes; the focus goes to where the page says the run is stopping.
            statusRef.current?.focus();
            // The row then says that the run is stopping, and later how it ended.
            void readRowAgain();
          }}
          onStale={stale}
        />
      )}
      {cut !== null && <CutNotice />}
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
      <AgentTasks tasks={agentTasks} now={now} />
      {conversed && caller !== null && (
        <p className="text-sm text-muted-foreground">
          Another agent started this task, and only that agent sends messages on
          it. Its proposals are decided here, and its run is stopped here, as
          for any task.
        </p>
      )}
      {/* Feature 009: the requesting agent canceled; nobody can approve what the task still
          proposed, and the service rejects it by rule (FR-021, FR-022a). What ran before the
          cancel, such as an action a person approved, stays in the conversation. */}
      {conversed && caller?.canceled === true && (
        <p className="text-sm">
          The agent that started this task canceled it. Nothing the task still
          proposed can be approved any more: Freyja rejects it by itself, for
          that cancel and not as a person's decision. What ran before the cancel
          is in the conversation.
        </p>
      )}
      {conversed && caller === null && (
        // While a proposal's card is shown, it takes the box's place at the end of the page.
        // The box is hidden, not removed, so that what was typed in it stays.
        <div hidden={pending !== null}>
          <FollowUp
            key={draft.box}
            threadId={threadId}
            before={state.data?.messages ?? []}
            blocked={blocked}
            initial={draft.text}
            onSent={() => {
              setNotice(null);
              setStopAsked(null);
              setFocusStatus(true);
            }}
            onMoved={() => {
              void readRowAgain();
              void readStateAgain();
            }}
          />
        </div>
      )}
      {pending !== null && (
        // The last part of the page, where a person looks for what the task waits for, and
        // brought into view when it appears (FR-014).
        <div ref={proposalRef}>
          <ProposalCard
            // Another proposal is another card: nothing of a decision on the earlier one stays.
            key={pending.interrupt_id}
            pending={pending}
            caller={caller}
            send={(decisions) => decideNow(pending.interrupt_id, decisions)}
            onStale={stale}
            onRefused={() => {
              void readRowAgain();
              void readStateAgain();
            }}
          />
        </div>
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

function Header({
  status,
  caller = null,
}: {
  status?: ThreadSummary["status"];
  caller?: ThreadSummary["caller"];
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <h1 className="font-heading text-2xl font-semibold">Task</h1>
      {status !== undefined && <TaskStatus status={status} />}
      <StartedBy caller={caller} className="text-sm text-muted-foreground" />
    </div>
  );
}
