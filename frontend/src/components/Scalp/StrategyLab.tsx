"use client";

import clsx from "clsx";
import { Check, FlaskConical, Loader2, Play, Square, Trash2, Wand2 } from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { money, num, pct } from "@/lib/format";
import {
  replayActive,
  smaApi,
  type StrategyLabCapital,
  type StrategyLabChoice,
  type StrategyLabJob,
  type StrategyLabJudge,
  type StrategyLabRecommend,
  type StrategyLabResult,
  type StrategyLabRules,
  type StrategyLabStop,
  type StrategyLabView,
} from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";
import { NewTag } from "@/components/ui/NewTag";
import { GuideBar } from "./GuideBar";

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
const LAB_CANDLES = [1, 2, 3, 5, 10, 15];
const TIMES = ["09:20", "09:30", "09:45", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30"];
const EMPTY: StrategyLabChoice = { entries: [], exits: ["cross"], stop: "ATR", targets: [], entry_from: "09:20", entry_to: "15:30", qty: 0, symbols: [], margin_pct: 20 };
/** Sent as the stock list when every stock is unticked: matches no stock, so nothing is traded. */
const NO_STOCK = "-";

const sameChoice = (a: StrategyLabChoice, b: StrategyLabChoice) =>
  JSON.stringify([a.entries, a.exits, a.stop, a.targets, a.entry_from, a.entry_to, a.qty, a.symbols ?? [], a.margin_pct ?? 20]) ===
  JSON.stringify([b.entries, b.exits, b.stop, b.targets, b.entry_from, b.entry_to, b.qty, b.symbols ?? [], b.margin_pct ?? 20]);

const hhmm = (ts: number) => new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

export function StrategyLab({ armed, onArmMany }: { armed: Map<string, string[]>; onArmMany?: (symbols: string[]) => void }) {
  const [lists, setLists] = useScanLists("sma.lab.lists", ["armed"]);
  const [days, setDays] = useState(10);
  const [bot, setBot] = useState(1);
  const [label, setLabel] = useState("");
  const [minutes, setMinutes] = useState(0);
  const [view, setView] = useState<StrategyLabView | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [job, setJob] = useState<StrategyLabJob | null>(null);
  const [choice, setChoice] = useState<StrategyLabChoice>(EMPTY);
  const [result, setResult] = useState<StrategyLabResult | null>(null);
  const [rec, setRec] = useState<StrategyLabRecommend | null>(null);
  const [judge, setJudge] = useState<StrategyLabJudge>("ROC");
  const [recFor, setRecFor] = useState<StrategyLabChoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [recBusy, setRecBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  // "Backtest this in the replay": every other strategy setting back to its default first (off = only the switches).
  const [resetFirst, setResetFirst] = useState(false);
  const [applyBot, setApplyBot] = useState(0); // 0 = the run's own bot
  const [show, setShow] = useState<"pick" | "stocks" | "days" | "trades" | "reasons">("stocks");
  // Every stock's P&L with the same rules but all stocks in (the Stocks tab shows it for unticked ones too).
  const [allStocks, setAllStocks] = useState<StrategyLabResult["per_stock"] | null>(null);
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
        setAllStocks(v.result.per_stock);
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
        const filtered = (next.symbols ?? []).length > 0;
        const [v, all] = await Promise.all([
          smaApi.strategyLabEvaluate(openId, next),
          filtered ? smaApi.strategyLabEvaluate(openId, { ...next, symbols: [] }) : Promise.resolve(null),
        ]);
        if (mine === latest.current) {
          setResult(v);
          setAllStocks(all ? all.per_stock : v.per_stock);
        }
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
      const v = await smaApi.strategyLabStart({ symbols, date: range[0], end_date: range[range.length - 1], bot, label, minutes: minutes || undefined });
      setView(v);
      setOpenId(null);
      lastDone.current = null;
      setNote({
        ok: true,
        text: `Reading ${symbols.length} stock(s), ${range[0]} → ${range[range.length - 1]}, with Bot ${bot}'s numbers${minutes ? ` on ${minutes}-minute candles` : ""}. The first read of a day downloads its candles (a second or two a stock); days read before come from the cache.`,
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
      setRec(await smaApi.strategyLabRecommend(openId, choice, judge));
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

  /** Save this choice's switches on a bot, no replay: the bot then trades them on its next signals (PAPER or LIVE as it
   * is). A LIVE bot asks first; a backtest playing on that bot is stopped only when the person says so. */
  const applyToBot = async () => {
    if (!job) return;
    const target = applyBot || job.bot;
    const what = resetFirst
      ? `Reset Bot ${target}'s other strategy settings to their defaults, then put on these switches and the numbers the lab read (SMAs, stop, filter ranges, quantity, candle).`
      : `Save these switches on Bot ${target} (its numbers and every other setting stay as they are).`;
    const text = `${what}\n\nNo replay starts and no order is placed: the bot uses them from its next signal on its armed stocks. The Trade list, PAPER/LIVE, name and per-stock settings stay; the old settings stay in Settings → Strategy → Saved strategies & history.`;
    if (!window.confirm(text)) return;
    setBusy(true);
    const body = { ...choice, reset: resetFirst, to_bot: target };
    const send = (extra: { confirm_live?: boolean; stop_replay?: boolean }) => smaApi.strategyLabApply(job.id, { ...body, ...extra });
    try {
      let extra: { confirm_live?: boolean; stop_replay?: boolean } = {};
      for (let i = 0; i < 3; i++) {
        try {
          await send(extra);
          setNote({
            ok: true,
            text: `${resetFirst ? "Reset and saved" : "Saved"} on Bot ${target}. No replay started. Arm the stocks (the Stocks tab's Arm ticked) and Start the bot in the terminal; it trades them on its next signals.`,
          });
          return;
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : "";
          if (/is LIVE/i.test(msg) && !extra.confirm_live) {
            if (!window.confirm(`Bot ${target} is LIVE: its next trades use real money with these settings. Change its settings?`)) return;
            extra = { ...extra, confirm_live: true };
          } else if (/backtest is playing/i.test(msg) && !extra.stop_replay) {
            if (!window.confirm(`Bot ${target}'s backtest is playing and reads its settings each day. Stop that backtest and save?`)) return;
            extra = { ...extra, stop_replay: true };
          } else throw e;
        }
      }
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not save on the bot" });
    } finally {
      setBusy(false);
    }
  };

  /** Save this choice's switches on the bot and start the real replay on the same stocks and days. */
  const backtest = async () => {
    if (!job || !result) return;
    const target = job.bot as 1 | 2 | 3 | 4;
    const picked = (choice.symbols ?? []).filter((x) => x !== NO_STOCK);
    const stocks = picked.length ? picked : job.symbols;
    const what = resetFirst
      ? `Reset Bot ${target}'s other strategy settings to their defaults, then put on these switches and the numbers the lab read (SMAs, stop, filter ranges, quantity, candle). The Trade list, PAPER/LIVE, name and per-stock settings stay.`
      : `Save these switches on Bot ${target} (its numbers and every other setting stay as they are).`;
    const text = `${what}\n\nThen replay ${stocks.length} stock(s)${picked.length ? " (the ticked ones)" : ""}, ${job.first} → ${job.last}, through the real bot? Practice money; no order is sent. The old settings stay in Settings → Strategy → Saved strategies & history.`;
    if (!window.confirm(text)) return;
    setBusy(true);
    try {
      const current = (await smaApi.replayAll().catch(() => null))?.[String(target)] ?? null;
      const playing = Boolean(current && replayActive(current) && current.status !== "FINISHED");
      if (playing && !window.confirm(`Bot ${target}'s replay is already running. Stop it and start this one instead?`)) return;
      await smaApi.strategyLabApply(job.id, { ...choice, reset: resetFirst, stop_replay: playing });
      await smaApi.replayStart(job.first, "09:15", 300, job.last, stocks, target, false, true);
      setNote({
        ok: true,
        text: `${resetFirst ? "Reset and saved" : "Saved"} on Bot ${target} and started the replay. Its trades build up in the terminal's Backtests tab; compare them with the lab's ${result.totals.trades} trade(s) and ${money(result.totals.gross, true)}.`,
      });
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
          <GuideBar lesson="scalp-strategy-lab" terms={["cross", "backtest", "stop", "trail", "target", "vwap", "rsi", "adx", "bollinger", "supertrend", "ema", "macd", "cvd", "slope", "pnl_before", "profit_factor", "drawdown"]} className="mt-2" />
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
          Candle
          <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className={select} disabled={running} title="The candle every stock is read on. Bot's own = the bot's candle setting.">
            <option value={0}>Bot&apos;s own</option>
            {LAB_CANDLES.map((m) => (
              <option key={m} value={m}>
                {m} minute{m === 1 ? "" : "s"}
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
                {j.minutes ? ` · ${j.minutes}-min` : ""}
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
          <BestFound
            rec={rec}
            stale={Boolean(recStale)}
            judge={judge}
            onJudge={(j) => setJudge(j)}
            busy={recBusy}
            onFind={recommend}
            onUse={useBest}
          />
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
                <label className="flex items-center gap-2 text-slate-300" title="The intraday margin blocked per trade, % of its value (20% = 5x, Groww MIS). Only changes the capital figures.">
                  Margin %
                  <input
                    type="number"
                    min={1}
                    max={100}
                    value={choice.margin_pct ?? 20}
                    onChange={(e) => change({ ...choice, margin_pct: Math.min(100, Math.max(1, Number(e.target.value) || 20)) })}
                    className={clsx(select, "w-16")}
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
              {result.capital && <CapitalRow cap={result.capital} />}
              <p className="text-xs text-slate-500">
                Skipped: {result.skipped.refused} refused by a check, {result.skipped.in_position} while already in a trade, {result.skipped.outside_window} outside the entry hours
                {result.skipped.stock_cap ? `, ${result.skipped.stock_cap} over the per-stock cap` : ""}
                {result.skipped.unticked ? `, ${result.skipped.unticked.toLocaleString("en-IN")} on unticked stocks` : ""}. Best trade {money(result.totals.best_trade, true)}, worst{" "}
                {money(result.totals.worst_trade, true)}; best day {money(result.totals.best_day, true)}, worst {money(result.totals.worst_day, true)}.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" onClick={recommend} disabled={recBusy} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-accentSky ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50">
                  {recBusy ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />} What does each rule add?
                </button>
                <button type="button" onClick={backtest} disabled={busy} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10 disabled:opacity-50">
                  <Play size={13} /> Backtest this in the replay
                </button>
                <span className="inline-flex items-center gap-1">
                  <select
                    value={applyBot || job?.bot || 1}
                    onChange={(e) => setApplyBot(Number(e.target.value))}
                    className={select}
                    disabled={busy}
                    aria-label="Bot to apply the lab's settings to"
                  >
                    {[1, 2, 3, 4, 5].map((b) => (
                      <option key={b} value={b}>
                        Bot {b}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={applyToBot}
                    disabled={busy}
                    title="Save these switches on the picked bot without a replay. The Reset tick box applies too. Nothing is armed and no order is placed."
                    className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-accentSky ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
                  >
                    <Check size={13} /> Apply to bot <NewTag id="lab-apply-bot" link={false} />
                  </button>
                </span>
                <label
                  className="inline-flex items-center gap-1.5 text-xs text-slate-300"
                  title="Off: only the lab's switches change on the bot; its other settings (entry mode, pullbacks, smart stop, hand-offs…) stay and play in the replay too. On: every other strategy setting goes back to its default first and the lab's numbers are put on, so the replay trades only what the lab tested. The Trade list, PAPER/LIVE, name and per-stock settings stay."
                >
                  <input type="checkbox" checked={resetFirst} onChange={(e) => setResetFirst(e.target.checked)} disabled={busy} />
                  Reset the bot&apos;s other settings first <NewTag id="lab-replay-reset" />
                </label>
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
                        {rec.best.totals.roc != null ? `, ${pct(rec.best.totals.roc)} on ${money(rec.best.totals.peak_margin ?? 0)} capital` : ""}
                      </button>
                    ) : (
                      <span className="text-slate-500">No single change beats this choice.</span>
                    )}
                  </div>
                  {rec.best.steps.length > 0 && (
                    <p className="text-xs text-slate-500">
                      Found by changing one rule at a time{rec.judge === "ROC" ? " (judged by return on capital)" : " (judged by P&L after charges)"}:{" "}
                      {rec.best.steps
                        .map((s) =>
                          rec.judge === "ROC" && s.roc_after != null
                            ? `${s.kind === "stop" ? "stop → " : ""}${ruleLabel(rules, s.kind, s.name)} (${s.roc_before == null ? "—" : pct(s.roc_before)} → ${pct(s.roc_after)}, ${money(s.net_after, true)})`
                            : `${s.kind === "stop" ? "stop → " : ""}${ruleLabel(rules, s.kind, s.name)} (${money(s.net_before, true)} → ${money(s.net_after, true)})`
                        )
                        .join("; ")}
                      .
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
                          <th className="px-2 py-1 text-right" title="The most margin in use at one moment with this change (the Margin % box)">Capital</th>
                          <th className="px-2 py-1 text-right" title="P&L after charges ÷ that capital">Return on capital</th>
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
                            <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.capital_changed == null ? "—" : money(r.capital_changed)}</td>
                            <td className={clsx("px-2 py-1 text-right tabular-nums", r.roc_changed == null ? "text-slate-500" : r.roc_changed >= 0 ? "text-emerald-300" : "text-rose-300")}>
                              {r.roc_changed == null ? "—" : pct(r.roc_changed)}
                            </td>
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
                {(["pick", "stocks", "days", "reasons", "trades"] as const).map((k) => (
                  <button key={k} type="button" onClick={() => setShow(k)} className={clsx("inline-flex items-center gap-1 rounded-md px-2 py-1", show === k ? "bg-slate-700 text-slate-100" : "text-slate-400 hover:text-slate-200")}>
                    {k === "pick"
                      ? `Stocks (${stockCount(choice, result.universe ?? [])} of ${(result.universe ?? []).length} ticked)`
                      : k === "stocks"
                        ? "By stock"
                        : k === "days"
                          ? "By day"
                          : k === "reasons"
                            ? "By exit reason"
                            : "Trades"}
                    {k === "pick" ? <NewTag id="lab-stocks-capital" link={false} /> : null}
                  </button>
                ))}
              </div>
              {show === "pick" && (
                <StockPicker
                  universe={result.universe ?? []}
                  perStock={allStocks ?? result.per_stock}
                  picked={choice.symbols ?? []}
                  armed={armed}
                  onChange={(symbols) => change({ ...choice, symbols })}
                  onArmMany={onArmMany}
                />
              )}
              {show === "stocks" && <StockTable rows={result.per_stock} pairs={result.per_stock_day ?? []} />}
              {show === "days" && <DayTable rows={result.per_day} pairs={result.per_stock_day ?? []} />}
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

/** The best set of rules found, shown next to the rule ticks: its P&L after charges, trades, the capital it needs
 * and the return on it, with "Use it" and a "Judge by" switch (P&L or return on capital). */
function BestFound({
  rec,
  stale,
  judge,
  onJudge,
  busy,
  onFind,
  onUse,
}: {
  rec: StrategyLabRecommend | null;
  stale: boolean;
  judge: StrategyLabJudge;
  onJudge: (j: StrategyLabJudge) => void;
  busy: boolean;
  onFind: () => void;
  onUse: () => void;
}) {
  const best = rec?.best;
  const now = rec?.now;
  const better = Boolean(best && best.steps.length);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs">
      <span className="inline-flex items-center gap-1 font-semibold text-slate-200">
        Best found <NewTag id="lab-best-capital" link={false} />
      </span>
      <label className="inline-flex items-center gap-1 text-slate-400">
        Judge by
        <select
          value={judge}
          onChange={(e) => onJudge(e.target.value as StrategyLabJudge)}
          className="rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
          title="Return on capital: P&L after charges ÷ the most margin in use at one moment, so a set that makes a little less with far less money can win. P&L: the most rupees after charges."
        >
          <option value="ROC">Return on capital</option>
          <option value="NET">P&amp;L after charges</option>
        </select>
      </label>
      {rec && best && now ? (
        <>
          <span className="text-slate-300">
            {better ? "Best" : "Now (nothing beats it)"}:{" "}
            <b className={clsx("tabular-nums", best.totals.net >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(best.totals.net, true)}</b> after charges ·{" "}
            {best.totals.trades} trades · capital <b className="tabular-nums text-slate-200">{money(best.totals.peak_margin ?? 0)}</b> · return{" "}
            <b className={clsx("tabular-nums", (best.totals.roc ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300")}>{best.totals.roc == null ? "—" : pct(best.totals.roc)}</b>
          </span>
          {better ? (
            <span className="text-slate-500">
              (now {money(now.net, true)}, {now.roc == null ? "—" : pct(now.roc)} on {money(now.peak_margin ?? 0)})
            </span>
          ) : null}
          {stale || rec.judge !== judge ? <span className="text-amber-300">The ticks or Judge by changed: find it again.</span> : null}
          {better && !stale && rec.judge === judge ? (
            <button type="button" onClick={onUse} className="rounded-md px-2 py-1 font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10">
              Use it
            </button>
          ) : null}
        </>
      ) : (
        <span className="text-slate-500">Not worked out yet for these ticks.</span>
      )}
      <button
        type="button"
        onClick={onFind}
        disabled={busy}
        className="inline-flex items-center gap-1 rounded-md px-2 py-1 font-semibold text-accentSky ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
      >
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />} {rec ? "Find again" : "Find the best"}
      </button>
    </div>
  );
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

type Pair = NonNullable<StrategyLabResult["per_stock_day"]>[number];

/** Rows that open: a set of open keys plus Open all / Close all. */
function useOpenRows() {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setOpen((was) => {
      const next = new Set(was);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  return { open, toggle, setAll: (keys: string[]) => setOpen(new Set(keys)) };
}

function OpenAllBar({ any, count, onOpen, onClose, what }: { any: boolean; count: number; onOpen: () => void; onClose: () => void; what: string }) {
  if (!any) return null;
  return (
    <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
      <span>
        Click a row to see its {what}. <NewTag id="lab-nested-rows" link={false} />
      </span>
      <button type="button" onClick={onOpen} className="rounded px-1.5 py-0.5 text-accentSky ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10">
        Open all ({count})
      </button>
      <button type="button" onClick={onClose} className="rounded px-1.5 py-0.5 text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-slate-800">
        Close all
      </button>
    </div>
  );
}

/** The inner rows under an opened day (its stocks) or stock (its days). */
function PairRows({ rows, label, cols }: { rows: Pair[]; label: (p: Pair) => string; cols: number }) {
  return (
    <tr className="bg-slate-900/40">
      <td colSpan={cols} className="px-2 py-1">
        <table className="ml-5 text-xs">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="px-2 py-0.5 font-normal">{label === pairDay ? "Day" : "Stock"}</th>
              <th className="px-2 py-0.5 text-right font-normal">Trades</th>
              <th className="px-2 py-0.5 text-right font-normal">Won</th>
              <th className="px-2 py-0.5 text-right font-normal">Before charges</th>
              <th className="px-2 py-0.5 text-right font-normal">After charges</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={`${p.symbol}:${p.day}`} className="border-t border-slate-800/40">
                <td className="px-2 py-0.5 text-slate-300">{label(p)}</td>
                <td className="px-2 py-0.5 text-right tabular-nums text-slate-400">{p.trades}</td>
                <td className="px-2 py-0.5 text-right tabular-nums text-slate-400">{p.wins}</td>
                <td className={clsx("px-2 py-0.5 text-right tabular-nums", p.gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(p.gross, true)}</td>
                <td className={clsx("px-2 py-0.5 text-right tabular-nums", p.net >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(p.net, true)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </td>
    </tr>
  );
}

const pairDay = (p: Pair) => p.day;
const pairStock = (p: Pair) => p.symbol;

function groupPairs(pairs: Pair[], key: (p: Pair) => string): Map<string, Pair[]> {
  const out = new Map<string, Pair[]>();
  for (const p of pairs) {
    const k = key(p);
    const list = out.get(k);
    if (list) list.push(p);
    else out.set(k, [p]);
  }
  return out;
}

type StockKey = "symbol" | "trades" | "win_pct" | "gross" | "net" | "days" | "green_days" | "per_trade";

function StockTable({ rows, pairs }: { rows: StrategyLabResult["per_stock"]; pairs: Pair[] }) {
  const { sorted, sort, onSort } = useSort<StrategyLabResult["per_stock"][number], StockKey>(rows, (r, k) => r[k]);
  const byStock = useMemo(() => {
    const m = groupPairs(pairs, pairStock);
    m.forEach((list) => list.sort((a, b) => a.day.localeCompare(b.day)));
    return m;
  }, [pairs]);
  const { open, toggle, setAll } = useOpenRows();
  if (!rows.length) return <p className="text-xs text-slate-500">No trades with this choice.</p>;
  return (
    <>
    <OpenAllBar any={byStock.size > 0} count={rows.length} what="days" onOpen={() => setAll(rows.map((r) => r.symbol))} onClose={() => setAll([])} />
    <div className="max-h-[32rem] overflow-auto rounded-lg border border-slate-800">
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
            <Fragment key={r.symbol}>
            <tr
              className={clsx("border-t border-slate-800/60", byStock.has(r.symbol) && "cursor-pointer hover:bg-slate-800/40")}
              onClick={() => byStock.has(r.symbol) && toggle(r.symbol)}
              aria-expanded={byStock.has(r.symbol) ? open.has(r.symbol) : undefined}
            >
              <td className="px-2 py-1 font-semibold text-slate-200">
                {byStock.has(r.symbol) ? <span className="mr-1 inline-block w-3 text-slate-500">{open.has(r.symbol) ? "▾" : "▸"}</span> : null}
                {r.symbol}
              </td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{pct(r.win_pct)}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.gross, true)}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.net >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.net, true)}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{money(r.per_trade, true)}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.days}</td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.green_days}</td>
            </tr>
            {open.has(r.symbol) && byStock.get(r.symbol) ? <PairRows rows={byStock.get(r.symbol) ?? []} label={pairDay} cols={8} /> : null}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
    </>
  );
}

function DayTable({ rows, pairs }: { rows: StrategyLabResult["per_day"]; pairs: Pair[] }) {
  const byDay = useMemo(() => {
    const m = groupPairs(pairs, pairDay);
    m.forEach((list) => list.sort((a, b) => b.gross - a.gross));
    return m;
  }, [pairs]);
  const { open, toggle, setAll } = useOpenRows();
  if (!rows.length) return <p className="text-xs text-slate-500">No trades with this choice.</p>;
  return (
    <>
    <OpenAllBar any={byDay.size > 0} count={rows.length} what="stocks" onOpen={() => setAll(rows.map((r) => r.day))} onClose={() => setAll([])} />
    <div className="max-h-[32rem] overflow-auto rounded-lg border border-slate-800">
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
            <Fragment key={r.day}>
            <tr
              className={clsx("border-t border-slate-800/60", byDay.has(r.day) && "cursor-pointer hover:bg-slate-800/40")}
              onClick={() => byDay.has(r.day) && toggle(r.day)}
              aria-expanded={byDay.has(r.day) ? open.has(r.day) : undefined}
            >
              <td className="px-2 py-1 text-slate-200">
                {byDay.has(r.day) ? <span className="mr-1 inline-block w-3 text-slate-500">{open.has(r.day) ? "▾" : "▸"}</span> : null}
                {r.day}
                {byDay.has(r.day) ? <span className="ml-1 text-slate-500">({byDay.get(r.day)?.length} stocks)</span> : null}
              </td>
              <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.gross, true)}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.net >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.net, true)}</td>
              <td className={clsx("px-2 py-1 text-right tabular-nums", r.cum_gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{money(r.cum_gross, true)}</td>
            </tr>
            {open.has(r.day) && byDay.get(r.day) ? <PairRows rows={byDay.get(r.day) ?? []} label={pairStock} cols={5} /> : null}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
    </>
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

/** How many of the run's stocks the choice keeps (empty list = all). */
function stockCount(choice: StrategyLabChoice, universe: { symbol: string }[]): number {
  const picked = (choice.symbols ?? []).filter((x) => x !== NO_STOCK);
  if (!(choice.symbols ?? []).length) return universe.length;
  return universe.filter((u) => picked.includes(u.symbol)).length;
}

/** The money the kept trades needed: the most in use at one moment, its margin and the return on it. */
function CapitalRow({ cap }: { cap: StrategyLabCapital }) {
  const when = cap.peak_at ? cap.peak_at.slice(0, 16).replace("T", " ") : null;
  const busiest = cap.per_day.length ? cap.per_day.reduce((a, b) => (b.peak_value > a.peak_value ? b : a)) : null;
  return (
    <div className="space-y-1">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        <Tile label="Capital needed (full value)" value={money(cap.peak_value)} />
        <Tile label={`Margin blocked (${num(cap.margin_pct, 0)}%)`} value={money(cap.peak_margin)} />
        <Tile label="Return on capital" value={cap.return_pct == null ? "—" : pct(cap.return_pct)} tone={cap.return_pct ?? undefined} />
        <Tile label="Return on margin" value={cap.return_on_margin_pct == null ? "—" : pct(cap.return_on_margin_pct)} tone={cap.return_on_margin_pct ?? undefined} />
        <Tile label="Largest trade" value={money(cap.max_trade_value)} />
        <Tile label="Average trade" value={money(cap.avg_trade_value)} />
      </div>
      <p className="text-xs text-slate-500">
        Capital = qty × entry price of the trades open at the same moment, at its most{when ? ` (${when}, ${cap.peak_open} trade(s) open)` : ""}
        {busiest ? `; busiest day ${busiest.day}` : ""}. Margin is what an intraday (MIS) account blocks for that: the money you need in the
        account. Returns are the P&amp;L before charges against it.
      </p>
    </div>
  );
}

type PickKey = "symbol" | "signals" | "trades" | "gross" | "net" | "win_pct" | "per_trade";

/**
 * The run's stocks with tick boxes: only ticked stocks count in every number above (re-worked at once), the replay
 * button replays only them, and Arm ticked arms them through the usual arm prompt. The P&L columns are each stock's
 * with the same rules and every stock in, so an unticked stock still shows what it would add.
 */
function StockPicker({
  universe,
  perStock,
  picked,
  armed,
  onChange,
  onArmMany,
}: {
  universe: { symbol: string; signals: number }[];
  perStock: StrategyLabResult["per_stock"];
  picked: string[];
  armed: Map<string, string[]>;
  onChange: (symbols: string[]) => void;
  onArmMany?: (symbols: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [topN, setTopN] = useState(20);
  const all = universe.map((u) => u.symbol);
  const ticked = useMemo(() => new Set(picked.length ? picked.filter((x) => x !== NO_STOCK) : all), [picked, all]);
  const stat = useMemo(() => new Map(perStock.map((r) => [r.symbol, r])), [perStock]);
  const rows = useMemo(
    () =>
      universe.map((u) => {
        const st = stat.get(u.symbol);
        return { symbol: u.symbol, signals: u.signals, trades: st?.trades ?? 0, gross: st?.gross ?? 0, net: st?.net ?? 0, win_pct: st?.win_pct ?? 0, per_trade: st?.per_trade ?? 0 };
      }),
    [universe, stat]
  );
  const shown = useMemo(() => {
    const q = query.trim().toUpperCase();
    return q ? rows.filter((r) => r.symbol.includes(q)) : rows;
  }, [rows, query]);
  const { sorted, sort, onSort } = useSort<(typeof rows)[number], PickKey>(shown, (r, k) => r[k]);
  const set = (next: Iterable<string>) => {
    const list = Array.from(new Set(next));
    // all ticked = no filter; none ticked = a list that matches nothing
    onChange(list.length === all.length ? [] : list.length ? list : [NO_STOCK]);
  };
  const flip = (symbol: string) => {
    const next = new Set(ticked);
    if (next.has(symbol)) next.delete(symbol);
    else next.add(symbol);
    set(next);
  };
  const byPnl = [...rows].sort((a, b) => b.gross - a.gross);
  const btn = "rounded-md px-2 py-1 ring-1 ring-inset ring-slate-700 hover:bg-white/5";
  const tickedList = all.filter((s) => ticked.has(s));
  const shownAll = shown.length > 0 && shown.every((r) => ticked.has(r.symbol));
  return (
    <div className="space-y-2 text-xs">
      <p className="text-slate-500">
        The run read {universe.length} stock(s). Only the ticked ones count in every number above, in &quot;Backtest this in the replay&quot; and in
        Arm ticked. The P&amp;L columns are each stock&apos;s with these rules, ticked or not.
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" className={btn} onClick={() => set(all)}>Tick all</button>
        <button type="button" className={btn} onClick={() => set([])}>Untick all</button>
        <button type="button" className={btn} onClick={() => set(all.filter((s) => !ticked.has(s)))}>Invert</button>
        <button type="button" className={btn} onClick={() => set(rows.filter((r) => r.gross > 0).map((r) => r.symbol))}>Only winners</button>
        <button type="button" className={btn} onClick={() => set(rows.filter((r) => r.gross < 0).map((r) => r.symbol))}>Only losers</button>
        <button type="button" className={btn} onClick={() => set(rows.filter((r) => r.trades > 0).map((r) => r.symbol))}>Only with trades</button>
        <button type="button" className={btn} onClick={() => set(rows.filter((r) => armed.has(r.symbol)).map((r) => r.symbol))}>Only armed now</button>
        <span className="inline-flex items-center gap-1">
          <button type="button" className={btn} onClick={() => set(byPnl.slice(0, topN).map((r) => r.symbol))}>Top</button>
          <select value={topN} onChange={(e) => setTopN(Number(e.target.value))} className="rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200" aria-label="How many top stocks">
            {[5, 10, 20, 30, 50, 100].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <span className="text-slate-500">by P&amp;L</span>
        </span>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search stock"
          className="ml-auto rounded border border-slate-700 bg-base px-2 py-1 text-xs text-slate-200"
        />
        {query ? (
          <button type="button" className={btn} onClick={() => set(shownAll ? tickedList.filter((s) => !shown.some((r) => r.symbol === s)) : [...tickedList, ...shown.map((r) => r.symbol)])}>
            {shownAll ? "Untick" : "Tick"} the {shown.length} found
          </button>
        ) : null}
        {onArmMany ? (
          <button
            type="button"
            disabled={!tickedList.length}
            onClick={() => onArmMany(tickedList)}
            className="rounded-md bg-emerald-600 px-2.5 py-1 font-semibold text-white disabled:opacity-40"
            title="Opens the usual Arm ticked prompt: pick the bot, quantity and stop. Nothing is ordered."
          >
            Arm ticked ({tickedList.length})
          </button>
        ) : null}
      </div>
      <div className="max-h-96 overflow-auto rounded-lg border border-slate-800">
        <table className="min-w-full text-xs">
          <thead className="sticky top-0 bg-card text-left text-slate-500">
            <tr>
              <th className="w-8 px-2 py-1">
                <input
                  type="checkbox"
                  aria-label="Tick every stock shown"
                  checked={shownAll}
                  onChange={() => set(shownAll ? tickedList.filter((s) => !shown.some((r) => r.symbol === s)) : [...tickedList, ...shown.map((r) => r.symbol)])}
                />
              </th>
              <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text />
              <SortTh label="Signals" k="signals" sort={sort} onSort={onSort} num title="SMA crosses the run found (before any check)" />
              <SortTh label="Trades" k="trades" sort={sort} onSort={onSort} num />
              <SortTh label="Win %" k="win_pct" sort={sort} onSort={onSort} num />
              <SortTh label="Before charges" k="gross" sort={sort} onSort={onSort} num />
              <SortTh label="After charges" k="net" sort={sort} onSort={onSort} num />
              <SortTh label="Per trade" k="per_trade" sort={sort} onSort={onSort} num />
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.symbol} onClick={() => flip(r.symbol)} className={clsx("cursor-pointer border-t border-slate-800/60 hover:bg-white/[0.03]", !ticked.has(r.symbol) && "opacity-50")}>
                <td className="px-2 py-1">
                  <input type="checkbox" checked={ticked.has(r.symbol)} onChange={() => flip(r.symbol)} onClick={(e) => e.stopPropagation()} aria-label={`Tick ${r.symbol}`} />
                </td>
                <td className="px-2 py-1 font-semibold text-slate-200">
                  {r.symbol}
                  {armed.has(r.symbol) ? <span className="ml-1 rounded px-1 text-[10px] font-normal text-emerald-300 ring-1 ring-inset ring-emerald-400/40">armed</span> : null}
                </td>
                <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.signals}</td>
                <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades}</td>
                <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades ? pct(r.win_pct) : "—"}</td>
                <td className={clsx("px-2 py-1 text-right tabular-nums", r.gross > 0 ? "text-emerald-300" : r.gross < 0 ? "text-rose-300" : "text-slate-400")}>{money(r.gross, true)}</td>
                <td className={clsx("px-2 py-1 text-right tabular-nums", r.net > 0 ? "text-emerald-300" : r.net < 0 ? "text-rose-300" : "text-slate-400")}>{money(r.net, true)}</td>
                <td className="px-2 py-1 text-right tabular-nums text-slate-300">{r.trades ? money(r.per_trade, true) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
