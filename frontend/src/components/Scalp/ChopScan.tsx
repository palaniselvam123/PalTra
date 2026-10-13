"use client";

import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { lastScanText } from "@/lib/lastScan";
import { Activity, Loader2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { smaApi, type ChopAuto, type ChopAutoUpdate, type ChopScan as ChopScanData, type ChopScanRow, type ChopScore, type ChopTrendCheck, type CrossScanMinutes } from "@/lib/smaApi";
import { FilterField, FilterGroup, FilterRow, NumberFilter, SelectFilter, TextFilter, Toggle, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { ColumnHint, inOrder, useColumnOrder } from "@/components/ui/columnOrder";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";

const CANDLE_CHOICES: CrossScanMinutes[] = [1, 2, 3, 5, 10, 15];
const POLL_MS = 3_000;
// Auto re-scan period in milliseconds (30 min). GitHub-cron-style, best-effort; the browser
// has to be awake. On mobile a backgrounded tab may skip ticks.
// While the server's re-scan is on, the card fetches the newest result this often.
const AUTO_POLL_MS = 60_000;
const EVERY_CHOICES = [5, 10, 15, 20, 30, 45, 60];
const QUIET_STORE = "sma.chop.quiet"; // localStorage key for the alert settings

type Props = {
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  /** Scans are paused from the top of the Scalp page. */
  paused?: boolean;
  /** Tells the page's tab bar when this scan last ran. */
  onStatus?: OnScanStatus;
};

type ScoreFilter = "ALL" | ChopScore | "STRONG" | "STRONG_BULLISH" | "STRONG_BEARISH";
type SortKey = "symbol" | "ltp" | "score" | "first" | "trend" | "dayMove" | "crosses" | "since" | "avgGap" | "avgMove" | "speed" | "volume";
type Col = SortKey | "sizes" | "arm";
const COLUMNS: readonly Col[] = ["symbol", "ltp", "score", "first", "trend", "dayMove", "sizes", "crosses", "since", "avgGap", "avgMove", "speed", "volume", "arm"];

const SCORE_LABEL: Record<ChopScore, string> = { TRENDING: "Trending", MIXED: "Mixed", CHOPPY: "Choppy" };
const SCORE_CLASS: Record<ChopScore, string> = {
  TRENDING: "bg-accentSky/10 text-accentSky ring-accentSky/40",
  MIXED: "bg-slate-700/40 text-slate-300 ring-slate-500/40",
  CHOPPY: "bg-accentViolet/10 text-accentViolet ring-accentViolet/40",
};

// What each strong-trend check means, in the order the server judges them.
const CHECK_TEXT: Record<ChopTrendCheck, string> = {
  stack: "Price beyond the fast SMA, fast beyond the slow SMA",
  vwap: "Price and the slow SMA on the trend side of VWAP",
  slope: "Fast and slow SMA both sloping with the trend",
  fan: "SMAs fanned apart (gap ≥ 0.10%) and not closing up",
  hold: "≥ 75% of the last 12 closes on the trend side of the fast SMA",
  structure: "Lower highs and lows (bearish) / higher highs and lows (bullish)",
  run: "No SMA cross in the last 12 candles",
};
const CHECK_ORDER = Object.keys(CHECK_TEXT) as ChopTrendCheck[];

const isStrong = (r: ChopScanRow) => r.trend === "STRONG_BULLISH" || r.trend === "STRONG_BEARISH";
// Inside [min, max] ₹; 0 = no bound. A stock with no price passes only when no bound is set (as chop_scan.in_price_range).
const inPriceRange = (ltp: number | null | undefined, min: number, max: number) =>
  (!min && !max) || (ltp != null && (!min || ltp >= min) && (!max || ltp <= max));

const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
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
export function ChopScan({ armed, arming, onArm, paused = false, onStatus }: Props) {
  const cols = useColumnOrder<Col>("scalp.cols.trend", COLUMNS);
  const [data, setData] = useState<ChopScanData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minutes, setMinutes] = useState<CrossScanMinutes>(5);
  const [score, setScore] = useState<ScoreFilter>("ALL");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const universe = useRef<string[] | null>(null);
  // Alert settings. Default: armed stocks only, 60 min of quiet, Telegram ping on.
  const [armedOnly, setArmedOnly] = useState(true);
  const [quietMin, setQuietMin] = useState(60);
  const [alertOnQuiet, setAlertOnQuiet] = useState(true);
  // The server's own re-scan (chop_auto.py): on/off and every N minutes live on the server, not in this browser.
  const [auto, setAuto] = useState<ChopAuto | null>(null);
  // True once the server's last result (and its auto settings) has loaded, so the card does not overwrite them.
  const [synced, setSynced] = useState(false);
  // Fast movers only: |speed| over the last 10 minutes at least this many % per minute (0 = off).
  const [minSpeed, setMinSpeed] = useState(0);
  // "No cross today on": keep stocks whose SMAs have not crossed once today on every ticked candle size.
  const [noCrossOn, setNoCrossOn] = useState<CrossScanMinutes[]>([]);
  // How many crosses today the ticked sizes must have: 0 (none) or exactly 1 (one turn since the morning).
  const [crossCount, setCrossCount] = useState<0 | 1>(0);
  // Price range in ₹ (0 = no bound): filters the table, the quiet pop-up and the Telegram alert.
  const [priceMin, setPriceMin] = useState(0);
  const [priceMax, setPriceMax] = useState(0);
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
        if (typeof saved.minSpeed === "number") setMinSpeed(saved.minSpeed);
        if (saved.crossCount === 0 || saved.crossCount === 1) setCrossCount(saved.crossCount);
        if (typeof saved.priceMin === "number") setPriceMin(saved.priceMin);
        if (typeof saved.priceMax === "number") setPriceMax(saved.priceMax);
        if (Array.isArray(saved.noCrossOn))
          setNoCrossOn(saved.noCrossOn.filter((m: unknown) => CANDLE_CHOICES.includes(m as CrossScanMinutes)));
        if (typeof saved.score === "string") setScore(saved.score as ScoreFilter);
      }
    } catch {
      /* private mode */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(QUIET_STORE, JSON.stringify({ armedOnly, quietMin, alertOnQuiet, minSpeed, score, noCrossOn, crossCount, priceMin, priceMax })
      );
    } catch {
      /* private mode */
    }
  }, [armedOnly, quietMin, alertOnQuiet, minSpeed, score, noCrossOn, crossCount, priceMin, priceMax]);

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
            price_min: priceMin,
            price_max: priceMax,
          })
        );
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Could not start the scan");
      } finally {
        setBusy(false);
      }
    },
    [minutes, armed, armedOnly, quietMin, alertOnQuiet, priceMin, priceMax]
  );

  // The server re-scans by itself (no browser needed). Tell it the card's choices, so its scans match.
  useEffect(() => {
    if (!synced) return;
    const id = setTimeout(() => {
      smaApi
        .chopScanAuto({
          minutes,
          armed_only: armedOnly,
          quiet_min: quietMin,
          alert_on_quiet: alertOnQuiet,
          price_min: priceMin,
          price_max: priceMax,
        })
        .then(setAuto)
        .catch(() => {
          /* an older server without the re-scan: the card still scans on Scan now */
        });
    }, 800);
    return () => clearTimeout(id);
  }, [synced, minutes, armedOnly, quietMin, alertOnQuiet, priceMin, priceMax]);

  const changeAuto = (change: ChopAutoUpdate) => {
    setError(null);
    smaApi
      .chopScanAuto(change)
      .then(setAuto)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not change the auto re-scan"));
  };

  // While the server's re-scan is on, pick up each new result (and its quiet runners) once a minute.
  useEffect(() => {
    if (!auto?.on || paused) return;
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      smaApi
        .chopScan()
        .then((d) => {
          setData(d);
          if (d.auto) setAuto(d.auto);
        })
        .catch(() => {});
    }, AUTO_POLL_MS);
    return () => clearInterval(id);
  }, [auto?.on, paused]);

  // When a result comes back, pop a toast for quiet runners we haven't highlighted yet.
  useEffect(() => {
    if (!data?.rows) return;
    const quiet = data.rows.filter((r) => r.quiet_runner && inPriceRange(r.ltp, priceMin, priceMax));
    if (quiet.length === 0) return;
    const fresh = quiet.filter((r) => !seenRef.current.has(r.symbol));
    if (fresh.length === 0) return;
    fresh.forEach((r) => seenRef.current.add(r.symbol));
    setToast(
      fresh.length === 1
        ? `${fresh[0].symbol} is quiet (possible strong ${fresh[0].last_cross_direction === "BULLISH" ? "bullish" : fresh[0].last_cross_direction === "BEARISH" ? "bearish" : "trend"}). Review.`
        : `${fresh.length} stocks are quiet: ${fresh
            .slice(0, 8)
            .map((r) => r.symbol)
            .join(", ")}${fresh.length > 8 ? ` and ${fresh.length - 8} more` : ""}. Review.${
            fresh.length > 30 ? " That is most of the list: raise \"Alert when quiet for\" to 30-60 min." : ""
          }`
    );
  }, [data?.rows, priceMin, priceMax]);

  // The server holds one result. Opening the page shows it — a scan starts only on Scan now.
  useEffect(() => {
    let live = true;
    smaApi
      .chopScan()
      .then((d) => {
        if (!live) return;
        setData(d);
        if (d.auto) {
          setAuto(d.auto);
          setMinutes(d.auto.minutes);
        }
      })
      .catch(() => {
        /* the terminal is not answering: the page shows "No scan yet" and Scan now still works */
      })
      .finally(() => live && setSynced(true));
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
        if (score === "STRONG" && !isStrong(r)) return false;
        if ((score === "STRONG_BULLISH" || score === "STRONG_BEARISH") && r.trend !== score) return false;
        if ((score === "TRENDING" || score === "MIXED" || score === "CHOPPY") && r.score !== score) return false;
        if (minSpeed > 0 && Math.abs(r.speed_pct_per_min ?? 0) < minSpeed) return false;
        if (!inPriceRange(r.ltp, priceMin, priceMax)) return false;
        if (noCrossOn.some((m) => r.crosses_by_minutes?.[String(m)] !== crossCount)) return false;
        return true;
      }),
    [data?.rows, scannedThisCandle, query, score, minSpeed, noCrossOn, crossCount, priceMin, priceMax]
  );
  const { sorted: rows, sort, onSort } = useSort<ChopScanRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "ltp":
        return r.ltp;
      case "first":
        return r.first_quiet_at ?? null;
      case "score":
        return r.score === "TRENDING" ? 0 : r.score === "MIXED" ? 1 : 2;
      case "trend":
        return (isStrong(r) ? 1000 : 0) + (r.trend_score ?? 0);
      case "dayMove":
        return r.day_move_pct ?? null;
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
  const label = data ? `SMA ${data.sma_fast}/${data.sma_slow}` : "SMA";

  useEffect(() => {
    onStatus?.({ when: data?.finished_at ?? data?.as_of ?? null, running: !!data?.running });
  }, [onStatus, data?.finished_at, data?.as_of, data?.running]);

  const paged = usePaged(rows, "scalp.trend.pageSize");
  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Activity size={15} className="text-accentSky" /> Trend quality — how choppy each stock has looked today on the {minutes}-minute candle
        </h2>
        <button
          type="button"
          onClick={() => void start(true)}
          disabled={busy || data?.running || paused}
          title={paused ? "Scans are paused at the top of the page" : undefined}
          className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
        >
          {busy || data?.running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
        </button>
      </div>
      <Explain
        className="mb-3"
        lead={`Information only. Each stock's ${label} crosses on today's closed candles are counted, with the longest run since the last one and the average time and move between them.`}
      >
        <b>Trending</b> is a stock with 0 or 1 crosses so far and a long run since the last one (more than an hour). <b>Choppy</b> is 4+ crosses, or crosses coming on average less than 15 minutes apart. <b>Mixed</b> is everything else. <b>Strong trend</b> (bearish ↓ / bullish ↑) is the clean one-way move: price, the fast SMA, the slow SMA and VWAP stacked one way, both SMAs sloping and fanned apart, at least 75% of the last 12 closes on the trend side of the fast SMA (a bounce that fails at the fast SMA still counts), lower highs and lows (higher for bullish) and no cross in the last 12 candles. The first three are required and 6 of the 7 must pass; hover the chip to see which. Add <b>Fast movers</b> to keep only the ones moving quickly right now. Untick <b>Armed only</b> to search every F&O stock. <b>Price from / to</b> (₹, 0 = any) keeps only stocks priced in that range, in the table, the quiet pop-up and the Telegram alert. <b>Crosses today</b> keeps only the stocks whose SMAs crossed <i>none</i> or <i>exactly 1</i> time today on every candle size you tick (1m, 5m, 15m…): a one-way day, or one turn since the morning and a one-way run after it (the cross time shows as @HH:MM). All sizes are read from the same download. Past crosses do not predict the next cross; the label describes the session so far. A scan starts only when Scan now is pressed and reads the stocks one at a time (hundreds of downloads).
      </Explain>

      <FilterRow
        status={
          data
            ? data.running
              ? `Scanning ${data.done} of ${data.total}…`
              : lastScanText(data, minutes) ?? "No scan yet"
            : null
        }
      >
        <FilterGroup title="View">
          <SelectFilter
            label="Candle"
            value={minutes}
            onChange={(v) => setMinutes(v as CrossScanMinutes)}
            options={CANDLE_CHOICES.map((m) => ({ value: m, label: `${m} min` }))}
            className="w-28"
          />
          <SelectFilter
            label="Show"
            value={score}
            onChange={(v) => setScore(v as ScoreFilter)}
            options={[
              { value: "ALL", label: "All" },
              { value: "STRONG", label: "Strong trend (both)" },
              { value: "STRONG_BEARISH", label: "Strong bearish ↓" },
              { value: "STRONG_BULLISH", label: "Strong bullish ↑" },
              { value: "TRENDING", label: "Trending only" },
              { value: "MIXED", label: "Mixed only" },
              { value: "CHOPPY", label: "Choppy only" },
            ]}
            className="w-44"
          />
          <TextFilter value={query} onChange={setQuery} />
          <NumberFilter
            label="Fast movers: |speed| ≥"
            value={minSpeed}
            onChange={(v) => setMinSpeed(Math.min(1, Math.max(0, Math.round(v * 1000) / 1000)))}
            min={0}
            max={0.3}
            step={0.01}
            suffix="%/min"
            className="w-44"
          />
          <FilterField label="Crosses today">
            <select
              value={crossCount}
              onChange={(e) => setCrossCount(Number(e.target.value) === 1 ? 1 : 0)}
              aria-label="How many SMA crosses today"
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
              title="None: the SMAs have not crossed once today. Exactly 1: one turn since the morning, then a one-way run."
            >
              <option value={0}>None (0)</option>
              <option value={1}>Exactly 1</option>
            </select>
            <span className="text-xs text-slate-400">on</span>
            {CANDLE_CHOICES.map((m) => {
              const on = noCrossOn.includes(m);
              return (
                <button
                  key={m}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setNoCrossOn((cur) => (on ? cur.filter((x) => x !== m) : [...cur, m].sort((a, b) => a - b)))}
                  className={clsx(
                    "min-h-9 min-w-10 rounded-md px-2.5 text-xs font-semibold ring-1 ring-inset",
                    on ? "bg-accentSky/15 text-accentSky ring-accentSky/50" : "text-slate-400 ring-slate-700 hover:bg-white/5"
                  )}
                >
                  {m}m
                </button>
              );
            })}
            {noCrossOn.length > 0 && (
              <button type="button" onClick={() => setNoCrossOn([])} className="text-xs text-slate-500 underline-offset-2 hover:underline">
                clear
              </button>
            )}
          </FilterField>
        </FilterGroup>
        <FilterGroup title="Price & alert">
          <NumberFilter
            label="Price from (0 = any)"
            value={priceMin}
            onChange={(v) => setPriceMin(Math.min(100000, Math.max(0, Math.round(v))))}
            min={0}
            max={5000}
            step={50}
            prefix="₹"
            className="w-36"
          />
          <NumberFilter
            label="Price to (0 = any)"
            value={priceMax}
            onChange={(v) => setPriceMax(Math.min(100000, Math.max(0, Math.round(v))))}
            min={0}
            max={5000}
            step={50}
            prefix="₹"
            className="w-36"
          />
          <NumberFilter
            label="Alert when quiet for ≥"
            value={quietMin}
            onChange={(v) => setQuietMin(Math.min(375, Math.max(1, Math.round(v))))}
            min={5}
            max={240}
            step={5}
            suffix="min"
            className="w-40"
          />
          <FilterField label="Quiet runner alert">
            <Toggle label="Telegram" checked={alertOnQuiet} onChange={setAlertOnQuiet} title="Ping the owner on Telegram for a new quiet runner" />
          </FilterField>
        </FilterGroup>
        <FilterGroup title="Scanning">
          <FilterField label="Stocks">
            <Toggle label="Armed only" checked={armedOnly} onChange={setArmedOnly} title="Only scan stocks on a bot's Trade list" />
          </FilterField>
          <FilterField label="Server">
            <Toggle
              label="Auto re-scan"
              checked={auto?.on ?? false}
              onChange={(on) => changeAuto({ on })}
              title="The server scans by itself every N minutes while the market is open, even with this page closed"
            />
          </FilterField>
          <SelectFilter
            label="Re-scan every"
            value={auto?.every_min ?? 30}
            onChange={(v) => changeAuto({ every_min: Number(v) })}
            options={(auto?.every_choices ?? EVERY_CHOICES).map((m) => ({ value: m, label: `${m} min` }))}
            className="w-32"
          />
        </FilterGroup>
      </FilterRow>
      {auto ? <AutoLine auto={auto} paused={paused} /> : null}
      {toast && (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          <span>🔔 Quiet runner — {toast}</span>
          <button type="button" onClick={() => setToast(null)} className="rounded px-2 py-0.5 text-amber-300 hover:bg-amber-500/20">
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
                : `${auto?.on ? `The server scans by itself every ${auto.every_min} min while the market is open, or press Scan now.` : "Nothing is scanned until you press Scan now."} A scan reads about 200 stocks one at a time and takes a few minutes${scannedThisCandle || !data?.as_of ? "" : `; the last scan was for the ${data.minutes}-minute candle`}.`}
        </p>
      ) : (
        <div className="overflow-x-auto" ref={paged.anchor}>
          <Pager p={paged} className="mb-2" />
          <ColumnHint moved={cols.moved} onReset={cols.reset} />
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
              <tr>
                {inOrder(cols.order, {
                  symbol: <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text drag={cols.dragProps("symbol")} />,
                  ltp: <SortTh label="LTP" k="ltp" sort={sort} onSort={onSort} num title="Last traded price at the scan" drag={cols.dragProps("ltp")} />,
                  score: <SortTh label="Score" k="score" sort={sort} onSort={onSort} text drag={cols.dragProps("score")} />,
                  first: (
                    <SortTh
                      label="First alert"
                      k="first"
                      sort={sort}
                      onSort={onSort}
                      text
                      title="When a scan first read the stock as a quiet runner today (📨 = its Telegram went)"
                      drag={cols.dragProps("first")}
                    />
                  ),
                  trend: (
                    <SortTh label="Trend" k="trend" sort={sort} onSort={onSort} text title="Strong trend label and how many of the 7 checks pass" drag={cols.dragProps("trend")} />
                  ),
                  dayMove: <SortTh label="Day %" k="dayMove" sort={sort} onSort={onSort} num title="Last price vs today's first open" drag={cols.dragProps("dayMove")} />,
                  sizes: (
                    <th
                      {...cols.dragProps("sizes")}
                      className={clsx("px-2 pb-2 text-left font-medium", cols.dragProps("sizes").className)}
                      title="Today's SMA crosses on each candle size (— = not enough candles yet)"
                    >
                      Crosses by size
                    </th>
                  ),
                  crosses: <SortTh label="Crosses today" k="crosses" sort={sort} onSort={onSort} num drag={cols.dragProps("crosses")} />,
                  since: (
                    <SortTh label="Last cross (min)" k="since" sort={sort} onSort={onSort} num title="Minutes since the start of the last cross candle" drag={cols.dragProps("since")} />
                  ),
                  avgGap: (
                    <SortTh label="Avg gap (min)" k="avgGap" sort={sort} onSort={onSort} num title="Average time between today's crosses" drag={cols.dragProps("avgGap")} />
                  ),
                  avgMove: (
                    <SortTh
                      label="Avg move %"
                      k="avgMove"
                      sort={sort}
                      onSort={onSort}
                      num
                      title="Average absolute close-to-close move between consecutive crosses"
                      drag={cols.dragProps("avgMove")}
                    />
                  ),
                  speed: (
                    <SortTh
                      label="Speed"
                      k="speed"
                      sort={sort}
                      onSort={onSort}
                      num
                      title="Signed % per minute over the last 10 minutes (same reader as the Movers page)"
                      drag={cols.dragProps("speed")}
                    />
                  ),
                  volume: <SortTh label="Volume" k="volume" sort={sort} onSort={onSort} num title="Shares traded today so far" drag={cols.dragProps("volume")} />,
                  arm: (
                    <th {...cols.dragProps("arm")} className={clsx("px-1 pb-2 text-right font-medium", cols.dragProps("arm").className)}>
                      Arm
                    </th>
                  ),
                })}
              </tr>
            </thead>
            <tbody>
              {paged.shown.map((r) => (
                <Row key={r.symbol} order={cols.order} row={r} armedOn={armed.get(r.symbol.toUpperCase()) ?? []} arming={arming === r.symbol} onArm={() => onArm(r.symbol)} />
              ))}
            </tbody>
          </table>
          <Pager p={paged} className="mt-2" />
        </div>
      )}
    </section>
  );
}

