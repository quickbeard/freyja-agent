import type { CallerSummary } from "@/lib/tasks";

/**
 * Who started a task, when another agent did (feature 009, FR-023): an agent on this
 * machine, for one that called the service's own address without a token, or the agent by
 * the name the deployment gave it, and whether the deployment still names it. A task a
 * person started says nothing. The name is put in a text node, never rendered as markup.
 */
export function startedBy(caller: CallerSummary | null): string | null {
  if (caller === null) {
    return null;
  }
  if (caller.name === "local") {
    return "Started by an agent on this machine";
  }
  return caller.configured
    ? `Started by agent ${caller.name}`
    : `Started by agent ${caller.name}, which is no longer configured`;
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
