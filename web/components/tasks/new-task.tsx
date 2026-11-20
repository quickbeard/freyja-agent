"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { begin } from "@/lib/task-runs";
import { newThreadId, startRun } from "@/lib/tasks";
import { SendBox } from "./send-box";

/**
 * New task: one text box, empty each time it opens. Sending starts the run under an id
 * chosen here, waits for the service's answer, and only then opens the task's page, which
 * shows the run as it arrives; a refusal stays in the box with its text (FR-007, FR-008).
 */
export function NewTask() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  // Every opening is a new box and a new task: nothing is left from an earlier one.
  const [session, setSession] = useState(0);
  const [threadId, setThreadId] = useState("");

  function change(next: boolean) {
    if (next) {
      setThreadId(newThreadId());
    }
    setSession((current) => current + 1);
    setOpen(next);
  }

  async function send(text: string) {
    const answer = await startRun(threadId, text);
    begin(threadId, text, answer);
    setOpen(false);
    router.push(`/tasks/${threadId}`);
  }

  return (
    <>
      <Button onClick={() => change(true)}>New task</Button>
      <Dialog open={open} onOpenChange={change}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New task</DialogTitle>
            <DialogDescription>
              Say what Freyja should do. An action with effects outside Freyja's
              workspace waits for a person's approval.
            </DialogDescription>
          </DialogHeader>
          <SendBox key={session} label="What should Freyja do?" send={send} />
        </DialogContent>
      </Dialog>
    </>
  );
}
