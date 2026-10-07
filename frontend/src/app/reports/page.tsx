"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import clsx from "clsx";
import { Navbar } from "@/components/Navbar";
import { SummaryGrid } from "@/components/Reports/SummaryGrid";
import { EquityCurve } from "@/components/Reports/EquityCurve";
import { BreakdownTable } from "@/components/Reports/BreakdownTable";
import { TransactionsTable } from "@/components/Reports/TransactionsTable";
import { FiltersBar } from "@/components/Reports/FiltersBar";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type FullReport, type ReportFilters, type Transaction } from "@/lib/api";
import { markIst, timestamp } from "@/lib/format";
import { smaApi, type TradeBookMode } from "@/lib/smaApi";
import { buildSmaReport, smaOptions, smaTransaction } from "@/lib/smaReport";

type Tab = "overview" | "breakdowns" | "transactions";

/** Which engine's trades: the SMA bots (the terminal) or the ORB desk (ORB bot and manual desk). */
type Source = "SMA" | "ORB";
const SOURCE_KEY = "reports.source";
const BOOK_KEY = "reports.smaBook";

const SMA_BOOKS: { id: TradeBookMode; label: string }[] = [
  { id: "LIVE", label: "NSE live (real money)" },
  { id: "PAPER", label: "Practice" },
  { id: "REPLAY", label: "Replays" },
  { id: "RESEARCH", label: "Research desk" },
];

function remembered<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private window: the choice just is not kept */
  }
}

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "breakdowns", label: "Breakdowns" },
  { id: "transactions", label: "Transactions" },
];

