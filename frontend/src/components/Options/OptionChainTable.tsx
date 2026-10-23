"use client";

import clsx from "clsx";
import { Fragment, useEffect, useMemo, useRef } from "react";
import type { OptionChain, OptionChainRow, OptionLeg } from "@/lib/api";
import { num } from "@/lib/format";

export type PickedLeg = { strike: number; type: "CE" | "PE"; leg: OptionLeg };

const COMPACT = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

function compact(v: number | null | undefined): string {
  return v == null ? "—" : COMPACT.format(v);
}

/** Open interest with a bar scaled to the biggest OI on that side, like Groww's chain. */
function OiCell({ value, max, side }: { value: number | null | undefined; max: number; side: "ce" | "pe" }) {
  const width = value && max ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <td className="relative px-2 py-1.5 text-right">
      <span
        aria-hidden
        className={clsx(
          "absolute inset-y-1 rounded-sm",
          side === "ce" ? "right-0 bg-loss/20" : "left-0 bg-profit/20"
        )}
        style={{ width: `${width}%` }}
      />
      <span className="relative">{compact(value)}</span>
    </td>
  );
}

function LtpButton({
  leg,
  strike,
  type,
  onPick,
  selected,
}: {
  leg: OptionLeg | null;
  strike: number;
  type: "CE" | "PE";
  onPick: (p: PickedLeg) => void;
  selected: boolean;
}) {
  if (!leg || leg.ltp == null) return <td className="px-2 py-1.5 text-center text-slate-600">—</td>;
  return (
    <td className="px-1 py-1 text-center">
      <button
        type="button"
        onClick={() => onPick({ strike, type, leg })}
        aria-label={`Trade ${strike} ${type} at ${leg.ltp}`}
        className={clsx(
          "min-h-8 w-full rounded-md px-2 font-semibold tabular-nums ring-1 ring-inset transition-colors",
          selected
            ? "bg-accentSky/25 text-accentSky ring-accentSky/60"
            : "text-slate-100 ring-white/10 hover:bg-white/10 hover:ring-accentSky/40"
        )}
      >
        {num(leg.ltp)}
      </button>
    </td>
  );
}

/**
 * Groww-style option chain: calls on the left, the strike in the middle, puts on the right.
 * In-the-money halves are shaded and a line marks the spot price. Click a price to trade it (practice).
 */
