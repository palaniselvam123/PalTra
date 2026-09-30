"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AccountSummary, type BotStatus, type FeedStatus } from "@/lib/api";
import { istNow } from "@/lib/format";
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

export type LoadState = "loading" | "ok" | "error";

function keepOk(prev: LoadState): LoadState {
  return prev === "ok" ? "ok" : "error";
}

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
  const [positionsLoad, setPositionsLoad] = useState<LoadState>("loading");
  const [historyLoad, setHistoryLoad] = useState<LoadState>("loading");
  const [accountLoad, setAccountLoad] = useState<LoadState>("loading");
  const [summaryLoad, setSummaryLoad] = useState<LoadState>("loading");
  const logIdRef = useRef(0);
  const noticeIdRef = useRef(0);
  const { notify } = useNotificationCenter();

  const refreshPositions = useCallback(() => {
    api
      .getPositions()
      .then((rows) => {
        setPositions(rows);
        setPositionsLoad("ok");
      })
      .catch(() => setPositionsLoad(keepOk));
  }, []);

  // Every P&L figure on the dashboard is scoped to the feed currently
  // selected. Blending a synthetic price series with the real market into one
  // number is the most misleading thing this screen could do — a strategy can
  // read as profitable purely because the simulator drifted upward.
  const feedRef = useRef(feed);
  feedRef.current = feed;
  const feedScope = feed?.source;

  const refreshSummary = useCallback(() => {
    api
      .getSummary(feedScope)
      .then((row) => {
        setSummary(row);
        setSummaryLoad("ok");
      })
      .catch(() => setSummaryLoad(keepOk));
    api
      .getHistory()
      .then((rows) => {
        setHistory(rows);
        setHistoryLoad("ok");
      })
      .catch(() => setHistoryLoad(keepOk));
    api
      .getAccount(feedScope)
      .then((row) => {
        setAccount(row);
        setAccountLoad("ok");
      })
      .catch(() => setAccountLoad(keepOk));
  }, [feedScope]);

  const refreshBot = useCallback(() => {
    api.getBotStatus().then(setBot).catch(() => {});
  }, []);

  const refreshFeed = useCallback(() => {
    api.getFeedStatus().then(setFeed).catch(() => {});
  }, []);

  useEffect(() => {
    let stop = false;
    let busy = false;
    const run = async () => {
      if (stop || busy) return;
      busy = true;
      try {
        // Status first. These stay fast, and the heavy reads must not keep
        // the header on "Offline" by occupying every connection.
        const [modeRes, feedRes] = await Promise.allSettled([api.getMode(), api.getFeedStatus()]);
        if (stop) return;
        if (modeRes.status === "fulfilled") {
          setModeState(modeRes.value.mode as "paper" | "live");
          setKillSwitchActive(modeRes.value.kill_switch_active);
        }
        if (feedRes.status === "fulfilled") {
          feedRef.current = feedRes.value;
          setFeed(feedRes.value);
        }
        const [posRes, sumRes, histRes, acctRes, botRes] = await Promise.allSettled([
          api.getPositions(),
          api.getSummary(feedRef.current?.source),
          api.getHistory(),
          api.getAccount(feedRef.current?.source),
          api.getBotStatus(),
        ]);
        if (stop) return;
        if (posRes.status === "fulfilled") {
          setPositions(posRes.value);
          setPositionsLoad("ok");
        } else setPositionsLoad(keepOk);
        if (sumRes.status === "fulfilled") {
          setSummary(sumRes.value);
          setSummaryLoad("ok");
        } else setSummaryLoad(keepOk);
        if (histRes.status === "fulfilled") {
          setHistory(histRes.value);
          setHistoryLoad("ok");
        } else setHistoryLoad(keepOk);
        if (acctRes.status === "fulfilled") {
          setAccount(acctRes.value);
          setAccountLoad("ok");
        } else setAccountLoad(keepOk);
        if (botRes.status === "fulfilled") setBot(botRes.value);
      } finally {
        busy = false;
      }
    };
    api.getRiskConfig().then((c) => setAccountCapital(c.account_capital)).catch(() => {});
    void run();
    const interval = setInterval(() => void run(), 15000);
    return () => {
      stop = true;
      clearInterval(interval);
    };
  }, []);

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
        at: istNow(),
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
    positionsLoad,
    historyLoad,
    accountLoad,
    summaryLoad,
    feed,
    setFeed,
    refreshFeed,
    refreshPositions,
    refreshSummary,
    refreshBot,
  };
}
