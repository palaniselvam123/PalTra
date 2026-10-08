"use client";

import { WalletChip } from "@/components/Wallet/WalletChip";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Activity,
  AlertTriangle,
  Bell,
  BellOff,
  CandlestickChart,
  FileBarChart,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LineChart,
  MoreHorizontal,
  Radar,
  Radio,
  Settings,
  ShieldCheck,
  Sun,
  Moon,
  Zap,
} from "lucide-react";
import clsx from "clsx";
import { api, type FeedStatus } from "@/lib/api";
import { useNotificationCenter } from "@/components/Notifications/NotificationProvider";
import { useTheme } from "@/hooks/useTheme";
import { Badge, Button, StatusDot } from "@/components/ui";

/**
 * Application chrome, organised into three zones of deliberately different
 * visual weight. The previous version put thirteen controls in one wrapping
 * flex row at near-identical weight, so a destination, a status readout and a
 * destructive action all looked like the same kind of thing — and the row
 * wrapped onto two cramped lines on every page.
 *
 *   [ brand · navigation ]        [ live state ]   [ P&L · kill switch ]
 *      quiet, underline            muted, dots       loud, the only
 *      active marker               not pills         filled button
 *
 * Set-once preferences (theme, alerts) moved into an overflow menu: they are
 * configuration, not per-session controls, and they were costing two chips of
 * permanent attention.
 */

export const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/terminal", label: "SMA Terminal", icon: Activity },
  { href: "/chart", label: "Charts", icon: LineChart },
  { href: "/trade", label: "Trade", icon: CandlestickChart },
  { href: "/movers", label: "Movers", icon: Zap },
  { href: "/scalp", label: "Scalp", icon: Gauge },
  { href: "/scanner", label: "Scanner", icon: Radar },
  { href: "/reports", label: "Reports", icon: FileBarChart },
  { href: "/settings", label: "Settings", icon: Settings },
];

const SESSION_TONE: Record<string, "profit" | "warn" | "neutral"> = {
  OPEN: "profit",
  PRE_OPEN: "warn",
  CLOSED: "neutral",
  WEEKEND: "neutral",
};

