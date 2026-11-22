// Feature 007 (T099): JSON read with its numbers as they are written.
//
// A browser holds a number as a double. A whole number beyond 2^53, such as a 64-bit id,
// becomes the nearest one it can hold, so an argument the service sent would be shown, and
// sent back, as another number than was proposed. A browser that gives a number's own text
// when it reads JSON (`JSON.parse` with a reviver's `context.source`) and writes text back
// as a number (`JSON.rawJSON`) loses nothing: such a number is kept as its text, and
// `JSON.stringify` writes that text again, wherever the value is written. Every other
// value is read as it always was.

type Raw = { rawJSON: string };
type WithDigits = typeof JSON & { rawJSON?: (text: string) => Raw };
type Context = { source?: string } | undefined;

/** Whether a browser may hold `value` as another number than was written. */
export function changes(value: number): boolean {
  return (
    !Number.isFinite(value) ||
    (Number.isInteger(value) && !Number.isSafeInteger(value))
  );
}

/** Whether this browser keeps a number's own text when it reads JSON, and writes it back. */
export function keepsDigits(): boolean {
  if (typeof (JSON as WithDigits).rawJSON !== "function") {
    return false;
  }
  let given = false;
  const look = (_key: string, value: unknown, context: Context) => {
    given ||= context?.source === "1";
    return value;
  };
  JSON.parse("1", look as (key: string, value: unknown) => unknown);
  return given;
}

/**
 * Read JSON. A number this browser would change is kept as it is written where the browser
 * can do that, and is read as the browser reads it where it cannot: `keepsDigits` says
 * which, for a caller that has to refuse what it cannot send as it was entered.
 */
export function readJson(text: string): unknown {
  if (!keepsDigits()) {
    return JSON.parse(text);
  }
  const raw = (JSON as WithDigits).rawJSON;
  const keep = (_key: string, value: unknown, context: Context) =>
    typeof value === "number" &&
    changes(value) &&
    typeof context?.source === "string" &&
    raw !== undefined
      ? raw(context.source)
      : value;
  return JSON.parse(text, keep as (key: string, value: unknown) => unknown);
}
