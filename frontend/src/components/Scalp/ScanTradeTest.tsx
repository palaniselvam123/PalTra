"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { Loader2, PlayCircle } from "lucide-react";
import { api, type ActiveHistoryResponse } from "@/lib/api";
import { smaApi, type ScanTradeLeg, type ScanTradePick, type ScanTradeResult } from "@/lib/smaApi";

/**
 * "Trade a saved scan" on the Most active card (backend/scan_trade_test.py): one saved scan gives the day, the time
 * and the stocks; each is traded from that minute on the day's real candles, out on the first candle of the other
 * colour (normal and Heikin Ashi) or at the square-off. A bot's own strategy on the same stocks is the replay.
 * Arithmetic and practice money only: no order, nothing armed.
 */
const PICKS: { value: ScanTradePick; label: string }[] = [
  { value: "PRESSURE", label: "Buyers / sellers share" },
  { value: "BIAS", label: "The scan's bias (long / short)" },
  { value: "MOVE", label: "Up or down on the day" },
];

const clock = (iso: string) => new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
const money = (v: number | null | undefined) => (v == null ? "—" : `${v < 0 ? "−" : v > 0 ? "+" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`);
const tone = (v: number | null | undefined) => ((v ?? 0) > 0 ? "text-emerald-300" : (v ?? 0) < 0 ? "text-rose-300" : "text-slate-300");