type Props = {
  connected: boolean;
  totalPnl: number | null;
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
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuBox, setMenuBox] = useState<{ top: number; right: number } | null>(null);
  const [feedSlow, setFeedSlow] = useState(false);
  const [deskLive, setDeskLive] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (feed) return;
    const id = setTimeout(() => setFeedSlow(true), 8000);
    return () => clearTimeout(id);
  }, [feed]);

  useEffect(() => {
    let stop = false;
    const pull = () => {
      api
        .deskAccount()
        .then((row) => {
          if (!stop) setDeskLive(row.execution === "groww");
        })
        .catch(() => undefined);
    };
    pull();
    const id = setInterval(pull, 15000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || menuButtonRef.current?.contains(target)) return;
      setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  const signOut = () => {
    fetch("/api/session/logout", { method: "POST" }).finally(() => {
      window.location.href = "/login/";
    });
  };

  const toggleMenu = () => {
    if (menuOpen) {
      setMenuOpen(false);
      return;
    }
    const rect = menuButtonRef.current?.getBoundingClientRect();
    if (rect) {
      setMenuBox({ top: rect.bottom + 6, right: Math.max(8, window.innerWidth - rect.right) });
    }
    setMenuOpen(true);
  };

  const linkLabel = !feed
    ? feedSlow
      ? "No reply"
      : "Checking…"
    : feed.source === "simulated"
      ? "Sim"
      : feed.market_open && connected
        ? "Live"
        : "Quotes";
  const linkTone =
    linkLabel === "Live" ? "info" : linkLabel === "Quotes" || linkLabel === "Sim" ? "warn" : linkLabel === "No reply" ? "loss" : "neutral";

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

  const pnlColor =
    totalPnl == null ? "text-slate-500" : totalPnl > 0 ? "text-profit" : totalPnl < 0 ? "text-loss" : "text-slate-400";
  const sourceLocked = switching || botRunning;

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-surface/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1720px] items-center gap-2 px-3 sm:h-16 sm:gap-6 sm:px-5">
        {/* --- Zone 1: identity + navigation ---------------------------- */}
        <Link href="/" className="flex shrink-0 items-center gap-2">
          <span
            className="grad-brand grid h-8 w-8 place-items-center rounded-full text-white shadow-sm"
          >
            <CandlestickChart size={16} />
          </span>
          <span className="grad-text hidden text-title font-bold sm:inline">ORB Desk</span>
        </Link>

        <nav className="hidden min-w-0 items-center gap-3 self-stretch xl:flex 2xl:gap-5">
          {NAV.map(({ href, label }) => {
            const active = isActive(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                prefetch={false}
                className={clsx(
                  "relative whitespace-nowrap px-1 py-5 text-body font-medium transition-colors",
                  active ? "text-slate-100" : "text-slate-400 hover:text-slate-100"
                )}
              >
                {label}
                {/* A green rule under the active item, flush with the bar's
                    bottom edge — the reference's only navigation affordance. */}
                {active && <span className="absolute inset-x-0 bottom-0 h-[3px] rounded-t bg-bot" />}
              </Link>
            );
          })}
        </nav>

        {/* --- Zone 2: live state, deliberately quiet -------------------- */}
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <div className="flex items-center gap-3">
            <span
              className="flex items-center gap-1.5"
              title={
                linkLabel === "Live"
                  ? "Market is open and NSE quotes are connected"
                  : linkLabel === "Sim"
                    ? "These prices are simulated. Switch to NSE for the real tape."
                  : linkLabel === "Quotes"
                    ? feed?.market_open
                      ? "Prices are loaded. The live socket is still reconnecting."
                      : "Quotes are the last close. The market is not in session."
                    : linkLabel === "No reply"
                      ? "Status did not answer. This is not a disconnected broker."
                      : "Checking the market status…"
              }
            >
              <StatusDot tone={linkTone} pulse={linkLabel === "Live"} />
              <span className="text-caption text-slate-400">{linkLabel}</span>
            </span>

            <span className="hidden h-3 w-px bg-border 2xl:block" />
            <div className="hidden items-center gap-3 2xl:flex">

            {/* Data source is a real choice, so it stays a control — but as a
                quiet segmented pair rather than two competing colour chips. */}
            <div className="flex items-center gap-0.5">
              {(["simulated", "live"] as const).map((src) => (
                <button
                  key={src}
                  onClick={() => switchSource(src)}
                  disabled={sourceLocked}
                  title={
                    botRunning
                      ? "Stop the bot before switching data source"
                      : src === "simulated"
                        ? "Synthetic prices — works any hour"
                        : "Real NSE quotes via Groww"
                  }
                  className={clsx(
                    "flex items-center gap-1 rounded px-1.5 py-0.5 text-caption transition-colors",
                    feed?.source === src
                      ? "bg-white/[0.07] text-slate-100"
                      : "text-slate-500 hover:text-slate-300",
                    sourceLocked && "cursor-not-allowed opacity-50"
                  )}
                >
                  {src === "simulated" ? <FlaskConical size={11} /> : <Radio size={11} />}
                  {src === "simulated" ? "Sim" : "NSE"}
                </button>
              ))}
            </div>

            {feed && (
              <>
                <span className="h-3 w-px bg-border" />
                <span className="flex items-center gap-1.5" title="NSE session">
                  <StatusDot tone={SESSION_TONE[feed.session] ?? "neutral"} />
                  <span className="text-caption text-slate-400">{feed.session.replace("_", "-")}</span>
                </span>
              </>
            )}
            </div>
          </div>

          <Badge
            tone={deskLive ? "loss" : "profit"}
            icon={<ShieldCheck size={11} />}
            className="hidden 2xl:inline-flex"
            title={
              deskLive
                ? "The next order on the Trade page is sent to Groww. A practice position already on the desk is not."
                : "Practice orders until you confirm Send orders to Groww on the Trade page. Groww cash can still be shown."
            }
          >
            {deskLive ? "LIVE ORDERS" : "PRACTICE"}
          </Badge>

          {/* --- Zone 3: the number, and the one destructive action ------ */}
          <div className="flex items-center gap-1 pl-1">
            <WalletChip className="mr-1" />
            <div className="text-right leading-tight">
              <div className="text-[9px] uppercase tracking-[0.08em] text-slate-500">P&amp;L</div>
              <div className={clsx("font-mono text-body font-semibold tabular-nums", pnlColor)}>
                {totalPnl == null ? "—" : `${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}`}
              </div>
            </div>

            {killSwitchActive ? (
              <Button variant="secondary" size="sm" icon={<AlertTriangle size={13} />} onClick={onResetKillSwitch}>
                Reset
              </Button>
            ) : (
              <Button variant="danger" size="sm" icon={<AlertTriangle size={13} />} onClick={onKillSwitch}>
                Kill
              </Button>
            )}

            {/* Overflow: preferences, not per-session controls. The menu is
                portaled so a short header cannot clip it. */}
            <div className="relative">
              <span ref={menuButtonRef} className="inline-flex">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={toggleMenu}
                  aria-label="More options"
                  aria-expanded={menuOpen}
                  className="px-1.5"
                >
                  <MoreHorizontal size={16} />
                </Button>
              </span>

              {menuOpen &&
                menuBox &&
                createPortal(
                  <div
                    ref={menuRef}
                    style={{ position: "fixed", top: menuBox.top, right: menuBox.right, zIndex: 80 }}
                    className="w-52 overflow-hidden rounded-card border border-border bg-surface shadow-pop"
                  >
                    {/* Phones and tablets: the Sim / NSE switch is hidden in the bar. */}
                    <div className="border-b border-border px-3 py-2 2xl:hidden">
                      <div className="mb-1.5 text-caption text-slate-500">Data source</div>
                      <div className="grid grid-cols-2 gap-1.5" role="group" aria-label="Data source">
                        {(["simulated", "live"] as const).map((src) => (
                          <button
                            key={src}
                            type="button"
                            aria-pressed={feed?.source === src}
                            onClick={() => switchSource(src)}
                            disabled={sourceLocked}
                            className={clsx(
                              "flex min-h-11 items-center justify-center gap-1.5 rounded-md text-body font-medium transition-colors",
                              feed?.source === src
                                ? "bg-bot/15 text-bot ring-1 ring-inset ring-bot/40"
                                : "text-slate-300 ring-1 ring-inset ring-border hover:bg-white/[0.05]",
                              sourceLocked && "cursor-not-allowed opacity-50"
                            )}
                          >
                            {src === "simulated" ? <FlaskConical size={14} /> : <Radio size={14} />}
                            {src === "simulated" ? "Sim" : "NSE"}
                          </button>
                        ))}
                      </div>
                      <div className="mt-1.5 text-caption tracking-normal text-slate-500">
                        {botRunning
                          ? "Stop the bot before switching."
                          : feed?.source === "live"
                            ? "Real NSE quotes from Groww."
                            : "Made-up prices that work any hour."}
                      </div>
                      {feedError ? <div className="mt-1 text-caption text-loss">{feedError}</div> : null}
                    </div>
                    <MenuItem
                      icon={notif.enabled ? <Bell size={14} /> : <BellOff size={14} />}
                      label={notif.enabled ? "Trade alerts on" : "Trade alerts off"}
                      hint={
                        notif.permission === "unsupported"
                          ? "Not supported in this browser"
                          : notif.enabled && notif.permission !== "granted"
                            ? "Browser blocked desktop popups"
                            : undefined
                      }
                      disabled={notif.permission === "unsupported"}
                      onClick={() => (notif.enabled ? notif.disable() : notif.enable())}
                    />
                    <MenuItem
                      icon={theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
                      label={theme === "dark" ? "Light theme" : "Dark theme"}
                      onClick={toggle}
                    />
                    <MenuItem
                      icon={<ShieldCheck size={14} />}
                      label="Sign out"
                      hint="Locks the desk on this browser"
                      onClick={signOut}
                    />
                    <div className="border-t border-border px-3 py-2 2xl:hidden">
                      <Badge tone={deskLive ? "loss" : "profit"} icon={<ShieldCheck size={11} />}>
                        {deskLive ? "LIVE ORDERS" : "PRACTICE"}
                      </Badge>
                    </div>
                  </div>,
                  document.body
                )}
            </div>
          </div>
        </div>
      </div>

      {/* Phone and tablet: the icon strip used to share one row with P&L,
          Kill, and Sign out, so it shrank to a single icon. The pages get
          their own scroll row instead. */}
      <nav
        aria-label="Pages"
        className="flex gap-1 overflow-x-auto border-t border-border px-3 py-1.5 xl:hidden"
      >
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              prefetch={false}
              className={clsx(
                "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-caption font-medium transition-colors",
                active ? "bg-bot/15 text-bot" : "text-slate-300 hover:bg-white/[0.05] hover:text-slate-100"
              )}
            >
              <Icon size={14} />
              {label}
            </Link>
          );
        })}
      </nav>

      {(feedError || feed?.error) && (
        <div className="border-t border-loss/30 bg-loss/10 px-4 py-1.5">
          <div className="mx-auto max-w-7xl text-caption tracking-normal text-loss">
            {feedError ?? feed?.error}
          </div>
        </div>
      )}

      {feed?.source === "simulated" && (
        <FeedBanner tone="warn" icon={<FlaskConical size={13} />}>
          <strong className="font-semibold">Simulated prices — not real market data.</strong> Randomly
          generated; they do not match NSE/BSE. Switch to NSE for real quotes.
        </FeedBanner>
      )}

      {feed?.source === "live" && !feed.market_open && (
        <FeedBanner tone="warn">
          Market is {feed.session.replace("_", "-").toLowerCase()} — quotes are the last close. Practice buys
          still fill here. Real Groww orders wait until 09:15 IST.
        </FeedBanner>
      )}

      {feed?.source === "live" && feed.market_open && feed.stale && (
        <FeedBanner tone="loss">
          Live feed looks stale — no price change recently. Entries are blocked until it recovers.
        </FeedBanner>
      )}
    </header>
  );
}

