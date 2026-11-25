"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { ArrowRightLeft, ChevronDown, ChevronRight, Loader2, RefreshCw, X } from "lucide-react";
import { smaApi, type HandoffRow, type HandoffTrade, type TrendGroupRow } from "@/lib/smaApi";
import { NewTag } from "@/components/ui/NewTag";
import { trendColor, trendText, type TrendGroup } from "@/lib/trendAtEntry";
import { Badge, pnlTone } from "./ui";

const todayIst = () => new Date(Date.now() + 5.5 * 3600 * 1000).toISOString().slice(0, 10);
const signed = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

const KIND_SHORT: Record<string, string> = {
  RECOVERY: "Recovered loss",
  VTURN_LOSS: "V-turn loss",
  CHOP_LOSSES: "Losing trades",
  BREAKOUT: "Trend breakout",
  TREND_PROFIT: "Trend in profit",
  STOP_EXIT: "Stop-loss exit",
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

type Basis = "gross" | "net";

/** One side's trade P&L: the amount, with its times and exit in the tooltip. */
function TradePnl({ t, basis }: { t: HandoffTrade | null | undefined; basis: Basis }) {
  if (!t) return <span className="text-slate-500">—</span>;
  const v = basis === "net" ? t.net : t.gross;
  const hhmm = (x: string | null) => (x ? x.slice(11, 16) : "?");
  return (
    <span
      className={clsx("font-mono font-semibold", pnlTone(v))}
      title={`${t.side} ${hhmm(t.entry_time)} → ${hhmm(t.exit_time)}${t.exit_reason ? ` · ${t.exit_reason}` : ""} · before charges ${signed(t.gross)}, after ${signed(t.net)}`}
    >
      {signed(v)}
      <span className="block font-sans text-[11px] font-normal text-slate-500">
        {hhmm(t.entry_time)} → {hhmm(t.exit_time)}
      </span>
    </span>
  );
}

/** Totals of the two P&L columns over the rows that have them: the sending bots' cut trades and the receiving
 * bots' trades. */
function PnlTotals({ rows, basis }: { rows: HandoffRow[]; basis: Basis }) {
  const pick = (t: HandoffTrade | null | undefined) => (t ? (basis === "net" ? t.net : t.gross) : null);
  const from = rows.map((r) => pick(r.from_trade)).filter((v): v is number => v != null);
  const to = rows.map((r) => pick(r.to_trade)).filter((v): v is number => v != null);
  if (!from.length && !to.length) return null;
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  const senders = [...new Set(rows.filter((r) => r.from_trade).map((r) => r.from_bot))].map((b) => `Bot ${b}`).join(" / ") || "Sending bot";
  const receivers = [...new Set(rows.filter((r) => r.to_trade).map((r) => r.to_bot))].map((b) => `Bot ${b}`).join(" / ") || "Receiving bot";
  const wins = to.filter((v) => v > 0).length;
  return (
    <p className="mb-1 flex flex-wrap gap-x-5 gap-y-1 px-2 text-xs text-slate-400">
      <span>
        {senders} trades cut ({from.length}): <span className={clsx("font-mono font-semibold", pnlTone(sum(from)))}>{signed(sum(from))}</span>
      </span>
      <span>
        {receivers} trades after ({to.length}, {wins} won):{" "}
        <span className={clsx("font-mono font-semibold", pnlTone(sum(to)))}>{signed(sum(to))}</span>
      </span>
      <span>
        Together: <span className={clsx("font-mono font-semibold", pnlTone(sum(from) + sum(to)))}>{signed(sum(from) + sum(to))}</span>
      </span>
      <span className="text-slate-500">{basis === "net" ? "after charges" : "before charges"}</span>
    </p>
  );
}

const FOLD_KEY = "backtests.handoffs.open";

function readOpen(): boolean {
  try {
    return window.localStorage.getItem(FOLD_KEY) !== "0";
  } catch {
    return true;
  }
}

/** A backtest run's hand-offs with a heading that shrinks / expands the table (kept per browser); the P&L totals
 * stay in view while it is shrunk. */
export function HandoffSection({ rows, showDay = false, basis }: { rows: HandoffRow[]; showDay?: boolean; basis?: Basis }) {
  const [open, setOpen] = useState(true);
  useEffect(() => setOpen(readOpen()), []);
  if (!rows.length) return null;
  const toggle = () =>
    setOpen((was) => {
      try {
        window.localStorage.setItem(FOLD_KEY, was ? "0" : "1");
      } catch {
        /* per-browser convenience only */
      }
      return !was;
    });
  const withPnl = basis != null && rows.some((r) => r.from_trade || r.to_trade);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="mb-4">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="mb-1 flex items-center gap-1 rounded px-2 text-xs uppercase tracking-wider text-slate-400 hover:text-slate-200"
        title={open ? "Shrink: hide the rows, keep the totals" : "Expand: show every hand-off"}
      >
        <Chevron className="h-3.5 w-3.5" aria-hidden />
        Hand-offs in this run ({rows.length}) · where each stock was sent
        <span className="ml-1 normal-case tracking-normal text-slate-500">{open ? "(shrink)" : "(expand)"}</span>
        <NewTag id="bt-handoff-fold" link={false} />
      </button>
      {open ? <HandoffTable rows={rows} showDay={showDay} basis={basis} /> : withPnl ? <PnlTotals rows={rows} basis={basis!} /> : null}
    </div>
  );
}

/** The hand-offs as a table: when, stock, from → to, why, what happened, choppy / trending then. With `basis` (a
 * run's detail), also each side's trade P&L. */
export function HandoffTable({ rows, showDay = false, basis }: { rows: HandoffRow[]; showDay?: boolean; basis?: Basis }) {
  if (!rows.length) return null;
  const withPnl = basis != null && rows.some((r) => r.from_trade || r.to_trade);
  const fromBots = [...new Set(rows.map((r) => r.from_bot))];
  const toBots = [...new Set(rows.map((r) => r.to_bot))];
  const fromHead = fromBots.length === 1 ? `Bot ${fromBots[0]} P&L` : "Sender P&L";
  const toHead = toBots.length === 1 ? `Bot ${toBots[0]} P&L` : "Receiver P&L";
  return (
    <div className="overflow-x-auto">
      {withPnl ? <PnlTotals rows={rows} basis={basis} /> : null}
      <table className="w-full min-w-[720px] text-left text-xs">
        <thead className="text-slate-400">
          <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
            <th>{showDay ? "Day · time" : "Time"}</th>
            <th>Stock</th>
            <th>From → to</th>
            <th>Why</th>
            <th title="Choppy or trending at that moment, on the bot's own candle">Trend then</th>
            <th>What happened</th>
            {withPnl ? (
              <>
                <th className="text-right" title="The trade the sending bot closed for this hand-off">
                  <span className="inline-flex items-center gap-1">
                    {fromHead} <NewTag id="bt-handoff-pnl" link={false} />
                  </span>
                </th>
                <th className="text-right" title="The first trade the receiving bot's settings opened on this stock after the hand-off">
                  {toHead}
                </th>
              </>
            ) : null}
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
              {withPnl ? (
                <>
                  <td className="whitespace-nowrap text-right">
                    <TradePnl t={r.from_trade} basis={basis} />
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <TradePnl t={r.to_trade} basis={basis} />
                  </td>
                </>
              ) : null}
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
