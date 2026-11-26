"use client";

import { useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import {
  type ActionRequest,
  type CallerSummary,
  type Decision,
  type DecisionSent,
  inexact,
  type PendingDecision,
  parseArguments,
  REJECT_REASON_LIMIT,
} from "@/lib/tasks";
import { argumentsText } from "./message";
import { startedBy } from "./started-by";

// The proposal card (FR-014 to FR-019). It offers the service's three decisions, only those
// the service allows for each action, and decides nothing itself: what an edit may be, and
// whether a decision still applies, is the service's to say. Its buttons stand outside any
// form, so Enter in a field presses none of them (FR-017).

/** Why a decision was refused in a way that means the page no longer shows the task as it is. */
const STALE: Record<string, string> = {
  proposal_mismatch:
    "This proposal is no longer the pending one: it was decided elsewhere, or another took its place. Nothing ran because of this decision. The task is shown as it is now.",
  nothing_pending:
    "Nothing is pending on this task any more: the proposal was decided elsewhere. Nothing ran because of this decision. The task is shown as it is now.",
  // A run starts on a proposal only when it is decided: one in progress most likely carries
  // out a decision made in another window (Edge Cases, "Two people decide the same
  // proposal").
  run_active:
    "A run is in progress on this task, so the decision was not applied: the proposal was most likely decided in another window. Nothing ran because of this decision. The task is shown as it is now.",
  thread_not_found: "This task no longer exists.",
};

const UNKNOWN =
  "No answer came, so whether the decision was applied is not known. The task is read again, and the proposal is shown only if it is still pending.";
// Feature 009: the agent that started the task canceled it, so only a rejection is taken
// (`requester_gone`). The card stays, with Reject where the service lists it: an action that
// no longer needs approval, as after the agents' approval mode turned Auto, takes only
// approve, so it can be rejected only once the mode is Manual again.
function requesterGone(single: boolean, rejectable: boolean): string {
  const them = single ? "this action" : "these actions";
  const it = single ? "it" : "them";
  return rejectable
    ? `The agent that started this task canceled it, so ${them} can no longer be approved. Reject ${it} to close the proposal; nothing of it runs.`
    : `The agent that started this task canceled it, so ${them} can no longer be approved. ${single ? "It no longer needs" : "They no longer need"} approval, so ${it} can be rejected only once the agents' approval mode is Manual again.`;
}

type Choice = {
  decision: Decision | null;
  /** The edited arguments as the person entered them. */
  text: string;
  reason: string;
};

/** How many characters a text holds, as the service counts them: by code point. */
function characters(text: string): number {
  return [...text].length;
}

function where(action: ActionRequest): string | null {
  if (action.agent !== null) {
    return `Sent to agent ${action.agent}`;
  }
  if (action.server !== null) {
    return `Runs on tool server ${action.server}`;
  }
  return null;
}

/** The decision a choice stands for, or why it cannot be sent yet. */
function sent(choice: Choice): DecisionSent | { error: string } | null {
  switch (choice.decision) {
    case null:
      return null;
    case "approve":
      return { type: "approve" };
    case "reject":
      if (characters(choice.reason) > REJECT_REASON_LIMIT) {
        return { error: "The reason is too long." };
      }
      return choice.reason.trim() === ""
        ? { type: "reject" }
        : { type: "reject", message: choice.reason };
    case "edit": {
      const parsed = parseArguments(choice.text);
      return "error" in parsed ? parsed : { type: "edit", args: parsed.args };
    }
  }
}

/**
 * The service's reasons for refused values, by the action they are about: a decision's
 * index is the third part of where a value is (`body`, `decisions`, index, ...). What names
 * no action is the card's.
 */
function reasons(error: ApiError): {
  byAction: Map<number, string[]>;
  card: string[];
} {
  const byAction = new Map<number, string[]>();
  const card: string[] = [];
  error.fields.forEach(({ message }, at) => {
    const where = error.locations[at] ?? [];
    const index =
      where[0] === "body" && where[1] === "decisions" ? where[2] : undefined;
    // The argument a reason is about, when it names one.
    const argument =
      where[3] === "args" &&
      typeof where.at(-1) === "string" &&
      where.length > 4
        ? `${String(where.at(-1))}: `
        : "";
    if (typeof index === "number") {
      byAction.set(index, [
        ...(byAction.get(index) ?? []),
        `${argument}${message}`,
      ]);
    } else {
      card.push(message);
    }
  });
  if (byAction.size === 0 && card.length === 0) {
    card.push(error.message);
  }
  return { byAction, card };
}

// A decision's button carries the action's name, which can be long. In a narrow window its
// words wrap inside the card, where a button that kept to one line would reach past the
// window's edge and out of reach (Edge Cases, "opened in a narrow window").
const WRAPS = "h-auto min-h-8 max-w-full shrink whitespace-normal text-left";

type Refusal = { byAction: Map<number, string[]>; card: string[] };

export function ProposalCard({
  pending,
  send,
  onStale,
  onRefused,
  caller = null,
}: {
  pending: PendingDecision;
  /** Who started the task, when another agent did (feature 009); null when a person did. */
  caller?: CallerSummary | null;
  /** Sends the decisions; a refusal is raised as an `ApiError`. */
  send: (decisions: DecisionSent[]) => Promise<void>;
  /** Told why the page no longer shows the task as it is; the task is read again. */
  onStale: (said: string) => void;
  /** Called when the service refused a decision's values; the task is read again. */
  onRefused?: () => void;
}) {
  const actions = pending.action_requests;
  const [choices, setChoices] = useState<Choice[]>(() =>
    actions.map((action) => ({
      decision: null,
      text: argumentsText(action.args),
      reason: "",
    })),
  );
  const [refused, setRefused] = useState<Refusal | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);
  const heading = useId();
  const single = actions.length === 1;
  const rejectable = actions.every((action) =>
    action.allowed_decisions.includes("reject"),
  );

  function choose(index: number, values: Partial<Choice>) {
    setRefused(null);
    setChoices((current) =>
      current.map((choice, at) =>
        at === index ? { ...choice, ...values } : choice,
      ),
    );
  }

  async function submit(made: Choice[]) {
    if (busy.current) {
      return;
    }
    const decisions = made.map(sent);
    if (
      decisions.some((decision) => decision === null || "error" in decision)
    ) {
      return;
    }
    busy.current = true;
    setSending(true);
    setRefused(null);
    try {
      await send(decisions as DecisionSent[]);
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "requester_gone") {
        setRefused({
          byAction: new Map(),
          card: [requesterGone(single, rejectable)],
        });
        onRefused?.();
      } else if (failure instanceof ApiError && failure.code === "invalid") {
        // Each edit stays, with the service's reason beside it (FR-015). The reason can be
        // that a decision is no longer allowed for the action: the task is read again, so
        // that the card offers what the service lists now (FR-014).
        setRefused(reasons(failure));
        onRefused?.();
      } else if (failure instanceof ApiError && failure.code in STALE) {
        onStale(STALE[failure.code]);
      } else if (
        failure instanceof ApiError &&
        failure.code === "unreachable"
      ) {
        onStale(UNKNOWN);
      } else {
        setRefused({
          byAction: new Map(),
          card: [
            failure instanceof ApiError
              ? failure.message
              : "The decision could not be sent.",
          ],
        });
      }
    } finally {
      busy.current = false;
      setSending(false);
    }
  }

  // A decision is offered only while the service lists it for the action (FR-014). The
  // service works its list out from what is configured now, so a later reading of the
  // same proposal can list fewer: a decision that was chosen, or whose form is open, and
  // is no longer listed counts as not chosen.
  const kept = choices.map((choice, index) =>
    choice.decision !== null &&
    !actions[index]?.allowed_decisions.includes(choice.decision)
      ? { ...choice, decision: null }
      : choice,
  );
  // It is dropped for good, not hidden: were the decision listed again later, it would
  // otherwise be chosen again, and its form open, by no press of anyone's (FR-017).
  if (kept.some((choice, index) => choice !== choices[index])) {
    setChoices(kept);
  }
  const ready = kept.map(sent);
  // A control's name says on which action (FR-012a): where two actions of a proposal have
  // one name, each is called by its place as well.
  const called = actions.map((action, index) =>
    actions.filter(({ name }) => name === action.name).length > 1
      ? `${action.name} (action ${index + 1} of ${actions.length})`
      : action.name,
  );
  const complete = ready.every(
    (decision) => decision !== null && !("error" in decision),
  );

  return (
    <section
      aria-labelledby={heading}
      className="flex flex-col gap-4 rounded-lg border-2 border-primary/40 p-4"
    >
      <div className="flex flex-col gap-1">
        <h2 id={heading} className="font-heading text-lg font-semibold">
          {single
            ? "Freyja proposes an action that needs your decision"
            : `Freyja proposes ${actions.length} actions that need your decision`}
        </h2>
        <p className="text-sm text-muted-foreground">
          Nothing has run. An approved action runs once at most, and not at all
          when it is no longer set up on the service; a rejected one never runs.
        </p>
        {/* Feature 009: another agent's task led to it, and no person asked for it. */}
        {caller !== null && (
          <p className="text-sm">
            {single
              ? `${startedBy(caller)}. No person asked for this action: the agent's task led Freyja to propose it.`
              : `${startedBy(caller)}. No person asked for these actions: the agent's task led Freyja to propose them.`}
          </p>
        )}
        {caller?.canceled === true && refused === null && (
          <p className="text-sm font-medium">
            {requesterGone(single, rejectable)}
          </p>
        )}
      </div>
      <ol className="flex flex-col gap-4">
        {actions.map((action, index) => (
          <li key={`${action.name}-${index}`}>
            <ActionDecision
              action={action}
              called={called[index]}
              choice={kept[index]}
              single={single}
              sending={sending}
              refused={refused?.byAction.get(index)?.join(" ") ?? null}
              onChoose={(values) => choose(index, values)}
              onSend={(values) => submit([{ ...kept[index], ...values }])}
            />
          </li>
        ))}
      </ol>
      {refused !== null && refused.card.length > 0 && (
        <p role="alert" className="text-sm text-destructive">
          {refused.card.join(" ")}
        </p>
      )}
      {!single && (
        <div className="flex flex-col items-start gap-2 border-t pt-3">
          <p className="text-sm text-muted-foreground">
            {complete
              ? "Every action has a decision."
              : "Each action needs a decision before anything is sent."}
          </p>
          <Button
            type="button"
            disabled={!complete || sending}
            onClick={() => submit(kept)}
          >
            {sending ? "Sending…" : `Send the ${actions.length} decisions`}
          </Button>
        </div>
      )}
    </section>
  );
}

