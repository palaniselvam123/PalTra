"use client";

import { ScanListPicker, cleanLists, resolveScanSymbols, type ScanList } from "@/components/Scalp/ScanLists";
import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { lastScanText } from "@/lib/lastScan";
import { ArrowRightLeft, Loader2, RefreshCw } from "lucide-react";
import { smaApi, type CarryRow, type CarryScan as CarryData, type CarryStatus } from "@/lib/smaApi";
import { FilterField, FilterRow, NumberFilter, SelectFilter, TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { ColumnHint, inOrder, useColumnOrder } from "@/components/ui/columnOrder";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { GrowwLink } from "@/components/ui/growwLink";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { DeliveryCells, DeliveryHead, DeliveryNote, useDelivery } from "@/components/Scalp/delivery";
import type { DeliveryRow } from "@/lib/api";

const POLL_MS = 3_000;
const STORE = "sma.carry.filters";
const SIZES = [1, 3, 5, 10, 15];

type Props = {
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
  paused?: boolean;
  onStatus?: OnScanStatus;
};

type Show = "ALL" | CarryStatus;
type Side = "ALL" | "LONG" | "SHORT";
type GapShow = "ALL" | "WITH" | "AGAINST";
type SortKey = "symbol" | "prev" | "status" | "gap" | "dev" | "devRs" | "move" | "best" | "worst" | "last";
type Col = "symbol" | "prev" | "prevSma" | "status" | "gap" | "last" | "dev" | "move" | "best" | "worst" | "arm";
const COLUMNS: readonly Col[] = ["symbol", "prev", "prevSma", "status", "gap", "last", "dev", "move", "best", "worst", "arm"];

const pct = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`);
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "text-slate-300" : v > 0 ? "text-profit" : "text-loss");
const STATUS_TEXT: Record<CarryStatus, string> = { CONTINUED: "Continued", REVERSED: "Reversed", BACK: "Back with it" };

/**
 * Carry-over: which way each stock's SMA fast / slow pointed when yesterday closed, and whether that trend carried on
 * today: the gap at the open, continued / reversed / came back, the price's deviation from yesterday's closing SMA in ₹
 * and %, and the best / worst move from yesterday's close. Candles only; Arm only puts a stock on a bot's Trade list.
 */
export function CarryScan({ armed, arming, onArm, onArmMany, paused = false, onStatus }: Props) {
  const cols = useColumnOrder<Col>("scalp.cols.carry", COLUMNS);
  const [data, setData] = useState<CarryData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [minutes, setMinutes] = useState(5);
  const [show, setShow] = useState<Show>("ALL");
  const [side, setSide] = useState<Side>("ALL");
  const [gapShow, setGapShow] = useState<GapShow>("ALL");
  const [minDev, setMinDev] = useState(0);
  const [query, setQuery] = useState("");
  const [lists, setLists] = useState<ScanList[]>(["fno"]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) || "null");
      if (saved?.show) setShow(saved.show);
      if (saved?.side) setSide(saved.side);
      if (saved?.gapShow) setGapShow(saved.gapShow);
      if (SIZES.includes(saved?.minutes)) setMinutes(saved.minutes);
      if (typeof saved?.minDev === "number") setMinDev(saved.minDev);
      setLists(cleanLists(saved?.lists, ["fno"]));
    } catch {
      /* private mode or an old value */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ show, side, gapShow, minutes, minDev, lists }));
    } catch {
      /* private mode */
    }
  }, [show, side, gapShow, minutes, minDev, lists]);

  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const symbols = await resolveScanSymbols(lists, armed.keys());
      setData(await smaApi.carryScanStart(symbols, minutes, true));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not start the scan");
    } finally {
      setBusy(false);
    }
  }, [armed, lists, minutes]);

  // Opening the tab only shows the last result the server holds; nothing is scanned until Scan now.
  useEffect(() => {
    let live = true;
    smaApi
      .carryScan()
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
        .carryScan()
        .then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Lost the scan"));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [data?.running]);
  useEffect(() => {
    onStatus?.({ when: data?.finished_at ?? data?.as_of ?? null, running: !!data?.running });
  }, [onStatus, data?.finished_at, data?.as_of, data?.running]);

  const all = useMemo(() => data?.rows ?? [], [data?.rows]);
  const counts = useMemo(
    () => ({
      CONTINUED: all.filter((r) => r.status === "CONTINUED").length,
      REVERSED: all.filter((r) => r.status === "REVERSED").length,
      BACK: all.filter((r) => r.status === "BACK").length,
    }),
    [all]
  );
  const ticks = useTicks();
  const filtered = useMemo(
    () =>
      all.filter((r) => {
        if (!matchesText(query, r.symbol) || !ticks.shows(r.symbol)) return false;
        if (show !== "ALL" && r.status !== show) return false;
        if (side !== "ALL" && r.prev_side !== side) return false;
        if (gapShow === "WITH" && r.gap_with !== true) return false;
        if (gapShow === "AGAINST" && r.gap_with !== false) return false;
        if (minDev > 0 && Math.abs(r.dev_pct) < minDev) return false;
        return true;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [all, query, show, side, gapShow, minDev, ticks.only, ticks.ticked]
  );
  const { sorted: rows, sort, onSort } = useSort<CarryRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "prev":
        return r.prev_side;
      case "status":
        return r.status;
      case "gap":
        return r.gap_pct;
      case "dev":
        return r.dev_pct;
      case "devRs":
        return r.dev_rs;
      case "move":
        return r.move_with_pct;
      case "best":
        return r.best_pct;
      case "worst":
        return r.worst_pct;
      case "last":
        return r.last;
    }
  });
  const paged = usePaged(rows, "scalp.carry.pageSize");
  const deliv = useDelivery(rows.map((r) => r.symbol));
  const sma = data ? `SMA ${data.sma_fast}/${data.sma_slow}` : "SMA 9/21";
  const day = all[0];
  const continuedPct = all.length ? Math.round((counts.CONTINUED / all.length) * 100) : null;

  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <ArrowRightLeft size={15} className="text-accentSky" /> Carry-over — did yesterday&apos;s SMA trend carry on today?
        </h2>
        <label className="ml-auto inline-flex items-center gap-1.5 text-xs text-slate-400">
          Candle
          <select
            value={minutes}
            disabled={busy || data?.running}
            onChange={(e) => setMinutes(Number(e.target.value))}
            className="min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
          >
            {SIZES.map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => void start()}
          disabled={busy || data?.running || paused}
          title={paused ? "Scans are paused at the top of the page" : undefined}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
        >
          {busy || data?.running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
        </button>
      </div>
      <Explain
        className="mb-3"
        lead={`Which way each stock's ${sma} pointed when yesterday closed, and whether that trend carried on today. Candles only — nothing here places an order.`}
      >
        Yesterday: the {sma} on the {data?.minutes ?? minutes}-minute candle at yesterday&apos;s last candle: up (fast above slow) or
        down, since when, and the SMA values there (the last day&apos;s moving average). Today: the gap at the open from yesterday&apos;s
        close (✓ when it went the trend&apos;s way); Continued = no SMA cross against yesterday&apos;s side on today&apos;s closed
        candles, Reversed = crossed against it (time shown) and still against, Back with it = crossed against it and came back.
        Deviation = the last price against yesterday&apos;s closing SMA {data?.sma_fast ?? 9}, in ₹ and % (+ above, − below).
        &quot;Moved its way&quot; is the change since yesterday&apos;s close, plus when it went the trend&apos;s way; Best / Worst are the
        furthest today went with and against the trend, from yesterday&apos;s close. After hours it reads the last two sessions. Arm
        puts a stock on a bot&apos;s Trade list; it orders only on that bot&apos;s own signal.
      </Explain>

      <FilterRow
        status={
          data
            ? data.running
              ? `Scanning ${data.done} of ${data.total}…`
              : [
                  lastScanText(data, data.minutes),
                  data.as_of && all.length
                    ? `${day ? `${day.prev_day} → ${day.day}: ` : ""}${counts.CONTINUED} continued (${continuedPct}%), ${counts.BACK} back, ${counts.REVERSED} reversed`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ") || "No scan yet"
            : null
        }
      >
        <SelectFilter
          label="Show"
          value={show}
          onChange={(v) => setShow(v as Show)}
          options={[
            { value: "ALL", label: `All (${all.length})` },
            { value: "CONTINUED", label: `Continued (${counts.CONTINUED})` },
            { value: "BACK", label: `Back with it (${counts.BACK})` },
            { value: "REVERSED", label: `Reversed (${counts.REVERSED})` },
          ]}
          className="w-44"
        />
        <SelectFilter
          label="Yesterday"
          value={side}
          onChange={(v) => setSide(v as Side)}
          options={[
            { value: "ALL", label: "Up and down" },
            { value: "LONG", label: "Up ↑" },
            { value: "SHORT", label: "Down ↓" },
          ]}
          className="w-32"
        />
        <SelectFilter
          label="Gap at open"
          value={gapShow}
          onChange={(v) => setGapShow(v as GapShow)}
          options={[
            { value: "ALL", label: "Any" },
            { value: "WITH", label: "With the trend" },
            { value: "AGAINST", label: "Against it" },
          ]}
          className="w-36"
        />
        <NumberFilter label="Deviation at least" value={minDev} onChange={setMinDev} suffix="%" step={0.1} min={0} max={10} className="w-36" />
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
          {data?.running ? "Scanning…" : data?.as_of ? "No stock matches these filters." : "Nothing is scanned until you press Scan now."}
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
                  symbol: <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text drag={cols.dragProps("symbol")} />,
                  prev: <SortTh label="Yesterday" k="prev" sort={sort} onSort={onSort} text title="The SMA side at yesterday's close, and since when" drag={cols.dragProps("prev")} />,
                  prevSma: (
                    <th {...cols.dragProps("prevSma")} title="Yesterday's close and its closing SMA fast / slow (the last day's moving average)" className={clsx("pb-2 text-right font-medium", cols.dragProps("prevSma").className)}>
                      Close · SMA
                    </th>
                  ),
                  status: <SortTh label="Today" k="status" sort={sort} onSort={onSort} text title="Continued / reversed (when) / back with it" drag={cols.dragProps("status")} />,
                  gap: <SortTh label="Gap at open" k="gap" sort={sort} onSort={onSort} num title="Today's open vs yesterday's close (✓ = the trend's way)" drag={cols.dragProps("gap")} />,
                  last: <SortTh label="Last" k="last" sort={sort} onSort={onSort} num title="Last price and its time" drag={cols.dragProps("last")} />,
                  dev: <SortTh label="Deviation" k="dev" sort={sort} onSort={onSort} num title="Last price vs yesterday's closing SMA fast: ₹ and % (+ above, − below); the slow SMA in the tooltip" drag={cols.dragProps("dev")} />,
                  move: <SortTh label="Moved its way" k="move" sort={sort} onSort={onSort} num title="Change since yesterday's close, + when it went the trend's way" drag={cols.dragProps("move")} />,
                  best: <SortTh label="Best" k="best" sort={sort} onSort={onSort} num title="Furthest today went with the trend, from yesterday's close" drag={cols.dragProps("best")} />,
                  worst: <SortTh label="Worst" k="worst" sort={sort} onSort={onSort} num title="Furthest today went against the trend, from yesterday's close" drag={cols.dragProps("worst")} />,
                  arm: (
                    <th {...cols.dragProps("arm")} className={clsx("px-1 pb-2 text-right font-medium", cols.dragProps("arm").className)}>
                      Arm
                    </th>
                  ),
                })}
                <DeliveryHead />
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
                  deliv={deliv.rows.get(r.symbol.toUpperCase())}
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

