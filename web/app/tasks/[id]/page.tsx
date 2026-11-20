import type { Metadata } from "next";
import { Suspense } from "react";
import { TaskView } from "@/components/tasks/task-view";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Task" };

// The task's id is the address's, known only when the page is asked for: with Cache
// Components it is read inside <Suspense> (Next.js docs, dynamic-routes.md).
export default function TaskPage({ params }: PageProps<"/tasks/[id]">) {
  return (
    <Suspense
      fallback={
        <output className="flex flex-col gap-3">
          <span className="sr-only">Loading the task</span>
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-24" />
        </output>
      }
    >
      {params.then(({ id }) => (
        <TaskView threadId={id} />
      ))}
    </Suspense>
  );
}
