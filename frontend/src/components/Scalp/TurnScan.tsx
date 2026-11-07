"use client";

import { StopScan } from "@/components/Scalp/StopScan";
import { ScanListPicker, cleanLists, resolveScanSymbols, type ScanList } from "@/components/Scalp/ScanLists";
import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { lastScanText } from "@/lib/lastScan";
import { Loader2, RefreshCw, Undo2 } from "lucide-react";
import { smaApi, type TurnRow, type TurnRule, type TurnScan as TurnData } from "@/lib/smaApi";
import { FilterField, FilterRow, NumberFilter, SelectFilter, TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { ColumnHint, inOrder, useColumnOrder } from "@/components/ui/columnOrder";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { GrowwLink } from "@/components/ui/growwLink";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { GuideBar } from "./GuideBar";

const POLL_MS = 3_000;
const STORE = "sma.turn.filters";
const SIZES = [1, 3, 5, 10, 15];
const RULE_TEXT: Record<TurnRule, string> = {
  COLOUR: "Candle colour (green / red)",
  CLOSES: "Each close beyond the one before",
  BOTH: "Both",
};

type Props = {
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
  paused?: boolean;
  onStatus?: OnScanStatus;
};

type Way = "ALL" | "LONG" | "SHORT";
type SortKey = "symbol" | "trend" | "counter" | "gap" | "closed" | "cross" | "moved" | "day" | "speed";
type Col = "symbol" | "trend" | "counter" | "price" | "gap" | "cross" | "moved" | "day" | "speed" | "arm";
const COLUMNS: readonly Col[] = ["symbol", "trend", "counter", "price", "gap", "cross", "moved", "day", "speed", "arm"];

const pct = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`);
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "text-slate-300" : v > 0 ? "text-profit" : "text-loss");

/**
 * Turning: stocks whose SMA trend still points one way while the last candles in a row go the other way and close back
 * beyond SMA 9 — the turn before the SMAs cross (the owner's ICICI Bank 14:15 example). Candles only; Arm only puts a
 * stock on a bot's Trade list.
 */
export function TurnScan({ armed, arming, onArm, onArmMany, paused = false, onStatus }: Props) {
  const cols = useColumnOrder<Col>("scalp.cols.turn", COLUMNS);
  const [data, setData] = useState<TurnData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [minutes, setMinutes] = useState(5);
  const [count, setCount] = useState(3);
  const [rule, setRule] = useState<TurnRule>("COLOUR");
  const [needFast, setNeedFast] = useState(true);
  const [alert, setAlert] = useState(false);
  const [way, setWay] = useState<Way>("ALL");
  const [minTrend, setMinTrend] = useState(0);
  const [crossWithin, setCrossWithin] = useState(0);
  const [query, setQuery] = useState("");
  const [lists, setLists] = useState<ScanList[]>(["fno"]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) || "null");
      if (SIZES.includes(saved?.minutes)) setMinutes(saved.minutes);
      if (typeof saved?.count === "number") setCount(saved.count);
      if (saved?.rule && saved.rule in RULE_TEXT) setRule(saved.rule);
      if (typeof saved?.needFast === "boolean") setNeedFast(saved.needFast);
      if (typeof saved?.alert === "boolean") setAlert(saved.alert);
      if (saved?.way) setWay(saved.way);
      if (typeof saved?.minTrend === "number") setMinTrend(saved.minTrend);
      if (typeof saved?.crossWithin === "number") setCrossWithin(saved.crossWithin);
      setLists(cleanLists(saved?.lists, ["fno"]));
    } catch {
      /* private mode or an old value */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ minutes, count, rule, needFast, alert, way, minTrend, crossWithin, lists }));
    } catch {
      /* private mode */
    }
  }, [minutes, count, rule, needFast, alert, way, minTrend, crossWithin, lists]);

  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const symbols = await resolveScanSymbols(lists, armed.keys());
      setData(await smaApi.turnScanStart({ symbols, minutes, count, rule, need_fast: needFast, alert }));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not start the scan");
    } finally {
      setBusy(false);
    }
  }, [armed, lists, minutes, count, rule, needFast, alert]);

  // Opening the tab only shows the last result the server holds; nothing is scanned until Scan now.
  useEffect(() => {
    let live = true;
    smaApi
      .turnScan()
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
        .turnScan()
        .then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Lost the scan"));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [data?.running]);
  useEffect(() => {
    onStatus?.({ when: data?.finished_at ?? data?.as_of ?? null, running: !!data?.running });
  }, [onStatus, data?.finished_at, data?.as_of, data?.running]);

  const all = useMemo(() => data?.rows ?? [], [data?.rows]);
  const up = all.filter((r) => r.turn === "LONG").length;
  const ticks = useTicks();
  const filtered = useMemo(
    () =>
      all.filter((r) => {
        if (!matchesText(query, r.symbol) || !ticks.shows(r.symbol)) return false;
        if (way !== "ALL" && r.turn !== way) return false;
        if (minTrend > 0 && r.trend_candles < minTrend) return false;
        if (crossWithin > 0 && (r.minutes_to_cross == null || r.minutes_to_cross > crossWithin)) return false;
        return true;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [all, query, way, minTrend, crossWithin, ticks.only, ticks.ticked]
  );
  const { sorted: rows, sort, onSort } = useSort<TurnRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "trend":
        return r.trend_candles;
      case "counter":
        return r.counter_candles;
      case "gap":
        return Math.abs(r.gap_pct);
      case "closed":
        return r.gap_closed_pct ?? -1;
      case "cross":
        return r.minutes_to_cross ?? 1e9;
      case "moved":
        return r.move_since_turn_pct ?? 0;
      case "day":
        return r.day_move_pct ?? 0;
      case "speed":
        return Math.abs(r.speed_pct_per_min ?? 0);
    }
  });
  const paged = usePaged(rows, "scalp.turn.pageSize");
  const sma = data ? `SMA ${data.sma_fast}/${data.sma_slow}` : "SMA 9/21";
  const size = data?.minutes ?? minutes;

  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Undo2 size={15} className="text-accentSky" /> Turning — candles turning against the trend before the SMAs cross
        </h2>
        <button
          type="button"
          onClick={() => void start()}
          disabled={busy || data?.running || paused}
          title={paused ? "Scans are paused at the top of the page" : undefined}
          className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
        >
          {busy || data?.running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
        </button>
        <StopScan
          running={data?.running}
          stop={() => smaApi.scanStop("turn")}
          onStopped={() => void smaApi.turnScan().then(setData).catch(() => undefined)}
        />
      </div>
      <GuideBar lesson="scalp-turning" terms={["sma", "cross", "trend", "gap_pct", "candle", "speed", "arm"]} className="mb-2" />
      <Explain
        className="mb-3"
        lead={`A falling stock (${sma} pointing down) whose last candles turn green and close back above SMA ${data?.sma_fast ?? 9} — before the lines cross. Or the mirror: a rising stock turning red. Candles only — nothing here places an order.`}
      >
        <TurnPicture />
        On each {size}-minute candle: the trend is which way SMA {data?.sma_fast ?? 9} points against SMA {data?.sma_slow ?? 21}{" "}
        now, and how long it has (since the last cross). A stock is listed when the last {data?.count ?? count} closed candles in a
        row went the other way ({RULE_TEXT[data?.rule ?? rule].toLowerCase()}) today
        {(data?.need_fast ?? needFast) ? ` and the last close is back beyond SMA ${data?.sma_fast ?? 9}` : ""}. The gap shows how far apart
        the two lines still are, how much of it has closed since the turn began, and when they would cross at that pace (an
        estimate). &quot;Moved since the turn&quot; is the price change since the first turning candle, the way it is turning;
        &quot;Day&quot; is today&apos;s move the trend&apos;s way. Soonest cross first. Arm puts a stock on a bot&apos;s Trade list; it
        orders only on that bot&apos;s own signal. A bot can trade the same picture by itself with Settings → 1. When to enter →
        &quot;Early reversal entry&quot; (counter candles).
      </Explain>

      <div className="mb-3 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-surface2/40 p-2 text-xs text-slate-400">
        <label className="inline-flex flex-col gap-1">
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
        <label className="inline-flex flex-col gap-1">
          Turning candles in a row
          <select
            value={count}
            disabled={busy || data?.running}
            onChange={(e) => setCount(Number(e.target.value))}
            className="min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
          >
            {[1, 2, 3, 4, 5, 6, 8, 10].map((n) => (
              <option key={n} value={n}>
                at least {n}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-flex flex-col gap-1">
          A turning candle is
          <select
            value={rule}
            disabled={busy || data?.running}
            onChange={(e) => setRule(e.target.value as TurnRule)}
            className="min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
          >
            {(Object.keys(RULE_TEXT) as TurnRule[]).map((k) => (
              <option key={k} value={k}>
                {RULE_TEXT[k]}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-flex min-h-9 items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={needFast} onChange={(e) => setNeedFast(e.target.checked)} className="accent-[#10B981]" />
          Last close back beyond SMA {data?.sma_fast ?? 9}
        </label>
        <label className="inline-flex min-h-9 items-center gap-2 text-sm text-slate-300" title="Market hours only; each stock once a day; the quiet-runner Telegram channel (else the trade one)">
          <input type="checkbox" checked={alert} onChange={(e) => setAlert(e.target.checked)} className="accent-[#10B981]" />
          Telegram me new ones
        </label>
      </div>

      <FilterRow
        status={
          data
            ? data.running
              ? `Scanning ${data.done} of ${data.total}…`
              : [lastScanText(data, data.minutes), data.as_of ? `${all.length} turning: ${up} up, ${all.length - up} down` : null]
                  .filter(Boolean)
                  .join(" · ") || "No scan yet"
            : null
        }
      >
        <SelectFilter
          label="Turning"
          value={way}
          onChange={(v) => setWay(v as Way)}
          options={[
            { value: "ALL", label: `Both ways (${all.length})` },
            { value: "LONG", label: `Up, against a fall (${up})` },
            { value: "SHORT", label: `Down, against a rise (${all.length - up})` },
          ]}
          className="w-52"
        />
        <NumberFilter label="Trend at least" value={minTrend} onChange={setMinTrend} suffix="candles" step={1} min={0} max={200} className="w-36" />
        <NumberFilter label="Cross within" value={crossWithin} onChange={setCrossWithin} suffix="min" step={5} min={0} max={240} className="w-32" />
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
          {data?.running ? "Scanning…" : data?.as_of ? "No stock is turning with these settings." : "Nothing is scanned until you press Scan now."}
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
                  trend: <SortTh label="Trend" k="trend" sort={sort} onSort={onSort} text title="Which way the SMAs point, since when, and for how many candles" drag={cols.dragProps("trend")} />,
                  counter: <SortTh label="Turning" k="counter" sort={sort} onSort={onSort} num title="Candles in a row against the trend, and since when" drag={cols.dragProps("counter")} />,
                  price: (
                    <th {...cols.dragProps("price")} title="Last close · SMA fast / slow" className={clsx("pb-2 text-right font-medium", cols.dragProps("price").className)}>
                      Close · SMA
                    </th>
                  ),
                  gap: <SortTh label="SMA gap" k="closed" sort={sort} onSort={onSort} num title="The gap between the SMAs now (and when the turn began), and how much of it has closed" drag={cols.dragProps("gap")} />,
                  cross: <SortTh label="Cross in" k="cross" sort={sort} onSort={onSort} num title="Minutes to the SMA cross at the gap's pace since the turn began (an estimate)" drag={cols.dragProps("cross")} />,
                  moved: <SortTh label="Moved since turn" k="moved" sort={sort} onSort={onSort} num title="Price change since the first turning candle, + the way it is turning" drag={cols.dragProps("moved")} />,
                  day: <SortTh label="Day" k="day" sort={sort} onSort={onSort} num title="Today's move from the open, + the trend's way" drag={cols.dragProps("day")} />,
                  speed: <SortTh label="Speed" k="speed" sort={sort} onSort={onSort} num title="% per minute over the last 10 minutes" drag={cols.dragProps("speed")} />,
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

/** A small picture of the idea: a falling stock, red candles, then green ones closing above SMA 9 before the cross. */
function TurnPicture() {
  const reds = [
    [10, 30, 40],
    [26, 38, 48],
    [42, 46, 58],
    [58, 56, 66],
    [74, 64, 72],
  ];
  const greens = [
    [96, 66, 60],
    [112, 60, 52],
    [128, 54, 44],
  ];
  return (
    <svg viewBox="0 0 170 90" className="float-right mb-2 ml-3 w-full max-w-[220px]" role="img" aria-label="Red candles falling, then green candles turning up before the lines cross">
      <path d="M4 22 C40 40 70 62 100 64 S150 50 166 40" fill="none" stroke="#f43f5e" strokeWidth="1.6" />
      <path d="M4 14 C50 30 90 52 120 58 S150 56 166 52" fill="none" stroke="#38bdf8" strokeWidth="1.6" />
      {reds.map(([x, o, c]) => (
        <rect key={`r${x}`} x={x} y={Math.min(o, c)} width="9" height={Math.abs(c - o) || 2} fill="#ef4444" rx="1" />
      ))}
      {greens.map(([x, o, c]) => (
        <rect key={`g${x}`} x={x} y={Math.min(o, c)} width="9" height={Math.abs(c - o) || 2} fill="#10b981" rx="1" />
      ))}
      <line x1="92" y1="6" x2="92" y2="84" stroke="currentColor" strokeDasharray="3 3" className="text-slate-500" />
      <text x="94" y="12" fontSize="8" className="fill-slate-400">
        turn
      </text>
      <text x="4" y="86" fontSize="7" className="fill-slate-500">
        SMA 9 (red) under SMA 21 (blue)
      </text>
    </svg>
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
  row: TurnRow;
  tick: React.ReactNode;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  const up = r.turn === "LONG";
  return (
    <tr className="whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2">
      {tick}
      {inOrder(order, {
        symbol: (
          <td className="py-1.5 font-semibold text-slate-100">
            <GrowwLink symbol={r.symbol} />
          </td>
        ),
        trend: (
          <td className="py-1.5">
            <span
              className={clsx(
                "rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
                r.trend === "BULLISH" ? "bg-profit/10 text-profit ring-profit/40" : "bg-loss/10 text-loss ring-loss/40"
              )}
            >
              {r.trend === "BULLISH" ? "Rising ↑" : "Falling ↓"}
            </span>
            <span className="ml-1.5 text-[11px] text-slate-500">
              {r.trend_since ? `since ${r.trend_since} · ` : ""}
              {r.trend_candles} candles
            </span>
          </td>
        ),
        counter: (
          <td className="py-1.5 text-right">
            <span className={clsx("font-semibold", up ? "text-profit" : "text-loss")}>
              {r.counter_candles} {up ? "green ↑" : "red ↓"}
            </span>
            <span className="ml-1 text-[11px] text-slate-500">from {r.counter_since}</span>
          </td>
        ),
        price: (
          <td className="py-1.5 text-right tabular-nums text-slate-300">
            {r.close.toFixed(2)}
            <span className="ml-1 text-[11px] text-slate-500">
              · {r.sma_fast.toFixed(2)} / {r.sma_slow.toFixed(2)}
            </span>
          </td>
        ),
        gap: (
          <td className="py-1.5 text-right tabular-nums" title={r.gap_at_turn_pct != null ? `At the turn: ${pct(r.gap_at_turn_pct, 3)}` : undefined}>
            {pct(r.gap_pct, 3)}
            {r.gap_closed_pct != null ? (
              <span className={clsx("ml-1 text-[11px]", r.gap_closed_pct > 0 ? "text-amber-300" : "text-slate-500")}>
                {r.gap_closed_pct > 0 ? `${Math.round(r.gap_closed_pct)}% closed` : "still widening"}
              </span>
            ) : null}
          </td>
        ),
        cross: (
          <td className="py-1.5 text-right tabular-nums text-slate-200">
            {r.minutes_to_cross != null ? `~${Math.max(0, Math.round(r.minutes_to_cross))} min` : "—"}
          </td>
        ),
        moved: <td className={clsx("py-1.5 text-right tabular-nums font-semibold", tone(r.move_since_turn_pct))}>{pct(r.move_since_turn_pct)}</td>,
        day: <td className={clsx("py-1.5 text-right tabular-nums", tone(r.day_move_pct))}>{pct(r.day_move_pct)}</td>,
        speed: <td className={clsx("py-1.5 text-right tabular-nums", tone(r.speed_pct_per_min))}>{pct(r.speed_pct_per_min, 3)}</td>,
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
