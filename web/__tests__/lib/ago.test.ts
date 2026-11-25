import { describe, expect, it } from "vitest";
import { ago, exactTime } from "@/lib/ago";

// Feature 008 (research R8): how long ago an agent's progress was learned, in whole units
// rounded down, and the exact time as the list of tasks writes a time.

const LEARNED = "2026-11-25T08:00:00Z";
const at = (seconds: number) => Date.parse(LEARNED) + seconds * 1000;

describe("ago", () => {
  it.each([
    [0, "just now"],
    [59, "just now"],
    [59.999, "just now"],
    [60, "1 minute ago"],
    [119, "1 minute ago"],
    [120, "2 minutes ago"],
    [3_599, "59 minutes ago"],
    [3_600, "1 hour ago"],
    [7_199, "1 hour ago"],
    [7_200, "2 hours ago"],
    [86_399, "23 hours ago"],
    [86_400, "1 day ago"],
    [172_799, "1 day ago"],
    [172_800, "2 days ago"],
  ])("%s seconds after it was learned is %s", (seconds, words) => {
    expect(ago(LEARNED, at(seconds))).toBe(words);
  });

  it("says just now of a time after the browser's own, as a wrong clock can give", () => {
    expect(ago(LEARNED, at(-1))).toBe("just now");
    expect(ago(LEARNED, at(-86_400))).toBe("just now");
  });

  it("reads a time with fractions of a second and an offset", () => {
    expect(ago("2026-11-25T08:00:00.987654Z", at(60.5))).toBe("just now");
    expect(ago("2026-11-25T10:00:00+02:00", at(120))).toBe("2 minutes ago");
  });
});

describe("exactTime", () => {
  it("writes a time as the list of tasks does", () => {
    const list = new Intl.DateTimeFormat("en", {
      dateStyle: "medium",
      timeStyle: "short",
    });
    expect(exactTime(LEARNED)).toBe(list.format(new Date(LEARNED)));
    expect(exactTime(LEARNED)).toMatch(
      /^Nov 2[45], 2026, \d{1,2}:\d{2}\s?[AP]M$/,
    );
  });
});
