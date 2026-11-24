"use client";

import Link from "next/link";
import clsx from "clsx";
import { History, Loader2 } from "lucide-react";
import { useState } from "react";
import { NewTag } from "@/components/ui/NewTag";
import { ResumePickRuns } from "@/components/Scalp/ResumePickRuns";
import { replayActive, smaApi, type SavedScanColumn } from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";

const COLUMNS: { value: SavedScanColumn; label: string; hint: string }[] = [
  { value: "rvol", label: "Vol vs usual", hint: "Shares traded so far against the stock's usual by that time" },
  { value: "value", label: "Money traded", hint: "₹ crore traded so far" },
  { value: "buyers", label: "Buyers %", hint: "Buyers' share of the waiting orders (highest first)" },
  { value: "sellers", label: "Sellers %", hint: "Sellers' share of the waiting orders (highest first)" },
  { value: "imbalance", label: "Buyer/seller imbalance", hint: "Either side far from 50 % of the waiting orders" },
  { value: "exec_buyers", label: "Executed buy %", hint: "Share of the traded shares bought at market (scans that read it)" },
  { value: "exec_sellers", label: "Executed sell %", hint: "Share of the traded shares sold at market" },
  { value: "move", label: "Day move (either way)", hint: "Biggest move from yesterday's close, up or down" },
  { value: "up", label: "Day move up", hint: "Most up on the day" },
  { value: "down", label: "Day move down", hint: "Most down on the day" },
  { value: "speed", label: "Speed", hint: "% a minute since the previous scan, either way" },
  { value: "vwap", label: "Distance from average price", hint: "Furthest from today's average traded price" },
  { value: "range", label: "Day range", hint: "Widest high-low so far" },
];
const BOTS = [1, 2, 3, 4];
const DAY_CHOICES = [1, 5, 10, 15, 20, 30];

/**
 * Saved-scan backtest (backend saved_picks.py), one click: for each day, the first saved Most active scan at or after
 * the time asked, the top N by the ticked columns, traded by the chosen bot from the minute after the scan read them.
 * Practice money only; nothing is armed, no order is sent.
 */
