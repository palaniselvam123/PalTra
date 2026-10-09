"use client";

import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { ArrowDown, ArrowUp, Eye, Flame, Loader2, RefreshCw } from "lucide-react";
import { api, type ActiveSort, type ActiveStock, type ActiveStocksResponse } from "@/lib/api";
import { NumberFilter, TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";

const POLL_MS = 30_000;
const SORT_LABEL: Record<ActiveSort, string> = {
  value: "Money traded",
  rvol: "Volume vs usual",
  change: "Biggest move",
  pressure: "Buyer/seller imbalance",
};
type ColKey = "symbol" | "ltp" | "change" | "value" | "rvol" | "buy" | "vwap" | "bias";

type Props = {
  /** Stocks already on the desk feed (shown in the monitor below). */
  watching: Set<string>;
  /** Stock → the SMA desks it is armed on. */
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  /** Called after a stock is added to the desk feed, so the monitor reloads. */
  onWatched: () => void;
};

const pct = (v: number | null, digits = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);

/** The F&O stocks ranked by where traders are: money traded, volume against
 * usual, and buy vs sell quantity waiting. From Groww quotes; no orders. */
export function MostActive({ watching, armed, arming, onArm, onWatched }: Props) {
  const [data, setData] = useState<ActiveStocksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<ActiveSort>("value");
  const [bias, setBias] = useState<"ALL" | "LONG" | "SHORT">("ALL");
  const [minValue, setMinValue] = useState(0);
  const [query, setQuery] = useState("");
  const [scanning, setScanning] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.scalpActive({
        sort,
        top: 40,
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

  const found = useMemo(() => (data?.rows ?? []).filter((r) => matchesText(query, r.symbol)), [data?.rows, query]);
  const { sorted: rows, sort: colSort, onSort } = useSort<ActiveStock, ColKey>(found, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "ltp":
        return r.ltp;
      case "change":
        return r.change_pct;
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
  const asOf = data?.as_of
    ? new Date(data.as_of).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) + " IST"
    : null;

  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Flame size={15} className="text-amber-400" /> Where traders are now — most active F&amp;O stocks
        </h2>
        <button
          type="button"
          onClick={scanNow}
          disabled={scanning || data?.running || data?.connected === false}
          className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
        >
          {scanning || data?.running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
        </button>
      </div>
      <Explain className="mb-3" lead="The most traded F&O stocks, ranked every 5 minutes. Market data only — nothing here places an order.">
        Every 5 minutes while the market is open, one Groww quote for each NSE stock that has futures. Ranked by money
        traded today, volume against its 20-day usual for this time of day, and how many shares buyers vs sellers have
        waiting. Watch adds a stock to the desk feed so the monitor below scores it for scalping.
      </Explain>

      <div className="mb-3 flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Rank by
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as ActiveSort)}
            className="rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
          >
            {(Object.keys(SORT_LABEL) as ActiveSort[]).map((k) => (
              <option key={k} value={k}>
                {SORT_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Bias
          <select
            value={bias}
            onChange={(e) => setBias(e.target.value as "ALL" | "LONG" | "SHORT")}
            className="rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
          >
            <option value="ALL">All</option>
            <option value="LONG">Long (up, above avg price)</option>
            <option value="SHORT">Short (down, below avg price)</option>
          </select>
        </label>
        <NumberFilter label="Min traded today (₹ crore)" value={minValue} onChange={setMinValue} min={0} max={500} step={5} prefix="₹" suffix="cr" />
        <TextFilter value={query} onChange={setQuery} />
        {data && (
          <span className="ml-auto">
            {asOf ? `Scanned ${asOf} · ${data.scanned} of ${data.universe} stocks` : "No scan yet"}
            {data.failed ? ` · ${data.failed} failed` : ""}
          </span>
        )}
      </div>

      {note && <p className="mb-2 text-xs text-accentSky">{note}</p>}
      {error && <p className="mb-2 text-xs text-loss">{error}</p>}
      {data?.connected === false ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          Groww is not connected. Go to Settings and click Connect Live Data, then Scan now.
        </p>
      ) : data?.error && rows.length === 0 ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{data.error}</p>
      ) : rows.length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-500">
          {data?.running
            ? "Scanning…"
            : data?.market_open
              ? "Nothing yet. Click Scan now, or wait for the next 5-minute scan."
              : "Scans run while the market is open (09:15–15:30). Click Scan now to read the last prices."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
              <tr>
                <th className="px-1 pb-2 text-left font-medium">#</th>
                <SortTh label="Stock" k="symbol" sort={colSort} onSort={onSort} text />
                <SortTh label="LTP" k="ltp" sort={colSort} onSort={onSort} num />
                <SortTh label="Day" k="change" sort={colSort} onSort={onSort} num />
                <SortTh label="₹ cr" k="value" sort={colSort} onSort={onSort} num title="Volume × average price today" />
                <SortTh label="Vol vs usual" k="rvol" sort={colSort} onSort={onSort} num title="Volume so far against the 20-day average scaled to the time of day" />
                <SortTh label="Buyers vs sellers" k="buy" sort={colSort} onSort={onSort} title="Total buy vs sell quantity waiting in the order book" />
                <SortTh label="vs avg" k="vwap" sort={colSort} onSort={onSort} num title="Price against Groww's average traded price today (VWAP)" />
                <SortTh label="Bias" k="bias" sort={colSort} onSort={onSort} text />
                <th className="px-1 pb-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <ActiveRowView
                  key={r.symbol}
                  rank={i + 1}
                  row={r}
                  watching={watching.has(r.symbol)}
                  adding={adding === r.symbol}
                  onWatch={() => watch(r.symbol)}
                  armedOn={armed.get(r.symbol.toUpperCase()) ?? []}
                  arming={arming === r.symbol}
                  onArm={() => onArm(r.symbol)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ActiveRowView({
  rank,
  row,
  watching,
  adding,
  onWatch,
  armedOn,
  arming,
  onArm,
}: {
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
      <td className="py-1.5 pr-2 text-xs text-slate-500">{rank}</td>
      <td className="py-1.5 pr-2 font-semibold text-slate-100">{row.symbol}</td>
      <td className="py-1.5 pr-2 text-right font-mono">{row.ltp.toFixed(2)}</td>
      <td className={clsx("py-1.5 pr-2 text-right font-mono", (row.change_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>
        {pct(row.change_pct)}
      </td>
      <td className="py-1.5 pr-2 text-right font-mono">{row.value_cr.toFixed(0)}</td>
      <td
        className={clsx(
          "py-1.5 pr-2 text-right font-mono",
          row.rvol != null && row.rvol >= 2 ? "font-semibold text-amber-300" : "text-slate-300"
        )}
      >
        {row.rvol == null ? "—" : `${row.rvol.toFixed(1)}×`}
      </td>
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
      <td className={clsx("py-1.5 pr-2 text-right font-mono", (row.vwap_dist_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>
        {pct(row.vwap_dist_pct)}
      </td>
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
    </tr>
  );
}
