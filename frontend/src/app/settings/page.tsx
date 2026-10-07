"use client";

import { Navbar } from "@/components/Navbar";
import { ApiKeyForm } from "@/components/Settings/ApiKeyForm";
import { AiKeyForm } from "@/components/Settings/AiKeyForm";
import { RiskSettings } from "@/components/Settings/RiskSettings";
import { MarketDataDiagnostics } from "@/components/Settings/MarketDataDiagnostics";
import { SmaStrategySettings } from "@/components/Settings/SmaStrategySettings";
import { WhatsAppAlerts } from "@/components/Terminal/WhatsAppAlerts";
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
        <nav aria-label="Settings sections" className="flex flex-wrap gap-1.5 text-xs">
          {[
            ["#sma-strategy", "SMA strategy"],
            ["#trade-alerts", "Trade alerts"],
            ["#broker-keys", "Broker keys"],
            ["#ai-key", "AI expert"],
            ["#diagnostics", "Diagnostics"],
            ["#risk", "Risk"],
          ].map(([href, label]) => (
            <a
              key={href}
              href={href}
              className="rounded-full border border-border px-3 py-1.5 text-slate-300 hover:bg-white/[0.05] hover:text-slate-100"
            >
              {label}
            </a>
          ))}
        </nav>
        <SmaStrategySettings />
        <section id="trade-alerts" aria-label="Trade alerts" className="terminal-dark scroll-mt-20">
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
      </main>
    </div>
  );
}
