"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { istToday, useChatScreen } from "@/lib/chatScreen";
import { Header } from "@/components/Terminal/Header";
import { StrategyChart } from "@/components/Terminal/StrategyChart";
import { LivePositionCard } from "@/components/Terminal/LivePositionCard";
import { PnlMetricsRow } from "@/components/Terminal/PnlMetricsRow";
import { StrategySummary } from "@/components/Terminal/StrategySummary";
import { TradeHistoryTable } from "@/components/Terminal/TradeHistoryTable";
import { AlertsStatus } from "@/components/Terminal/WhatsAppAlerts";
import { ReplayBar } from "@/components/Terminal/ReplayBar";
import { Board } from "@/components/Layout/Board";
import { StockTabs, chartHref, tradeTotals, type StockTab } from "@/components/Terminal/StockTabs";
import {
  SMA_API,
  ApiError,
  answered,
  initialDesk,
  setDesk,
  setReplayRouting,
  smaApi,
  deskBot,
  stateForSymbol,
  type Desk,
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


export default function TerminalPage() {
  // Which bot this page drives. Set before the first request (effects run in
  // order), so every call below already goes to the right desk.
  const [desk, setDeskState] = useState<Desk>("live");
  const research = desk === "research";
  // Bots 2-4 and the research desk have no stream and never follow a replay.
  const mainDesk = desk === "live";
  const botNo = deskBot(desk);
  const researchRef = useRef(false);
  // The bot whose replays this desk follows (null on the research desk, which never follows one).
  const replayBotRef = useRef<number | null>(1);
  useEffect(() => {
    const chosen = initialDesk();
    setDesk(chosen);
    researchRef.current = chosen !== "live";
    replayBotRef.current = deskBot(chosen);
    setDeskState(chosen);
  }, []);
  const changeDesk = useCallback((next: Desk) => {
    setDesk(next);
    // A fresh page: nothing from the other desk's state, chart or replay carries over.
    window.location.href = next === "live" ? "/terminal/" : `/terminal/?desk=${next}`;
  }, []);
  const [state, setState] = useState<SmaState | null>(null);
  const [config, setConfig] = useState<SmaConfig | null>(null);
  const [chart, setChart] = useState<ChartPayload | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  // A bot's desk shows only its own practice / real trades; replay and research rows keep their own tabs.
  const deskTrades = useMemo(
    () =>
      botNo == null
        ? trades
        : trades.filter((t) => {
            const mode = (t.mode || "PAPER").toUpperCase();
            // Replays sit on the desk of the bot whose settings they played; research rows on the main desk.
            if (mode === "REPLAY") return (t.bot ?? 1) === botNo;
            if (mode !== "PAPER" && mode !== "LIVE") return botNo === 1;
            return (t.bot ?? 1) === botNo;
          }),
    [trades, botNo]
  );
  const [tradesLoaded, setTradesLoaded] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  // Polls in a row with no answer. One slow moment (a restart, a busy
  // server) is not an outage, so the banner waits for a second miss.
  const misses = useRef(0);
  const [connected, setConnected] = useState(false);
  const [loadNote, setLoadNote] = useState<string | null>(null);
  const [closingSymbol, setClosingSymbol] = useState<string | null>(null);
  const [closeNote, setCloseNote] = useState<string | null>(null);
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
  // "Hold view" keeps the stock on screen: the chart asks for it by name, so a
  // replay moving its focus (a new day, another stock) does not swap the chart.
  const heldRef = useRef<string | null>(null);
  const [held, setHeld] = useState<string | null>(null);
  const onHoldChange = useCallback((on: boolean, symbol: string) => {
    const name = on && symbol ? symbol.toUpperCase() : null;
    heldRef.current = name;
    setHeld(name);
  }, []);
  // The replay last played (its day, run and stocks), for the stock tabs after it ends.
  const [endedRun, setEndedRun] = useState<{ date: string; runId: number | null; symbols: string[] } | null>(null);
  const runSymbols = useRef<string[]>([]);
  const askLiveBars = useCallback((count: number) => {
    if (count === liveBars.current) return;
    liveBars.current = count;
    smaApi.chart(count, heldRef.current).then(setChart).catch(() => {});
  }, []);

  // What this page shows, for the "Ask the bot" chat: the bot, the books,
  // today's trades and the settings in use.
  const screenSummary = useMemo(() => {
    if (!state && !config) return null;
    const today = istToday();
    const todays = deskTrades.filter((t) => t.date === today && (t.mode ?? "PAPER").toUpperCase() !== "REPLAY");
    return {
      view: research ? "SMA terminal — research desk (paper only, own settings and book)" : "SMA terminal",
      chart_stock: state?.symbol ?? config?.symbol ?? null,
      bot_status: state?.bot_status,
      halt_reason: state?.halt_reason || undefined,
      mode: state?.mode,
      data_source: state?.data_source,
      last_signal: state?.last_signal,
      armed_stocks: state?.trade_symbols ?? config?.trade_symbols,
      open_books: (state?.books ?? []).filter((b) => b.direction !== "FLAT"),
      realized_net_pnl: state?.realized_net_pnl,
      open_net_total: state?.open_net_total,
      trades_today_count: state?.trades_today,
      kpis: state?.kpis,
      todays_trades: todays.slice(0, 40).map((t) => ({
        symbol: t.symbol,
        side: t.direction,
        qty: t.qty,
        entry: t.entry_price,
        entry_time: t.entry_time,
        exit: t.exit_price,
        exit_time: t.exit_time,
        exit_reason: t.exit_reason,
        net: t.net_pnl,
        mode: t.mode,
      })),
      settings: config ?? undefined,
      replay: replay && replay.status !== "IDLE" ? replay : undefined,
    };
  }, [state, config, trades, replay, research]);
  useChatScreen(screenSummary);

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
        misses.current = down ? misses.current + 1 : 0;
        setUnreachable(misses.current >= 2);
        if (down) {
          setLoadNote(null);
        }
      })
      .finally(finish);
    smaApi.chart(liveBars.current, heldRef.current).then(setChart).catch(() => {});
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
          Promise.allSettled([smaApi.state(), smaApi.trades(), smaApi.chart(liveBars.current, heldRef.current)]).then(([next, rows, nextChart]) => {
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
      // Each bot's desk follows the replays of its own settings; the research desk never follows one.
      if (replayBotRef.current == null) return;
      setReplay(info);
      const on = replayRouted(info) && (info.bot ?? 1) === replayBotRef.current;
      if (on && info.symbols?.length) {
        for (const name of info.symbols) if (!runSymbols.current.includes(name)) runSymbols.current.push(name);
      }
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
        setEndedRun(held ? { date: held.date, runId: held.runId, symbols: [...runSymbols.current] } : null);
        if (on) {
          lastPlayed.current = null;
          runSymbols.current = [...(info.symbols ?? [])];
        }
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
          misses.current = 0;
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
  const routed = replayRouted(replay) && (replay?.bot ?? 1) === botNo;
  // The chart marks only the book on screen: this replay run, or the PAPER /
  // LIVE book. Each earlier replay of the same day would otherwise add its own
  // EXIT at the same time and price.
  const chartTrades = deskTrades.filter((t) => {
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
          misses.current = 0;
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
      if (n % 2 === 0) smaApi.chart(liveBars.current, heldRef.current).then(setChart).catch(() => {});
      if (n % 3 === 0) smaApi.trades().then(setTrades).catch(() => {});
    }, 1000);
    return () => clearInterval(poll);
  }, [routed, onReplay]);

  // Bots 2-4 and the research desk have no stream: poll their state every 2 s while the tab is visible.
  useEffect(() => {
    if (mainDesk) return;
    const poll = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      smaApi
        .state()
        .then((next) => {
          setState(next);
          setConnected(true);
        })
        .catch(() => setConnected(false));
    }, 2000);
    return () => clearInterval(poll);
  }, [mainDesk]);

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
          // The stream is the live engine. During a replay, or on the research
          // desk, the page polls its own engine instead.
          if (replayOn.current || researchRef.current) return;
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

  // Stock tabs above the chart: the replay's stocks (playing or just ended),
  // otherwise the armed or held stocks. Each opens its chart in a new tab.
  const stockTabs = useMemo((): { tabs: StockTab[]; label: string; href: (s: string) => string } => {
    const books = state?.books ?? [];
    const side = (name: string) => {
      const book = books.find((b) => b.symbol.toUpperCase() === name);
      return book && book.direction !== "FLAT" ? book.direction : null;
    };
    const runTabs = (symbols: string[], runId: number | null, withSide: boolean) => {
      const totals = tradeTotals(
        trades.filter(
          (t) => (t.mode ?? "").toUpperCase() === "REPLAY" && t.exit_price != null && (runId == null || t.run_id === runId)
        )
      );
      return symbols.map((name) => ({
        symbol: name,
        net: totals.get(name)?.net ?? null,
        trades: totals.get(name)?.trades ?? 0,
        side: withSide ? side(name) : null,
      }));
    };
    if (routed && replay) {
      const names = Array.from(new Set([...(replay.symbols ?? []), ...runSymbols.current]));
      return {
        tabs: runTabs(names, replay.run_id ?? null, true),
        label: "Replay stocks",
        href: (name) => chartHref(name, { date: replay.date, runId: replay.run_id ?? null }),
      };
    }
    if (endedRun && endedRun.symbols.length) {
      return {
        tabs: runTabs(endedRun.symbols, endedRun.runId, false),
        label: "Replay stocks",
        href: (name) => chartHref(name, { date: endedRun.date, runId: endedRun.runId }),
      };
    }
    const armed = new Set((state?.trade_symbols ?? []).map((n) => n.toUpperCase()));
    return {
      tabs: books
        .filter((b) => armed.has(b.symbol.toUpperCase()) || b.direction !== "FLAT" || (b.closed_trades ?? 0) > 0)
        .map((b) => ({
          symbol: b.symbol.toUpperCase(),
          net: b.day_net ?? b.closed_net ?? null,
          trades: b.closed_trades ?? 0,
          side: b.direction !== "FLAT" ? b.direction : null,
        })),
      label: research ? "Research stocks" : "Stocks",
      href: (name) => chartHref(name, { desk }),
    };
  }, [state?.books, state?.trade_symbols, trades, routed, replay, endedRun, research, desk]);
  const viewState = useMemo(() => stateForSymbol(state, held), [state, held]);
  const activeTab = (held ?? chart?.symbol ?? state?.symbol ?? null) || null;

  return (
    <div className="terminal-dark min-h-screen w-full min-w-0 bg-[#0B0E14] text-slate-200">
      <Header
        state={state}
        config={config}
        connected={connected}
        loadNote={loadNote}
        onChanged={refresh}
        desk={desk}
        onDeskChange={changeDesk}
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
        {botNo != null && botNo > 1 ? (
          <>
            <div role="note" className="rounded-xl border border-sky-400/30 bg-sky-500/[0.07] px-3 py-2 text-sm text-sky-100">
              <span className="font-semibold">{config?.bot_name ?? `Bot ${botNo}`}</span> — its own settings, Trade list,
              trades ({config?.trading_mode === "LIVE" ? "N" : "P"}
              {botNo}-1, …), P&amp;L, limits and panic. PAPER or LIVE on its own switch. A stock can be traded LIVE by only
              one bot at a time.
            </div>
            <ReplayBar
              info={replay}
              live={config?.trading_mode === "LIVE"}
              armedCount={config?.trade_symbols?.length ?? 0}
              onChanged={onReplay}
              bot={botNo}
              botName={config?.bot_name ?? `Bot ${botNo}`}
            />
          </>
        ) : research ? (
          <div role="note" className="rounded-xl border border-teal-400/30 bg-teal-500/[0.07] px-3 py-2 text-sm text-teal-100">
            <span className="font-semibold">Research desk</span> — a second bot on today&apos;s live prices with practice
            money only. Its settings, Trade list (up to 10 stocks), trades (Q-1, Q-2…) and P&amp;L are its own. It never
            sends an order or alert, and changes here never touch the live desk.
          </div>
        ) : (
          <ReplayBar
            info={replay}
            live={config?.trading_mode === "LIVE"}
            armedCount={config?.trade_symbols?.length ?? 0}
            onChanged={onReplay}
            bot={1}
            botName={config?.bot_name ?? "Bot 1"}
          />
        )}
        {/* Movable, resizable panels: drag by the grip, resize by the bottom edge, change the width on a wide screen. */}
        <Board
          page={`terminal.${desk}`}
          panels={[
            { id: "metrics", title: "P&L", node: <PnlMetricsRow state={state} />, resize: "none" },
            {
              id: "chart",
              title: "Chart",
              span: 9,
              resize: "var",
              minHeight: 220,
              hideable: false,
              node: (
                <div className="min-w-0">
                  <StockTabs tabs={stockTabs.tabs} active={activeTab} hrefFor={stockTabs.href} label={stockTabs.label} />
                  <StrategyChart
                    chart={chart}
                    state={viewState}
                    onHoldChange={onHoldChange}
                    trades={chartTrades}
                    allTrades={deskTrades}
                    pin={pin}
                    closing={Boolean(viewState?.symbol) && closingSymbol === viewState?.symbol.toUpperCase()}
                    onLiveBars={askLiveBars}
                    onClose={() => {
                      const pos = viewState?.position;
                      if (!pos || !viewState?.symbol) return;
                      closePosition(viewState.symbol, pos.direction, pos.qty);
                    }}
                  />
                </div>
              ),
            },
            {
              id: "side",
              title: "Side panel",
              span: 3,
              node: (
                <div className="space-y-3">
                  <LivePositionCard state={viewState} pending={Boolean(loadNote)} />
                  {research ? null : <AlertsStatus />}
                </div>
              ),
            },
            { id: "strategy", title: "Strategy", node: <StrategySummary config={config} research={research} onChanged={refresh} /> },
            {
              id: "blotter",
              title: "Trades",
              minHeight: 240,
              node: (
                <TradeHistoryTable
                  loading={!tradesLoaded}
                  trades={deskTrades}
                  state={state}
                  closingSymbol={closingSymbol}
                  onClose={(trade) => closePosition(trade.symbol, trade.direction, trade.qty)}
                />
              ),
            },
          ]}
        />
      </main>
    </div>
  );
}
