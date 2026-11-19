"use client";

import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AGENTS_KEY,
  type AgentList as Agents,
  downAgents,
  listAgents,
} from "@/lib/agents";
import { ApiError } from "@/lib/api";
import { AgentCard } from "./agent-card";
import { AgentForm } from "./agent-form";
import { AgentsWarning } from "./agents-warning";

function reason(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The agents could not be loaded.";
}

/**
 * The Agents section: every agent as a card, read from the service in the browser. The
 * sidebar reads the same list under the same key, so the cards, the warning above them, and
 * the number on the sidebar are one reading (FR-001a).
 */
export function AgentList() {
  const { data, error, isLoading, isValidating, mutate } = useSWR<
    Agents,
    unknown
  >(AGENTS_KEY, listAgents, {
    // A failure is shown, with a way to try again, and not retried behind the operator's back.
    shouldRetryOnError: false,
  });
  const [adding, setAdding] = useState(false);

  let list: React.ReactNode;
  if (error !== undefined) {
    // Whatever was loaded before is not shown: it may no longer be true.
    list = (
      <div
        role="alert"
        className="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 p-4"
      >
        <p className="text-sm">{reason(error)}</p>
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
  } else if (isLoading || data === undefined) {
    list = (
      <output className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <span className="sr-only">Loading agents</span>
        <Skeleton className="h-56" />
        <Skeleton className="h-56" />
      </output>
    );
  } else {
    list = (
      <div className="flex flex-col gap-4">
        {/* Only of a list that was read: while the service cannot be reached, no agent is
            warned about as if what was read before were so now. */}
        <AgentsWarning agents={downAgents(data)} />
        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={isValidating}
            onClick={() => mutate()}
          >
            Refresh
          </Button>
          <Button size="sm" onClick={() => setAdding(true)}>
            Add
          </Button>
        </div>
        {data.agents.length === 0 ? (
          <p className="rounded-lg border p-4 text-sm text-muted-foreground">
            No agent is configured.
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {/* After a check the list is read again: the card's state, the warning above,
                and the sidebar's number all follow from that one reading. */}
            {data.agents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                onChecked={() => mutate()}
                onGone={() => mutate()}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      {list}
      {/* The form is beside the list and not inside it: when the list cannot be read after
          a request that got no answer, the form is still there to say that the outcome is
          not known, with what was typed. After an add, and after a request with no answer,
          the list is read again: the service says what is so. */}
      <AgentForm
        open={adding}
        onOpenChange={setAdding}
        onSaved={() => mutate()}
        onUnknown={() => mutate()}
      />
    </>
  );
}
