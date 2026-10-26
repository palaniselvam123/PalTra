"use client";

import { Navbar } from "@/components/Navbar";
import { NewsPage } from "@/components/News/NewsPage";
import { useTradingState } from "@/hooks/useTradingState";

export default function News() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();
  return (
    <div className="min-h-screen bg-base text-slate-100">
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
      <main className="mx-auto max-w-7xl px-4 py-6">
        <NewsPage />
      </main>
    </div>
  );
}
