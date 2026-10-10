"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { WalletChip } from "@/components/Wallet/WalletChip";
import { AlertTriangle, CandlestickChart, ChevronDown, Loader2, Moon, Search, Sun } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import clsx from "clsx";
import { inr, smaApi, px, type BotSummary, type Desk, type ReplayInfo, type SmaConfig, type SmaState } from "@/lib/smaApi";
import { StatusBar } from "./StatusBar";
import { NAV } from "@/components/Navbar";
import { Skeleton } from "./ui";
import { StockCard } from "./StockCard";
import { ControlBar } from "./ControlBar";
import { ReviewCards } from "./ReviewCard";
import { ownSummary } from "./StrategyConfigPanel";
import { ArmPrompt } from "./ArmPrompt";
import { inTab, type TerminalTab } from "./SectionTabs";

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
  /** The section tabs, shown above the page's sections. */
  tabs?: ReactNode;
  /** Which section is in view: the stock list shows on Watchlist, Force order on Live (both on All). */
  tab?: TerminalTab;
  /** This desk's replay, shown small in the top strip while it runs. */
  replay?: ReplayInfo | null;
  onReplay?: (info: ReplayInfo) => void;
  onDeskChange?: (desk: Desk) => void;
};

/** Light / dark, shared with the rest of the desk (saved in this browser). */
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const light = theme === "light";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={light ? "Switch to the dark theme" : "Switch to the light theme"}
      title={light ? "Dark theme" : "Light theme"}
      className="flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-md text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
    >
      {light ? <Moon size={15} aria-hidden /> : <Sun size={15} aria-hidden />}
    </button>
  );
}

/**
 * Main desk, bots 2-4 and the research desk. Each browser (or `?desk=` link)
 * keeps its own choice. A red dot marks a bot trading LIVE.
 */
function DeskSwitch({ desk, onChange }: { desk: Desk; onChange: (desk: Desk) => void }) {
  const [bots, setBots] = useState<BotSummary[]>([]);
  useEffect(() => {
    let stop = false;
    const pull = () =>
      smaApi
        .bots()
        .then((rows) => !stop && setBots(rows))
        .catch(() => undefined);
    pull();
    const id = setInterval(pull, 15000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, []);
  const byNo = new Map(bots.map((b) => [b.bot, b]));
  const options: { id: Desk; label: string; title: string; live: boolean }[] = [
    ...(["live", "bot2", "bot3", "bot4"] as const).map((id, i) => {
      const info = byNo.get(i + 1);
      const name = info?.name ?? (i === 0 ? "Bot 1" : `Bot ${i + 1}`);
      return {
        id,
        label: name,
        live: info?.mode === "LIVE",
        title: `${name}: ${info?.mode ?? "PAPER"} · ${info?.status?.toLowerCase() ?? "…"}${
          info
            ? ` · ${info.armed.length} armed · today ${(info.gross_today ?? info.net_today) >= 0 ? "+" : ""}₹${(info.gross_today ?? info.net_today).toFixed(2)} before charges`
            : ""
        }`,
      };
    }),
    {
      id: "research",
      label: "Research",
      live: false,
      title: "A paper-only bot on today's live prices, with its own settings and book. Never sends an order.",
    },
  ];
  return (
    <span role="group" aria-label="Bot" className="inline-flex max-w-full shrink-0 overflow-x-auto rounded-md ring-1 ring-inset ring-white/15">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          aria-pressed={desk === o.id}
          title={o.title}
          onClick={() => desk !== o.id && onChange(o.id)}
          className={clsx(
            "flex min-h-9 items-center gap-1.5 whitespace-nowrap px-2.5 text-xs font-semibold first:rounded-l-md last:rounded-r-md",
            desk === o.id
              ? o.id === "research"
                ? "bg-teal-600/30 text-teal-100"
                : "bg-white/10 text-slate-100"
              : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
          )}
        >
          {o.live ? <span aria-label="LIVE" className="h-1.5 w-1.5 rounded-full bg-rose-500" /> : null}
          {o.label}
        </button>
      ))}
    </span>
  );
}

