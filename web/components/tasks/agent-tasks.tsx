import { useId } from "react";
import { Badge } from "@/components/ui/badge";
import { ago, exactTime } from "@/lib/ago";
import type { AgentTask } from "@/lib/tasks";

// What a delegate agent works on for a task is an "agent task" in every word the console
// writes, never a task (FR-001a). An agent's name and the message an agent task was started
// with are shown as written, in text nodes (FR-012).

/** Each status of an agent task in the console's words. */
export const AGENT_TASK_STATUS: Record<AgentTask["status"], string> = {
  working: "The agent is working",
  input_required: "The agent asks for more input",
  completed: "Completed",
  failed: "Failed",
  rejected: "Rejected by the agent",
  canceled: "Canceled",
  given_up: "Given up",
};

/** Whether the agent has not ended its work: the service's two pending statuses. */
export function pending(task: AgentTask): boolean {
  return task.status === "working" || task.status === "input_required";
}

/**
 * Whether a task still waits for something of this agent task: the agent has not ended it,
 * or it ended and no run has recorded its outcome in the conversation yet.
 */
export function awaited(task: AgentTask): boolean {
  return pending(task) || !task.outcome_delivered;
}

/**
 * Feature 008: what a working agent last reported, and how long before `now` the service
 * learned it. The text is the agent's own words, put in a text node and never rendered as
 * markup, with its line breaks, in a box that scrolls inside itself so that the longest text
 * neither hides the page nor is cut (FR-012); a word longer than the box, such as a long
 * address, is broken, so that it never widens the page. The box can be reached by keyboard to scroll
 * it, takes its direction from its own text, and is no live region: a text read again every
 * few seconds is not announced each time it changes (research R8). Until the page has a
 * time to compare with, the text is shown without its age.
 */
function Progress({
  agent,
  progress,
  now,
}: {
  agent: string;
  progress: NonNullable<AgentTask["progress"]>;
  now: number | null;
}) {
  return (
    <>
      <p className="text-xs text-muted-foreground">
        Last reported by the agent
        {now !== null && (
          <>
            {" "}
            <time
              dateTime={progress.learned_at}
              title={exactTime(progress.learned_at)}
            >
              {ago(progress.learned_at, now)}
            </time>
          </>
        )}
        :
      </p>
      <section
        aria-label={`Progress reported by ${agent}`}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a box that scrolls must be reachable by keyboard to be scrolled without a pointer.
        tabIndex={0}
        dir="auto"
        className="max-h-40 overflow-y-auto whitespace-pre-wrap wrap-anywhere rounded-md bg-muted/40 p-2 text-sm focus-visible:outline-2"
      >
        {progress.text}
      </section>
    </>
  );
}

/**
 * A task's agent tasks, oldest first as the service lists them: each with its agent, its
 * status in words, and the message it was started with, the pending ones marked (FR-009).
 * Under one that is working, what its agent last reported, with how long before `now` it
 * was learned; under no other (feature 008, FR-010). Nothing is shown for a task that handed
 * nothing to an agent.
 */
export function AgentTasks({
  tasks,
  now = null,
}: {
  tasks: readonly AgentTask[];
  /** The browser's time, from the view's state; null until the view has read it. */
  now?: number | null;
}) {
  const heading = useId();
  if (tasks.length === 0) {
    return null;
  }
  const waiting = tasks.filter(pending).length;
  return (
    <section aria-labelledby={heading} className="flex flex-col gap-2">
      <h2 id={heading} className="text-sm font-medium">
        Agent tasks
      </h2>
      {waiting > 0 && (
        <p className="text-sm text-muted-foreground">
          {waiting === 1
            ? "1 agent task is pending."
            : `${waiting} agent tasks are pending.`}
        </p>
      )}
      <ul aria-labelledby={heading} className="flex flex-col gap-2">
        {tasks.map((task) => (
          <li
            key={`${task.agent}:${task.task_id}`}
            data-pending={pending(task)}
            data-status={task.status}
            className="flex flex-col gap-1 rounded-lg border p-3"
          >
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium break-all">{task.agent}</span>
              {pending(task) && <Badge variant="secondary">Pending</Badge>}
              <span className="text-muted-foreground">
                {AGENT_TASK_STATUS[task.status]}
                {!pending(task) &&
                  !task.outcome_delivered &&
                  ". Its outcome is not in the conversation yet."}
              </span>
            </p>
            <p className="text-xs text-muted-foreground">Started with:</p>
            <p className="whitespace-pre-wrap break-words text-sm">
              {task.message}
            </p>
            {task.status === "working" && task.progress !== null && (
              <Progress agent={task.agent} progress={task.progress} now={now} />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
