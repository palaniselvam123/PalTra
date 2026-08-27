"use client";

import { useState } from "react";
import clsx from "clsx";
import { Play, Square, Bot, Loader2 } from "lucide-react";
import { api, type BotStatus } from "@/lib/api";

const STATUS_STYLES: Record<BotStatus["status"], string> = {
  STOPPED: "bg-slate-700/40 text-slate-300",
  WAITING_FOR_OPEN: "bg-amber-500/20 text-amber-400",
  BUILDING_RANGE: "bg-bot/20 text-bot",
  ARMED: "bg-profit/20 text-profit",
  NO_RANGE: "bg-amber-500/20 text-amber-400",
  HALTED: "bg-loss/20 text-loss",
};

const STATUS_HELP: Record<BotStatus["status"], string> = {
  STOPPED: "Bot is off. No automated entries.",
  WAITING_FOR_OPEN: "Waiting for the session open before building the range.",
  BUILDING_RANGE: "Measuring the opening range and relative volume.",
  ARMED: "Range locked. Watching for breakout closes on qualifying symbols.",
  NO_RANGE:
    "No opening range was built, so no breakout can be detected and NO entries will be taken this session. The bot was not running during the range window. Stop it and use DEMO TIMING to test now, or restart before the next session open.",
  HALTED: "Halted by the risk engine. Reset the kill switch to resume.",
};

type Props = {
  bot: BotStatus | null;
  onChanged: (next: BotStatus) => void;
};

