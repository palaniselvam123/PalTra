"use client";

import { StopScan } from "@/components/Scalp/StopScan";
import { ActiveTrendTest } from "@/components/Scalp/ActiveTrendTest";
import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import clsx from "clsx";
import { Activity, ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Download, Eye, FlaskConical, Flame, History, Loader2, RefreshCw, Target } from "lucide-react";
import { api, type ActiveHistoryResponse, type ActiveSort, type ActiveStock, type ActiveStocksResponse, type DeliveryRow } from "@/lib/api";
import { saveFile } from "@/lib/chartTable";
import { FilterRow, NumberFilter, SelectFilter, TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { ColumnHint, inOrder, useColumnOrder } from "@/components/ui/columnOrder";
import { DeliveryCells, DeliveryHead, DeliveryNote, useDelivery } from "./delivery";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { GrowwLink } from "@/components/ui/growwLink";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { smaApi, type ChopScanRow, type CrossScanMinutes, type ScanCheckRow, type ScanRunDetail } from "@/lib/smaApi";

const POLL_MS = 30_000;
const SORT_LABEL: Record<ActiveSort, string> = {
  value: "Money traded",
  rvol: "Volume vs usual",
  change: "Biggest move",
  pressure: "Buyer/seller imbalance",
  speed: "Fastest move now (speed)",
  vwap: "Furthest from average price",
};
/** The same ranking as the server's SORTS (active_stocks.py), for a saved scan. */
const RANK_KEY: Record<ActiveSort, (r: ActiveStock) => number> = {
  value: (r) => r.value_cr,
  rvol: (r) => r.rvol ?? 0,
  change: (r) => Math.abs(r.change_pct ?? 0),
  pressure: (r) => Math.abs((r.buy_share ?? 0.5) - 0.5),
  speed: (r) => Math.abs(r.speed_pct_min ?? 0),
  vwap: (r) => Math.abs(r.vwap_dist_pct ?? 0),
};
type ColKey = "symbol" | "ltp" | "change" | "speed" | "value" | "rvol" | "buy" | "vwap" | "bias";
type Col = "rank" | ColKey | "actions";
const COLUMNS: readonly Col[] = ["rank", "symbol", "ltp", "change", "speed", "value", "rvol", "buy", "vwap", "bias", "actions"];
const TOP = 40;

const clockIst = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });

const CSV_HEAD = ["Rank", "Stock", "LTP", "Day %", "Speed %/min", "Traded Rs cr", "Vol vs usual", "Buyers %", "Buy qty", "Sell qty", "vs avg %", "Avg price", "Bias"];
const num = (v: number | null | undefined, d = 2) => (v == null ? "" : v.toFixed(d));
function activeCsv(rows: ActiveStock[]): string {
  const lines = rows.map((r, i) =>
    [
      i + 1,
      r.symbol,
      num(r.ltp),
      num(r.change_pct),
      num(r.speed_pct_min, 3),
      num(r.value_cr, 1),
      num(r.rvol),
      r.buy_share == null ? "" : Math.round(r.buy_share * 100),
      r.buy_qty ?? "",
      r.sell_qty ?? "",
      num(r.vwap_dist_pct),
      num(r.avg_price),
      r.bias === "NONE" ? "" : r.bias,
    ].join(",")
  );
  return [CSV_HEAD.join(","), ...lines].join("\n");
}

/** Trend quality filter on the stocks read (Read trend quality). */
type TrendPick = "ALL" | "STRONG" | "TRENDING" | "NOT_CHOPPY";
const TREND_PICKS: { value: TrendPick; label: string }[] = [
  { value: "ALL", label: "Any" },
  { value: "STRONG", label: "Strong trend" },
  { value: "TRENDING", label: "Trending" },
  { value: "NOT_CHOPPY", label: "Not choppy" },
];
const TQ_SIZES: CrossScanMinutes[] = [1, 3, 5, 15];
const TQ_MAX = 200;
const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

function trendPasses(pick: TrendPick, row: ChopScanRow | undefined): boolean {
  if (pick === "ALL") return true;
  if (!row) return false;
  if (pick === "STRONG") return !!row.trend;
  if (pick === "TRENDING") return row.score === "TRENDING";
  return row.score !== "CHOPPY";
}

type Props = {
  /** Stocks already on the desk feed (shown in the monitor below). */
  watching: Set<string>;
  /** Stock → the SMA desks it is armed on. */
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  /** Called after a stock is added to the desk feed, so the monitor reloads. */
  onWatched: () => void;
  /** Scans are paused from the top of the Scalp page. */
  paused?: boolean;
  /** Tells the page's tab bar when this scan last ran. */
  onStatus?: OnScanStatus;
  /** Arm the ticked stocks on one bot at once (the other Scalp tables' Arm ticked). */
  onArmMany?: (symbols: string[]) => void;
};

