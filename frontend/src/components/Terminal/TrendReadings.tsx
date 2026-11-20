"use client";

/**
 * A backtest's trend readings (backend trend_log.py; display only): for one stock and day, what the bot read at every
 * candle close (side, Trend quality score, checks, phase) and what it did with it. Runs saved before readings were kept
 * can work them out again from the candles ("Work it out now").
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Download, Loader2 } from "lucide-react";
import { NewTag } from "@/components/ui/NewTag";
import { smaApi, type TrendReadsResult } from "@/lib/smaApi";
import { CHECK_WORDS, checksPassed, istClock, phaseWord, sideWord, trendChange, type TrendRead } from "@/lib/trendReads";

const CHECK_ORDER = ["stack", "vwap", "slope", "fan", "hold", "structure", "run", "volume"];

function csvCell(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function TrendReadings({ runId, symbols }: { runId: number; symbols: string[] }) {
  const [open, setOpen] = useState(false);
  const [meta, setMeta] = useState<TrendReadsResult | null>(null);
  const [day, setDay] = useState("");
  const [symbol, setSymbol] = useState("");
  const [rows, setRows] = useState<TrendRead[]>([]);
  const [source, setSource] = useState<TrendReadsResult["source"]>("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);

  // The picker: days and stocks with saved readings (else the run's days and stocks, for "Work it out now").
  useEffect(() => {
    if (!open || meta) return;
    smaApi
      .replayRunTrendReads(runId)
      .then((m) => {
        setMeta(m);
        const first = m.stocks[0];
        setDay(first?.day ?? m.days[0] ?? "");
        setSymbol(first?.symbol ?? symbols[0] ?? "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not read the trend readings."));
  }, [open, meta, runId, symbols]);

  const saved = meta?.stocks ?? [];
  const days = useMemo(() => {
    const all = new Set<string>([...(meta?.days ?? []), ...saved.map((s) => s.day)]);
    return [...all].sort();
  }, [meta, saved]);
  const stockChoices = useMemo(() => {
    const onDay = saved.filter((s) => s.day === day).map((s) => s.symbol);
    const rest = symbols.filter((s) => !onDay.includes(s)).sort();
    return { onDay, rest };
  }, [saved, day, symbols]);

  const load = useCallback(
    (workOut: boolean) => {
      if (!symbol || !day) return;
      setBusy(true);
      setError("");
      smaApi
        .replayRunTrendReads(runId, { symbol, day, workOut })
        .then((r) => {
          setRows(r.rows);
          setSource(r.source);
        })
        .catch((e) => setError(e instanceof Error ? e.message : "Could not read the trend readings."))
        .finally(() => setBusy(false));
    },
    [runId, symbol, day],
  );

  useEffect(() => {
    if (open && meta && symbol && day) load(false);
  }, [open, meta, symbol, day, load]);

  const shown = useMemo(() => {
    const withChange = rows.map((r, i) => ({ r, change: trendChange(r, rows[i - 1]) }));
    return changedOnly ? withChange.filter((x, i) => i === 0 || x.change.text !== "—") : withChange;
  }, [rows, changedOnly]);

  const downloadCsv = () => {
    const head = ["day", "time", "stock", "price", "trend", "score", "checks", "missed", "label", "crosses", "phase", "held", "decision", "what_changed", "source"];
    const lines = [head.join(",")];
    rows.forEach((r, i) => {
      const c = checksPassed(r);
      lines.push(
        [r.day ?? day, istClock(r.ts), r.symbol ?? symbol, r.price, sideWord(r.side), r.score, `${c.passed}/${c.total}`, c.failed.join(" "),
          r.label, r.crosses, phaseWord(r), r.held ?? "", r.decision, trendChange(r, rows[i - 1]).text, r.source ?? source]
          .map(csvCell)
          .join(","),
      );
    });
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `trend-readings-run${runId}-${symbol}-${day}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const box = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";
  return (
    <details className="mb-4 rounded-md ring-1 ring-inset ring-white/10" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer px-2 py-2 text-xs uppercase tracking-wider text-slate-400">
        Trend readings · what the bot read at each candle and what it did <NewTag id="bt-trend-readings" link={false} />
      </summary>
      <div className="space-y-2 px-2 pb-3">
        <p className="text-xs text-slate-400">
          The backtest works the Trend quality reading out by itself from the 1-minute candles at every candle close; it does
          not read the saved Trend quality scans. Each row is that reading (trend score out of 100, the checks passed, the phase)
          and the bot&apos;s decision, so it lines up with the History tab&apos;s &quot;Every scan&quot; table. Only stocks traded on the
          trend reading (auto-armed by Trend quality, or all with &quot;every armed stock&quot;) are kept.
        </p>
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Day
            <select className={box} value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day">
              {days.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Stock
            <select className={box} value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Stock">
              {stockChoices.onDay.length ? (
                <optgroup label="Readings saved">
                  {stockChoices.onDay.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              <optgroup label={stockChoices.onDay.length ? "Other stocks of the run" : "Stocks of the run"}>
                {stockChoices.rest.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </optgroup>
            </select>
          </label>
          <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} />
            Only when it changed
          </label>
          {rows.length ? (
            <button type="button" onClick={downloadCsv} className="inline-flex min-h-9 items-center gap-1 rounded-md border border-white/15 px-3 text-sm text-slate-200 hover:bg-white/5">
              <Download className="h-4 w-4" aria-hidden /> CSV
            </button>
          ) : null}
          {busy ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" aria-label="Loading" /> : null}
        </div>
        {error ? <p className="text-sm text-rose-300">{error}</p> : null}
        {!busy && meta && source === "none" ? (
          <div className="rounded-md bg-white/5 p-2 text-sm text-slate-300">
            No readings were kept for {symbol || "this stock"} on {day || "this day"}: the run was played before readings were
            saved, or the stock was not traded on the trend reading.{" "}
            <button type="button" onClick={() => load(true)} className="ml-1 rounded-md border border-white/15 px-2 py-1 text-slate-100 hover:bg-white/10">
              Work it out now
            </button>
            <span className="mt-1 block text-xs text-slate-500">
              Reads the cached 1-minute candles again with this run&apos;s settings (SMA periods, candle size, the trend entry score
              and phase box). It does not know the bot&apos;s trades, so the decision only says what the trend entry would have said.
            </span>
          </div>
        ) : null}
        {source === "after" && rows.length ? (
          <p className="text-xs text-amber-300">Worked out now from the candles (not kept while the backtest played): no trades or exits known.</p>
        ) : null}
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="text-slate-400">
                <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                  <th>Time</th>
                  <th className="text-right">Price</th>
                  <th>Trend</th>
                  <th className="text-right" title="Share of the 7 strong-trend checks passed for the side the SMAs point to">
                    Trend score
                  </th>
                  <th>Checks</th>
                  <th title="Today's SMA crosses: trending / mixed / choppy">Score</th>
                  <th>Phase</th>
                  <th className="text-right">Crosses</th>
                  <th>What the bot did</th>
                  <th>What changed</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(({ r, change }) => {
                  const c = checksPassed(r);
                  const acted = /^opened|^closed the trade|^would enter/i.test(r.decision);
                  return (
                    <tr key={r.ts} className={clsx("border-t border-white/5 align-top [&_td]:px-2 [&_td]:py-1.5", acted && "bg-emerald-500/10")}>
                      <td className="whitespace-nowrap font-mono text-slate-200">{istClock(r.ts)}</td>
                      <td className="whitespace-nowrap text-right font-mono text-slate-200">{r.price?.toFixed(2) ?? "—"}</td>
                      <td className={clsx(r.side === "BULLISH" ? "text-emerald-300" : r.side === "BEARISH" ? "text-rose-300" : "text-slate-400")}>
                        {sideWord(r.side)}
                        {r.strong ? <span className="ml-1 text-slate-400">(Strong)</span> : null}
                      </td>
                      <td className="text-right font-mono text-slate-100">{r.score ?? "—"}</td>
                      <td className="whitespace-nowrap">
                        <span className="font-mono text-slate-200">
                          {c.passed}/{c.total}
                        </span>{" "}
                        {CHECK_ORDER.filter((k) => k in (r.checks ?? {})).map((k) => (
                          <span key={k} className={clsx("ml-0.5", r.checks[k] ? "text-emerald-300" : "text-rose-300")} title={`${CHECK_WORDS[k]}: ${r.checks[k] ? "passed" : "failed"}`}>
                            {r.checks[k] ? "✓" : "✗"}
                            <span className="sr-only">{CHECK_WORDS[k]}</span>
                          </span>
                        ))}
                        {c.failed.length ? <span className="block text-slate-500">missed {c.failed.join(", ")}</span> : null}
                      </td>
                      <td className="text-slate-300">{r.label ? r.label.charAt(0) + r.label.slice(1).toLowerCase() : "—"}</td>
                      <td className={clsx(r.phase === "BULL" ? "text-emerald-300" : r.phase === "BEAR" ? "text-rose-300" : "text-slate-300")}>{phaseWord(r)}</td>
                      <td className="text-right font-mono text-slate-300">{r.crosses ?? "—"}</td>
                      <td className="max-w-[24rem] text-slate-300">{r.decision}</td>
                      <td className={clsx(change.tone === "up" ? "text-emerald-300" : change.tone === "down" ? "text-rose-300" : "text-slate-500")}>{change.text}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </details>
  );
}
