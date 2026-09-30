"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Header } from "@/components/Terminal/Header";
import { StrategyChart } from "@/components/Terminal/StrategyChart";
import { LivePositionCard } from "@/components/Terminal/LivePositionCard";
import { PnlMetricsRow } from "@/components/Terminal/PnlMetricsRow";
import { StrategyConfigPanel } from "@/components/Terminal/StrategyConfigPanel";
import { TradeHistoryTable } from "@/components/Terminal/TradeHistoryTable";
import { WhatsAppAlerts } from "@/components/Terminal/WhatsAppAlerts";
import { smaApi, type ChartPayload, type SmaConfig, type SmaState, type TradeRow } from "@/lib/smaApi";

function useFold(key: string) {
  const [folded, setFolded] = useState(false);
  useEffect(() => {
    try {
      setFolded(localStorage.getItem(key) === "1");
    } catch {
      /* private mode */
    }
  }, [key]);
  const toggle = () => {
    setFolded((current) => {
      const next = !current;
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch {
        /* private mode */
      }
      return next;
    });
  };
  return [folded, toggle] as const;
}

function Fold({
  title,
  storageKey,
  children,
}: {
  title: string;
  storageKey: string;
  children: ReactNode;
}) {
  const [folded, toggle] = useFold(storageKey);
  if (folded) {
    return (
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between rounded-xl border border-white/10 bg-[#151921] px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400"
      >
        <span>{title}</span>
        <span className="font-normal normal-case tracking-normal text-slate-500">Show</span>
      </button>
    );
  }
  return (
    <div>
      <div className="mb-1 flex justify-end">
        <button type="button" onClick={toggle} className="text-[11px] text-slate-500 hover:text-slate-300">
          Minimize {title.toLowerCase()}
        </button>
      </div>
      {children}
    </div>
  );
}

export default function TerminalPage() {
  const [state, setState] = useState<SmaState | null>(null);
  const [config, setConfig] = useState<SmaConfig | null>(null);
  const [chart, setChart] = useState<ChartPayload | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [connected, setConnected] = useState(false);
  const [loadNote, setLoadNote] = useState<string | null>(null);
  const [closingSymbol, setClosingSymbol] = useState<string | null>(null);
  const [closeNote, setCloseNote] = useState<string | null>(null);
  const [railFolded, toggleRail] = useFold("sma.rail");
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

  const closePosition = useCallback(
    (symbol: string, direction: string, qty: number) => {
      const name = symbol.toUpperCase();
      const live = state?.mode === "LIVE";
      const sure = window.confirm(
        live
          ? `Close ${name} ${direction} ${qty} on Groww now? This sends the exit and does not stop the bot.`
          : `Close ${name} ${direction} ${qty} on the practice book?`
      );
      if (!sure) return;
      setClosingSymbol(name);
      setCloseNote(null);
      smaApi
        .closePosition(name)
        .then(() =>
          Promise.allSettled([smaApi.state(), smaApi.trades(), smaApi.chart()]).then(([next, rows, nextChart]) => {
            if (next.status === "fulfilled") {
              setState((prev) => (next.value.ltp > 0 || !prev || prev.ltp <= 0 ? next.value : prev));
            }
            if (rows.status === "fulfilled") setTrades(rows.value);
            if (nextChart.status === "fulfilled") setChart(nextChart.value);
          })
        )
        .catch((err: unknown) => setCloseNote(err instanceof Error ? err.message : "Close failed"))
        .finally(() => setClosingSymbol(null));
    },
    [state?.mode]
  );

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
    <div className="terminal-dark min-h-screen w-full min-w-0 bg-[#0B0E14] text-slate-200">
      <Header state={state} config={config} connected={connected} loadNote={loadNote} onChanged={refresh} />
      <main className="mx-auto w-full min-w-0 max-w-[1600px] space-y-4 px-3 py-3 sm:px-4 sm:py-4">
        {loadNote && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            {loadNote}
          </div>
        )}
        {closeNote && (
          <div className="rounded-md border border-[#F43F5E]/40 bg-[#F43F5E]/10 px-3 py-2 text-xs text-[#fda4af]">
            {closeNote}
          </div>
        )}
        <PnlMetricsRow state={state} />
        <div className={`flex flex-col gap-4 ${railFolded ? "" : "xl:flex-row"}`}>
          <div className="min-w-0 flex-1">
            <StrategyChart
              chart={chart}
              state={state}
              closing={Boolean(state?.symbol) && closingSymbol === state?.symbol.toUpperCase()}
              onClose={() => {
                const pos = state?.position;
                if (!pos || !state?.symbol) return;
                closePosition(state.symbol, pos.direction, pos.qty);
              }}
            />
          </div>
          {railFolded ? (
            <button
              type="button"
              onClick={toggleRail}
              className="self-start rounded-xl border border-white/10 bg-[#151921] px-3 py-2 text-xs font-semibold text-slate-300 hover:bg-white/5 xl:self-stretch xl:[writing-mode:vertical-rl]"
            >
              Show side panel
            </button>
          ) : (
            <div className="w-full shrink-0 space-y-3 xl:w-[360px]">
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={toggleRail}
                  className="rounded-md border border-white/10 px-2 py-1 text-[11px] text-slate-400 hover:bg-white/5"
                >
                  Minimize side panel
                </button>
              </div>
              <Fold title="Position" storageKey="sma.card.position">
                <LivePositionCard state={state} pending={Boolean(loadNote)} />
              </Fold>
              <Fold title="Alerts" storageKey="sma.card.alerts">
                <WhatsAppAlerts />
              </Fold>
              <Fold title="Strategy" storageKey="sma.card.config">
                <StrategyConfigPanel config={config} onChanged={refresh} />
              </Fold>
            </div>
          )}
        </div>
        <TradeHistoryTable
          trades={trades}
          state={state}
          closingSymbol={closingSymbol}
          onClose={(trade) => closePosition(trade.symbol, trade.direction, trade.qty)}
        />
      </main>
    </div>
  );
}
