"use client";

import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AGENTS_KEY, type AgentList as Agents, listAgents } from "@/lib/agents";
import { ApiError } from "@/lib/api";
import { AgentCard } from "./agent-card";

function reason(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The agents could not be loaded.";
}

/** The Agents section: every agent as a card, read from the service in the browser. */
export function AgentList() {
  const { data, error, isLoading, isValidating, mutate } = useSWR<
    Agents,
    unknown
  >(AGENTS_KEY, listAgents, {
    // A failure is shown, with a way to try again, and not retried behind the operator's back.
    shouldRetryOnError: false,
  });

  if (error !== undefined) {
    // Whatever was loaded before is not shown: it may no longer be true.
    return (
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
  }

  if (isLoading || data === undefined) {
    return (
      <output className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <span className="sr-only">Loading agents</span>
        <Skeleton className="h-56" />
        <Skeleton className="h-56" />
      </output>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={isValidating}
          onClick={() => mutate()}
        >
          Refresh
        </Button>
      </div>
      {data.agents.length === 0 ? (
        <p className="rounded-lg border p-4 text-sm text-muted-foreground">
          No agent is configured.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {data.agents.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      )}
    </div>
  );
}
