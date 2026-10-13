"use client";

import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import Link from "next/link";
import { ArrowDown, ArrowUp, BellRing, FlaskConical, Gauge, Loader2, Pause, Play, RefreshCw } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type ScalpMonitorResponse, type ScalpRow } from "@/lib/api";
import { replayActive, smaApi } from "@/lib/smaApi";
import { useArming } from "@/components/Scalp/useArming";
import { useServerRole } from "@/hooks/useServerRole";
import { lastClosedWeekdays } from "@/lib/tradingDays";
import { ScalpPickBacktest } from "@/components/Scalp/ScalpPickBacktest";
import { ScanHistory } from "@/components/Scalp/ScanHistory";
import { MostActive } from "@/components/Scalp/MostActive";
import { ChopScan } from "@/components/Scalp/ChopScan";
import { CrossScan } from "@/components/Scalp/CrossScan";
import type { ScanStatus } from "@/components/Scalp/scanStatus";
import { ColumnHint, inOrder, useColumnOrder } from "@/components/ui/columnOrder";
import { FilterRow, NumberFilter, SelectFilter, TextFilter, Toggle, matchesText } from "@/components/ui/tableTools";

/** 1 to 30 trading days, one at a time. */
const BACKTEST_DAYS = Array.from({ length: 30 }, (_, i) => i + 1);
const MAX_BACKTEST_STOCKS = 24;

const REFRESH_MS = 10_000;

type SortKey =
  | "score"
  | "symbol"
  | "ltp"
  | "ready"
  | "change_pct"
  | "atr_pct"
  | "spread_pct"
  | "volume_ratio"
  | "value_cr"
  | "move_1m_pct"
  | "move_5m_pct"
  | "vwap_dist_pct";

type MonitorCol = SortKey | "terminal";
const MONITOR_COLUMNS: readonly MonitorCol[] = [
  "symbol",
  "score",
  "ltp",
  "change_pct",
  "atr_pct",
  "spread_pct",
  "volume_ratio",
  "value_cr",
  "move_1m_pct",
  "move_5m_pct",
  "vwap_dist_pct",
  "ready",
  "terminal",
];

const TABS = [
  { id: "monitor", label: "Monitor" },
  { id: "cross", label: "Cross scan" },
  { id: "trend", label: "Trend quality" },
  { id: "active", label: "Most active" },
  { id: "backtest", label: "Backtest" },
  { id: "history", label: "History" },
  { id: "alerts", label: "Alerts" },
] as const;
type TabId = (typeof TABS)[number]["id"];
const TAB_STORE = "scalp.tab";
const isTab = (v: string | null | undefined): v is TabId => TABS.some((t) => t.id === v);

