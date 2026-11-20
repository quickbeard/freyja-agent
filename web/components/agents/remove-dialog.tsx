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
import { type Agent, agentInWords, removeAgent } from "@/lib/agents";
import { ApiError, isGone } from "@/lib/api";

// The refusals that say the agent is not what this dialog has: the list is read again.
const NOT_AS_THE_DIALOG_HAS_IT = ["agent_changed", "agent_read_only"];

/**
 * What is said when the request got no answer. The agent may have been removed and the
 * answer lost, so this never says that the request failed (FR-004).
 */
const OUTCOME_NOT_KNOWN =
  "The request got no answer, so it is not known whether the agent was removed. The list is being read again: look for the agent there before removing it again.";

/**
 * How many of the agent's tasks are pending and what becomes of them (FR-011, FR-024): as
 * the list has it now, or as the dialog opened while the list is not known.
 */
function pending(name: string, count: number, fromList: boolean): string {
  if (!fromList) {
    const had =
      count === 0
        ? "no pending task"
        : count === 1
          ? "1 pending task"
          : `${count} pending tasks`;
    return `${name} had ${had} when this was opened. Every task that is pending when the agent is removed will be given up.`;
  }
  if (count === 0) {
    return `${name} has no pending task now, so none will be given up.`;
  }
  if (count === 1) {
    return `${name} has 1 pending task now, and it will be given up: Freyja stops waiting for it and tells its thread. No cancel is sent.`;
  }
  return `${name} has ${count} pending tasks now, and they will be given up: Freyja stops waiting for them and tells their threads. No cancel is sent.`;
}

