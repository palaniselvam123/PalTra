"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AccountSummary, type BotStatus, type FeedStatus } from "@/lib/api";
import { useWebSocket } from "./useWebSocket";
import { useNotificationCenter } from "@/components/Notifications/NotificationProvider";
import type { ClosedTrade } from "@/components/Dashboard/TradeHistory";

export type Tick = { symbol: string; ltp: number; bid: number; ask: number; volume: number };
export type LogEntry = { id: number; level: string; message: string; at: string };
export type Position = {
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  stop_loss: number;
  target: number;
  order_id: string;
};
export type FeedPnl = {
  pnl: number;
  trades: number;
  win_rate_pct: number;
  profit_factor: number;
};

export type Summary = {
  total_pnl: number;
  trades_closed: number;
  win_rate_pct: number;
  profit_factor: number;
  max_drawdown: number;
  /** Split by price series — simulated fills say nothing about real edge. */
  by_feed?: Record<string, FeedPnl>;
};

const EMPTY_SUMMARY: Summary = { total_pnl: 0, trades_closed: 0, win_rate_pct: 0, profit_factor: 0, max_drawdown: 0 };

export function useTradingState() {
  const [connected, setConnected] = useState(false);
  const [ticks, setTicks] = useState<Record<string, Tick>>({});
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [mode, setModeState] = useState<"paper" | "live">("paper");
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [summary, setSummary] = useState<Summary>(EMPTY_SUMMARY);
  const [accountCapital, setAccountCapital] = useState(100_000);
  const [bot, setBot] = useState<BotStatus | null>(null);
  const [history, setHistory] = useState<ClosedTrade[]>([]);
  const [feed, setFeed] = useState<FeedStatus | null>(null);
  const [account, setAccount] = useState<AccountSummary | null>(null);
  const logIdRef = useRef(0);
  const noticeIdRef = useRef(0);
  const { notify } = useNotificationCenter();

  const refreshPositions = useCallback(() => {
    api.getPositions().then(setPositions).catch(() => {});
  }, []);

  // Every P&L figure on the dashboard is scoped to the feed currently
  // selected. Blending a synthetic price series with the real market into one
  // number is the most misleading thing this screen could do — a strategy can
  // read as profitable purely because the simulator drifted upward.
  const feedScope = feed?.source;

  const refreshSummary = useCallback(() => {
    api.getSummary(feedScope).then(setSummary).catch(() => {});
    api.getHistory().then(setHistory).catch(() => {});
    api.getAccount(feedScope).then(setAccount).catch(() => {});
  }, []);

  const refreshBot = useCallback(() => {
    api.getBotStatus().then(setBot).catch(() => {});
  }, []);

  const refreshFeed = useCallback(() => {
    api.getFeedStatus().then(setFeed).catch(() => {});
  }, []);

  useEffect(() => {
    api.getMode().then((m) => {
      setModeState(m.mode as "paper" | "live");
      setKillSwitchActive(m.kill_switch_active);
    }).catch(() => {});
    api.getRiskConfig().then((c) => setAccountCapital(c.account_capital)).catch(() => {});
    refreshPositions();
    refreshSummary();
    refreshBot();
    refreshFeed();
    const interval = setInterval(() => {
      refreshPositions();
      refreshSummary();
      refreshBot();
    }, 4000);
    return () => clearInterval(interval);
  }, [refreshPositions, refreshSummary, refreshBot, refreshFeed]);

  const { connected: wsConnected } = useWebSocket((msg) => {
    if (msg.channel === "tick") {
      const tick = msg.data as Tick;
      setTicks((prev) => ({ ...prev, [tick.symbol]: tick }));
    } else if (msg.channel === "log") {
      // Build the entry (and its id) out here: reading a mutable ref inside
      // the updater is impure, and with batched updates several queued
      // updaters would all read the same final id and collide as React keys.
      logIdRef.current += 1;
      const entry: LogEntry = {
        id: logIdRef.current,
        level: msg.data.level,
        message: msg.data.message,
        at: new Date().toLocaleTimeString(),
      };
      setLogs((prev) => [entry, ...prev].slice(0, 200));
    } else if (msg.channel === "order_filled") {
      refreshPositions();
    } else if (msg.channel === "position_closed") {
      refreshPositions();
      refreshSummary();
    } else if (msg.channel === "kill_switch") {
      setKillSwitchActive(msg.data.active);
      refreshBot();
    } else if (msg.channel === "bot_status") {
      setBot(msg.data as BotStatus);
    } else if (msg.channel === "notify") {
      noticeIdRef.current += 1;
      notify({ ...msg.data, id: noticeIdRef.current });
    } else if (msg.channel === "feed_health") {
      setFeed((prev) => ({ ...(prev ?? ({} as FeedStatus)), ...msg.data }));
    }
  });

  useEffect(() => setConnected(wsConnected), [wsConnected]);

  const setMode = useCallback(async (next: "paper" | "live") => {
    await api.setMode(next);
    setModeState(next);
  }, []);

  const killSwitch = useCallback(async () => {
    await api.killSwitch();
    setKillSwitchActive(true);
    refreshPositions();
    refreshSummary();
  }, [refreshPositions, refreshSummary]);

  const resetKillSwitch = useCallback(async () => {
    await api.resetKillSwitch();
    setKillSwitchActive(false);
  }, []);

  return {
    connected,
    ticks,
    logs,
    positions,
    mode,
    setMode,
    killSwitchActive,
    killSwitch,
    resetKillSwitch,
    summary,
    accountCapital,
    bot,
    setBot,
    history,
    account,
    feed,
    setFeed,
    refreshFeed,
    refreshPositions,
    refreshSummary,
    refreshBot,
  };
}
