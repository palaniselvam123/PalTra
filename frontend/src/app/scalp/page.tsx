"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import Link from "next/link";
import { ArrowDown, ArrowUp, BellRing, FlaskConical, Gauge, Loader2, RefreshCw } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type ScalpMonitorResponse, type ScalpRow } from "@/lib/api";
import { replayActive, smaApi, type SmaConfig } from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";
import { ScalpPickBacktest } from "@/components/Scalp/ScalpPickBacktest";
import { MostActive } from "@/components/Scalp/MostActive";

const BACKTEST_DAYS = [1, 5, 10, 20];
const MAX_BACKTEST_STOCKS = 24;

const REFRESH_MS = 10_000;
const ATR_STOPS = [0, 0.03, 0.05, 0.08, 0.1, 0.15, 0.2];
const SPREAD_STOPS = [0.02, 0.03, 0.05, 0.1, 0.2, 1];
const VALUE_STOPS = [0, 1, 5, 10, 25, 50, 100];

type SortKey =
  | "score"
  | "symbol"
  | "change_pct"
  | "atr_pct"
  | "spread_pct"
  | "volume_ratio"
  | "value_cr"
  | "move_1m_pct"
  | "move_5m_pct"
  | "vwap_dist_pct";

export default function ScalpPage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();

  const [data, setData] = useState<ScalpMonitorResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [minAtr, setMinAtr] = useState(0.08);
  const [maxSpread, setMaxSpread] = useState(0.05);
  const [minValue, setMinValue] = useState(5);
  const [readyOnly, setReadyOnly] = useState(false);
  const [bias, setBias] = useState<"ALL" | "LONG" | "SHORT">("ALL");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "score", desc: true });

  // The SMA terminal's Trade list, so a stock can be armed from here.
  const [smaConfig, setSmaConfig] = useState<SmaConfig | null>(null);
  const [arming, setArming] = useState<string | null>(null);
  const [armNote, setArmNote] = useState<string | null>(null);

  // "Backtest these": replay the ticked stocks over past days with the bot.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [btDays, setBtDays] = useState(10);
  const [btBusy, setBtBusy] = useState(false);
  const [btNote, setBtNote] = useState<{ ok: boolean; text: string } | null>(null);

  const [alertScore, setAlertScore] = useState(60);
  const [alertCooldown, setAlertCooldown] = useState(30);
  const [preview, setPreview] = useState<{ symbol: string; message: string }[] | null>(null);
  const [alertBusy, setAlertBusy] = useState(false);

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
    smaApi.config().then(setSmaConfig).catch(() => {});
  }, [minAtr, maxSpread, minValue]);

  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      refresh();
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (!data) return;
    setAlertScore(data.alerts.min_score);
    setAlertCooldown(data.alerts.cooldown_minutes);
    // Only on first load; afterwards the inputs belong to the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data === null]);

  const armed = useMemo(
    () => new Set((smaConfig?.trade_symbols ?? []).map((s) => s.toUpperCase())),
    [smaConfig]
  );

  const rows = useMemo(() => {
    let list = data?.rows ?? [];
    if (readyOnly) list = list.filter((r) => r.ready);
    if (bias !== "ALL") list = list.filter((r) => r.bias === bias);
    const dir = sort.desc ? -1 : 1;
    return [...list].sort((a, b) => {
      if (sort.key === "symbol") return dir * a.symbol.localeCompare(b.symbol);
      const av = a[sort.key] as number | null;
      const bv = b[sort.key] as number | null;
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return dir * (av - bv);
    });
  }, [data, readyOnly, bias, sort]);

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

  const arm = async (symbol: string) => {
    const live = (smaConfig?.trading_mode ?? "PAPER") === "LIVE";
    if (live) {
      const ok = window.confirm(
        `Arm ${symbol} for LIVE SMA orders? The bot can buy or sell it with real money on its next SMA cross.`
      );
      if (!ok) return;
    }
    setArming(symbol);
    setArmNote(null);
    try {
      const next = await smaApi.setTradeSymbol(symbol, true);
      setSmaConfig(next);
      setArmNote(`${symbol} is armed in the SMA terminal. It orders on its next SMA cross, not now.`);
    } catch (e: unknown) {
      setArmNote(e instanceof Error ? e.message : "Could not arm that stock");
    } finally {
      setArming(null);
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
    <th className={clsx("whitespace-nowrap pb-2 font-medium", right ? "text-right" : "text-left")} title={title}>
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

      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        <header className="space-y-1">
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Gauge size={20} className="text-bot" /> Scalp monitor
          </h1>
          <p className="max-w-3xl text-sm text-slate-400">
            Which streaming stocks are worth scalping right now: enough movement per minute (ATR), a tight bid/ask
            spread, enough money traded today, and something happening in the last few minutes. Watch only — nothing
            here places an order.
          </p>
        </header>

        <section className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-slate-800 bg-card p-4 text-sm">
          <span
            className={clsx(
              "rounded px-2 py-0.5 text-xs font-semibold",
              simulated ? "bg-amber-500/15 text-amber-300" : "bg-profit/15 text-profit"
            )}
          >
            {data ? (simulated ? "SIMULATED prices" : "LIVE NSE") : "…"}
          </span>
          {data && (
            <span className="text-slate-400">
              <span className="font-semibold text-profit">{data.ready}</span> of {data.universe} streaming stocks
              scalp-ready · {data.market_open ? "market open" : "market closed"} · updated{" "}
              {new Date(data.as_of).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
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

        <MostActive
          watching={new Set((data?.rows ?? []).map((r) => r.symbol))}
          armed={armed}
          arming={arming}
          onArm={arm}
          onWatched={refresh}
        />

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
          <div className="mb-3 flex flex-wrap items-end gap-3 text-xs text-slate-400">
            <Select label="Min ATR %/min" value={minAtr} options={ATR_STOPS} suffix="%" onChange={setMinAtr} />
            <Select label="Max spread" value={maxSpread} options={SPREAD_STOPS} suffix="%" onChange={setMaxSpread} />
            <Select label="Min traded today" value={minValue} options={VALUE_STOPS} prefix="₹" suffix=" cr" onChange={setMinValue} />
            <label className="flex flex-col gap-1">
              Bias
              <select
                value={bias}
                onChange={(e) => setBias(e.target.value as "ALL" | "LONG" | "SHORT")}
                className="rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
              >
                <option value="ALL">All</option>
                <option value="LONG">Long (above VWAP, rising)</option>
                <option value="SHORT">Short (below VWAP, falling)</option>
              </select>
            </label>
            <label className="flex min-h-9 cursor-pointer items-center gap-1.5 self-end">
              <input
                type="checkbox"
                checked={readyOnly}
                onChange={(e) => setReadyOnly(e.target.checked)}
                className="accent-bot"
              />
              Scalp-ready only
            </label>
          </div>

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

          {!data ? (
            <Empty>Loading…</Empty>
          ) : rows.length === 0 ? (
            <Empty>
              {data.universe === 0
                ? "No stocks are streaming. Add stocks to the watchlist on the Dashboard, or track the full universe on Movers."
                : "No stock passes these filters. Lower Min ATR or Min traded, or raise Max spread."}
            </Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase text-slate-500">
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
                    {header("symbol", "Stock", false)}
                    {header("score", "Score", true, "0–100: movement 35, volume spike 25, 5-minute move 20, money traded 20; cut by a wide spread")}
                    <th className="pb-2 text-right font-medium">LTP</th>
                    {header("change_pct", "Day")}
                    {header("atr_pct", "ATR/min", true, "Wilder ATR(14) on closed 1-minute candles, as % of price")}
                    {header("spread_pct", "Spread", true, "Ask minus bid, as % of price")}
                    {header("volume_ratio", "Vol ×", true, "Last closed minute against the 20 before it")}
                    {header("value_cr", "₹ cr", true, "Money traded today")}
                    {header("move_1m_pct", "1m")}
                    {header("move_5m_pct", "5m")}
                    {header("vwap_dist_pct", "vs VWAP")}
                    <th className="pb-2 text-left font-medium">Status</th>
                    <th className="pb-2 text-right font-medium">Terminal</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <Row
                      key={r.symbol}
                      row={r}
                      armed={armed.has(r.symbol)}
                      arming={arming === r.symbol}
                      onArm={() => arm(r.symbol)}
                      picked={picked.has(r.symbol)}
                      onPick={(on) => pick(r.symbol, on)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 text-[11px] leading-snug text-slate-500">
            The list is every stock the desk is streaming. Readings use closed 1-minute candles; LTP, spread and the
            1-minute move use the live quote. Bias is LONG when price is above VWAP and up over 5 minutes, SHORT when
            below and down. “Arm” adds the stock to the SMA terminal&apos;s Trade list — the bot still waits for its
            SMA cross and filters.
          </p>
        </section>

        <ScalpPickBacktest universe={(data?.rows ?? []).map((r) => r.symbol)} minAtr={minAtr} minValue={minValue} />

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
      </main>
    </div>
  );
}

function Row({
  row: r,
  armed,
  arming,
  onArm,
  picked,
  onPick,
}: {
  row: ScalpRow;
  armed: boolean;
  arming: boolean;
  onArm: () => void;
  picked: boolean;
  onPick: (on: boolean) => void;
}) {
  return (
    <tr className={clsx("border-t border-slate-800/70", r.ready && "bg-profit/[0.04]", picked && "bg-accentViolet/[0.08]")}>
      <td className="py-1.5">
        <input
          type="checkbox"
          aria-label={`Tick ${r.symbol} for a backtest`}
          className="accent-violet-400"
          checked={picked}
          onChange={(e) => onPick(e.target.checked)}
        />
      </td>
      <td className="py-1.5 font-semibold text-amber-300">{r.symbol}</td>
      <td className="py-1.5 text-right">
        <ScoreBar value={r.score} />
      </td>
      <td className="py-1.5 text-right tabular-nums">{r.ltp == null ? "—" : r.ltp.toFixed(2)}</td>
      <td className="py-1.5 text-right">
        <Pct value={r.change_pct} />
      </td>
      <td className="py-1.5 text-right tabular-nums">{r.atr_pct == null ? "—" : `${r.atr_pct.toFixed(3)}%`}</td>
      <td className="py-1.5 text-right tabular-nums">{r.spread_pct == null ? "—" : `${r.spread_pct.toFixed(3)}%`}</td>
      <td
        className={clsx(
          "py-1.5 text-right tabular-nums",
          r.volume_ratio != null && r.volume_ratio >= 2 ? "font-semibold text-accentSky" : ""
        )}
      >
        {r.volume_ratio == null ? "—" : `${r.volume_ratio.toFixed(1)}×`}
      </td>
      <td className="py-1.5 text-right tabular-nums">{r.value_cr == null ? "—" : r.value_cr.toFixed(1)}</td>
      <td className="py-1.5 text-right">
        <Pct value={r.move_1m_pct} />
      </td>
      <td className="py-1.5 text-right">
        <Pct value={r.move_5m_pct} />
      </td>
      <td className="py-1.5 text-right">
        <Pct value={r.vwap_dist_pct} />
      </td>
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
      <td className="py-1.5 text-right">
        {armed ? (
          <span className="text-xs font-semibold text-profit">Armed</span>
        ) : (
          <button
            type="button"
            disabled={arming}
            onClick={onArm}
            className="min-h-8 rounded-md px-2.5 text-xs font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/10 disabled:opacity-50"
          >
            {arming ? "…" : "Arm"}
          </button>
        )}
      </td>
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

function Select({
  label,
  value,
  options,
  onChange,
  prefix = "",
  suffix = "",
}: {
  label: string;
  value: number;
  options: number[];
  onChange: (v: number) => void;
  prefix?: string;
  suffix?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
      >
        {options.map((v) => (
          <option key={v} value={v}>
            {prefix}
            {v}
            {suffix}
          </option>
        ))}
      </select>
    </label>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-800 px-4 py-6 text-center text-sm text-slate-500">
      {children}
    </div>
  );
}
