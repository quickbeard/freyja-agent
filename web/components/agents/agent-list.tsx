"use client";

import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AGENTS_KEY,
  type Agent,
  type AgentList as Agents,
  downAgents,
  listAgents,
} from "@/lib/agents";
import { ApiError } from "@/lib/api";
import { AgentCard } from "./agent-card";
import { AgentForm } from "./agent-form";
import { AgentsWarning } from "./agents-warning";
import { RemoveDialog } from "./remove-dialog";

function reason(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The agents could not be loaded.";
}

/** How many pending tasks a move or a removal gave up, as the service counted them. */
function givenUp(count: number): string {
  if (count === 0) {
    return "It had no pending task, so none was given up.";
  }
  if (count === 1) {
    return "1 pending task was given up: its thread will be told.";
  }
  return `${count} pending tasks were given up: their threads will be told.`;
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
  // The agent whose form is open, as it was when Edit was pressed: the form's fields are
  // filled from it once, and it stays after the form is closed, so the form can close.
  const [editing, setEditing] = useState<{
    agent: Agent;
    open: boolean;
  } | null>(null);

  // The agent whose removal is being asked about, as it was when Remove was pressed.
  const [removing, setRemoving] = useState<Agent | null>(null);
  // What the last move or removal came to: how many tasks it gave up, or that the agent
  // was gone already (FR-024). It stays until another change is started.
  const [outcome, setOutcome] = useState<string | null>(null);

  /**
   * The agent being changed or removed as the list has it now, for a confirmation: null
   * when the list no longer has it, and undefined while the list is not known (FR-011).
   */
  function live(agent: Agent): Agent | null | undefined {
    if (error !== undefined || data === undefined) {
      return undefined;
    }
    return data.agents.find(({ id }) => id === agent.id) ?? null;
  }

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
          <Button
            size="sm"
            onClick={() => {
              setOutcome(null);
              setAdding(true);
            }}
          >
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
                onEdit={(chosen) => {
                  setOutcome(null);
                  setEditing({ agent: chosen, open: true });
                }}
                onRemove={(chosen) => {
                  setOutcome(null);
                  setRemoving(chosen);
                }}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      {/* A status that appears together with its text, as the result of a check does. It
          is beside the list and not inside it, so it stays when the list cannot be read. */}
      {outcome !== null && (
        <output className="mb-4 block break-words rounded-lg border p-3 text-sm">
          {outcome}
        </output>
      )}
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
      {/* A form of its own for changing a saved agent. After a change, after a refusal that
          says the agent is no longer as the form has it, and after a request with no
          answer, the list is read again. */}
      {editing && (
        <AgentForm
          agent={editing.agent}
          live={live(editing.agent)}
          open={editing.open}
          onOpenChange={(open) => setEditing({ ...editing, open })}
          onSaved={() => mutate()}
          onUnknown={() => mutate()}
          onStale={() => mutate()}
          onMoved={(moved, count) =>
            setOutcome(`${moved.name} was moved. ${givenUp(count)}`)
          }
        />
      )}
      {/* The removal's confirmation follows the list's copy of its agent, and closes when
          the list no longer has it. After a removal, after a refusal that says the agent is
          no longer as the dialog has it, and after a request with no answer, the list is
          read again. */}
      <RemoveDialog
        agent={removing}
        live={removing === null ? null : live(removing)}
        onClose={() => setRemoving(null)}
        onRemoved={(removed, count) => {
          setOutcome(`${removed.name} was removed. ${givenUp(count)}`);
          mutate();
        }}
        onStale={() => mutate()}
        onUnknown={() => mutate()}
        onVanished={(vanished, unanswered) =>
          setOutcome(
            unanswered
              ? `${vanished.name} is no longer in the list. The request to remove it got no answer, so how many of its pending tasks were given up is not known here.`
              : `${vanished.name} is no longer in the list: it was removed elsewhere.`,
          )
        }
      />
    </>
  );
}
