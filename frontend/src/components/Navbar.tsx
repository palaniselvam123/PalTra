"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  AlertTriangle,
  Bell,
  BellOff,
  CandlestickChart,
  FileBarChart,
  LineChart,
  Radar,
  Settings,
  LayoutDashboard,
  Wifi,
  WifiOff,
  ShieldCheck,
  Radio,
  FlaskConical,
  Sun,
  Moon,
} from "lucide-react";
import clsx from "clsx";
import { api, type FeedStatus } from "@/lib/api";
import { useNotificationCenter } from "@/components/Notifications/NotificationProvider";
import { useTheme } from "@/hooks/useTheme";

const SESSION_STYLES: Record<string, string> = {
  OPEN: "bg-profit/20 text-profit",
  PRE_OPEN: "bg-amber-500/20 text-amber-400",
  CLOSED: "bg-slate-700/40 text-slate-400",
  WEEKEND: "bg-slate-700/40 text-slate-400",
};

type Props = {
  connected: boolean;
  totalPnl: number;
  killSwitchActive: boolean;
  onKillSwitch: () => void;
  onResetKillSwitch: () => void;
  feed: FeedStatus | null;
  onFeedChanged: (f: FeedStatus) => void;
  botRunning: boolean;
};

export function Navbar({
  connected,
  totalPnl,
  killSwitchActive,
  onKillSwitch,
  onResetKillSwitch,
  feed,
  onFeedChanged,
  botRunning,
}: Props) {
  const pathname = usePathname();
  const notif = useNotificationCenter();
  const { theme, toggle } = useTheme();
  const [switching, setSwitching] = useState(false);
  const [feedError, setFeedError] = useState<string | null>(null);

  const pnlColor = totalPnl > 0 ? "text-profit" : totalPnl < 0 ? "text-loss" : "text-slate-400";

  const switchSource = async (source: "simulated" | "live") => {
    if (feed?.source === source) return;
    setSwitching(true);
    setFeedError(null);
    try {
      onFeedChanged(await api.setFeedSource(source));
    } catch (e: any) {
      setFeedError(e.message ?? "Could not switch data source");
    } finally {
      setSwitching(false);
    }
  };

  return (
    <header className="border-b border-border bg-surface/60 backdrop-blur sticky top-0 z-20">
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-3 flex-wrap">
        <span className="font-semibold text-slate-100 tracking-tight">ORB Intraday Bot</span>

        <nav className="flex items-center gap-1 text-sm">
          <NavLink href="/" active={pathname === "/"} icon={<LayoutDashboard size={15} />} label="Dashboard" />
          <NavLink href="/chart" active={pathname === "/chart"} icon={<LineChart size={15} />} label="Charts" />
          <NavLink href="/trade" active={pathname === "/trade"} icon={<CandlestickChart size={15} />} label="Trade" />
          <NavLink href="/scanner" active={pathname === "/scanner"} icon={<Radar size={15} />} label="Scanner" />
          <NavLink href="/reports" active={pathname === "/reports"} icon={<FileBarChart size={15} />} label="Reports" />
          <NavLink href="/settings" active={pathname === "/settings"} icon={<Settings size={15} />} label="Settings" />
        </nav>

        <div className="flex items-center gap-1.5 text-xs px-2 py-1 rounded-full border border-border">
          {connected ? <Wifi size={13} className="text-bot" /> : <WifiOff size={13} className="text-loss" />}
          <span className={connected ? "text-bot" : "text-loss"}>{connected ? "Streaming" : "Disconnected"}</span>
        </div>

        {/* Data source: what prices we use. Separate from execution by design. */}
        <div className="flex items-center rounded-full border border-border overflow-hidden text-[11px]">
          <button
            onClick={() => switchSource("simulated")}
            disabled={switching || botRunning}
            title={botRunning ? "Stop the bot before switching data source" : "Synthetic prices — works any hour"}
            className={clsx(
              "flex items-center gap-1 px-2.5 py-1.5 transition",
              feed?.source === "simulated" ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200",
              (switching || botRunning) && "opacity-50 cursor-not-allowed"
            )}
          >
            <FlaskConical size={12} /> SIMULATED
          </button>
          <button
            onClick={() => switchSource("live")}
            disabled={switching || botRunning}
            title={botRunning ? "Stop the bot before switching data source" : "Real NSE quotes via Groww"}
            className={clsx(
              "flex items-center gap-1 px-2.5 py-1.5 transition",
              feed?.source === "live" ? "bg-profit/20 text-profit" : "text-slate-400 hover:text-slate-200",
              (switching || botRunning) && "opacity-50 cursor-not-allowed"
            )}
          >
            <Radio size={12} /> LIVE NSE
          </button>
        </div>

        {feed && (
          <span className={clsx("text-[11px] px-2 py-1 rounded-full", SESSION_STYLES[feed.session] ?? "")}>
            {feed.session.replace("_", "-")}
          </span>
        )}

        {/* Execution is virtual, permanently. This is a status badge, not a toggle. */}
        <span
          className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-full bg-profit/10 text-profit border border-profit/30"
          title="Orders are always simulated. No real money is ever sent to the broker."
        >
          <ShieldCheck size={12} /> VIRTUAL MONEY
        </span>

        <button
          onClick={() => (notif.enabled ? notif.disable() : notif.enable())}
          disabled={notif.permission === "unsupported"}
          title={
            notif.permission === "unsupported"
              ? "This browser does not support notifications"
              : notif.enabled
              ? notif.permission === "granted"
                ? "Trade alerts on — desktop notifications and in-app toasts"
                : "Trade alerts on, but the browser blocked desktop notifications — showing in-app toasts only"
              : "Turn on alerts for every entry and exit, with the reason"
          }
          className={clsx(
            "flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-full border transition",
            notif.enabled
              ? notif.permission === "granted"
                ? "border-bot/40 bg-bot/10 text-bot"
                : "border-amber-500/40 bg-amber-500/10 text-amber-400"
              : "border-border text-slate-400 hover:text-slate-200",
            notif.permission === "unsupported" && "opacity-40 cursor-not-allowed"
          )}
        >
          {notif.enabled ? <Bell size={12} /> : <BellOff size={12} />}
          ALERTS
        </button>

        <button
          onClick={toggle}
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          className="flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-full border border-border text-slate-400 hover:text-slate-200 transition"
        >
          {theme === "dark" ? <Sun size={12} /> : <Moon size={12} />}
          {theme === "dark" ? "LIGHT" : "DARK"}
        </button>

        <div className={clsx("font-mono text-sm ml-auto", pnlColor)}>
          P&amp;L: {totalPnl >= 0 ? "+" : ""}
          {totalPnl.toFixed(2)}
        </div>

        {killSwitchActive ? (
          <button
            onClick={onResetKillSwitch}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-slate-700 hover:bg-slate-600 text-xs font-semibold"
          >
            <AlertTriangle size={14} /> RESET KILL SWITCH
          </button>
        ) : (
          <button
            onClick={onKillSwitch}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-loss hover:bg-loss/80 text-white text-xs font-semibold"
          >
            <AlertTriangle size={14} /> KILL SWITCH
          </button>
        )}

        {(feedError || feed?.error) && (
          <div className="w-full text-[11px] text-loss">{feedError ?? feed?.error}</div>
        )}
      </div>

      {feed?.source === "simulated" && (
        <div className="bg-amber-500/15 border-t border-amber-500/30 px-4 py-2">
          <div className="max-w-7xl mx-auto flex items-center gap-2 text-[12px] text-amber-300">
            <FlaskConical size={14} className="shrink-0" />
            <span>
              <strong>SIMULATED PRICES — not real market data.</strong> These values are randomly generated and do not
              match NSE/BSE. Switch to LIVE NSE for real quotes.
            </span>
          </div>
        </div>
      )}

      {feed?.source === "live" && !feed.market_open && (
        <div className="bg-amber-500/10 border-t border-amber-500/25 px-4 py-2">
          <div className="max-w-7xl mx-auto text-[12px] text-amber-300">
            Live NSE data — market is {feed.session.replace("_", "-").toLowerCase()}. Quotes are frozen at last close,
            so new entries are blocked until 09:15 IST. Existing brackets still track.
          </div>
        </div>
      )}

      {feed?.source === "live" && feed.market_open && feed.stale && (
        <div className="bg-loss/10 border-t border-loss/30 px-4 py-2">
          <div className="max-w-7xl mx-auto text-[12px] text-loss">
            Live feed looks stale — no price change recently. Entries are blocked until it recovers.
          </div>
        </div>
      )}
    </header>
  );
}

function NavLink({ href, active, icon, label }: { href: string; active: boolean; icon: React.ReactNode; label: string }) {
  return (
    <Link
      href={href}
      className={clsx(
        "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md",
        active ? "bg-white/5 text-slate-100" : "text-slate-400 hover:text-slate-200"
      )}
    >
      {icon}
      {label}
    </Link>
  );
}
