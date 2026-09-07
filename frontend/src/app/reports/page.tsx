"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import clsx from "clsx";
import { Navbar } from "@/components/Navbar";
import { SummaryGrid } from "@/components/Reports/SummaryGrid";
import { EquityCurve } from "@/components/Reports/EquityCurve";
import { BreakdownTable } from "@/components/Reports/BreakdownTable";
import { TransactionsTable } from "@/components/Reports/TransactionsTable";
import { FiltersBar } from "@/components/Reports/FiltersBar";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type FullReport, type ReportFilters } from "@/lib/api";
import { timestamp } from "@/lib/format";

type Tab = "overview" | "breakdowns" | "transactions";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "breakdowns", label: "Breakdowns" },
  { id: "transactions", label: "Transactions" },
];

export default function ReportsPage() {
  const { connected, summary, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } = useTradingState();

  const [filters, setFilters] = useState<ReportFilters>({});
  const [options, setOptions] = useState<any>(null);
  const [report, setReport] = useState<FullReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");

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

  useEffect(() => {
    api.getReportOptions().then(setOptions).catch(() => {});
    // Honour ?account=MANUAL so "Desk Reports" lands on the right wallet.
    // Read directly rather than via useSearchParams, which would force this
    // page behind a Suspense boundary for no benefit.
    const wanted = new URLSearchParams(window.location.search).get("account");
    if (wanted === "MANUAL" || wanted === "AUTO") setFilters((f) => ({ ...f, account: wanted }));
  }, []);

  // Re-query on every filter change; the dataset is local SQLite, so a debounce
  // would add latency without saving anything meaningful.
  useEffect(() => {
    load(filters);
  }, [filters, load]);

  return (
    <div>
      <Navbar
        connected={connected}
        totalPnl={summary.total_pnl}
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
              Every paper transaction with its full forensics — costs, R-multiples, holding times, and the
              expert view behind bot entries. All times IST.
            </p>
          </div>
          <button
            onClick={() => load(filters)}
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border text-[11px] text-slate-300 hover:bg-white/5 transition"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : undefined} /> Refresh
          </button>
        </div>

        {/* The two wallets are independent, so mixing their trades into one
            performance figure would be meaningless. Account comes first. */}
        <div className="flex items-center gap-1 rounded-card border border-border bg-surface p-1 w-fit">
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
          options={options}
          onChange={setFilters}
          onReset={() => setFilters({})}
          csvUrl={api.reportCsvUrl(filters)}
        />

        {error && (
          <div className="rounded-lg border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss">{error}</div>
        )}

        {loading && !report ? (
          <div className="rounded-card border border-border bg-surface px-4 py-10 flex items-center justify-center gap-2 text-sm text-slate-500">
            <Loader2 size={16} className="animate-spin" /> Building report…
          </div>
        ) : report ? (
          <>
            <SummaryGrid summary={report.summary} />

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
                    <span className="ml-1.5 text-slate-600">{report.transactions.length}</span>
                  )}
                </button>
              ))}
              <span className="ml-auto text-[11px] text-slate-600 pb-2">
                generated {timestamp(report.generated_at)}
              </span>
            </div>

            {tab === "overview" && (
              <div className="space-y-4">
                <EquityCurve points={report.equity_curve} />
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <BreakdownTable title="By Day" rows={report.daily.map((d) => ({ ...d, key: d.date }))} keyLabel="Date" />
                  <BreakdownTable title="By Symbol" rows={report.by_symbol} keyLabel="Symbol" />
                </div>
              </div>
            )}

            {tab === "breakdowns" && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <BreakdownTable title="By Side" rows={report.by_side} keyLabel="Side" />
                <BreakdownTable title="By Source" rows={report.by_source} keyLabel="Manual vs Bot" />
                <BreakdownTable title="By Account" rows={report.by_account} keyLabel="Wallet" />
                <BreakdownTable
                  title="By Exit Reason"
                  rows={report.by_exit_reason}
                  keyLabel="Reason"
                  empty="No exit reasons recorded yet — trades closed before this was tracked show as “—”."
                />
                <BreakdownTable title="By Strategy" rows={report.by_strategy} keyLabel="Strategy" />
                <BreakdownTable title="By Weekday" rows={report.by_weekday} keyLabel="Day" />
                <BreakdownTable title="By Entry Hour (IST)" rows={report.by_hour} keyLabel="Hour" />
              </div>
            )}

            {tab === "transactions" && <TransactionsTable transactions={report.transactions} />}
          </>
        ) : null}
      </main>
    </div>
  );
}
