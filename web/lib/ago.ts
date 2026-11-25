// Feature 008 (research R8): how long ago a time was, in the console's own words, and the
// exact time as the console writes one. Both are pure: the time to compare with is given,
// never read here, so that nothing reads the clock while a page renders.

const WHEN = new Intl.DateTimeFormat("en", {
  dateStyle: "medium",
  timeStyle: "short",
});

const UNITS = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
] as const;

/**
 * How long before `now` (milliseconds since the epoch) the time `learnedAt` was: "just now"
 * under a minute, then whole minutes, hours, or days, rounded down. A time after `now`, as a
 * browser with a wrong clock can give, is "just now" too.
 */
export function ago(learnedAt: string, now: number): string {
  const seconds = Math.floor((now - Date.parse(learnedAt)) / 1000);
  for (const [unit, size] of UNITS) {
    if (seconds >= size) {
      const count = Math.floor(seconds / size);
      return `${count} ${unit}${count === 1 ? "" : "s"} ago`;
    }
  }
  return "just now";
}

/** A time as the console writes one, in the browser's time zone. */
export function exactTime(iso: string): string {
  return WHEN.format(new Date(iso));
}
