import type { Agent } from "@/lib/agents";

/**
 * The warning above the cards: every agent that cannot be handed work now, by name, and in
 * words that they cannot be used (FR-001a). It is handed the agents of one reading of the
 * list and shows nothing when there is none.
 */
export function AgentsWarning({ agents }: { agents: Agent[] }) {
  if (agents.length === 0) {
    return null;
  }
  const one = agents.length === 1;
  return (
    <p
      role="note"
      aria-label="Agents that cannot be used"
      className="rounded-lg border border-destructive/40 p-4 text-sm"
    >
      <span className="font-medium text-destructive">Warning: </span>
      {one
        ? "1 agent cannot be used: "
        : `${agents.length} agents cannot be used: `}
      {/* Names are text, like everything the service sends. */}
      <span className="break-words font-medium">
        {agents.map((agent) => agent.name).join(", ")}
      </span>
      {one ? ". Its card says why." : ". Each one's card says why."}
    </p>
  );
}
