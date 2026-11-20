import type { AgentCheck } from "@/lib/agents";

const WHEN = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** What a check found, in a few words. */
export function outcome(check: AgentCheck): string {
  return check.usable
    ? "The check found that this agent can be used"
    : "The check found that this agent cannot be used";
}

/**
 * One check's result: what it found, when the service said so, and for an agent that cannot
 * be used the service's reason. The time is the service's own, only put in the reader's words
 * here. Every sentence of the service is a text node: nothing is made of it.
 */
export function CheckResult({ check }: { check: AgentCheck }) {
  return (
    <>
      <p className={check.usable ? undefined : "text-destructive"}>
        {outcome(check)}
        {" · "}
        <time dateTime={check.checked_at} className="text-muted-foreground">
          {WHEN.format(new Date(check.checked_at))}
        </time>
      </p>
      {!check.usable && check.message && (
        <p className="break-words text-muted-foreground">{check.message}</p>
      )}
      {check.card_changed && (
        <p>
          The agent's card changed. Its tools are described from the new details
          from the next run.
        </p>
      )}
      {!check.recorded && (
        <p className="text-muted-foreground">
          This result was not recorded: the agent was moved or removed while it
          was checked.
        </p>
      )}
    </>
  );
}