export function BotControl({ bot, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!bot) {
    return (
      <div className="rounded-lg border border-border bg-surface p-4 text-xs text-slate-500">Loading bot status…</div>
    );
  }

  const act = async (fn: () => Promise<BotStatus>) => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await fn());
    } catch (e: any) {
      setError(e.message ?? "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const qualifying = bot.opening_ranges.filter((r) => r.qualifies);

  return (
    <div className="rounded-lg border border-border bg-surface p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Bot size={15} className="text-bot" />
          <span className="text-sm font-medium text-slate-200">ORB Strategy Bot</span>
        </div>
        <span className={clsx("text-[11px] font-semibold px-2 py-1 rounded-full", STATUS_STYLES[bot.status])}>
          {bot.status.replace(/_/g, " ")}
        </span>
      </div>

      <p className="text-[11px] text-slate-500">{STATUS_HELP[bot.status]}</p>

      {bot.status === "BUILDING_RANGE" && bot.range_ends_in_sec !== null && (
        <div className="text-xs text-bot font-mono">Range locks in {bot.range_ends_in_sec}s</div>
      )}

      <div className="flex items-center gap-2">
        {bot.enabled ? (
          <button
            onClick={() => act(api.stopBot)}
            disabled={busy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-loss/20 text-loss text-xs font-semibold hover:bg-loss/30 transition"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Square size={13} />} Stop Bot
          </button>
        ) : (
          <button
            onClick={() => act(api.startBot)}
            disabled={busy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-profit/20 text-profit text-xs font-semibold hover:bg-profit/30 transition"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Start Bot
          </button>
        )}

        <div className="flex rounded-md border border-border overflow-hidden text-[11px]">
          {(["demo", "market"] as const).map((m) => (
            <button
              key={m}
              disabled={bot.enabled || busy}
              onClick={() => act(() => api.setBotConfig({ session_mode: m }))}
              className={clsx(
                "px-2.5 py-1.5 transition",
                bot.config.session_mode === m ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200",
                bot.enabled && "opacity-50 cursor-not-allowed"
              )}
            >
              {m === "demo" ? "DEMO TIMING" : "MARKET TIMING"}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-slate-400 font-mono border-t border-border pt-2">
        <span>Candle: {bot.config.candle_interval_sec}s</span>
        <span>Range: {bot.config.range_duration_sec}s</span>
        <span>RVOL ≥ {bot.config.rvol_threshold}</span>
        <span>R:R 1:{bot.config.risk_reward}</span>
        <span className="col-span-2">Trailing SL: {bot.config.trailing_enabled ? "Supertrend(10,3)" : "off"}</span>
        <button
          disabled={bot.enabled || busy}
          onClick={() => act(() => api.setBotConfig({ adx_filter_enabled: !bot.config.adx_filter_enabled }))}
          title="Rejects a breakout unless ADX shows a real trend behind it — a breakout in a range-bound market is more likely noise than a real move."
          className={clsx(
            "col-span-2 text-left",
            bot.config.adx_filter_enabled ? "text-bot" : "text-slate-600",
            (bot.enabled || busy) && "cursor-not-allowed"
          )}
        >
          ADX trend filter: {bot.config.adx_filter_enabled ? `on, ≥ ${bot.config.adx_threshold}` : "off"}
        </button>
      </div>

      {bot.config.session_mode === "demo" && (
        <p className="text-[11px] text-amber-400/80">
          Demo timing compresses the opening range so the engine can be tested outside market hours. Use MARKET TIMING
          (5-min candles, 09:15–09:30 range) for anything real.
        </p>
      )}

      {bot.config.session_mode === "market" && bot.enabled && bot.range_window && (
        <p className={clsx("text-[11px]", bot.late_start ? "text-amber-400/80" : "text-slate-500")}>
          {bot.late_start ? (
            <>
              <strong>Late start.</strong> The 09:15 opening-range window had already closed, so the range is being
              measured over <span className="font-mono">{bot.range_window}</span> instead — a mid-session range on real
              5-min candles, not the classic opening range. Start before 09:15 for a true ORB session.
            </>
          ) : (
            <>
              Measuring the opening range over <span className="font-mono">{bot.range_window}</span>, then hunting
              breakouts until the {""}
              15:30 IST square-off.
            </>
          )}
        </p>
      )}

      {bot.range_ready && (
        <div className="border-t border-border pt-2">
          <div className="text-[11px] text-slate-400 mb-1">
            Opening ranges — {qualifying.length}/{bot.opening_ranges.length} clear the RVOL filter
          </div>
          <div className="max-h-40 overflow-y-auto">
            <table className="w-full text-[11px] font-mono">
              <tbody>
                {bot.opening_ranges.map((r) => {
                  // A row can pass RVOL but still be blocked by the ADX gate
                  // — worth showing as a distinct reason, not just "no".
                  const adxBlocks = bot.config.adx_filter_enabled && r.adx_trending === false;
                  const tradable = r.qualifies && !adxBlocks;
                  return (
                    <tr key={r.symbol} className={clsx(!tradable && "opacity-40")}>
                      <td className="py-0.5 text-slate-200">{r.symbol}</td>
                      <td className="py-0.5 text-slate-500">
                        {r.low}–{r.high}
                      </td>
                      <td className={clsx("py-0.5 text-right", r.qualifies ? "text-profit" : "text-slate-500")}>
                        {r.rvol}x
                      </td>
                      <td
                        className={clsx(
                          "py-0.5 text-right pl-2",
                          r.adx === null
                            ? "text-slate-600"
                            : r.adx_trending
                            ? "text-bot"
                            : "text-amber-500/70"
                        )}
                        title={
                          r.adx === null
                            ? "Not enough candle history yet to compute ADX"
                            : r.adx_trending
                            ? "Trending — clears the ADX filter"
                            : "Range-bound — ADX filter would block a breakout here"
                        }
                      >
                        {r.adx === null ? "ADX…" : `ADX ${r.adx}`}
                      </td>
                      <td className="py-0.5 text-right pl-2">
                        {r.bb_squeeze && (
                          <span
                            className="text-[9px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-400"
                            title={`Bollinger bandwidth ${r.bb_bandwidth_pct}% — tightly coiled, watch for a real move`}
                          >
                            SQUEEZE
                          </span>
                        )}
                      </td>
                      <td className="py-0.5 text-right text-slate-500 pl-2">
                        {bot.symbols_traded.includes(r.symbol) ? "traded" : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {error && <div className="text-xs text-loss">{error}</div>}
    </div>
  );
}
