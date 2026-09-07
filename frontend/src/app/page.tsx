"use client";

import { useMemo, useState } from "react";
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
import { api } from "@/lib/api";

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
    feed,
    setFeed,
    refreshPositions,
    refreshSummary,
  } = useTradingState();

  const symbols = useMemo(() => Object.keys(ticks).slice(0, 10), [ticks]);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const activeSymbol = selectedSymbol ?? symbols[0];
  const activePosition = positions.find((p) => p.symbol === activeSymbol);

  const capitalDeployed = positions.reduce((sum, p) => sum + p.entry_price * p.quantity, 0);

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

      {/* A trading desk is a main workspace plus a rail, not a 50/50 split.
          Previously the chart shared one row equally with the event log, so
          the most important element on the page got half the width and an
          almost-always-empty console got the other half. The chart now leads
          a two-thirds workspace; status and controls stack in the rail. */}
      <main className="mx-auto max-w-[1720px] space-y-4 px-5 py-5">
        <MetricCards
          totalPnl={summary.total_pnl}
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
              positions={positions}
              ticks={ticks}
              onClose={async (symbol) => {
                await api.closePosition(symbol);
                refreshPositions();
                refreshSummary();
              }}
            />

            <TradeHistory trades={history} />
          </div>

          {/* --- rail: status, then controls ------------------------------ */}
          <div className="space-y-4">
            <AccountBalance account={account} onCapitalChanged={refreshSummary} />
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
