"use client";

import { useEffect, useState } from "react";
import { api, type ServerRole } from "@/lib/api";

// One request per page load; every caller shares it.
let pending: Promise<ServerRole | null> | null = null;

export function loadServerRole(): Promise<ServerRole | null> {
  if (!pending) pending = api.serverRole().catch(() => null);
  return pending;
}

/** Which server this page is on (null until known, or on an older backend). */
export function useServerRole(): ServerRole | null {
  const [role, setRole] = useState<ServerRole | null>(null);
  useEffect(() => {
    let live = true;
    loadServerRole().then((r) => {
      if (live) setRole(r);
    });
    return () => {
      live = false;
    };
  }, []);
  return role;
}
