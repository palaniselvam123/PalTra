"use client";

import { Navbar } from "@/components/Navbar";
import { ApiKeyForm } from "@/components/Settings/ApiKeyForm";
import { AiKeyForm } from "@/components/Settings/AiKeyForm";
import { RiskSettings } from "@/components/Settings/RiskSettings";
import { MarketDataDiagnostics } from "@/components/Settings/MarketDataDiagnostics";
import { SmaStrategySettings } from "@/components/Settings/SmaStrategySettings";
import { useTradingState } from "@/hooks/useTradingState";

export default function SettingsPage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();

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
      <main className="max-w-4xl mx-auto px-4 py-6 space-y-4">
        <SmaStrategySettings />
        <ApiKeyForm />
        <AiKeyForm />
        <MarketDataDiagnostics />
        <RiskSettings />
      </main>
    </div>
  );
}
