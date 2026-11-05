"use client";

import { Navbar } from "@/components/Navbar";
import { PnlDashboard } from "@/components/PnlDashboard/PnlDashboard";
import { useTradingState } from "@/hooks/useTradingState";

/** Home page: the P&L dashboard (when to close everything). The ORB desk's old dashboard lives at /orb. */
export default function DashboardPage() {
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
      <main className="mx-auto max-w-[1720px] px-4 py-5">
        <PnlDashboard />
      </main>
    </div>
  );
}
