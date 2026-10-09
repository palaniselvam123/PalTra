"use client";

import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Crosshair, Loader2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { smaApi, type CrossScan as CrossScanData, type CrossScanMinutes, type CrossScanRow } from "@/lib/smaApi";

const CANDLE_CHOICES: CrossScanMinutes[] = [1, 2, 3, 5, 10, 15];
const WITHIN_STOPS = [10, 15, 30, 60, 120]; // minutes
const POLL_MS = 3_000;
const RECHECK_MS = 30_000; // a new candle may have closed: the server skips a scan it already has

type Props = {
  /** Stock → the SMA bots it is armed on. */
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
};

type Side = "ALL" | "BULLISH" | "BEARISH";

const num = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));

/**
 * Stocks whose SMA fast / slow are about to cross on the chosen candle (5 minutes
 * by default), ranked soonest first. The SMAs and the "minutes to cross" are
 * worked out from Groww's candles exactly as the bot does it. Display only: it
 * places no order, and Arm only puts a stock on a bot's Trade list.
 */
export function CrossScan({ armed, arming, onArm }: Props) {
  const [data, setData] = useState<CrossScanData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minutes, setMinutes] = useState<CrossScanMinutes>(5);
  const [side, setSide] = useState<Side>("ALL");
  const [within, setWithin] = useState(30);
  const [showCrossed, setShowCrossed] = useState(true);
  const [busy, setBusy] = useState(false);
  const universe = useRef<string[] | null>(null);

  const start = useCallback(
    async (force = false) => {
      setBusy(true);
      setError(null);
      try {
        if (!universe.current || universe.current.length === 0) {
          const u = await api.scalpUniverse();
          if (u.symbols.length === 0) throw new Error(u.error || "The F&O stock list is empty, so there is nothing to scan.");
          universe.current = u.symbols;
        }
        setData(await smaApi.crossScanStart(universe.current, minutes, force));
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Could not start the scan");
      } finally {
        setBusy(false);
      }
    },
    [minutes]
  );

  // Start when the section opens or the candle size changes; the server keeps one result per candle.
  useEffect(() => {
    void start(false);
  }, [start]);

  // While a pass runs, watch its progress.
  useEffect(() => {
    if (!data?.running) return;
    const id = setInterval(() => {
      smaApi
        .crossScan()
        .then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Lost the scan"));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [data?.running]);

  // While the market is open, look again for a newly closed candle.
  useEffect(() => {
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      if (data?.market_open) void start(false);
    }, RECHECK_MS);
    return () => clearInterval(id);
  }, [data?.market_open, start]);

  const rows = (data?.rows ?? []).filter((r) => {
    if (side !== "ALL" && r.side !== side) return false;
    if (r.state === "CROSSED") return showCrossed;
    return (r.minutes_to_cross ?? Infinity) <= within;
  });
  const asOf = data?.as_of
    ? new Date(data.as_of).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) + " IST"
    : null;
  const label = data ? `SMA ${data.sma_fast}/${data.sma_slow}` : "SMA";

  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Crosshair size={15} className="text-accentSky" /> Cross scan — F&amp;O stocks about to cross on the {minutes}-minute candle
        </h2>
        <button
          type="button"
          onClick={() => void start(true)}
          disabled={busy || data?.running}
          className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
        >
          {busy || data?.running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
        </button>
      </div>
      <Explain
        className="mb-3"
        lead={`Stocks whose ${label} are closing in on each other, soonest first. Candles only — nothing here places an order.`}
      >
        Groww has no indicator feed, so the averages are worked out here from Groww&apos;s 1-minute candles, grouped into {minutes}
        -minute candles from 09:15 and averaged exactly as the bot does it, on closed candles only (the candle still forming is
        ignored). &quot;In&quot; is how long the cross is away if the gap keeps closing at the pace of the last few candles; it is an estimate,
        not a promise, and a stock can turn away. Arm puts a stock on a bot&apos;s Trade list; it orders only on that bot&apos;s own
        cross.
      </Explain>

      <div className="mb-3 flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Candle
          <select
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value) as CrossScanMinutes)}
            className="min-h-9 rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
          >
            {CANDLE_CHOICES.map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Direction
          <select
            value={side}
            onChange={(e) => setSide(e.target.value as Side)}
            className="min-h-9 rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
          >
            <option value="ALL">Both</option>
            <option value="BULLISH">Bullish (fast above slow)</option>
            <option value="BEARISH">Bearish (fast below slow)</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Crossing within
          <select
            value={within}
            onChange={(e) => setWithin(Number(e.target.value))}
            className="min-h-9 rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
          >
            {WITHIN_STOPS.map((m) => (
              <option key={m} value={m}>
                {m < 60 ? `${m} min` : `${m / 60} h`}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-h-9 items-center gap-2 self-end text-slate-300">
          <input type="checkbox" className="h-5 w-5 accent-sky-400" checked={showCrossed} onChange={(e) => setShowCrossed(e.target.checked)} />
          Also show stocks that just crossed
        </label>
        {data && (
          <span className="ml-auto">
            {data.running
              ? `Scanning ${data.done} of ${data.total}…`
              : asOf
                ? `Closed candles to ${asOf}${data.market_open ? "" : " · market closed"}`
                : "No scan yet"}
          </span>
        )}
      </div>

      {error && <p className="mb-2 text-xs text-loss">{error}</p>}
      {data?.error && !error && (
        <p className="mb-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{data.error}</p>
      )}
      {rows.length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-500">
          {data?.running
            ? "Scanning…"
            : data?.error && (data.failed ?? 0) > 0 && (data.done ?? 0) <= (data.failed ?? 0)
              ? "The scan could not read any stock, so there is nothing to show yet."
              : data?.as_of
                ? `No stock is within ${within < 60 ? `${within} minutes` : `${within / 60} h`} of crossing right now. Widen "Crossing within", or check back after the next candle closes.`
                : "Starting the scan…"}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
              <tr>
                <th className="pb-2 text-left font-medium">Stock</th>
                <th className="pb-2 text-left font-medium">Cross</th>
                <th className="pb-2 text-right font-medium" title="Estimated time to the cross at the pace of the last few candles">
                  In
                </th>
                <th className="pb-2 text-right font-medium">LTP</th>
                <th className="pb-2 text-right font-medium" title="(fast SMA − slow SMA) / slow SMA, on the last closed candle">
                  Gap %
                </th>
                <th className="pb-2 text-right font-medium" title="How much the gap changes each candle">
                  Closing / candle
                </th>
                <th className="pb-2 text-right font-medium">{label}</th>
                <th className="pb-2 text-right font-medium">Arm</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Row key={r.symbol} row={r} armedOn={armed.get(r.symbol.toUpperCase()) ?? []} arming={arming === r.symbol} onArm={() => onArm(r.symbol)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Row({ row: r, armedOn, arming, onArm }: { row: CrossScanRow; armedOn: string[]; arming: boolean; onArm: () => void }) {
  const crossed = r.state === "CROSSED";
  const bullish = r.side === "BULLISH";
  return (
    <tr className="whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2">
      <td className="py-1.5 font-semibold text-slate-100">{r.symbol}</td>
      <td className="py-1.5">
        <span
          className={clsx(
            "rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
            bullish ? "bg-sky-500/15 text-sky-200 ring-sky-400/40" : "bg-violet-500/15 text-violet-200 ring-violet-400/40"
          )}
          title={bullish ? "The fast SMA is moving above the slow SMA" : "The fast SMA is moving below the slow SMA"}
        >
          {bullish ? "Bullish ↑" : "Bearish ↓"}
        </span>
      </td>
      <td className="py-1.5 text-right tabular-nums">
        {crossed ? (
          <span className="text-amber-300">{r.crossed_candles_ago === 0 ? "just crossed" : "crossed 1 candle ago"}</span>
        ) : (
          <span title={`${num(r.candles_to_cross, 1)} candles`}>~{Math.max(1, Math.round(r.minutes_to_cross ?? 0))} min</span>
        )}
      </td>
      <td className="py-1.5 text-right tabular-nums">{num(r.ltp)}</td>
      <td className="py-1.5 text-right tabular-nums">
        {r.gap_pct >= 0 ? "+" : ""}
        {num(r.gap_pct, 3)}%
      </td>
      <td className="py-1.5 text-right tabular-nums text-slate-400">
        {r.slope_pct >= 0 ? "+" : ""}
        {num(r.slope_pct, 3)}
      </td>
      <td className="py-1.5 text-right tabular-nums text-slate-400">
        {num(r.sma_fast)} / {num(r.sma_slow)}
      </td>
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
            aria-label={`Arm ${r.symbol} on a bot`}
            title="Arm on a bot (asks which)"
            className="min-h-9 rounded-md px-2.5 text-xs font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/10 disabled:opacity-50"
          >
            {arming ? "…" : "Arm"}
          </button>
        </span>
      </td>
    </tr>
  );
}
