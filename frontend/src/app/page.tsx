"use client";

import { useEffect, useMemo, useState } from "react";
import { Navbar } from "@/components/Navbar";
import { MetricCards } from "@/components/Dashboard/MetricCards";
import { PositionsTable } from "@/components/Dashboard/PositionsTable";
import { AdvancedChart } from "@/components/Chart/AdvancedChart";
import { LiveConsole } from "@/components/Dashboard/LiveConsole";
import { SymbolStrip } from "@/components/Dashboard/SymbolStrip";
import { PlaceOrderForm } from "@/components/Dashboard/PlaceOrderForm";
import { BotControl } from "@/components/Dashboard/BotControl";
import { AccountBalance } from "@/components/Dashboard/AccountBalance";
import { TradeHistory } from "@/components/Dashboard/TradeHistory";
import { AiExpertPanel } from "@/components/Dashboard/AiExpertPanel";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type DeskPosition } from "@/lib/api";
import type { ClosedTrade } from "@/components/Dashboard/TradeHistory";

export default function DashboardPage() {
  const {
    connected,
    ticks,
    logs,
    positions,
    killSwitchActive,
    killSwitch,
    resetKillSwitch,
    summary,
    bot,
    setBot,
    history,
    account,
    accountLoad,
    positionsLoad,
    historyLoad,
    summaryLoad,
    feed,
    setFeed,
    refreshPositions,
    refreshSummary,
  } = useTradingState();

  const [deskPositions, setDeskPositions] = useState<DeskPosition[]>([]);
  const [deskHistory, setDeskHistory] = useState<ClosedTrade[]>([]);
  const [deskPosLoad, setDeskPosLoad] = useState<"loading" | "ok" | "error">("loading");
  const [deskHistLoad, setDeskHistLoad] = useState<"loading" | "ok" | "error">("loading");
  useEffect(() => {
    let busy = false;
    const load = () => {
      if (busy) return;
      busy = true;
      const done = () => {
        busy = false;
      };
      api
        .deskPositions()
        .then((rows) => {
          setDeskPositions(rows);
          setDeskPosLoad("ok");
        })
        .catch(() => setDeskPosLoad((prev) => (prev === "ok" ? "ok" : "error")))
        .finally(done);
      api
        .deskHistory()
        .then((rows) => {
          setDeskHistory(
            rows
              .filter((t) => t.status === "CLOSED")
              .map((t) => ({
                id: t.id + 1_000_000,
                symbol: t.symbol,
                side: t.side,
                quantity: t.quantity,
                entry_price: t.entry_price,
                exit_price: t.exit_price,
                stop_loss: t.stop_loss,
                target: t.target,
                amount: t.amount,
                pnl: t.pnl,
                closed_at: t.closed_at,
              }))
          );
          setDeskHistLoad("ok");
        })
        .catch(() => setDeskHistLoad((prev) => (prev === "ok" ? "ok" : "error")));
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  const symbols = useMemo(() => Object.keys(ticks).slice(0, 10), [ticks]);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const activeSymbol = selectedSymbol ?? symbols[0];
  const activePosition = positions.find((p) => p.symbol === activeSymbol);

  const capitalDeployed = positions.reduce((sum, p) => sum + p.entry_price * p.quantity, 0);

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

      {/* A trading desk is a main workspace plus a rail, not a 50/50 split.
          Previously the chart shared one row equally with the event log, so
          the most important element on the page got half the width and an
          almost-always-empty console got the other half. The chart now leads
          a two-thirds workspace; status and controls stack in the rail. */}
      <main className="mx-auto max-w-[1720px] space-y-4 px-5 py-5">
        <MetricCards
          totalPnl={summaryLoad === "ok" ? summary.total_pnl : null}
          winRatePct={summary.win_rate_pct}
          profitFactor={summary.profit_factor}
          maxDrawdown={summary.max_drawdown}
          capitalDeployed={capitalDeployed}
          history={history}
          accountCapital={account?.starting_capital}
          byFeed={summary.by_feed}
        />

        <SymbolStrip symbols={symbols} ticks={ticks} active={activeSymbol} onSelect={setSelectedSymbol} />

        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-3">
          {/* --- workspace ------------------------------------------------ */}
          <div className="space-y-4 xl:col-span-2">
            {activeSymbol && (
              <AdvancedChart
                symbol={activeSymbol}
                symbols={Object.keys(ticks).sort()}
                onSymbolChange={setSelectedSymbol}
                tick={ticks[activeSymbol]}
                stopLoss={activePosition?.stop_loss}
                target={activePosition?.target}
                height={560}
              />
            )}

            <PositionsTable
              emptyTitle={
                deskPosLoad === "error" || positionsLoad === "error"
                  ? "Positions did not load"
                  : deskPosLoad === "loading" || positionsLoad === "loading"
                    ? "Loading positions…"
                    : "No open positions"
              }
              emptyHint={
                deskPosLoad === "error" || positionsLoad === "error"
                  ? "The request did not answer. This is not an empty account."
                  : deskPosLoad === "loading" || positionsLoad === "loading"
                    ? "Still waiting on the desk."
                    : "Entries opened by the bot or the manual desk appear here with live P&L and their bracket levels."
              }
              positions={[
                ...deskPositions.map((d) => ({
                  symbol: d.symbol,
                  side: d.side,
                  quantity: d.quantity,
                  entry_price: d.entry_price,
                  stop_loss: d.stop_loss,
                  target: d.target,
                  order_id: d.order_id,
                  ltp: d.ltp,
                })),
                ...positions.filter((p) => !deskPositions.some((d) => d.symbol === p.symbol)),
              ]}
              ticks={ticks}
              onClose={async (symbol) => {
                if (deskPositions.some((d) => d.symbol === symbol)) await api.deskClose(symbol);
                else await api.closePosition(symbol);
                refreshPositions();
                refreshSummary();
              }}
            />

            <TradeHistory
              trades={[...deskHistory, ...history]}
              emptyLabel={
                deskHistLoad === "error" || historyLoad === "error"
                  ? "Trades did not load. This is not an empty book."
                  : deskHistLoad === "loading" || historyLoad === "loading"
                    ? "Loading trades…"
                    : "No closed trades yet."
              }
            />
          </div>

          {/* --- rail: status, then controls ------------------------------ */}
          <div className="space-y-4">
            <AccountBalance account={account} loadState={accountLoad} onCapitalChanged={refreshSummary} />
            <BotControl bot={bot} onChanged={setBot} />
            <LiveConsole logs={logs} />
            <PlaceOrderForm
              symbols={symbols}
              ticks={ticks}
              feed={feed}
              killSwitchActive={killSwitchActive}
              onOrderPlaced={() => {
                refreshPositions();
                refreshSummary();
              }}
            />
            <AiExpertPanel symbols={symbols} activeSymbol={activeSymbol} />
          </div>
        </div>
      </main>
    </div>
  );
}
