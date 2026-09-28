"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Radio, Search } from "lucide-react";
import clsx from "clsx";
import { smaApi, px, type SmaConfig, type SmaState } from "@/lib/smaApi";

const DEFAULTS = ["KIRLOSFER", "ANTELOPUS"];
const SAVED_KEY = "sma.symbols";

type Hit = { symbol: string; name: string };

function deskOrigin(): string {
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host !== "localhost" && host !== "127.0.0.1") return window.location.origin;
  }
  return process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
}

type Props = {
  state: SmaState | null;
  config: SmaConfig | null;
  connected: boolean;
  onChanged: () => void;
};

export function Header({ state, config, connected, onChanged }: Props) {
  const [symbol, setSymbol] = useState(config?.symbol ?? "");
  const [saved, setSaved] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [searching, setSearching] = useState(false);
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLDivElement>(null);
  const symbols = Array.from(new Set([...DEFAULTS, ...saved]));

  useEffect(() => {
    if (config?.symbol) setSymbol(config.symbol);
  }, [config?.symbol]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(SAVED_KEY);
      const parsed = raw ? (JSON.parse(raw) as unknown) : [];
      if (Array.isArray(parsed)) {
        setSaved(parsed.filter((s) => typeof s === "string" && /^[A-Z0-9]+$/.test(s)).slice(0, 12));
      }
    } catch {
      /* a private browser can refuse storage */
    }
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) {
      setHits([]);
      return;
    }
    const id = setTimeout(() => {
      setSearching(true);
      fetch(`${deskOrigin()}/api/instruments/search?q=${encodeURIComponent(q)}&limit=8`)
        .then(async (res) => {
          if (!res.ok) throw new Error("Search is unavailable");
          return (await res.json()) as Hit[];
        })
        .then((rows) => {
          setHits(rows);
          setOpen(true);
        })
        .catch(() => {
          setHits([]);
          setOpen(true);
        })
        .finally(() => setSearching(false));
    }, 200);
    return () => clearTimeout(id);
  }, [query]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const remember = (next: string) => {
    const cleaned = next.trim().toUpperCase();
    if (!cleaned || DEFAULTS.includes(cleaned)) return;
    setSaved((prev) => {
      const list = [cleaned, ...prev.filter((s) => s !== cleaned)].slice(0, 12);
      try {
        localStorage.setItem(SAVED_KEY, JSON.stringify(list));
      } catch {
        /* storage full or blocked */
      }
      return list;
    });
  };

  const quoted = state != null && state.ltp > 0;
  const ltp = quoted ? state.ltp : null;
  const changePct = quoted ? state?.day_change_pct : null;
  const live = (state?.mode ?? config?.trading_mode) === "LIVE";
  const running = state?.bot_status === "RUNNING";

  const applySymbol = async (next: string) => {
    const cleaned = next.trim().toUpperCase();
    if (!cleaned) return;
    setBusy(true);
    setError(null);
    try {
      await smaApi.saveConfig({ symbol: cleaned });
      setSymbol(cleaned);
      remember(cleaned);
      setQuery("");
      setHits([]);
      setOpen(false);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not change symbol");
    } finally {
      setBusy(false);
    }
  };

  const switchMode = async (mode: "PAPER" | "LIVE", confirmLive = false) => {
    setBusy(true);
    setError(null);
    try {
      await smaApi.setMode(mode, confirmLive);
      setConfirm(false);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Mode change refused");
    } finally {
      setBusy(false);
    }
  };

  const toggleBot = async () => {
    setBusy(true);
    setError(null);
    try {
      if (running) await smaApi.pause();
      else await smaApi.start();
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Bot command failed");
    } finally {
      setBusy(false);
    }
  };

  const panic = async () => {
    setBusy(true);
    setError(null);
    try {
      await smaApi.kill();
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Kill switch failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <header className="sticky top-0 z-30 border-b border-white/5 bg-[#0B0E14]/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-4 py-3">
        <nav className="flex items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-slate-500">
          <a href="/" className="hover:text-slate-300">Dashboard</a>
          <a href="/trade/" className="hover:text-slate-300">Trade</a>
          <a href="/chart/" className="hover:text-slate-300">Charts</a>
          <a href="/settings/" className="hover:text-slate-300">Settings</a>
        </nav>
        <div className="text-sm font-semibold tracking-tight text-slate-100">SMA × ATR Terminal</div>

        <div ref={searchRef} className="relative flex flex-wrap items-center gap-1 rounded-full border border-white/10 bg-[#151921] px-2 py-1">
          <span className="px-1 text-[10px] uppercase tracking-wider text-slate-500">NSE</span>
          {symbols.map((s) => (
            <button
              key={s}
              disabled={busy}
              onClick={() => applySymbol(s)}
              className={clsx(
                "rounded-full px-2 py-0.5 text-xs font-medium",
                symbol === s ? "bg-white/10 text-slate-100" : "text-slate-400 hover:text-slate-200"
              )}
            >
              {s === symbol ? `NSE: ${s}` : s}
            </button>
          ))}
          <form
            className="flex items-center"
            onSubmit={(e) => {
              e.preventDefault();
              const typed = query.trim().toUpperCase();
              if (typed) applySymbol(typed);
            }}
          >
            <Search size={12} className="text-slate-500" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value.toUpperCase())}
              onFocus={() => hits.length > 0 && setOpen(true)}
              placeholder="Find a stock"
              className="w-28 bg-transparent px-2 text-xs uppercase text-slate-100 outline-none placeholder:normal-case placeholder:text-slate-500"
              aria-label="Find an NSE stock"
            />
            {searching && <Loader2 size={11} className="animate-spin text-slate-500" />}
          </form>
          {open && query.trim() && !searching && hits.length === 0 && (
            <div className="absolute left-0 top-full z-40 mt-1 w-56 rounded-xl border border-white/10 bg-[#151921] px-3 py-2 text-xs text-slate-400 shadow-xl">
              No NSE match
            </div>
          )}
          {open && hits.length > 0 && (
            <div className="absolute left-0 top-full z-40 mt-1 max-h-72 w-[min(20rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-white/10 bg-[#151921] shadow-xl">
              {hits.map((hit) => (
                <button
                  key={hit.symbol}
                  type="button"
                  disabled={busy}
                  onClick={() => applySymbol(hit.symbol)}
                  className="flex w-full flex-col px-3 py-2 text-left hover:bg-white/[0.04]"
                >
                  <span className="text-xs font-medium text-slate-100">{hit.symbol}</span>
                  <span className="truncate text-[10px] text-slate-500">{hit.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="font-mono text-sm">
          <span className="text-slate-100">{px(ltp)}</span>
          <span
            className={clsx(
              "ml-2 text-xs",
              changePct == null ? "text-slate-500" : changePct >= 0 ? "text-[#10B981]" : "text-[#F43F5E]"
            )}
          >
            {changePct == null ? "—" : `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`}
          </span>
        </div>

        <button
          disabled={busy}
          onClick={() => (live ? switchMode("PAPER") : setConfirm(true))}
          title={live ? "Live Groww orders are on" : "Practice fills only. This does not send orders to Groww."}
          className={clsx(
            "rounded-full px-3 py-1 text-xs font-semibold",
            live
              ? "animate-pulse bg-[#F43F5E]/20 text-[#F43F5E] ring-1 ring-[#F43F5E]/50"
              : "bg-[#F59E0B]/15 text-[#F59E0B] ring-1 ring-[#F59E0B]/40"
          )}
        >
          {live ? "LIVE REAL MONEY" : "PAPER · no Groww orders"}
        </button>

        <button
          disabled={busy}
          onClick={toggleBot}
          title="Paper fills on this page only. Starting the bot does not send a Groww order."
          className={clsx(
            "rounded-md px-3 py-1.5 text-xs font-semibold",
            running ? "bg-white/10 text-slate-100" : "bg-[#10B981] text-[#04140d]"
          )}
        >
          {running ? "PAUSE BOT" : "START BOT"}
        </button>

        <span className="flex items-center gap-1.5 text-[11px] text-slate-400" title={state?.data_source}>
          <Radio size={12} className={connected ? "text-[#10B981]" : "text-[#F43F5E]"} />
          {connected ? state?.bot_status ?? "LIVE" : "OFFLINE"}
          {state?.data_source ? ` · ${state.data_source}` : ""}
        </span>

        <button
          disabled={busy}
          onClick={panic}
          className="ml-auto rounded-md bg-[#F43F5E] px-3 py-1.5 text-xs font-bold tracking-wide text-white shadow-[0_0_24px_rgba(244,63,94,0.35)]"
        >
          PANIC SQUARE-OFF ALL
        </button>
      </div>
      {(error || state?.halt_reason || state?.last_error) && (
        <div className="border-t border-[#F43F5E]/30 bg-[#F43F5E]/10 px-4 py-1.5 text-xs text-[#F43F5E]">
          {error || state?.halt_reason || state?.last_error}
        </div>
      )}

      {confirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-xl border border-[#F43F5E]/40 bg-[#151921] p-5">
            <div className="flex gap-3">
              <AlertTriangle className="mt-0.5 text-[#F43F5E]" size={20} />
              <div>
                <h2 className="text-base font-semibold text-slate-100">Enable LIVE REAL MONEY?</h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  Orders will be sent to Groww as NSE MIS limit orders with a 0.20% protection buffer,
                  plus an exchange stop-loss. This needs <span className="text-slate-200">GROWW_ACCESS_TOKEN</span> in
                  the backend environment. Paper mode stays the default until you confirm.
                </p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-md px-3 py-1.5 text-sm text-slate-300 hover:bg-white/5"
                onClick={() => setConfirm(false)}
              >
                Cancel
              </button>
              <button
                disabled={busy}
                className="rounded-md bg-[#F43F5E] px-3 py-1.5 text-sm font-semibold text-white"
                onClick={() => switchMode("LIVE", true)}
              >
                Confirm LIVE
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