function SideChip({ side }: { side: "LONG" | "SHORT" | null }) {
  if (!side) return <span className="text-slate-600">—</span>;
  const up = side === "LONG";
  return (
    <span
      className={clsx(
        "rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        up ? "bg-accentSky/10 text-accentSky ring-accentSky/40" : "bg-accentViolet/10 text-accentViolet ring-accentViolet/40"
      )}
    >
      {up ? "Up ↑" : "Down ↓"}
    </span>
  );
}

function Row({
  order,
  row: r,
  tick,
  armedOn,
  arming,
  onArm,
  deliv,
}: {
  deliv?: DeliveryRow;
  order: readonly Col[];
  row: CarryRow;
  tick: React.ReactNode;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  const statusTone =
    r.status === "CONTINUED"
      ? "bg-profit/15 text-profit ring-profit/40"
      : r.status === "BACK"
        ? "bg-amber-500/10 text-amber-300 ring-amber-400/40"
        : "bg-loss/10 text-loss ring-loss/40";
  return (
    <tr className={clsx("whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2", r.status === "CONTINUED" && "bg-profit/[0.04]")}>
      {tick}
      {inOrder(order, {
        symbol: (
          <td className="py-1.5 font-semibold text-slate-100">
            <GrowwLink symbol={r.symbol} />
          </td>
        ),
        prev: (
          <td className="py-1.5" title={`${r.prev_day}: SMA gap ${pct(r.prev_gap_pct, 3)}`}>
            <SideChip side={r.prev_side} />
            {r.prev_since ? <span className="ml-1.5 text-[11px] text-slate-500">since {r.prev_since}</span> : null}
          </td>
        ),
        prevSma: (
          <td className="py-1.5 text-right tabular-nums text-slate-300">
            {r.prev_close.toFixed(2)}
            <span className="ml-1 text-[11px] text-slate-500">
              · {r.prev_sma_fast.toFixed(2)} / {r.prev_sma_slow.toFixed(2)}
            </span>
          </td>
        ),
        status: (
          <td className="py-1.5">
            <span className={clsx("rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset", statusTone)}>{STATUS_TEXT[r.status]}</span>
            {r.reversed_at ? <span className="ml-1.5 text-[11px] text-slate-400">crossed {r.reversed_at}</span> : null}
            {r.back_at ? <span className="ml-1 text-[11px] text-slate-400">· back {r.back_at}</span> : null}
          </td>
        ),
        gap: (
          <td className={clsx("py-1.5 text-right tabular-nums", tone(r.gap_pct))} title={r.open != null ? `Opened ${r.open.toFixed(2)}` : undefined}>
            {pct(r.gap_pct)}
            {r.gap_with != null ? (
              <span className={clsx("ml-1 text-xs", r.gap_with ? "text-profit" : "text-loss")}>{r.gap_with ? "✓" : "✗"}</span>
            ) : null}
          </td>
        ),
        last: (
          <td className="py-1.5 text-right tabular-nums">
            {r.last.toFixed(2)}
            <span className="ml-1 text-[11px] text-slate-500">{r.last_at}</span>
          </td>
        ),
        dev: (
          <td className={clsx("py-1.5 text-right tabular-nums", tone(r.dev_pct))} title={`vs SMA ${r.prev_sma_fast.toFixed(2)}; vs slow SMA ${pct(r.dev_slow_pct)}`}>
            {r.dev_rs > 0 ? "+" : r.dev_rs < 0 ? "−" : ""}₹{Math.abs(r.dev_rs).toFixed(2)}
            <span className="ml-1 text-[11px]">({pct(r.dev_pct)})</span>
          </td>
        ),
        move: <td className={clsx("py-1.5 text-right tabular-nums font-semibold", tone(r.move_with_pct))}>{pct(r.move_with_pct)}</td>,
        best: (
          <td className={clsx("py-1.5 text-right tabular-nums", tone(r.best_pct))}>
            {pct(r.best_pct)}
            {r.best_at ? <span className="ml-1 text-[11px] text-slate-500">{r.best_at}</span> : null}
          </td>
        ),
        worst: (
          <td className={clsx("py-1.5 text-right tabular-nums", tone(r.worst_pct))}>
            {pct(r.worst_pct)}
            {r.worst_at ? <span className="ml-1 text-[11px] text-slate-500">{r.worst_at}</span> : null}
          </td>
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
      <DeliveryCells d={deliv} />
    </tr>
  );
}
