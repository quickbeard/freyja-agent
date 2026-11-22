import { afterEach, describe, expect, it } from "vitest";
import { withoutDigits } from "@/__tests__/tasks-testing";
import { changes, keepsDigits, readJson } from "@/lib/json";

// Feature 007 (T099): a number as it was written. A browser reads a whole number beyond
// 2^53, such as a 64-bit id, as the nearest one it can hold. One that gives a number's own
// text when it reads JSON keeps that text, and writes it back as it was.

let restore = () => {};
afterEach(() => {
  restore();
  restore = () => {};
});

describe("readJson", () => {
  it("runs where a number's digits are kept, as the browsers the console is tested in do", () => {
    expect(keepsDigits()).toBe(true);
  });

  it.each([
    ["a 64-bit id", '{"id":1234567890123456789}'],
    ["the first whole number a browser changes", '{"n":9007199254740993}'],
    ["a negative one", '{"n":-9007199254740993}'],
    ["one of 22 digits", '{"id":1234567890123456789012}'],
    [
      "one inside a list inside an object",
      '{"a":{"ids":[1,2,12345678901234567890]}}',
    ],
    ["a number too large to hold at all", '{"n":1e999}'],
    ["a whole number written with an exponent", '{"n":1e30}'],
  ])("keeps %s as it is written, and writes it back the same", (_what, text) => {
    expect(JSON.stringify(readJson(text))).toBe(text);
  });

  it("writes such a number in full when the value is laid out for a person to read", () => {
    expect(
      JSON.stringify(
        readJson('{"id": 1234567890123456789, "note": "Closed."}'),
        null,
        2,
      ),
    ).toBe('{\n  "id": 1234567890123456789,\n  "note": "Closed."\n}');
  });

  it("reads every other value as a browser always has", () => {
    const text =
      '{"n": 9007199254740991, "m": -9007199254740991, "f": 0.1, "e": 1.5e3, "s": "1234567890123456789", "t": true, "z": null, "l": [1, "x"], "1234567890123456789": 1}';
    expect(readJson(text)).toEqual(JSON.parse(text));
    expect(readJson('"12345678901234567890"')).toBe("12345678901234567890");
    expect(readJson("42")).toBe(42);
  });

  it("fails on text that is not JSON, as reading it always has", () => {
    expect(() => readJson("channel: #ops")).toThrow(SyntaxError);
    expect(() => readJson("")).toThrow(SyntaxError);
  });

  it("reads as a browser always has where a number's digits are not kept", () => {
    restore = withoutDigits();
    expect(keepsDigits()).toBe(false);
    const read = readJson('{"id": 1234567890123456789}') as { id: unknown };
    expect(typeof read.id).toBe("number");
    expect(JSON.stringify(read)).not.toBe('{"id":1234567890123456789}');
  });
});

describe("changes", () => {
  it("says which numbers a browser may hold as another one than was written", () => {
    expect(changes(2 ** 53 + 2)).toBe(true);
    expect(changes(-(2 ** 60))).toBe(true);
    expect(changes(Number.POSITIVE_INFINITY)).toBe(true);
    expect(changes(1e30)).toBe(true);
    expect(changes(2 ** 53 - 1)).toBe(false);
    expect(changes(0.1)).toBe(false);
    expect(changes(2.5e-30)).toBe(false);
    expect(changes(0)).toBe(false);
  });
});