function Row({
  order,
  row: r,
  armedOn,
  arming,
  onArm,
}: {
  order: readonly Col[];
  row: ChopScanRow;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  return (
    <tr className="whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2">
      {inOrder(order, {
        symbol: (
          <td className="py-1.5 font-semibold text-slate-100">{r.symbol}</td>
        ),
        ltp: <td className="py-1.5 text-right tabular-nums">{num(r.ltp)}</td>,
        score: (
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
        ),
        first: (
          <td className="py-1.5 tabular-nums text-xs">
            {r.first_quiet_at ? (
              <span
                className="text-amber-300"
                title={`First read as quiet at ${clock(r.first_quiet_at)} IST${r.alerted_at ? ` · Telegram sent ${clock(r.alerted_at)} IST` : " · no Telegram sent"}`}
              >
                {clock(r.first_quiet_at)}
                {r.alerted_at ? " 📨" : ""}
              </span>
            ) : (
              <span className="text-slate-600">—</span>
            )}
          </td>
        ),
        trend: (
          <td className="py-1.5">
            <TrendChip row={r} />
          </td>
        ),
        dayMove: (
          <td className="py-1.5 text-right tabular-nums">
            {r.day_move_pct == null ? (
              "—"
            ) : (
              <span className={r.day_move_pct >= 0 ? "text-profit" : "text-loss"}>
                {r.day_move_pct >= 0 ? "+" : ""}
                {r.day_move_pct.toFixed(2)}%
              </span>
            )}
          </td>
        ),
        sizes: (
          <td className="py-1.5">
            <CrossSizes row={r} />
          </td>
        ),
        crosses: (
          <td className="py-1.5 text-right tabular-nums">{r.crosses_today}</td>
        ),
        since: (
          <td className="py-1.5 text-right tabular-nums">{r.minutes_since_last_cross == null ? "—" : Math.max(0, Math.round(r.minutes_since_last_cross))}</td>
        ),
        avgGap: (
          <td className="py-1.5 text-right tabular-nums text-slate-400">{num(r.avg_minutes_between, 1)}</td>
        ),
        avgMove: (
          <td className="py-1.5 text-right tabular-nums text-slate-400">{r.avg_move_pct == null ? "—" : `${r.avg_move_pct.toFixed(2)}%`}</td>
        ),
        speed: (
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
        ),
        volume: (
          <td className="py-1.5 text-right tabular-nums text-slate-300">{shares(r.volume)}</td>
        ),
        arm: (
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
        ),
      })}
    </tr>
  );
}

function TrendChip({ row: r }: { row: ChopScanRow }) {
  const checks = r.trend_checks ?? {};
  const passed = CHECK_ORDER.filter((k) => checks[k]).length;
  const title = [
    r.trend ? `Strong ${r.trend === "STRONG_BULLISH" ? "bullish" : "bearish"} trend` : `Not a strong trend (${r.trend_side?.toLowerCase() ?? "no"} side)`,
    ...CHECK_ORDER.map((k) => `${checks[k] ? "✓" : "✗"} ${CHECK_TEXT[k]}`),
    r.vwap != null ? `VWAP ${r.vwap.toFixed(2)}` : "VWAP not available",
    r.sma_gap_pct != null ? `SMA gap ${r.sma_gap_pct >= 0 ? "+" : ""}${r.sma_gap_pct.toFixed(2)}%` : "",
  ]
    .filter(Boolean)
    .join("\n");
  if (!r.trend_side) return <span className="text-slate-600">—</span>;
  if (!r.trend)
    return (
      <span className="text-xs tabular-nums text-slate-500" title={title}>
        {r.trend_side === "BULLISH" ? "↑" : "↓"} {passed}/7
      </span>
    );
  const bull = r.trend === "STRONG_BULLISH";
  return (
    <span
      className={clsx(
        "rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        bull ? "bg-profit/10 text-profit ring-profit/40" : "bg-loss/10 text-loss ring-loss/40"
      )}
      title={title}
    >
      {bull ? "Strong ↑" : "Strong ↓"} {passed}/7
    </span>
  );
}

const istTime = (ts: number | null | undefined) =>
  ts == null ? "" : new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

/** Today's crosses on 1 / 5 / 15-minute candles (the other sizes on hover): 0 is green with the side;
 * one cross shows when it happened and which way. */
function CrossSizes({ row: r }: { row: ChopScanRow }) {
  const by = r.crosses_by_minutes ?? {};
  const side = r.side_by_minutes ?? {};
  const lastTs = r.last_cross_ts_by_minutes ?? {};
  const lastSide = r.last_cross_side_by_minutes ?? {};
  const arrow = (s: string | null | undefined) => (s === "BULLISH" ? "↑" : s === "BEARISH" ? "↓" : "");
  const all = CANDLE_CHOICES.map((m) => {
    const k = String(m);
    const last = lastTs[k] != null ? ` last ${istTime(lastTs[k])} ${arrow(lastSide[k])}` : "";
    return `${m}m: ${by[k] ?? "—"}${last}`;
  });
  return (
    <span className="inline-flex gap-1 text-xs tabular-nums" title={`Today's SMA crosses — ${all.join(" · ")}`}>
      {([1, 5, 15] as const).map((m) => {
        const k = String(m);
        const n = by[k];
        return (
          <span
            key={m}
            className={clsx(
              "rounded px-1.5 py-0.5 ring-1 ring-inset",
              n === 0
                ? "bg-profit/10 text-profit ring-profit/30"
                : n === 1
                  ? "bg-accentSky/10 text-accentSky ring-accentSky/30"
                  : n == null
                    ? "text-slate-600 ring-slate-800"
                    : "text-slate-400 ring-slate-700"
            )}
          >
            {m}m {n ?? "—"}
            {n === 0 && side[k] ? ` ${arrow(side[k])}` : ""}
            {n === 1 && lastTs[k] != null ? ` @${istTime(lastTs[k])} ${arrow(lastSide[k])}` : ""}
          </span>
        );
      })}
    </span>
  );
}

/** "14:45 IST", or "09:15 IST on 14 Oct" when it is not today. */
function whenText(iso: string) {
  const at = new Date(iso);
  const time = at.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  const day = (d: Date) => d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  return day(at) === day(new Date())
    ? `${time} IST`
    : `${time} IST on ${at.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" })}`;
}

/** One line on the server's own re-scan: on/off, how often, when next, or why it did not run. */
function AutoLine({ auto, paused }: { auto: ChopAuto; paused: boolean }) {
  if (!auto.on)
    return <p className="mb-3 text-xs text-slate-500">Auto re-scan is off. Scans run only when you press Scan now.</p>;
  const next = auto.next_at ? new Date(auto.next_at) : null;
  const soon = next != null && next.getTime() <= Date.now();
  return (
    <p className="mb-3 text-xs text-slate-400">
      <span className="font-semibold text-profit">Auto re-scan on</span> · every {auto.every_min} min on the server, even with this
      page closed{auto.armed_only ? " · armed stocks" : " · every F&O stock"}
      {paused
        ? " · paused at the top of the page"
        : next
          ? soon
            ? " · next scan within a minute"
            : ` · next ${whenText(auto.next_at as string)}`
          : ""}
      {auto.error ? <span className="text-loss"> · last try: {auto.error}</span> : null}
    </p>
  );
}
