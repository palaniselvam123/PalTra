"use client";

import { Navbar } from "@/components/Navbar";
import { ApiKeyForm } from "@/components/Settings/ApiKeyForm";
import { AiKeyForm } from "@/components/Settings/AiKeyForm";
import { RiskSettings } from "@/components/Settings/RiskSettings";
import { MarketDataDiagnostics } from "@/components/Settings/MarketDataDiagnostics";
import { AllBotsGuard } from "@/components/Settings/AllBotsGuard";
import { PnlAlerts } from "@/components/Settings/PnlAlerts";
import { FastMoveAlerts } from "@/components/Settings/FastMoveAlerts";
import { FastMoverBot } from "@/components/Settings/FastMoverBot";
import { NewsEntries } from "@/components/Settings/NewsEntries";
import { NewsAlerts } from "@/components/Settings/NewsAlerts";
import { NewsByDay } from "@/components/Settings/NewsByDay";
import { SmaStrategySettings } from "@/components/Settings/SmaStrategySettings";
import { SectionNav } from "@/components/Settings/SectionNav";
import { WhatsAppAlerts } from "@/components/Terminal/WhatsAppAlerts";
import { useTradingState } from "@/hooks/useTradingState";

const SECTIONS: [string, string][] = [
  ["all-bots-guard", "All-bots target"],
  ["pnl-alerts", "P&L alerts"],
  ["fast-moves", "Fast moves"],
  ["fast-mover-bot", "Fast movers → Bot 5"],
  ["news-entries", "News entries"],
  ["news-alerts", "News alerts"],
  ["sma-strategy", "SMA strategy"],
  ["trade-alerts", "Trade alerts"],
  ["broker-keys", "Broker keys"],
  ["ai-key", "AI expert"],
  ["diagnostics", "Diagnostics"],
  ["risk", "ORB desk risk"],
];

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
      <main className="mx-auto max-w-[1440px] space-y-4 px-4 py-6 lg:px-6">
        <SectionNav sections={SECTIONS} />
        <AllBotsGuard />
        <PnlAlerts />
        <FastMoveAlerts />
        <FastMoverBot />
        <NewsEntries />
        <NewsAlerts />
        <NewsByDay />
        <SmaStrategySettings />
        {/* The smaller forms sit two to a row on a wide screen instead of stretching across it. */}
        <div className="grid items-start gap-4 xl:grid-cols-2">
          <section id="trade-alerts" aria-label="Trade alerts" className="terminal-dark scroll-mt-20 xl:col-span-2">
            <h2 className="mb-2 text-base font-semibold text-slate-100">Trade alerts</h2>
            <WhatsAppAlerts />
          </section>
          <div id="broker-keys" className="scroll-mt-20">
            <ApiKeyForm />
          </div>
          <div id="ai-key" className="scroll-mt-20">
            <AiKeyForm />
          </div>
          <div id="diagnostics" className="scroll-mt-20">
            <MarketDataDiagnostics />
          </div>
          <div id="risk" className="scroll-mt-20">
            <RiskSettings />
          </div>
        </div>
      </main>
    </div>
  );
}
