"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronRight, Loader2, Play, RefreshCw, Send, Square, Trash2, Radar } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { CandleIcon } from "@/components/Scanner/CandleIcon";
import { useTradingState } from "@/hooks/useTradingState";
import {
  api,
  type ScanChannel,
  type ScanConfig,
  type ScanOptions,
  type ScanSignal,
  type ScanStatus,
} from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  SENT: "bg-profit/15 text-profit",
  FAILED: "bg-loss/15 text-loss",
  PENDING: "bg-bot/15 text-bot",
  SKIPPED: "bg-slate-700/40 text-slate-400",
};

export default function ScannerPage() {
  const { connected, summary, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();

  const [config, setConfig] = useState<ScanConfig | null>(null);
  const [options, setOptions] = useState<ScanOptions | null>(null);
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [signals, setSignals] = useState<ScanSignal[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const [provider, setProvider] = useState("callmebot");
  const [target, setTarget] = useState("");
  const [secret, setSecret] = useState("");
  const [extra, setExtra] = useState("");
  const [channelMsg, setChannelMsg] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const refresh = useCallback(() => {
    api.scanStatus().then(setStatus).catch(() => {});
    api.scanSignals(60).then(setSignals).catch(() => {});
  }, []);

  useEffect(() => {
    api
      .scanConfig()
      .then((r) => {
        setConfig(r.config);
        setOptions(r.options);
      })
      .catch((e) => setError(e.message));
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  const patch = (p: Partial<ScanConfig>) => setConfig((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!config) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.setScanConfig(config);
      setConfig(r.config);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<any>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveChannel = async () => {
    setBusy(true);
    setChannelMsg(null);
    try {
      await api.saveScanChannel({ provider, enabled: true, target, secret, extra });
      setChannelMsg("Saved and enabled. Send a test to confirm it actually delivers.");
      setSecret("");
      refresh();
    } catch (e: any) {
      setChannelMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  const testAlert = async () => {
    setBusy(true);
    setChannelMsg(null);
    try {
      const r = await api.testScanAlert();
      setChannelMsg(`Test message sent via ${r.provider}. Check WhatsApp.`);
    } catch (e: any) {
      setChannelMsg(`Send failed — ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const running = status?.running ?? false;
  const activeChannel = status?.channels?.find((c: ScanChannel) => c.enabled);

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

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        <div className="flex items-start gap-3 flex-wrap">
          <div>
            <h1 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
              <Radar size={17} className="text-bot" /> Automated Scanner &amp; Alert Engine
            </h1>
            <p className="text-xs text-slate-500 max-w-3xl">
              Watches a universe for moving-average crossovers and sends a WhatsApp alert. It only observes — it can
              never place an order. Signals are evaluated <strong>on candle close only</strong>, so a cross that
              appears and vanishes inside a forming bar never fires.
            </p>
          </div>
          <button
            onClick={refresh}
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border text-[11px] text-slate-300 hover:bg-white/5 transition"
          >
            <RefreshCw size={12} /> Refresh
          </button>
        </div>

        {error && (
          <div className="rounded-lg border border-loss/40 bg-loss/10 px-4 py-2.5 text-sm text-loss">{error}</div>
        )}

        {/* controls */}
        <div className="rounded-lg border border-border bg-surface p-4 flex items-center gap-3 flex-wrap">
          {running ? (
            <button
              onClick={() => act(api.scanStop)}
              disabled={busy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-loss/20 text-loss text-xs font-semibold hover:bg-loss/30 transition"
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Square size={13} />} Stop Scanner
            </button>
          ) : (
            <button
              onClick={() => act(api.scanStart)}
              disabled={busy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-profit/20 text-profit text-xs font-semibold hover:bg-profit/30 transition"
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Start Scanner
            </button>
          )}

          <button
            onClick={() => act(api.scanNow)}
            disabled={busy}
            className="px-3 py-1.5 rounded-md border border-border text-xs text-slate-300 hover:bg-white/5 transition"
          >
            Scan once now
          </button>

          <span
            className={clsx(
              "text-[11px] px-2 py-1 rounded-full",
              running ? "bg-profit/15 text-profit" : "bg-slate-700/40 text-slate-400"
            )}
          >
            {running ? "RUNNING" : "STOPPED"}
          </span>

          <div className="flex items-center gap-4 text-[11px] text-slate-500 font-mono ml-auto flex-wrap">
            <span>universe {status?.universe_size ?? 0}</span>
            <span>scanned {status?.scanned_symbols ?? 0}</span>
            <span>signals {status?.signals_today ?? 0}</span>
            {status?.running && status?.next_bar_close && (
              <span
                className="text-slate-400"
                title={`Signals are only evaluated when a ${status.timeframe} bar closes. Nothing can fire before then.`}
              >
                next {status.timeframe} bar closes{" "}
                {new Date(status.next_bar_close).toLocaleTimeString("en-IN", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            )}
            <span className={status?.feed_source === "live" ? "text-profit" : "text-amber-400"}>
              {status?.feed_source ?? "—"}
            </span>
          </div>
        </div>

        {status?.feed_source === "simulated" && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-[11px] text-amber-300">
            The feed is on <strong>SIMULATED</strong> prices. Crossovers detected here are against synthetic data and
            mean nothing about the real market. Switch to LIVE NSE for signals worth acting on.
          </div>
        )}

        {status?.notes && status.notes.length > 0 && (
          <div className="rounded-lg border border-border bg-surface px-4 py-2 text-[11px] text-slate-400 space-y-0.5">
            {status.notes.map((n) => (
              <div key={n}>· {n}</div>
            ))}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          {/* strategy */}
          <div className="rounded-lg border border-border bg-surface p-4 space-y-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-slate-200">Strategy</h2>
              {running && <span className="text-[10px] text-amber-400">stop the scanner to edit</span>}
              {saved && <span className="text-[10px] text-profit ml-auto">saved</span>}
            </div>

            {config && options && (
              <fieldset disabled={running} className={clsx("space-y-3", running && "opacity-50")}>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Universe">
                    <select
                      value={config.universe}
                      onChange={(e) => patch({ universe: e.target.value as ScanConfig["universe"] })}
                      className={inputCls}
                    >
                      {options.universes.map((u) => (
                        <option key={u} value={u}>
                          {u === "WATCHLIST" ? "Streaming watchlist" : u === "CORE" ? "Core 20" : "Custom"}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Timeframe">
                    <select
                      value={config.timeframe}
                      onChange={(e) => patch({ timeframe: e.target.value })}
                      className={inputCls}
                    >
                      {options.timeframes.map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                  </Field>
                </div>

                {config.universe === "CUSTOM" && (
                  <Field label="Custom symbols (comma separated)">
                    <input
                      value={config.custom_symbols}
                      onChange={(e) => patch({ custom_symbols: e.target.value })}
                      placeholder="RELIANCE, TCS, INFY"
                      className={inputCls}
                    />
                  </Field>
                )}

                <div className="grid grid-cols-2 gap-3">
                  <Field label={`Fast MA — ${config.fast_type}${config.fast_period}`}>
                    <div className="flex gap-2">
                      <select
                        value={config.fast_type}
                        onChange={(e) => patch({ fast_type: e.target.value as "EMA" | "SMA" })}
                        className={`${inputCls} w-20`}
                      >
                        {options.ma_types.map((m) => (
                          <option key={m}>{m}</option>
                        ))}
                      </select>
                      <input
                        type="range"
                        min={2}
                        max={100}
                        value={config.fast_period}
                        onChange={(e) => patch({ fast_period: +e.target.value })}
                        className="flex-1 accent-cyan-500"
                      />
                    </div>
                  </Field>
                  <Field label={`Slow MA — ${config.slow_type}${config.slow_period}`}>
                    <div className="flex gap-2">
                      <select
                        value={config.slow_type}
                        onChange={(e) => patch({ slow_type: e.target.value as "EMA" | "SMA" })}
                        className={`${inputCls} w-20`}
                      >
                        {options.ma_types.map((m) => (
                          <option key={m}>{m}</option>
                        ))}
                      </select>
                      <input
                        type="range"
                        min={3}
                        max={250}
                        value={config.slow_period}
                        onChange={(e) => patch({ slow_period: +e.target.value })}
                        className="flex-1 accent-violet-500"
                      />
                    </div>
                  </Field>
                </div>

                {config.fast_period >= config.slow_period && (
                  <p className="text-[11px] text-loss">
                    Fast must be shorter than slow, or the two lines can never cross meaningfully.
                  </p>
                )}

                <Field label="Signal type">
                  <div className="flex rounded-md border border-border overflow-hidden text-[11px]">
                    {options.signal_types.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => patch({ signal_type: s as ScanConfig["signal_type"] })}
                        className={clsx(
                          "px-2.5 py-1.5 flex-1 transition",
                          config.signal_type === s ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200"
                        )}
                      >
                        {s === "GOLDEN_CROSS" ? "Golden only" : s === "DEATH_CROSS" ? "Death only" : "Both"}
                      </button>
                    ))}
                  </div>
                </Field>

                <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.trend_filter}
                    onChange={(e) => patch({ trend_filter: e.target.checked })}
                    className="accent-cyan-500"
                  />
                  Require close on the right side of EMA{config.trend_period}
                </label>
                {config.trend_filter && (
                  <input
                    type="range"
                    min={20}
                    max={250}
                    value={config.trend_period}
                    onChange={(e) => patch({ trend_period: +e.target.value })}
                    className="w-full accent-cyan-500"
                  />
                )}

                <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.volume_filter}
                    onChange={(e) => patch({ volume_filter: e.target.checked })}
                    className="accent-cyan-500"
                  />
                  Require volume ≥ {config.volume_multiplier}× the {config.volume_lookback}-bar average
                </label>
                {config.volume_filter && (
                  <input
                    type="range"
                    min={0.5}
                    max={5}
                    step={0.1}
                    value={config.volume_multiplier}
                    onChange={(e) => patch({ volume_multiplier: +e.target.value })}
                    className="w-full accent-amber-500"
                  />
                )}

                <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.adx_filter}
                    onChange={(e) => patch({ adx_filter: e.target.checked })}
                    className="accent-cyan-500"
                  />
                  Require a real trend — ADX ≥ {config.adx_threshold}
                </label>
                {config.adx_filter && (
                  <div className="space-y-1 pl-6">
                    <input
                      type="range"
                      min={10}
                      max={40}
                      value={config.adx_threshold}
                      onChange={(e) => patch({ adx_threshold: +e.target.value })}
                      className="w-full accent-cyan-500"
                    />
                    <p className="text-[10px] text-slate-600 leading-relaxed">
                      A crossover is a trend-following signal. In a flat range it sells the dip and buys the bounce —
                      the classic whipsaw. ADX measures trend strength regardless of direction; below 20 means there
                      is no trend to follow. On real NSE data this removed 45% of crossovers.
                    </p>
                  </div>
                )}

                <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.pattern_filter}
                    onChange={(e) => patch({ pattern_filter: e.target.checked })}
                    className="accent-cyan-500"
                  />
                  Require a confirming candlestick pattern
                </label>
                {config.pattern_filter && (
                  <div className="space-y-1 pl-6">
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min={1}
                        max={10}
                        value={config.pattern_lookback}
                        onChange={(e) => patch({ pattern_lookback: +e.target.value })}
                        className="flex-1 accent-cyan-500"
                      />
                      <span className="text-[11px] text-slate-400 font-mono w-24">
                        within {config.pattern_lookback} bar{config.pattern_lookback === 1 ? "" : "s"}
                      </span>
                    </div>
                    <p className="text-[10px] text-slate-600 leading-relaxed">
                      A hammer/engulfing/marubozu matching the signal direction must appear on the signal bar or up to
                      this many bars before it. A doji never confirms — it means neither side kept control, which is
                      not agreement. On real NSE data this kept ~20% of crossovers at 3 bars, ~7% at 1 bar.
                    </p>
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3 border-t border-border pt-3">
                  <Field label="Min price (₹)">
                    <input
                      type="number"
                      min={0}
                      step={10}
                      value={config.min_price || ""}
                      placeholder="no limit"
                      onChange={(e) => patch({ min_price: parseFloat(e.target.value) || 0 })}
                      className={inputCls}
                    />
                  </Field>
                  <Field label="Max price (₹)">
                    <input
                      type="number"
                      min={0}
                      step={10}
                      value={config.max_price || ""}
                      placeholder="no limit"
                      onChange={(e) => patch({ max_price: parseFloat(e.target.value) || 0 })}
                      className={inputCls}
                    />
                  </Field>
                </div>
                <p className="text-[10px] text-slate-600 leading-relaxed -mt-1">
                  Only scan stocks whose current price is inside this band. Leave blank for no limit. Useful because a
                  ₹1 lakh account cannot meaningfully size a position in a ₹13,000 stock — scanning it just produces
                  alerts you cannot act on.
                </p>
                {config.max_price > 0 && config.min_price > config.max_price && (
                  <p className="text-[11px] text-loss">Min price cannot be above max price.</p>
                )}

                <div className="grid grid-cols-2 gap-3 border-t border-border pt-3">
                  <Field label={`Cooldown — ${config.cooldown_minutes} min`}>
                    <input
                      type="range"
                      min={0}
                      max={240}
                      step={5}
                      value={config.cooldown_minutes}
                      onChange={(e) => patch({ cooldown_minutes: +e.target.value })}
                      className="w-full accent-cyan-500"
                    />
                  </Field>
                  <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer self-end pb-1">
                    <input
                      type="checkbox"
                      checked={config.once_per_session}
                      onChange={(e) => patch({ once_per_session: e.target.checked })}
                      className="accent-cyan-500"
                    />
                    Max 1 alert per stock per session
                  </label>
                </div>

                <button
                  type="button"
                  onClick={save}
                  disabled={
                    busy ||
                    config.fast_period >= config.slow_period ||
                    (config.max_price > 0 && config.min_price > config.max_price)
                  }
                  className="w-full py-2 rounded-md bg-bot/20 text-bot text-xs font-semibold hover:bg-bot/30 transition disabled:opacity-40"
                >
                  Save strategy
                </button>
              </fieldset>
            )}
          </div>

          {/* whatsapp */}
          <div className="rounded-lg border border-border bg-surface p-4 space-y-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-slate-200">WhatsApp alerts</h2>
              {activeChannel && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-profit/15 text-profit">
                  {activeChannel.provider} enabled
                </span>
              )}
            </div>

            <div className="flex rounded-md border border-border overflow-hidden text-[11px]">
              {["callmebot", "twilio"].map((p) => (
                <button
                  key={p}
                  onClick={() => setProvider(p)}
                  className={clsx(
                    "px-2.5 py-1.5 flex-1 transition",
                    provider === p ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200"
                  )}
                >
                  {p === "callmebot" ? "CallMeBot (simple)" : "Twilio"}
                </button>
              ))}
            </div>

            <p className="text-[10px] text-slate-500 leading-relaxed">
              {provider === "callmebot"
                ? "WhatsApp +34 623 75 84 18 with exactly: “I allow callmebot to send me messages”. If that bot does not reply within 2 minutes it is likely down — CallMeBot runs a backup bot at +34 623 78 64 49, so try that number with the same message. Already had a key? Send “Recover APIKey”."
                : "Needs a Twilio account with the WhatsApp sandbox or an approved sender."}
            </p>

            <Field label={provider === "callmebot" ? "Your phone (with country code)" : "Destination number"}>
              <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="+919876543210" className={inputCls} />
            </Field>
            <Field label={provider === "callmebot" ? "CallMeBot API key" : "account_sid:auth_token"}>
              <input
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder={provider === "callmebot" ? "123456" : "ACxxxx:your_auth_token"}
                className={inputCls}
              />
            </Field>
            {provider === "twilio" && (
              <Field label="Twilio WhatsApp 'from' number">
                <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="+14155238886" className={inputCls} />
              </Field>
            )}

            <div className="flex gap-2">
              <button
                onClick={saveChannel}
                disabled={busy || !target || !secret}
                className="flex-1 py-2 rounded-md bg-bot/20 text-bot text-xs font-semibold hover:bg-bot/30 transition disabled:opacity-40"
              >
                Save &amp; enable
              </button>
              <button
                onClick={testAlert}
                disabled={busy}
                className="flex items-center gap-1.5 px-3 py-2 rounded-md border border-border text-xs text-slate-300 hover:bg-white/5 transition"
              >
                <Send size={12} /> Test
              </button>
              {activeChannel && (
                <button
                  onClick={() => act(() => api.deleteScanChannel(activeChannel.provider))}
                  disabled={busy}
                  title="Remove saved credentials"
                  className="px-2 py-2 rounded-md border border-border text-slate-500 hover:text-loss transition"
                >
                  <Trash2 size={12} />
                </button>
              )}
            </div>

            {channelMsg && <p className="text-[11px] text-slate-300">{channelMsg}</p>}
            <p className="text-[10px] text-slate-600">
              Credentials are Fernet-encrypted at rest and never returned by the API. A signal with no enabled channel
              is still logged below with status SKIPPED, so nothing disappears silently.
            </p>
            {provider === "callmebot" && (
              <p className="text-[10px] text-slate-600">
                CallMeBot is a free community service and its bots do go down — numbers have changed before. Check{" "}
                <a
                  href="https://www.callmebot.com/blog/free-api-whatsapp-messages/"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-bot hover:underline"
                >
                  the setup page
                </a>{" "}
                or the{" "}
                <a
                  href="https://www.callmebot.com/?ae_global_templates=setup-whatsapp-for-dead-bot"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-bot hover:underline"
                >
                  dead-bot backup page
                </a>
                . If neither bot responds, use Twilio — it does not depend on a shared community bot being up.
              </p>
            )}
          </div>
        </div>

        {/* signal log */}
        <div className="rounded-lg border border-border bg-surface overflow-hidden">
          <div className="px-4 py-3 border-b border-border text-sm font-medium text-slate-200">
            Live Signal Log <span className="text-xs text-slate-500">({signals.length})</span>
          </div>
          <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-left text-[11px] text-slate-500 border-b border-border">
                  <th className="px-4 py-2 font-medium">Time</th>
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 font-medium">TF</th>
                  <th className="px-3 py-2 font-medium">Signal</th>
                  <th className="px-3 py-2 font-medium text-right">Price</th>
                  <th className="px-3 py-2 font-medium">What happened</th>
                  <th className="px-3 py-2 font-medium">Candle</th>
                  <th className="px-3 py-2 font-medium">Alert</th>
                </tr>
              </thead>
              <tbody>
                {signals.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-xs text-slate-500">
                      No signals yet. Start the scanner, or run one scan to check your configuration matches something.
                    </td>
                  </tr>
                )}
                {signals.map((s) => {
                  const open = expanded === s.id;
                  // Surfaced as a badge because a hair's-breadth cross is the
                  // single most misleading thing this log can show.
                  const weak = s.plain_english.some((l) => l.includes("very narrow cross"));
                  return (
                    <Fragment key={s.id}>
                      <tr
                        onClick={() => setExpanded(open ? null : s.id)}
                        className="border-b border-border/50 last:border-0 align-top cursor-pointer hover:bg-white/[0.03] transition"
                      >
                        <td className="px-4 py-2 font-mono text-[11px] text-slate-500 whitespace-nowrap">
                          {open ? (
                            <ChevronDown size={11} className="inline mr-1" />
                          ) : (
                            <ChevronRight size={11} className="inline mr-1" />
                          )}
                          {s.created_at ? (
                            <>
                              <span className="text-slate-400">
                                {new Date(s.created_at + "Z").toLocaleDateString("en-IN", {
                                  day: "2-digit",
                                  month: "short",
                                })}
                              </span>{" "}
                              {new Date(s.created_at + "Z").toLocaleTimeString("en-IN")}
                            </>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="px-3 py-2 text-slate-100">{s.symbol}</td>
                        <td className="px-3 py-2 text-[11px] text-slate-500">{s.timeframe}</td>
                        <td
                          className={clsx(
                            "px-3 py-2 text-xs font-medium",
                            s.side === "BUY" ? "text-profit" : "text-loss"
                          )}
                        >
                          {s.side}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-right">{s.price.toFixed(2)}</td>
                        <td className="px-3 py-2 text-[11px] text-slate-300 max-w-[340px]">
                          {s.side === "BUY"
                            ? "Short-term average rose above the longer one — momentum turning up"
                            : "Short-term average fell below the longer one — momentum turning down"}
                          {weak && (
                            <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-400">
                              WEAK · razor-thin gap
                            </span>
                          )}
                          {s.adx_value !== null && s.adx_value !== undefined && (
                            <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-bot/15 text-bot">
                              ADX {s.adx_value}
                            </span>
                          )}
                          <div className="text-[10px] text-slate-600 mt-0.5">click for the full explanation</div>
                        </td>
                        <td className="px-3 py-2 text-[11px] max-w-[220px]">
                          <div className="flex items-start gap-2">
                            <span className="shrink-0 pt-0.5">
                              <CandleIcon ohlc={s.candle_ohlc} title={s.candle_desc || undefined} />
                            </span>
                            <div className="min-w-0">
                          {/* The CONFIRMING pattern leads, because that is the
                              one that actually gated the signal. It may sit a
                              bar or two before the signal bar, so showing the
                              signal bar's own shape first read as "no named
                              pattern" on a properly confirmed signal. */}
                          {s.pattern ? (
                            <>
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-profit/15 text-profit">
                                ✓ {s.pattern.replace(/_/g, " ").toLowerCase()}
                              </span>
                              <div className="text-[9px] text-slate-500 mt-0.5">confirmed the signal</div>
                            </>
                          ) : s.candle_pattern ? (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-300">
                              {s.candle_pattern.replace(/_/g, " ").toLowerCase()}
                            </span>
                          ) : (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/40 text-slate-400">
                              no named pattern
                            </span>
                          )}
                          {s.candle_desc && (
                            <div className="text-[10px] text-slate-500 mt-0.5 leading-snug">
                              {s.candle_desc.split(" — ")[0]}
                            </div>
                          )}
                            </div>
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className={clsx(
                              "text-[10px] px-1.5 py-0.5 rounded",
                              STATUS_STYLES[s.alert_status] ?? "bg-slate-700/40 text-slate-400"
                            )}
                            title={s.alert_error ?? undefined}
                          >
                            {s.alert_status}
                          </span>
                          {s.alert_error && (
                            <div className="text-[10px] text-slate-600 mt-0.5 max-w-[220px]">{s.alert_error}</div>
                          )}
                        </td>
                      </tr>
                      {open && (
                        <tr className="border-b border-border/50 bg-base/60">
                          <td colSpan={8} className="px-4 py-3">
                            <div className="space-y-1.5 max-w-3xl">
                              {s.candle_ohlc && (
                                <div className="flex items-center gap-3 pb-2 mb-1 border-b border-border/60">
                                  <CandleIcon ohlc={s.candle_ohlc} size={44} />
                                  <div className="text-[11px] text-slate-400 leading-relaxed">
                                    <div className="text-slate-300">The signal candle, drawn to scale</div>
                                    <div className="font-mono text-[10px] text-slate-500">
                                      O {s.candle_ohlc[0].toFixed(2)} · H {s.candle_ohlc[1].toFixed(2)} · L{" "}
                                      {s.candle_ohlc[2].toFixed(2)} · C {s.candle_ohlc[3].toFixed(2)}
                                    </div>
                                  </div>
                                </div>
                              )}
                              {s.plain_english.length === 0 && (
                                <p className="text-[11px] text-slate-500">
                                  This signal was recorded before plain-English explanations existed. Newer signals
                                  include them.
                                </p>
                              )}
                              {s.plain_english.map((line, i) => (
                                <p key={i} className="text-[11px] text-slate-300 leading-relaxed flex gap-2">
                                  <span className="text-slate-600 shrink-0">{i + 1}.</span>
                                  <span>{line}</span>
                                </p>
                              ))}
                              <p className="text-[10px] text-slate-600 pt-1.5 border-t border-border/60 font-mono">
                                technical: {s.reasons.join(" · ")}
                              </p>
                              <p className="text-[10px] text-slate-600">
                                This is an observation of what the chart did, not advice. The scanner never places an
                                order.
                              </p>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}

const inputCls =
  "bg-base border border-border rounded-md px-2 py-1.5 text-xs text-slate-200 w-full focus:outline-none focus:border-bot/60";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="text-[11px] text-slate-400 flex flex-col gap-1">
      {label}
      {children}
    </label>
  );
}
