"use client";

import { useEffect, useState, type ReactNode } from "react";
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
import { groupForHash, OnThisTab, rememberedGroup, SettingsTabs, type SettingsGroup } from "@/components/Settings/SettingsTabs";
import { WhatsAppAlerts } from "@/components/Terminal/WhatsAppAlerts";
import { useTradingState } from "@/hooks/useTradingState";

/**
 * One group of settings at a time (the whole page was a 9,000 px scroll). A link to a
 * section (`/settings/#trade-alerts`, `#sma-strategy`, `#sma-stop`) opens its tab and lands on it.
 */
const GROUPS: SettingsGroup[] = [
  { id: "strategy", label: "Strategy", sections: [["sma-strategy", "SMA strategy"]] },
  {
    id: "bots",
    label: "Bots",
    sections: [
      ["all-bots-guard", "All-bots target"],
      ["fast-mover-bot", "Fast movers → Bot 5"],
      ["news-entries", "News entries"],
    ],
  },
  {
    id: "alerts",
    label: "Alerts & news",
    sections: [
      ["trade-alerts", "Trade alerts"],
      ["pnl-alerts", "P&L alerts"],
      ["fast-moves", "Fast moves"],
      ["news-alerts", "News alerts"],
      ["news-by-day", "News on a date"],
    ],
  },
  {
    id: "account",
    label: "Account",
    sections: [
      ["broker-keys", "Broker keys"],
      ["ai-key", "AI expert"],
      ["diagnostics", "Diagnostics"],
      ["risk", "ORB desk risk"],
    ],
  },
];

export default function SettingsPage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();
  const [group, setGroup] = useState<SettingsGroup>(GROUPS[0]);

  // The tab in the address wins, then the last one used, then Strategy.
  useEffect(() => {
    const fromHash = () => groupForHash(GROUPS, window.location.hash);
    setGroup(fromHash() ?? rememberedGroup(GROUPS) ?? GROUPS[0]);
    const onHash = () => {
      const g = fromHash();
      if (g) setGroup(g);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // A section link lands on its section once the tab is on screen (the SMA form scrolls itself once loaded).
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id || id === group.id || id.startsWith("sma-")) return;
    requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView());
  }, [group]);

  const pick = (g: SettingsGroup) => {
    setGroup(g);
    const url = new URL(window.location.href);
    url.hash = g.id;
    window.history.replaceState(null, "", url.toString());
    window.scrollTo({ top: 0 });
  };

  const card = (id: string, node: ReactNode, cls = "") => (
    <div id={id} className={`scroll-mt-20 ${cls}`}>
      {node}
    </div>
  );

  let body: ReactNode;
  switch (group.id) {
    case "bots":
      body = (
        <>
          <AllBotsGuard />
          <FastMoverBot />
          <NewsEntries />
        </>
      );
      break;
    case "alerts":
      body = (
        <>
          <section id="trade-alerts" aria-label="Trade alerts" className="terminal-dark scroll-mt-20">
            <h2 className="mb-2 text-base font-semibold text-slate-100">Trade alerts</h2>
            <WhatsAppAlerts />
          </section>
          <PnlAlerts />
          <FastMoveAlerts />
          <NewsAlerts />
          <NewsByDay />
        </>
      );
      break;
    case "account":
      body = (
        // The smaller forms sit two to a row on a wide screen instead of stretching across it.
        <div className="grid items-start gap-4 xl:grid-cols-2">
          {card("broker-keys", <ApiKeyForm />)}
          {card("ai-key", <AiKeyForm />)}
          {card("diagnostics", <MarketDataDiagnostics />)}
          {card("risk", <RiskSettings />)}
        </div>
      );
      break;
    default:
      body = <SmaStrategySettings />;
  }

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
        <SettingsTabs groups={GROUPS} active={group.id} onPick={pick} />
        <div role="tabpanel" id={group.id} aria-label={group.label} className="space-y-4">
          <OnThisTab sections={group.sections} />
          {body}
        </div>
      </main>
    </div>
  );
}
