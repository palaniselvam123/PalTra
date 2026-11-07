"use client";

import clsx from "clsx";
import { FlaskConical, Loader2, Play, Square, Trash2, Wand2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { money, num, pct } from "@/lib/format";
import {
  replayActive,
  smaApi,
  type SmaConfig,
  type StrategyLabChoice,
  type StrategyLabJob,
  type StrategyLabRecommend,
  type StrategyLabResult,
  type StrategyLabRules,
  type StrategyLabStop,
  type StrategyLabView,
} from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";

/**
 * Strategy lab (backend strategy_lab.py): tick the entry checks, exits and stop and watch the P&L change at once.
 * One run reads the picked stocks' past days (candles cached) into a table of base trades (SMA crosses) with every
 * rule's answer already in it; each click then re-adds that table on the server in milliseconds. "What each rule
 * adds" toggles every rule one at a time against the current choice. Practice money, read-only, nothing is armed;
 * the replay is the proof, this is the sieve. No order path.
 */
const POLL_MS = 2500;
const DAYS = [1, 2, 3, 5, 10, 15, 20, 30];
const BOTS = [1, 2, 3, 4];
const TIMES = ["09:20", "09:30", "09:45", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30"];
const EMPTY: StrategyLabChoice = { entries: [], exits: ["cross"], stop: "ATR", targets: [], entry_from: "09:20", entry_to: "15:30", qty: 0 };

const sameChoice = (a: StrategyLabChoice, b: StrategyLabChoice) =>
  JSON.stringify([a.entries, a.exits, a.stop, a.targets, a.entry_from, a.entry_to, a.qty]) ===
  JSON.stringify([b.entries, b.exits, b.stop, b.targets, b.entry_from, b.entry_to, b.qty]);

const hhmm = (ts: number) => new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

export function StrategyLab({ armed }: { armed: Map<string, string[]> }) {
  const [lists, setLists] = useScanLists("sma.lab.lists", ["armed"]);
  const [days, setDays] = useState(10);
  const [bot, setBot] = useState(1);
  const [label, setLabel] = useState("");
  const [view, setView] = useState<StrategyLabView | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [job, setJob] = useState<StrategyLabJob | null>(null);
  const [choice, setChoice] = useState<StrategyLabChoice>(EMPTY);
  const [result, setResult] = useState<StrategyLabResult | null>(null);
  const [rec, setRec] = useState<StrategyLabRecommend | null>(null);
  const [recFor, setRecFor] = useState<StrategyLabChoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [recBusy, setRecBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [show, setShow] = useState<"stocks" | "days" | "trades" | "reasons">("stocks");
  const latest = useRef(0);

  const rules: StrategyLabRules | null = view?.rules ?? null;

  const loadView = useCallback(async () => {
    try {
      setView(await smaApi.strategyLab());
    } catch {
      /* the server may be waking up */
    }
  }, []);
  useEffect(() => {
    void loadView();
  }, [loadView]);
  useEffect(() => {
    if (!view?.running) return;
    const id = setInterval(() => void loadView(), POLL_MS);
    return () => clearInterval(id);
  }, [view?.running, loadView]);

  // A finished run opens itself once.
  const lastDone = useRef<string | null>(null);
  useEffect(() => {
    const done = view?.jobs.find((j) => j.status === "DONE" || j.status === "STOPPED");
    if (!done || view?.running) return;
    if (openId === null && lastDone.current !== done.id) {
      lastDone.current = done.id;
      setOpenId(done.id);
    }
  }, [view, openId]);

  // Opening a run: its own choice (the bot's switches) evaluated by the server.
  useEffect(() => {
    if (!openId) {
      setJob(null);
      setResult(null);
      setRec(null);
      return;
    }
    let alive = true;
    setEvaluating(true);
    smaApi
      .strategyLabJob(openId)
      .then((v) => {
        if (!alive) return;
        setJob(v.job);
        setChoice({ ...EMPTY, ...(v.job.choice ?? {}) });
        setResult(v.result);
        setRec(null);
        setRecFor(null);
      })
      .catch((e: unknown) => alive && setNote({ ok: false, text: e instanceof Error ? e.message : "Could not open the run" }))
      .finally(() => alive && setEvaluating(false));
    return () => {
      alive = false;
    };
  }, [openId]);

  // Every change of the choice is evaluated at once; the last answer wins.
  const evaluate = useCallback(
    async (next: StrategyLabChoice) => {
      if (!openId) return;
      const mine = ++latest.current;
      setEvaluating(true);
      try {
        const v = await smaApi.strategyLabEvaluate(openId, next);
        if (mine === latest.current) setResult(v);
      } catch (e: unknown) {
        if (mine === latest.current) setNote({ ok: false, text: e instanceof Error ? e.message : "Could not evaluate" });
      } finally {
        if (mine === latest.current) setEvaluating(false);
      }
    },
    [openId]
  );
  const change = (next: StrategyLabChoice) => {
    setChoice(next);
    void evaluate(next);
  };
  const toggle = (kind: "entries" | "exits" | "targets", name: string) => {
    const set = new Set(choice[kind]);
    if (set.has(name)) set.delete(name);
    else set.add(name);
    change({ ...choice, [kind]: Array.from(set) });
  };
  const setAll = (kind: "entries" | "exits" | "targets", names: string[]) => change({ ...choice, [kind]: names });

  const run = async () => {
    const range = lastClosedWeekdays(days);
    setBusy(true);
    setNote(null);
    try {
      const symbols = (await resolveScanSymbols(lists as ScanList[], armed.keys())).slice(0, 250);
      if (!symbols.length) throw new Error("No stocks in the picked lists.");
      const v = await smaApi.strategyLabStart({ symbols, date: range[0], end_date: range[range.length - 1], bot, label });
      setView(v);
      setOpenId(null);
      lastDone.current = null;
      setNote({
        ok: true,
        text: `Reading ${symbols.length} stock(s), ${range[0]} → ${range[range.length - 1]}, with Bot ${bot}'s numbers. The first read of a day downloads its candles (a second or two a stock); days read before come from the cache.`,
      });
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start" });
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    try {
      setView(await smaApi.strategyLabStop());
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not stop" });
    }
  };
  const remove = async (id: string) => {
    if (!window.confirm("Delete this run's table? The cached candles stay, so running it again is quick.")) return;
    try {
      setView(await smaApi.strategyLabDelete(id));
      if (openId === id) setOpenId(null);
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not delete" });
    }
  };
  const recommend = async () => {
    if (!openId) return;
    setRecBusy(true);
    try {
      setRec(await smaApi.strategyLabRecommend(openId, choice));
      setRecFor(choice);
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not work out the rules" });
    } finally {
      setRecBusy(false);
    }
  };
  const useBest = () => {
    if (!rec) return;
    change({ ...choice, ...rec.best.choice });
  };

  /** Save this choice's switches on the bot and start the real replay on the same stocks and days. */
  const backtest = async () => {
    if (!job || !result) return;
    const target = job.bot as 1 | 2 | 3 | 4;
    const text = `Save these switches on Bot ${target} (the numbers behind them stay as they are) and replay ${job.symbols.length} stock(s), ${job.first} → ${job.last}, through the real bot? Practice money; no order is sent.`;
    if (!window.confirm(text)) return;
    setBusy(true);
    try {
      const current = (await smaApi.replayAll().catch(() => null))?.[String(target)] ?? null;
      if (current && replayActive(current) && current.status !== "FINISHED") {
        if (!window.confirm(`Bot ${target}'s replay is already running. Stop it and start this one instead?`)) return;
      }
      await smaApi.saveConfigOn(target, result.settings as Partial<SmaConfig>);
      await smaApi.replayStart(job.first, "09:15", 0, job.last, job.symbols, target, false, true);
      setNote({ ok: true, text: `Saved on Bot ${target} and started the replay. Its trades build up in the terminal's Backtests tab; compare them with the lab's ${result.totals.trades} trade(s) and ${money(result.totals.gross, true)}.` });
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the replay" });
    } finally {
      setBusy(false);
    }
  };

  const exportCsv = () => {
    if (!result?.trades) return;
    const head = ["symbol", "day", "side", "entry_time", "entry_price", "exit_time", "exit_price", "reason", "qty", "gross", "net", "charges", "best", "worst"];
    const rows = result.trades.map((t) =>
      [t.symbol, t.day, t.side, hhmm(t.entry_ts), t.entry_price, hhmm(t.exit_ts), t.exit_price, t.reason, t.qty, t.gross, t.net, t.charges, t.best, t.worst].join(",")
    );
    const blob = new Blob([[head.join(","), ...rows].join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `strategy-lab-${job?.first ?? ""}-${job?.last ?? ""}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  const current = view?.current;
  const running = Boolean(view?.running);
  const recStale = rec && recFor && !sameChoice(recFor, choice);

  return (
    <section className="space-y-4 rounded-xl border border-slate-800 bg-card p-4">
      <div>
        <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
          <FlaskConical size={15} className="text-accentSky" /> Strategy lab: which checks and exits make money?
        </h2>
        <p className="max-w-3xl text-xs text-slate-500">
          One run reads your stocks&apos; past days once and keeps every SMA cross as a trade with every rule&apos;s answer already
          worked out. Then tick a check, an exit or a stop and the P&amp;L changes at once — no replay. It works on closed candles
          (stops on the minute&apos;s high / low), so it is a sieve, not the bot: find the switches that help here, then press
          &quot;Backtest this in the replay&quot; to prove them through the real bot. Practice money; nothing is armed, no order is sent.
        </p>
      </div>

      {/* Run */}
      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Stocks
          <ScanListPicker value={lists as ScanList[]} onChange={setLists} disabled={running} />
        </label>
        <label className="flex flex-col gap-1">
          Last
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={select} disabled={running}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d} trading day{d === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Numbers from
          <select value={bot} onChange={(e) => setBot(Number(e.target.value))} className={select} disabled={running}>
            {BOTS.map((b) => (
              <option key={b} value={b}>
                Bot {b}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Name (optional)
          <input value={label} onChange={(e) => setLabel(e.target.value)} className={clsx(select, "w-40")} placeholder="e.g. Nifty 50, 5-min" maxLength={60} />
        </label>
        {running ? (
          <button type="button" onClick={stop} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-rose-600/80 px-3 text-xs font-semibold text-white hover:bg-rose-600">
            <Square size={13} /> Stop
          </button>
        ) : (
          <button type="button" onClick={run} disabled={busy} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-accentSky px-3 text-xs font-semibold text-slate-900 hover:bg-sky-300 disabled:opacity-50">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Read the days
          </button>
        )}
        {running && current && (
          <span className="inline-flex items-center gap-1.5 text-slate-300">
            <Loader2 size={13} className="animate-spin" /> {current.done} of {current.total} stocks, {current.trades.toLocaleString("en-IN")} trades so far
          </span>
        )}
      </div>
      {note && <p className={clsx("text-xs", note.ok ? "text-accentSky" : "text-amber-300")}>{note.text}</p>}

      {/* Runs */}
      {view && view.jobs.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Runs:</span>
          {view.jobs.map((j) => (
            <span key={j.id} className={clsx("inline-flex items-center gap-1 rounded-md border px-2 py-1", openId === j.id ? "border-accentSky text-accentSky" : "border-slate-700 text-slate-300")}>
              <button type="button" onClick={() => j.status !== "RUNNING" && setOpenId(j.id)} className="hover:underline" title={`${j.symbols.length} stocks, Bot ${j.bot}, ${j.trades} trades, ${j.status}`}>
                {j.label || `${j.symbols.length} stocks`} · {j.first === j.last ? j.first : `${j.first} → ${j.last}`} · Bot {j.bot}
                {j.status !== "DONE" && <span className="ml-1 text-slate-500">({j.status.toLowerCase()})</span>}
              </button>
              {j.status !== "RUNNING" && (
                <button type="button" onClick={() => remove(j.id)} className="text-slate-500 hover:text-rose-300" aria-label="Delete this run">
                  <Trash2 size={12} />
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {job && rules && (
        <>
          {job.errors.length > 0 && (
            <p className="text-xs text-amber-300">
              {job.errors.length} stock(s) could not be read: {job.errors.slice(0, 6).map((e) => `${e.symbol} (${e.error})`).join("; ")}
              {job.errors.length > 6 ? " …" : ""}
            </p>
          )}

          {/* Rules */}
          <div className="grid gap-3 lg:grid-cols-[1fr_1fr_auto]">
            <RuleBox title="Checks before entering" hint="A trade is taken only when every ticked check agrees at the signal candle." rules={rules.entries} picked={choice.entries} onToggle={(n) => toggle("entries", n)} onAll={() => setAll("entries", rules.entries.map((r) => r.name))} onNone={() => setAll("entries", [])} />
            <RuleBox title="Exits" hint="The trade ends at the earliest ticked exit, the stop, a target or the square-off." rules={rules.exits} picked={choice.exits} onToggle={(n) => toggle("exits", n)} onAll={() => setAll("exits", rules.exits.map((r) => r.name))} onNone={() => setAll("exits", [])} />
            <div className="space-y-3 rounded-lg border border-slate-800 bg-base/60 p-3 text-xs">
              <div>
                <div className="mb-1 font-semibold text-slate-200">Stop</div>
                {rules.stops.map((r) => (
                  <label key={r.name} className="flex items-center gap-2 py-0.5 text-slate-300">
                    <input type="radio" name="lab-stop" checked={choice.stop === r.name} onChange={() => change({ ...choice, stop: r.name as StrategyLabStop })} />
                    {r.label}
                  </label>
                ))}
              </div>
              <div>
                <div className="mb-1 font-semibold text-slate-200">Targets</div>
                {rules.targets.map((r) => (
                  <label key={r.name} className="flex items-center gap-2 py-0.5 text-slate-300">
                    <input type="checkbox" checked={choice.targets.includes(r.name)} onChange={() => toggle("targets", r.name)} />
                    {r.label}
                  </label>
                ))}
              </div>
              <div className="space-y-1">
                <div className="font-semibold text-slate-200">Enter between</div>
                <div className="flex items-center gap-1">
                  <select value={choice.entry_from} onChange={(e) => change({ ...choice, entry_from: e.target.value })} className={select}>
                    {TIMES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                  <span className="text-slate-500">and</span>
                  <select value={choice.entry_to} onChange={(e) => change({ ...choice, entry_to: e.target.value })} className={select}>
                    {TIMES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </div>
                <label className="flex items-center gap-2 text-slate-300">
                  Qty per trade
                  <input
                    type="number"
                    min={0}
                    value={choice.qty ?? 0}
                    onChange={(e) => change({ ...choice, qty: Math.max(0, Number(e.target.value) || 0) })}
                    className={clsx(select, "w-20")}
                    title="0 = the bot's own quantity"
                  />
                </label>
              </div>
            </div>
          </div>

          {/* Totals */}
          {result && (
            <div className={clsx("space-y-3 transition-opacity", evaluating && "opacity-60")}>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
                <Tile label="P&L before charges" value={money(result.totals.gross, true)} tone={result.totals.gross} />
                <Tile label="After charges" value={money(result.totals.net, true)} tone={result.totals.net} />
                <Tile label="Trades" value={`${result.totals.trades.toLocaleString("en-IN")} of ${result.base_trades.toLocaleString("en-IN")}`} />
                <Tile label="Winning trades" value={pct(result.totals.win_pct)} />
                <Tile label="Profit factor" value={result.totals.profit_factor == null ? "—" : num(result.totals.profit_factor)} />
                <Tile label="Green days" value={`${result.totals.green_days} of ${result.totals.days} (${pct(result.totals.green_pct, 0)})`} />
                <Tile label="Per trade" value={money(result.totals.per_trade, true)} tone={result.totals.per_trade} />
                <Tile label="Biggest drop" value={money(result.totals.drawdown)} tone={result.totals.drawdown} />
              </div>
              <p className="text-xs text-slate-500">
                Skipped: {result.skipped.refused} refused by a check, {result.skipped.in_position} while already in a trade, {result.skipped.outside_window} outside the entry hours
                {result.skipped.stock_cap ? `, ${result.skipped.stock_cap} over the per-stock cap` : ""}. Best trade {money(result.totals.best_trade, true)}, worst{" "}
                {money(result.totals.worst_trade, true)}; best day {money(result.totals.best_day, true)}, worst {money(result.totals.worst_day, true)}.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" onClick={recommend} disabled={recBusy} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-accentSky ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50">
                  {recBusy ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />} What does each rule add?
                </button>
                <button type="button" onClick={backtest} disabled={busy} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10 disabled:opacity-50">
                  <Play size={13} /> Backtest this in the replay
                </button>
                <button type="button" onClick={exportCsv} disabled={!result.trades?.length} className="min-h-9 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-slate-800 disabled:opacity-50">
                  CSV
                </button>
              </div>

              {rec && (
                <div className="space-y-2 rounded-lg border border-slate-800 bg-base/60 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="font-semibold text-slate-200">What each rule adds on top of the choice above {recStale && <span className="font-normal text-amber-300">(the choice changed since; press the button again)</span>}</div>
                    {rec.best.steps.length > 0 ? (
                      <button type="button" onClick={useBest} className="rounded-md px-2 py-1 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10">
                        Use the best found: {money(rec.best.totals.net, true)} after charges, {rec.best.totals.trades} trades
                      </button>
                    ) : (
                      <span className="text-slate-500">No single change beats this choice.</span>
                    )}
                  </div>
                  {rec.best.steps.length > 0 && (
                    <p className="text-xs text-slate-500">
                      Found by changing one rule at a time: {rec.best.steps.map((s) => `${s.kind === "stop" ? "stop → " : ""}${ruleLabel(rules, s.kind, s.name)} (${money(s.net_before, true)} → ${money(s.net_after, true)})`).join("; ")}.
                    </p>
                  )}
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-xs">
                      <thead className="text-left text-slate-500">
                        <tr>
                          <th className="px-2 py-1">Rule</th>
                          <th className="px-2 py-1">Now</th>
                          <th className="px-2 py-1 text-right">P&L if changed</th>
                          <th className="px-2 py-1 text-right">Adds (after charges)</th>
                          <th className="px-2 py-1 text-right">Trades</th>
                          <th className="px-2 py-1 text-right">Win %</th>
                          <th className="px-2 py-1 text-right">Profit factor</th>
                          <th className="px-2 py-1 text-right">Green days</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rec.rows.map((r) => (
                          <tr key={`${r.kind}:${r.name}`} className="border-t border-slate-800/60">
                            <td className="px-2 py-1 text-slate-200">
                              <span className="mr-1 rounded px-1 text-[10px] uppercase text-slate-500 ring-1 ring-inset ring-slate-500/40">{r.kind}</span>
                              {r.label}
                            </td>
                            <td className="px-2 py-1 text-slate-400">{r.kind === "stop" ? "—" : r.on ? "on → off" : "off → on"}</td>
                            <td className={clsx("px-2 py-1 text-right tabular-nums", r.net_changed >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.net_changed, true)}</td>
                            <td className={clsx("px-2 py-1 text-right font-semibold tabular-nums", r.delta_net > 0 ? "text-emerald-300" : r.delta_net < 0 ? "text-rose-300" : "text-slate-400")}>{money(r.delta_net, true)}</td>
                            <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades_changed}</td>
                            <td className="px-2 py-1 text-right tabular-nums text-slate-300">{pct(r.win_pct_changed)}</td>
                            <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.profit_factor_changed == null ? "—" : num(r.profit_factor_changed)}</td>
                            <td className="px-2 py-1 text-right tabular-nums text-slate-300">{pct(r.green_pct_changed, 0)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-xs text-slate-500">
                    &quot;Adds&quot; is the P&amp;L with only that rule changed minus the P&amp;L now. A rule that adds money by removing trades is only as good as the trades it leaves: check the trade count and the green days too.
                  </p>
                </div>
              )}

              <Equity days={result.per_day} />

              <div className="flex flex-wrap gap-1 text-xs">
                {(["stocks", "days", "reasons", "trades"] as const).map((k) => (
                  <button key={k} type="button" onClick={() => setShow(k)} className={clsx("rounded-md px-2 py-1", show === k ? "bg-slate-700 text-slate-100" : "text-slate-400 hover:text-slate-200")}>
                    {k === "stocks" ? "By stock" : k === "days" ? "By day" : k === "reasons" ? "By exit reason" : "Trades"}
                  </button>
                ))}
              </div>
              {show === "stocks" && <StockTable rows={result.per_stock} />}
              {show === "days" && <DayTable rows={result.per_day} />}
              {show === "reasons" && <ReasonTable rows={result.by_reason} />}
              {show === "trades" && <TradeTable rows={result.trades ?? []} cut={result.trades_cut ?? 0} />}
            </div>
          )}
        </>
      )}
      {!job && !running && view && view.jobs.length === 0 && (
        <p className="text-xs text-slate-500">No run yet. Pick the stocks and the days and press &quot;Read the days&quot;.</p>
      )}
    </section>
  );
}

function ruleLabel(rules: StrategyLabRules, kind: string, name: string): string {
  const list = kind === "entry" ? rules.entries : kind === "exit" ? rules.exits : kind === "target" ? rules.targets : rules.stops;
  return list.find((r) => r.name === name)?.label ?? name;
}

function RuleBox({
  title,
  hint,
  rules,
  picked,
  onToggle,
  onAll,
  onNone,
}: {
  title: string;
  hint: string;
  rules: { name: string; label: string }[];
  picked: string[];
  onToggle: (name: string) => void;
  onAll: () => void;
  onNone: () => void;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-base/60 p-3 text-xs">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="font-semibold text-slate-200">
          {title} <span className="font-normal text-slate-500">({picked.length} of {rules.length})</span>
        </div>
        <div className="flex gap-2 text-accentSky">
          <button type="button" onClick={onAll} className="hover:underline">
            Select all
          </button>
          <button type="button" onClick={onNone} className="hover:underline">
            Clear
          </button>
        </div>
      </div>
      <p className="mb-2 text-slate-500">{hint}</p>
      <div className="grid gap-x-3 sm:grid-cols-2">
        {rules.map((r) => (
          <label key={r.name} className="flex items-center gap-2 py-0.5 text-slate-300">
            <input type="checkbox" checked={picked.includes(r.name)} onChange={() => onToggle(r.name)} />
            {r.label}
          </label>
        ))}
      </div>
    </div>
  );
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-base/60 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={clsx("text-sm font-semibold tabular-nums", tone == null ? "text-slate-100" : tone > 0 ? "text-emerald-300" : tone < 0 ? "text-rose-300" : "text-slate-100")}>{value}</div>
    </div>
  );
}

/** The running total of P&L before charges, day by day, as plain SVG. */
function Equity({ days }: { days: StrategyLabResult["per_day"] }) {
  const W = 720;
  const H = 120;
  const pad = { l: 8, r: 8, t: 8, b: 18 };
  const points = useMemo(() => {
    if (!days.length) return [];
    const values = [0, ...days.map((d) => d.cum_gross)];
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = hi - lo || 1;
    const x = (i: number) => pad.l + (i / Math.max(1, days.length)) * (W - pad.l - pad.r);
    const y = (v: number) => pad.t + (1 - (v - lo) / span) * (H - pad.t - pad.b);
    return values.map((v, i) => ({ x: x(i), y: y(v), v, day: i === 0 ? "" : days[i - 1].day }));
  }, [days]);
  if (points.length < 2) return null;
  const zero = points[0].y;
  const path = points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <div className="rounded-lg border border-slate-800 bg-base/60 p-2">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">Running total before charges, day by day</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-28 w-full" role="img" aria-label="Running total of P&L by day">
        <line x1={pad.l} x2={W - pad.r} y1={zero} y2={zero} stroke="rgb(148 163 184 / 0.4)" strokeDasharray="3 3" />
        <path d={path} fill="none" stroke={last.v >= 0 ? "rgb(52 211 153)" : "rgb(251 113 133)"} strokeWidth={1.8} />
        {points.slice(1).map((p) => (
          <circle key={p.day} cx={p.x} cy={p.y} r={2.2} fill={p.v >= 0 ? "rgb(52 211 153)" : "rgb(251 113 133)"}>
            <title>
              {p.day}: {money(p.v, true)}
            </title>
          </circle>
        ))}
        {/* The day labels take the text colour of the card (light and dark themes); one day shows one label. */}
        <text x={points[1].x} y={H - 4} fontSize={9} fill="currentColor" className="text-slate-500">
          {days[0].day}
        </text>
        {days.length > 1 ? (
          <text x={last.x} y={H - 4} fontSize={9} fill="currentColor" className="text-slate-500" textAnchor="end">
            {days[days.length - 1].day}
          </text>
        ) : null}
      </svg>
    </div>
  );
}

type StockKey = "symbol" | "trades" | "win_pct" | "gross" | "net" | "days" | "green_days" | "per_trade";

function StockTable({ rows }: { rows: StrategyLabResult["per_stock"] }) {
  const { sorted, sort, onSort } = useSort<StrategyLabResult["per_stock"][number], StockKey>(rows, (r, k) => r[k]);
  if (!rows.length) return <p className="text-xs text-slate-500">No trades with this choice.</p>;
  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-slate-800">
      <table className="min-w-full text-xs">
        <thead className="sticky top-0 bg-card text-left text-slate-500">
          <tr>
            <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text />
            <SortTh label="Trades" k="trades" sort={sort} onSort={onSort} num />
            <SortTh label="Win %" k="win_pct" sort={sort} onSort={onSort} num />
            <SortTh label="Before charges" k="gross" sort={sort} onSort={onSort} num />
            <SortTh label="After charges" k="net" sort={sort} onSort={onSort} num />
            <SortTh label="Per trade" k="per_trade" sort={sort} onSort={onSort} num />
            <SortTh label="Days" k="days" sort={sort} onSort={onSort} num />
            <SortTh label="Green days" k="green_days" sort={sort} onSort={onSort} num />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.symbol} className="border-t border-slate-800/60">
              <td className="px-2 py-1 font-semibold text-slate-200">{r.symbol}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{pct(r.win_pct)}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.gross, true)}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.net >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.net, true)}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{money(r.per_trade, true)}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.days}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.green_days}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DayTable({ rows }: { rows: StrategyLabResult["per_day"] }) {
  if (!rows.length) return <p className="text-xs text-slate-500">No trades with this choice.</p>;
  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-slate-800">
      <table className="min-w-full text-xs">
        <thead className="sticky top-0 bg-card text-left text-slate-500">
          <tr>
            <th className="px-2 py-1">Day</th>
            <th className="px-2 py-1 text-right">Trades</th>
            <th className="px-2 py-1 text-right">Before charges</th>
            <th className="px-2 py-1 text-right">After charges</th>
            <th className="px-2 py-1 text-right">Running total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.day} className="border-t border-slate-800/60">
              <td className="px-2 py-1 text-slate-200">{r.day}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.gross, true)}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.net >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.net, true)}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.cum_gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.cum_gross, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReasonTable({ rows }: { rows: StrategyLabResult["by_reason"] }) {
  if (!rows.length) return <p className="text-xs text-slate-500">No trades with this choice.</p>;
  return (
    <div className="overflow-auto rounded-lg border border-slate-800">
      <table className="min-w-full text-xs">
        <thead className="bg-card text-left text-slate-500">
          <tr>
            <th className="px-2 py-1">How the trade ended</th>
            <th className="px-2 py-1 text-right">Trades</th>
            <th className="px-2 py-1 text-right">Winning</th>
            <th className="px-2 py-1 text-right">Before charges</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.reason} className="border-t border-slate-800/60">
              <td className="px-2 py-1 text-slate-200">{r.reason}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades ? pct((r.wins / r.trades) * 100, 0) : "—"}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.gross, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TradeTable({ rows, cut }: { rows: StrategyLabResult["trades"] & {}; cut: number }) {
  if (!rows.length) return <p className="text-xs text-slate-500">No trades with this choice.</p>;
  return (
    <div className="space-y-1">
      {cut > 0 && <p className="text-xs text-slate-500">Showing the last {rows.length.toLocaleString("en-IN")} trades; {cut.toLocaleString("en-IN")} earlier ones are in the totals (the CSV has these too).</p>}
      <div className="max-h-96 overflow-auto rounded-lg border border-slate-800">
        <table className="min-w-full text-xs">
          <thead className="sticky top-0 bg-card text-left text-slate-500">
            <tr>
              <th className="px-2 py-1">Day</th>
              <th className="px-2 py-1">Stock</th>
              <th className="px-2 py-1">Side</th>
              <th className="px-2 py-1">In</th>
              <th className="px-2 py-1">Out</th>
              <th className="px-2 py-1">Why</th>
              <th className="px-2 py-1 text-right">Qty</th>
              <th className="px-2 py-1 text-right">Before charges</th>
              <th className="px-2 py-1 text-right">After</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t, i) => (
              <tr key={`${t.symbol}-${t.entry_ts}-${i}`} className="border-t border-slate-800/60">
                <td className="px-2 py-1 text-slate-400">{t.day}</td>
                <td className="px-2 py-1 font-semibold text-slate-200">{t.symbol}</td>
                <td className={clsx("px-2 py-1", t.side === "LONG" ? "text-emerald-300" : "text-rose-300")}>{t.side}</td>
                <td className="px-2 py-1 tabular-nums text-slate-300">
                  {hhmm(t.entry_ts)} @ {t.entry_price.toFixed(2)}
                </td>
                <td className="px-2 py-1 tabular-nums text-slate-300">
                  {hhmm(t.exit_ts)} @ {t.exit_price.toFixed(2)}
                </td>
                <td className="px-2 py-1 text-slate-400">{t.reason}</td>
                <td className="px-2 py-1 text-right tabular-nums text-slate-300">{t.qty}</td>
                <td className={clsx("px-2 py-1 text-right tabular-nums", t.gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(t.gross, true)}</td>
                <td className={clsx("px-2 py-1 text-right tabular-nums", t.net >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(t.net, true)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
