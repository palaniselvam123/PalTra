"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Loader2, Plus, Search } from "lucide-react";
import { api, type InstrumentResult } from "@/lib/api";

/** Search across every NSE cash-market symbol Groww knows about — not just
 *  the bot's fixed 20-name watchlist. This is the piece that was missing
 *  entirely: the app had no way to discover a symbol, only a hand-typed list.
 *  Search is local once the instrument master is cached server-side, so
 *  results come back instantly per keystroke with no per-character API cost. */
export function AddSymbolSearch({ onAdded }: { onAdded: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<InstrumentResult[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [addingSymbol, setAddingSymbol] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) {
      setResults([]);
      return;
    }
    setSearching(true);
    const id = setTimeout(() => {
      api
        .searchInstruments(q, 12)
        .then((r) => {
          setResults(r);
          setOpen(true);
        })
        .catch(() => setResults([]))
        .finally(() => setSearching(false));
    }, 200);
    return () => clearTimeout(id);
  }, [query]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const add = async (symbol: string) => {
    setAddingSymbol(symbol);
    setError(null);
    try {
      await api.addToWatchlist(symbol);
      setQuery("");
      setResults([]);
      setOpen(false);
      onAdded();
    } catch (e: any) {
      setError(e.message ?? "Could not add that symbol");
    } finally {
      setAddingSymbol(null);
    }
  };

  return (
    <div ref={boxRef} className="relative">
      <div className="relative">
        <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-600" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder="Add any NSE stock…"
          className="bg-base border border-border rounded-md pl-6 pr-2 py-1 text-xs text-slate-200 w-44 focus:outline-none focus:border-bot/60"
        />
        {searching && (
          <Loader2 size={11} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-600 animate-spin" />
        )}
      </div>

      {open && (results.length > 0 || error) && (
        <div className="absolute z-20 mt-1 w-72 right-0 rounded-lg border border-border bg-surface shadow-xl overflow-hidden">
          {error && <div className="px-3 py-2 text-[11px] text-loss border-b border-border">{error}</div>}
          <div className="max-h-72 overflow-y-auto">
            {results.map((r) => (
              <button
                key={r.symbol}
                onClick={() => add(r.symbol)}
                disabled={addingSymbol !== null}
                className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-white/[0.04] transition disabled:opacity-50"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-slate-100 flex items-center gap-1.5">
                    {r.symbol}
                    {!r.intraday_allowed && (
                      <span
                        className="text-[9px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-400"
                        title="Not currently MIS-eligible on Groww — you can watch it, but orders will be refused"
                      >
                        NO MIS
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-slate-500 truncate">{r.name}</div>
                </div>
                {addingSymbol === r.symbol ? (
                  <Loader2 size={13} className="text-bot animate-spin shrink-0" />
                ) : (
                  <Plus size={13} className="text-bot shrink-0" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
