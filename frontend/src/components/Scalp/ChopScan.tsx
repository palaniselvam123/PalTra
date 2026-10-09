"use client";

import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { Activity, Loader2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { smaApi, type ChopScan as ChopScanData, type ChopScanRow, type ChopScore, type CrossScanMinutes } from "@/lib/smaApi";
import { TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";

const CANDLE_CHOICES: CrossScanMinutes[] = [1, 2, 3, 5, 10, 15];
const POLL_MS = 3_000;
// Auto re-scan period in milliseconds (30 min). GitHub-cron-style, best-effort; the browser
// has to be awake. On mobile a backgrounded tab may skip ticks.
const AUTO_SCAN_MS = 30 * 60 * 1000;
const QUIET_STORE = "sma.chop.quiet"; // localStorage key for the alert settings

type Props = {
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
};

type ScoreFilter = "ALL" | ChopScore;
type SortKey = "symbol" | "score" | "crosses" | "since" | "avgGap" | "avgMove" | "speed" | "volume";

const SCORE_LABEL: Record<ChopScore, string> = { TRENDING: "Trending", MIXED: "Mixed", CHOPPY: "Choppy" };
const SCORE_CLASS: Record<ChopScore, string> = {
  TRENDING: "bg-accentSky/10 text-accentSky ring-accentSky/40",
  MIXED: "bg-slate-700/40 text-slate-300 ring-slate-500/40",
  CHOPPY: "bg-accentViolet/10 text-accentViolet ring-accentViolet/40",
};

const num = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));

function shares(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 1e7) return `${(v / 1e7).toFixed(1)} Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(1)} L`;
  return Math.round(v).toLocaleString("en-IN");
}

/**
 * How choppy or trending each F&O stock has looked this session. Reads today's
 * SMA crosses on the chosen candle and labels each row Trending / Mixed /
 * Choppy. Information only: it places no order, and Arm only puts a stock on a
 * bot's Trade list.
 */
