import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AGENTS,
  approvals,
  body,
  chooseApprovals,
  END,
  refusal,
  START,
  serve,
} from "@/__tests__/tasks-testing";
import { NewTask } from "@/components/tasks/new-task";
import type { AgentsMode } from "@/lib/agents";
import { clear, get } from "@/lib/task-runs";

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: navigation.push }),
}));

const box = () => screen.getByLabelText("What should Freyja do?");
const send = () => screen.getByRole("button", { name: "Send" });

function open() {
  fireEvent.click(screen.getByRole("button", { name: "New task" }));
}

function type(text: string) {
  fireEvent.change(box(), { target: { value: text } });
}

let started: string[] = [];

beforeEach(() => {
  navigation.push.mockReset();
  started = [];
});

afterEach(() => {
  for (const id of started) {
    clear(id);
  }
});

/** The id of the task the page went to. */
function wentTo(): string {
  const [address] = navigation.push.mock.calls[0];
  const id = String(address).replace(/^\/tasks\//, "");
  started.push(id);
  return id;
}

describe("New task", () => {
  it("opens one empty text box each time", () => {
    render(<NewTask />);
    open();
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    type("Something half written");
    fireEvent.keyDown(box(), { key: "Escape" });
    open();
    expect((box() as HTMLTextAreaElement).value).toBe("");
  });

  it("does not send a text that is empty", () => {
    const asked = serve(() => ({ json: {} }));
    render(<NewTask />);
    open();
    expect((send() as HTMLButtonElement).disabled).toBe(true);
    type("   ");
    expect((send() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(box().closest("form") as HTMLFormElement);
    expect(asked).toEqual([]);
  });

  it("starts one run for two presses, then opens the task's page", async () => {
    const asked = serve(() => ({ stream: body(START, END) }));
    render(<NewTask />);
    open();
    type("Summarize the week.");
    const button = send();
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(navigation.push).toHaveBeenCalledTimes(1));

    const id = wentTo();
    expect(id).toMatch(/^[0-9a-f]{32}$/);
    expect(asked).toEqual([
      {
        method: "POST",
        path: `/threads/${id}/runs/unattended`,
        body: { message: "Summarize the week." },
      },
    ]);
    // The task's page finds the run as it arrives.
    expect(get(id)?.message).toBe("Summarize the week.");
  });

  it("sends once when the form is sent twice before the page has drawn again", async () => {
    const asked = serve(() => ({ stream: body(START, END) }));
    render(<NewTask />);
    open();
    type("Summarize the week.");
    const form = box().closest("form") as HTMLFormElement;
    // Two submits within one event: no render between them disables the button.
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    await waitFor(() => expect(navigation.push).toHaveBeenCalledTimes(1));
    wentTo();
    expect(asked).toHaveLength(1);
  });

  it("gives every task an id of its own", async () => {
    serve(() => ({ stream: body(START, END) }));
    render(<NewTask />);
    for (const text of ["One.", "Two."]) {
      open();
      type(text);
      fireEvent.click(send());
      await waitFor(() =>
        expect(navigation.push).toHaveBeenCalledTimes(started.length + 1),
      );
      started.push(
        String(navigation.push.mock.calls.at(-1)?.[0]).replace(
          /^\/tasks\//,
          "",
        ),
      );
    }
    expect(new Set(started).size).toBe(2);
  });

  it("keeps the text and says the service's reason when it refuses", async () => {
    serve(() =>
      refusal(409, "run_active", "A run is already active on this thread."),
    );
    render(<NewTask />);
    open();
    type("Summarize the week.");
    fireEvent.click(send());

    expect((await screen.findByRole("alert")).textContent).toBe(
      "A run is already active on this thread.",
    );
    expect((box() as HTMLTextAreaElement).value).toBe("Summarize the week.");
    expect(navigation.push).not.toHaveBeenCalled();
  });

  it("says which value was refused", async () => {
    serve(() => ({
      status: 422,
      json: {
        detail: [
          {
            loc: ["body", "message"],
            msg: "String should have at most 32000 characters",
          },
        ],
      },
    }));
    render(<NewTask />);
    open();
    type("x".repeat(40));
    fireEvent.click(send());
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The message was refused: String should have at most 32000 characters",
    );
  });

  it("says the service cannot be reached, keeps the text, and can send again", async () => {
    let down = true;
    serve(() => (down ? { fails: "refused" } : { stream: body(START, END) }));
    render(<NewTask />);
    open();
    type("Summarize the week.");
    fireEvent.click(send());
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /cannot be reached/,
    );
    expect((box() as HTMLTextAreaElement).value).toBe("Summarize the week.");

    down = false;
    fireEvent.click(send());
    await waitFor(() => expect(navigation.push).toHaveBeenCalledTimes(1));
    wentTo();
  });
});

// Feature 010, FR-008 as the owner placed it on 2026-11-26: the agents' approval mode stands
// beside the button that sends.
describe("the agents' approval mode beside New task's Send", () => {
  it("is confirmed before Auto, and the task being written stays as it was", async () => {
    let mode: AgentsMode = "manual";
    const asked = serve(
      (method, path) => {
        if (method === "PUT" && path === "/agents/mode") {
          mode = "auto";
          return { json: { mode } };
        }
        return { stream: body(START, END) };
      },
      () => ({ ...AGENTS, mode }),
    );
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <NewTask />
      </SWRConfig>,
    );
    open();
    type("Summarize the week.");
    await waitFor(() => expect(approvals().textContent).toContain("Manual"));
    expect(send().parentElement?.contains(approvals())).toBe(true);

    await chooseApprovals("Auto");
    const confirm = await screen.findByRole("alertdialog", {
      name: "Set agent approvals to Auto?",
    });
    // Beside one task's Send button, it says that the mode is every task's.
    expect(confirm.textContent).toContain("In every task");
    fireEvent.click(
      within(confirm).getByRole("button", { name: "Set to Auto" }),
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await waitFor(() => expect(approvals().textContent).toContain("Auto"));

    // The task is still being written: its dialog is open with its text, and nothing started.
    expect(screen.getByRole("dialog", { name: "New task" })).toBeTruthy();
    expect((box() as HTMLTextAreaElement).value).toBe("Summarize the week.");
    expect(asked).toEqual([
      { method: "PUT", path: "/agents/mode", body: { mode: "auto" } },
    ]);
    expect(navigation.push).not.toHaveBeenCalled();
  });
});