export function ScanTradeTestView() {
  const [hist, setHist] = useState<ActiveHistoryResponse | null>(null);
  const [day, setDay] = useState("");
  const [runId, setRunId] = useState<number | null>(null);
  const [pick, setPick] = useState<ScanTradePick>("PRESSURE");
  const [share, setShare] = useState(75);
  const [topN, setTopN] = useState(20);
  const [minutes, setMinutes] = useState(5);
  const [squareOff, setSquareOff] = useState("15:15");
  const [qty, setQty] = useState(100);
  const [bot, setBot] = useState(1);
  const [data, setData] = useState<ScanTradeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const loadDay = useCallback(async (d?: string) => {
    try {
      const got = await api.scalpActiveHistory(d);
      setHist(got);
      setDay(got.day ?? "");
      // Default to a mid-morning scan: the first one at or after 10:00, else the day's first.
      const firstTen = got.runs.find((r) => clock(r.as_of) >= "10:00") ?? got.runs[0];
      setRunId(firstTen?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the saved scans");
    }
  }, []);

  useEffect(() => {
    void loadDay();
  }, [loadDay]);

  const run = async () => {
    if (runId == null) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      setData(await smaApi.scanTradeTest({ run_id: runId, pick, pct: share, top_n: topN, minutes, square_off: squareOff, qty }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not run the test");
    } finally {
      setBusy(false);
    }
  };

  const runBot = async () => {
    if (!data?.rows.length) return;
    setNote(null);
    setError(null);
    try {
      await smaApi.replayStart(data.day, data.rows[0].entry_at, 0, undefined, data.rows.map((r) => r.symbol), bot);
      setNote(`Bot ${bot}'s own strategy is now replaying these ${data.rows.length} stocks from ${data.rows[0].entry_at} on ${data.day}. Its result appears in the terminal's Backtests tab (practice money).`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the bot's replay");
    }
  };

  const select = "h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-200";
  const th = "px-2 py-1.5 text-right font-medium";
  const td = "px-2 py-1.5 text-right tabular-nums";
  const leg = (l: ScanTradeLeg) => (
    <>
      <td className={clsx(td, "text-slate-400")} title={l.reason === "candle turned" ? `Out after ${l.candles} candles: the candle turned` : "Held to the square-off"}>
        {l.exit_at}
      </td>
      <td className={clsx(td, "font-semibold", tone(l.rupees))}>{money(l.rupees)}</td>
    </>
  );

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-sky-400/25 bg-sky-400/[0.05] p-3 text-xs leading-relaxed text-slate-300">
        <p className="text-sm font-semibold text-slate-100">Would that scan&apos;s stocks really have made money?</p>
        <p className="mt-1">
          Pick a day and one saved Most active scan. Each stock the scan pointed at is bought (or sold short) at the next minute&apos;s real
          price and followed on that day&apos;s real candles three ways: out when a <b>normal candle</b> turns the other colour, out when a{" "}
          <b>Heikin Ashi candle</b> turns, or simply <b>held</b> to the square-off. You see what each made, per stock and in total.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Day
          <select className={select} value={day} onChange={(e) => void loadDay(e.target.value)}>
            {(hist?.days ?? []).map((d) => (
              <option key={d.day} value={d.day}>
                {d.day} ({d.runs} scans)
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Scan taken at
          <select className={select} value={runId ?? ""} onChange={(e) => setRunId(Number(e.target.value))}>
            {(hist?.runs ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {clock(r.as_of)} · {r.row_count} stocks
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Trade which stocks, which way
          <select className={select} value={pick} onChange={(e) => setPick(e.target.value as ScanTradePick)}>
            {PICKS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        {pick === "PRESSURE" ? (
          <label className="flex flex-col gap-1" title="Buyers at least this share: buy. Sellers at least this share: sell short.">
            Share at least
            <select className={select} value={share} onChange={(e) => setShare(Number(e.target.value))}>
              {[60, 65, 70, 75, 80, 85, 90].map((v) => (
                <option key={v} value={v}>
                  {v}%
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="flex flex-col gap-1" title="The busiest stocks by money traded">
          Busiest
          <select className={select} value={topN} onChange={(e) => setTopN(Number(e.target.value))}>
            {[5, 10, 20, 30, 40].map((v) => (
              <option key={v} value={v}>
                Top {v}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Candle size
          <select className={select} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
            {[1, 3, 5, 10, 15].map((v) => (
              <option key={v} value={v}>
                {v} min
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Square-off
          <input type="time" value={squareOff} min="09:20" max="15:30" onChange={(e) => setSquareOff(e.target.value)} className={clsx(select, "w-28")} />
        </label>
        <label className="flex flex-col gap-1">
          Shares per trade
          <input type="number" min={1} value={qty} onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))} className={clsx(select, "w-24")} />
        </label>
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy || runId == null}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-500/20 px-3 text-xs font-semibold text-sky-100 ring-1 ring-inset ring-sky-400/50 hover:bg-sky-500/30 disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <PlayCircle size={13} />} {busy ? "Reading the candles…" : "Run the test"}
        </button>
      </div>
      {hist && hist.days.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-800 p-4 text-sm text-slate-400">
          No saved Most active scans on this server yet. Keep the card&apos;s &quot;Auto scan&quot; on during market hours, then come back.
        </p>
      ) : null}
      {error ? <p className="text-xs font-semibold text-amber-300">{error}</p> : null}
      {busy ? <p className="text-xs text-slate-500">The first run of a day downloads each stock&apos;s candles (about a second a stock); later runs are quick.</p> : null}
      {data ? (
        <>
          <div className="rounded-lg border border-emerald-400/30 bg-emerald-400/[0.04] p-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-300">
              The answer · scan of {data.day} at {data.time} · {data.rows.length} stocks traded · {data.qty} shares each · {data.minutes}-minute candles
            </p>
            <ul className="mt-1 space-y-1 text-sm leading-relaxed text-slate-100">
              {data.answer.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            {data.ways.map((w) => (
              <div key={w.way} className={clsx("rounded-lg border p-3", data.best === w.way && w.rupees > 0 ? "border-emerald-400/50 bg-emerald-400/[0.05]" : "border-slate-800")}>
                <p className="text-xs font-semibold text-slate-300">
                  {w.label}
                  {data.best === w.way && w.rupees > 0 ? <span className="ml-2 rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-300">best</span> : null}
                </p>
                <p className={clsx("mt-1 text-2xl font-semibold tabular-nums", tone(w.rupees))}>{money(w.rupees)}</p>
                <p className="mt-1 text-xs text-slate-400">
                  {w.winners} won · {w.losers} lost{w.win_pct != null ? ` (${w.win_pct.toFixed(0)}% won)` : ""} · held {w.avg_minutes ?? "—"} min on average
                </p>
                <p className="text-xs text-slate-500">
                  Best trade {money(w.best_rupees)} · worst {money(w.worst_rupees)}
                </p>
              </div>
            ))}
          </div>
          {data.rows.length ? (
            <div className="overflow-x-auto rounded-lg border border-slate-800">
              <table className="w-full min-w-[820px] text-xs">
                <thead className="text-slate-400">
                  <tr>
                    <th className="px-2 py-1.5 text-left font-medium" rowSpan={2}>
                      Stock
                    </th>
                    <th className="px-2 py-1.5 text-left font-medium" rowSpan={2}>
                      Trade
                    </th>
                    <th className="px-2 py-1.5 text-left font-medium" rowSpan={2}>
                      Why
                    </th>
                    <th className={th} rowSpan={2}>
                      In at
                    </th>
                    <th className={th} rowSpan={2}>
                      Price
                    </th>
                    <th className="px-2 py-1.5 text-center font-medium" colSpan={2}>
                      Normal candles
                    </th>
                    <th className="px-2 py-1.5 text-center font-medium" colSpan={2}>
                      Heikin Ashi
                    </th>
                    <th className="px-2 py-1.5 text-center font-medium" colSpan={2}>
                      Hold
                    </th>
                  </tr>
                  <tr>
                    {[0, 1, 2].map((i) => (
                      <OutCols key={i} th={th} />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.symbol} className="border-t border-slate-800/70 text-slate-200">
                      <td className="px-2 py-1.5 font-semibold">{r.symbol}</td>
                      <td className="px-2 py-1.5">
                        <span className={clsx("rounded px-1.5 py-0.5 text-[10px] font-semibold", r.side === "LONG" ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300")}>
                          {r.side === "LONG" ? "Buy" : "Sell short"}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-slate-400">{r.why}</td>
                      <td className={td}>{r.entry_at}</td>
                      <td className={td}>₹{r.entry.toFixed(2)}</td>
                      {leg(r.normal)}
                      {leg(r.ha)}
                      {leg(r.hold)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {data.errors.length ? (
            <p className="text-xs text-slate-500">
              Not traded ({data.errors.length}): {data.errors.slice(0, 8).map((e) => `${e.symbol} (${e.error})`).join(", ")}
              {data.errors.length > 8 ? "…" : ""}
            </p>
          ) : null}
          {data.rows.length ? (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 p-3 text-xs text-slate-300">
              <span className="font-semibold text-slate-100">Now try a bot&apos;s own strategy on the same stocks:</span>
              <select className={select} value={bot} onChange={(e) => setBot(Number(e.target.value))}>
                {[1, 2, 3, 4].map((b) => (
                  <option key={b} value={b}>
                    Bot {b}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => void runBot()}
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 font-semibold text-sky-200 ring-1 ring-inset ring-sky-400/50 hover:bg-sky-500/10"
              >
                <PlayCircle size={13} /> Replay with this bot&apos;s settings from {data.rows[0].entry_at}
              </button>
              <span className="text-slate-500">Practice money. The bot waits for its own signals, so it may trade fewer of them.</span>
            </div>
          ) : null}
          {note ? <p className="text-xs font-semibold text-emerald-300">{note}</p> : null}
          <p className="text-xs leading-relaxed text-slate-500">
            How it trades: in at the open of the first minute after the scan; out at the open of the minute after a candle of the other colour
            closes (a candle already running at the entry is not counted), or at the square-off. Before charges. Heikin Ashi only changes
            which candle counts as turned; every price is the real traded price.
          </p>
        </>
      ) : null}
    </div>
  );
}

function OutCols({ th }: { th: string }) {
  return (
    <>
      <th className={th}>Out at</th>
      <th className={th}>Made</th>
    </>
  );
}
