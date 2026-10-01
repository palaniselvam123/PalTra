"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { FileBarChart, Loader2, Search, Wallet, X, XCircle } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { AddSymbolSearch } from "@/components/Trade/AddSymbolSearch";
import { OrderTicket } from "@/components/Trade/OrderTicket";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type DeskAccount, type DeskPosition, type WatchRow } from "@/lib/api";
import { istTime, money, num, pct, pnlClass } from "@/lib/format";

export default function TradePage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot, ticks } =
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
  const [walletError, setWalletError] = useState<string | null>(null);
  const [watchLoad, setWatchLoad] = useState<"loading" | "ok" | "error">("loading");
  const [positionsLoad, setPositionsLoad] = useState<"loading" | "ok" | "error">("loading");
  const [historyLoad, setHistoryLoad] = useState<"loading" | "ok" | "error">("loading");
  const [accountLoad, setAccountLoad] = useState<"loading" | "ok" | "error">("loading");
  const [summaryDeskLoad, setSummaryDeskLoad] = useState<"loading" | "ok" | "error">("loading");
  const inflight = useRef(false);

  const refresh = useCallback(() => {
    if (inflight.current) return;
    inflight.current = true;
    const keep = (setter: typeof setWatchLoad) => setter((prev) => (prev === "ok" ? "ok" : "error"));
    Promise.allSettled([
      api
        .deskWatchlist()
        .then((rows) => {
          setWatch(rows);
          setWatchLoad("ok");
        })
        .catch(() => keep(setWatchLoad)),
      api
        .deskAccount()
        .then((row) => {
          setAccount(row);
          setAccountLoad("ok");
        })
        .catch(() => keep(setAccountLoad)),
      api
        .deskPositions()
        .then((rows) => {
          setPositions(rows);
          setPositionsLoad("ok");
        })
        .catch(() => keep(setPositionsLoad)),
      api
        .deskSummary()
        .then((row) => {
          setDeskSummary(row);
          setSummaryDeskLoad("ok");
        })
        .catch(() => keep(setSummaryDeskLoad)),
      api
        .deskHistory()
        .then((rows) => {
          setHistory(rows);
          setHistoryLoad("ok");
        })
        .catch(() => keep(setHistoryLoad)),
    ]).finally(() => {
      inflight.current = false;
    });
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
    const id = setInterval(refresh, 15000);
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

  const fromGroww = account?.funds_source === "groww";
  const armed = account?.execution === "groww";
  const realised = summaryDeskLoad === "ok" ? (deskSummary?.total_pnl ?? null) : null;
  const openShown = positionsLoad === "ok" ? openPnl : null;
  const pnlToday = realised != null && openShown != null ? realised + openShown : null;
  const stalled = [watchLoad, positionsLoad, historyLoad, accountLoad, summaryDeskLoad].some((s) => s === "error");

  const toggleGrowwOrders = async () => {
    setWalletError(null);
    if (!armed) {
      if (!account?.groww_connected) {
        setWalletError(account?.funds_error || "Groww is not connected. Log in from Settings first.");
        return;
      }
      const ok = window.confirm(
        "Buy and Sell will place real MIS orders in your Groww account. You can lose real money. Continue?"
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      setAccount(await api.deskSetExecution(armed ? "paper" : "groww", true));
    } catch (e: any) {
      setWalletError(e.message ?? "Could not change order mode");
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
        totalPnl={summaryLoad === "ok" ? summary.total_pnl : null}
        killSwitchActive={killSwitchActive}
        onKillSwitch={killSwitch}
        onResetKillSwitch={resetKillSwitch}
        feed={feed}
        onFeedChanged={setFeed}
        botRunning={bot?.enabled ?? false}
      />

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        {stalled && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            The desk did not answer. Blank figures are a failed load, not an empty account.
          </div>
        )}
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
            value={money(pnlToday, true)}
            tone={pnlClass(pnlToday)}
            sub="realised + open"
            big
          />
          <PnlTile
            label="Realised Today"
            value={money(realised, true)}
            tone={pnlClass(realised)}
            sub={summaryDeskLoad === "ok" ? `${deskSummary?.trades_closed ?? 0} closed` : "did not load"}
          />
          <PnlTile
            label="Open P&L"
            value={money(openShown, true)}
            tone={pnlClass(openShown)}
            sub={positionsLoad === "ok" ? `${positions.length} positions` : "did not load"}
          />
          <PnlTile label="Win Rate" value={summaryDeskLoad === "ok" ? pct(deskSummary?.win_rate_pct) : "—"} sub="today" />
          <PnlTile
            label="Profit Factor"
            value={summaryDeskLoad === "ok" ? num(deskSummary?.profit_factor) : "—"}
            tone={(deskSummary?.profit_factor ?? 0) >= 1 ? "text-profit" : "text-loss"}
            sub="gross win ÷ gross loss"
          />
        </div>

        {/* Wallet */}
        <div className="rounded-card border border-border bg-surface p-4">
          <div className="flex items-center gap-2 mb-3">
            <Wallet size={14} className="text-bot" />
            <span className="text-sm font-medium text-slate-200">Desk Wallet</span>
            <span
              className={clsx(
                "text-[10px] px-1.5 py-0.5 rounded border",
                armed
                  ? "bg-loss/10 text-loss border-loss/40"
                  : fromGroww
                    ? "bg-bot/10 text-bot border-bot/40"
                    : "bg-profit/10 text-profit border-profit/30"
              )}
            >
              {armed ? "LIVE GROWW ORDERS" : "PRACTICE ORDERS"}
            </span>
            <button
              type="button"
              onClick={toggleGrowwOrders}
              disabled={busy}
              className={clsx(
                "ml-auto text-[11px] px-2 py-1 rounded-md border transition disabled:opacity-40",
                armed
                  ? "border-loss/40 text-loss hover:bg-loss/10"
                  : "border-bot/40 text-bot hover:bg-bot/10"
              )}
            >
              {armed ? "Stop real orders" : "Send orders to Groww"}
            </button>
          </div>
          {fromGroww && !armed && (
            <p className="text-[11px] text-slate-400 mb-2">
              This figure is your Groww cash. Buy and Sell still fill on the practice book until you confirm Send orders to Groww.
            </p>
          )}
          {account?.funds_error && (
            <p className="text-[11px] text-loss mb-2">{account.funds_error}</p>
          )}
          {walletError && <p className="text-[11px] text-loss mb-2">{walletError}</p>}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <Stat
              label={armed ? "Groww cash" : fromGroww ? "Groww cash · practice orders" : "Practice balance"}
              value={
                accountLoad !== "ok"
                  ? "—"
                  : account?.funds_error && account.balance === 0
                    ? "—"
                    : money(account?.balance)
              }
              tone="text-slate-100"
              big
            />
            <Stat
              label="Realised (all time)"
              value={accountLoad === "ok" ? money(account?.realised_all_time, true) : "—"}
              tone={pnlClass(account?.realised_all_time)}
            />
            <Stat label="Unrealised" value={money(openShown, true)} tone={pnlClass(openShown)} />
            <Stat
              label="Equity"
              value={accountLoad === "ok" && openShown != null ? money((account?.balance ?? 0) + openShown) : "—"}
            />
            <Stat
              label="Margin available"
              value={accountLoad === "ok" ? money(account?.margin_available) : "—"}
              sub={
                fromGroww
                  ? "MIS margin from Groww"
                  : account
                    ? `${account.max_leverage}× on balance`
                    : undefined
              }
              tone="text-bot"
            />
            <Stat
              label="Deployed"
              value={accountLoad === "ok" ? money(account?.open_exposure) : "—"}
              sub={account ? `${num(account.exposure_ratio)}× capital` : undefined}
            />
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
          {/* Watchlist */}
          <div className="lg:col-span-2 rounded-card border border-border bg-surface overflow-hidden">
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
                        {watchLoad === "error"
                          ? "The watchlist did not load. This is not an empty list."
                          : watchLoad === "loading"
                            ? "Loading the watchlist…"
                            : "No symbols on this watchlist."}
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
              <div className="rounded-card border border-border bg-surface p-6 text-center text-xs text-slate-500">
                Pick a symbol from the watchlist to open an order ticket.
              </div>
            )}
          </div>
        </div>

        {/* Positions */}
        <div className="rounded-card border border-border bg-surface overflow-hidden">
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
                  <th className="px-3 py-2 font-medium text-right">Amount</th>
                  <th className="px-3 py-2 font-medium text-right">SL / Target</th>
                  <th className="px-3 py-2 font-medium text-right">P&amp;L</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center text-xs text-slate-500">
                      {positionsLoad === "error"
                        ? "Positions did not load. This is not an empty desk."
                        : positionsLoad === "loading"
                          ? "Loading positions…"
                          : "No open positions on the desk."}
                    </td>
                  </tr>
                )}
                {positions.map((p) => (
                  <tr key={p.symbol} className="border-b border-border/50 last:border-0">
                    <td className="px-4 py-2 text-slate-100">
                      <span className="inline-flex items-center gap-1">
                        {p.symbol}
                        {p.on_groww === false && (
                          <span
                            className="text-[9px] px-1 py-0.5 rounded bg-slate-700/50 text-slate-400"
                            title="This row is on the practice book. Closing it does not send an order to Groww."
                          >
                            PRACTICE
                          </span>
                        )}
                      </span>
                    </td>
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
        <div className="rounded-card border border-border bg-surface overflow-hidden">
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
                  <th className="px-3 py-2 font-medium text-right">Amount</th>
                  <th className="px-3 py-2 font-medium text-right">Exit</th>
                  <th className="px-3 py-2 font-medium text-right">SL</th>
                  <th className="px-3 py-2 font-medium text-right">Target</th>
                  <th className="px-3 py-2 font-medium text-right">P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 && (
                  <tr>
                    <td colSpan={10} className="px-4 py-8 text-center text-xs text-slate-500">
                      {historyLoad === "error"
                        ? "Trade history did not load. This is not an empty book."
                        : historyLoad === "loading"
                          ? "Loading trades…"
                          : "No closed trades on the desk yet."}
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
                        {t.closed_at ? `${istTime(t.closed_at, true)} IST` : "—"}
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
                      <td className="px-3 py-2 font-mono text-xs text-right text-slate-400">
                        {money(t.amount ?? t.entry_price * t.quantity)}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-right">
                        {voided ? "—" : t.exit_price !== null ? num(t.exit_price) : "—"}
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-right text-loss">
                        {t.stop_loss ? num(t.stop_loss) : "—"}
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-right text-profit">
                        {t.target ? num(t.target) : "—"}
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
    <div className="rounded-card border border-border bg-surface px-3 py-2.5">
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
