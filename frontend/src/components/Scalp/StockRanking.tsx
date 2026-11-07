"use client";

import clsx from "clsx";
import { ListOrdered, Loader2, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { GrowwLink } from "@/components/ui/growwLink";
import { smaApi, type StockRankJob, type StockRankRow, type StockRankVerdict, type StockRankView } from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";

const DAYS = [5, 10, 15, 20, 30];
const BOTS = [1, 2, 3, 4];
const BATCHES = [10, 15, 20, 25, 30, 40, 50];
const EARLIER = [
  { value: 0, label: "This test only" },
  { value: 30, label: "+ the bot's backtests of the last 30 days" },
  { value: 60, label: "+ the bot's backtests of the last 60 days" },
  { value: 90, label: "+ the bot's backtests of the last 90 days" },
];
const VERDICT: Record<StockRankVerdict, { text: string; cls: string; help: string }> = {
  STRONG: { text: "Strong", cls: "bg-emerald-500/15 text-emerald-300", help: "won at least 1.5× what it lost and was green on 55%+ of its days" },
  GOOD: { text: "Good", cls: "bg-sky-500/15 text-sky-300", help: "won at least 1.2× what it lost" },
  WEAK: { text: "Weak", cls: "bg-slate-500/15 text-slate-300", help: "in profit, but only just" },
  AVOID: { text: "Avoid", cls: "bg-rose-500/15 text-rose-300", help: "lost money with this strategy" },
  FEW: { text: "Too few", cls: "bg-amber-500/15 text-amber-300", help: "under 5 trades or 3 days: not enough to judge" },
};
const ORDER: Record<StockRankVerdict, number> = { STRONG: 5, GOOD: 4, WEAK: 3, AVOID: 1, FEW: 0 };
type Key = "rank" | "symbol" | "verdict" | "trades" | "win_rate" | "green_pct" | "gross" | "net" | "per_trade" | "avg_return_pct" | "profit_factor" | "worst_trade";
type Show = "ALL" | "KEEP" | StockRankVerdict;

const money = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "text-slate-300" : v > 0 ? "text-emerald-400" : "text-rose-400");

/**
 * Which stocks does my strategy win on? (backend stock_rank.py): the chosen bot's own strategy backtested on many stocks
 * over the same days, in batches, then every stock ranked by how often and how much it made. Practice money only.
 */
