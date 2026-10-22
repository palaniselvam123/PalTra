"use client";

import { useCallback, useEffect, useState } from "react";

const PREFIX = "paltra.pin.";
const EVENT = "paltra-pin";

/**
 * Whether a bar that sticks to the top of the screen (a "frozen" header) stays pinned
 * while the page scrolls. On by default; the person's choice is kept in this browser
 * and shared by every copy of the same bar on the page.
 */
export function usePinned(key: string, initial = true): [boolean, (next: boolean) => void] {
  const [pinned, setPinned] = useState(initial);
  useEffect(() => {
    const read = () => {
      try {
        const saved = localStorage.getItem(PREFIX + key);
        setPinned(saved == null ? initial : saved === "1");
      } catch {
        setPinned(initial);
      }
    };
    read();
    window.addEventListener(EVENT, read);
    window.addEventListener("storage", read);
    return () => {
      window.removeEventListener(EVENT, read);
      window.removeEventListener("storage", read);
    };
  }, [key, initial]);
  const set = useCallback(
    (next: boolean) => {
      setPinned(next);
      try {
        localStorage.setItem(PREFIX + key, next ? "1" : "0");
        window.dispatchEvent(new Event(EVENT));
      } catch {
        /* private window: this page only */
      }
    },
    [key]
  );
  return [pinned, set];
}
