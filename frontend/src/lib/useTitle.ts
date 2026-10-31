"use client";

import { useEffect } from "react";

/**
 * Names the browser tab from a client component.
 *
 * The route's layout gives the tab its page name at load (Next's metadata); a desk or a stock refines it here.
 * React re-writes that server title once while it hydrates, after this effect has already run, so the hook also
 * watches the title and puts its own text back when something else changes it (only while mounted; a different
 * text we set ourselves is never fought).
 */
export function useTitle(title: string | null): void {
  useEffect(() => {
    if (!title || typeof document === "undefined") return;
    const apply = () => {
      if (document.title !== title) document.title = title;
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.head, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [title]);
}
