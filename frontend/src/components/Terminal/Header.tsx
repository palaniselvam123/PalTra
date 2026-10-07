"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, ChevronDown, Loader2, Moon, Search, Sun } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import clsx from "clsx";
import { inr, smaApi, px, type Desk, type SmaConfig, type SmaState } from "@/lib/smaApi";
import { StatusBar } from "./StatusBar";
import { Skeleton } from "./ui";
import { StockCard } from "./StockCard";
import { ControlBar } from "./ControlBar";
import { ownSummary } from "./StrategyConfigPanel";

const DEFAULTS = ["KIRLOSFER", "ANTELOPUS"];
const ARM_LIMIT = 24;
// The research desk shares the live bot's Groww quota, so it arms fewer stocks.
const RESEARCH_ARM_LIMIT = 10;

function chipNote(note: string, symbol: string): string {
  return note.replace(new RegExp(`^${symbol}\\s+`, "i"), "").trim();
}
const SAVED_KEY = "sma.symbols";
// Stocks taken off the list with Remove, so a default does not come back.
const HIDDEN_KEY = "sma.symbols.hidden";
const FOLD_KEY = "sma.stocks.folded";

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
  loadNote?: string | null;
  onChanged: () => void;
  /** A page-level alert shown right under the sticky header. */
  notice?: ReactNode;
  /** The bot this page drives: live desk or the paper-only research desk. */
  desk?: Desk;
  onDeskChange?: (desk: Desk) => void;
};

/** Light / dark, shared with the rest of the desk (saved in this browser). */
function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const light = theme === "light";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={light ? "Switch to the dark theme" : "Switch to the light theme"}
      title={light ? "Dark theme" : "Light theme"}
      className="flex min-h-9 min-w-9 shrink-0 items-center justify-center rounded-md text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
    >
      {light ? <Moon size={15} aria-hidden /> : <Sun size={15} aria-hidden />}
    </button>
  );
}

/** Live desk / Research desk. Each browser (or `?desk=` link) keeps its own choice. */
function DeskSwitch({ desk, onChange }: { desk: Desk; onChange: (desk: Desk) => void }) {
  const options: { id: Desk; label: string; title: string }[] = [
    { id: "live", label: "Live desk", title: "The bot that trades your account (PAPER or LIVE)" },
    {
      id: "research",
      label: "Research",
      title: "A paper-only second bot on today's live prices, with its own settings and book. Never sends an order.",
    },
  ];
  return (
    <span role="group" aria-label="Desk" className="inline-flex shrink-0 rounded-md ring-1 ring-inset ring-white/15">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          aria-pressed={desk === o.id}
          title={o.title}
          onClick={() => desk !== o.id && onChange(o.id)}
          className={clsx(
            "min-h-9 px-2.5 text-xs font-semibold first:rounded-l-md last:rounded-r-md",
            desk === o.id
              ? o.id === "research"
                ? "bg-teal-600/30 text-teal-100"
                : "bg-white/10 text-slate-100"
              : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
          )}
        >
          {o.label}
        </button>
      ))}
    </span>
  );
}