export function ChopScan({ armed, arming, onArm }: Props) {
  const [data, setData] = useState<ChopScanData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minutes, setMinutes] = useState<CrossScanMinutes>(5);
  const [score, setScore] = useState<ScoreFilter>("ALL");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const universe = useRef<string[] | null>(null);
  // Alert settings. Default: armed stocks only, 60 min of quiet, Telegram ping on, auto re-scan on.
  const [armedOnly, setArmedOnly] = useState(true);
  const [quietMin, setQuietMin] = useState(60);
  const [alertOnQuiet, setAlertOnQuiet] = useState(true);
  const [auto, setAuto] = useState(true);
  // "I have shown this stock as quiet already" — stops the popup repeating each tick while it's open.
  const seenRef = useRef<Set<string>>(new Set());
  const [toast, setToast] = useState<string | null>(null);

  // Remember the alert settings between visits.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(QUIET_STORE);
      if (raw) {
        const saved = JSON.parse(raw);
        if (typeof saved.armedOnly === "boolean") setArmedOnly(saved.armedOnly);
        if (typeof saved.quietMin === "number") setQuietMin(saved.quietMin);
        if (typeof saved.alertOnQuiet === "boolean") setAlertOnQuiet(saved.alertOnQuiet);
        if (typeof saved.auto === "boolean") setAuto(saved.auto);
      }
    } catch {
      /* private mode */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(QUIET_STORE, JSON.stringify({ armedOnly, quietMin, alertOnQuiet, auto }));
    } catch {
      /* private mode */
    }
  }, [armedOnly, quietMin, alertOnQuiet, auto]);

  const start = useCallback(
    async (force = false) => {
      setBusy(true);
      setError(null);
      try {
        let symbols: string[];
        if (armedOnly) {
          symbols = Array.from(armed.keys());
          if (symbols.length === 0) throw new Error("No stocks are armed on any bot, so there is nothing to scan.");
        } else {
          if (!universe.current || universe.current.length === 0) {
            const u = await api.scalpUniverse();
            if (u.symbols.length === 0) throw new Error(u.error || "The F&O stock list is empty, so there is nothing to scan.");
            universe.current = u.symbols;
          }
          symbols = universe.current;
        }
        setData(
          await smaApi.chopScanStart(symbols, minutes, force, {
            quiet_min: quietMin,
            alert_on_quiet: alertOnQuiet,
          })
        );
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Could not start the scan");
      } finally {
        setBusy(false);
      }
    },
    [minutes, armed, armedOnly, quietMin, alertOnQuiet]
  );

  // Auto re-scan every 30 minutes while the user has it on and the market is open.
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => {
      if (data?.market_open !== false) void start(true);
    }, AUTO_SCAN_MS);
    return () => clearInterval(id);
  }, [auto, start, data?.market_open]);

  // When a result comes back, pop a toast for quiet runners we haven't highlighted yet.
  useEffect(() => {
    if (!data?.rows) return;
    const quiet = data.rows.filter((r) => r.quiet_runner);
    if (quiet.length === 0) return;
    const fresh = quiet.filter((r) => !seenRef.current.has(r.symbol));
    if (fresh.length === 0) return;
    fresh.forEach((r) => seenRef.current.add(r.symbol));
    setToast(
      fresh.length === 1
        ? `${fresh[0].symbol} is quiet (possible strong ${fresh[0].last_cross_direction === "BULLISH" ? "bullish" : fresh[0].last_cross_direction === "BEARISH" ? "bearish" : "trend"}). Review.`
        : `${fresh.length} stocks are quiet: ${fresh.map((r) => r.symbol).join(", ")}. Review.`
    );
  }, [data?.rows]);

  // The server holds one result. Opening the page shows it — a scan starts only on Scan now.
  useEffect(() => {
    let live = true;
    smaApi
      .chopScan()
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
        .chopScan()
        .then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Lost the scan"));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [data?.running]);

  const scannedThisCandle = data?.minutes === minutes;
  const filtered = useMemo(
    () =>
      (scannedThisCandle ? (data?.rows ?? []) : []).filter((r) => {
        if (!matchesText(query, r.symbol)) return false;
        if (score !== "ALL" && r.score !== score) return false;
        return true;
      }),
    [data?.rows, scannedThisCandle, query, score]
  );
  const { sorted: rows, sort, onSort } = useSort<ChopScanRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "score":
        return r.score === "TRENDING" ? 0 : r.score === "MIXED" ? 1 : 2;
      case "crosses":
        return r.crosses_today;
      case "since":
        return r.minutes_since_last_cross;
      case "avgGap":
        return r.avg_minutes_between;
      case "avgMove":
        return r.avg_move_pct;
      case "speed":
        return r.speed_pct_per_min;
      case "volume":
        return r.volume;
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
          <Activity size={15} className="text-accentSky" /> Trend quality — how choppy each stock has looked today on the {minutes}-minute candle
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
        lead={`Information only. Each stock's ${label} crosses on today's closed candles are counted, with the longest run since the last one and the average time and move between them.`}
      >
        <b>Trending</b> is a stock with 0 or 1 crosses so far and a long run since the last one (more than an hour). <b>Choppy</b> is 4+ crosses, or crosses coming on average less than 15 minutes apart. <b>Mixed</b> is everything else. Past crosses do not predict the next cross; the label describes the session so far. A scan starts only when Scan now is pressed and reads the stocks one at a time (hundreds of downloads).
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
          Show
          <select
            value={score}
            onChange={(e) => setScore(e.target.value as ScoreFilter)}
            className="min-h-9 rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
          >
            <option value="ALL">All</option>
            <option value="TRENDING">Trending only</option>
            <option value="MIXED">Mixed only</option>
            <option value="CHOPPY">Choppy only</option>
          </select>
        </label>
        <TextFilter value={query} onChange={setQuery} />
        <label className="flex min-h-9 items-center gap-2 self-end text-slate-300">
          <input type="checkbox" className="h-5 w-5 accent-sky-400" checked={armedOnly} onChange={(e) => setArmedOnly(e.target.checked)} />
          Armed stocks only
        </label>
        <label className="flex min-h-9 items-center gap-2 self-end text-slate-300">
          <input type="checkbox" className="h-5 w-5 accent-sky-400" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          Rescan every 30 min
        </label>
        <label className="flex flex-col gap-1">
          Alert when quiet for ≥
          <input
            type="number"
            min={1}
            max={375}
            step={5}
            value={quietMin}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v > 0) setQuietMin(Math.min(375, Math.max(1, Math.round(v))));
            }}
            className="min-h-9 w-20 rounded border border-slate-700 bg-base px-1.5 py-1 text-xs tabular-nums text-slate-200"
          />
          <span className="text-[11px] text-slate-500">min</span>
        </label>
        <label className="flex min-h-9 items-center gap-2 self-end text-slate-300">
          <input type="checkbox" className="h-5 w-5 accent-sky-400" checked={alertOnQuiet} onChange={(e) => setAlertOnQuiet(e.target.checked)} />
          Telegram too
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
      {toast && (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          <span>🔔 Quiet runner — {toast}</span>
          <button type="button" onClick={() => setToast(null)} className="rounded px-2 py-0.5 text-amber-100 hover:bg-amber-500/20">
            Dismiss
          </button>
        </div>
      )}

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
                ? "No stock matches these filters. Change Show or clear the stock filter, or press Scan now after the next candle closes."
                : `Nothing is scanned until you press Scan now. A scan reads about 200 stocks one at a time and takes a few minutes${scannedThisCandle || !data?.as_of ? "" : `; the last scan was for the ${data.minutes}-minute candle`}.`}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
              <tr>
                <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text />
                <SortTh label="Score" k="score" sort={sort} onSort={onSort} text />
                <SortTh label="Crosses today" k="crosses" sort={sort} onSort={onSort} num />
                <SortTh label="Last cross (min)" k="since" sort={sort} onSort={onSort} num title="Minutes since the start of the last cross candle" />
                <SortTh label="Avg gap (min)" k="avgGap" sort={sort} onSort={onSort} num title="Average time between today's crosses" />
                <SortTh label="Avg move %" k="avgMove" sort={sort} onSort={onSort} num title="Average absolute close-to-close move between consecutive crosses" />
                <SortTh label="Speed" k="speed" sort={sort} onSort={onSort} num title="Signed % per minute over the last 10 minutes (same reader as the Movers page)" />
                <SortTh label="Volume" k="volume" sort={sort} onSort={onSort} num title="Shares traded today so far" />
                <th className="px-1 pb-2 text-right font-medium">Arm</th>
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

