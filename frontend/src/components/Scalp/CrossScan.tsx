"use client";

import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { Crosshair, Loader2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { smaApi, type CrossScan as CrossScanData, type CrossScanMinutes, type CrossScanRow } from "@/lib/smaApi";
import { NumberFilter, TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";

const CANDLE_CHOICES: CrossScanMinutes[] = [1, 2, 3, 5, 10, 15];
const POLL_MS = 3_000;

type Props = {
  /** Stock → the SMA bots it is armed on. */
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
};

type Side = "ALL" | "BULLISH" | "BEARISH";
type SortKey = "symbol" | "side" | "mins" | "ltp" | "gap" | "slope" | "speed" | "volume" | "volWin";

const num = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));

/** Shares traded, short Indian style: 8,450 · 1.2 L · 3.4 Cr; "—" when not reported. */
function shares(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 1e7) return `${(v / 1e7).toFixed(1)} Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(1)} L`;
  return Math.round(v).toLocaleString("en-IN");
}

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
  const [query, setQuery] = useState("");
  // Fast movers: stocks whose price has moved at least this fast (% per minute) over the last few minutes.
  const [fastOnly, setFastOnly] = useState(false);
  const [minSpeed, setMinSpeed] = useState(0.1);
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

  // Nothing is scanned until "Scan now" is pressed. Opening the page only looks at the last result the
  // server already holds (one pass is hundreds of downloads, so it never starts by itself).
  useEffect(() => {
    let live = true;
    smaApi
      .crossScan()
      .then((d) => live && setData(d))
      .catch(() => {
        /* the terminal is not answering: the page shows "No scan yet" and Scan now still works */
      });
    return () => {
      live = false;
    };
  }, []);

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

  const speedOf = useCallback((r: CrossScanRow) => (r.speed_pct_per_min == null ? null : Math.abs(r.speed_pct_per_min)), []);

  // The server holds one result, for the candle size it last scanned.
  const scannedThisCandle = data?.minutes === minutes;
  const filtered = useMemo(
    () =>
      (scannedThisCandle ? (data?.rows ?? []) : []).filter((r) => {
        if (!matchesText(query, r.symbol)) return false;
        if (side !== "ALL" && r.side !== side) return false;
        if (fastOnly && (speedOf(r) ?? 0) < minSpeed) return false;
        if (r.state === "CROSSED") return showCrossed;
        return (r.minutes_to_cross ?? Infinity) <= within;
      }),
    [data?.rows, scannedThisCandle, query, side, fastOnly, minSpeed, speedOf, showCrossed, within]
  );
  const { sorted: rows, sort, onSort } = useSort<CrossScanRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "side":
        return r.side;
      case "mins":
        return r.state === "CROSSED" ? -1 : r.minutes_to_cross;
      case "ltp":
        return r.ltp;
      case "gap":
        return r.gap_pct;
      case "slope":
        return r.slope_pct;
      case "speed":
        return r.speed_pct_per_min;
      case "volume":
        return r.volume;
      case "volWin":
        return r.volume_window;
    }
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
        cross. A scan starts only when you press Scan now, and reads the stocks one at a time so the desk and the bots stay
        responsive.
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
        <NumberFilter label="Crossing within (minutes)" value={within} onChange={setWithin} min={1} max={240} step={1} suffix="min" />
        <TextFilter value={query} onChange={setQuery} />
        <NumberFilter
          label="Fast movers: min speed (last 10 min)"
          value={minSpeed}
          onChange={setMinSpeed}
          min={0}
          max={0.5}
          step={0.01}
          suffix="%/min"
          className="w-52"
        />
        <label className="flex min-h-9 items-center gap-2 self-end text-slate-300">
          <input type="checkbox" className="h-5 w-5 accent-sky-400" checked={fastOnly} onChange={(e) => setFastOnly(e.target.checked)} />
          Fast movers only
        </label>
        <label className="flex min-h-9 items-center gap-2 self-end text-slate-300">
          <input type="checkbox" className="h-5 w-5 accent-sky-400" checked={showCrossed} onChange={(e) => setShowCrossed(e.target.checked)} />
          Also show stocks that just crossed
        </label>
        {data && (
          <span className="ml-auto">
            {data.running
              ? `Scanning ${data.done} of ${data.total}…`
              : asOf && scannedThisCandle
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
              : data?.as_of && scannedThisCandle
                ? `No stock matches these filters. Widen "Crossing within", clear the stock or fast-mover filter, or press Scan now after the next candle closes.`
                : `Nothing is scanned until you press Scan now. A scan reads about 200 stocks one at a time and takes a few minutes${scannedThisCandle || !data?.as_of ? "" : `; the last scan was for the ${data.minutes}-minute candle`}.`}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
              <tr>
                <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text />
                <SortTh label="Cross" k="side" sort={sort} onSort={onSort} text />
                <SortTh label="In" k="mins" sort={sort} onSort={onSort} num title="Estimated time to the cross at the pace of the last few candles" />
                <SortTh label="LTP" k="ltp" sort={sort} onSort={onSort} num />
                <SortTh label="Gap %" k="gap" sort={sort} onSort={onSort} num title="(fast SMA − slow SMA) / slow SMA, on the last closed candle" />
                <SortTh label="Closing / candle" k="slope" sort={sort} onSort={onSort} num title="How much the gap changes each candle" />
                <SortTh
                  label="Speed"
                  k="speed"
                  sort={sort}
                  onSort={onSort}
                  num
                  title="How fast the price moved over the last 10 minutes, in % per minute (+ up, − down). From the same 1-minute candles as the scan."
                />
                <SortTh label="Volume" k="volume" sort={sort} onSort={onSort} num title="Shares traded today so far" />
                <SortTh label="Vol 10m" k="volWin" sort={sort} onSort={onSort} num title="Shares traded in the last 10 minutes" />
                <th className="px-1 pb-2 text-right font-medium">{label}</th>
                <th className="px-1 pb-2 text-right font-medium">Arm</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Row key={r.symbol} row={r} fastAt={minSpeed} armedOn={armed.get(r.symbol.toUpperCase()) ?? []} arming={arming === r.symbol} onArm={() => onArm(r.symbol)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Row({
  row: r,
  fastAt,
  armedOn,
  arming,
  onArm,
}: {
  row: CrossScanRow;
  fastAt: number;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  const crossed = r.state === "CROSSED";
  const bullish = r.side === "BULLISH";
  return (
    <tr className="whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2">
      <td className="py-1.5 font-semibold text-slate-100">{r.symbol}</td>
      <td className="py-1.5">
        <span
          className={clsx(
            "rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
            bullish ? "bg-accentSky/10 text-accentSky ring-accentSky/40" : "bg-accentViolet/10 text-accentViolet ring-accentViolet/40"
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
      <td className="py-1.5 text-right tabular-nums">
        {r.speed_pct_per_min == null ? (
          <span className="text-slate-600" title="Not enough candles to say">—</span>
        ) : (
          <span
            className={clsx(Math.abs(r.speed_pct_per_min) >= fastAt ? "font-semibold" : "", r.speed_pct_per_min >= 0 ? "text-profit" : "text-loss")}
            title={`${r.move_pct != null ? `${r.move_pct >= 0 ? "+" : ""}${r.move_pct.toFixed(2)}% ` : ""}over the last ${r.window_min ?? 10} minutes${Math.abs(r.speed_pct_per_min) >= fastAt ? " · fast mover" : ""}`}
          >
            {Math.abs(r.speed_pct_per_min) >= fastAt ? "⚡ " : ""}
            {r.speed_pct_per_min >= 0 ? "+" : ""}
            {r.speed_pct_per_min.toFixed(3)}
          </span>
        )}
      </td>
      <td className="py-1.5 text-right tabular-nums text-slate-300">{shares(r.volume)}</td>
      <td className="py-1.5 text-right tabular-nums text-slate-400">{shares(r.volume_window)}</td>
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
