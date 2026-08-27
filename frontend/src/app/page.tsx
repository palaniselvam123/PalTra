"use client";

import { useMemo, useState } from "react";
import { Navbar } from "@/components/Navbar";
import { MetricCards } from "@/components/Dashboard/MetricCards";
import { PositionsTable } from "@/components/Dashboard/PositionsTable";
import { AdvancedChart } from "@/components/Chart/AdvancedChart";
import { LiveConsole } from "@/components/Dashboard/LiveConsole";
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

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        <MetricCards
          totalPnl={summary.total_pnl}
          winRatePct={summary.win_rate_pct}
          profitFactor={summary.profit_factor}
          maxDrawdown={summary.max_drawdown}
          capitalDeployed={capitalDeployed}
        />

        <div className="flex items-center gap-2 flex-wrap">
          {symbols.map((s) => (
            <button
              key={s}
              onClick={() => setSelectedSymbol(s)}
              className={`px-2.5 py-1 rounded-md text-xs font-mono border ${
                s === activeSymbol ? "border-bot text-bot bg-bot/10" : "border-border text-slate-400 hover:text-slate-200"
              }`}
            >
              {s} {ticks[s]?.ltp.toFixed(2)}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {activeSymbol && (
            <AdvancedChart
              symbol={activeSymbol}
              symbols={Object.keys(ticks).sort()}
              onSymbolChange={setSelectedSymbol}
              tick={ticks[activeSymbol]}
              stopLoss={activePosition?.stop_loss}
              target={activePosition?.target}
            />
          )}
          <LiveConsole logs={logs} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
          <div className="lg:col-span-1 space-y-4">
            <AccountBalance account={account} />
            <BotControl bot={bot} onChanged={setBot} />
            <AiExpertPanel symbols={symbols} activeSymbol={activeSymbol} />
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
          </div>
          <div className="lg:col-span-2 space-y-4">
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
        </div>
      </main>
    </div>
  );
}
