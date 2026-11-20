import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEvery } from "@/hooks/use-every";

function Reader({ name, act }: { name: string; act: () => void }) {
  useEvery(name, 15, act);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
});

afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
});

describe("useEvery", () => {
  it("reads once a period for every component that follows the same key", () => {
    const first = vi.fn();
    const second = vi.fn();
    render(
      <>
        <Reader name="/threads" act={first} />
        <Reader name="/threads" act={second} />
      </>,
    );
    vi.advanceTimersByTime(15_000 - 1);
    expect(first.mock.calls.length + second.mock.calls.length).toBe(0);
    vi.advanceTimersByTime(1);
    expect(first.mock.calls.length + second.mock.calls.length).toBe(1);
    vi.advanceTimersByTime(30_000);
    expect(first.mock.calls.length + second.mock.calls.length).toBe(3);
  });

  it("goes on with the others when one stops following, and stops with the last", () => {
    const first = vi.fn();
    const second = vi.fn();
    const one = render(<Reader name="/threads" act={first} />);
    const two = render(<Reader name="/threads" act={second} />);
    one.unmount();
    vi.advanceTimersByTime(15_000);
    expect(second).toHaveBeenCalledTimes(1);
    two.unmount();
    // No timer is left behind.
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(45_000);
    expect(first).toHaveBeenCalledTimes(0);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("follows two keys apart", () => {
    const tasks = vi.fn();
    const other = vi.fn();
    render(
      <>
        <Reader name="/threads" act={tasks} />
        <Reader name="/other" act={other} />
      </>,
    );
    vi.advanceTimersByTime(15_000);
    expect([tasks.mock.calls.length, other.mock.calls.length]).toEqual([1, 1]);
  });

  it("reads nothing while the page is hidden", () => {
    const act = vi.fn();
    render(<Reader name="/threads" act={act} />);
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    vi.advanceTimersByTime(45_000);
    expect(act).not.toHaveBeenCalled();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    vi.advanceTimersByTime(15_000);
    expect(act).toHaveBeenCalledTimes(1);
  });
});
