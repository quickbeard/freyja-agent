"use client";

import { type MouseEvent, useEffect, useRef, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  type Agent,
  type AgentsMode,
  approvalOf,
  changeAgent,
} from "@/lib/agents";
import { ApiError, isGone } from "@/lib/api";

/**
 * What is said when the request got no answer. The agent may have been changed and the
 * answer lost, so this never says that the request failed.
 */
const OUTCOME_NOT_KNOWN =
  "The request got no answer, so it is not known whether the agent was changed. The list is being read again.";

/** What setting the agent to `to` means, as the agent's form says it (005 FR-011). */
function means(name: string, to: AgentsMode): string {
  return to === "auto"
    ? `While agent approvals are Auto in the Tasks section, every message to ${name} will be sent without approval: no person sees it first. This holds from the next run. A proposal already waiting for ${name} can then only be approved.`
    : `Every message to ${name} will wait for a person's approval. This holds at once, also for runs in progress; a paused step that holds a message to ${name} proposed without approval cannot be continued until it is set back or the agent is removed.`;
}

function Confirmation({
  agent,
  to,
  live,
  onClose,
  onChanged,
}: {
  agent: Agent;
  to: AgentsMode;
  live: Agent;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [refusal, setRefusal] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // A second press arriving before the first one shows as sending still sends nothing.
  const inFlight = useRef(false);
  // The dialog can be closed before the answer comes; the answer then closes nothing.
  const shown = useRef(true);
  useEffect(() => {
    shown.current = true;
    return () => {
      shown.current = false;
    };
  }, []);
  // The list was read again while this was open and shows the agent at a later version:
  // what is asked here was asked of an earlier agent, and nothing is sent from here.
  const stale = live.version !== agent.version;

  async function confirm(event: MouseEvent) {
    // The second press of a double press on the card's switch can land on this button.
    // That was not a decision; a press of its own is.
    if (event.detail > 1 || inFlight.current || stale) return;
    inFlight.current = true;
    setSending(true);
    setRefusal(null);
    try {
      // The version the card showed when the switch was pressed.
      await changeAgent(agent.id, {
        version: agent.version ?? 0,
        approval: approvalOf(to),
      });
      onChanged();
      if (shown.current) onClose();
    } catch (error) {
      onChanged();
      if (isGone(error)) {
        if (shown.current) onClose();
        return;
      }
      setRefusal(
        error instanceof ApiError && error.code !== "unreachable"
          ? error.message
          : OUTCOME_NOT_KNOWN,
      );
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>
          Set {agent.name} to {to === "auto" ? "Auto" : "Manual"}?
        </AlertDialogTitle>
        <AlertDialogDescription>{means(agent.name, to)}</AlertDialogDescription>
      </AlertDialogHeader>
      {/* The service's sentence is text, whatever it holds: nothing is made of it. */}
      {refusal !== null && (
        <p
          role="alert"
          className="break-words rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {refusal}
        </p>
      )}
      {stale && refusal === null && (
        <p
          role="alert"
          className="break-words rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {agent.name} was changed elsewhere while this was open, so nothing is
          sent from here. Close this and use the switch again.
        </p>
      )}
      <AlertDialogFooter>
        <AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction
          disabled={sending || stale}
          aria-busy={sending}
          onClick={confirm}
        >
          {sending ? "Setting…" : `Set to ${to === "auto" ? "Auto" : "Manual"}`}
        </AlertDialogAction>
      </AlertDialogFooter>
    </>
  );
}

/**
 * The confirmation before a saved agent's own mode is switched from its card (feature 010,
 * FR-009). It is open while it is given a mode to switch to, and names the version of the
 * agent as the card showed it when the switch was pressed (`agent`); `live` is the agent as
 * the card shows it now. `onChanged` is told whenever the list should be read again.
 */
export function ModeDialog({
  agent,
  to,
  live,
  onClose,
  onChanged,
}: {
  agent: Agent;
  to: AgentsMode | null;
  live: Agent;
  onClose: () => void;
  onChanged: () => void;
}) {
  return (
    <AlertDialog
      open={to !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <AlertDialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
        {to !== null && (
          <Confirmation
            key={to}
            agent={agent}
            to={to}
            live={live}
            onClose={onClose}
            onChanged={onChanged}
          />
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
