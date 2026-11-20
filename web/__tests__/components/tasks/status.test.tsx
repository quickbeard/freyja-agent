import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TaskStatus } from "@/components/tasks/status";
import type { ThreadStatus } from "@/lib/tasks";

const WORDS: [ThreadStatus, string][] = [
  ["running", "Running"],
  ["needs_decision", "Needs your decision"],
  ["failed", "Failed"],
  ["stopped", "Stopped"],
  ["waiting_for_agent", "Waiting for an agent"],
  ["done", "Done"],
];

describe("a task's status", () => {
  it.each(WORDS)("says %s in words", (status, words) => {
    render(<TaskStatus status={status} />);
    expect(screen.getByText(words)).toBeTruthy();
  });
});
