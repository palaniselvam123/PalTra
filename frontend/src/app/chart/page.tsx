"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Navbar } from "@/components/Navbar";
import { AdvancedChart } from "@/components/Chart/AdvancedChart";
import { CoursePanel } from "@/components/Chart/CoursePanel";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type WatchRow } from "@/lib/api";
import { money, num, pct, pnlClass } from "@/lib/format";

export default function ChartPage() {
  const { connected, summary, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot, ticks, positions } =
    useTradingState();

  const [watch, setWatch] = useState<WatchRow[]>([]);
  const [symbol, setSymbol] = useState("RELIANCE");

  useEffect(() => {
    const load = () => api.deskWatchlist().then(setWatch).catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  // Live tick prices fold in so the sidebar moves at tick speed, not poll speed.
  const rows = useMemo(
    () => watch.map((r) => (ticks[r.symbol] ? { ...r, ltp: ticks[r.symbol].ltp } : r)),
    [watch, ticks]
  );

  const symbols = useMemo(() => {
    const names = rows.map((r) => r.symbol);
    // Keep the selected symbol addressable even if it has stopped ticking,
    // otherwise the dropdown would silently reset itself.
    return names.includes(symbol) ? names : [symbol, ...names];
  }, [rows, symbol]);

  // A position on either wallet should show its bracket on the chart.
  const position = positions.find((p: any) => p.symbol === symbol);

  return (
    <div>
      <Navbar
        connected={connected}
        totalPnl={summary.total_pnl}
        killSwitchActive={killSwitchActive}
        onKillSwitch={killSwitch}
        onResetKillSwitch={resetKillSwitch}
        feed={feed}
        onFeedChanged={setFeed}
        botRunning={bot?.enabled ?? false}
      />

      <main className="max-w-[1600px] mx-auto px-4 py-4">
        <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-4 items-start">
          {/* watchlist rail */}
          <div className="rounded-card border border-border bg-surface overflow-hidden order-2 lg:order-1">
            <div className="px-3 py-2 border-b border-border text-[11px] text-slate-400">
              Watchlist <span className="text-slate-600">({rows.length})</span>
            </div>
            <div className="max-h-[560px] overflow-y-auto">
              {rows.length === 0 && <div className="px-3 py-6 text-center text-[11px] text-slate-600">Waiting…</div>}
              {rows.map((r) => (
                <button
                  key={r.symbol}
                  onClick={() => setSymbol(r.symbol)}
                  className={clsx(
                    "w-full flex items-baseline justify-between gap-2 px-3 py-1.5 text-left transition border-l-2",
                    symbol === r.symbol
                      ? "bg-white/[0.04] border-bot"
                      : "border-transparent hover:bg-white/[0.02]"
                  )}
                >
                  <span className="text-[11px] text-slate-200 truncate">
                    {r.symbol}
                    {r.has_position && <span className="ml-1 text-[8px] text-bot">●</span>}
                  </span>
                  <span className="font-mono text-[11px] text-slate-400">{num(r.ltp)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3 order-1 lg:order-2">
            <AdvancedChart
              symbol={symbol}
              symbols={symbols}
              onSymbolChange={setSymbol}
              tick={ticks[symbol]}
              stopLoss={position?.stop_loss || undefined}
              target={position?.target || undefined}
              height={520}
            />

            <CoursePanel symbol={symbol} hasPosition={!!position} />

            {position && (
              <div className="rounded-card border border-border bg-surface px-4 py-2.5 flex items-center gap-5 flex-wrap text-[11px]">
                <span className="text-slate-400">
                  Open position ·{" "}
                  <span className={position.side === "BUY" ? "text-profit" : "text-loss"}>{position.side}</span>{" "}
                  <span className="font-mono text-slate-200">{position.quantity}</span> @{" "}
                  <span className="font-mono text-slate-200">{num(position.entry_price)}</span>
                  {" · "}
                  <span className="font-mono text-slate-200">{money(position.entry_price * position.quantity)}</span>
                </span>
                <span className="text-slate-500">
                  SL <span className="font-mono text-loss">{num(position.stop_loss)}</span>
                </span>
                <span className="text-slate-500">
                  Target <span className="font-mono text-profit">{num(position.target)}</span>
                </span>
                {"unrealised" in position && (
                  <span className={clsx("font-mono ml-auto", pnlClass((position as any).unrealised))}>
                    {(position as any).unrealised >= 0 ? "+" : ""}
                    {num((position as any).unrealised)}
                  </span>
                )}
              </div>
            )}

            <p className="text-[10px] text-slate-600">
              Indicators are computed server-side from the same code the strategy engine uses, so what you see here is
              what the bot actually decided on. Arrows mark real entries and exits from the trade ledger.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
