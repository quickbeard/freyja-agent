import type { Metadata } from "next";
import { NewTask } from "@/components/tasks/new-task";

export const metadata: Metadata = { title: "Tasks" };

export default function TasksPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-2xl font-semibold">Tasks</h1>
          <p className="text-sm text-muted-foreground">
            The work handed to Freyja. An action with effects outside Freyja's
            workspace waits for a person's approval.
          </p>
        </div>
        <NewTask />
      </div>
      {/* FR-026a: said above the list, whenever it is shown. */}
      <p
        role="note"
        className="rounded-lg border bg-muted/40 p-3 text-sm text-muted-foreground"
      >
        The console has no users: everyone who can open it sees every task and
        can decide every proposal, and nothing records who decided.
      </p>
    </div>
  );
}