export default function ReportsPage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();

  const [filters, setFilters] = useState<ReportFilters>({});
  const [options, setOptions] = useState<any>(null);
  const [report, setReport] = useState<FullReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [source, setSource] = useState<Source>("SMA");
  const [book, setBook] = useState<TradeBookMode>("LIVE");
  const [smaTx, setSmaTx] = useState<Transaction[] | null>(null);
  const [smaTotal, setSmaTotal] = useState(0);

  const load = useCallback(async (active: ReportFilters) => {
    setLoading(true);
    setError(null);
    try {
      setReport(await api.getReport(active));
    } catch (e: any) {
      setError(e.message ?? "Could not load the report");
    } finally {
      setLoading(false);
    }
  }, []);

  // The SMA books come whole (up to 20,000 trades each); filters then run in the browser.
  const loadSma = useCallback(async (which: TradeBookMode) => {
    setLoading(true);
    setError(null);
    try {
      const [rows, bots] = await Promise.all([smaApi.reportBook(which), smaApi.bots().catch(() => [])]);
      const names = new Map(bots.map((b) => [b.bot, b.name]));
      setSmaTx(rows.rows.map((r) => smaTransaction(r, (n) => names.get(n) ?? `Bot ${n}`)));
      setSmaTotal(rows.total);
    } catch (e: any) {
      setSmaTx(null);
      setError(e.message ?? "The SMA terminal did not answer");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    api.getReportOptions().then(setOptions).catch(() => {});
    // Honour ?account=MANUAL so "Desk Reports" lands on the right wallet.
    // Read directly rather than via useSearchParams, which would force this
    // page behind a Suspense boundary for no benefit.
    const params = new URLSearchParams(window.location.search);
    const wanted = params.get("account");
    if (wanted === "MANUAL" || wanted === "AUTO") {
      setSource("ORB");
      setFilters((f) => ({ ...f, account: wanted }));
    } else {
      setSource(params.get("source") === "orb" ? "ORB" : remembered(SOURCE_KEY, ["SMA", "ORB"] as const, "SMA"));
    }
    setBook(remembered(BOOK_KEY, ["LIVE", "PAPER", "REPLAY", "RESEARCH"] as const, "LIVE"));
  }, []);

  // ORB desk: re-query on every filter change; the dataset is local SQLite, so a
  // debounce would add latency without saving anything meaningful.
  useEffect(() => {
    if (source === "ORB") load(filters);
  }, [filters, load, source]);

  useEffect(() => {
    if (source === "SMA") loadSma(book);
  }, [source, book, loadSma]);

  const smaReport = useMemo(() => (smaTx ? buildSmaReport(smaTx, filters) : null), [smaTx, filters]);
  const shown = source === "SMA" ? smaReport : report;
  const shownOptions = source === "SMA" ? (smaTx ? smaOptions(smaTx) : null) : options;

  const pickSource = (next: Source) => {
    if (next === source) return;
    remember(SOURCE_KEY, next);
    setFilters({});
    setSource(next);
  };
  const pickBook = (next: TradeBookMode) => {
    if (next === book) return;
    remember(BOOK_KEY, next);
    setBook(next);
  };

  return (
    <div>
      <Navbar
        connected={connected}
        totalPnl={summaryLoad === "ok" ? summary.total_pnl : null}
        killSwitchActive={killSwitchActive}
        onKillSwitch={killSwitch}
        onResetKillSwitch={resetKillSwitch}
        feed={feed}
        onFeedChanged={setFeed}
        botRunning={bot?.enabled ?? false}
      />

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <div>
            <h1 className="text-lg font-semibold text-slate-100">Transaction History &amp; Reports</h1>
            <p className="text-xs text-slate-500">
              {source === "SMA"
                ? "Every SMA bot trade with its costs, holding time and exit reason, book by book. All times IST."
                : "Every ORB desk transaction with its full forensics — costs, R-multiples, holding times, and the expert view behind bot entries. All times IST."}
            </p>
          </div>
          <button
            onClick={() => (source === "SMA" ? loadSma(book) : load(filters))}
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border text-[11px] text-slate-300 hover:bg-white/5 transition"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : undefined} /> Refresh
          </button>
        </div>

        <div role="group" aria-label="Which trades" className="flex flex-wrap items-center gap-1 rounded-card border border-border bg-surface p-1 w-fit">
          {(
            [
              { id: "SMA", label: "SMA bots (terminal)" },
              { id: "ORB", label: "ORB desk" },
            ] as const
          ).map((o) => (
            <button
              key={o.id}
              type="button"
              aria-pressed={source === o.id}
              onClick={() => pickSource(o.id)}
              className={clsx(
                "px-3 py-1.5 rounded-md text-xs font-semibold transition",
                source === o.id ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200"
              )}
            >
              {o.label}
            </button>
          ))}
        </div>

        {source === "SMA" ? (
          <div className="flex flex-wrap items-center gap-2">
            <div role="group" aria-label="Book" className="flex flex-wrap items-center gap-1 rounded-card border border-border bg-surface p-1 w-fit">
              {SMA_BOOKS.map((b) => (
                <button
                  key={b.id}
                  type="button"
                  aria-pressed={book === b.id}
                  onClick={() => pickBook(b.id)}
                  className={clsx(
                    "px-3 py-1.5 rounded-md text-xs font-medium transition",
                    book === b.id ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200"
                  )}
                >
                  {b.label}
                </button>
              ))}
            </div>
            <span className="text-[11px] text-slate-500">
              Pick a bot under “Any source”.
              {smaTx && smaTotal > smaTx.length ? ` Showing the newest ${smaTx.length.toLocaleString("en-IN")} of ${smaTotal.toLocaleString("en-IN")} trades.` : ""}
            </span>
          </div>
        ) : null}

        {/* The two wallets are independent, so mixing their trades into one
            performance figure would be meaningless. Account comes first. */}
        <div className={clsx("flex items-center gap-1 rounded-card border border-border bg-surface p-1 w-fit", source === "SMA" && "hidden")}>
          {[
            { id: undefined, label: "Both accounts" },
            { id: "AUTO", label: "Strategy / Bot" },
            { id: "MANUAL", label: "Manual Desk" },
          ].map((a) => (
            <button
              key={a.label}
              onClick={() => setFilters({ ...filters, account: a.id })}
              className={clsx(
                "px-3 py-1.5 rounded-md text-xs font-medium transition",
                filters.account === a.id ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200"
              )}
            >
              {a.label}
            </button>
          ))}
        </div>

        <FiltersBar
          filters={filters}
          options={shownOptions}
          onChange={setFilters}
          onReset={() => setFilters({})}
          csvUrl={source === "SMA" ? smaApi.csvUrl(book) : api.reportCsvUrl(filters)}
        />

        {error && (
          <div className="rounded-lg border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss">{error}</div>
        )}

        {loading && !shown ? (
          <div className="rounded-card border border-border bg-surface px-4 py-10 flex items-center justify-center gap-2 text-sm text-slate-500">
            <Loader2 size={16} className="animate-spin" /> Building report… If this stays, the report did not load.
          </div>
        ) : shown ? (
          <>
            <SummaryGrid summary={shown.summary} capital={source === "ORB"} />

            <div className="flex items-center gap-1 border-b border-border">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={clsx(
                    "px-3 py-2 text-xs font-medium border-b-2 -mb-px transition",
                    tab === t.id
                      ? "border-bot text-bot"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  )}
                >
                  {t.label}
                  {t.id === "transactions" && (
                    <span className="ml-1.5 text-slate-600">{shown.transactions.length}</span>
                  )}
                </button>
              ))}
              <span className="ml-auto text-[11px] text-slate-600 pb-2">
                generated {markIst(timestamp(shown.generated_at))}
              </span>
            </div>

            {tab === "overview" && (
              <div className="space-y-4">
                <EquityCurve points={shown.equity_curve} />
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <BreakdownTable title="By Day" rows={shown.daily.map((d) => ({ ...d, key: d.date }))} keyLabel="Date" />
                  <BreakdownTable title="By Symbol" rows={shown.by_symbol} keyLabel="Symbol" />
                </div>
              </div>
            )}

            {tab === "breakdowns" && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <BreakdownTable title="By Side" rows={shown.by_side} keyLabel="Side" />
                <BreakdownTable title={source === "SMA" ? "By Bot" : "By Source"} rows={shown.by_source} keyLabel={source === "SMA" ? "Bot" : "Manual vs Bot"} />
                {source === "ORB" ? <BreakdownTable title="By Account" rows={shown.by_account} keyLabel="Wallet" /> : null}
                <BreakdownTable
                  title="By Exit Reason"
                  rows={shown.by_exit_reason}
                  keyLabel="Reason"
                  empty="No exit reasons recorded yet — trades closed before this was tracked show as “—”."
                />
                <BreakdownTable title="By Strategy" rows={shown.by_strategy} keyLabel="Strategy" />
                <BreakdownTable title="By Weekday" rows={shown.by_weekday} keyLabel="Day" />
                <BreakdownTable title="By Entry Hour (IST)" rows={shown.by_hour} keyLabel="Hour" />
              </div>
            )}

            {tab === "transactions" && <TransactionsTable transactions={shown.transactions} expert={source === "ORB"} />}
          </>
        ) : null}
      </main>
    </div>
  );
}
