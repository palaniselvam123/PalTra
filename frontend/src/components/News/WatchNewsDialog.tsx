"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import { ExternalLink, Loader2, Newspaper, X } from "lucide-react";
import { useNewsLists } from "@/components/News/useNewsLists";

/**
 * Add stocks to a news watch list (an existing one or a new one), then open their news on the News page.
 * Information only: nothing is armed or traded. On the scanner server the lists are the desk's (over the link).
 */
export function WatchNewsDialog({
  symbols,
  onClose,
  onAdded,
}: {
  symbols: string[];
  onClose: () => void;
  /** Called after the stocks were added (the News page reloads its lists). */
  onAdded?: () => void;
}) {
  const { lists, error, addTo, newsPageUrl } = useNewsLists();
  const [pick, setPick] = useState<string>("new");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const names = symbols.map((s) => s.toUpperCase());

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      const listId = pick === "new" ? null : Number(pick);
      const row = await addTo(names, listId, pick === "new" ? name.trim() || "Watch list" : null);
      setNote({ ok: true, text: `Added to "${row.name}" (${row.symbols.length} stocks). Their news is read on every news pass.` });
      onAdded?.();
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not add" });
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="Watch news" className="w-full max-w-md rounded-xl border border-border bg-surface p-4 text-sm shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="inline-flex items-center gap-2 text-sm font-semibold text-slate-100">
            <Newspaper size={15} className="text-amber-300" /> Watch news · {names.length} stock{names.length === 1 ? "" : "s"}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mt-1 truncate text-xs text-slate-400" title={names.join(", ")}>
          {names.join(", ")}
        </p>
        <div className="mt-3 flex flex-col gap-2 text-xs text-slate-400">
          <label className="flex flex-col gap-1">
            Watch list
            <select
              value={pick}
              onChange={(e) => setPick(e.target.value)}
              className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
            >
              <option value="new">New list…</option>
              {lists.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.symbols.length})
                </option>
              ))}
            </select>
          </label>
          {pick === "new" ? (
            <label className="flex flex-col gap-1">
              Name
              <input
                value={name}
                maxLength={40}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Banks"
                className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
              />
            </label>
          ) : null}
        </div>
        {error ? <p className="mt-2 text-xs text-rose-300">{error}</p> : null}
        {note ? <p className={note.ok ? "mt-2 text-xs text-emerald-300" : "mt-2 text-xs text-rose-300"}>{note.text}</p> : null}
        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
          <a
            href={newsPageUrl(names)}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-9 items-center gap-1 rounded-md px-3 text-xs font-semibold text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
          >
            Open their news <ExternalLink size={12} />
          </a>
          <button
            type="button"
            disabled={busy || names.length === 0}
            onClick={save}
            className="inline-flex min-h-9 items-center gap-1 rounded-md bg-amber-500/15 px-3 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/25 disabled:opacity-50"
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : null} Add to watch list
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
