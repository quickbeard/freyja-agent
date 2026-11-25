import type { CallerSummary } from "@/lib/tasks";

/**
 * Who started a task, when another agent did (feature 009, FR-023): an agent on this
 * machine, for one that called the service's own address without a token, or the agent by
 * the name the deployment gave it. A task a person started says nothing. The name is put in
 * a text node, never rendered as markup.
 */
export function startedBy(caller: CallerSummary | null): string | null {
  if (caller === null) {
    return null;
  }
  return caller.name === "local"
    ? "Started by an agent on this machine"
    : `Started by agent ${caller.name}`;
}

export function StartedBy({
  caller,
  className,
}: {
  caller: CallerSummary | null;
  className?: string;
}) {
  const words = startedBy(caller);
  return words === null ? null : <span className={className}>{words}</span>;
}
