"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Header } from "@/components/Terminal/Header";
import { StrategyChart } from "@/components/Terminal/StrategyChart";
import { LivePositionCard } from "@/components/Terminal/LivePositionCard";
import { PnlMetricsRow } from "@/components/Terminal/PnlMetricsRow";
import { StrategyConfigPanel } from "@/components/Terminal/StrategyConfigPanel";
import { TradeHistoryTable } from "@/components/Terminal/TradeHistoryTable";
import { smaApi, type ChartPayload, type SmaConfig, type SmaState, type TradeRow } from "@/lib/smaApi";

export default function TerminalPage() {
  const [state, setState] = useState<SmaState | null>(null);
  const [config, setConfig] = useState<SmaConfig | null>(null);
  const [chart, setChart] = useState<ChartPayload | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [connected, setConnected] = useState(false);
  const [loadNote, setLoadNote] = useState<string | null>(null);
  const busy = useRef(false);

  const refresh = useCallback(() => {
    if (busy.current) return;
    busy.current = true;
    const finish = () => {
      busy.current = false;
    };
    Promise.allSettled([smaApi.config(), smaApi.state()])
      .then(([cfg, next]) => {
        if (cfg.status === "fulfilled") setConfig(cfg.value);
        if (next.status === "fulfilled") {
          setState((prev) => (next.value.ltp > 0 || !prev || prev.ltp <= 0 ? next.value : prev));
          setLoadNote(null);
        } else {
          setLoadNote("Terminal data did not load. This is not a flat position.");
        }
        if (cfg.status === "rejected" && next.status === "rejected") {
          setLoadNote("The saved symbol did not load.");
        }
      })
      .finally(finish);
    smaApi.chart().then(setChart).catch(() => {});
    smaApi.trades().then(setTrades).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const poll = setInterval(refresh, 15000);
    return () => clearInterval(poll);
  }, [refresh]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let closed = false;

    let delay = 2000;
    const connect = () => {
      ws = new WebSocket(smaApi.streamUrl());
      const openTimer = setTimeout(() => ws?.close(), 8000);
      ws.onopen = () => {
        clearTimeout(openTimer);
        delay = 2000;
        setConnected(true);
      };
      ws.onclose = () => {
        clearTimeout(openTimer);
        setConnected(false);
        if (!closed) {
          timer = setTimeout(connect, delay);
          delay = Math.min(delay * 2, 15000);
        }
      };
      ws.onerror = () => ws?.close();
      ws.onmessage = (ev) => {
        try {
          const next = JSON.parse(ev.data) as SmaState;
          setState((prev) => (next.ltp > 0 || !prev || prev.ltp <= 0 ? next : prev));
        } catch {
          /* ignore malformed frames */
        }
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return (
    <div className="min-h-screen bg-[#0B0E14] text-slate-200">
      <Header state={state} config={config} connected={connected} loadNote={loadNote} onChanged={refresh} />
      <main className="mx-auto max-w-[1600px] space-y-4 px-4 py-4">
        {loadNote && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            {loadNote}
          </div>
        )}
        <PnlMetricsRow state={state} />
        <div className="grid items-start gap-4 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <StrategyChart chart={chart} />
          </div>
          <div className="space-y-4">
            <LivePositionCard state={state} pending={Boolean(loadNote)} />
            <StrategyConfigPanel config={config} onChanged={refresh} />
          </div>
        </div>
        <TradeHistoryTable trades={trades} />
      </main>
    </div>
  );
}