function Confirmation({
  agent,
  live,
  onClose,
  onRemoved,
  onStale,
  onUnknown,
  onVanished,
}: {
  agent: Agent;
  live?: Agent | null;
  onClose: () => void;
  onRemoved: (agent: Agent, givenUp: number) => void;
  onStale?: () => void;
  onUnknown?: () => void;
  onVanished?: (agent: Agent, unanswered: boolean) => void;
}) {
  const [refusal, setRefusal] = useState<string | null>(null);
  // The refusal says that the agent is not what this dialog has. Once the list shows as
  // much, the dialog says so in its own words, with what to do, and not twice.
  const [refusedAsStale, setRefusedAsStale] = useState(false);
  const [sending, setSending] = useState(false);
  // A second press arriving before the first one shows as sending still sends nothing.
  const inFlight = useRef(false);
  // The dialog can be closed, and opened for another agent, before the answer comes. The
  // answer then belongs to a dialog that is gone: it closes nothing that is open now.
  const shown = useRef(true);
  useEffect(() => {
    shown.current = true;
    return () => {
      shown.current = false;
    };
  }, []);

  // The list was read again while this was open and shows the agent at a later version:
  // what is asked here was asked of an earlier agent, and nothing is removed from here. It
  // stays so until the dialog is closed: a later reading of the list that fails makes the
  // list unknown, not the agent as it was (FR-012, research R14).
  const [changed, setChanged] = useState(false);
  const later = live != null && live.version !== agent.version;
  if (later && !changed) {
    // Kept during render, so that no render offers to remove what it no longer shows.
    setChanged(true);
  }
  const stale = later || changed;

  // The list no longer has the agent: there is nothing left to ask about. Whoever holds
  // the list is told, and whether a removal asked from here got no answer before: that
  // one may be why the agent is gone.
  const gone = live === null;
  const told = useRef(false);
  const unanswered = useRef(false);
  useEffect(() => {
    if (gone && !told.current) {
      told.current = true;
      onVanished?.(agent, unanswered.current);
      onClose();
    }
  }, [gone, agent, onVanished, onClose]);

  async function confirm(event: MouseEvent) {
    // The second press of a double press on the card's Remove can land on this button.
    // That was not a decision; a press of its own is.
    if (event.detail > 1 || inFlight.current || stale) return;
    inFlight.current = true;
    setSending(true);
    setRefusal(null);
    try {
      // The version the dialog opened with, whatever the list shows now.
      const givenUp = await removeAgent(agent.id, agent.version ?? 0);
      onRemoved(agent, givenUp);
      if (shown.current) {
        onClose();
      }
    } catch (error) {
      const answered =
        error instanceof ApiError && error.code !== "unreachable";
      const notAsItWas =
        answered &&
        (isGone(error) || NOT_AS_THE_DIALOG_HAS_IT.includes(error.code));
      if (!answered) {
        // No answer came back. The list is what says whether the agent is still there.
        unanswered.current = true;
        onUnknown?.();
      } else if (notAsItWas) {
        // The service says the agent is not what this dialog has: the list says what it is.
        onStale?.();
      }
      if (shown.current) {
        // The service's own sentence, or that the outcome is not known.
        setRefusal(answered ? error.message : OUTCOME_NOT_KNOWN);
        setRefusedAsStale(notAsItWas);
      }
    } finally {
      inFlight.current = false;
      if (shown.current) {
        setSending(false);
      }
    }
  }

  // The agent as the list has it now, or as the dialog opened while the list is not known.
  const listed = live ?? agent;

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>Remove {agent.name}?</AlertDialogTitle>
        <AlertDialogDescription>
          The removal holds at once, also for runs in progress: nothing more is
          sent to {agent.name}, and the next run has neither of its tools. This
          cannot be undone: an agent added again under this name is another
          agent.
        </AlertDialogDescription>
      </AlertDialogHeader>
      <p className="break-words text-sm">
        {pending(agent.name, listed.pending_tasks, live != null)}
      </p>
      {/* The agent as the list has it at this moment, as every confirmation shows it (FR-011). */}
      <p className="break-words text-sm text-muted-foreground">
        {live != null
          ? "As the list has it now:"
          : "The list cannot be read now. As it was when this opened:"}{" "}
        {agentInWords(listed)}
      </p>
      {/* The service's sentence is text, whatever it holds: nothing is made of it. */}
      {refusal && !(stale && refusedAsStale) && (
        <p
          role="alert"
          className="break-words rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {refusal}
        </p>
      )}
      {stale && (
        <p
          role="alert"
          className="break-words rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {agent.name} was changed elsewhere while this was open, so it is not
          removed from here. Close this and press Remove again to remove the
          agent as it is now.
        </p>
      )}
      <AlertDialogFooter>
        <AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction
          variant="destructive"
          disabled={sending || stale}
          aria-busy={sending}
          onClick={confirm}
        >
          {sending ? "Removing…" : "Remove"}
        </AlertDialogAction>
      </AlertDialogFooter>
    </>
  );
}

/**
 * The confirmation before a saved agent is removed (FR-010, FR-011). It is open while it
 * is given an agent, which is the agent as it was when Remove was pressed: the removal
 * names that version. `live` is the same agent as the list has it now, which is what the
 * dialog shows: null when the list no longer has it, and the dialog then closes; undefined
 * while the list is not known.
 */
export function RemoveDialog({
  agent,
  live,
  onClose,
  onRemoved,
  onStale,
  onUnknown,
  onVanished,
}: {
  agent: Agent | null;
  live?: Agent | null;
  onClose: () => void;
  /** Told that the agent was removed, and how many of its pending tasks were given up. */
  onRemoved: (agent: Agent, givenUp: number) => void;
  /** Told when the service says the agent is no longer as the dialog has it. */
  onStale?: () => void;
  /** Told when the request got no answer, so that whoever holds the list reads it again. */
  onUnknown?: () => void;
  /**
   * Told when the dialog closed because the list no longer has the agent, and whether a
   * removal asked from this dialog got no answer before that.
   */
  onVanished?: (agent: Agent, unanswered: boolean) => void;
}) {
  return (
    <AlertDialog
      open={agent !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {/* Taller than a short, narrow window once it shows a long reason: it scrolls in itself,
          so its buttons can always be reached. */}
      <AlertDialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
        {agent !== null && (
          <Confirmation
            key={agent.id}
            agent={agent}
            live={live}
            onClose={onClose}
            onRemoved={onRemoved}
            onStale={onStale}
            onUnknown={onUnknown}
            onVanished={onVanished}
          />
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