export function Header({ state, config, connected, loadNote, onChanged, notice, desk = "live", onDeskChange, replay, onReplay, tabs, tab = "all" }: Props) {
  const armLimit = desk === "research" ? RESEARCH_ARM_LIMIT : ARM_LIMIT;
  const deskBot: 1 | 2 | 3 | 4 | null =
    desk === "live" ? 1 : desk === "bot2" ? 2 : desk === "bot3" ? 3 : desk === "bot4" ? 4 : null;
  const [copyBots, setCopyBots] = useState<BotSummary[]>([]);
  useEffect(() => {
    if (!deskBot) return;
    smaApi.bots().then(setCopyBots).catch(() => {});
  }, [deskBot]);
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
  // Open P&L before charges (the screens' basis); the net fields stand in on an older server.
  const openOf = (b: (typeof books)[number]) => (b.open_gross !== undefined ? b.open_gross : b.open_net) ?? null;
  const openNet = state?.open_gross_total ?? state?.open_net_total ?? openBooks.reduce((sum, b) => sum + (openOf(b) ?? 0), 0);
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

  // Arming asks for the stock's quantity, stop, trail and target first (ArmPrompt).
  const [armAsk, setArmAsk] = useState<string | null>(null);

  const toggleTrade = async (next: string) => {
    const cleaned = next.trim().toUpperCase();
    if (!cleaned) return;
    const turningOn = !armed.has(cleaned);
    if (turningOn && armedList.length >= armLimit) {
      setError(`Trade is limited to ${armLimit} stocks at once. Turn one off before adding another.`);
      return;
    }
    if (turningOn) {
      setError(null);
      setArmAsk(cleaned);
      return;
    }
    await setTrade(cleaned, false);
  };

  /** Arm or unarm. Arming saves the prompt's changes (`save`) just before. */
  const setTrade = async (cleaned: string, turningOn: boolean, save?: () => Promise<void>) => {
    if (turningOn && live) {
      const ok = window.confirm(
        `Arm ${cleaned} for live SMA orders? The bot can buy or sell it while this chart stays on ${symbol || "the stock you are viewing"}.`
      );
      if (!ok) return;
    }
    setBusy(true);
    setError(null);
    try {
      if (save) await save();
      await smaApi.setTradeSymbol(cleaned, turningOn, turningOn ? "Manual" : null);
      remember(cleaned);
      onChanged();
    } catch (e: unknown) {
      if (save) throw e; // the prompt shows it
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

  const bulkCopyTo = async (toBot: 1 | 2 | 3 | 4, names: string[]) => {
    if (!deskBot || toBot === deskBot || !names.length) return;
    setBusy(true);
    setError(null);
    try {
      const res = await smaApi.copyTradeSymbols(deskBot, toBot, names);
      const notes: string[] = [];
      if (res.added.length) notes.push(`Copied ${res.added.length} to Bot ${toBot}: ${res.added.join(", ")}`);
      for (const s of res.skipped) notes.push(`${s.symbol}: ${s.why}`);
      setError(notes.length ? notes.join(" · ") : null);
      setSelected(new Set());
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Copy refused");
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

  const panicAll = async () => {
    setBusy(true);
    setError(null);
    try {
      await smaApi.killAllBots();
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Panic all failed");
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
      {armAsk ? (
        <ArmPrompt
          symbol={armAsk}
          deskName={state?.bot_name || (desk === "research" ? "Research" : undefined)}
          live={live}
          price={bookBySymbol.get(armAsk)?.ltp ?? (state?.symbol?.toUpperCase() === armAsk ? state?.ltp : null)}
          onArm={(save) => setTrade(armAsk, true, save)}
          onClose={() => setArmAsk(null)}
        />
      ) : null}
      <nav
        aria-label="Desk pages"
        className="sticky top-0 z-40 flex h-11 items-center gap-1 overflow-x-auto border-b border-white/10 bg-[#0B0E14]/95 px-2 backdrop-blur sm:gap-2 sm:px-4"
      >
        <a href="/" className="mr-1 flex min-h-10 min-w-10 shrink-0 items-center gap-2 py-2 pr-2" aria-label="ORB Desk home">
          <span className="grad-brand grid h-7 w-7 place-items-center rounded-full text-white">
            <CandlestickChart size={14} aria-hidden />
          </span>
        </a>
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/terminal";
          return (
            <a
              key={href}
              href={href === "/" ? "/" : `${href}/`}
              aria-current={active ? "page" : undefined}
              className={clsx(
                "flex h-11 shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-2 text-sm font-medium tracking-normal",
                active ? "border-sky-400 text-slate-100" : "border-transparent text-slate-400 hover:text-slate-100"
              )}
            >
              <Icon size={14} aria-hidden className="hidden sm:block" />
              {label}
            </a>
          );
        })}
      </nav>
      <header
        aria-label="SMA terminal"
        className={clsx(
          "sticky top-11 z-30 bg-[#0B0E14]",
          live ? "border-2 border-rose-500 shadow-[0_0_0_1px_rgba(244,63,94,0.25)]" : "border-b border-white/10"
        )}
      >
        <div className="mx-auto flex w-full min-w-0 flex-col gap-2 px-3 py-2 sm:px-4">
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-1">
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="hidden truncate text-sm font-semibold tracking-tight text-slate-100 sm:inline">
                SMA × ATR Terminal
              </span>
              {onDeskChange ? <DeskSwitch desk={desk} onChange={onDeskChange} /> : null}
              <ThemeToggle />
              <WalletChip />
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
          <div className="flex min-w-0 flex-col gap-2 xl:flex-row xl:items-center">
          <div className="min-w-0 xl:flex-1">
          <StatusBar
            state={state}
            config={config}
            connected={connected}
            busy={busy}
            onModeClick={() => (live ? switchMode("PAPER") : setConfirm(true))}
            onResetTrades={state?.mode === "REPLAY" ? undefined : resetTrades}
            onToggleSeconds={state?.mode === "REPLAY" ? undefined : toggleSeconds}
            replay={replay}
            onReplay={onReplay}
          />
          </div>
          {/* Start/Pause and Panic stay in reach while the page scrolls. */}
          <div className="shrink-0">
          <ControlBar
            part="pinned"
            state={state}
            live={live}
            running={running}
            busy={busy}
            symbol={(symbol || config?.symbol || "").toUpperCase()}
            symbolArmed={armed.has((symbol || config?.symbol || "").toUpperCase())}
            onToggleBot={toggleBot}
            onForce={forceOrder}
            onPanic={panic}
            onPanicAll={desk === "research" ? undefined : panicAll}
            botName={state?.bot_name}
          />
          </div>
          </div>
        </div>
        {(error || state?.halt_reason || state?.last_error) && (
          <div role="alert" className="border-t border-rose-500/30 bg-rose-500/10 px-4 py-1.5 text-xs text-rose-300">
            {error || state?.halt_reason || state?.last_error}
          </div>
        )}
        {/* A 1-minute review stays in reach until it is answered or ends. */}
        <ReviewCards state={state} onChanged={onChanged} />
      </header>

      <div className="mx-auto flex w-full min-w-0 flex-col gap-2 px-3 pt-3 sm:px-4">
        {notice}
        {tabs}
        <div className={clsx(!inTab(tab, "live") && "hidden")}>
        <ControlBar
          part="rest"
          state={state}
          live={live}
          running={running}
          busy={busy}
          symbol={(symbol || config?.symbol || "").toUpperCase()}
          symbolArmed={armed.has((symbol || config?.symbol || "").toUpperCase())}
          onToggleBot={toggleBot}
          onForce={forceOrder}
          onPanic={panic}
          onPanicAll={desk === "research" ? undefined : panicAll}
          botName={state?.bot_name}
        />
        </div>
        <div ref={searchRef} className={clsx("relative min-w-0", !inTab(tab, "watchlist") && "hidden")}>
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
                    <span className={clsx("shrink-0 font-semibold", openNet >= 0 ? "text-emerald-300" : "text-rose-300")} title="Unrealized P&L of every open position, before charges">
                      Open {signed(openNet)}
                    </span>
                    <span className="hidden min-w-0 items-center gap-x-3 overflow-hidden sm:flex">
                      {openBooks.map((b) => (
                        <span key={b.symbol} className="shrink-0" title={`${b.symbol} ${b.direction} ${b.qty}${b.entry_price ? ` @ ${px(b.entry_price)}` : ""}`}>
                          <span className="font-sans text-slate-200">{b.symbol}</span>{" "}
                          <span className={b.direction === "LONG" ? "text-sky-300" : "text-violet-300"}>{b.direction === "LONG" ? "L" : "S"}</span>
                          <span className="text-slate-500">×{b.qty}</span>{" "}
                          <span className={(openOf(b) ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>
                            {openOf(b) == null ? "—" : signed(openOf(b) ?? 0)}
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
                      <span className={dir === "LONG" ? "text-sky-300" : dir === "SHORT" ? "text-violet-300" : "text-slate-300"}>
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
                    <span className="truncate text-xs text-slate-400">{hit.name}</span>
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
                  {deskBot ? (
                    <select
                      disabled={busy}
                      value=""
                      onChange={(e) => {
                        const to = Number(e.target.value) as 1 | 2 | 3 | 4;
                        if (to) bulkCopyTo(to, shownSelected);
                      }}
                      title="Copy the ticked stocks to another bot's Trade list. The source bot keeps them."
                      className="min-h-9 rounded-md border border-sky-400/40 bg-transparent px-2 font-semibold text-sky-200 hover:bg-sky-500/10 disabled:opacity-40"
                    >
                      <option value="" className="bg-[#0B0E14]">
                        Copy to…
                      </option>
                      {([1, 2, 3, 4] as const)
                        .filter((n) => n !== deskBot)
                        .map((n) => {
                          const b = copyBots.find((x) => x.bot === n);
                          const label = b?.name ? `Bot ${n} · ${b.name}` : `Bot ${n}`;
                          return (
                            <option key={n} value={n} className="bg-[#0B0E14]">
                              {label}
                            </option>
                          );
                        })}
                    </select>
                  ) : null}
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
            <div className="relative overflow-x-auto px-1 pb-1 sm:px-2">
            <table className="w-full text-left" aria-label="Stocks">
              <thead className="text-xs font-medium uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="w-8 py-1.5 sm:w-10"><span className="sr-only">Select</span></th>
                  <th className="py-1.5 pr-2">Stock</th>
                  <th className="py-1.5 pr-2 text-right">LTP</th>
                  <th className="py-1.5 pr-2 text-right">Chg</th>
                  <th className="hidden py-1.5 pr-2 text-right sm:table-cell">Today</th>
                  <th className="py-1.5 pr-1">Trade</th>
                  <th className="py-1.5 pr-1"><span className="sr-only">Chart</span></th>
                  <th className="py-1.5 pr-1"><span className="sr-only">Remove</span></th>
                </tr>
              </thead>
              <tbody>
              {/* Armed stocks first (display order only). */}
              {[...symbols].sort((a, b) => Number(armed.has(b)) - Number(armed.has(a))).map((s) => {
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
                      openNet: book ? openOf(book) : null,
                      closedNet: book?.closed_gross ?? book?.closed_net ?? 0,
                      closedTrades: book?.closed_trades ?? 0,
                      dayNet: book?.day_gross ?? book?.day_net,
                      reject: book?.last_reject ?? null,
                      ownStrategy: ownSummary(config?.stock_settings?.[s] as Record<string, unknown> | undefined),
                      rejectAt: book?.last_reject_at ?? null,
                      source: config?.trade_sources?.[s] ?? null,
                    }}
                  />
                );
              })}
            </tbody>
            </table>
            </div>
            </>
            )}
            </div>
          </div>
        </div>

      </div>
      {confirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-xl border border-[#F43F5E]/40 bg-[#151921] p-4 sm:p-6">
            <div className="flex gap-3">
              <AlertTriangle className="mt-0.5 text-[#F43F5E]" size={20} />
              <div>
                <h2 className="text-xl font-semibold text-slate-100">
                  Enable LIVE REAL MONEY{state?.bot_name ? ` for ${state.bot_name}` : ""}?
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  Orders will be sent to Groww as NSE MIS limit orders with a 0.20% protection buffer,
                  plus an exchange stop-loss. This uses the Groww login already saved on the desk.
                  Paper mode stays the default until you confirm. Only this bot switches; a stock another
                  LIVE bot trades cannot be armed here.
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