function Row({ row: r, armedOn, arming, onArm }: { row: ChopScanRow; armedOn: string[]; arming: boolean; onArm: () => void }) {
  return (
    <tr className="whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2">
      <td className="py-1.5 font-semibold text-slate-100">{r.symbol}</td>
      <td className="py-1.5">
        <span
          className={clsx("rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset", SCORE_CLASS[r.score])}
          title={
            r.score === "TRENDING"
              ? "Trending — few crosses, long run since the last one"
              : r.score === "CHOPPY"
                ? "Choppy — many crosses, short gaps between them"
                : "Mixed — neither trending nor obviously choppy"
          }
        >
          {SCORE_LABEL[r.score]}
          {r.last_cross_direction === "BULLISH" ? " ↑" : r.last_cross_direction === "BEARISH" ? " ↓" : ""}
        </span>
        {r.quiet_runner && (
          <span
            className="ml-1 rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-amber-300 ring-1 ring-inset ring-amber-500/40"
            title={`Quiet runner — had a cross earlier today and has been silent since. Possible strong ${
              r.last_cross_direction === "BULLISH" ? "bullish" : r.last_cross_direction === "BEARISH" ? "bearish" : "trend"
            }.`}
          >
            🔔 Quiet {r.minutes_since_last_cross == null ? "" : `${Math.max(0, Math.round(r.minutes_since_last_cross))}m`}
          </span>
        )}
      </td>
      <td className="py-1.5 text-right tabular-nums">{r.crosses_today}</td>
      <td className="py-1.5 text-right tabular-nums">{r.minutes_since_last_cross == null ? "—" : Math.max(0, Math.round(r.minutes_since_last_cross))}</td>
      <td className="py-1.5 text-right tabular-nums text-slate-400">{num(r.avg_minutes_between, 1)}</td>
      <td className="py-1.5 text-right tabular-nums text-slate-400">{r.avg_move_pct == null ? "—" : `${r.avg_move_pct.toFixed(2)}%`}</td>
      <td className="py-1.5 text-right tabular-nums">
        {r.speed_pct_per_min == null ? (
          <span className="text-slate-600" title="Not enough candles to say">—</span>
        ) : (
          <span
            className={r.speed_pct_per_min >= 0 ? "text-profit" : "text-loss"}
            title={`${r.move_pct != null ? `${r.move_pct >= 0 ? "+" : ""}${r.move_pct.toFixed(2)}% ` : ""}over the last ${r.window_min ?? 10} minutes`}
          >
            {r.speed_pct_per_min >= 0 ? "+" : ""}
            {r.speed_pct_per_min.toFixed(3)}
          </span>
        )}
      </td>
      <td className="py-1.5 text-right tabular-nums text-slate-300">{shares(r.volume)}</td>
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
