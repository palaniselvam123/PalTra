"use client";

import { ScanListPicker, cleanLists, resolveScanSymbols, type ScanList } from "@/components/Scalp/ScanLists";
import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { lastScanText } from "@/lib/lastScan";
import { Loader2, Mountain, RefreshCw } from "lucide-react";
import { smaApi, type High52Scan as High52ScanData, type High52ScanRow } from "@/lib/smaApi";
import { FilterField, FilterRow, SelectFilter, TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { ColumnHint, inOrder, useColumnOrder } from "@/components/ui/columnOrder";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { GrowwLink } from "@/components/ui/growwLink";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";

const POLL_MS = 3_000;
const STORE = "sma.high52.filters";

type Props = {
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
  paused?: boolean;
  onStatus?: OnScanStatus;
};

/** Which stocks to keep: at the high today, within the band of the high, within the band of the low, everything. */
type Show = "BROKE" | "NEAR_HIGH" | "NEAR_LOW" | "ALL";
type SortKey = "symbol" | "price" | "high" | "distHigh" | "low" | "distLow" | "sinceHigh" | "dayMove" | "yearMove";
type Col = "symbol" | "price" | "high" | "distHigh" | "low" | "distLow" | "sinceHigh" | "dayMove" | "yearMove" | "arm";
const COLUMNS: readonly Col[] = ["symbol", "price", "high", "distHigh", "low", "distLow", "sinceHigh", "dayMove", "yearMove", "arm"];
const BANDS = ["1", "2", "3", "5", "10"] as const;

const num = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));
const signed = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
const shortDate = (iso: string | null | undefined) =>
  iso ? new Date(`${iso}T00:00:00+05:30`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit", timeZone: "Asia/Kolkata" }) : "—";

/**
 * The 52-week high scan: how far each stock is from its 52-week high (and low), from
 * Groww's daily candles, breakouts first. Display only: it places no order, and Arm only
 * puts a stock on a bot's Trade list (the bot's own "52-week high" entry check then buys
 * the breakout on its signal).
 */
export function High52Scan({ armed, arming, onArm, onArmMany, paused = false, onStatus }: Props) {
  const cols = useColumnOrder<Col>("scalp.cols.high52", COLUMNS);
  const [data, setData] = useState<High52ScanData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState<Show>("NEAR_HIGH");
  const [band, setBand] = useState<string>("3");
  const [query, setQuery] = useState("");
  const [lists, setLists] = useState<ScanList[]>(["fno"]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) || "null");
      if (saved?.show) setShow(saved.show);
      if (saved?.band && BANDS.includes(saved.band)) setBand(saved.band);
      setLists(cleanLists(saved?.lists, ["fno"]));
    } catch {
      /* private mode or an old value */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ show, band, lists }));
    } catch {
      /* private mode */
    }
  }, [show, band, lists]);

  const start = useCallback(
    async (force = false) => {
      setBusy(true);
      setError(null);
      try {
        const symbols = await resolveScanSymbols(lists, armed.keys());
        setData(await smaApi.high52ScanStart(symbols, force));
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Could not start the scan");
      } finally {
        setBusy(false);
      }
    },
    [armed, lists]
  );

  // Opening the tab only shows the last result the server holds; nothing is scanned until Scan now.
  useEffect(() => {
    let live = true;
    smaApi
      .high52Scan()
      .then((d) => live && setData(d))
      .catch(() => {
        /* the terminal is not answering: "No scan yet", and Scan now still works */
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (!data?.running) return;
    const id = setInterval(() => {
      smaApi
        .high52Scan()
        .then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Lost the scan"));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [data?.running]);

  useEffect(() => {
    onStatus?.({ when: data?.finished_at ?? data?.as_of ?? null, running: !!data?.running });
  }, [onStatus, data?.finished_at, data?.as_of, data?.running]);

  const all = useMemo(() => data?.rows ?? [], [data?.rows]);
  const bandPct = Number(band);
  const counts = useMemo(
    () => ({
      broke: all.filter((r) => r.broke_high).length,
      near: all.filter((r) => r.dist_high_pct != null && r.dist_high_pct <= bandPct).length,
    }),
    [all, bandPct]
  );
  const ticks = useTicks();
  const filtered = useMemo(
    () =>
      all.filter((r) => {
        if (!matchesText(query, r.symbol) || !ticks.shows(r.symbol)) return false;
        if (show === "BROKE") return r.broke_high;
        if (show === "NEAR_HIGH") return r.dist_high_pct != null && r.dist_high_pct <= bandPct;
        if (show === "NEAR_LOW") return r.dist_low_pct != null && r.dist_low_pct <= bandPct;
        return true;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [all, query, show, bandPct, ticks.only, ticks.ticked]
  );
  const { sorted: rows, sort, onSort } = useSort<High52ScanRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "price":
        return r.ltp;
      case "high":
        return r.high52;
      case "distHigh":
        return r.dist_high_pct;
      case "low":
        return r.low52;
      case "distLow":
        return r.dist_low_pct;
      case "sinceHigh":
        return r.days_since_high;
      case "dayMove":
        return r.day_move_pct;
      case "yearMove":
        return r.year_move_pct;
    }
  });
  const paged = usePaged(rows, "scalp.high52.pageSize");

  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Mountain size={15} className="text-accentSky" /> 52-week high — stocks at or near their yearly high
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
        lead="Each stock's highest daily high and lowest daily low over the 52 weeks before today, from Groww's daily candles, and how far today's price is from them. A stock whose high today reached the old 52-week high is a breakout. Candles only — nothing here places an order."
      >
        &quot;From high&quot; is how far under the 52-week high the last price is (negative = above it, a new high today);
        &quot;From low&quot; the same for the 52-week low. &quot;Since high&quot; counts the sessions since the high was set, so a
        fresh high and a high from months ago read differently. Day % is today against the previous close, Year % against the
        close a year ago. Arm puts a stock on a bot&apos;s Trade list; with the bot&apos;s &quot;Enter only at the 52-week high&quot;
        check on (Settings, 2. Checks before entering) it buys only a close at that high, on its own signal. One daily-candle
        download per stock, so a full F&amp;O scan takes a couple of minutes.
      </Explain>

      <FilterRow
        status={
          data
            ? data.running
              ? `Scanning ${data.done} of ${data.total}…`
              : [lastScanText({ ...data, minutes: 1440 }, 1440), data.as_of ? `${counts.broke} at a new high · ${counts.near} within ${band}% of the high` : null].filter(Boolean).join(" · ") || "No scan yet"
            : null
        }
      >
        <SelectFilter
          label="Show"
          value={show}
          onChange={(v) => setShow(v as Show)}
          options={[
            { value: "BROKE", label: "New 52-week high today" },
            { value: "NEAR_HIGH", label: `Within the band of the high` },
            { value: "NEAR_LOW", label: `Within the band of the low` },
            { value: "ALL", label: "Every stock read" },
          ]}
          className="w-52"
        />
        <SelectFilter label="Band" value={band} onChange={setBand} options={BANDS.map((b) => ({ value: b, label: `${b}%` }))} className="w-24" />
        <TextFilter value={query} onChange={setQuery} />
        <FilterField label="Stocks">
          <ScanListPicker value={lists} onChange={setLists} />
        </FilterField>
      </FilterRow>

      {error && <p className="mb-2 text-xs text-loss">{error}</p>}
      {data?.error && !error && (
        <p className="mb-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{data.error}</p>
      )}
      {rows.length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-500">
          {data?.running
            ? "Scanning…"
            : data?.as_of
              ? show === "BROKE"
                ? "No stock made a new 52-week high today. Show “Within the band of the high” for the ones close to it."
                : "No stock matches these filters."
              : "Nothing is scanned until you press Scan now."}
        </p>
      ) : (
        <div className="overflow-x-auto" ref={paged.anchor}>
          {onArmMany ? <TickBar ticks={ticks} onArm={onArmMany} /> : null}
          <Pager p={paged} className="mb-2" />
          <ColumnHint moved={cols.moved} onReset={cols.reset} />
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
              <tr>
                {onArmMany ? <TickHead ticks={ticks} symbols={rows.map((r) => r.symbol)} /> : null}
                {inOrder(cols.order, {
                  symbol: <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text drag={cols.dragProps("symbol")} />,
                  price: <SortTh label="Price" k="price" sort={sort} onSort={onSort} num title="Today's last price (the running daily candle)" drag={cols.dragProps("price")} />,
                  high: <SortTh label="52w high" k="high" sort={sort} onSort={onSort} num title="Highest daily high of the 52 weeks before today, and its date" drag={cols.dragProps("high")} />,
                  distHigh: <SortTh label="From high" k="distHigh" sort={sort} onSort={onSort} num title="How far under the 52-week high (negative = above it today)" drag={cols.dragProps("distHigh")} />,
                  low: <SortTh label="52w low" k="low" sort={sort} onSort={onSort} num title="Lowest daily low of the 52 weeks before today, and its date" drag={cols.dragProps("low")} />,
                  distLow: <SortTh label="From low" k="distLow" sort={sort} onSort={onSort} num title="How far above the 52-week low" drag={cols.dragProps("distLow")} />,
                  sinceHigh: <SortTh label="Since high" k="sinceHigh" sort={sort} onSort={onSort} num title="Sessions since the 52-week high was set" drag={cols.dragProps("sinceHigh")} />,
                  dayMove: <SortTh label="Day %" k="dayMove" sort={sort} onSort={onSort} num title="Today against the previous close" drag={cols.dragProps("dayMove")} />,
                  yearMove: <SortTh label="Year %" k="yearMove" sort={sort} onSort={onSort} num title="Today against the close 52 weeks ago" drag={cols.dragProps("yearMove")} />,
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
                <Row
                  key={r.symbol}
                  order={cols.order}
                  row={r}
                  tick={onArmMany ? <TickCell ticks={ticks} symbol={r.symbol} /> : null}
                  armedOn={armed.get(r.symbol.toUpperCase()) ?? []}
                  arming={arming === r.symbol}
                  onArm={() => onArm(r.symbol)}
                />
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
  tick,
  armedOn,
  arming,
  onArm,
}: {
  order: readonly Col[];
  row: High52ScanRow;
  tick: React.ReactNode;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  const tone = (v: number | null | undefined) => ((v ?? 0) > 0 ? "text-profit" : (v ?? 0) < 0 ? "text-loss" : "text-slate-400");
  return (
    <tr className={clsx("whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2", r.broke_high && "bg-profit/[0.04]")}>
      {tick}
      {inOrder(order, {
        symbol: (
          <td className="py-1.5 font-semibold text-slate-100">
            <GrowwLink symbol={r.symbol} />
            {r.broke_high ? (
              <span className="ml-1.5 rounded-md bg-profit/15 px-1.5 py-0.5 text-[10px] font-semibold text-profit ring-1 ring-inset ring-profit/40" title="Today's high reached the 52-week high">
                new high
              </span>
            ) : r.broke_low ? (
              <span className="ml-1.5 rounded-md bg-loss/15 px-1.5 py-0.5 text-[10px] font-semibold text-loss ring-1 ring-inset ring-loss/40" title="Today's low reached the 52-week low">
                new low
              </span>
            ) : null}
          </td>
        ),
        price: <td className="py-1.5 text-right tabular-nums">{num(r.ltp)}</td>,
        high: (
          <td className="py-1.5 text-right tabular-nums">
            {num(r.high52)} <span className="text-[11px] text-slate-500">{shortDate(r.high52_date)}</span>
          </td>
        ),
        distHigh: (
          <td className={clsx("py-1.5 text-right tabular-nums font-semibold", (r.dist_high_pct ?? 1) <= 0 ? "text-profit" : (r.dist_high_pct ?? 99) <= 3 ? "text-accentSky" : "text-slate-300")}>
            {r.dist_high_pct == null ? "—" : `${r.dist_high_pct <= 0 ? "+" : "−"}${Math.abs(r.dist_high_pct).toFixed(2)}%`}
          </td>
        ),
        low: (
          <td className="py-1.5 text-right tabular-nums text-slate-400">
            {num(r.low52)} <span className="text-[11px] text-slate-500">{shortDate(r.low52_date)}</span>
          </td>
        ),
        distLow: <td className="py-1.5 text-right tabular-nums text-slate-400">{r.dist_low_pct == null ? "—" : `${r.dist_low_pct.toFixed(2)}%`}</td>,
        sinceHigh: <td className="py-1.5 text-right tabular-nums text-slate-400">{r.days_since_high == null ? "—" : `${r.days_since_high} d`}</td>,
        dayMove: <td className={clsx("py-1.5 text-right tabular-nums", tone(r.day_move_pct))}>{signed(r.day_move_pct)}</td>,
        yearMove: <td className={clsx("py-1.5 text-right tabular-nums", tone(r.year_move_pct))}>{signed(r.year_move_pct)}</td>,
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