function ActionDecision({
  action,
  called,
  choice,
  single,
  sending,
  refused,
  onChoose,
  onSend,
}: {
  action: ActionRequest;
  /** What the action's controls call it: its name, and its place where the name repeats. */
  called: string;
  choice: Choice;
  single: boolean;
  sending: boolean;
  /** The service's reason for refusing this action's decision. */
  refused: string | null;
  onChoose: (values: Partial<Choice>) => void;
  onSend: (values: Partial<Choice>) => void;
}) {
  const editor = useId();
  const reasonBox = useId();
  const editSaid = useId();
  const allowed = (decision: Decision) =>
    action.allowed_decisions.includes(decision);
  const parsed =
    choice.decision === "edit" ? parseArguments(choice.text) : null;
  const notObject = parsed !== null && "error" in parsed ? parsed.error : null;
  const length = characters(choice.reason);
  const tooLong = length > REJECT_REASON_LIMIT;
  const goesTo = where(action);
  const name = called;
  const editing = choice.decision === "edit";
  const rejecting = choice.decision === "reject";
  // With one action, Edit and Reject open what they need, and the button that sends is
  // inside it; with several, each button chooses this action's decision.
  const state = (on: boolean) =>
    single ? { "aria-expanded": on } : { "aria-pressed": on };

  return (
    <article
      aria-label={`Proposed action ${name}`}
      className="flex flex-col gap-3 rounded-md border p-3"
    >
      <div className="flex flex-col gap-1">
        <h3 className="font-mono text-sm font-semibold">{action.name}</h3>
        {goesTo !== null && (
          <p className="text-xs text-muted-foreground">{goesTo}</p>
        )}
        {action.description && (
          <p className="whitespace-pre-wrap break-words text-sm">
            {action.description}
          </p>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <p className="text-xs font-medium text-muted-foreground">Arguments</p>
        <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 font-mono text-xs">
          {argumentsText(action.args)}
        </pre>
        {inexact(action.args) && (
          <p className="text-xs text-muted-foreground">
            An argument is a whole number too large for a browser to hold
            exactly, so its last digits here may differ from what was proposed.
            Approving runs the action as it was proposed. This page cannot send
            such a number, so these arguments are edited through the service's
            API.
          </p>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {allowed("approve") &&
          (single ? (
            <Button
              className={WRAPS}
              type="button"
              variant={editing || rejecting ? "outline" : "default"}
              disabled={sending}
              onClick={() => onSend({ decision: "approve" })}
            >
              {/* While the arguments are being edited, this button is not the one that
                  sends them: it says so. */}
              {editing ? `Approve ${name} as proposed` : `Approve ${name}`}
            </Button>
          ) : (
            <Button
              className={WRAPS}
              type="button"
              variant={choice.decision === "approve" ? "default" : "outline"}
              aria-pressed={choice.decision === "approve"}
              disabled={sending}
              onClick={() => onChoose({ decision: "approve" })}
            >
              {`Approve ${name}`}
            </Button>
          ))}
        {allowed("edit") && (
          <Button
            className={WRAPS}
            type="button"
            variant={editing ? "default" : "outline"}
            {...state(editing)}
            disabled={sending}
            onClick={() => onChoose({ decision: "edit" })}
          >
            {`Edit the arguments of ${name}`}
          </Button>
        )}
        {allowed("reject") && (
          <Button
            className={WRAPS}
            type="button"
            variant={rejecting ? "destructive" : "outline"}
            {...state(rejecting)}
            disabled={sending}
            onClick={() => onChoose({ decision: "reject" })}
          >
            {`Reject ${name}`}
          </Button>
        )}
      </div>

      {editing && (
        <div className="flex flex-col gap-2">
          <Label htmlFor={editor}>{`Arguments for ${name}, as JSON`}</Label>
          <textarea
            id={editor}
            value={choice.text}
            onChange={(event) => onChoose({ text: event.target.value })}
            rows={Math.min(12, choice.text.split("\n").length + 1)}
            spellCheck={false}
            aria-describedby={
              notObject !== null || refused !== null ? editSaid : undefined
            }
            aria-invalid={notObject !== null || refused !== null || undefined}
            className="w-full rounded-lg border border-input bg-transparent px-3 py-2 font-mono text-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
          {(notObject !== null || refused !== null) && (
            <p id={editSaid} role="alert" className="text-sm text-destructive">
              {notObject ?? refused}
            </p>
          )}
          {single && (
            <div>
              <Button
                className={WRAPS}
                type="button"
                disabled={sending || notObject !== null}
                onClick={() => onSend({})}
              >
                {sending ? "Sending…" : `Approve ${name} with these arguments`}
              </Button>
            </div>
          )}
        </div>
      )}

      {!editing && refused !== null && (
        <p role="alert" className="text-sm text-destructive">
          {refused}
        </p>
      )}

      {rejecting && (
        <div className="flex flex-col gap-2">
          <Label htmlFor={reasonBox}>
            {`Why ${name} is rejected (optional, Freyja is told)`}
          </Label>
          <textarea
            id={reasonBox}
            value={choice.reason}
            onChange={(event) => onChoose({ reason: event.target.value })}
            rows={3}
            aria-invalid={tooLong || undefined}
            className="w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
          <p
            className={
              tooLong
                ? "text-xs text-destructive"
                : "text-xs text-muted-foreground"
            }
          >
            {`${length} of ${REJECT_REASON_LIMIT} characters`}
          </p>
          {single && (
            <div>
              <Button
                className={WRAPS}
                type="button"
                variant="destructive"
                disabled={sending || tooLong}
                onClick={() => onSend({})}
              >
                {sending ? "Sending…" : `Reject ${name} now`}
              </Button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
