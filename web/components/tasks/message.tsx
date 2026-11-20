import type { Message, ToolCall } from "@/lib/tasks";

// Everything here is shown as text: what a model, a tool, an agent, or a person wrote is put
// in text nodes and nothing is rendered as markup, loaded, or followed as a link (FR-012).

/** The text of a message's content: its text blocks, and any other block as JSON. */
export function contentText(content: Message["content"]): string {
  if (typeof content === "string") {
    return content;
  }
  return content
    .map((block) => {
      if (
        typeof block === "object" &&
        block !== null &&
        (block as { type?: unknown }).type === "text" &&
        typeof (block as { text?: unknown }).text === "string"
      ) {
        return (block as { text: string }).text;
      }
      return JSON.stringify(block);
    })
    .join("\n");
}

/** Arguments in the structured form a proposal shows them in. */
export function argumentsText(args: Record<string, unknown>): string {
  return JSON.stringify(args, null, 2);
}

function who(message: Message): string {
  switch (message.type) {
    case "human":
      return "You";
    case "ai":
      return "Freyja";
    case "tool":
      return message.name ? `Result of ${message.name}` : "Result of an action";
    default:
      return "System";
  }
}

function Call({ call }: { call: ToolCall }) {
  return (
    <div className="flex flex-col gap-1 rounded-md border bg-muted/40 p-2">
      <p className="text-sm">
        Freyja asked to run <span className="font-mono">{call.name}</span>
      </p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs">
        {argumentsText(call.args)}
      </pre>
    </div>
  );
}

/** One message of a task's conversation. */
export function MessageView({ message }: { message: Message }) {
  const text = contentText(message.content);
  const calls = message.tool_calls ?? [];
  return (
    <article
      aria-label={who(message)}
      data-type={message.type}
      className="flex flex-col gap-2 rounded-lg border p-3"
    >
      <h2 className="text-xs font-medium text-muted-foreground">
        {who(message)}
      </h2>
      {text !== "" && (
        <p
          className={
            message.type === "tool"
              ? "whitespace-pre-wrap break-words font-mono text-xs"
              : "whitespace-pre-wrap break-words text-sm"
          }
        >
          {text}
        </p>
      )}
      {calls.map((call, index) => (
        <Call key={call.id ?? index} call={call} />
      ))}
    </article>
  );
}