/** The current page's link: "/" only on the dashboard, others also on their sub-pages. */
export function isActive(pathname: string | null, href: string): boolean {
  const path = (pathname ?? "/").replace(/\/+$/, "") || "/";
  if (href === "/") return path === "/";
  return path === href || path.startsWith(`${href}/`);
}

function FeedBanner({
  children,
  tone,
  icon,
}: {
  children: React.ReactNode;
  tone: "warn" | "loss";
  icon?: React.ReactNode;
}) {
  return (
    <div
      className={clsx(
        "border-t px-4 py-1.5",
        tone === "warn" ? "border-amber-500/25 bg-amber-500/10" : "border-loss/25 bg-loss/10"
      )}
    >
      <div
        className={clsx(
          "mx-auto flex max-w-7xl items-center gap-2 text-caption leading-relaxed tracking-normal",
          tone === "warn" ? "text-amber-400" : "text-loss"
        )}
      >
        {icon && <span className="shrink-0">{icon}</span>}
        <span>{children}</span>
      </div>
    </div>
  );
}

function MenuItem({
  icon,
  label,
  hint,
  onClick,
  disabled,
}: {
  icon: React.ReactNode;
  label: string;
  hint?: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors",
        disabled ? "cursor-not-allowed opacity-45" : "hover:bg-white/[0.05]"
      )}
    >
      <span className="mt-0.5 text-slate-400">{icon}</span>
      <span className="min-w-0">
        <span className="block text-body text-slate-200">{label}</span>
        {hint && <span className="block text-caption tracking-normal text-slate-500">{hint}</span>}
      </span>
    </button>
  );
}