export function Header({ state, config, connected, loadNote, onChanged, notice, desk = "live", onDeskChange }: Props) {
  const armLimit = desk === "research" ? RESEARCH_ARM_LIMIT : ARM_LIMIT;
  const [symbol, setSymbol] = useState(config?.symbol ?? "");
  const [saved, setSaved] = useState<string[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [searching, setSearching] = useState(false);
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [folded, setFolded] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (config?.symbol) setSymbol(config.symbol);
  }, [config?.symbol]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(SAVED_KEY);
      const parsed = raw ? (JSON.parse(raw) as unknown) : [];
      if (Array.isArray(parsed)) {
        setSaved(parsed.filter((s) => typeof s === "string" && /^[A-Z0-9]+$/.test(s)).slice(0, 24));
      }
    } catch {
      /* a private browser can refuse storage */
    }
    try {
      const raw = localStorage.getItem(HIDDEN_KEY);
      const parsed = raw ? (JSON.parse(raw) as unknown) : [];
      if (Array.isArray(parsed)) setHidden(parsed.filter((s) => typeof s === "string" && /^[A-Z0-9]+$/.test(s)));
    } catch {
      /* ignore */
    }
    try {
      setFolded(localStorage.getItem(FOLD_KEY) === "1");
    } catch {
      /* ignore */
    }
  }, []);

  const toggleFold = () => {
    setFolded((was) => {
      const next = !was;
      try {
        localStorage.setItem(FOLD_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      if (next) setOpen(false);
      return next;
    });
  };

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

  const storeHidden = (list: string[]) => {
    try {
      localStorage.setItem(HIDDEN_KEY, JSON.stringify(list));
    } catch {
      /* storage full or blocked */
    }
  };

  const remember = (next: string) => {
    const cleaned = next.trim().toUpperCase();
    if (!cleaned) return;
    // Picking a removed stock again brings it back.
    setHidden((prev) => {
      if (!prev.includes(cleaned)) return prev;
      const list = prev.filter((s) => s !== cleaned);
      storeHidden(list);
      return list;
    });
    if (DEFAULTS.includes(cleaned)) return;
    setSaved((prev) => {
      const list = [cleaned, ...prev.filter((s) => s !== cleaned)].slice(0, 24);
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

  const armedList = (config?.trade_symbols ?? []).map((s) => s.toUpperCase());
  const armed = new Set(armedList);
  const books = state?.books ?? [];
  const bookBySymbol = new Map(books.map((book) => [book.symbol.toUpperCase(), book]));
  const openBooks = books.filter((b) => (b.direction === "LONG" || b.direction === "SHORT") && b.qty > 0);
  const openCount = openBooks.length;
  const openNet = state?.open_net_total ?? openBooks.reduce((sum, b) => sum + (b.open_net ?? 0), 0);
  const signed = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${inr(Math.abs(v))}`;
  // Armed first, then the rest, each alphabetically. A held stock is never
  // dropped; a removed one stays off unless it is armed, held or on the chart.
  const chartSymbol = (config?.symbol ?? "").toUpperCase();
  const symbols = Array.from(
    new Set([
      ...armedList,
      ...books.map((book) => book.symbol.toUpperCase()),
      ...(chartSymbol ? [chartSymbol] : []),
      ...[...DEFAULTS, ...saved].filter((s) => !hidden.includes(s)),
    ])
  )
    .slice(0, 24)
    .sort((a, b) => Number(armed.has(b)) - Number(armed.has(a)) || a.localeCompare(b));

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

  const toggleTrade = async (next: string) => {
    const cleaned = next.trim().toUpperCase();
    if (!cleaned) return;
    const turningOn = !armed.has(cleaned);
    if (turningOn && armedList.length >= armLimit) {
      setError(`Trade is limited to ${armLimit} stocks at once. Turn one off before adding another.`);
      return;
    }
    if (turningOn && live) {
      const ok = window.confirm(
        `Arm ${cleaned} for live SMA orders? The bot can buy or sell it while this chart stays on ${symbol || "the stock you are viewing"}.`
      );
      if (!ok) return;
    }
    setBusy(true);
    setError(null);
    try {
      await smaApi.setTradeSymbol(cleaned, turningOn);
      remember(cleaned);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not change the trade button");
    } finally {
      setBusy(false);
    }
  };

  const isHeld = (s: string) => {
    const dir = bookBySymbol.get(s)?.direction;
    return dir === "LONG" || dir === "SHORT";
  };
  const shownSelected = symbols.filter((s) => selected.has(s));
  const select = (s: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(s);
      else next.delete(s);
      return next;
    });

  /** Unarm each stock in turn. Open positions stay open; only new orders stop. */
  const unarm = async (names: string[]) => {
    const targets = names.filter((s) => armed.has(s));
    if (!targets.length) return [] as string[];
    const failed: string[] = [];
    for (const name of targets) {
      try {
        await smaApi.setTradeSymbol(name, false);
      } catch (e: unknown) {
        failed.push(`${name}: ${e instanceof Error ? e.message : "could not unarm"}`);
      }
    }
    return failed;
  };

  const bulkUnarm = async (names: string[]) => {
    setBusy(true);
    setError(null);
    try {
      // Unarmed stocks stay on the list; only Remove takes them off.
      names.forEach(remember);
      const failed = await unarm(names);
      if (failed.length) setError(failed.join(" · "));
      setSelected(new Set());
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  /** Unarm, then drop from this list. The chart stock and held stocks stay. */
  const remove = async (names: string[]) => {
    const kept = names.filter((s) => s === chartSymbol || isHeld(s));
    const going = names.filter((s) => !kept.includes(s));
    setBusy(true);
    setError(null);
    try {
      const failed = await unarm(going);
      const failedNames = failed.map((f) => f.split(":")[0]);
      const gone = going.filter((s) => !failedNames.includes(s));
      setSaved((prev) => {
        const list = prev.filter((s) => !gone.includes(s));
        try {
          localStorage.setItem(SAVED_KEY, JSON.stringify(list));
        } catch {
          /* ignore */
        }
        return list;
      });
      setHidden((prev) => {
        const list = Array.from(new Set([...prev, ...gone]));
        storeHidden(list);
        return list;
      });
      setSelected((prev) => new Set([...prev].filter((s) => !gone.includes(s))));
      const notes = [...failed];
      if (kept.length) {
        notes.push(`Kept ${kept.join(", ")}: on the chart or holding a position.`);
      }
      if (notes.length) setError(notes.join(" · "));
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const forceOrder = async () => {
    // The ControlBar dialog has already named the stock and, in LIVE, had CONFIRM typed.
    const target = (symbol || config?.symbol || "").trim().toUpperCase();
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await smaApi.forceOrder(target);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Force order failed");
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

  const resetTrades = async () => {
    const used = state?.trades_today ?? 0;
    const cap = state?.max_trades ?? config?.max_trades_per_day ?? 0;
    const money = live ? "REAL Groww orders" : "practice trades";
    if (
      !window.confirm(
        `Reset today's trade count from ${used} to 0?\n\nThe bot may then place up to ${cap} more ${money} today. ` +
          "Today's trades, P&L and the daily loss limit are kept. A loss-limit or panic stop stays locked."
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await smaApi.resetTrades();
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  };

  const toggleSeconds = async () => {
    setBusy(true);
    setError(null);
    try {
      await smaApi.setTickFeed(!state?.second_ticks);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not change the per-second prices");
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
    <>
      <nav
        aria-label="Desk pages"
        className="grid grid-cols-4 border-b border-white/10 bg-[#0B0E14] text-xs uppercase tracking-[0.12em] text-slate-300 sm:flex sm:gap-5 sm:px-4"
      >
        {[
          ["/", "Dashboard"],
          ["/trade/", "Trade"],
          ["/chart/", "Charts"],
          ["/settings/", "Settings"],
        ].map(([href, label]) => (
          <a key={href} href={href} className="flex min-h-11 items-center justify-center hover:text-white sm:min-h-9 sm:justify-start">
            {label}
          </a>
        ))}
      </nav>
      <header
        aria-label="SMA terminal"
        className={clsx(
          "sticky top-0 z-30 bg-[#0B0E14]",
          live ? "border-2 border-rose-500 shadow-[0_0_0_1px_rgba(244,63,94,0.25)]" : "border-b border-white/10"
        )}
      >
        <div className="mx-auto flex w-full min-w-0 flex-col gap-2 px-3 py-2 sm:px-4">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <span className="flex min-w-0 items-center gap-2">
              <span className="hidden truncate text-sm font-semibold tracking-tight text-slate-100 sm:inline">
                SMA × ATR Terminal
              </span>
              {onDeskChange ? <DeskSwitch desk={desk} onChange={onDeskChange} /> : null}
              <ThemeToggle />
            </span>
            <div className="flex shrink-0 items-baseline gap-2 text-sm">
              {config?.symbol ? <span className="font-semibold text-amber-300">{config.symbol}</span> : null}
              <span className="font-mono text-slate-100">{px(ltp)}</span>
              <span
                className={clsx(
                  "font-mono text-xs",
                  changePct == null ? "text-slate-400" : changePct >= 0 ? "text-emerald-400" : "text-rose-400"
                )}
              >
                {changePct == null ? "" : `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`}
              </span>
            </div>
          </div>
          <StatusBar
            state={state}
            config={config}
            connected={connected}
            busy={busy}
            onModeClick={() => (live ? switchMode("PAPER") : setConfirm(true))}
            onResetTrades={state?.mode === "REPLAY" ? undefined : resetTrades}
            onToggleSeconds={state?.mode === "REPLAY" ? undefined : toggleSeconds}
          />
        </div>
        {(error || state?.halt_reason || state?.last_error) && (
          <div role="alert" className="border-t border-rose-500/30 bg-rose-500/10 px-4 py-1.5 text-xs text-rose-300">
            {error || state?.halt_reason || state?.last_error}
          </div>
        )}
      </header>

      <div className="mx-auto flex w-full min-w-0 flex-col gap-2 px-3 pt-3 sm:px-4">
        {notice}
        <ControlBar
          state={state}
          live={live}
          running={running}
          busy={busy}
          symbol={(symbol || config?.symbol || "").toUpperCase()}
          symbolArmed={armed.has((symbol || config?.symbol || "").toUpperCase())}
          onToggleBot={toggleBot}
          onForce={forceOrder}
          onPanic={panic}
        />
        <div ref={searchRef} className="relative min-w-0">
          <div className="rounded-xl border border-white/10 bg-[#151921]">
            <h2 className={clsx(!folded && "border-b border-white/10")}>
              <button
                type="button"
                aria-expanded={!folded}
                aria-controls="stock-list-body"
                onClick={toggleFold}
                title={folded ? "Show the stock list" : "Shrink the stock list to one line"}
                className="flex min-h-11 w-full min-w-0 items-center gap-2 rounded-xl px-3 py-1.5 text-left hover:bg-white/[0.03]"
              >
                <ChevronDown
                  size={16}
                  aria-hidden
                  className={clsx("shrink-0 text-slate-400 transition-transform", folded && "-rotate-90")}
                />
                <span className="shrink-0 text-sm font-semibold text-slate-100">Stocks</span>
                {openCount > 0 ? (
                  // Every open position and its live net, in the bar's empty middle.
                  <span className="flex min-w-0 flex-1 items-center gap-x-3 overflow-hidden whitespace-nowrap pl-2 font-mono text-xs" aria-label="Open positions">
                    <span className={clsx("shrink-0 font-semibold", openNet >= 0 ? "text-emerald-300" : "text-rose-300")} title="Unrealized net of every open position, after estimated charges">
                      Open {signed(openNet)}
                    </span>
                    <span className="hidden min-w-0 items-center gap-x-3 overflow-hidden sm:flex">
                      {openBooks.map((b) => (
                        <span key={b.symbol} className="shrink-0" title={`${b.symbol} ${b.direction} ${b.qty}${b.entry_price ? ` @ ${px(b.entry_price)}` : ""}`}>
                          <span className="font-sans text-slate-200">{b.symbol}</span>{" "}
                          <span className={b.direction === "LONG" ? "text-emerald-400" : "text-rose-400"}>{b.direction === "LONG" ? "L" : "S"}</span>
                          <span className="text-slate-500">×{b.qty}</span>{" "}
                          <span className={(b.open_net ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>
                            {b.open_net == null ? "—" : signed(b.open_net)}
                          </span>
                        </span>
                      ))}
                    </span>
                  </span>
                ) : null}
                <span className="ml-auto shrink-0 text-xs font-normal text-slate-400">
                  <span className="font-semibold text-emerald-300">{armedList.length}</span>
                  {folded ? " armed" : ` of ${armLimit} armed for trading`}
                  {folded && openCount > 0 ? (
                    <>
                      {" · "}
                      <span className="font-semibold text-amber-300">{openCount}</span> open
                    </>
                  ) : null}
                </span>
              </button>
            </h2>
            {folded && armedList.length > 0 && (
              <p className="-mt-1 truncate px-3 pb-2 pl-9 text-xs text-slate-400" title={armedList.join(", ")}>
                {armedList.map((s, i) => {
                  const dir = bookBySymbol.get(s)?.direction;
                  return (
                    <span key={s}>
                      {i > 0 && ", "}
                      <span className={dir === "LONG" ? "text-emerald-300" : dir === "SHORT" ? "text-rose-300" : "text-slate-300"}>
                        {s}
                      </span>
                    </span>
                  );
                })}
              </p>
            )}
            <div id="stock-list-body" hidden={folded}>
            <div className="relative border-b border-white/5">
            <form
              className="flex items-center gap-2 px-3 py-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                const typed = query.trim().toUpperCase();
                if (typed) applySymbol(typed);
              }}
            >
              <Search size={14} className="shrink-0 text-slate-400" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value.toUpperCase())}
                onFocus={() => hits.length > 0 && setOpen(true)}
                placeholder="Find a stock"
                className="min-w-0 flex-1 bg-transparent py-1 text-sm uppercase text-slate-100 outline-none placeholder:normal-case placeholder:text-slate-400"
                aria-label="Find an NSE stock"
              />
              {searching && <Loader2 size={14} className="shrink-0 animate-spin text-slate-400" />}
            </form>
            {open && query.trim() && !searching && hits.length === 0 && (
              <div className="absolute left-3 top-full z-40 mt-1 w-56 rounded-xl border border-white/10 bg-[#151921] px-3 py-2 text-xs text-slate-400 shadow-xl">
                No NSE match
              </div>
            )}
            {open && hits.length > 0 && (
              <div className="absolute left-3 top-full z-40 mt-1 max-h-72 w-[min(24rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-white/10 bg-[#151921] shadow-xl">
                {hits.map((hit) => (
                  <button
                    key={hit.symbol}
                    type="button"
                    disabled={busy}
                    onClick={() => applySymbol(hit.symbol)}
                    className="flex w-full flex-col px-3 py-2 text-left hover:bg-white/[0.04]"
                  >
                    <span className="text-sm font-semibold text-[#FBBF24]">{hit.symbol}</span>
                    <span className="truncate text-[11px] text-slate-400">{hit.name}</span>
                  </button>
                ))}
              </div>
            )}
            </div>
            {!config?.symbol && loadNote && <p className="px-3 py-2 text-sm text-amber-200">The stock list did not load.</p>}
            {!config ? (
              <ul aria-busy="true" aria-label="Loading stocks" className="grid grid-cols-1 gap-2 p-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {[0, 1, 2].map((i) => (
                  <li key={i} className="space-y-2 rounded-lg bg-[#151921] p-3 ring-1 ring-inset ring-white/10">
                    <Skeleton className="h-5 w-28" />
                    <Skeleton className="h-4 w-48" />
                    <Skeleton className="h-11 w-full" />
                  </li>
                ))}
              </ul>
            ) : (
            <>
            <div className="flex flex-wrap items-center gap-2 border-b border-white/5 px-3 py-1.5 text-xs text-slate-300">
              <label className="flex min-h-9 cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-sky-400"
                  checked={symbols.length > 0 && shownSelected.length === symbols.length}
                  ref={(el) => {
                    if (el) el.indeterminate = shownSelected.length > 0 && shownSelected.length < symbols.length;
                  }}
                  onChange={(e) => setSelected(e.target.checked ? new Set(symbols) : new Set())}
                  aria-label="Select every stock"
                />
                {shownSelected.length ? `${shownSelected.length} selected` : "Select"}
              </label>
              {shownSelected.length > 0 && (
                <>
                  <button
                    type="button"
                    disabled={busy || !shownSelected.some((s) => armed.has(s))}
                    onClick={() => bulkUnarm(shownSelected)}
                    className="min-h-9 rounded-md px-2.5 font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10 disabled:opacity-40"
                  >
                    Unarm selected
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => remove(shownSelected)}
                    className="min-h-9 rounded-md px-2.5 font-semibold text-rose-200 ring-1 ring-inset ring-rose-400/40 hover:bg-rose-500/10 disabled:opacity-40"
                  >
                    Remove selected
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelected(new Set())}
                    className="min-h-9 rounded-md px-2 text-slate-400 hover:text-slate-200"
                  >
                    Clear
                  </button>
                </>
              )}
              <button
                type="button"
                disabled={busy || armedList.length === 0}
                onClick={() => bulkUnarm(armedList)}
                title="Stop new orders on every stock. Open positions stay open until their stop, a cross or square-off."
                className="ml-auto min-h-9 rounded-md px-2.5 font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10 disabled:opacity-40"
              >
                Unarm all{armedList.length ? ` (${armedList.length})` : ""}
              </button>
            </div>
            <ul className="grid grid-cols-1 gap-2 p-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4" aria-label="Stocks">
              {symbols.map((s) => {
                const book = bookBySymbol.get(s);
                const side = book?.direction === "LONG" || book?.direction === "SHORT" ? book.direction : "FLAT";
                const onChart = Boolean(config?.symbol) && symbol === s;
                const ltpOf = book?.ltp != null && book.ltp > 0 ? book.ltp : onChart ? ltp : null;
                return (
                  <StockCard
                    key={s}
                    busy={busy}
                    armLimitReached={armedList.length >= armLimit}
                    onToggleArmed={() => toggleTrade(s)}
                    onShowOnChart={() => applySymbol(s)}
                    selected={selected.has(s)}
                    onSelect={(on) => select(s, on)}
                    onRemove={() => remove([s])}
                    stock={{
                      symbol: s,
                      ltp: ltpOf,
                      changePct: onChart ? changePct ?? null : null,
                      side,
                      qty: book?.qty ?? 0,
                      note: chipNote(book?.note || "", s),
                      stopOff: book?.stop_active === false,
                      armed: armed.has(s),
                      onChart,
                      openNet: book?.open_net ?? null,
                      closedNet: book?.closed_net ?? 0,
                      closedTrades: book?.closed_trades ?? 0,
                      dayNet: book?.day_net,
                      reject: book?.last_reject ?? null,
                      ownStrategy: ownSummary(config?.stock_settings?.[s] as Record<string, unknown> | undefined),
                      rejectAt: book?.last_reject_at ?? null,
                    }}
                  />
                );
              })}
            </ul>
            </>
            )}
            </div>
          </div>
        </div>

      </div>
      {confirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-xl border border-[#F43F5E]/40 bg-[#151921] p-5">
            <div className="flex gap-3">
              <AlertTriangle className="mt-0.5 text-[#F43F5E]" size={20} />
              <div>
                <h2 className="text-[17px] font-semibold text-slate-100">Enable LIVE REAL MONEY?</h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  Orders will be sent to Groww as NSE MIS limit orders with a 0.20% protection buffer,
                  plus an exchange stop-loss. This uses the Groww login already saved on the desk.
                  Paper mode stays the default until you confirm.
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
    </>
  );
}