export function SavedScanBacktest() {
  const init = lastClosedWeekdays(10);
  const [from, setFrom] = useState(init[0]);
  const [to, setTo] = useState(init[init.length - 1]);
  const [at, setAt] = useState("09:15");
  const [rankBy, setRankBy] = useState<SavedScanColumn[]>(["rvol"]);
  const [topN, setTopN] = useState(24);
  const [qty, setQty] = useState(100);
  const [side, setSide] = useState<"ANY" | "BUYERS" | "SELLERS">("ANY");
  const [minValue, setMinValue] = useState(0);
  const [minRvol, setMinRvol] = useState(0);
  // Last price (LTP) range in ₹ the scan read: 0 = no bound (saved_picks.Rule price_min / price_max).
  const [priceMin, setPriceMin] = useState(0);
  const [priceMax, setPriceMax] = useState(0);
  const [bot, setBot] = useState(1);
  const [playHandoffs, setPlayHandoffs] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string; lines?: string[] } | null>(null);

  const toggle = (c: SavedScanColumn) =>
    setRankBy((cur) => (cur.includes(c) ? (cur.length > 1 ? cur.filter((x) => x !== c) : cur) : [...cur, c]));
  const lastDays = (n: number) => {
    const r = lastClosedWeekdays(n);
    setFrom(r[0]);
    setTo(r[r.length - 1]);
  };

  const run = async () => {
    setBusy(true);
    setNote(null);
    try {
      if (!from || !to || from > to) throw new Error("Pick a From date on or before the To date.");
      const current = (await smaApi.replayAll().catch(() => null))?.[String(bot)] ?? null;
      if (current && replayActive(current) && current.status !== "FINISHED") {
        if (!window.confirm(`Bot ${bot}'s replay is already running. Stop it and start this backtest instead?`)) return;
      }
      const got = await smaApi.replaySavedScans({
        date: from,
        end_date: to,
        at,
        rank_by: rankBy,
        top_n: topN,
        qty,
        side,
        min_value_cr: minValue,
        min_rvol: minRvol,
        price_min: priceMin,
        price_max: priceMax,
        bot,
        play_handoffs: playHandoffs,
      });
      const range =
        priceMin || priceMax
          ? `, LTP ${priceMin ? `₹${priceMin.toLocaleString("en-IN")}` : "any"} to ${priceMax ? `₹${priceMax.toLocaleString("en-IN")}` : "any"}`
          : "";
      const by = rankBy.map((c) => COLUMNS.find((x) => x.value === c)?.label ?? c).join(" + ");
      setNote({
        ok: true,
        text: `Started on Bot ${bot}: ${from} → ${to}, ${got.days_with_picks ?? "?"} day(s) with a usable scan. Each day the top ${topN} by ${by}${range}${qty ? ` at ${qty} shares each` : " at the bot's own quantity"}. The run builds up in the terminal's Backtests tab; its detail and PDF list the scan each day used.`,
        lines: got.notes,
      });
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the backtest" });
    } finally {
      setBusy(false);
    }
  };

  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  return (
    <div className="space-y-3 rounded-lg border border-slate-800 p-3">
      <ResumePickRuns kinds={["SAVED"]} label="Saved Most active scan" />
      <div>
        <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-200">
          <History size={14} className="text-accentSky" /> Backtest the saved scans in one click <NewTag id="saved-scan-backtest" />
        </h3>
        <p className="max-w-3xl text-xs text-slate-500">
          For each day, the first saved Most active scan at or after the time you pick (with enough stocks that have every
          ticked column; a thinner scan is skipped and named, e.g. &ldquo;09:15 not available, run from 09:29&rdquo;). Its stocks
          are ranked by the columns you tick (several = each stock&apos;s places averaged), the top ones are traded by the bot
          from the minute after the scan read them, to the square-off, with its own settings. Practice money only — nothing is
          armed and no order is sent.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          From
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={select} />
        </label>
        <label className="flex flex-col gap-1">
          To
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={select} />
        </label>
        <div className="flex flex-wrap items-center gap-1 self-end">
          {DAY_CHOICES.map((d) => (
            <button key={d} type="button" onClick={() => lastDays(d)} className="rounded px-1.5 py-1 text-[11px] text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-white/5">
              Last {d}
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1" title="The first saved scan at or after this time is used each day">
          Scan at or after
          <input type="time" min="09:15" max="15:00" step={60} value={at} onChange={(e) => setAt(e.target.value || "09:15")} className={select} />
        </label>
        <label className="flex flex-col gap-1">
          Top
          <input type="number" min={1} max={50} value={topN} onChange={(e) => setTopN(Math.max(1, Math.min(50, Number(e.target.value) || 1)))} className={clsx(select, "w-16")} />
        </label>
        <label className="flex flex-col gap-1" title="Shares for every picked stock; 0 = the bot's own quantity">
          Qty each
          <input type="number" min={0} value={qty} onChange={(e) => setQty(Math.max(0, Math.floor(Number(e.target.value) || 0)))} className={clsx(select, "w-20")} />
        </label>
        <label className="flex flex-col gap-1">
          Which
          <select value={side} onChange={(e) => setSide(e.target.value as typeof side)} className={select}>
            <option value="ANY">Any</option>
            <option value="BUYERS">Buyers lead (waiting)</option>
            <option value="SELLERS">Sellers lead (waiting)</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Money traded at least (₹ cr)
          <input type="number" min={0} value={minValue} onChange={(e) => setMinValue(Math.max(0, Number(e.target.value) || 0))} className={clsx(select, "w-20")} />
        </label>
        <label className="flex flex-col gap-1">
          Vol vs usual at least (×)
          <input type="number" min={0} step={0.1} value={minRvol} onChange={(e) => setMinRvol(Math.max(0, Number(e.target.value) || 0))} className={clsx(select, "w-20")} />
        </label>
        <label className="flex flex-col gap-1" title="The last price (LTP) in the saved scan. 0 = no lower bound.">
          <span className="flex items-center gap-1">
            LTP from (₹) <NewTag id="saved-scan-ltp-range" link={false} />
          </span>
          <input type="number" min={0} value={priceMin} onChange={(e) => setPriceMin(Math.max(0, Number(e.target.value) || 0))} className={clsx(select, "w-24")} />
        </label>
        <label className="flex flex-col gap-1" title="The last price (LTP) in the saved scan. 0 = no upper bound.">
          LTP to (₹)
          <input type="number" min={0} value={priceMax} onChange={(e) => setPriceMax(Math.max(0, Number(e.target.value) || 0))} className={clsx(select, "w-24")} />
        </label>
        <label className="flex flex-col gap-1">
          Trade them on
          <select value={bot} onChange={(e) => setBot(Number(e.target.value))} className={select}>
            {BOTS.map((b) => (
              <option key={b} value={b}>
                Bot {b}&apos;s settings
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-h-9 items-center gap-1.5 self-end text-xs text-slate-200">
          <input type="checkbox" checked={playHandoffs} onChange={(e) => setPlayHandoffs(e.target.checked)} className="accent-violet-400" />
          Play hand-offs (Bot 2 / Bot 3)
        </label>
      </div>

      <div className="text-xs text-slate-400">
        <div className="mb-1">Rank by (tick one or more; highest first)</div>
        <div className="flex flex-wrap gap-1.5">
          {COLUMNS.map((c) => {
            const on = rankBy.includes(c.value);
            return (
              <button
                key={c.value}
                type="button"
                title={c.hint}
                aria-pressed={on}
                onClick={() => toggle(c.value)}
                className={clsx(
                  "rounded-full px-2.5 py-1 text-[11px] ring-1 ring-inset",
                  on ? "bg-accentSky/15 font-semibold text-accentSky ring-accentSky/40" : "text-slate-300 ring-slate-700 hover:bg-white/5",
                )}
              >
                {on ? "✓ " : ""}
                {c.label}
              </button>
            );
          })}
        </div>
      </div>

      <button
        type="button"
        disabled={busy}
        onClick={() => void run()}
        className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-accentSky/15 px-3 text-xs font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/25 disabled:opacity-40"
      >
        {busy ? <Loader2 size={13} className="animate-spin" /> : <History size={13} />} Run on the saved scans
      </button>
      {note && (
        <div className={clsx("text-xs", note.ok ? "text-accentSky" : "text-amber-300")}>
          <p>
            {note.text}{" "}
            {note.ok && (
              <Link href={bot === 1 ? "/terminal/" : `/terminal/?desk=bot${bot}`} className="font-semibold underline-offset-2 hover:underline">
                Open Bot {bot} →
              </Link>
            )}
          </p>
          {note.lines?.length ? (
            <ul className="mt-1 list-disc pl-5 text-slate-400">
              {note.lines.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </div>
  );
}
