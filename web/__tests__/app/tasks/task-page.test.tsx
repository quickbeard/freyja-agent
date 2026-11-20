import { act, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { SWRConfig } from "swr";
import { describe, expect, it } from "vitest";
import { human, serve, state, summary } from "@/__tests__/tasks-testing";
import TaskPage from "@/app/tasks/[id]/page";

describe("a task's page", () => {
  it("shows the task its address names", async () => {
    const asked = serve((_method, path) =>
      path.endsWith("/state")
        ? { json: state([human("Summarize the week.")]) }
        : { json: summary() },
    );
    // The page hands React a promise of the id; React waits for it inside act.
    await act(async () => {
      render(
        <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
          <Suspense>
            <TaskPage
              params={Promise.resolve({ id: "5f1c0a9e" })}
              searchParams={Promise.resolve({})}
            />
          </Suspense>
        </SWRConfig>,
      );
    });

    expect(
      await screen.findByRole("heading", { level: 1, name: "Task" }),
    ).toBeTruthy();
    await waitFor(() =>
      expect(asked.map(({ path }) => path).sort()).toEqual([
        "/threads/5f1c0a9e",
        "/threads/5f1c0a9e/state",
      ]),
    );
    expect(await screen.findByText("Summarize the week.")).toBeTruthy();
  });
});