export function OptionChainTable({
  chain,
  rows,
  greeks,
  picked,
  onPick,
}: {
  chain: OptionChain;
  rows: OptionChainRow[];
  greeks: boolean;
  picked: PickedLeg | null;
  onPick: (p: PickedLeg) => void;
}) {
  const spot = chain.spot;
  const maxCe = useMemo(() => Math.max(0, ...rows.map((r) => r.ce?.oi ?? 0)), [rows]);
  const maxPe = useMemo(() => Math.max(0, ...rows.map((r) => r.pe?.oi ?? 0)), [rows]);
  const spotIndex = spot == null ? -1 : rows.findIndex((r) => r.strike > spot);
  const scroller = useRef<HTMLDivElement>(null);
  const spotRow = useRef<HTMLTableRowElement>(null);
  const centred = useRef("");

  // Bring the spot line into view once per underlying + expiry, not on every refresh.
  useEffect(() => {
    const key = `${chain.underlying}|${chain.expiry}`;
    if (centred.current === key || !spotRow.current || !scroller.current) return;
    centred.current = key;
    const box = scroller.current;
    box.scrollTop = spotRow.current.offsetTop - box.clientHeight / 2;
  }, [chain.underlying, chain.expiry, rows.length]);

  const sideCols = greeks ? 7 : 4;
  const head = "bg-surface px-2 py-2 font-medium";

  return (
    <div ref={scroller} className="max-h-[70vh] overflow-auto rounded-card border border-border">
      <table className="w-full min-w-[720px] border-collapse font-mono text-xs tabular-nums">
        <thead className="sticky top-0 z-10 bg-surface text-slate-400 shadow-[0_1px_0_rgba(127,127,127,0.25)]">
          <tr>
            <th colSpan={sideCols + 1} className={clsx(head, "text-center text-loss/90")}>
              CALLS
            </th>
            <th className={clsx(head, "text-center text-slate-300")}>Strike</th>
            <th colSpan={sideCols + 1} className={clsx(head, "text-center text-profit/90")}>
              PUTS
            </th>
          </tr>
          <tr className="text-right">
            <th className={head}>OI</th>
            <th className={head}>Volume</th>
            <th className={head}>IV</th>
            {greeks && (
              <>
                <th className={head}>Vega</th>
                <th className={head}>Theta</th>
                <th className={head}>Gamma</th>
              </>
            )}
            {greeks ? <th className={head}>Delta</th> : null}
            <th className={clsx(head, "text-center")}>LTP</th>
            <th className={clsx(head, "text-center")} />
            <th className={clsx(head, "text-center")}>LTP</th>
            {greeks ? <th className={head}>Delta</th> : null}
            {greeks && (
              <>
                <th className={head}>Gamma</th>
                <th className={head}>Theta</th>
                <th className={head}>Vega</th>
              </>
            )}
            <th className={head}>IV</th>
            <th className={head}>Volume</th>
            <th className={head}>OI</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const ceItm = spot != null && r.strike < spot;
            const peItm = spot != null && r.strike > spot;
            const ceCls = clsx(ceItm && "bg-amber-400/[0.07]");
            const peCls = clsx(peItm && "bg-amber-400/[0.07]");
            const isAtm = chain.summary.atm === r.strike;
            return (
              <Fragment key={r.strike}>
                {i === spotIndex && spot != null ? (
                  <tr ref={spotRow} aria-label={`Spot ${num(spot)}`}>
                    <td colSpan={sideCols * 2 + 3} className="p-0">
                      <div className="relative flex items-center justify-center border-t border-accentSky/70">
                        <span className="-mt-px rounded-b-md bg-accentSky px-2 py-0.5 font-sans text-[11px] font-semibold text-slate-950">
                          {chain.underlying} {num(spot)}
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : null}
                <tr className="border-t border-white/5 text-right text-slate-300 hover:bg-white/[0.03]">
                  <OiCell value={r.ce?.oi} max={maxCe} side="ce" />
                  <td className={clsx("px-2 py-1.5", ceCls)}>{compact(r.ce?.volume)}</td>
                  <td className={clsx("px-2 py-1.5", ceCls)}>{num(r.ce?.iv, 1)}</td>
                  {greeks && (
                    <>
                      <td className={clsx("px-2 py-1.5", ceCls)}>{num(r.ce?.vega)}</td>
                      <td className={clsx("px-2 py-1.5", ceCls)}>{num(r.ce?.theta)}</td>
                      <td className={clsx("px-2 py-1.5", ceCls)}>{num(r.ce?.gamma, 4)}</td>
                    </>
                  )}
                  {greeks ? <td className={clsx("px-2 py-1.5", ceCls)}>{num(r.ce?.delta)}</td> : null}
                  <LtpButton
                    leg={r.ce}
                    strike={r.strike}
                    type="CE"
                    onPick={onPick}
                    selected={picked?.strike === r.strike && picked.type === "CE"}
                  />
                  <td
                    className={clsx(
                      "bg-white/[0.04] px-3 py-1.5 text-center font-semibold",
                      isAtm ? "text-accentSky" : "text-slate-100"
                    )}
                  >
                    {r.strike.toLocaleString("en-IN")}
                  </td>
                  <LtpButton
                    leg={r.pe}
                    strike={r.strike}
                    type="PE"
                    onPick={onPick}
                    selected={picked?.strike === r.strike && picked.type === "PE"}
                  />
                  {greeks ? <td className={clsx("px-2 py-1.5", peCls)}>{num(r.pe?.delta)}</td> : null}
                  {greeks && (
                    <>
                      <td className={clsx("px-2 py-1.5", peCls)}>{num(r.pe?.gamma, 4)}</td>
                      <td className={clsx("px-2 py-1.5", peCls)}>{num(r.pe?.theta)}</td>
                      <td className={clsx("px-2 py-1.5", peCls)}>{num(r.pe?.vega)}</td>
                    </>
                  )}
                  <td className={clsx("px-2 py-1.5", peCls)}>{num(r.pe?.iv, 1)}</td>
                  <td className={clsx("px-2 py-1.5", peCls)}>{compact(r.pe?.volume)}</td>
                  <OiCell value={r.pe?.oi} max={maxPe} side="pe" />
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
