"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { ArrowRightLeft, Loader2, RefreshCw, X } from "lucide-react";
import { smaApi, type HandoffRow, type TrendGroupRow } from "@/lib/smaApi";
import { trendColor, trendText, type TrendGroup } from "@/lib/trendAtEntry";
import { Badge, pnlTone } from "./ui";

const todayIst = () => new Date(Date.now() + 5.5 * 3600 * 1000).toISOString().slice(0, 10);
const signed = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

const KIND_SHORT: Record<string, string> = {
  RECOVERY: "Recovered loss",
  VTURN_LOSS: "V-turn loss",
  BREAKOUT: "Trend breakout",
  FAST_MOVE: "Fast move",
  FAST_BACK: "Back from Bot 5",
};

/** Bot 2 is the choppy bot, Bot 3 the trend bot (recovery.py / breakout.py). */
function botWord(bot: number): string {
  return bot === 2 ? "Bot 2 (choppy)" : bot === 3 ? "Bot 3 (trend)" : bot ? `Bot ${bot}` : "—";
}

function PhaseChip({ row }: { row: HandoffRow }) {
  const r = row.regime;
  if (!r || !r.phase) return <span className="text-slate-500">—</span>;
  const group: TrendGroup = r.phase === "CHOP" ? "CHOPPY" : "UP_WITH";
  const label = r.phase === "BULL" ? "Up ↑" : r.phase === "BEAR" ? "Down ↓" : "Choppy";
  return (
    <Badge color={r.phase === "BEAR" ? "violet" : trendColor(group)} title={trendText(r, null)}>
      {label}
    </Badge>
  );
}

/** The hand-offs as a table: when, stock, from → to, why, what happened, choppy / trending then. */
export function HandoffTable({ rows, showDay = false }: { rows: HandoffRow[]; showDay?: boolean }) {
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-xs">
        <thead className="text-slate-400">
          <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
            <th>{showDay ? "Day · time" : "Time"}</th>
            <th>Stock</th>
            <th>From → to</th>
            <th>Why</th>
            <th title="Choppy or trending at that moment, on the bot's own candle">Trend then</th>
            <th>What happened</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-white/5 align-top [&_td]:px-2 [&_td]:py-1.5">
              <td className="whitespace-nowrap font-mono text-slate-200">
                {showDay && r.at ? `${r.at.slice(5, 10)} ` : ""}
                {r.at ? r.at.slice(11, 16) : "—"}
              </td>
              <td className="font-semibold text-amber-300">{r.symbol}</td>
              <td className="whitespace-nowrap text-slate-200">
                Bot {r.from_bot} → {botWord(r.to_bot)}
              </td>
              <td className="text-slate-300" title={r.why ?? undefined}>
                <span className="font-medium">{KIND_SHORT[r.kind] ?? r.kind}</span>
                {r.why ? <span className="block max-w-[18rem] truncate text-slate-500">{r.why}</span> : null}
              </td>
              <td>
                <PhaseChip row={r} />
              </td>
              <td className={clsx("max-w-[22rem]", r.moved ? "text-emerald-300" : "text-slate-400")} title={r.result ?? undefined}>
                {r.moved ? "Moved" : "Not moved"}
                {r.result ? <span className="block truncate text-slate-500">{r.result}</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A backtest run's closed trades split by the trend when each opened. */
export function ByTrend({ groups }: { groups: TrendGroupRow[] }) {
  if (!groups.length) return null;
  return (
    <div className="mb-4">
      <div className="mb-1 px-2 text-xs uppercase tracking-wider text-slate-400" title="Choppy or trending on the bot's own candle when each trade opened">
        By trend at entry
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] whitespace-nowrap text-left text-xs">
          <thead className="text-xs uppercase tracking-wider text-slate-400">
            <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
              <th>When it opened</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Win %</th>
              <th className="text-right" title="Before charges">P&amp;L</th>
              <th className="text-right">After charges</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5 font-mono">
            {groups.map((g) => (
              <tr key={g.group} className="[&_td]:px-2 [&_td]:py-1.5">
                <td className="font-sans">
                  <Badge color={trendColor(g.group)}>{g.label}</Badge>
                </td>
                <td className="text-right text-slate-200">{g.trades}</td>
                <td className="text-right text-slate-300">{g.win_pct == null ? "—" : `${g.win_pct.toFixed(0)}%`}</td>
                <td className={clsx("text-right font-semibold", pnlTone(g.gross))}>{signed(g.gross)}</td>
                <td className={clsx("text-right", pnlTone(g.net))}>{signed(g.net)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** A market day's hand-offs (Stocks bar / blotter button): which stocks went to Bot 2, Bot 3 or Bot 5, and why. */
export function HandoffTrail({ bot, onClose }: { bot?: number; onClose: () => void }) {
  const [day, setDay] = useState(todayIst);
  const [onlyBot, setOnlyBot] = useState(bot != null);
  const [rows, setRows] = useState<HandoffRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setLoading(true);
    smaApi
      .handoffs({ day, bot: onlyBot ? bot : undefined })
      .then((got) => {
        setRows(got.rows);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Hand-offs did not load"))
      .finally(() => setLoading(false));
  }, [day, bot, onlyBot]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div
      className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div role="dialog" aria-modal="true" aria-label="Hand-offs" className="max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-xl border border-border bg-surface p-4 text-sm shadow-xl">
        <div className="mb-1 flex items-center gap-2">
          <h2 className="mr-auto inline-flex items-center gap-1.5 font-semibold text-slate-100">
            <ArrowRightLeft size={15} aria-hidden /> Hand-offs between bots
          </h2>
          <button type="button" aria-label="Refresh" onClick={load} className="rounded p-1 text-slate-400 hover:bg-white/5">
            {loading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
          </button>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mb-3 text-xs text-slate-400">
          Every stock one bot passed to another: a recovered loss or losing V-turn to Bot 2 (choppy), a trend breakout to Bot 3
          (trend), a fast move to Bot 5 and back. &quot;Trend then&quot; is the stock&apos;s read at that moment. Backtests show their own
          hand-offs in the run&apos;s detail.
        </p>
        <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-slate-400">
          <label className="flex items-center gap-2">
            Day
            <input
              type="date"
              value={day}
              onChange={(e) => e.target.value && setDay(e.target.value)}
              className="h-8 rounded-md border border-border bg-surface px-1.5 font-mono text-xs text-slate-200 [color-scheme:dark]"
            />
          </label>
          {bot != null ? (
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={onlyBot} onChange={(e) => setOnlyBot(e.target.checked)} className="accent-sky-400" />
              Only to / from Bot {bot}
            </label>
          ) : null}
        </div>
        {error && <p className="mb-2 text-xs text-rose-300">{error}</p>}
        {rows.length === 0 ? (
          <p className="py-6 text-center text-xs text-slate-400">No hand-offs on this day. They are kept from the day this was added.</p>
        ) : (
          <HandoffTable rows={rows} />
        )}
      </div>
    </div>,
    document.body
  );
}
