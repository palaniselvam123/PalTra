"use client";

import { useCallback, useEffect, useState } from "react";
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

  const refresh = useCallback(() => {
    smaApi.config().then(setConfig).catch(() => {});
    smaApi.chart().then(setChart).catch(() => {});
    smaApi.trades().then(setTrades).catch(() => {});
    smaApi.state().then(setState).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const poll = setInterval(() => {
      smaApi.chart().then(setChart).catch(() => {});
      smaApi.trades().then(setTrades).catch(() => {});
    }, 4000);
    return () => clearInterval(poll);
  }, [refresh]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let closed = false;

    const connect = () => {
      ws = new WebSocket(smaApi.streamUrl());
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws?.close();
      ws.onmessage = (ev) => {
        try {
          setState(JSON.parse(ev.data) as SmaState);
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
      <Header state={state} config={config} connected={connected} onChanged={refresh} />
      <main className="mx-auto max-w-[1600px] space-y-4 px-4 py-4">
        <PnlMetricsRow state={state} />
        <div className="grid items-start gap-4 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <StrategyChart chart={chart} />
          </div>
          <div className="space-y-4">
            <LivePositionCard state={state} />
            <StrategyConfigPanel config={config} onChanged={refresh} />
          </div>
        </div>
        <TradeHistoryTable trades={trades} />
      </main>
    </div>
  );
}
