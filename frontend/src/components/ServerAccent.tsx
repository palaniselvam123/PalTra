"use client";

import { useEffect } from "react";
import { useServerRole } from "@/hooks/useServerRole";

/** The key the layout's inline script reads before first paint, so the server's colour never flashes. */
export const SERVER_STORE = "orb.server";

/**
 * One colour per server: puts `data-server="backtest"` or `"scanner"` on the page root (none on the desk), which
 * the theme file turns into the blue / teal accent (globals.css, "One colour per server"). The role comes from
 * /api/role once per page load; the answer is kept per browser so the next load paints the right colour at once.
 * Display only.
 */
export function ServerAccent() {
  const role = useServerRole();
  useEffect(() => {
    if (!role) return;
    const value = role.role === "desk" ? "" : role.role;
    const root = document.documentElement;
    if (value) root.setAttribute("data-server", value);
    else root.removeAttribute("data-server");
    try {
      if (value) localStorage.setItem(SERVER_STORE, value);
      else localStorage.removeItem(SERVER_STORE);
    } catch {
      /* private mode: the colour still applies for this load */
    }
  }, [role]);
  return null;
}

/** The server's name for a badge next to the logo (null on the desk). */
export function serverBadge(role: string | null | undefined): { text: string; className: string } | null {
  if (role === "backtest") return { text: "Backtest server", className: "bg-blue-500/15 text-blue-300 ring-blue-400/40" };
  if (role === "scanner") return { text: "Scanner", className: "bg-teal-500/15 text-teal-300 ring-teal-400/40" };
  return null;
}
