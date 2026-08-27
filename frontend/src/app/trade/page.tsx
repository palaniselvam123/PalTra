"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { FileBarChart, Loader2, Search, Wallet, X, XCircle } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { AddSymbolSearch } from "@/components/Trade/AddSymbolSearch";
import { OrderTicket } from "@/components/Trade/OrderTicket";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type DeskAccount, type DeskPosition, type WatchRow } from "@/lib/api";
import { money, num, pct, pnlClass } from "@/lib/format";

export default function TradePage() {
  const { connected, summary, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot, ticks } =
    useTradingState();

  const [watch, setWatch] = useState<WatchRow[]>([]);
  const [account, setAccount] = useState<DeskAccount | null>(null);
  const [positions, setPositions] = useState<DeskPosition[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [deskSummary, setDeskSummary] = useState<{
    total_pnl: number;
    trades_closed: number;
    win_rate_pct: number;
    profit_factor: number;
    max_drawdown: number;
  } | null>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [removing, setRemoving] = useState<string | null>(null);
  const [instrumentStats, setInstrumentStats] = useState<{
    mainboard_equity: number;
    intraday_eligible: number;
  } | null>(null);

  const refresh = useCallback(() => {
    api.deskWatchlist().then(setWatch).catch(() => {});
    api.deskAccount().then(setAccount).catch(() => {});
    api.deskPositions().then(setPositions).catch(() => {});
    api.deskSummary().then(setDeskSummary).catch(() => {});
    api.deskHistory().then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    // Also warms the server-side instrument-master cache before the first search.
    api.getInstrumentStats().then(setInstrumentStats).catch(() => {});
  }, []);

  const removeSymbol = async (symbol: string) => {
    setRemoving(symbol);
    try {
      await api.removeFromWatchlist(symbol);
      refresh();
    } finally {
      setRemoving(null);
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  // Live tick prices are pushed over the WebSocket; fold them into the rows so
  // the list moves at tick speed rather than the 3s poll.
  const rows = useMemo(() => {
    const merged = watch.map((r) => {
      const tick = ticks[r.symbol];
      return tick ? { ...r, ltp: tick.ltp, bid: tick.bid, ask: tick.ask } : r;
    });
    const q = query.trim().toUpperCase();
    return q ? merged.filter((r) => r.symbol.includes(q)) : merged;
  }, [watch, ticks, query]);

  const selectedRow = rows.find((r) => r.symbol === selected) ?? null;
  const openPnl = positions.reduce((s, p) => s + p.unrealised, 0);

  const closeOne = async (symbol: string) => {
    setBusy(true);
    try {
      await api.deskClose(symbol);
      refresh();
    } finally {
      setBusy(false);
    }
  };

  const squareOffAll = async () => {
    setBusy(true);
    try {
      await api.deskSquareOffAll();
      refresh();
    } finally {
      setBusy(false);
    }
  };

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
        <div className="flex items-start gap-3 flex-wrap">
          <div>
            <h1 className="text-lg font-semibold text-slate-100">Manual Trading Desk</h1>
            <p className="text-xs text-slate-500">
              Your own discretionary account, with a wallet and reports kept entirely separate from the strategy
              bot. You choose the quantity here — the 1% rule does not apply.
            </p>
          </div>
          <Link
            href="/reports?account=MANUAL"
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border text-[11px] text-slate-300 hover:bg-white/5 transition"
          >
            <FileBarChart size={12} /> Desk Reports
          </Link>
        </div>

        {/* Desk P&L — the bot's figures live on the dashboard; these are yours. */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          <PnlTile
            label="P&L Today"
            value={money((deskSummary?.total_pnl ?? 0) + openPnl, true)}
            tone={pnlClass((deskSummary?.total_pnl ?? 0) + openPnl)}
            sub="realised + open"
            big
          />
          <PnlTile
            label="Realised Today"
            value={money(deskSummary?.total_pnl, true)}
            tone={pnlClass(deskSummary?.total_pnl)}
            sub={`${deskSummary?.trades_closed ?? 0} closed`}
          />
          <PnlTile label="Open P&L" value={money(openPnl, true)} tone={pnlClass(openPnl)} sub={`${positions.length} positions`} />
          <PnlTile label="Win Rate" value={pct(deskSummary?.win_rate_pct)} sub="today" />
          <PnlTile
            label="Profit Factor"
            value={num(deskSummary?.profit_factor)}
            tone={(deskSummary?.profit_factor ?? 0) >= 1 ? "text-profit" : "text-loss"}
            sub="gross win ÷ gross loss"
          />
        </div>

        {/* Wallet */}
        <div className="rounded-lg border border-border bg-surface p-4">
          <div className="flex items-center gap-2 mb-3">
            <Wallet size={14} className="text-bot" />
            <span className="text-sm font-medium text-slate-200">Desk Wallet</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-profit/10 text-profit border border-profit/30">
              NOT REAL MONEY
            </span>
            <span className="text-[10px] text-slate-600 ml-auto">separate from the bot account</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <Stat label="Balance" value={money(account?.balance)} tone="text-slate-100" big />
            <Stat
              label="Realised (all time)"
              value={money(account?.realised_all_time, true)}
              tone={pnlClass(account?.realised_all_time)}
            />
            <Stat label="Unrealised" value={money(openPnl, true)} tone={pnlClass(openPnl)} />
            <Stat label="Equity" value={money((account?.balance ?? 0) + openPnl)} />
            <Stat
              label="Margin available"
              value={money(account?.margin_available)}
              sub={account ? `${account.max_leverage}× on balance` : undefined}
              tone="text-bot"
            />
            <Stat
              label="Deployed"
              value={money(account?.open_exposure)}
              sub={account ? `${num(account.exposure_ratio)}× capital` : undefined}
            />
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
          {/* Watchlist */}
          <div className="lg:col-span-2 rounded-lg border border-border bg-surface overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center gap-2 flex-wrap">
              <div>
                <span className="text-sm font-medium text-slate-200">
                  Intraday Watchlist <span className="text-xs text-slate-500">({rows.length})</span>
                </span>
                {instrumentStats && (
                  <div className="text-[10px] text-slate-600">
                    {instrumentStats.intraday_eligible.toLocaleString("en-IN")} NSE stocks are MIS-eligible on
                    Groww — search to add any of them
                  </div>
                )}
              </div>
              <div className="relative ml-auto">
                <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-600" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Filter watching"
                  className="bg-base border border-border rounded-md pl-6 pr-2 py-1 text-xs text-slate-200 w-28 focus:outline-none focus:border-bot/60"
                />
              </div>
              <AddSymbolSearch onAdded={refresh} />
            </div>
            <div className="overflow-x-auto max-h-[520px] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-surface">
                  <tr className="text-left text-[11px] text-slate-500 border-b border-border">
                    <th className="px-4 py-2 font-medium">Symbol</th>
                    <th className="px-3 py-2 font-medium text-right">LTP</th>
                    <th className="px-3 py-2 font-medium text-right">Bid / Ask</th>
                    <th className="px-3 py-2 font-medium text-right">Spread</th>
                    <th className="px-3 py-2 font-medium text-right w-32">Trade</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-xs text-slate-500">
                        Waiting for quotes…
                      </td>
                    </tr>
                  )}
                  {rows.map((r) => (
                    <tr
                      key={r.symbol}
                      className={clsx(
                        "border-b border-border/50 last:border-0 hover:bg-white/[0.03] transition",
                        selected === r.symbol && "bg-white/[0.04]"
                      )}
                    >
                      <td className="px-4 py-2 text-slate-100">
                        <span className="inline-flex items-center gap-1">
                          {r.symbol}
                          {!r.in_universe && (
                            <span
                              className="text-[9px] px-1 py-0.5 rounded bg-slate-700/50 text-slate-400"
                              title="Added by you — not part of the bot's fixed strategy universe"
                            >
                              ADDED
                            </span>
                          )}
                          {r.intraday_allowed === false && (
                            <span
                              className="text-[9px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-400"
                              title="Not MIS-eligible on Groww right now — you can watch it, but orders will be refused"
                            >
                              NO MIS
                            </span>
                          )}
                          {r.has_position && (
                            <span className="text-[9px] px-1 py-0.5 rounded bg-bot/15 text-bot">HOLDING</span>
                          )}
                          {!r.in_universe && !r.has_position && (
                            <button
                              onClick={() => removeSymbol(r.symbol)}
                              disabled={removing === r.symbol}
                              title="Remove from watchlist"
                              className="text-slate-600 hover:text-loss transition disabled:opacity-40"
                            >
                              {removing === r.symbol ? (
                                <Loader2 size={11} className="animate-spin" />
                              ) : (
                                <X size={11} />
                              )}
                            </button>
                          )}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono text-right text-slate-200">{num(r.ltp)}</td>
                      <td className="px-3 py-2 font-mono text-right text-[11px] text-slate-500">
                        {num(r.bid)} / {num(r.ask)}
                      </td>
                      <td
                        className={clsx(
                          "px-3 py-2 font-mono text-right text-[11px]",
                          (r.spread_pct ?? 0) > 0.15 ? "text-amber-400" : "text-slate-500"
                        )}
                      >
                        {pct(r.spread_pct, 3)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          onClick={() => setSelected(r.symbol)}
                          className="px-2.5 py-1 rounded-md bg-bot/15 text-bot text-[11px] font-medium hover:bg-bot/25 transition"
                        >
                          Buy / Sell
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Ticket */}
          <div className="space-y-4">
            {selectedRow ? (
              <OrderTicket
                row={selectedRow}
                account={account}
                onClose={() => setSelected(null)}
                onFilled={refresh}
              />
            ) : (
              <div className="rounded-lg border border-border bg-surface p-6 text-center text-xs text-slate-500">
                Pick a symbol from the watchlist to open an order ticket.
              </div>
            )}
          </div>
        </div>

        {/* Positions */}
        <div className="rounded-lg border border-border bg-surface overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <span className="text-sm font-medium text-slate-200">
              Desk Positions <span className="text-xs text-slate-500">({positions.length})</span>
            </span>
            {positions.length > 0 && (
              <>
                <span className={clsx("font-mono text-sm ml-2", pnlClass(openPnl))}>{money(openPnl, true)}</span>
                <button
                  onClick={squareOffAll}
                  disabled={busy}
                  className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-loss/20 text-loss text-[11px] font-medium hover:bg-loss/30 transition"
                >
                  {busy ? <Loader2 size={11} className="animate-spin" /> : <XCircle size={11} />} Square Off All
                </button>
              </>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] text-slate-500 border-b border-border">
                  <th className="px-4 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 font-medium">Side</th>
                  <th className="px-3 py-2 font-medium text-right">Qty</th>
                  <th className="px-3 py-2 font-medium text-right">Entry</th>
                  <th className="px-3 py-2 font-medium text-right">LTP</th>
                  <th className="px-3 py-2 font-medium text-right">Value</th>
                  <th className="px-3 py-2 font-medium text-right">SL / Target</th>
                  <th className="px-3 py-2 font-medium text-right">P&amp;L</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center text-xs text-slate-500">
                      No open positions on the desk.
                    </td>
                  </tr>
                )}
                {positions.map((p) => (
                  <tr key={p.symbol} className="border-b border-border/50 last:border-0">
                    <td className="px-4 py-2 text-slate-100">{p.symbol}</td>
                    <td className={clsx("px-3 py-2 text-xs", p.side === "BUY" ? "text-profit" : "text-loss")}>
                      {p.side}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-right">{p.quantity.toLocaleString("en-IN")}</td>
                    <td className="px-3 py-2 font-mono text-xs text-right">{num(p.entry_price)}</td>
                    <td className="px-3 py-2 font-mono text-xs text-right">{num(p.ltp)}</td>
                    <td className="px-3 py-2 font-mono text-xs text-right text-slate-400">{money(p.value)}</td>
                    <td className="px-3 py-2 font-mono text-[11px] text-right text-slate-500">
                      {p.stop_loss ? num(p.stop_loss) : "—"} / {p.target ? num(p.target) : "—"}
                    </td>
                    <td className={clsx("px-3 py-2 font-mono text-right", pnlClass(p.unrealised))}>
                      {money(p.unrealised, true)}
                      <span className="block text-[10px]">{pct(p.unrealised_pct, 2)}</span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => closeOne(p.symbol)}
                        disabled={busy}
                        className="px-2 py-1 rounded-md border border-border text-[11px] text-slate-300 hover:bg-white/5 transition"
                      >
                        Close
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Closed P&L */}
        <div className="rounded-lg border border-border bg-surface overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <span className="text-sm font-medium text-slate-200">
              Desk Trade History <span className="text-xs text-slate-500">({history.filter((t) => t.status !== "CANCELLED").length} closed)</span>
            </span>
            <Link
              href="/reports?account=MANUAL"
              className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-bot/15 text-bot text-[11px] font-medium hover:bg-bot/25 transition"
            >
              <FileBarChart size={12} /> Full Report
            </Link>
          </div>
          <div className="overflow-x-auto max-h-72 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-left text-[11px] text-slate-500 border-b border-border">
                  <th className="px-4 py-2 font-medium">Closed</th>
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 font-medium">Side</th>
                  <th className="px-3 py-2 font-medium text-right">Qty</th>
                  <th className="px-3 py-2 font-medium text-right">Entry</th>
                  <th className="px-3 py-2 font-medium text-right">Exit</th>
                  <th className="px-3 py-2 font-medium text-right">P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-xs text-slate-500">
                      No closed trades on the desk yet.
                    </td>
                  </tr>
                )}
                {history.map((t) => {
                  const voided = t.status === "CANCELLED";
                  return (
                    <tr
                      key={t.id}
                      className={clsx("border-b border-border/50 last:border-0", voided && "opacity-60")}
                      title={voided ? t.exit_reason ?? undefined : undefined}
                    >
                      <td className="px-4 py-2 font-mono text-xs text-slate-500">
                        {t.closed_at ? new Date(t.closed_at + "Z").toLocaleTimeString("en-IN") : "—"}
                      </td>
                      <td className="px-3 py-2 text-slate-100">
                        {t.symbol}
                        {voided && (
                          <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-400">
                            VOID
                          </span>
                        )}
                      </td>
                      <td className={clsx("px-3 py-2 text-xs", t.side === "BUY" ? "text-profit" : "text-loss")}>
                        {t.side}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-right">{t.quantity}</td>
                      <td className="px-3 py-2 font-mono text-xs text-right">{num(t.entry_price)}</td>
                      <td className="px-3 py-2 font-mono text-xs text-right">
                        {voided ? "—" : t.exit_price !== null ? num(t.exit_price) : "—"}
                      </td>
                      <td className={clsx("px-3 py-2 font-mono text-right", voided ? "text-slate-500" : pnlClass(t.pnl))}>
                        {voided ? "₹0.00" : money(t.pnl, true)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}

function PnlTile({
  label,
  value,
  sub,
  tone,
  big,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
  big?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2.5">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={clsx("font-mono mt-0.5", big ? "text-xl" : "text-base", tone ?? "text-slate-100")}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
  big,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
  big?: boolean;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={clsx("font-mono mt-0.5", big ? "text-lg" : "text-sm", tone ?? "text-slate-200")}>{value}</div>
      {sub && <div className="text-[10px] text-slate-600">{sub}</div>}
    </div>
  );
}
