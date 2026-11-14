"use client";

import { NewTag } from "@/components/ui/NewTag";
import clsx from "clsx";
import { CandlestickChart, Loader2 } from "lucide-react";
import { useState } from "react";
import { smaApi, type CandleDirRule, type FirstCandleRow, type FirstCandleRule, type SecondCandleSideRule } from "@/lib/smaApi";

/**
 * First candles on the Most active card (display only; backend first_candles.py): read the day's first N candles of the
 * stocks the filters show, with the Second candle rule (that candle / most of 1..N / that candle when most agree), so the
 * colours, how many matched and the call show as columns without opening every chart. No order is placed.
 */
export type FirstPick = "ALL" | "CALL" | "LONG" | "SHORT";
export const FIRST_PICKS: { value: FirstPick; label: string }[] = [
  { value: "ALL", label: "Any" },
  { value: "CALL", label: "Has a call" },
  { value: "LONG", label: "Call: Buy" },
  { value: "SHORT", label: "Call: Sell" },
];
const MAX_STOCKS = 200;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function firstPasses(pick: FirstPick, row: FirstCandleRow | undefined, read: boolean): boolean {
  if (!read || pick === "ALL") return true;
  if (!row?.call) return false;
  return pick === "CALL" || row.call === pick;
}

export function FirstCandleControls({
  symbols,
  disabled,
  at,
  onRows,
}: {
  symbols: string[];
  disabled?: boolean;
  /** Read as of this time (a saved scan's time); empty = now. */
  at?: string | null;
  onRows: (rows: Map<string, FirstCandleRow>, rule: FirstCandleRule) => void;
}) {
  const [rule, setRule] = useState<FirstCandleRule>({ minutes: 5, candle_no: 3, side_rule: "AGREE", majority_by: "COUNT", min_body_pct: 0, dir_rule: "COLOUR", ha: false, last_n: 3 });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const set = (k: keyof FirstCandleRule, v: number | string) => setRule((r) => ({ ...r, [k]: v }));
  const sel = "min-h-8 rounded-md border border-slate-700 bg-surface2 px-1 text-xs text-slate-100";

  const run = async () => {
    const list = symbols.slice(0, MAX_STOCKS);
    if (!list.length) return;
    setBusy(true);
    setNote(`Reading the first ${rule.candle_no} candles of ${list.length} stocks…`);
    try {
      await smaApi.firstCandlesStart({ ...rule, symbols: list, ...(at ? { at } : {}) });
      const want = new Set(list);
      for (let i = 0; i < 150; i += 1) {
        await sleep(2_500);
        const scan = await smaApi.firstCandles();
        if (!scan.running) {
          const rows = scan.rows.filter((r) => want.has(r.symbol));
          onRows(new Map(rows.map((r) => [r.symbol, r])), rule);
          const calls = rows.filter((r) => r.call).length;
          setNote(scan.error ? `First candles: ${scan.error}` : `First candles read: ${calls} of ${rows.length} stocks have a call.`);
          return;
        }
        setNote(`Reading first candles… ${scan.done} of ${scan.total}`);
      }
      setNote("Still reading; press the button again in a minute.");
    } catch (e: unknown) {
      setNote(e instanceof Error ? e.message : "Could not read the first candles");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
      <button
        type="button"
        onClick={() => void run()}
        disabled={busy || disabled || symbols.length === 0}
        title="Read today's first candles of the stocks the filters show now (up to 200). Read-only."
        className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-3 font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
      >
        {busy ? <Loader2 size={13} className="animate-spin" /> : <CandlestickChart size={13} />} Read first candles <NewTag id="ma-first-candles" link={false} />
      </button>
      <label className="flex items-center gap-1">
        up to candle
        <select value={rule.candle_no} onChange={(e) => set("candle_no", Number(e.target.value))} className={sel}>
          {[1, 2, 3, 4, 5, 6].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1">
        of
        <select value={rule.minutes} onChange={(e) => set("minutes", Number(e.target.value))} className={sel}>
          {[1, 3, 5, 10, 15].map((m) => (
            <option key={m} value={m}>
              {m} min
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1">
        trade which way
        <select value={rule.side_rule} onChange={(e) => set("side_rule", e.target.value as SecondCandleSideRule)} className={sel}>
          <option value="NTH">That candle&apos;s colour</option>
          <option value="MAJORITY">Most of candles 1 to N</option>
          <option value="AGREE">That candle, only if most agree</option>
        </select>
      </label>
      <label className="flex items-center gap-1">
        up / down by
        <select
          value={rule.dir_rule}
          onChange={(e) => set("dir_rule", e.target.value as CandleDirRule)}
          title="Colour = green / red. Closes = its close above / below the previous candle's close (candle 1 against yesterday's last close). Both = green and a higher close, red and a lower close."
          className={sel}
        >
          <option value="COLOUR">colour</option>
          <option value="CLOSES">closes</option>
          <option value="BOTH">colour and closes</option>
        </select>
      </label>
      {rule.side_rule !== "NTH" && (
        <label className="flex items-center gap-1">
          most by
          <select value={rule.majority_by} onChange={(e) => set("majority_by", e.target.value)} className={sel}>
            <option value="COUNT">green vs red</option>
            <option value="SIZE">body size</option>
          </select>
        </label>
      )}
      <label className="flex items-center gap-1">
        smallest body
        <input
          type="number"
          min={0}
          max={5}
          step={0.05}
          value={rule.min_body_pct}
          onChange={(e) => set("min_body_pct", Number(e.target.value))}
          className={clsx(sel, "w-16")}
        />
        %
      </label>
      <label
        className="flex items-center gap-1"
        title="Read each candle's colour and body from its Heikin Ashi candle (smoothed), as the bot does with Heikin Ashi colours on. The icons then draw the Heikin Ashi candles."
      >
        <input type="checkbox" checked={rule.ha} onChange={(e) => setRule((r) => ({ ...r, ha: e.target.checked }))} className="accent-[#10B981]" />
        Heikin Ashi
      </label>
      <label className="flex items-center gap-1" title="Also draw the last candles that had closed when the scan ran (a saved scan: its time; live: now).">
        last
        <select value={rule.last_n} onChange={(e) => set("last_n", Number(e.target.value))} className={sel}>
          {[0, 1, 2, 3, 4, 5, 6].map((n) => (
            <option key={n} value={n}>
              {n === 0 ? "off" : n}
            </option>
          ))}
        </select>
        candles
      </label>
      {at ? <span className="text-slate-500">as of {new Date(at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })} IST</span> : null}
      <span className="text-slate-600">Changed a setting? Press Read first candles again.</span>
      {note && <span className="text-slate-500">{note}</span>}
    </div>
  );
}

export function FirstCandleHead({ last = 0 }: { last?: number }) {
  return (
    <>
      {last > 0 && (
        <th className="pb-2 text-left font-medium" title="The last candles that had closed when the scan ran (same size), drawn to scale; the time is when the last one closed">
          Last {last} candles
        </th>
      )}
      <th className="pb-2 text-left font-medium" title="Today's first candles drawn to scale, candle 1 first (body and wicks; hover for each candle's type and size)">First candles</th>
      <th className="pb-2 text-right font-medium" title="How many of those candles went the call's way">Matched</th>
      <th className="pb-2 text-left font-medium" title="Buy / sell by the rule picked, or why there is no call">Call</th>
    </>
  );
}

/** What a candle looks like, in plain words: doji, marubozu, hammer, shooting star, spinning top or a normal candle. */
export function candleType(o: number, h: number, l: number, c: number): string {
  const range = h - l;
  if (!(range > 0)) return "flat";
  const body = Math.abs(c - o);
  const upper = h - Math.max(o, c);
  const lower = Math.min(o, c) - l;
  if (body <= range * 0.1) return "doji";
  if (body >= range * 0.9) return c > o ? "green marubozu (no wicks)" : "red marubozu (no wicks)";
  if (lower >= body * 2 && upper <= body) return "hammer (long lower wick)";
  if (upper >= body * 2 && lower <= body) return "shooting star (long upper wick)";
  if (body <= range * 0.3) return "spinning top (small body)";
  return c > o ? "green candle" : "red candle";
}

function tipFor(row: FirstCandleRow): string {
  return row.colours
    .map((c, i) => {
      const d = row.dirs?.[i];
      const close = row.dir_rule && row.dir_rule !== "COLOUR" ? `, ${d === "U" ? "up" : d === "D" ? "down" : "no direction"} by ${row.dir_rule === "CLOSES" ? "closes" : "colour and closes"}` : "";
      const k = row.ohlc?.[i];
      const shape = k ? `, ${candleType(k[0], k[1], k[2], k[3])}, range ${k[2] > 0 ? (((k[1] - k[2]) / k[2]) * 100).toFixed(2) : "0"}%` : "";
      return `${i + 1}: ${c === "G" ? "green" : c === "R" ? "red" : "doji"} body ${row.bodies[i]?.toFixed(2)}%${shape}${close}`;
    })
    .join("\n") + (row.ha ? "\n(Heikin Ashi candles)" : "");
}

/** The first candles drawn to scale: body and wicks, one price scale for the row. The scale covers at least 0.6% of the
 * price, so a quiet stock's candles look small and a busy one's big. Display only. */
function MiniCandles({ ohlc }: { ohlc: [number, number, number, number][] }) {
  const H = 24;
  const W = 6;
  const GAP = 3;
  const hi = Math.max(...ohlc.map((k) => k[1]));
  const lo = Math.min(...ohlc.map((k) => k[2]));
  const mid = (hi + lo) / 2;
  const span = Math.max(hi - lo, mid * 0.006) || 1;
  const top = mid + span / 2;
  const y = (p: number) => ((top - p) / span) * (H - 2) + 1;
  return (
    <svg width={ohlc.length * (W + GAP) - GAP} height={H} className="block" aria-hidden>
      {ohlc.map(([o, h, l, c], i) => {
        const x = i * (W + GAP);
        const colour = c > o ? "#10B981" : c < o ? "#F43F5E" : "#94A3B8";
        const bodyTop = y(Math.max(o, c));
        const bodyH = Math.max(1, y(Math.min(o, c)) - bodyTop);
        return (
          <g key={i}>
            <line x1={x + W / 2} x2={x + W / 2} y1={y(h)} y2={y(l)} stroke={colour} strokeWidth={1} />
            <rect x={x} y={bodyTop} width={W} height={bodyH} fill={colour} rx={0.5} />
          </g>
        );
      })}
    </svg>
  );
}

export function FirstCandleCells({ row, n, last = 0 }: { row: FirstCandleRow | null; n: number; last?: number }) {
  const lastCell =
    last > 0 ? (
      <td className="py-1.5">
        {row?.last_ohlc?.length ? (
          <span
            className="inline-flex items-end gap-1"
            title={row.last_ohlc
              .map((k, i) => `${i + 1}: ${candleType(k[0], k[1], k[2], k[3])}, body ${k[0] > 0 ? ((Math.abs(k[3] - k[0]) / k[0]) * 100).toFixed(2) : "0"}%`)
              .join("\n")}
          >
            <MiniCandles ohlc={row.last_ohlc} />
            {row.last_at ? <span className="text-[10px] text-slate-500">to {row.last_at}</span> : null}
          </span>
        ) : (
          <span className="text-xs text-slate-600">—</span>
        )}
      </td>
    ) : null;
  if (!row) {
    return (
      <>
        {lastCell}
        <td className="py-1.5 text-xs text-slate-600">—</td>
        <td />
        <td />
      </>
    );
  }
  return (
    <>
      {lastCell}
      <td className="py-1.5">
        <span className="inline-flex items-end gap-0.5" title={tipFor(row)}>
          {row.ohlc?.length ? <MiniCandles ohlc={row.ohlc} /> : null}
          {!row.ohlc?.length &&
            row.colours.map((c, i) => (
              <span key={i} className={clsx("inline-block h-3.5 w-2.5 rounded-sm", c === "G" ? "bg-emerald-500" : c === "R" ? "bg-rose-500" : "bg-slate-500")} />
            ))}
        </span>
        {row.dir_rule && row.dir_rule !== "COLOUR" && row.dirs?.length ? (
          <span className="flex gap-[3px] pl-px">
            {row.dirs.map((d, i) => (
              <span key={i} className={clsx("w-[6px] text-center text-[9px] leading-none", d === "U" ? "text-emerald-400" : d === "D" ? "text-rose-400" : "text-slate-500")}>
                {d === "U" ? "▲" : d === "D" ? "▼" : "·"}
              </span>
            ))}
          </span>
        ) : null}
        {row.ha ? <span className="text-[9px] text-slate-500">HA</span> : null}
      </td>
      <td className="py-1.5 text-right tabular-nums text-slate-300">{row.matched == null ? "—" : `${row.matched} of ${n}`}</td>
      <td className="py-1.5 text-xs">
        {row.call ? (
          <span className={clsx("font-semibold", row.call === "LONG" ? "text-emerald-300" : "text-rose-300")}>
            {row.call === "LONG" ? "Buy" : "Sell"}
            {row.entry_at ? <span className="ml-1 font-normal text-slate-500">at {row.entry_at}</span> : null}
          </span>
        ) : (
          <span className="text-slate-500">{row.why ?? "no call"}</span>
        )}
      </td>
    </>
  );
}
