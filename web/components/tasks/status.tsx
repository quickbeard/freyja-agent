import { Badge } from "@/components/ui/badge";
import type { ThreadStatus } from "@/lib/tasks";

/** Each status in the console's words; a status is never said by color alone (FR-012a). */
export const STATUS_LABEL: Record<ThreadStatus, string> = {
  running: "Running",
  needs_decision: "Needs your decision",
  failed: "Failed",
  stopped: "Stopped",
  waiting_for_agent: "Waiting for an agent",
  done: "Done",
};

const VARIANT: Record<
  ThreadStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  running: "secondary",
  needs_decision: "default",
  failed: "destructive",
  stopped: "outline",
  waiting_for_agent: "secondary",
  done: "outline",
};

export function TaskStatus({ status }: { status: ThreadStatus }) {
  return (
    <Badge variant={VARIANT[status]} data-status={status}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}