const pct = (v: number | null, digits = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);

/** The F&O stocks ranked by where traders are: money traded, volume against
 * usual, and buy vs sell quantity waiting. From Groww quotes; no orders. */
export function MostActive({ watching, armed, arming, onArm, onWatched, paused = false, onStatus, onArmMany }: Props) {
  const ticks = useTicks();
  const [priceMin, setPriceMin] = useState(0);
  const [priceMax, setPriceMax] = useState(0);
  const [minRvol, setMinRvol] = useState(0);
  // Trend quality of the stocks on screen (the Trend quality scan, run on just these stocks).
  const [tqMinutes, setTqMinutes] = useState<CrossScanMinutes>(5);
  const [tq, setTq] = useState<Map<string, ChopScanRow>>(new Map());
  const [tqBusy, setTqBusy] = useState(false);
  const [tqNote, setTqNote] = useState<string | null>(null);
  const [trendPick, setTrendPick] = useState<TrendPick>("ALL");
  // A saved scan's accuracy check (the same check as the History tab's).
  const [detail, setDetail] = useState<ScanRunDetail | null>(null);
  const [checkNote, setCheckNote] = useState<string | null>(null);
  const cols = useColumnOrder<Col>("scalp.cols.active", COLUMNS);
  const [data, setData] = useState<ActiveStocksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<ActiveSort>("value");
  const [bias, setBias] = useState<"ALL" | "LONG" | "SHORT">("ALL");
  const [minValue, setMinValue] = useState(0);
  const [query, setQuery] = useState("");
  const [minSpeed, setMinSpeed] = useState(0);
  const [scanning, setScanning] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // History: any saved scan (kept on the server for about 90 days), shown in the same table.
  const [view, setView] = useState<"live" | "history" | "test">("live");
  const [hist, setHist] = useState<ActiveHistoryResponse | null>(null);
  const [histDay, setHistDay] = useState("");
  const [runId, setRunId] = useState<number | null>(null);
  const [saved, setSaved] = useState<{ id: number; as_of: string; total: number | null; failed: number | null; rows: ActiveStock[] } | null>(null);
  const [histBusy, setHistBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await api.scalpActive({
        sort,
        top: TOP,
        min_value_cr: minValue,
        ...(bias === "ALL" ? {} : { bias }),
      });
      setData(next);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not load the most active stocks");
    }
  }, [sort, bias, minValue]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  // The saved days and that day's scans; the latest scan of the day opens first.
  useEffect(() => {
    if (view !== "history") return;
    let live = true;
    setHistBusy(true);
    api
      .scalpActiveHistory(histDay || undefined)
      .then((h) => {
        if (!live) return;
        setHist(h);
        setError(null);
        if (!histDay && h.day) setHistDay(h.day);
        setRunId((cur) => (cur != null && h.runs.some((r) => r.id === cur) ? cur : h.runs.length ? h.runs[h.runs.length - 1].id : null));
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Could not load the saved scans"))
      .finally(() => live && setHistBusy(false));
    return () => {
      live = false;
    };
  }, [view, histDay]);

  useEffect(() => {
    if (view !== "history" || runId == null) {
      setSaved(null);
      return;
    }
    let live = true;
    api
      .scalpActiveHistoryRun(runId)
      .then((r) => live && setSaved({ id: r.id, as_of: r.as_of, total: r.total, failed: r.failed, rows: r.rows }))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Could not load that scan"));
    return () => {
      live = false;
    };
  }, [view, runId]);

  // The saved scan's accuracy check, and whether its day can be checked yet.
  const loadDetail = useCallback(async (id: number) => {
    try {
      setDetail(await smaApi.scanHistoryRun(id));
    } catch {
      setDetail(null);
    }
  }, []);
  useEffect(() => {
    setDetail(null);
    setCheckNote(null);
    if (view === "history" && saved?.id != null) void loadDetail(saved.id);
  }, [view, saved?.id, loadDetail]);
  const checking = !!detail?.checking.running && detail.checking.run_id === detail.id;
  useEffect(() => {
    if (!checking || !detail) return;
    const id = setTimeout(() => void loadDetail(detail.id), 4_000);
    return () => clearTimeout(id);
  }, [checking, detail, loadDetail]);
  const startCheck = async () => {
    if (!saved) return;
    setCheckNote(null);
    try {
      await smaApi.scanHistoryCheck(saved.id);
      setCheckNote("Checking — each stock's candles for that day are downloaded again; this takes a few minutes.");
      void loadDetail(saved.id);
    } catch (e: unknown) {
      setCheckNote(e instanceof Error ? e.message : "Could not start the check");
    }
  };
  const checkBy = useMemo(
    () => (detail?.check ? new Map(detail.check.rows.map((c) => [c.symbol, c])) : null),
    [detail?.check]
  );

  const runs = hist?.runs ?? [];
  const runAt = runs.findIndex((r) => r.id === runId);
  // A saved scan is filtered and ranked here the way the server ranks the live one.
  const source = useMemo(() => {
    if (view === "live") return data?.rows ?? [];
    const kept = (saved?.rows ?? []).filter((r) => r.value_cr >= minValue && (bias === "ALL" || r.bias === bias));
    return [...kept].sort((a, b) => RANK_KEY[sort](b) - RANK_KEY[sort](a)).slice(0, TOP);
  }, [view, data?.rows, saved, minValue, bias, sort]);

  // While a scan runs, check back sooner.
  useEffect(() => {
    if (!data?.running) return;
    const id = setTimeout(load, 5_000);
    return () => clearTimeout(id);
  }, [data, load]);

  const scanNow = async () => {
    setScanning(true);
    setNote(null);
    try {
      await api.scalpActiveScan();
      setNote("Scanning about 200 F&O stocks — this takes under a minute.");
      setTimeout(load, 3_000);
    } catch (e: unknown) {
      setNote(e instanceof Error ? e.message : "Could not start a scan");
    } finally {
      setScanning(false);
    }
  };

  const auto = data?.auto;
  const [autoBusy, setAutoBusy] = useState(false);
  const saveAuto = async (change: { on?: boolean; every_min?: number }) => {
    setAutoBusy(true);
    setNote(null);
    try {
      const next = await api.scalpActiveAuto(change);
      setData((d) => (d ? { ...d, auto: next } : d));
    } catch (e: unknown) {
      setNote(e instanceof Error ? e.message : "Could not change the auto scan");
    } finally {
      setAutoBusy(false);
    }
  };
  const every = auto?.every_min ?? 5;

  const watch = async (symbol: string) => {
    setAdding(symbol);
    setNote(null);
    try {
      await api.addToWatchlist(symbol);
      setNote(`${symbol} now streams on the desk. Its ATR, spread and score show in the monitor below within a few minutes.`);
      onWatched();
    } catch (e: unknown) {
      setNote(e instanceof Error ? e.message : "Could not add that stock");
    } finally {
      setAdding(null);
    }
  };

  const found = useMemo(
    () =>
      source.filter(
        (r) =>
          matchesText(query, r.symbol) &&
          (minSpeed <= 0 || Math.abs(r.speed_pct_min ?? 0) >= minSpeed) &&
          (priceMin <= 0 || r.ltp >= priceMin) &&
          (priceMax <= 0 || r.ltp <= priceMax) &&
          (minRvol <= 0 || (r.rvol ?? 0) >= minRvol) &&
          trendPasses(trendPick, tq.get(r.symbol)) &&
          ticks.shows(r.symbol)
      ),
    [source, query, minSpeed, priceMin, priceMax, minRvol, trendPick, tq, ticks]
  );

  // Read trend quality: run the Trend quality scan on the stocks the filters show (read-only, candles only).
  const readTrend = async () => {
    const symbols = found.map((r) => r.symbol).slice(0, TQ_MAX);
    if (!symbols.length) return;
    setTqBusy(true);
    setTqNote(`Reading trend quality of ${symbols.length} stock${symbols.length === 1 ? "" : "s"} on ${tqMinutes}-minute candles…`);
    try {
      await smaApi.chopScanStart(symbols, tqMinutes, true);
      const want = new Set(symbols);
      for (let i = 0; i < 120; i += 1) {
        await sleep(3_000);
        const scan = await smaApi.chopScan();
        if (!scan.running) {
          setTq(new Map(scan.rows.filter((row) => want.has(row.symbol)).map((row) => [row.symbol, row])));
          setTqNote(
            scan.error
              ? `Trend quality: ${scan.error}`
              : `Trend quality read ${clockIst(new Date().toISOString())} IST on ${tqMinutes}-minute candles (${scan.rows.filter((row) => want.has(row.symbol)).length} stocks).`
          );
          return;
        }
        setTqNote(`Reading trend quality… ${scan.done} of ${scan.total}`);
      }
      setTqNote("Trend quality is still running; press the button again in a minute.");
    } catch (e: unknown) {
      setTqNote(e instanceof Error ? e.message : "Could not read trend quality");
    } finally {
      setTqBusy(false);
    }
  };
  const { sorted: rows, sort: colSort, onSort } = useSort<ActiveStock, ColKey>(found, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "ltp":
        return r.ltp;
      case "change":
        return r.change_pct;
      case "speed":
        return r.speed_pct_min ?? null;
      case "value":
        return r.value_cr;
      case "rvol":
        return r.rvol;
      case "buy":
        return r.buy_share;
      case "vwap":
        return r.vwap_dist_pct;
      case "bias":
        return r.bias;
    }
  });
  const asOf = data?.as_of ? clockIst(data.as_of) + " IST" : null;
  const deliv = useDelivery(rows.map((r) => r.symbol));

  useEffect(() => {
    onStatus?.({ when: data?.as_of ?? null, running: !!data?.running });
  }, [onStatus, data?.as_of, data?.running]);

  const paged = usePaged(rows, "scalp.active.pageSize");
  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Flame size={15} className="text-amber-400" /> Where traders are now — most active F&amp;O stocks
        </h2>
        <div className="ml-auto inline-flex overflow-hidden rounded-md ring-1 ring-inset ring-slate-700" role="group" aria-label="Live or saved scans">
          {(["live", "history", "test"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              aria-pressed={view === v}
              className={clsx(
                "inline-flex min-h-9 items-center gap-1.5 px-3 text-xs font-semibold",
                view === v ? "bg-accentSky/15 text-accentSky" : "text-slate-300 hover:bg-white/5"
              )}
            >
              {v === "live" ? <Flame size={13} /> : v === "history" ? <History size={13} /> : <FlaskConical size={13} />}
              {v === "live" ? "Live" : v === "history" ? "History" : "Which column finds the trend?"}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={scanNow}
          disabled={scanning || data?.running || data?.connected === false || paused}
          title={paused ? "Scans are paused at the top of the page" : undefined}
          className={clsx(
            "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50",
            view !== "live" && "hidden"
          )}
        >
          {scanning || data?.running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
        </button>
        {view === "live" ? <StopScan running={data?.running} stop={() => api.scalpActiveStop()} onStopped={() => void load()} /> : null}
      </div>
      {view === "test" ? (
        <ActiveTrendTest />
      ) : (
        <>
      {view === "live" && auto ? (
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-border bg-surface2 px-3 py-2 text-xs text-slate-300">
          <label className="inline-flex min-h-9 items-center gap-2 font-semibold text-slate-200">
            <input
              type="checkbox"
              checked={auto.on}
              disabled={autoBusy}
              onChange={(e) => void saveAuto({ on: e.target.checked })}
              className="accent-[#10B981]"
            />
            Auto scan
          </label>
          <label className="inline-flex items-center gap-1.5">
            every
            <select
              value={auto.every_min}
              disabled={autoBusy}
              onChange={(e) => void saveAuto({ every_min: Number(e.target.value) })}
              className="min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
            >
              {auto.every_choices.map((m) => (
                <option key={m} value={m}>
                  {m} min
                </option>
              ))}
            </select>
          </label>
          <span className="text-slate-400">
            {!auto.on
              ? "Off: nothing is scanned until you press Scan now."
              : paused
                ? "Waiting: scans are paused at the top of the page."
                : auto.next_at
                  ? `The server scans by itself while the market is open; next at ${clockIst(auto.next_at)}.`
                  : "The server scans by itself while the market is open (09:15–15:30), no browser needed."}
          </span>
          {auto.on && auto.last_error ? <span className="text-amber-300">{auto.last_error}</span> : null}
        </div>
      ) : null}
      <Explain className="mb-3" lead={`The most traded F&O stocks, ranked ${auto?.on ? `every ${every} minutes` : "on each Scan now"}. Market data only — nothing here places an order.`}>
        {auto?.on ? `Every ${every} minutes` : "On each Scan now"} while the market is open (Auto scan above sets how often), one Groww quote for each NSE stock that has futures. Ranked by money
        traded today, volume against its 20-day usual for this time of day, and how many shares buyers vs sellers have
        waiting. Speed is the price change since the previous scan, in % per minute. Every scan is saved: History shows
        any past scan (same filters, sorting and CSV). Watch adds a stock to the desk feed so the monitor below scores it
        for scalping.
      </Explain>

      {view === "history" && (
        <div className="mb-3 flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface2/60 p-3 text-xs">
          <label className="flex flex-col gap-1 text-slate-400">
            Day
            <select
              value={histDay}
              onChange={(e) => {
                setHistDay(e.target.value);
                setRunId(null);
              }}
              className="min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
            >
              {(hist?.days ?? []).map((d) => (
                <option key={d.day} value={d.day}>
                  {d.day} ({d.runs} scans)
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-slate-400">
            Scan at
            <select
              value={runId ?? ""}
              onChange={(e) => setRunId(Number(e.target.value))}
              className="min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {clockIst(r.as_of)} IST · {r.row_count} stocks
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => runAt > 0 && setRunId(runs[runAt - 1].id)}
            disabled={runAt <= 0}
            title="Earlier scan"
            aria-label="Earlier scan"
            className="inline-flex min-h-9 items-center rounded-md px-2 text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-40"
          >
            <ChevronLeft size={14} />
          </button>
          <button
            type="button"
            onClick={() => runAt >= 0 && runAt < runs.length - 1 && setRunId(runs[runAt + 1].id)}
            disabled={runAt < 0 || runAt >= runs.length - 1}
            title="Later scan"
            aria-label="Later scan"
            className="inline-flex min-h-9 items-center rounded-md px-2 text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-40"
          >
            <ChevronRight size={14} />
          </button>
          <button
            type="button"
            onClick={() => saved && saveFile(`most-active-${histDay}-${clockIst(saved.as_of).replace(":", "")}.csv`, activeCsv(rows), "text/csv;charset=utf-8")}
            disabled={!saved || rows.length === 0}
            className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-40"
          >
            <Download size={13} /> CSV
          </button>
          {histBusy && <Loader2 size={14} className="animate-spin text-slate-400" />}
          <p className="w-full text-slate-500">
            {hist && hist.days.length === 0
              ? "No saved scans yet. Each scan is saved from now on (each Scan now and each auto scan while the market is open)."
              : `Saved scans are kept ${hist?.keep_days ?? 90} days. Filters, Rank by, column sorting and Find a stock work on the saved scan.`}
          </p>
        </div>
      )}

      <FilterRow
        status={
          view === "history"
            ? saved
              ? `Saved scan ${clockIst(saved.as_of)} IST on ${histDay} · ${saved.rows.length}${saved.total ? ` of ${saved.total}` : ""} stocks${saved.failed ? ` · ${saved.failed} failed` : ""}`
              : null
            : data
              ? `${asOf ? `Scanned ${asOf} · ${data.scanned} of ${data.universe} stocks` : "No scan yet"}${data.failed ? ` · ${data.failed} failed` : ""}`
              : null
        }
      >
        <SelectFilter
          label="Rank by"
          value={sort}
          onChange={(v) => setSort(v as ActiveSort)}
          options={(Object.keys(SORT_LABEL) as ActiveSort[]).map((k) => ({ value: k, label: SORT_LABEL[k] }))}
          className="w-40"
        />
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
        <NumberFilter label="Min traded today" value={minValue} onChange={setMinValue} min={0} max={500} step={5} prefix="₹" suffix="cr" />
        <NumberFilter label="Min speed, either way" value={minSpeed} onChange={setMinSpeed} min={0} max={1} step={0.01} suffix="%/m" />
        <NumberFilter label="Price from (0 = any)" value={priceMin} onChange={setPriceMin} min={0} max={50000} step={10} prefix="₹" />
        <NumberFilter label="Price to (0 = any)" value={priceMax} onChange={setPriceMax} min={0} max={50000} step={10} prefix="₹" />
        <NumberFilter
          label="Vol vs usual at least"
          value={minRvol}
          onChange={setMinRvol}
          min={0}
          max={10}
          step={0.1}
          suffix="×"
          hint="Volume so far against the 20-day usual for this time of day; 0 = any"
        />
        <SelectFilter
          label="Trend quality"
          value={trendPick}
          onChange={(v) => setTrendPick(v as TrendPick)}
          options={TREND_PICKS}
          className="w-36"
        />
        <TextFilter value={query} onChange={setQuery} />
      </FilterRow>

      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <button
          type="button"
          onClick={() => void readTrend()}
          disabled={tqBusy || found.length === 0 || paused}
          title="Run the Trend quality scan (strong trend, crosses today, phase) on the stocks the filters show now. Read-only; it replaces the Trend quality tab's last result."
          className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-3 font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
        >
          {tqBusy ? <Loader2 size={13} className="animate-spin" /> : <Activity size={13} />} Read trend quality
        </button>
        <label className="flex items-center gap-1">
          on
          <select
            value={tqMinutes}
            onChange={(e) => setTqMinutes(Number(e.target.value) as CrossScanMinutes)}
            className="min-h-8 rounded-md border border-slate-700 bg-surface2 px-1 text-xs text-slate-100"
          >
            {TQ_SIZES.map((m) => (
              <option key={m} value={m}>
                {m}-min candles
              </option>
            ))}
          </select>
        </label>
        {tq.size > 0 && (
          <button type="button" onClick={() => setTq(new Map())} className="text-slate-500 hover:text-slate-300">
            Clear trend columns
          </button>
        )}
        {tqNote && <span className="text-accentSky">{tqNote}</span>}
        {view === "history" && saved && (
          <span className="ml-auto inline-flex flex-wrap items-center gap-2">
            {detail?.check ? (
              <span className="text-profit">Checked ✓</span>
            ) : null}
            <button
              type="button"
              onClick={() => void startCheck()}
              disabled={!detail?.can_check || checking || !!detail?.checking.running}
              title={detail?.can_check ? "Download that day's candles again and see what each stock did after the scan (its call = its Bias, else its day move)" : "Available after 15:30 on the scan's day"}
              className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-3 font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
            >
              {checking ? <Loader2 size={13} className="animate-spin" /> : <Target size={13} />}
              {checking ? `Checking ${detail?.checking.done ?? 0}/${detail?.checking.total ?? 0}` : detail?.check ? "Check again" : "Check accuracy"}
            </button>
          </span>
        )}
      </div>
      {checkNote && <p className="mb-2 text-xs text-accentSky">{checkNote}</p>}
      {view === "history" && detail?.check && (
        <div className="mb-2 rounded-lg border border-border bg-surface2/60 p-2 text-xs text-slate-300">
          {detail.check.summary.map((g) => (
            <p key={g.key}>
              <span className="font-semibold text-slate-100">{g.label}</span> ({g.count}): went the call&apos;s way after 30 min{" "}
              {g.right_after_30_pct == null ? "—" : `${g.right_after_30_pct}%`} (avg {pct(g.avg_after_30_pct)}), at the close{" "}
              {g.right_close_pct == null ? "—" : `${g.right_close_pct}%`} (avg {pct(g.avg_close_pct)}), best on the way {pct(g.avg_best_pct)}
            </p>
          ))}
          {detail.check.error ? <p className="text-amber-300">{detail.check.error}</p> : null}
        </div>
      )}

      {note && <p className="mb-2 text-xs text-accentSky">{note}</p>}
      {error && <p className="mb-2 text-xs text-loss">{error}</p>}
      {view === "history" && rows.length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-500">
          {histBusy ? "Loading…" : saved ? "No stock in this saved scan matches the filters." : "Pick a day and a scan."}
        </p>
      ) : view === "live" && data?.connected === false ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          Groww is not connected. Go to Settings and click Connect Live Data, then Scan now.
        </p>
      ) : view === "live" && data?.error && rows.length === 0 ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{data.error}</p>
      ) : rows.length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-500">
          {data?.running
            ? "Scanning…"
            : data?.market_open
              ? auto?.on ? `Nothing yet. Click Scan now, or wait for the next auto scan (every ${every} min).` : "Nothing yet. Click Scan now."
              : "Scans run while the market is open (09:15–15:30). Click Scan now to read the last prices."}
        </p>
      ) : (
        <div className="overflow-x-auto" ref={paged.anchor}>
          {onArmMany ? <TickBar ticks={ticks} onArm={onArmMany} /> : null}
          <Pager p={paged} className="mb-2" />
          <ColumnHint moved={cols.moved} onReset={cols.reset} />
          <DeliveryNote day={deliv.day} error={deliv.error} />
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
              <tr>
                {onArmMany ? <TickHead ticks={ticks} symbols={rows.map((r) => r.symbol)} /> : null}
                {inOrder(cols.order, {
                  rank: (
                    <th {...cols.dragProps("rank")} className={clsx("px-1 pb-2 text-left font-medium", cols.dragProps("rank").className)}>
                      #
                    </th>
                  ),
                  symbol: <SortTh label="Stock" k="symbol" sort={colSort} onSort={onSort} text drag={cols.dragProps("symbol")} />,
                  ltp: <SortTh label="LTP" k="ltp" sort={colSort} onSort={onSort} num drag={cols.dragProps("ltp")} />,
                  change: <SortTh label="Day" k="change" sort={colSort} onSort={onSort} num drag={cols.dragProps("change")} />,
                  speed: (
                    <SortTh
                      label="Speed"
                      k="speed"
                      sort={colSort}
                      onSort={onSort}
                      num
                      title="Price change since the previous scan, in % per minute (none on the day's first scan)"
                      drag={cols.dragProps("speed")}
                    />
                  ),
                  value: <SortTh label="₹ cr" k="value" sort={colSort} onSort={onSort} num title="Volume × average price today" drag={cols.dragProps("value")} />,
                  rvol: (
                    <SortTh
                      label="Vol vs usual"
                      k="rvol"
                      sort={colSort}
                      onSort={onSort}
                      num
                      title="Volume so far against the 20-day average scaled to the time of day"
                      drag={cols.dragProps("rvol")}
                    />
                  ),
                  buy: (
                    <SortTh
                      label="Buyers vs sellers"
                      k="buy"
                      sort={colSort}
                      onSort={onSort}
                      title="Total buy vs sell quantity waiting in the order book"
                      drag={cols.dragProps("buy")}
                    />
                  ),
                  vwap: (
                    <SortTh
                      label="vs avg"
                      k="vwap"
                      sort={colSort}
                      onSort={onSort}
                      num
                      title="Price against Groww's average traded price today (VWAP)"
                      drag={cols.dragProps("vwap")}
                    />
                  ),
                  bias: <SortTh label="Bias" k="bias" sort={colSort} onSort={onSort} text drag={cols.dragProps("bias")} />,
                  actions: (
                    <th {...cols.dragProps("actions")} className={clsx("px-1 pb-2 text-right font-medium", cols.dragProps("actions").className)}>
                      Actions
                    </th>
                  ),
                })}
                <DeliveryHead />
                {tq.size > 0 && (
                  <>
                    <th className="pb-2 text-left font-medium" title="Strong trend, else the Trend quality score (trending / mixed / choppy)">Trend</th>
                    <th className="pb-2 text-right font-medium" title="SMA crosses today on the chosen candle">Crosses</th>
                    <th className="pb-2 text-left font-medium" title="Trend phase now">Phase</th>
                  </>
                )}
                {checkBy && (
                  <>
                    <th className="pb-2 text-left font-medium" title="The call checked: the row's Bias, else its day move">Call</th>
                    <th className="pb-2 text-right font-medium">30 min</th>
                    <th className="pb-2 text-right font-medium">1 hour</th>
                    <th className="pb-2 text-right font-medium">At close</th>
                    <th className="pb-2 text-right font-medium" title="Best move on the way to the close">Best</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {paged.shown.map((r, i) => (
                <ActiveRowView
                  key={r.symbol}
                  order={cols.order}
                  rank={paged.start + i + 1}
                  row={r}
                  watching={watching.has(r.symbol)}
                  adding={adding === r.symbol}
                  onWatch={() => watch(r.symbol)}
                  armedOn={armed.get(r.symbol.toUpperCase()) ?? []}
                  arming={arming === r.symbol}
                  onArm={() => onArm(r.symbol)}
                  tick={onArmMany ? <TickCell ticks={ticks} symbol={r.symbol} /> : null}
                  trend={tq.size > 0 ? tq.get(r.symbol) ?? null : undefined}
                  deliv={deliv.rows.get(r.symbol.toUpperCase())}
                  check={checkBy ? checkBy.get(r.symbol) ?? null : undefined}
                />
              ))}
            </tbody>
          </table>
          <Pager p={paged} className="mt-2" />
        </div>
      )}
        </>
      )}
    </section>
  );
}

function ActiveRowView({
  order,
  rank,
  row,
  watching,
  adding,
  onWatch,
  armedOn,
  arming,
  onArm,
  tick,
  trend,
  check,
  deliv,
}: {
  tick?: ReactNode;
  /** NSE's delivery % of the stock (latest published day). */
  deliv?: DeliveryRow;
  /** The stock's Trend quality row (undefined = the columns are not shown). */
  trend?: ChopScanRow | null;
  /** The saved scan's accuracy check for this stock (undefined = not checked). */
  check?: ScanCheckRow | null;
  order: readonly Col[];
  rank: number;
  row: ActiveStock;
  watching: boolean;
  adding: boolean;
  onWatch: () => void;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  const share = row.buy_share;
  return (
    <tr className="whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2">
      {tick}
      {inOrder(order, {
        rank: (
          <td className="py-1.5 pr-2 text-xs text-slate-500">{rank}</td>
        ),
        symbol: (
          <td className="py-1.5 pr-2 font-semibold text-slate-100"><GrowwLink symbol={row.symbol} /></td>
        ),
        ltp: (
          <td className="py-1.5 pr-2 text-right font-mono">{row.ltp.toFixed(2)}</td>
        ),
        change: (
          <td className={clsx("py-1.5 pr-2 text-right font-mono", (row.change_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>
            {pct(row.change_pct)}
          </td>
        ),
        speed: (
          <td
            className={clsx(
              "py-1.5 pr-2 text-right font-mono",
              row.speed_pct_min == null ? "text-slate-500" : row.speed_pct_min >= 0 ? "text-profit" : "text-loss",
              row.speed_pct_min != null && Math.abs(row.speed_pct_min) >= 0.1 && "font-semibold"
            )}
          >
            {row.speed_pct_min == null ? "—" : `${row.speed_pct_min > 0 ? "+" : ""}${row.speed_pct_min.toFixed(3)}%/m`}
          </td>
        ),
        value: (
          <td className="py-1.5 pr-2 text-right font-mono">{row.value_cr.toFixed(0)}</td>
        ),
        rvol: (
          <td
            className={clsx(
              "py-1.5 pr-2 text-right font-mono",
              row.rvol != null && row.rvol >= 2 ? "font-semibold text-amber-300" : "text-slate-300"
            )}
          >
            {row.rvol == null ? "—" : `${row.rvol.toFixed(1)}×`}
          </td>
        ),
        buy: (
          <td className="py-1.5 pr-2">
            {share == null ? (
              <span className="text-xs text-slate-500">—</span>
            ) : (
              <div className="flex items-center gap-1.5" title={`Buy ${row.buy_qty?.toLocaleString("en-IN")} · Sell ${row.sell_qty?.toLocaleString("en-IN")}`}>
                <div className="flex h-1.5 w-16 overflow-hidden rounded bg-loss/60">
                  <div className="h-full bg-profit" style={{ width: `${Math.round(share * 100)}%` }} />
                </div>
                <span className="font-mono text-xs text-slate-300">{Math.round(share * 100)}%</span>
              </div>
            )}
          </td>
        ),
        vwap: (
          <td className={clsx("py-1.5 pr-2 text-right font-mono", (row.vwap_dist_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>
            {pct(row.vwap_dist_pct)}
          </td>
        ),
        bias: (
          <td className="py-1.5 pr-2">
            {row.bias === "LONG" ? (
              <span className="inline-flex items-center gap-0.5 text-xs font-semibold text-profit">
                <ArrowUp size={12} /> Long
              </span>
            ) : row.bias === "SHORT" ? (
              <span className="inline-flex items-center gap-0.5 text-xs font-semibold text-loss">
                <ArrowDown size={12} /> Short
              </span>
            ) : (
              <span className="text-xs text-slate-500">—</span>
            )}
          </td>
        ),
        actions: (
          <td className="py-1.5 text-right">
            <div className="flex justify-end gap-1.5">
              <button
                type="button"
                onClick={onWatch}
                disabled={watching || adding}
                title={watching ? "Already streaming on the desk" : "Stream on the desk so the monitor below scores it"}
                className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-xs text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
              >
                {adding ? <Loader2 size={12} className="animate-spin" /> : <Eye size={12} />}
                {watching ? "Watching" : "Watch"}
              </button>
              {armedOn.length ? (
                <span className="self-center text-[11px] font-semibold text-profit" title={`Armed on ${armedOn.join(", ")}`}>
                  {armedOn.join(" · ")}
                </span>
              ) : null}
              <button
                type="button"
                onClick={onArm}
                disabled={arming}
                title="Arm on a bot (asks which)"
                className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-xs font-semibold text-accentViolet ring-1 ring-inset ring-violet-400/40 hover:bg-violet-500/10 disabled:opacity-50"
              >
                {arming ? <Loader2 size={12} className="animate-spin" /> : null}
                Arm
              </button>
            </div>
          </td>
        ),
      })}
      <DeliveryCells d={deliv} />
      {trend !== undefined && (
        <>
          <td className="py-1.5 text-xs">
            {trend == null ? (
              <span className="text-slate-500">—</span>
            ) : trend.trend ? (
              <span className={clsx("font-semibold", trend.trend === "STRONG_BULLISH" ? "text-profit" : "text-loss")}>
                Strong {trend.trend === "STRONG_BULLISH" ? "↑" : "↓"}
              </span>
            ) : (
              <span className={trend.score === "CHOPPY" ? "text-amber-300" : "text-slate-300"}>{trend.score.toLowerCase()}</span>
            )}
          </td>
          <td className="py-1.5 text-right font-mono text-xs">{trend?.crosses_today ?? "—"}</td>
          <td className="py-1.5 text-xs">
            {trend?.phase ? (
              <span className={trend.phase === "BULL" ? "text-profit" : trend.phase === "BEAR" ? "text-loss" : "text-slate-400"}>
                {trend.phase === "BULL" ? "up" : trend.phase === "BEAR" ? "down" : "choppy"}
              </span>
            ) : (
              <span className="text-slate-500">—</span>
            )}
          </td>
        </>
      )}
      {check !== undefined && (
        <>
          <td className="py-1.5 text-xs">
            {check?.side ? (
              <span className={check.side === "BULLISH" ? "text-profit" : "text-loss"}>{check.side === "BULLISH" ? "↑ up" : "↓ down"}</span>
            ) : (
              <span className="text-slate-500">—</span>
            )}
          </td>
          {[check?.after_30_pct, check?.after_60_pct, check?.close_pct, check?.best_pct].map((v, i) => (
            <td key={i} className={clsx("py-1.5 text-right font-mono text-xs", v == null ? "text-slate-500" : v >= 0 ? "text-profit" : "text-loss")}>
              {v == null ? "—" : pct(v)}
            </td>
          ))}
        </>
      )}
    </tr>
  );
}
