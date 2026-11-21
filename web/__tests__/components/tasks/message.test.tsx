import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ai, human } from "@/__tests__/tasks-testing";
import { contentText, MessageView } from "@/components/tasks/message";
import type { Message } from "@/lib/tasks";

const MARKUP =
  '<b>bold</b> <a href="http://example.test/x">a link</a> <img src="http://example.test/i.png" onerror="alert(1)">';

describe("a message", () => {
  it("shows a person's message as theirs", () => {
    render(<MessageView message={human("Summarize the week.")} />);
    const shown = screen.getByRole("article", { name: "You" });
    expect(shown.textContent).toContain("Summarize the week.");
  });

  it("shows Freyja's reply and each action it asked for, with the arguments", () => {
    const reply: Message = {
      ...ai("I will tell #ops."),
      tool_calls: [
        {
          id: "n1",
          name: "send_notification",
          args: { channel: "#ops", message: "Ready." },
        },
      ],
    };
    render(<MessageView message={reply} />);
    const shown = screen.getByRole("article", { name: "Freyja" });
    expect(shown.textContent).toContain("I will tell #ops.");
    expect(shown.textContent).toContain(
      "Freyja asked to run send_notification",
    );
    expect(shown.textContent).toContain('"channel": "#ops"');
    expect(shown.textContent).toContain('"message": "Ready."');
  });

  it("shows an action's result with the action's name", () => {
    const result: Message = {
      id: "r1",
      type: "tool",
      content: "Notification to #ops recorded.",
      name: "send_notification",
      tool_call_id: "n1",
    };
    render(<MessageView message={result} />);
    const shown = screen.getByRole("article", {
      name: "Result of send_notification",
    });
    expect(shown.textContent).toContain("Notification to #ops recorded.");
  });

  it("shows markup and links as characters, from whoever wrote them", () => {
    const { container } = render(
      <>
        <MessageView message={human(MARKUP, "h")} />
        <MessageView
          message={{
            ...ai(MARKUP, "a"),
            tool_calls: [{ id: "c", name: MARKUP, args: { html: MARKUP } }],
          }}
        />
        <MessageView
          message={{ id: "t", type: "tool", content: MARKUP, name: MARKUP }}
        />
      </>,
    );
    expect(container.querySelector("b, a, img, script")).toBeNull();
    expect(container.textContent).toContain("<b>bold</b>");
    expect(container.textContent).toContain('<a href="http://example.test/x">');
  });

  it("reads the text of content blocks, and any other block as JSON", () => {
    expect(
      contentText([
        { type: "text", text: "First." },
        { type: "image", url: "x" },
      ]),
    ).toBe('First.\n{"type":"image","url":"x"}');
  });
});

// US2/AC3: after an edit the conversation shows both what was proposed and that a person
// changed it. The first is Freyja's own message, kept as it was; the second is in the
// action's result, in words the service's approval step writes
// (tests/test_unattended_runs.py holds the service to them). Both are shown as text.
describe("an action a person edited before it ran", () => {
  const NOTICE =
    'Note: a human reviewer replaced this tool call before it ran. The call recorded in your message is the one you produced; the call that ran was send_notification with arguments {"channel": "#ops-staging", "message": "Ready."}.';

  it("is shown as it was proposed, and its result says that a person changed it and to what", () => {
    const proposed: Message = {
      ...ai(""),
      tool_calls: [
        {
          id: "n1",
          name: "send_notification",
          args: { channel: "#ops", message: "Ready." },
        },
      ],
    };
    const result: Message = {
      id: "r1",
      type: "tool",
      content: `${NOTICE}\n\nTool response:\nNotification to #ops-staging recorded.`,
      name: "send_notification",
      tool_call_id: "n1",
    };
    render(
      <>
        <MessageView message={proposed} />
        <MessageView message={result} />
      </>,
    );

    const asked = screen.getByRole("article", { name: "Freyja" });
    expect(asked.textContent).toContain('"channel": "#ops"');
    expect(asked.textContent).not.toContain("#ops-staging");
    const ran = screen.getByRole("article", {
      name: "Result of send_notification",
    });
    expect(ran.textContent).toContain(
      "a human reviewer replaced this tool call before it ran",
    );
    expect(ran.textContent).toContain('"channel": "#ops-staging"');
    expect(ran.textContent).toContain("Notification to #ops-staging recorded.");
  });
});
