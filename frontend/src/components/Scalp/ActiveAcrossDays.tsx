"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { CalendarRange, Download, Loader2 } from "lucide-react";
import { smaApi, type ActiveAcross } from "@/lib/smaApi";
import { FilterField, FilterGroup, FilterRow, NumberFilter, SelectFilter, TextFilter, Toggle, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { GrowwLink } from "@/components/ui/growwLink";
import { NewTag } from "@/components/ui/NewTag";
import { saveFile } from "@/lib/chartTable";

const MAX_DAYS = 10;
const STORE = "scalp.active.across";

/** One stock in one saved Most active scan (the row as saved, slimmed: scan_history.ACTIVE_KEEP). */
type Row = {
  symbol: string;
  ltp: number | null;
  change_pct: number | null;
  value_cr: number | null;
  rvol: number | null;
  buy_share: number | null;
  vwap_dist_pct: number | null;
  range_pct: number | null;
  bias: string | null;
  speed_pct_min: number | null;
};

type Who = "ANY" | "BUY" | "SELL";
type Filter = {
  bias: "ALL" | "LONG" | "SHORT";
  who: Who;
  /** Buyers (or sellers) hold at least this % of the waiting orders; 0 = any. */
  pressure: number;
  minRvol: number;
  minValue: number;
  /** |day move| at least, %; 0 = any. */
  minMove: number;
  /** |speed| at least, % per minute; 0 = any. */
  minSpeed: number;
  priceMin: number;
  priceMax: number;
};
const NO_FILTER: Filter = { bias: "ALL", who: "ANY", pressure: 0, minRvol: 0, minValue: 0, minMove: 0, minSpeed: 0, priceMin: 0, priceMax: 0 };

/** One stock on one day: every saved scan of that day in which it passed the filters. */
type Hit = {
  key: string;
  day: string;
  symbol: string;
  firstAt: string;
  lastAt: string;
  matched: number;
  scans: number;
  firstPrice: number | null;
  endPrice: number | null;
  movePct: number | null;
  buyersFirst: number | null;
  rvolFirst: number | null;
  valueFirst: number | null;
  biasFirst: string | null;
};
type Stock = { key: string; symbol: string; days: number; matched: number; firstDay: string; firstAt: string; avgMove: number | null };
/** One stock in one saved scan (the timeline view). */
type Seen = Row & { key: string; day: string; at: string; change: string; dir: number };

type Table = "day" | "stock" | "scan";
type SortKey = "day" | "symbol" | "first" | "last" | "matched" | "firstPrice" | "move" | "buyers" | "rvol" | "value";
type StockSort = "symbol" | "days" | "matched" | "first" | "avgMove";
type ScanSort = "day" | "time" | "symbol" | "ltp" | "move" | "buyers" | "rvol" | "value" | "vwap" | "speed" | "bias" | "change";

const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
const dayText = (day: string) =>
  new Date(`${day}T12:00:00+05:30`).toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
const pct = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const buyersPct = (r: Row) => (r.buy_share == null ? null : r.buy_share * 100);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const tone = (v: number | null | undefined) => ((v ?? 0) > 0 ? "text-profit" : (v ?? 0) < 0 ? "text-loss" : "");
const biasText = (b: string | null) => (b === "LONG" ? "Long" : b === "SHORT" ? "Short" : "—");

function toRows(cols: string[], rows: (string | number | null)[][]): Row[] {
  const at = (name: string) => cols.indexOf(name);
  const i = {
    symbol: at("symbol"), ltp: at("ltp"), change_pct: at("change_pct"), value_cr: at("value_cr"), rvol: at("rvol"),
    buy_share: at("buy_share"), vwap_dist_pct: at("vwap_dist_pct"), range_pct: at("range_pct"), bias: at("bias"),
    speed_pct_min: at("speed_pct_min"),
  };
  return rows.map((r) => ({
    symbol: String(r[i.symbol] ?? ""),
    ltp: num(r[i.ltp]),
    change_pct: num(r[i.change_pct]),
    value_cr: num(r[i.value_cr]),
    rvol: num(r[i.rvol]),
    buy_share: num(r[i.buy_share]),
    vwap_dist_pct: num(r[i.vwap_dist_pct]),
    range_pct: num(r[i.range_pct]),
    bias: i.bias >= 0 && r[i.bias] != null ? String(r[i.bias]) : null,
    speed_pct_min: num(r[i.speed_pct_min]),
  }));
}

/** The Most active card's filters, read on one saved row. */
function passes(r: Row, f: Filter): boolean {
  if (f.bias !== "ALL" && r.bias !== f.bias) return false;
  if (f.pressure > 0) {
    const b = buyersPct(r);
    if (b == null) return false;
    const buyers = b >= f.pressure;
    const sellers = 100 - b >= f.pressure;
    if (f.who === "BUY" ? !buyers : f.who === "SELL" ? !sellers : !(buyers || sellers)) return false;
  } else if (f.who !== "ANY") {
    const b = buyersPct(r);
    if (b == null || (f.who === "BUY" ? b <= 50 : b >= 50)) return false;
  }
  if (f.minRvol > 0 && (r.rvol == null || r.rvol < f.minRvol)) return false;
  if (f.minValue > 0 && (r.value_cr ?? 0) < f.minValue) return false;
  if (f.minMove > 0 && Math.abs(r.change_pct ?? 0) < f.minMove) return false;
  if (f.minSpeed > 0 && Math.abs(r.speed_pct_min ?? 0) < f.minSpeed) return false;
  if (f.priceMin > 0 && (r.ltp ?? 0) < f.priceMin) return false;
  if (f.priceMax > 0 && (r.ltp ?? 0) > f.priceMax) return false;
  return true;
}

/** What changed since the stock's previous scan that day. dir: +1 the buyers / sellers lean got stronger, -1 weaker. */
function changeText(prev: Row | undefined, r: Row): { change: string; dir: number } {
  if (!prev) return { change: "first scan", dir: 0 };
  const out: string[] = [];
  if (prev.bias !== r.bias) out.push(`bias → ${biasText(r.bias)}`);
  const a = buyersPct(prev);
  const b = buyersPct(r);
  if (a != null && b != null) {
    if ((a >= 50) !== (b >= 50)) out.push(b >= 50 ? "buyers took over" : "sellers took over");
    else if (Math.abs(b - a) >= 10) out.push(`buyers ${a.toFixed(0)}% → ${b.toFixed(0)}%`);
  }
  const leanParts = out.length;
  const volDelta = prev.rvol != null && r.rvol != null ? r.rvol - prev.rvol : 0;
  if (Math.abs(volDelta) >= 0.5) out.push(`vol ${prev.rvol!.toFixed(1)}× → ${r.rvol!.toFixed(1)}×`);
  if (out.length === 0) return { change: "", dir: 0 };
  // Coloured by the buyers / sellers lead when it changed, else by the volume (busier = green).
  if (leanParts === 0) return { change: out.join(" · "), dir: volDelta > 0 ? 1 : -1 };
  const lean = (x: number | null) => (x == null ? 0 : Math.abs(x - 50));
  const d = lean(b) - lean(a);
  return { change: out.join(" · "), dir: d > 2 ? 1 : d < -2 ? -1 : 0 };
}

/**
 * The Most active filters over every saved scan of one or more days (Most active → History). A stock is listed for a
 * day when it passed the filters in at least one scan that day; "Every scan" shows how its buyers / sellers, volume vs
 * usual and money traded built up scan by scan. Read-only: nothing is armed until Arm is pressed, no order is placed.
 */
export function ActiveAcrossDays({
  days,
  armed,
  arming,
  onArm,
  onArmMany,
}: {
  /** The days with saved Most active scans, newest first. */
  days: { day: string; runs: number }[];
  armed?: Map<string, string[]>;
  arming?: string | null;
  onArm: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [f, setF] = useState<Filter>(NO_FILTER);
  const [query, setQuery] = useState("");
  const [table, setTable] = useState<Table>("day");
  const [changesOnly, setChangesOnly] = useState(false);
  const [data, setData] = useState<ActiveAcross | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof Filter>(key: K, value: Filter[K]) => setF((cur) => ({ ...cur, [key]: value }));

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORE);
      if (raw) {
        const s = JSON.parse(raw);
        if (s.f && typeof s.f === "object") setF({ ...NO_FILTER, ...s.f });
      }
    } catch {
      /* storage blocked: defaults */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ f }));
    } catch {
      /* storage blocked */
    }
  }, [f]);
  useEffect(() => {
    if (picked.length === 0 && days.length > 0) setPicked([days[0].day]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  useEffect(() => {
    if (picked.length === 0) {
      setData(null);
      return;
    }
    let live = true;
    setLoading(true);
    smaApi
      .scanHistoryAcrossActive(picked)
      .then((d) => {
        if (!live) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [picked]);

  const runs = useMemo(
    () => (data?.runs ?? []).map((run) => ({ id: run.id, day: run.day, at: run.as_of, rows: toRows(data?.cols ?? [], run.rows) })),
    [data]
  );

  const hits: Hit[] = useMemo(() => {
    const out = new Map<string, Hit>();
    const scansPerDay = new Map<string, number>();
    const lastPrice = new Map<string, number | null>();
    for (const run of runs) {
      scansPerDay.set(run.day, (scansPerDay.get(run.day) ?? 0) + 1);
      for (const r of run.rows) {
        const key = `${run.day}|${r.symbol}`;
        lastPrice.set(key, r.ltp);
        if (!matchesText(query, r.symbol) || !passes(r, f)) continue;
        const h = out.get(key);
        if (!h) {
          out.set(key, {
            key, day: run.day, symbol: r.symbol, firstAt: run.at, lastAt: run.at, matched: 1, scans: 0,
            firstPrice: r.ltp, endPrice: null, movePct: null, buyersFirst: buyersPct(r), rvolFirst: r.rvol,
            valueFirst: r.value_cr, biasFirst: r.bias,
          });
        } else {
          h.lastAt = run.at;
          h.matched += 1;
        }
      }
    }
    for (const h of out.values()) {
      h.scans = scansPerDay.get(h.day) ?? 0;
      h.endPrice = lastPrice.get(h.key) ?? null;
      h.movePct = h.firstPrice && h.endPrice ? ((h.endPrice - h.firstPrice) / h.firstPrice) * 100 : null;
    }
    return [...out.values()];
  }, [runs, f, query]);

  const seen: Seen[] = useMemo(() => {
    const out: Seen[] = [];
    const last = new Map<string, Row>();
    for (const run of runs) {
      for (const r of run.rows) {
        const track = `${run.day}|${r.symbol}`;
        const prev = last.get(track);
        last.set(track, r);
        if (!matchesText(query, r.symbol) || !passes(r, f)) continue;
        const { change, dir } = changeText(prev, r);
        if (changesOnly && (!change || change === "first scan")) continue;
        out.push({ ...r, key: `${run.id}|${r.symbol}`, day: run.day, at: run.at, change, dir });
      }
    }
    return out;
  }, [runs, f, query, changesOnly]);

  const stocks: Stock[] = useMemo(() => {
    const by = new Map<string, Hit[]>();
    for (const h of hits) by.set(h.symbol, [...(by.get(h.symbol) ?? []), h]);
    return [...by.entries()].map(([symbol, list]) => {
      const ordered = [...list].sort((a, b) => a.firstAt.localeCompare(b.firstAt));
      const moves = list.map((h) => h.movePct).filter((v): v is number => v != null);
      return {
        key: symbol,
        symbol,
        days: list.length,
        matched: list.reduce((n, h) => n + h.matched, 0),
        firstDay: ordered[0].day,
        firstAt: ordered[0].firstAt,
        avgMove: moves.length ? moves.reduce((a, b) => a + b, 0) / moves.length : null,
      };
    });
  }, [hits]);

  const byDay = useSort<Hit, SortKey>(hits, (h, k) => {
    switch (k) {
      case "day":
        return h.firstAt;
      case "symbol":
        return h.symbol;
      case "first":
        return clock(h.firstAt);
      case "last":
        return clock(h.lastAt);
      case "matched":
        return h.matched;
      case "firstPrice":
        return h.firstPrice;
      case "move":
        return h.movePct;
      case "buyers":
        return h.buyersFirst;
      case "rvol":
        return h.rvolFirst;
      case "value":
        return h.valueFirst;
    }
  });
  const byStock = useSort<Stock, StockSort>(stocks, (s, k) =>
    k === "symbol" ? s.symbol : k === "days" ? s.days : k === "matched" ? s.matched : k === "first" ? s.firstAt : s.avgMove
  );
  const scanSort = useSort<Seen, ScanSort>(seen, (r, k) => {
    switch (k) {
      case "day":
        return r.day;
      case "time":
        return r.at;
      case "symbol":
        return r.symbol;
      case "ltp":
        return r.ltp;
      case "move":
        return r.change_pct;
      case "buyers":
        return r.buy_share;
      case "rvol":
        return r.rvol;
      case "value":
        return r.value_cr;
      case "vwap":
        return r.vwap_dist_pct;
      case "speed":
        return r.speed_pct_min;
      case "bias":
        return r.bias;
      case "change":
        return r.dir;
    }
  });
  const paged = usePaged(byDay.sorted, "scalp.active.across.pageSize");
  const pagedStocks = usePaged(byStock.sorted, "scalp.active.across.pageSize");
  const pagedScans = usePaged(scanSort.sorted, "scalp.active.across.pageSize");

  const scans = runs.length;
  const symbols = [...new Set(hits.map((h) => h.symbol))];
  const toggleDay = (d: string) =>
    setPicked((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : cur.length >= MAX_DAYS ? cur : [...cur, d]));
  const timelineOf = (symbol: string) => {
    setQuery(symbol);
    setTable("scan");
  };
  const tag = picked.slice().sort().join("_");

  const csv = () => {
    if (table === "scan") {
      const head = "day,time,stock,price,day_move_pct,buyers_pct,vol_vs_usual,money_cr,vwap_dist_pct,speed_pct_min,bias,change";
      const lines = scanSort.sorted.map((r) =>
        [r.day, clock(r.at), r.symbol, r.ltp ?? "", r.change_pct ?? "", buyersPct(r)?.toFixed(1) ?? "", r.rvol ?? "", r.value_cr ?? "",
          r.vwap_dist_pct ?? "", r.speed_pct_min ?? "", r.bias ?? "", `"${r.change}"`].join(",")
      );
      saveFile(`most-active-timeline-${tag}.csv`, [head, ...lines].join("\n"), "text/csv");
      return;
    }
    if (table === "stock") {
      const head = "stock,days,scans_matched,first_day,first_time,avg_move_pct";
      const lines = byStock.sorted.map((s) => [s.symbol, s.days, s.matched, s.firstDay, clock(s.firstAt), s.avgMove?.toFixed(2) ?? ""].join(","));
      saveFile(`most-active-across-stocks-${tag}.csv`, [head, ...lines].join("\n"), "text/csv");
      return;
    }
    const head = "day,stock,first_matched,last_matched,scans_matched,scans_that_day,price_first,price_day_end,move_pct,buyers_pct_first,vol_vs_usual_first,money_cr_first,bias_first";
    const lines = byDay.sorted.map((h) =>
      [h.day, h.symbol, clock(h.firstAt), clock(h.lastAt), h.matched, h.scans, h.firstPrice ?? "", h.endPrice ?? "", h.movePct?.toFixed(2) ?? "",
        h.buyersFirst?.toFixed(1) ?? "", h.rvolFirst ?? "", h.valueFirst ?? "", h.biasFirst ?? ""].join(",")
    );
    saveFile(`most-active-across-${tag}.csv`, [head, ...lines].join("\n"), "text/csv");
  };

  const armCell = (symbol: string) => {
    const on = armed?.get(symbol);
    return (
      <button
        type="button"
        disabled={arming === symbol}
        onClick={() => onArm(symbol)}
        className={clsx("min-h-7 rounded-md px-2 text-[11px] ring-1 ring-inset", on?.length ? "text-profit ring-profit/40" : "text-slate-300 ring-slate-700 hover:bg-white/5")}
        title={on?.length ? `Armed on ${on.join(", ")}` : "Arm this stock (Trade list only, no order)"}
      >
        {arming === symbol ? "…" : on?.length ? "Armed" : "Arm"}
      </button>
    );
  };
  const timelineLink = (symbol: string) => (
    <button type="button" onClick={() => timelineOf(symbol)} className="ml-1 text-[11px] font-normal text-accentSky hover:underline" title="Every scan of this stock: how its buyers / sellers and volume built up">
      timeline
    </button>
  );

  return (
    <section className="mb-4 rounded-xl border border-slate-800 bg-card p-4">
      <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold">
        <CalendarRange size={15} className="text-accentSky" /> Most active across days and scans <NewTag id="ma-history-across" />
      </h3>
      <p className="mb-3 max-w-3xl text-xs text-slate-500">
        The Most active filters run over every saved scan of the days you tick, not just one scan. A stock is listed for a
        day when it passed the filters in at least one scan that day, with the first and last time it did, in how many
        scans, and what its price did from the first match to the day&apos;s last scan. &quot;Every scan&quot; shows how its
        buyers / sellers, volume vs usual and money traded built up, scan by scan. Information only: nothing here places an
        order.
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
        <span className="mr-1 text-slate-400">Days (up to {MAX_DAYS}):</span>
        {days.slice(0, 30).map((d) => {
          const on = picked.includes(d.day);
          return (
            <button
              key={d.day}
              type="button"
              aria-pressed={on}
              onClick={() => toggleDay(d.day)}
              title={`${d.runs} saved scans`}
              className={clsx("min-h-8 rounded-md px-2 ring-1 ring-inset", on ? "bg-accentSky/15 text-accentSky ring-accentSky/50" : "text-slate-400 ring-slate-700 hover:bg-white/5")}
            >
              {dayText(d.day)}
            </button>
          );
        })}
        <button type="button" onClick={() => setPicked(days.slice(0, 5).map((d) => d.day))} className="ml-1 text-slate-400 underline-offset-2 hover:underline">
          last 5
        </button>
        <button type="button" onClick={() => setPicked(days.slice(0, MAX_DAYS).map((d) => d.day))} className="text-slate-400 underline-offset-2 hover:underline">
          last {MAX_DAYS}
        </button>
        <button type="button" onClick={() => setPicked([])} className="text-slate-500 underline-offset-2 hover:underline">
          clear
        </button>
      </div>

      <FilterRow
        status={
          loading
            ? "Reading the saved scans…"
            : `${scans} saved scan${scans === 1 ? "" : "s"} on ${picked.length} day${picked.length === 1 ? "" : "s"} · ${symbols.length} stock${symbols.length === 1 ? "" : "s"} passed at least once (${hits.length} stock-days)`
        }
      >
        <FilterGroup title="Table">
          <FilterField label="Table">
            <select
              value={table}
              onChange={(e) => setTable(e.target.value as Table)}
              aria-label="How to list the stocks"
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
            >
              <option value="day">One row per stock per day</option>
              <option value="stock">One row per stock (all days)</option>
              <option value="scan">Every scan: how it built up</option>
            </select>
            {table === "scan" && (
              <Toggle
                label="Only when it changed"
                checked={changesOnly}
                onChange={setChangesOnly}
                title="Show only the scans in which the stock's bias, buyers / sellers lead or volume vs usual changed since its previous scan that day"
              />
            )}
          </FilterField>
          <TextFilter value={query} onChange={setQuery} />
        </FilterGroup>
        <FilterGroup title="Buyers / sellers">
          <SelectFilter
            label="Who leads"
            value={f.who}
            onChange={(v) => set("who", v as Who)}
            options={[
              { value: "ANY", label: "Either" },
              { value: "BUY", label: "Buyers" },
              { value: "SELL", label: "Sellers" },
            ]}
            className="w-28"
          />
          <NumberFilter
            label="Their share at least (0 = any)"
            value={f.pressure}
            onChange={(v) => set("pressure", Math.min(99, Math.max(0, v)))}
            min={0}
            max={99}
            step={5}
            suffix="%"
            className="w-44"
          />
          <SelectFilter
            label="Bias"
            value={f.bias}
            onChange={(v) => set("bias", v as Filter["bias"])}
            options={[
              { value: "ALL", label: "All" },
              { value: "LONG", label: "Long" },
              { value: "SHORT", label: "Short" },
            ]}
            className="w-24"
          />
        </FilterGroup>
        <FilterGroup title="Activity">
          <NumberFilter label="Vol vs usual at least" value={f.minRvol} onChange={(v) => set("minRvol", Math.max(0, v))} min={0} max={10} step={0.1} suffix="×" className="w-36" />
          <NumberFilter label="Min traded today" value={f.minValue} onChange={(v) => set("minValue", Math.max(0, v))} min={0} max={500} step={5} prefix="₹" suffix="cr" className="w-36" />
          <NumberFilter label="Day move at least, either way" value={f.minMove} onChange={(v) => set("minMove", Math.max(0, v))} min={0} max={10} step={0.25} suffix="%" className="w-44" />
          <NumberFilter label="Speed at least, either way" value={f.minSpeed} onChange={(v) => set("minSpeed", Math.max(0, v))} min={0} max={1} step={0.01} suffix="%/m" className="w-40" />
        </FilterGroup>
        <FilterGroup title="Price">
          <NumberFilter label="Price from (0 = any)" value={f.priceMin} onChange={(v) => set("priceMin", Math.max(0, v))} min={0} max={50000} step={50} prefix="₹ " className="w-36" />
          <NumberFilter label="Price to (0 = any)" value={f.priceMax} onChange={(v) => set("priceMax", Math.max(0, v))} min={0} max={50000} step={50} prefix="₹ " className="w-36" />
        </FilterGroup>
      </FilterRow>

      {error && <p className="mb-2 text-xs text-loss">{error}</p>}

      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        {onArmMany && symbols.length > 0 && (
          <button
            type="button"
            onClick={() => onArmMany(symbols.slice(0, 24))}
            className="min-h-8 rounded-md px-2.5 ring-1 ring-inset ring-slate-700 hover:bg-white/5"
            title="Open the Arm ticked dialog for the stocks listed (at most 24, a bot's Trade list limit)"
          >
            Arm these {Math.min(24, symbols.length)}
          </button>
        )}
        {hits.length > 0 && (
          <button type="button" onClick={csv} className="flex min-h-8 items-center gap-1 rounded-md px-2.5 ring-1 ring-inset ring-slate-700 hover:bg-white/5">
            <Download size={13} /> CSV
          </button>
        )}
        {(f !== NO_FILTER || query) && (
          <button type="button" onClick={() => { setF(NO_FILTER); setQuery(""); }} className="text-slate-500 underline-offset-2 hover:underline">
            clear filters
          </button>
        )}
        {loading && <Loader2 size={13} className="animate-spin text-slate-400" />}
      </div>

      {picked.length === 0 ? (
        <p className="text-xs text-slate-500">Tick one or more days above.</p>
      ) : (table === "scan" ? seen.length === 0 : hits.length === 0) && !loading ? (
        <p className="text-xs text-slate-500">
          {scans === 0 ? "No saved Most active scans on those days." : changesOnly && table === "scan" ? "Nothing changed for the stocks that passed." : "No stock passed these filters in any scan of those days."}
        </p>
      ) : table === "scan" ? (
        <>
          <p className="mb-2 text-xs text-slate-400">
            Every saved scan in which the stock passed the filters, oldest first. Type a stock in Find a stock (or press
            &quot;timeline&quot;) to follow one stock through the day. &quot;What changed&quot; compares with that stock&apos;s
            previous scan that day: green = the buyers / sellers lead got stronger (or, with only the volume changing, it got busier), red = weaker or quieter.
          </p>
          <Pager p={pagedScans} className="mb-2" noun="scans" />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-xs">
              <thead className="text-slate-400">
                <tr>
                  <SortTh label="Day" k="day" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <SortTh label="Time" k="time" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <SortTh label="Stock" k="symbol" sort={scanSort.sort} onSort={scanSort.onSort} text />
                  <SortTh label="Price" k="ltp" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="Day move" k="move" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="Buyers / sellers" k="buyers" sort={scanSort.sort} onSort={scanSort.onSort} num title="Buyers' share of the waiting orders (sellers = 100 − buyers)" />
                  <SortTh label="Vol vs usual" k="rvol" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="Money traded" k="value" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="From avg price" k="vwap" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="Speed" k="speed" sort={scanSort.sort} onSort={scanSort.onSort} num title="% per minute since the previous scan" />
                  <SortTh label="Bias" k="bias" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <SortTh label="What changed" k="change" sort={scanSort.sort} onSort={scanSort.onSort} />
                </tr>
              </thead>
              <tbody>
                {pagedScans.shown.map((r) => {
                  const b = buyersPct(r);
                  return (
                    <tr key={r.key} className="border-t border-slate-800">
                      <td className="px-2 py-1.5 tabular-nums">{dayText(r.day)}</td>
                      <td className="px-2 py-1.5 tabular-nums">{clock(r.at)}</td>
                      <td className="px-2 py-1.5 font-semibold">
                        <button type="button" onClick={() => timelineOf(r.symbol)} className="hover:underline" title="Follow this stock through the day">
                          {r.symbol}
                        </button>
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.ltp?.toFixed(2) ?? "—"}</td>
                      <td className={clsx("px-2 py-1.5 text-right tabular-nums", tone(r.change_pct))}>{pct(r.change_pct)}</td>
                      <td className={clsx("px-2 py-1.5 text-right tabular-nums", b == null ? "" : b >= 50 ? "text-profit" : "text-loss")}>
                        {b == null ? "—" : `${b.toFixed(0)}% / ${(100 - b).toFixed(0)}%`}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.rvol != null ? `${r.rvol.toFixed(1)}×` : "—"}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.value_cr != null ? `₹${r.value_cr.toFixed(1)} cr` : "—"}</td>
                      <td className={clsx("px-2 py-1.5 text-right tabular-nums", tone(r.vwap_dist_pct))}>{pct(r.vwap_dist_pct)}</td>
                      <td className={clsx("px-2 py-1.5 text-right tabular-nums", tone(r.speed_pct_min))}>{r.speed_pct_min != null ? r.speed_pct_min.toFixed(3) : "—"}</td>
                      <td className={clsx("px-2 py-1.5", r.bias === "LONG" ? "text-profit" : r.bias === "SHORT" ? "text-loss" : "text-slate-400")}>{biasText(r.bias)}</td>
                      <td className={clsx("px-2 py-1.5", r.dir > 0 ? "text-profit" : r.dir < 0 ? "text-loss" : "text-slate-400")}>{r.change || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pager p={pagedScans} className="mt-2" noun="scans" />
        </>
      ) : table === "stock" ? (
        <>
          <Pager p={pagedStocks} className="mb-2" />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-xs">
              <thead className="text-slate-400">
                <tr>
                  <SortTh label="Stock" k="symbol" sort={byStock.sort} onSort={byStock.onSort} text />
                  <SortTh label="Days" k="days" sort={byStock.sort} onSort={byStock.onSort} num title="On how many of the picked days it passed at least once" />
                  <SortTh label="Scans passed" k="matched" sort={byStock.sort} onSort={byStock.onSort} num />
                  <SortTh label="First seen" k="first" sort={byStock.sort} onSort={byStock.onSort} />
                  <SortTh label="Avg move after" k="avgMove" sort={byStock.sort} onSort={byStock.onSort} num title="Average of each day's move from the first match to the day's last scan" />
                  <th className="px-2 py-1.5 text-right">Arm</th>
                </tr>
              </thead>
              <tbody>
                {pagedStocks.shown.map((s) => (
                  <tr key={s.key} className="border-t border-slate-800">
                    <td className="px-2 py-1.5 font-semibold">
                      <GrowwLink symbol={s.symbol} /> {timelineLink(s.symbol)}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{s.days}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{s.matched}</td>
                    <td className="px-2 py-1.5 tabular-nums">{dayText(s.firstDay)} {clock(s.firstAt)}</td>
                    <td className={clsx("px-2 py-1.5 text-right tabular-nums", tone(s.avgMove))}>{pct(s.avgMove)}</td>
                    <td className="px-2 py-1.5 text-right">{armCell(s.symbol)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager p={pagedStocks} className="mt-2" />
        </>
      ) : (
        <>
          <Pager p={paged} className="mb-2" />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-xs">
              <thead className="text-slate-400">
                <tr>
                  <SortTh label="Day" k="day" sort={byDay.sort} onSort={byDay.onSort} />
                  <SortTh label="Stock" k="symbol" sort={byDay.sort} onSort={byDay.onSort} text />
                  <SortTh label="First passed" k="first" sort={byDay.sort} onSort={byDay.onSort} title="The first saved scan that day in which it passed every filter" />
                  <SortTh label="Last passed" k="last" sort={byDay.sort} onSort={byDay.onSort} />
                  <SortTh label="Scans passed" k="matched" sort={byDay.sort} onSort={byDay.onSort} num title="In how many of that day's saved scans it passed" />
                  <SortTh label="Price then" k="firstPrice" sort={byDay.sort} onSort={byDay.onSort} num />
                  <SortTh label="Move to day's last scan" k="move" sort={byDay.sort} onSort={byDay.onSort} num title="From the price at the first pass to its price in the day's last saved scan" />
                  <SortTh label="Buyers then" k="buyers" sort={byDay.sort} onSort={byDay.onSort} num />
                  <SortTh label="Vol vs usual then" k="rvol" sort={byDay.sort} onSort={byDay.onSort} num />
                  <SortTh label="Money then" k="value" sort={byDay.sort} onSort={byDay.onSort} num />
                  <th className="px-2 py-1.5 text-right">Arm</th>
                </tr>
              </thead>
              <tbody>
                {paged.shown.map((h) => (
                  <tr key={h.key} className="border-t border-slate-800">
                    <td className="px-2 py-1.5 tabular-nums">{dayText(h.day)}</td>
                    <td className="px-2 py-1.5 font-semibold">
                      <GrowwLink symbol={h.symbol} /> {timelineLink(h.symbol)}
                    </td>
                    <td className="px-2 py-1.5 tabular-nums">{clock(h.firstAt)}</td>
                    <td className="px-2 py-1.5 tabular-nums">{clock(h.lastAt)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {h.matched} of {h.scans}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{h.firstPrice?.toFixed(2) ?? "—"}</td>
                    <td className={clsx("px-2 py-1.5 text-right tabular-nums", tone(h.movePct))}>{pct(h.movePct)}</td>
                    <td className={clsx("px-2 py-1.5 text-right tabular-nums", h.buyersFirst == null ? "" : h.buyersFirst >= 50 ? "text-profit" : "text-loss")}>
                      {h.buyersFirst == null ? "—" : `${h.buyersFirst.toFixed(0)}%`}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{h.rvolFirst != null ? `${h.rvolFirst.toFixed(1)}×` : "—"}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{h.valueFirst != null ? `₹${h.valueFirst.toFixed(1)} cr` : "—"}</td>
                    <td className="px-2 py-1.5 text-right">{armCell(h.symbol)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager p={paged} className="mt-2" />
        </>
      )}
    </section>
  );
}
