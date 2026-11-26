"use client";

import { type MouseEvent, useId, useRef, useState } from "react";
import useSWR from "swr";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AGENTS_KEY,
  type AgentList,
  type AgentsMode,
  listAgents,
  setAgentsMode,
} from "@/lib/agents";
import { ApiError } from "@/lib/api";

const MODES: { value: AgentsMode; label: string }[] = [
  { value: "manual", label: "Manual" },
  { value: "auto", label: "Auto" },
];

/** What the mode means, said under the dropdown. */
const MEANS: Record<AgentsMode, string> = {
  manual: "Every message to an agent waits for your approval.",
  auto: "Agents set to Auto run without approval; the others ask.",
};

/**
 * What is said when the request got no answer. The mode may have changed and the answer
 * been lost, so this never says that the request failed.
 */
const OUTCOME_NOT_KNOWN =
  "The request got no answer, so it is not known whether the mode changed. It is being read again.";

/** How many agents in use are set to Auto: those the switch to Auto lets run unasked. */
export function autoAgents(list: AgentList): number {
  return list.agents.filter(
    (agent) => agent.approval === "none" && agent.state !== "not_in_use",
  ).length;
}

function lettingRun(count: number): string {
  if (count === 0) {
    return "No agent is set to Auto now, so nothing changes until one is.";
  }
  const agents = count === 1 ? "1 agent is" : `${count} agents are`;
  return `${agents} set to Auto now: every message to ${count === 1 ? "it" : "them"} will be sent with no person seeing it first. A proposal already waiting for ${count === 1 ? "it" : "one of them"} can then only be approved.`;
}

/**
 * The agents' approval mode, under Tasks in the sidebar (feature 010, FR-008). It is read
 * with the list of agents, under the key every page reads that list under, so it costs no
 * reading of its own. Manual applies at once. Auto lets every agent set to Auto run without
 * approval, so it is confirmed first, with how many agents that is. While the list cannot be
 * read the mode is not known, and cannot be changed.
 */
export function AgentsModeSelect() {
  const { data, error, mutate } = useSWR<AgentList, unknown>(
    AGENTS_KEY,
    listAgents,
    {
      // Like the Agents section: a reading that failed is not tried again unasked.
      shouldRetryOnError: false,
    },
  );
  const id = useId();
  const known = data !== undefined && error === undefined;
  const [asking, setAsking] = useState(false);
  const [sending, setSending] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  // A second press arriving before the first one shows as sending still sends nothing.
  const inFlight = useRef(false);

  async function send(mode: AgentsMode) {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setSaid(null);
    try {
      await setAgentsMode(mode);
      setAsking(false);
    } catch (refused) {
      setAsking(false);
      setSaid(
        refused instanceof ApiError && refused.code !== "unreachable"
          ? refused.message
          : OUTCOME_NOT_KNOWN,
      );
    } finally {
      inFlight.current = false;
      setSending(false);
      // What the service holds now, whatever the answer was.
      void mutate();
    }
  }

  function confirm(event: MouseEvent) {
    // The second press of a double press on the dropdown can land here. That was not a
    // decision; a press of its own is.
    if (event.detail > 1) return;
    void send("auto");
  }

  const mode = known ? data.mode : null;
  return (
    <div className="flex flex-col gap-1 px-2 pb-1 group-data-[collapsible=icon]:hidden">
      <span id={`${id}-label`} className="text-xs text-sidebar-foreground/70">
        Agent approvals
      </span>
      <Select
        items={MODES}
        value={mode}
        disabled={!known || sending}
        onValueChange={(value) => {
          if (value === "auto") {
            setSaid(null);
            setAsking(true);
          } else if (value === "manual") {
            void send("manual");
          }
        }}
      >
        <SelectTrigger
          size="sm"
          className="w-full"
          aria-labelledby={`${id}-label`}
          aria-describedby={`${id}-means`}
        >
          <SelectValue placeholder="Not known" />
        </SelectTrigger>
        <SelectContent>
          {MODES.map(({ value, label }) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p id={`${id}-means`} className="text-xs text-sidebar-foreground/70">
        {mode === null
          ? "Not known: the list of agents could not be read."
          : MEANS[mode]}
      </p>
      {/* The service's sentence is text, whatever it holds: nothing is made of it. */}
      {said !== null && (
        <p role="alert" className="break-words text-xs text-destructive">
          {said}
        </p>
      )}
      <AlertDialog
        open={asking}
        onOpenChange={(open) => {
          if (!open && !inFlight.current) setAsking(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Set agent approvals to Auto?</AlertDialogTitle>
            <AlertDialogDescription>
              Agents set to Auto will run without approval, from the next run.{" "}
              {data !== undefined ? lettingRun(autoAgents(data)) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={sending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={sending}
              aria-busy={sending}
              onClick={confirm}
            >
              {sending ? "Setting…" : "Set to Auto"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
