"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Header } from "@/components/Terminal/Header";
import { StrategyChart } from "@/components/Terminal/StrategyChart";
import { LivePositionCard } from "@/components/Terminal/LivePositionCard";
import { PnlMetricsRow } from "@/components/Terminal/PnlMetricsRow";
import { StrategyConfigPanel } from "@/components/Terminal/StrategyConfigPanel";
import { TradeHistoryTable } from "@/components/Terminal/TradeHistoryTable";
import { WhatsAppAlerts } from "@/components/Terminal/WhatsAppAlerts";
import { ReplayBar } from "@/components/Terminal/ReplayBar";
import {
  SMA_API,
  ApiError,
  answered,
  setReplayRouting,
  smaApi,
  type ChartPayload,
  type ReplayInfo,
  type SmaConfig,
  type SmaState,
  type TradeRow,
} from "@/lib/smaApi";

/** A replay engine exists to read from (not while its candles are still loading). */
function replayRouted(info: ReplayInfo | null): boolean {
  return !!info && ["PLAYING", "PAUSED", "FINISHED"].includes(info.status);
}

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
        className="flex min-h-11 w-full items-center justify-between rounded-xl border border-white/10 bg-[#151921] px-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-300"
      >
        <span>{title}</span>
        <span className="font-normal normal-case tracking-normal text-slate-400">Show</span>
      </button>
    );
  }
  return (
    <div>
      <div className="mb-1 flex justify-end">
        <button type="button" onClick={toggle} className="min-h-11 px-2 text-xs text-slate-400 hover:text-slate-200 sm:min-h-8">
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
  const [tradesLoaded, setTradesLoaded] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [connected, setConnected] = useState(false);
  const [loadNote, setLoadNote] = useState<string | null>(null);
  const [closingSymbol, setClosingSymbol] = useState<string | null>(null);
  const [closeNote, setCloseNote] = useState<string | null>(null);
  const [railFolded, toggleRail] = useFold("sma.rail");
  const busy = useRef(false);
  const [replay, setReplay] = useState<ReplayInfo | null>(null);
  const replayOn = useRef(false);
  // The day, run and stock the replay was last showing, so the chart can stay
  // on that day after the replay ends instead of jumping to today.
  const lastPlayed = useRef<{ date: string; runId: number | null; symbol: string | null } | null>(null);
  const pinSeq = useRef(0);
  const [pin, setPin] = useState<{ seq: number; date: string | null; runId: number | null; symbol: string | null } | null>(
    null
  );
  const liveBars = useRef(240);
  const askLiveBars = useCallback((count: number) => {
    if (count === liveBars.current) return;
    liveBars.current = count;
    smaApi.chart(count).then(setChart).catch(() => {});
  }, []);

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
        // Down means neither call got an answer. An error reply (a 409 while a
        // replay loads, say) still came from the engine, so it is reachable.
        const down =
          cfg.status === "rejected" &&
          next.status === "rejected" &&
          !answered(cfg.reason) &&
          !answered(next.reason);
        setUnreachable(down);
        if (down) {
          setLoadNote(null);
        }
      })
      .finally(finish);
    smaApi.chart(liveBars.current).then(setChart).catch(() => {});
    smaApi
      .trades()
      .then((rows) => {
        setTrades(rows);
        setTradesLoaded(true);
      })
      .catch(() => {});
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
          Promise.allSettled([smaApi.state(), smaApi.trades(), smaApi.chart(liveBars.current)]).then(([next, rows, nextChart]) => {
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

  // Replay: poll its status; while it plays, state/chart/bot buttons follow the
  // replay engine (smaApi routes them) and update every second.
  const onReplay = useCallback(
    (info: ReplayInfo) => {
      setReplay(info);
      const on = replayRouted(info);
      if (on && info.date) {
        lastPlayed.current = {
          date: info.date,
          runId: info.run_id ?? null,
          symbol: lastPlayed.current?.symbol ?? info.symbols?.[0] ?? null,
        };
      }
      if (on !== replayOn.current) {
        replayOn.current = on;
        setReplayRouting(on);
        // Starting a replay shows its chart; ending one keeps the day it played.
        pinSeq.current += 1;
        const held = on ? null : lastPlayed.current;
        setPin({ seq: pinSeq.current, date: held?.date ?? null, runId: held?.runId ?? null, symbol: held?.symbol ?? null });
        if (on) lastPlayed.current = null;
        setState(null);
        setChart(null);
        refresh();
      }
    },
    [refresh]
  );
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      smaApi
        .replayInfo()
        .then((info) => {
          if (stop) return;
          setUnreachable(false);
          onReplay(info);
        })
        .catch(() => {})
        .finally(() => {
          if (!stop) timer = setTimeout(tick, replayOn.current || replay?.status === "LOADING" ? 1000 : 10000);
        });
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [onReplay, replay?.status]);
  const routed = replayRouted(replay);
  // The chart marks only the book on screen: this replay run, or the PAPER /
  // LIVE book. Each earlier replay of the same day would otherwise add its own
  // EXIT at the same time and price.
  const chartTrades = trades.filter((t) => {
    const mode = (t.mode || "PAPER").toUpperCase();
    if (routed) return mode === "REPLAY" && (replay?.run_id == null || t.run_id === replay.run_id);
    return mode === (config?.trading_mode || "PAPER").toUpperCase();
  });
  useEffect(() => {
    if (!routed) return;
    let n = 0;
    const poll = setInterval(() => {
      // A hidden tab needs no replay frames; each one costs the server CPU.
      if (typeof document !== "undefined" && document.hidden) return;
      n += 1;
      smaApi
        .state()
        .then((next) => {
          setState(next);
          setUnreachable(false);
          setLoadNote(null);
          if (lastPlayed.current && next.symbol) lastPlayed.current.symbol = next.symbol;
        })
        .catch((err: unknown) => {
          // 409: the server has no replay (it ended or the server restarted).
          // Re-read the replay status now so the page stops showing it.
          if (err instanceof ApiError && err.status === 409) {
            smaApi.replayInfo().then(onReplay).catch(() => {});
          }
        });
      if (n % 2 === 0) smaApi.chart(liveBars.current).then(setChart).catch(() => {});
      if (n % 3 === 0) smaApi.trades().then(setTrades).catch(() => {});
    }, 1000);
    return () => clearInterval(poll);
  }, [routed, onReplay]);

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
          // The stream is the live engine. During a replay the page polls instead.
          if (replayOn.current) return;
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
      <Header
        state={state}
        config={config}
        connected={connected}
        loadNote={loadNote}
        onChanged={refresh}
        notice={
          unreachable ? (
              <div
                role="alert"
                className="flex flex-col gap-2 rounded-xl border border-rose-500/50 bg-rose-500/10 p-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="text-sm text-rose-100">
                  <p className="font-semibold">Can&apos;t reach the trading engine.</p>
                  <p className="text-rose-200/90">
                    {state
                      ? "Prices, positions and P&L below are from the last reply and may be out of date."
                      : "Nothing has loaded yet. This is not a flat position."}{" "}
                    Retrying every 15 s. <span className="break-all font-mono text-xs text-rose-200/70">{SMA_API}</span>
                  </p>
                </div>
                <button
                  type="button"
                  onClick={refresh}
                  className="min-h-11 shrink-0 rounded-md bg-rose-600 px-4 text-sm font-semibold text-white hover:bg-rose-500"
                >
                  Retry now
                </button>
              </div>
          ) : null
        }
      />
      <main className="mx-auto w-full min-w-0 space-y-4 px-3 py-3 sm:px-4 sm:py-4">
        {loadNote && !unreachable && (
          <div role="status" className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
            {loadNote}
          </div>
        )}
        {closeNote && (
          <div role="alert" className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
            {closeNote}
          </div>
        )}
        <ReplayBar
          info={replay}
          live={config?.trading_mode === "LIVE"}
          armedCount={config?.trade_symbols?.length ?? 0}
          onChanged={onReplay}
        />
        <PnlMetricsRow state={state} />
        <div className="flex flex-col gap-4 xl:flex-row">
          <div className="min-w-0 flex-1">
            <StrategyChart
              chart={chart}
              state={state}
              trades={chartTrades}
              allTrades={trades}
              pin={pin}
              closing={Boolean(state?.symbol) && closingSymbol === state?.symbol.toUpperCase()}
              onLiveBars={askLiveBars}
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
              className="min-h-11 self-start rounded-xl border border-white/10 bg-[#151921] px-3 text-xs font-semibold text-slate-300 hover:bg-white/5 xl:w-10 xl:self-stretch xl:px-1 xl:[writing-mode:vertical-rl]"
            >
              Show side panel
            </button>
          ) : (
            <div className="w-full shrink-0 space-y-3 xl:w-[360px]">
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={toggleRail}
                  className="min-h-11 rounded-md border border-white/10 px-3 text-xs text-slate-300 hover:bg-white/5 sm:min-h-8"
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
          loading={!tradesLoaded}
          trades={trades}
          state={state}
          closingSymbol={closingSymbol}
          onClose={(trade) => closePosition(trade.symbol, trade.direction, trade.qty)}
        />
      </main>
    </div>
  );
}