export default function ScalpPage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();

  const [data, setData] = useState<ScalpMonitorResponse | null>(null);

  // Sub-tabs: one scanner at a time. Every tab stays mounted, so scans, polls and pop-ups keep running.
  const [tab, setTab] = useState<TabId>("monitor");
  useEffect(() => {
    const fromHash = window.location.hash.replace("#", "");
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(TAB_STORE);
    } catch {
      /* private mode */
    }
    const first = isTab(fromHash) ? fromHash : isTab(saved) ? saved : null;
    if (first) setTab(first);
  }, []);
  const chooseTab = useCallback((next: TabId) => {
    setTab(next);
    try {
      localStorage.setItem(TAB_STORE, next);
    } catch {
      /* private mode */
    }
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${next}`);
  }, []);
  const [crossStatus, setCrossStatus] = useState<ScanStatus | null>(null);
  const [trendStatus, setTrendStatus] = useState<ScanStatus | null>(null);
  const [activeStatus, setActiveStatus] = useState<ScanStatus | null>(null);
  const onCrossStatus = useCallback((st: ScanStatus) => setCrossStatus(st), []);
  const onTrendStatus = useCallback((st: ScanStatus) => setTrendStatus(st), []);
  const onActiveStatus = useCallback((st: ScanStatus) => setActiveStatus(st), []);
  const cols = useColumnOrder<MonitorCol>("scalp.cols.monitor", MONITOR_COLUMNS);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [minAtr, setMinAtr] = useState(0.08);
  const [maxSpread, setMaxSpread] = useState(0.05);
  const [minValue, setMinValue] = useState(5);
  const [readyOnly, setReadyOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [bias, setBias] = useState<"ALL" | "LONG" | "SHORT">("ALL");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "score", desc: true });
  // LTP range: blank = no limit on that side.
  const [ltpMin, setLtpMin] = useState("");
  const [ltpMax, setLtpMax] = useState("");

  // Arm a stock on any SMA bot from here: which bot, then quantity / stop / trail / target (useArming).
  const { armed, arming, armNote, arm, loadDesks, dialogs } = useArming(
    (symbol) => (data?.rows ?? []).find((r) => r.symbol === symbol)?.ltp ?? null
  );

  // Scanner server (scans only, never trades) or a desk whose scans moved there (SCANS=off).
  const role = useServerRole();
  const onScanner = role?.role === "scanner";
  const scansMoved = role?.role === "desk" && !role.scans_enabled;

  // The scanner's Arm opens this page on the desk with ?arm=SYMBOL&from=…: open the arm prompt once.
  const armHandled = useRef(false);
  useEffect(() => {
    if (!role || onScanner || armHandled.current) return;
    armHandled.current = true;
    const params = new URLSearchParams(window.location.search);
    const symbol = (params.get("arm") ?? "").trim().toUpperCase();
    if (!/^[A-Z0-9&_-]{1,30}$/.test(symbol)) return;
    window.history.replaceState(null, "", window.location.pathname);
    arm(symbol, params.get("from") || "Scanner server");
  }, [role, onScanner, arm]);

  // "Backtest these": replay the ticked stocks over past days with the bot.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [btDays, setBtDays] = useState(10);
  const [btBusy, setBtBusy] = useState(false);
  const [btNote, setBtNote] = useState<{ ok: boolean; text: string } | null>(null);

  const [alertScore, setAlertScore] = useState(60);
  const [alertCooldown, setAlertCooldown] = useState(30);
  const [preview, setPreview] = useState<{ symbol: string; message: string }[] | null>(null);
  const [alertBusy, setAlertBusy] = useState(false);

  // "Pause scans": stops every Scalp scan on both servers (desk + SMA terminal) until resumed.
  const [paused, setPaused] = useState(false);
  const [pauseBusy, setPauseBusy] = useState(false);
  const [pauseNote, setPauseNote] = useState<string | null>(null);
  useEffect(() => {
    Promise.allSettled([api.scalpPauseStatus(), smaApi.scansPauseStatus()]).then((res) => {
      setPaused(res.some((r) => r.status === "fulfilled" && r.value.paused));
    });
  }, []);
  const togglePause = async () => {
    const next = !paused;
    setPauseBusy(true);
    setPauseNote(null);
    const res = await Promise.allSettled([api.scalpPause(next), smaApi.scansPause(next)]);
    const failed = res.filter((r) => r.status === "rejected").length;
    setPaused(next || res.some((r) => r.status === "fulfilled" && r.value.paused));
    if (failed)
      setPauseNote(
        failed === 2 ? "Neither server answered; press again to retry." : "One of the two servers did not answer; press again to retry."
      );
    setPauseBusy(false);
  };

  const refresh = useCallback(() => {
    setLoading(true);
    api
      .scalpMonitor({ min_atr_pct: minAtr, max_spread_pct: maxSpread, min_value_cr: minValue, top: 500 })
      .then((next) => {
        setData(next);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
    loadDesks();
  }, [minAtr, maxSpread, minValue, loadDesks]);

  useEffect(() => {
    refresh();
    if (paused) return;
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      refresh();
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh, paused]);

  useEffect(() => {
    if (!data) return;
    setAlertScore(data.alerts.min_score);
    setAlertCooldown(data.alerts.cooldown_minutes);
    // Only on first load; afterwards the inputs belong to the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data === null]);

  // While the pointer or keyboard focus is in the list, rows keep their places (values still update),
  // so a refresh never moves the row you are reading. They re-sort when you leave the list.
  const [holdOrder, setHoldOrder] = useState(false);
  const order = useRef<string[]>([]);

  const sorted = useMemo(() => {
    let list = data?.rows ?? [];
    if (readyOnly) list = list.filter((r) => r.ready);
    if (query.trim()) list = list.filter((r) => matchesText(query, r.symbol));
    if (minScore > 0) list = list.filter((r) => r.score >= minScore);
    if (bias !== "ALL") list = list.filter((r) => r.bias === bias);
    const lo = ltpMin.trim() === "" ? null : Number(ltpMin);
    const hi = ltpMax.trim() === "" ? null : Number(ltpMax);
    if ((lo != null && Number.isFinite(lo)) || (hi != null && Number.isFinite(hi))) {
      list = list.filter(
        (r) =>
          r.ltp != null &&
          (lo == null || !Number.isFinite(lo) || r.ltp >= lo) &&
          (hi == null || !Number.isFinite(hi) || r.ltp <= hi)
      );
    }
    const dir = sort.desc ? -1 : 1;
    return [...list].sort((a, b) => {
      if (sort.key === "symbol") return dir * a.symbol.localeCompare(b.symbol);
      if (sort.key === "ready") return dir * (Number(a.ready) - Number(b.ready)) || b.score - a.score;
      const av = a[sort.key] as number | null;
      const bv = b[sort.key] as number | null;
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return dir * (av - bv);
    });
  }, [data, readyOnly, bias, sort, ltpMin, ltpMax, query, minScore]);

  const rows = useMemo(() => {
    if (!holdOrder || order.current.length === 0) return sorted;
    const place = new Map(order.current.map((s, i) => [s, i]));
    // Rows already shown keep their place; new ones go to the end in sorted order.
    return [...sorted].sort((a, b) => (place.get(a.symbol) ?? Infinity) - (place.get(b.symbol) ?? Infinity));
  }, [sorted, holdOrder]);
  useEffect(() => {
    order.current = rows.map((r) => r.symbol);
  }, [rows]);

  const pick = (symbol: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(symbol);
      else next.delete(symbol);
      return next;
    });

  const backtest = async () => {
    const symbols = Array.from(picked).slice(0, MAX_BACKTEST_STOCKS);
    if (!symbols.length) return;
    const days = lastClosedWeekdays(btDays);
    setBtBusy(true);
    setBtNote(null);
    try {
      const current = await smaApi.replayInfo().catch(() => null);
      if (current && replayActive(current) && current.status !== "FINISHED") {
        const ok = window.confirm("A replay is already running. Stop it and start this backtest instead?");
        if (!ok) return;
      }
      await smaApi.replayStart(days[0], "09:15", 300, days[days.length - 1], symbols);
      setBtNote({
        ok: true,
        text: `Backtest started: ${symbols.join(", ")} · ${days.length === 1 ? days[0] : `${days[0]} → ${days[days.length - 1]}`}. It replays your SMA settings (each stock's own, if set) on Groww's past candles — practice money, no orders. A day takes a few minutes; results build up in the SMA terminal's Backtests tab.`,
      });
    } catch (e: unknown) {
      setBtNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the backtest" });
    } finally {
      setBtBusy(false);
    }
  };

  const saveAlerts = async (enabled: boolean) => {
    setAlertBusy(true);
    try {
      const status = await api.scalpAlerts({ enabled, min_score: alertScore, cooldown_minutes: alertCooldown });
      setData((prev) => (prev ? { ...prev, alerts: status } : prev));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAlertBusy(false);
    }
  };

  const showPreview = async () => {
    setAlertBusy(true);
    try {
      setPreview((await api.scalpAlertPreview()).messages);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAlertBusy(false);
    }
  };

  const header = (key: SortKey, label: string, right = true, title?: string) => (
    <th
      {...cols.dragProps(key)}
      className={clsx(
        "whitespace-nowrap pb-2 font-medium",
        right ? "text-right" : "text-left",
        !right && key !== "symbol" && "pl-3",
        cols.dragProps(key).className
      )}
      title={title}
    >
      <button
        type="button"
        onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "symbol" && key !== "spread_pct" }))}
        className={clsx("inline-flex items-center gap-0.5 hover:text-slate-200", sort.key === key && "text-slate-200")}
      >
        {label}
        {sort.key === key ? sort.desc ? <ArrowDown size={11} /> : <ArrowUp size={11} /> : null}
      </button>
    </th>
  );

  const simulated = data && data.source !== "live";
  const alerts = data?.alerts;
  const tabTimes: Record<TabId, ScanStatus | null> = {
    monitor: data ? { when: data.as_of, running: false } : null,
    cross: crossStatus,
    trend: trendStatus,
    active: activeStatus,
    backtest: null,
    history: null,
    alerts: alerts?.last_run ? { when: alerts.last_run, running: false } : null,
  };

  return (
    <div className="min-h-screen bg-base text-slate-100">
      <Navbar
        connected={connected}
        totalPnl={summaryLoad === "ok" ? summary.total_pnl : null}
        killSwitchActive={killSwitchActive}
        onKillSwitch={killSwitch}
        onResetKillSwitch={resetKillSwitch}
        feed={feed}
        onFeedChanged={setFeed}
        botRunning={bot?.enabled ?? false}
      />

      <main className="w-full space-y-4 px-4 py-6 lg:px-6">
        <header className="space-y-1">
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Gauge size={20} className="text-bot" /> Scalp scanners
          </h1>
          <Explain lead="Which streaming stocks are worth scalping right now. Watch only — nothing here places an order.">
            A stock scores well with enough movement per minute (ATR), a tight bid/ask spread, enough money traded
            today, and something happening in the last few minutes.
          </Explain>
        </header>

        {onScanner ? (
          <section className="rounded-xl border border-violet-500/40 bg-violet-500/10 p-4 text-sm text-violet-300">
            <span className="font-semibold">Scanner server.</span> Scans and alerts run here on their own machine and
            Groww key; this server never sends an order. Arm puts the stock on a bot of the{" "}
            <a href={`${role.desk_url}/scalp/`} target="_blank" rel="noopener noreferrer" className="underline">
              main desk
            </a>{" "}
            (a LIVE bot asks first); the bot orders there on its next SMA cross.
          </section>
        ) : null}

        {scansMoved ? (
          <section className="rounded-xl border border-violet-500/40 bg-violet-500/10 p-4 text-sm text-violet-300">
            <span className="font-semibold">Scans are off on this server</span> so the bots keep it and Groww to
            themselves.{" "}
            {role.scanner_url ? (
              <>
                Run them on the{" "}
                <a href={`${role.scanner_url}/scalp/`} target="_blank" rel="noopener noreferrer" className="underline">
                  scanner server
                </a>
                .
              </>
            ) : (
              "Run them on the scanner server."
            )}{" "}
            Arm still works here.
          </section>
        ) : (
        <section
          className={clsx(
            "flex flex-wrap items-center gap-3 rounded-xl border p-4 text-sm",
            paused ? "border-amber-500/40 bg-amber-500/10" : "border-slate-800 bg-card"
          )}
        >
          <button
            type="button"
            onClick={() => void togglePause()}
            disabled={pauseBusy}
            className={clsx(
              "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold ring-1 ring-inset disabled:opacity-50",
              paused
                ? "bg-amber-500/20 text-amber-300 ring-amber-500/50 hover:bg-amber-500/30"
                : "text-slate-200 ring-slate-700 hover:bg-white/5"
            )}
          >
            {pauseBusy ? <Loader2 size={14} className="animate-spin" /> : paused ? <Play size={14} /> : <Pause size={14} />}
            {paused ? "Resume scans" : "Pause all scans"}
          </button>
          <span className={paused ? "text-amber-300" : "text-slate-400"}>
            {paused
              ? "Scans are paused: no Cross scan, Trend quality, most-active scan, monitor scoring or Scalp alerts, so the bots get the server and Groww to themselves. A restart or deploy resumes them."
              : "Scans compete with the bots for the server and Groww. Pause them while the bots need every second."}
          </span>
          {pauseNote && <span className="text-xs text-loss">{pauseNote}</span>}
        </section>
        )}

        {dialogs}

        <ScalpTabs tab={tab} onTab={chooseTab} times={tabTimes} />

        <div hidden={tab !== "monitor"} className="space-y-4">
          <section className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-slate-800 bg-card p-4 text-sm">
            <span
              className={clsx(
                "rounded px-2 py-0.5 text-xs font-semibold",
                simulated ? "bg-amber-500/15 text-amber-300" : "bg-profit/15 text-profit"
              )}
            >
              {data ? (simulated ? "SIMULATED prices" : "LIVE NSE") : "…"}
            </span>
            {data?.paused ? (
              <span className="text-amber-300">
                {scansMoved
                  ? "Monitor off here — it runs on the scanner server."
                  : "Monitor paused — press Resume scans above to score stocks again."}
              </span>
            ) : data && (
              <span className="text-slate-400">
                <span className="font-semibold text-profit">{data.ready}</span> of {data.universe} streaming stocks
                scalp-ready · {data.market_open ? "market open" : "market closed"} · Last scan{" "}
                {new Date(data.as_of).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })} IST
              </span>
            )}
            <button
              type="button"
              onClick={refresh}
              disabled={loading}
              className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5"
            >
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Refresh
            </button>
          </section>
          {simulated && (
            <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              The desk is on the simulated feed, so these prices are invented and alerts are off. Switch the data source
              to LIVE NSE (top bar) for real readings.
            </p>
          )}
          {error && (
            <p role="alert" className="rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">
              {error}
            </p>
          )}
          {(data?.notes ?? []).map((note) => (
            <p key={note} className="text-xs text-slate-500">
              {note}
            </p>
          ))}
          <section className="rounded-xl border border-slate-800 bg-card p-4">
            <FilterRow>
              <NumberFilter label="Min ATR %/min" value={minAtr} onChange={setMinAtr} min={0} max={0.5} step={0.01} suffix="%" />
              <NumberFilter label="Max spread" value={maxSpread} onChange={setMaxSpread} min={0.01} max={2} step={0.01} suffix="%" />
              <NumberFilter label="Min traded today" value={minValue} onChange={setMinValue} min={0} max={200} step={1} prefix="₹" suffix="cr" />
              <NumberFilter label="Min score" value={minScore} onChange={setMinScore} min={0} max={100} step={1} />
              <TextFilter value={query} onChange={setQuery} />
              <div className="w-44">
                <div className="mb-1 block whitespace-nowrap text-[11px] font-medium text-slate-400">LTP range ₹</div>
                <div className="flex min-h-9 items-stretch rounded-md border border-slate-700 bg-base">
                  <input
                    type="number"
                    inputMode="decimal"
                    min={0}
                    placeholder="min"
                    aria-label="Lowest LTP"
                    value={ltpMin}
                    onChange={(e) => setLtpMin(e.target.value)}
                    className="w-1/2 bg-transparent px-2 py-1 text-sm tabular-nums text-slate-100 outline-none"
                  />
                  <span aria-hidden className="flex items-center text-slate-600">–</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    min={0}
                    placeholder="max"
                    aria-label="Highest LTP"
                    value={ltpMax}
                    onChange={(e) => setLtpMax(e.target.value)}
                    className="w-1/2 bg-transparent px-2 py-1 text-sm tabular-nums text-slate-100 outline-none"
                  />
                  {ltpMin || ltpMax ? (
                    <button
                      type="button"
                      onClick={() => {
                        setLtpMin("");
                        setLtpMax("");
                      }}
                      className="px-2 text-slate-400 hover:text-slate-200"
                      aria-label="Clear the LTP range"
                    >
                      ×
                    </button>
                  ) : null}
                </div>
              </div>
              <SelectFilter
                label="Bias"
                value={bias}
                onChange={(v) => setBias(v as "ALL" | "LONG" | "SHORT")}
                options={[
                  { value: "ALL", label: "All" },
                  { value: "LONG", label: "Long" },
                  { value: "SHORT", label: "Short" },
                ]}
                className="w-28"
              />
              <div className="flex min-h-9 items-end self-end">
                <Toggle label="Scalp-ready only" checked={readyOnly} onChange={setReadyOnly} />
              </div>
            </FilterRow>

            {armNote && <p className="mb-2 text-xs text-accentSky">{armNote}</p>}

            <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-accentViolet/30 bg-accentViolet/[0.06] px-3 py-2 text-xs text-slate-300">
              <FlaskConical size={14} className="text-accentViolet" />
              <span>
                {picked.size
                  ? `${picked.size} ticked`
                  : "Tick stocks below to test how the bot would have traded them"}
              </span>
              {picked.size > 0 && (
                <button type="button" onClick={() => setPicked(new Set())} className="text-slate-400 hover:text-slate-200">
                  Clear
                </button>
              )}
              <label className="ml-auto flex items-center gap-1.5 text-slate-400">
                over the last
                <select
                  value={btDays}
                  onChange={(e) => setBtDays(Number(e.target.value))}
                  className="rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
                >
                  {BACKTEST_DAYS.map((d) => (
                    <option key={d} value={d}>
                      {d} trading day{d === 1 ? "" : "s"}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={btBusy || picked.size === 0}
                onClick={backtest}
                title="Replays the ticked stocks on Groww's past 1-minute candles with your SMA settings. Practice money only; nothing is armed and no order is sent."
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-accentViolet/15 px-3 font-semibold text-accentViolet ring-1 ring-inset ring-accentViolet/40 hover:bg-accentViolet/25 disabled:opacity-40"
              >
                {btBusy ? <Loader2 size={13} className="animate-spin" /> : <FlaskConical size={13} />} Backtest selected
              </button>
            </div>
            {btNote && (
              <p className={clsx("mb-3 text-xs", btNote.ok ? "text-accentViolet" : "text-amber-300")}>
                {btNote.text}{" "}
                {btNote.ok && (
                  <Link href="/terminal" className="font-semibold text-accentSky underline-offset-2 hover:underline">
                    Open the SMA terminal →
                  </Link>
                )}
              </p>
            )}

            <ColumnHint moved={cols.moved} onReset={cols.reset} />
            <div
              className="h-[min(75vh,820px)] min-h-[320px] overflow-auto rounded-lg [overflow-anchor:none]"
              onPointerEnter={() => setHoldOrder(true)}
              onPointerLeave={() => setHoldOrder(false)}
              onFocus={() => setHoldOrder(true)}
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHoldOrder(false);
              }}
            >
            {!data ? (
              <Empty>Loading…</Empty>
            ) : rows.length === 0 ? (
              <Empty>
                {data.universe === 0
                  ? "No stocks are streaming. Add stocks to the watchlist on the Dashboard, or track the full universe on Movers."
                  : "No stock passes these filters. Lower Min ATR or Min traded, or raise Max spread."}
              </Empty>
            ) : (
              <div>
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-10 bg-card text-xs uppercase text-slate-500 shadow-[0_1px_0_rgba(148,163,184,0.15)]">
                    <tr>
                      <th className="w-7 pb-2">
                        <input
                          type="checkbox"
                          aria-label="Tick every stock shown"
                          className="accent-violet-400"
                          checked={rows.length > 0 && rows.every((r) => picked.has(r.symbol))}
                          onChange={(e) =>
                            setPicked(e.target.checked ? new Set(rows.slice(0, MAX_BACKTEST_STOCKS).map((r) => r.symbol)) : new Set())
                          }
                        />
                      </th>
                      {inOrder(cols.order, {
                        symbol: header("symbol", "Stock", false),
                        score: header("score", "Score", true, "0–100: movement 35, volume spike 25, 5-minute move 20, money traded 20; cut by a wide spread"),
                        ltp: header("ltp", "LTP", true, "Last traded price"),
                        change_pct: header("change_pct", "Day"),
                        atr_pct: header("atr_pct", "ATR/min", true, "Wilder ATR(14) on closed 1-minute candles, as % of price"),
                        spread_pct: header("spread_pct", "Spread", true, "Ask minus bid, as % of price"),
                        volume_ratio: header("volume_ratio", "Vol ×", true, "Last closed minute against the 20 before it"),
                        value_cr: header("value_cr", "₹ cr", true, "Money traded today"),
                        move_1m_pct: header("move_1m_pct", "1m"),
                        move_5m_pct: header("move_5m_pct", "5m"),
                        vwap_dist_pct: header("vwap_dist_pct", "vs VWAP"),
                        ready: header("ready", "Status", false, "Ready stocks first (then by score)"),
                        terminal: (
                          <th {...cols.dragProps("terminal")} className={clsx("pb-2 text-right font-medium", cols.dragProps("terminal").className)}>
                            Terminal
                          </th>
                        ),
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <Row
                        key={r.symbol}
                        order={cols.order}
                        row={r}
                        armedOn={armed.get(r.symbol.toUpperCase()) ?? []}
                        arming={arming === r.symbol}
                        onArm={() => arm(r.symbol, "Scalp monitor")}
                        picked={picked.has(r.symbol)}
                        onPick={(on) => pick(r.symbol, on)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            </div>
            <p className="mt-2 text-[11px] text-slate-500" aria-live="polite">
              {rows.length} stock{rows.length === 1 ? "" : "s"} shown
              {holdOrder ? " · order held while you point at the list; it re-sorts when you move away" : ""}
            </p>
            <p className="mt-3 text-[11px] leading-snug text-slate-500">
              The list is every stock the desk is streaming. Readings use closed 1-minute candles; LTP, spread and the
              1-minute move use the live quote. Bias is LONG when price is above VWAP and up over 5 minutes, SHORT when
              below and down. “Arm” adds the stock to the SMA terminal&apos;s Trade list — the bot still waits for its
              SMA cross and filters.
            </p>
          </section>
        </div>
        <div hidden={tab !== "cross"}>
          <CrossScan armed={armed} arming={arming} onArm={(s) => arm(s, "Cross scan")} paused={paused} onStatus={onCrossStatus} />
        </div>
        <div hidden={tab !== "trend"}>
          <ChopScan armed={armed} arming={arming} onArm={(s) => arm(s, "Trend quality")} paused={paused} onStatus={onTrendStatus} />
        </div>
        <div hidden={tab !== "active"}>
          <MostActive
            watching={new Set((data?.rows ?? []).map((r) => r.symbol))}
            armed={armed}
            arming={arming}
            onArm={(s) => arm(s, "Scalp monitor")}
            onWatched={refresh}
            paused={paused}
            onStatus={onActiveStatus}
          />
        </div>
        <div hidden={tab !== "backtest"}>
          <ScalpPickBacktest universe={(data?.rows ?? []).map((r) => r.symbol)} minAtr={minAtr} minValue={minValue} />
        </div>
        <div hidden={tab !== "history"}>{tab === "history" && <ScanHistory />}</div>
        <div hidden={tab !== "alerts"}>
          <section className="rounded-xl border border-slate-800 bg-card p-4">
            <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
              <BellRing size={15} className="text-bot" /> Telegram scalp alerts
            </h2>
            <p className="mb-3 max-w-3xl text-xs text-slate-500">
              Once a minute during market hours on LIVE prices, sends a Telegram when a stock is scalp-ready and scores
              at least the minimum. Each stock alerts at most once per cooldown. Watch only — no order is placed. Uses
              the alert channel set up on Settings. Turns off again if the server restarts.
            </p>
            <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
              <label className="flex flex-col gap-1">
                Min score
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={alertScore}
                  onChange={(e) => setAlertScore(Number(e.target.value))}
                  className="w-20 rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
                />
              </label>
              <label className="flex flex-col gap-1">
                Cooldown (min)
                <input
                  type="number"
                  min={1}
                  max={600}
                  value={alertCooldown}
                  onChange={(e) => setAlertCooldown(Number(e.target.value))}
                  className="w-20 rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
                />
              </label>
              <button
                type="button"
                disabled={alertBusy}
                onClick={() => saveAlerts(!alerts?.enabled)}
                className={clsx(
                  "min-h-9 rounded-md px-3 text-xs font-semibold",
                  alerts?.enabled ? "bg-loss/20 text-loss hover:bg-loss/30" : "bg-profit/20 text-profit hover:bg-profit/30"
                )}
              >
                {alerts?.enabled ? "Turn alerts off" : "Turn alerts on"}
              </button>
              {alerts?.enabled && (
                <button
                  type="button"
                  disabled={alertBusy}
                  onClick={() => saveAlerts(true)}
                  className="min-h-9 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-white/5"
                >
                  Save settings
                </button>
              )}
              <button
                type="button"
                disabled={alertBusy}
                onClick={showPreview}
                className="min-h-9 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-white/5"
              >
                Preview (no send)
              </button>
            </div>
            {alerts && (
              <p className="mt-2 text-xs text-slate-500">
                {alerts.enabled ? (
                  <span className="font-semibold text-profit">On</span>
                ) : (
                  <span className="font-semibold text-slate-400">Off</span>
                )}
                {alerts.last_run && ` · last check ${new Date(alerts.last_run).toLocaleTimeString("en-IN")}`}
                {alerts.sent_today.length > 0 && ` · sent today: ${alerts.sent_today.join(", ")}`}
                {alerts.last_error && <span className="text-amber-300"> · {alerts.last_error}</span>}
              </p>
            )}
            {preview && (
              <div className="mt-3 space-y-2">
                {preview.length === 0 ? (
                  <p className="text-xs text-slate-500">Nothing would be sent right now.</p>
                ) : (
                  preview.map((p) => (
                    <pre key={p.symbol} className="whitespace-pre-wrap rounded-lg border border-slate-800 bg-base/60 p-3 font-sans text-xs text-slate-300">
                      {p.message}
                    </pre>
                  ))
                )}
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}

function Row({
  order,
  row: r,
  armedOn,
  arming,
  onArm,
  picked,
  onPick,
}: {
  order: readonly MonitorCol[];
  row: ScalpRow;
  /** The desks this stock is armed on. */
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
  picked: boolean;
  onPick: (on: boolean) => void;
}) {
  return (
    <tr className={clsx("border-t border-slate-800/70", r.ready && "bg-profit/[0.04]", picked && "bg-accentViolet/[0.08]")}>
      {inOrder(["pick" as const, ...order], {
        pick: (
          <td className="py-1.5">
            <input
              type="checkbox"
              aria-label={`Tick ${r.symbol} for a backtest`}
              className="accent-violet-400"
              checked={picked}
              onChange={(e) => onPick(e.target.checked)}
            />
          </td>
        ),
        symbol: (
          <td className="py-1.5 font-semibold text-amber-300">{r.symbol}</td>
        ),
        score: (
          <td className="py-1.5 text-right">
            <ScoreBar value={r.score} />
          </td>
        ),
        ltp: (
          <td className="py-1.5 text-right tabular-nums">{r.ltp == null ? "—" : r.ltp.toFixed(2)}</td>
        ),
        change_pct: (
          <td className="py-1.5 text-right">
            <Pct value={r.change_pct} />
          </td>
        ),
        atr_pct: (
          <td className="py-1.5 text-right tabular-nums">{r.atr_pct == null ? "—" : `${r.atr_pct.toFixed(3)}%`}</td>
        ),
        spread_pct: (
          <td className="py-1.5 text-right tabular-nums">{r.spread_pct == null ? "—" : `${r.spread_pct.toFixed(3)}%`}</td>
        ),
        volume_ratio: (
          <td
            className={clsx(
              "py-1.5 text-right tabular-nums",
              r.volume_ratio != null && r.volume_ratio >= 2 ? "font-semibold text-accentSky" : ""
            )}
          >
            {r.volume_ratio == null ? "—" : `${r.volume_ratio.toFixed(1)}×`}
          </td>
        ),
        value_cr: (
          <td className="py-1.5 text-right tabular-nums">{r.value_cr == null ? "—" : r.value_cr.toFixed(1)}</td>
        ),
        move_1m_pct: (
          <td className="py-1.5 text-right">
            <Pct value={r.move_1m_pct} />
          </td>
        ),
        move_5m_pct: (
          <td className="py-1.5 text-right">
            <Pct value={r.move_5m_pct} />
          </td>
        ),
        vwap_dist_pct: (
          <td className="py-1.5 text-right">
            <Pct value={r.vwap_dist_pct} />
          </td>
        ),
        ready: (
          <td className="max-w-[16rem] py-1.5 pl-3 text-xs">
            {r.ready ? (
              <span className="inline-flex items-center gap-1.5">
                <span className="rounded bg-profit/15 px-1.5 py-0.5 font-semibold text-profit">READY</span>
                {r.bias !== "NONE" && (
                  <span className={r.bias === "LONG" ? "text-profit" : "text-loss"}>{r.bias}</span>
                )}
              </span>
            ) : (
              <span className="block truncate text-slate-500" title={r.reasons.join("; ")}>
                {r.reasons[0]}
                {r.reasons.length > 1 ? ` +${r.reasons.length - 1}` : ""}
              </span>
            )}
          </td>
        ),
        terminal: (
          <td className="py-1.5 text-right">
            <span className="inline-flex items-center justify-end gap-1.5">
              {armedOn.length ? (
                <span className="text-[11px] font-semibold text-profit" title={`Armed on ${armedOn.join(", ")}`}>
                  {armedOn.join(" · ")}
                </span>
              ) : null}
              <button
                type="button"
                disabled={arming}
                onClick={onArm}
                title="Arm on a bot (asks which)"
                className="min-h-8 rounded-md px-2.5 text-xs font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/10 disabled:opacity-50"
              >
                {arming ? "…" : "Arm"}
              </button>
            </span>
          </td>
        ),
      })}
    </tr>
  );
}

function ScoreBar({ value }: { value: number }) {
  return (
    <span className="inline-flex items-center justify-end gap-1.5">
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-800">
        <span
          className={clsx("block h-full", value >= 60 ? "bg-profit" : value >= 35 ? "bg-amber-400" : "bg-slate-500")}
          style={{ width: `${Math.max(0, Math.min(value, 100))}%` }}
        />
      </span>
      <span className="w-7 text-right tabular-nums">{value.toFixed(0)}</span>
    </span>
  );
}

function Pct({ value }: { value: number | null }) {
  if (value == null) return <span className="text-slate-500">—</span>;
  return (
    <span className={clsx("tabular-nums", value > 0 ? "text-profit" : value < 0 ? "text-loss" : "text-slate-400")}>
      {value > 0 ? "+" : ""}
      {value.toFixed(2)}%
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-[160px] items-center justify-center rounded-lg border border-dashed border-slate-800 px-4 py-6 text-center text-sm text-slate-500">
      {children}
    </div>
  );
}

const shortTime = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

/** The Scalp page's scanners as tabs; each shows when it last scanned (or that it is scanning now). */
function ScalpTabs({ tab, onTab, times }: { tab: TabId; onTab: (t: TabId) => void; times: Record<TabId, ScanStatus | null> }) {
  return (
    <div role="tablist" aria-label="Scalp scanners" className="flex flex-wrap gap-1 border-b border-slate-800">
      {TABS.map((t) => {
        const on = tab === t.id;
        const st = times[t.id];
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onTab(t.id)}
            title={st?.when ? `Last scan ${new Date(st.when).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST` : undefined}
            className={clsx(
              "-mb-px inline-flex min-h-10 items-center gap-2 rounded-t-md border-b-2 px-3 text-sm font-semibold",
              on ? "border-accentSky text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"
            )}
          >
            {t.label}
            {st?.running ? (
              <span className="inline-flex items-center gap-1 text-[11px] font-normal text-accentSky">
                <Loader2 size={11} className="animate-spin" aria-hidden /> scanning
              </span>
            ) : st?.when ? (
              <span className="text-[11px] font-normal tabular-nums text-slate-500">{shortTime(st.when)}</span>
            ) : t.id !== "backtest" && t.id !== "history" ? (
              <span className="text-[11px] font-normal text-slate-600">no scan yet</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
