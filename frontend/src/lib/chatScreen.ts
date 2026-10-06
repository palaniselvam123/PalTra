/**
 * What the current page is showing, for the "Ask the bot" chat.
 *
 * A page registers a compact summary of its numbers with `useChatScreen`; the
 * chat widget (mounted once in the layout) sends it with each question so the
 * answer is about what the user is looking at. Plain data only.
 */
import { useEffect } from "react";

let current: Record<string, unknown> | null = null;

export function chatScreen(): Record<string, unknown> | null {
  return current;
}

/** Register this page's summary while it is mounted. `data` should be memo-free
 *  plain JSON; it is replaced whenever it changes. */
export function useChatScreen(data: Record<string, unknown> | null): void {
  useEffect(() => {
    current = data;
  }, [data]);
  useEffect(
    () => () => {
      current = null;
    },
    []
  );
}

/** Today's date in IST as YYYY-MM-DD, the format trade rows use. */
export function istToday(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}