export function StockRanking({
  armed,
  arming,
  onArm,
  onArmMany,
}: {
  armed: Map<string, string[]>;
  arming?: string | null;
  onArm?: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
}) {
  const [lists, setLists] = useScanLists("sma.stockrank.lists", ["fno"]);
  const [days, setDays] = useState(10);
  const [bot, setBot] = useState(1);
  const [batch, setBatch] = useState(25);
  const [playHandoffs, setPlayHandoffs] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [items, setItems] = useState<StockRankJob[]>([]);
  const [running, setRunning] = useState<number | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [earlier, setEarlier] = useState(0);
  const [view, setView] = useState<StockRankView | null>(null);

  const loadList = useCallback(async () => {
    try {
      const got = await smaApi.stockRanks();
      setItems(got.items);
      setRunning(got.running);
      setOpenId((cur) => cur ?? got.running ?? got.items[0]?.id ?? null);
    } catch {
      /* optional */
    }
  }, []);
  const loadView = useCallback(async (id: number, extra: number) => {
    try {
      setView(await smaApi.stockRank(id, extra));
    } catch {
      setView(null);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);
  useEffect(() => {
    if (openId == null) setView(null);
    else void loadView(openId, earlier);
  }, [openId, earlier, loadView]);
  useEffect(() => {
    if (running == null) return;
    const timer = setInterval(() => {
      void loadList();
      if (openId != null) void loadView(openId, earlier);
    }, 8000);
    return () => clearInterval(timer);
  }, [running, openId, earlier, loadList, loadView]);

  const start = async () => {
    const range = lastClosedWeekdays(days);
    setBusy(true);
    setNote(null);
    try {
      const universe = (await resolveScanSymbols(lists as ScanList[], armed.keys())).slice(0, 250);
      if (!universe.length) throw new Error("No stocks in the picked lists.");
      const job = await smaApi.stockRankStart({
        date: range[0],
        end_date: range[range.length - 1],
        universe,
        bot,
        batch,
        play_handoffs: playHandoffs,
        lists,
      });
      setOpenId(job.id);
      setRunning(job.id);
      setNote({
        ok: true,
        text: `Started: ${universe.length} stocks on Bot ${bot}'s settings, ${range[0]} → ${range[range.length - 1]}, in ${job.runs.length} batch(es) of up to ${batch}, one after another at full speed. The table fills in as each batch finishes.`,
      });
      void loadList();
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start" });
    } finally {
      setBusy(false);
    }
  };

  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  const done = view ? view.runs.filter((r) => r.status === "DONE").length : 0;
  return (
    <section className="space-y-3 rounded-xl border border-slate-800 bg-card p-4">
      <div>
        <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
          <ListOrdered size={15} className="text-accentSky" /> Which stocks does my strategy win on?
        </h2>
        <p className="max-w-3xl text-xs text-slate-500">
          Backtests a bot&apos;s own settings on every stock you pick, over the same past days, then ranks the stocks: how
          often each one made money (winning trades, green days) and how much (₹ before charges, average return per trade).
          Keep the strong ones and arm them; re-run it every week or two, since a stock that worked last month may not this
          month. Practice money only — nothing is armed until you press Arm, and no order is sent.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Stocks
          <ScanListPicker value={lists as ScanList[]} onChange={setLists} />
        </label>
        <label className="flex flex-col gap-1">
          Last
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={select}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d} trading days
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Strategy of
          <select value={bot} onChange={(e) => setBot(Number(e.target.value))} className={select}>
            {BOTS.map((b) => (
              <option key={b} value={b}>
                Bot {b}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1" title="How many stocks each backtest run plays at once">
          Stocks per run
          <select value={batch} onChange={(e) => setBatch(Number(e.target.value))} className={select}>
            {BATCHES.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <label
          className="flex min-h-9 items-center gap-1.5 self-end text-xs text-slate-200"
          title="A stock the bot's own settings hand to Bot 2 or Bot 3 stays in the backtest and trades on that bot's settings (marked 'as Bot 2' / 'as Bot 3'). The live Trade lists are not touched."
        >
          <input type="checkbox" checked={playHandoffs} onChange={(e) => setPlayHandoffs(e.target.checked)} className="accent-violet-400" />
          Play hand-offs (Bot 2 / Bot 3)
        </label>
        <button
          type="button"
          disabled={busy || running != null}
          onClick={() => void start()}
          className="inline-flex min-h-9 items-center gap-1.5 self-end rounded-md bg-accentSky/15 px-3 font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/25 disabled:opacity-40"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <ListOrdered size={13} />} Rank the stocks
        </button>
        {running != null && (
          <button
            type="button"
            onClick={() => void smaApi.stockRankStop().then(() => setTimeout(() => void loadList(), 1500))}
            className="inline-flex min-h-9 items-center gap-1.5 self-end rounded-md px-3 font-semibold text-rose-300 ring-1 ring-inset ring-rose-500/40 hover:bg-rose-500/10"
          >
            <Square size={12} /> Stop
          </button>
        )}
      </div>
      <p className="text-[11px] text-slate-500">
        The backtests use this server&apos;s copy of the bot settings: on the backtest server, press Sync from desk first if you
        changed them on the main desk. A full F&amp;O list over 10 days is about 9 runs and can take an hour or more.
      </p>
      {note && <p className={clsx("text-xs", note.ok ? "text-accentSky" : "text-amber-300")}>{note.text}</p>}

      {items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
          <label className="flex items-center gap-2">
            Ranking
            <select value={openId ?? ""} onChange={(e) => setOpenId(e.target.value ? Number(e.target.value) : null)} className={select}>
              {items.map((c) => (
                <option key={c.id} value={c.id}>
                  #{c.id} · Bot {c.bot} · {c.universe} stocks · {c.start_date} → {c.end_date}
                  {c.id === running ? " · running" : ` · ${c.status.toLowerCase()}`}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2" title="Count the bot's other finished backtests too (a stock-day played twice counts once, the newest)">
            Count
            <select value={earlier} onChange={(e) => setEarlier(Number(e.target.value))} className={select}>
              {EARLIER.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          {openId != null && openId !== running && (
            <button
              type="button"
              onClick={() => {
                if (!window.confirm("Forget this ranking? Its backtest runs stay in the Backtests tab.")) return;
                void smaApi.stockRankDelete(openId).then(() => {
                  setOpenId(null);
                  void loadList();
                });
              }}
              className="inline-flex min-h-8 items-center gap-1 rounded px-2 text-slate-400 hover:bg-white/5 hover:text-rose-300"
            >
              <Trash2 size={12} /> Forget
            </button>
          )}
        </div>
      )}

      {view && (
        <>
          <p className="text-xs text-slate-500">
            Bot {view.bot}&apos;s settings{view.play_handoffs ? " with hand-offs to Bot 2 / Bot 3 played" : ""} · {view.start_date} →{" "}
            {view.end_date} · {done} of {view.runs.length} batch(es) done · {view.runs_used} backtest run(s) counted · P&amp;L before
            charges unless marked.
            {view.runs.some((r) => r.status === "SKIPPED" || r.status === "INTERRUPTED") && (
              <span className="text-amber-300"> Some batches did not finish (see the run list in Backtests).</span>
            )}
          </p>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-200">
            {view.answer.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <RankTable rows={view.rows} armed={armed} arming={arming} onArm={onArm} onArmMany={onArmMany} />
        </>
      )}
    </section>
  );
}

function RankTable({
  rows,
  armed,
  arming,
  onArm,
  onArmMany,
}: {
  rows: StockRankRow[];
  armed: Map<string, string[]>;
  arming?: string | null;
  onArm?: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
}) {
  const [show, setShow] = useState<Show>("ALL");
  const [query, setQuery] = useState("");
  const ticks = useTicks();
  const shown = useMemo(
    () =>
      rows.filter(
        (r) =>
          (show === "ALL" || (show === "KEEP" ? r.verdict === "STRONG" || r.verdict === "GOOD" : r.verdict === show)) &&
          (!query || r.symbol.includes(query.trim().toUpperCase())) &&
          ticks.shows(r.symbol)
      ),
    [rows, show, query, ticks]
  );
  const { sorted, sort, onSort } = useSort<StockRankRow, Key>(shown, (r, k) =>
    k === "verdict" ? ORDER[r.verdict] : ((r[k] as number | string | null) ?? null)
  );
  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
        <label className="flex items-center gap-2">
          Show
          <select value={show} onChange={(e) => setShow(e.target.value as Show)} className={select}>
            <option value="ALL">All stocks</option>
            <option value="KEEP">Strong and good (the ones to keep)</option>
            {(Object.keys(VERDICT) as StockRankVerdict[]).map((v) => (
              <option key={v} value={v}>
                {VERDICT[v].text} only
              </option>
            ))}
          </select>
        </label>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a stock"
          className="min-h-8 w-40 rounded border border-slate-700 bg-base px-2 text-xs text-slate-200"
        />
        <span>{shown.length} shown</span>
      </div>
      {onArmMany ? <TickBar ticks={ticks} onArm={onArmMany} /> : null}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1000px] text-xs">
          <thead className="text-[10px] text-slate-400">
            <tr>
              {onArmMany ? <TickHead ticks={ticks} symbols={sorted.map((r) => r.symbol)} /> : null}
              <SortTh label="#" k="rank" sort={sort} onSort={onSort} num />
              <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text />
              <SortTh label="Verdict" k="verdict" sort={sort} onSort={onSort} />
              <SortTh label="Trades" k="trades" sort={sort} onSort={onSort} num />
              <SortTh label="Winning trades" k="win_rate" sort={sort} onSort={onSort} num />
              <SortTh label="Green days" k="green_pct" sort={sort} onSort={onSort} num />
              <SortTh label="P&L" k="gross" sort={sort} onSort={onSort} num title="Before charges" />
              <SortTh label="After charges" k="net" sort={sort} onSort={onSort} num />
              <SortTh label="Per trade" k="per_trade" sort={sort} onSort={onSort} num />
              <SortTh label="Avg return / trade" k="avg_return_pct" sort={sort} onSort={onSort} num title="P&L of each trade in % of the money in it (qty × entry price), averaged" />
              <SortTh label="Won ÷ lost" k="profit_factor" sort={sort} onSort={onSort} num title="Profit factor: money made on winning trades ÷ money lost on losing ones (above 1 = in profit)" />
              <SortTh label="Worst trade" k="worst_trade" sort={sort} onSort={onSort} num />
              {onArm ? <th className="px-2 text-right font-medium uppercase tracking-wider">Arm</th> : null}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const on = armed.get(r.symbol) ?? [];
              return (
                <tr key={r.symbol} className="border-t border-slate-800">
                  {onArmMany ? <TickCell ticks={ticks} symbol={r.symbol} /> : null}
                  <td className="px-2 py-1 text-right font-mono text-slate-400">{r.rank}</td>
                  <td className="px-2 py-1 font-semibold text-slate-100">
                    <GrowwLink symbol={r.symbol} />
                  </td>
                  <td className="px-2 py-1">
                    <span title={VERDICT[r.verdict].help} className={clsx("rounded px-1.5 py-0.5 text-[11px] font-semibold", VERDICT[r.verdict].cls)}>
                      {VERDICT[r.verdict].text}
                    </span>
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-slate-300">{r.trades}</td>
                  <td className="px-2 py-1 text-right font-mono text-slate-100">{r.win_rate.toFixed(0)}%</td>
                  <td className="px-2 py-1 text-right font-mono text-slate-100">
                    {r.green_days} of {r.days}
                  </td>
                  <td className={clsx("px-2 py-1 text-right font-mono", tone(r.gross))}>{money(r.gross)}</td>
                  <td className={clsx("px-2 py-1 text-right font-mono", tone(r.net))}>{money(r.net)}</td>
                  <td className={clsx("px-2 py-1 text-right font-mono", tone(r.per_trade))}>{money(r.per_trade)}</td>
                  <td className={clsx("px-2 py-1 text-right font-mono", tone(r.avg_return_pct))}>
                    {r.avg_return_pct == null ? "—" : `${r.avg_return_pct > 0 ? "+" : ""}${r.avg_return_pct.toFixed(2)}%`}
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-slate-300">{r.profit_factor == null ? "no losses" : r.profit_factor.toFixed(2)}</td>
                  <td className={clsx("px-2 py-1 text-right font-mono", tone(r.worst_trade))}>{money(r.worst_trade)}</td>
                  {onArm ? (
                    <td className="px-2 py-1 text-right">
                      <span className="inline-flex items-center justify-end gap-1.5">
                        {on.length ? <span className="text-[11px] font-semibold text-emerald-300">{on.join(" · ")}</span> : null}
                        <button
                          type="button"
                          disabled={arming === r.symbol}
                          onClick={() => onArm(r.symbol)}
                          title="Arm on a bot (asks which). Arming only: the bot orders on its own signal."
                          className="min-h-8 rounded-md px-2.5 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
                        >
                          {arming === r.symbol ? "…" : "Arm"}
                        </button>
                      </span>
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
