// Feature 007 (T098): the pieces of a reply joined as the service's own library joins them,
// so that what a page shows while a run streams is what the conversation holds once it has
// ended. A port of `merge_content`, `merge_lists`, and `merge_dicts` of LangChain
// (`langchain_core.messages.base` and `langchain_core.utils._merge`), with one difference:
// where the library raises, for two values of different kinds under one key, the later
// value is kept, since a page that streams has nothing better to show.

import type { Message } from "./tasks";

type Block = Record<string, unknown>;

function isBlock(value: unknown): value is Block {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// The keys whose texts are kept once when the same text comes again.
const ONCE = new Set(["id", "output_version", "model_provider"]);
// The keys whose numbers are the later one, not the sum.
const LAST = new Set(["index", "created", "timestamp"]);

/** `merge_dicts`: what a block holds once a later piece of it has arrived. */
export function mergeDicts(left: Block, right: Block): Block {
  const merged: Block = { ...left };
  for (const [key, value] of Object.entries(right)) {
    const had = merged[key];
    if (
      !(key in merged) ||
      (value !== null && value !== undefined && had == null)
    ) {
      merged[key] = value;
    } else if (value === null || value === undefined) {
    } else if (typeof had === "string" && typeof value === "string") {
      if (
        (key === "index" && had.startsWith("lc_")) ||
        (ONCE.has(key) && had === value)
      ) {
        continue;
      }
      merged[key] = had + value;
    } else if (isBlock(had) && isBlock(value)) {
      merged[key] = mergeDicts(had, value);
    } else if (Array.isArray(had) && Array.isArray(value)) {
      merged[key] = mergeLists(had, value);
    } else if (had === value) {
    } else if (
      typeof had === "number" &&
      typeof value === "number" &&
      Number.isInteger(had) &&
      Number.isInteger(value)
    ) {
      merged[key] = LAST.has(key) ? value : had + value;
    } else {
      merged[key] = value;
    }
  }
  return merged;
}

/** Whether a block's place is one the library joins by: a number, or its own text form. */
function placed(block: unknown): block is Block {
  if (!isBlock(block) || !("index" in block)) {
    return false;
  }
  const index = block.index;
  return (
    (typeof index === "number" && Number.isInteger(index)) ||
    (typeof index === "string" && index.startsWith("lc_"))
  );
}

function noId(id: unknown): boolean {
  return id === undefined || id === null || id === "";
}

/** `merge_lists`: a block with the place and the id of one before it goes on that one. */
export function mergeLists(left: unknown[], right: unknown[]): unknown[] {
  const merged = [...left];
  for (const piece of right) {
    if (!placed(piece)) {
      merged.push(piece);
      continue;
    }
    const at = merged.findIndex(
      (block) =>
        isBlock(block) &&
        "index" in block &&
        block.index === piece.index &&
        (noId(block.id) || noId(piece.id) || block.id === piece.id),
    );
    if (at === -1) {
      merged.push(piece);
      continue;
    }
    const earlier = merged[at] as Block;
    const value = piece.value;
    let later: Block;
    if (earlier.type && piece.type === "non_standard" && isBlock(value)) {
      const rest = Object.fromEntries(
        Object.entries(value).filter(([key]) => key !== "type"),
      );
      later =
        earlier.type !== "non_standard"
          ? { extras: rest }
          : {
              value: rest,
              ...("index" in piece ? { index: piece.index } : {}),
            };
    } else {
      // The later piece's kind is not taken over: the block keeps the kind it began as.
      later = Object.fromEntries(
        Object.entries(piece).filter(([key]) => key !== "type"),
      );
    }
    merged[at] = mergeDicts(earlier, later);
  }
  return merged;
}

/** `merge_content`: a reply's content once a later piece of it has arrived. */
export function mergeContent(
  earlier: Message["content"],
  later: Message["content"],
): Message["content"] {
  if (typeof earlier === "string") {
    return typeof later === "string" ? earlier + later : [earlier, ...later];
  }
  if (Array.isArray(later)) {
    return mergeLists(earlier, later);
  }
  const last = earlier.at(-1);
  if (typeof last === "string") {
    return [...earlier.slice(0, -1), last + later];
  }
  if (later === "") {
    return earlier;
  }
  return earlier.length > 0 ? [...earlier, later] : earlier;
}
