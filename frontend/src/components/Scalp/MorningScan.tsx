"use client";

import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { lastScanText } from "@/lib/lastScan";
import { Loader2, RefreshCw, Sunrise } from "lucide-react";
import { api } from "@/lib/api";
import { smaApi, type MorningCheck, type MorningScan as MorningScanData, type MorningScanRow } from "@/lib/smaApi";
import { FilterField, FilterRow, SelectFilter, TextFilter, Toggle, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { ColumnHint, inOrder, useColumnOrder } from "@/components/ui/columnOrder";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { GrowwLink } from "@/components/ui/growwLink";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";

const POLL_MS = 3_000;
const STORE = "sma.morning.filters";

type Props = {
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
  paused?: boolean;
  onStatus?: OnScanStatus;
};

type Show = "RUNNERS" | "READY" | "ALL";
type Side = "ALL" | "LONG" | "SHORT";
type SortKey = "symbol" | "score" | "side" | "price" | "range" | "rvol" | "gap" | "dayMove";
type Col = "symbol" | "side" | "score" | MorningCheck | "price" | "range" | "rvol" | "gap" | "dayMove" | "arm";
const COLUMNS: readonly Col[] = ["symbol", "side", "score", "orb", "rvol", "vwap", "gap", "candles", "price", "range", "dayMove", "arm"];

/** The five checks, in order, with the short header and what each one asks. */
const CHECKS: { key: MorningCheck; head: string; text: string }[] = [
  { key: "orb", head: "Break", text: "Closes beyond the 09:15–09:30 high (long) or low (short). Inside the range = sideways." },
  { key: "rvol", head: "Volume", text: "Opening 15-minute volume at least 1.8× the same 15 minutes on the last 10 sessions." },
  { key: "vwap", head: "VWAP", text: "All three candles on one side of VWAP, no wick touching it." },
  { key: "gap", head: "SMA gap", text: "SMA 9/21 gap at least +0.10% (long) or −0.10% (short)." },
  { key: "candles", head: "Candles", text: "2 of 3 candles in the trend colour, the 09:25 close beyond the 09:15 close and the fast SMA." },
];

const num = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));

/**
 * The 09:30 morning checklist: five checks that can be read on the three opening
 * 5-minute candles, so a real morning trend is told apart from sideways chop (the
 * Trend quality checks need 12 candles and are not ready that early). 4 of 5 or more
 * is a runner. Display only: it places no order, and Arm only puts a stock on a
 * bot's Trade list.
 */
