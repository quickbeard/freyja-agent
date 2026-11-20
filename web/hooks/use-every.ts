import { useEffect, useRef } from "react";

/**
 * Feature 007: call `act` every `seconds`, once for every component that asks under the same
 * `key`. SWR's own `refreshInterval` skips a reading while the last one failed, so a list read
 * that way would stop following the service at its first failed reading, and stay stopped once
 * the service is back. The Tasks section and the sidebar both follow the list of tasks; one
 * timer for the key keeps that one reading, not one per component.
 *
 * Nothing is called while the page is hidden: SWR reads again when it is shown.
 */
type Poll = {
  timer: ReturnType<typeof setInterval>;
  acts: Set<{ current: () => void }>;
};

const polls = new Map<string, Poll>();

export function useEvery(key: string, seconds: number, act: () => void): void {
  const latest = useRef(act);
  latest.current = act;
  useEffect(() => {
    let poll = polls.get(key);
    if (poll === undefined) {
      const acts = new Set<{ current: () => void }>();
      const timer = setInterval(() => {
        if (
          typeof document !== "undefined" &&
          document.visibilityState === "hidden"
        ) {
          return;
        }
        // Every caller reads the same key: one of them reads it for all.
        acts.values().next().value?.current();
      }, seconds * 1000);
      poll = { timer, acts };
      polls.set(key, poll);
    }
    poll.acts.add(latest);
    const held = poll;
    return () => {
      held.acts.delete(latest);
      if (held.acts.size === 0) {
        clearInterval(held.timer);
        polls.delete(key);
      }
    };
  }, [key, seconds]);
}
