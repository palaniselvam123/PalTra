"use client";

import { useCallback, useEffect, useState } from "react";

export type TradeNotice = {
  id: number;
  kind: "ENTRY" | "EXIT" | "ALERT";
  account?: string;
  source?: string;
  symbol?: string;
  side?: string;
  quantity?: number;
  price?: number;
  value?: number;
  charges?: number | null;
  pnl?: number | null;
  reason: string;
  title: string;
  body: string;
  level?: string;
  at: string;
};

const STORAGE_KEY = "orb.notifications.enabled";

/** Desktop notifications for trade events, with an in-app toast fallback.
 *
 *  Browser notifications need three things to line up: the API existing, the
 *  user having granted permission, and the user having switched them on here.
 *  Any of those can be false, so every notice is also surfaced as a toast —
 *  otherwise a denied permission silently swallows the alert. */
export function useNotifications() {
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");
  const [enabled, setEnabled] = useState(false);
  const [toasts, setToasts] = useState<TradeNotice[]>([]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("Notification" in window)) {
      setPermission("unsupported");
      return;
    }
    setPermission(Notification.permission);
    setEnabled(localStorage.getItem(STORAGE_KEY) === "1");
  }, []);

  const enable = useCallback(async () => {
    if (!("Notification" in window)) return;
    let result = Notification.permission;
    if (result === "default") result = await Notification.requestPermission();
    setPermission(result);
    // Stay on even if permission was denied — toasts still work, and the
    // alternative is a toggle that silently refuses to turn on.
    setEnabled(true);
    localStorage.setItem(STORAGE_KEY, "1");
  }, []);

  const disable = useCallback(() => {
    setEnabled(false);
    localStorage.setItem(STORAGE_KEY, "0");
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const notify = useCallback(
    (notice: TradeNotice) => {
      if (!enabled) return;

      setToasts((prev) => [notice, ...prev].slice(0, 4));
      // Toasts auto-clear; an OS notification is dismissed by the user.
      setTimeout(() => dismiss(notice.id), 12000);

      if (typeof window === "undefined" || !("Notification" in window)) return;
      if (Notification.permission !== "granted") return;
      try {
        new Notification(notice.title, {
          body: notice.body,
          // Same tag per symbol so a burst of updates on one stock replaces
          // itself rather than stacking a wall of notifications.
          tag: `orb-${notice.symbol ?? notice.kind}`,
          silent: false,
        });
      } catch {
        // Some browsers throw when constructed outside a service worker;
        // the toast has already been shown, so there is nothing to recover.
      }
    },
    [enabled, dismiss]
  );

  return { permission, enabled, enable, disable, notify, toasts, dismiss };
}