export function MorningScan({ armed, arming, onArm, onArmMany, paused = false, onStatus }: Props) {
  const cols = useColumnOrder<Col>("scalp.cols.morning", COLUMNS);
  const [data, setData] = useState<MorningScanData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState<Show>("RUNNERS");
  const [side, setSide] = useState<Side>("ALL");
  const [query, setQuery] = useState("");
  const [armedOnly, setArmedOnly] = useState(false);
  const universe = useRef<string[] | null>(null);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) || "null");
      if (saved?.show) setShow(saved.show);
      if (saved?.side) setSide(saved.side);
      if (typeof saved?.armedOnly === "boolean") setArmedOnly(saved.armedOnly);
    } catch {
      /* private mode or an old value */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ show, side, armedOnly }));
    } catch {
      /* private mode */
    }
  }, [show, side, armedOnly]);

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
        setData(await smaApi.morningScanStart(symbols, force));
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Could not start the scan");
      } finally {
        setBusy(false);
      }
    },
    [armed, armedOnly]
  );

  // Opening the tab only shows the last result the server holds; nothing is scanned until Scan now.
  useEffect(() => {
    let live = true;
    smaApi
      .morningScan()
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
        .morningScan()
        .then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Lost the scan"));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [data?.running]);

  useEffect(() => {
    onStatus?.({ when: data?.finished_at ?? data?.as_of ?? null, running: !!data?.running });
  }, [onStatus, data?.finished_at, data?.as_of, data?.running]);

  const runnerMin = data?.rules?.runner_min ?? 4;
  const all = useMemo(() => data?.rows ?? [], [data?.rows]);
  const counts = useMemo(
    () => ({
      ready: all.filter((r) => r.ready).length,
      runners: all.filter((r) => r.ready && r.runner).length,
      waiting: all.filter((r) => !r.ready).length,
    }),
    [all]
  );
  const filtered = useMemo(
    () =>
      all.filter((r) => {
        if (!matchesText(query, r.symbol)) return false;
        if (show === "RUNNERS" && !(r.ready && r.runner)) return false;
        if (show === "READY" && !r.ready) return false;
        if (side !== "ALL" && r.side !== side) return false;
        return true;
      }),
    [all, query, show, side]
  );
  const { sorted: rows, sort, onSort } = useSort<MorningScanRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "score":
        return r.ready ? r.score : -1;
      case "side":
        return r.side;
      case "price":
        return r.price ?? r.ltp;
      case "range":
        return r.or_high != null && r.or_low != null && r.or_low > 0 ? (r.or_high / r.or_low - 1) * 100 : null;
      case "rvol":
        return r.rvol;
      case "gap":
        return r.gap_pct;
      case "dayMove":
        return r.day_move_pct;
    }
  });
  const paged = usePaged(rows, "scalp.morning.pageSize");
  const ticks = useTicks();
  const sma = data ? `SMA ${data.sma_fast}/${data.sma_slow}` : "SMA 9/21";

  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Sunrise size={15} className="text-amber-300" /> 09:30 Morning — runners or chop, from the first three 5-minute candles
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
        lead={`At 09:30 a stock has only three 5-minute candles, too few for Trend quality. These five checks need only those three. ${runnerMin} or more of 5 is a runner worth arming; fewer stays in the chop zone. Candles only — nothing here places an order.`}
      >
        Break: the last closed 1-minute price after 09:30 is beyond the 09:15–09:30 high (long) or low (short). Volume: shares
        traded 09:15–09:30 are at least 1.8× the same 15 minutes on up to the last 10 sessions (needs 2). VWAP: each candle
        closes beyond today&apos;s VWAP, the 09:20 and 09:25 candles also open beyond it and none of their 1-minute wicks touches it
        (the VWAP starts inside the 09:15 candle, so that one is judged on its close). SMA gap: {sma} on 5-minute candles at
        least ±0.10% on the last closed candle. Candles: at least 2 of 3 in the trend colour, the 09:25 candle closing beyond the
        09:15 close and beyond the fast SMA. Each stock is judged for a long and a short; the side with more passes is shown.
        The first scan of the day downloads two weeks of 1-minute candles per stock for the volume history; later scans
        download four days, so a scan at 09:20 makes the 09:30 one quicker. Arm puts a stock on a bot&apos;s Trade list (Bot 3 for
        morning runners); it orders only on that bot&apos;s own signal.
      </Explain>

      <FilterRow
        status={
          data
            ? data.running
              ? `Scanning ${data.done} of ${data.total}…`
              : [lastScanText(data, data.minutes), data.as_of ? `${counts.runners} runner${counts.runners === 1 ? "" : "s"} of ${counts.ready} read${counts.waiting ? ` · ${counts.waiting} waiting for 09:30` : ""}` : null]
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
            { value: "RUNNERS", label: `Runners (${runnerMin}+ of 5)` },
            { value: "READY", label: "Every stock read" },
            { value: "ALL", label: "All, incl. waiting" },
          ]}
          className="w-44"
        />
        <SelectFilter
          label="Side"
          value={side}
          onChange={(v) => setSide(v as Side)}
          options={[
            { value: "ALL", label: "Both" },
            { value: "LONG", label: "Long" },
            { value: "SHORT", label: "Short" },
          ]}
          className="w-28"
        />
        <TextFilter value={query} onChange={setQuery} />
        <FilterField label="Stocks">
          <Toggle label="Armed only" checked={armedOnly} onChange={setArmedOnly} title="Only scan stocks on a bot's Trade list (else every F&O stock)" />
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
              ? show === "RUNNERS" && counts.ready > 0
                ? "No runner: every stock read is in the chop zone. Show “Every stock read” to see how each one scored."
                : counts.waiting > 0 && counts.ready === 0
                  ? "The 09:15–09:30 candles are not closed yet. Scan again after 09:30."
                  : "No stock matches these filters."
              : "Nothing is scanned until you press Scan now. Best pressed just after 09:30."}
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
                  side: <SortTh label="Side" k="side" sort={sort} onSort={onSort} text drag={cols.dragProps("side")} />,
                  score: (
                    <SortTh label="Score" k="score" sort={sort} onSort={onSort} num title={`Checks passed of 5 on the side shown; ${runnerMin}+ is a runner`} drag={cols.dragProps("score")} />
                  ),
                  ...Object.fromEntries(
                    CHECKS.map((c) => [
                      c.key,
                      <th key={c.key} {...cols.dragProps(c.key)} title={c.text} className={clsx("px-1 pb-2 text-center font-medium", cols.dragProps(c.key).className)}>
                        {c.head}
                      </th>,
                    ])
                  ) as Record<MorningCheck, React.ReactNode>,
                  price: <SortTh label="Price" k="price" sort={sort} onSort={onSort} num title="Last closed 1-minute close after 09:30 (else the last price)" drag={cols.dragProps("price")} />,
                  range: (
                    <SortTh label="09:15–09:30 range" k="range" sort={sort} onSort={onSort} num title="The opening range: low – high, and its width in %" drag={cols.dragProps("range")} />
                  ),
                  dayMove: <SortTh label="Day %" k="dayMove" sort={sort} onSort={onSort} num title="Last price vs today's open" drag={cols.dragProps("dayMove")} />,
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
                  runnerMin={runnerMin}
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
  runnerMin,
  tick,
  armedOn,
  arming,
  onArm,
}: {
  order: readonly Col[];
  row: MorningScanRow;
  runnerMin: number;
  tick: React.ReactNode;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  const long = r.side === "LONG";
  const width = r.or_high != null && r.or_low != null && r.or_low > 0 ? (r.or_high / r.or_low - 1) * 100 : null;
  const checkCell = (key: MorningCheck) => {
    const ok = r.checks?.[key];
    const note = r.notes?.[key] ?? "";
    if (!r.ready) return <td key={key} className="py-1.5 text-center text-slate-600" title={note}>·</td>;
    return (
      <td key={key} className="py-1.5 text-center" title={note}>
        <span className={clsx("text-sm font-semibold", ok ? "text-profit" : "text-loss")} aria-label={ok ? "pass" : "fail"}>
          {ok ? "✓" : "✗"}
        </span>
        {key === "rvol" && r.rvol != null ? <span className="ml-1 text-[11px] tabular-nums text-slate-400">{r.rvol.toFixed(1)}×</span> : null}
        {key === "gap" && r.gap_pct != null ? (
          <span className="ml-1 text-[11px] tabular-nums text-slate-400">
            {r.gap_pct >= 0 ? "+" : ""}
            {r.gap_pct.toFixed(2)}%
          </span>
        ) : null}
      </td>
    );
  };
  return (
    <tr className={clsx("whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2", r.ready && r.runner && "bg-profit/[0.04]")}>
      {tick}
      {inOrder(order, {
        symbol: (
          <td className="py-1.5 font-semibold text-slate-100">
            <GrowwLink symbol={r.symbol} />
          </td>
        ),
        side: (
          <td className="py-1.5">
            {r.side ? (
              <span
                className={clsx(
                  "rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
                  long ? "bg-accentSky/10 text-accentSky ring-accentSky/40" : "bg-accentViolet/10 text-accentViolet ring-accentViolet/40"
                )}
                title={`Long ${r.long_score}/5 · Short ${r.short_score}/5`}
              >
                {long ? "Long ↑" : "Short ↓"}
              </span>
            ) : (
              <span className="text-slate-600">—</span>
            )}
          </td>
        ),
        score: (
          <td className="py-1.5 text-right tabular-nums">
            {!r.ready ? (
              <span className="text-xs text-slate-500" title="The 09:15–09:30 candles are not closed yet">
                waiting
              </span>
            ) : (
              <span
                className={clsx(
                  "rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
                  r.runner ? "bg-profit/15 text-profit ring-profit/40" : "bg-slate-700/40 text-slate-400 ring-slate-500/40"
                )}
                title={[
                  r.runner ? `Runner: ${r.score} of 5 (needs ${runnerMin})` : `Chop zone: ${r.score} of 5 (needs ${runnerMin})`,
                  ...CHECKS.map((c) => `${r.checks?.[c.key] ? "✓" : "✗"} ${r.notes?.[c.key] ?? c.text}`),
                ].join("\n")}
              >
                {r.score}/5{r.runner ? " · runner" : ""}
              </span>
            )}
          </td>
        ),
        orb: checkCell("orb"),
        rvol: checkCell("rvol"),
        vwap: checkCell("vwap"),
        gap: checkCell("gap"),
        candles: checkCell("candles"),
        price: <td className="py-1.5 text-right tabular-nums">{num(r.price ?? r.ltp)}</td>,
        range: (
          <td className="py-1.5 text-right tabular-nums text-slate-400">
            {r.or_low != null && r.or_high != null ? (
              <>
                {num(r.or_low)} – {num(r.or_high)}
                {width != null ? <span className="ml-1 text-[11px] text-slate-500">({width.toFixed(2)}%)</span> : null}
              </>
            ) : (
              "—"
            )}
          </td>
        ),
        dayMove: (
          <td className={clsx("py-1.5 text-right tabular-nums", (r.day_move_pct ?? 0) > 0 ? "text-profit" : (r.day_move_pct ?? 0) < 0 ? "text-loss" : "text-slate-400")}>
            {r.day_move_pct == null ? "—" : `${r.day_move_pct >= 0 ? "+" : ""}${r.day_move_pct.toFixed(2)}%`}
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
    </tr>
  );
}
