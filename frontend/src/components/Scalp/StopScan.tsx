"use client";

import { Square } from "lucide-react";
import { useState } from "react";

/**
 * Stop for one running Scalp scan (backend main.scan_stop / the desk's /api/scalp/active/stop). The scan stops after
 * the stock it is reading; the last finished rows stay on screen. Shown only while that scan runs.
 */
export function StopScan({ running, stop, onStopped }: { running: boolean | undefined; stop: () => Promise<unknown>; onStopped?: () => void }) {
  const [busy, setBusy] = useState(false);
  if (!running) return null;
  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await stop();
          onStopped?.();
        } catch {
          /* the next poll shows where it stands */
        } finally {
          setBusy(false);
        }
      }}
      title="Stop this scan now. The last finished result stays on screen."
      className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-rose-300 ring-1 ring-inset ring-rose-400/40 hover:bg-rose-500/10 disabled:opacity-50"
    >
      <Square size={13} aria-hidden /> {busy ? "Stopping…" : "Stop"}
    </button>
  );
}
