"use client";

import Link from "next/link";

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
      <div className="rounded-card border border-border bg-surface p-4 text-xs text-slate-500">Loading bot status…</div>
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
    <div className="rounded-card border border-border bg-surface p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Bot size={15} className="text-bot" />
          <span className="text-sm font-medium text-slate-200">ORB Strategy Bot</span>
        </div>
        <span className={clsx("text-[11px] font-semibold px-2 py-1 rounded-full", STATUS_STYLES[bot.status])}>
          {bot.status.replace(/_/g, " ")}
        </span>
      </div>

      <p className="text-[11px] text-slate-500">
        {/* ARMED means something different per strategy — the ORB wording
            ("range locked, watching for breakouts") is actively wrong for the
            two strategies that never build a range. */}
        {bot.status === "ARMED" && bot.config.strategy === "scanner"
          ? "Armed. Trading BUY and SELL signals as the scanner fires them — no need to start it separately."
          : bot.status === "ARMED" && bot.config.strategy === "gainers"
            ? "Armed. Ranking the top 50 gainers, then buying only on an EMA 9/21 entry. A death cross sells the same stock."
            : STATUS_HELP[bot.status]}
      </p>

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

        {/* Strategy picker. Sits ahead of the timing toggle because it
            changes what the timing means: ORB needs an opening range, the
            gainers strategy only needs a ranking. */}
        <div className="flex overflow-hidden rounded-md border border-border text-[11px]">
          {(["orb", "gainers", "scanner"] as const).map((st) => (
            <button
              key={st}
              disabled={bot.enabled || busy}
              onClick={() => act(() => api.setBotConfig({ strategy: st }))}
              className={clsx(
                "px-2.5 py-1.5 transition",
                bot.config.strategy === st ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200",
                bot.enabled && "cursor-not-allowed opacity-50"
              )}
            >
              {st === "orb" ? "ORB" : st === "gainers" ? "TOP GAINERS" : "SCANNER"}
            </button>
          ))}
        </div>

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

      {(bot.config.strategy === "scanner" || bot.config.strategy === "gainers") && (
        <div
          className={clsx(
            "rounded-md border px-3 py-2 text-[11px] leading-relaxed",
            bot.scanner_running
              ? "border-border bg-surface2 text-slate-300"
              : "border-amber-500/30 bg-amber-500/10 text-amber-400"
          )}
        >
          {bot.scanner_running ? (
            <>
              <strong className="font-semibold">Scanner is running with the bot.</strong> Top 50
              gainers (or whichever universe is saved on the Scanner page) are watched for EMA 9/21
              signals. A BUY opens a position; a SELL closes the same stock. Stop-loss and target
              still apply in between.
            </>
          ) : (
            <>
              <strong className="font-semibold">Scanner will start when you start the bot.</strong> You
              do not have to visit the{" "}
              <Link href="/scanner" className="underline">
                Scanner page
              </Link>{" "}
              first.
            </>
          )}
        </div>
      )}

      {bot.config.strategy === "gainers" && (bot.gainers_candidates?.length ?? 0) > 0 && (
        <div className="rounded-md border border-border bg-surface2 px-3 py-2">
          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-caption font-medium text-slate-300">
              Candidates — top {bot.config.gainers_top_n} by move from open
            </span>
            <span className="text-caption text-slate-400">via {bot.gainers_source}</span>
          </div>
          <div className="flex flex-wrap gap-1">
            {bot.gainers_candidates!.slice(0, 10).map((c) => (
              <span
                key={c.symbol}
                className="rounded bg-profit/10 px-1.5 py-0.5 font-mono text-[11px] text-profit"
              >
                {c.symbol} +{c.pct_from_open}%
              </span>
            ))}
          </div>
        </div>
      )}

      {bot.config.strategy === "gainers" && (
        <div className="rounded-md border border-border bg-surface2 px-3 py-2 text-[11px] leading-relaxed text-slate-400">
          Ranking is the watchlist, not the buy. A top gainer is bought only after the same EMA 9/21
          entry the scanner uses, then sold on the matching exit (or stop/target / 15:30).
        </div>
      )}

      {(bot.config.strategy === "scanner" || bot.config.strategy === "gainers") && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-400">
          <strong className="font-semibold">Entries are gated the same way on every universe.</strong>{" "}
          Closed candles only, EMA 9/21 minimum, ADX + volume + RSI, 5-minute hold before a SELL can
          close, even-split size with a 1% risk cap, and no new buys after{" "}
          {bot.config.last_entry_buffer_min
            ? `${bot.config.last_entry_buffer_min} min before square-off`
            : "the cut-off"}
          . Starting the bot rewrites a noise config (EMA2/EMA3, forming-bar) to those defaults.
        </div>
      )}

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-slate-400 font-mono border-t border-border pt-2">
        {bot.config.strategy === "scanner" || bot.config.strategy === "gainers" ? (
          <>
            <span>
              {bot.config.strategy === "gainers"
                ? `Universe: top ${bot.config.gainers_top_n} + TA`
                : "Universe: scanner (all saved lists)"}
            </span>
            <span>Max positions: {bot.config.gainers_max_positions}</span>
            <span className="text-loss">Stop: −{bot.config.gainers_stop_loss_pct}%</span>
            <span className="text-profit">Target: +{bot.config.gainers_target_pct}%</span>
            <span className="col-span-2">
              Entry: <span className="text-profit">closed 5m EMA 9/21</span>
              {bot.scanner_running ? " · scanner live" : ""}
            </span>
          </>
        ) : (
        <>
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
        </>
        )}
      </div>

      {bot.config.session_mode === "demo" && bot.config.strategy === "orb" && (
        <p className="text-[11px] text-amber-400/80">
          Demo timing compresses the opening range so the engine can be tested outside market hours. Use MARKET TIMING
          (5-min candles, 09:15–09:30 range) for anything real.
        </p>
      )}

      {bot.config.strategy === "orb" && bot.config.session_mode === "market" && bot.enabled && bot.range_window && (
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
