import { describe, expect, it } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("joins class names", () => {
    expect(cn("text-sm", "font-medium")).toBe("text-sm font-medium");
  });

  it("leaves out what is false or missing", () => {
    expect(cn("text-sm", false, null, undefined, { hidden: false })).toBe(
      "text-sm",
    );
  });

  it("lets a later Tailwind class replace the one it conflicts with", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
    expect(cn("bg-primary hover:bg-primary/80", "bg-secondary")).toBe(
      "hover:bg-primary/80 bg-secondary",
    );
  });
});
