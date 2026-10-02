"use client";

import { useEffect, useRef, useState } from "react";
import { smaApi, type SmaConfig } from "@/lib/smaApi";

type Props = { config: SmaConfig | null; onChanged: () => void };

export function StrategyConfigPanel({ config, onChanged }: Props) {
  const [form, setForm] = useState<SmaConfig | null>(config);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dirty = useRef(false);

  useEffect(() => {
    if (!dirty.current) setForm(config);
  }, [config]);
  if (!form) return null;

  const set = (key: keyof SmaConfig, value: string | boolean) => {
    dirty.current = true;
    setForm((prev) => {
      if (!prev) return prev;
      if (typeof value === "boolean" || typeof prev[key] === "boolean") {
        return { ...prev, [key]: Boolean(value) };
      }
      // Keep the typed text, including a trailing decimal, until Save parses it.
      return { ...prev, [key]: value };
    });
    setMsg(null);
  };

  const save = async () => {
    if (!form) return;
    const cap = Number(form.max_trades_per_day);
    if (!Number.isInteger(cap) || cap < 1 || cap > 100) {
      setMsg("Max trades / day must be a whole number from 1 to 100.");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await smaApi.saveConfig({
        symbol: form.symbol,
        qty: Number(form.qty),
        sma_fast: Number(form.sma_fast),
        sma_slow: Number(form.sma_slow),
        atr_period: Number(form.atr_period),
        atr_multiplier: Number(form.atr_multiplier),
        use_adx_filter: form.use_adx_filter,
        use_stop: form.use_stop !== false,
        stop_type: form.stop_type === "SMA_GAP" || form.stop_type === "TSL" ? form.stop_type : "ATR",
        tsl_sl_points: Number(form.tsl_sl_points ?? 20),
        tsl_trail_points: Number(form.tsl_trail_points ?? 10),
        tsl_target_points: Number(form.tsl_target_points ?? 0),
        gap_sl_mult: Number(form.gap_sl_mult ?? 1),
        gap_tp_mult: Number(form.gap_tp_mult ?? 2),
        gap_min_pct: Number(form.gap_min_pct ?? 0.2),
        adx_threshold: Number(form.adx_threshold),
        use_vwap: Boolean(form.use_vwap),
        use_volume: Boolean(form.use_volume),
        volume_min_ratio: Number(form.volume_min_ratio ?? 1),
        use_density: Boolean(form.use_density),
        density_min_pct: Number(form.density_min_pct ?? 50),
        use_rsi: Boolean(form.use_rsi),
        rsi_long_min: Number(form.rsi_long_min ?? 40),
        rsi_long_max: Number(form.rsi_long_max ?? 70),
        rsi_short_min: Number(form.rsi_short_min ?? 30),
        rsi_short_max: Number(form.rsi_short_max ?? 60),
        max_daily_loss: Number(form.max_daily_loss),
        max_trades_per_day: Number(form.max_trades_per_day),
        square_off_time: form.square_off_time,
        entry_cutoff_time: form.entry_cutoff_time || "15:00",
      });
      dirty.current = false;
      setMsg("Saved. Press Start bot. Open positions stay open.");
      onChanged();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="text-[11px] uppercase tracking-[0.14em] text-slate-400">Strategy & risk</div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <Field label="Symbol" value={form.symbol} onChange={(v) => set("symbol", v.toUpperCase())} />
        <Field label="Quantity" value={String(form.qty)} onChange={(v) => set("qty", v)} />
        <Field label="Fast MA" value={String(form.sma_fast)} onChange={(v) => set("sma_fast", v)} />
        <Field label="Slow MA" value={String(form.sma_slow)} onChange={(v) => set("sma_slow", v)} />
        <Field label="ATR period" value={String(form.atr_period)} onChange={(v) => set("atr_period", v)} />
        <Field label="ATR SL ×" value={String(form.atr_multiplier)} onChange={(v) => set("atr_multiplier", v)} />
        <Field label="ADX threshold" value={String(form.adx_threshold)} onChange={(v) => set("adx_threshold", v)} />
        <Field label="Max daily loss ₹" value={String(form.max_daily_loss)} onChange={(v) => set("max_daily_loss", v)} />
        <Field label="Max trades / day (1–100)" value={String(form.max_trades_per_day)} onChange={(v) => set("max_trades_per_day", v)} />
        <Field
          label="No new entries after"
          value={form.entry_cutoff_time || "15:00"}
          onChange={(v) => set("entry_cutoff_time", v)}
        />
        <Field label="Square-off" value={form.square_off_time} onChange={(v) => set("square_off_time", v)} />
      </div>
      <p className="mt-2 text-[11px] leading-snug text-slate-400">
        If the bot stopped on the trade cap, type a higher max and press Save, then Start. Open positions
        stay open. A loss-limit stop stays locked.
      </p>
      <label className="mt-3 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={form.use_stop !== false}
          onChange={(e) => set("use_stop", e.target.checked)}
          className="accent-[#10B981]"
        />
        {form.stop_type === "SMA_GAP"
          ? "Stop-loss on new entries (moving, from the SMA gap). Uncheck to enter with no stop."
          : form.stop_type === "TSL"
            ? "Trailing stop-loss on new entries. Uncheck to enter with no stop order."
            : `Exchange stop-loss at ${form.atr_multiplier}× ATR. Uncheck to enter with no stop order.`}
      </label>
      <label className="mt-3 block text-sm text-slate-300">
        <span className="text-[11px] uppercase tracking-wider text-slate-400">Stop type</span>
        <select
          value={form.stop_type === "SMA_GAP" || form.stop_type === "TSL" ? form.stop_type : "ATR"}
          onChange={(e) => set("stop_type", e.target.value)}
          className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
        >
          <option value="ATR">ATR — fixed stop at {String(form.atr_multiplier)}× ATR</option>
          <option value="SMA_GAP">SMA gap — moving stop + target</option>
          <option value="TSL">Trailing stop (TSL) — ₹ steps, like Groww</option>
        </select>
      </label>
      {form.stop_type === "SMA_GAP" ? (
        <div className="mt-2 rounded-md border border-sky-400/20 bg-sky-400/[0.04] p-2">
          <div className="grid grid-cols-3 gap-2">
            <Field label="Stop × gap" value={String(form.gap_sl_mult ?? 1)} onChange={(v) => set("gap_sl_mult", v)} />
            <Field label="Target × gap" value={String(form.gap_tp_mult ?? 2)} onChange={(v) => set("gap_tp_mult", v)} />
            <Field label="Min gap %" value={String(form.gap_min_pct ?? 0.2)} onChange={(v) => set("gap_min_pct", v)} />
          </div>
          <p className="mt-2 text-[11px] leading-snug text-slate-400">
            Gap % = SMA 9 vs SMA 21 on the last closed candle (at least the min gap). A buy gets stop = price −
            gap × stop multiple and target = price + gap × target multiple; a sell is the mirror. Recalculated every
            closed 1-minute candle: the stop only moves in your favour, the target follows the gap both ways.
            PAPER only — in LIVE the bot keeps the {String(form.atr_multiplier)}× ATR exchange stop.
          </p>
        </div>
      ) : null}
      {form.stop_type === "TSL" ? (
        <div className="mt-2 rounded-md border border-emerald-400/20 bg-emerald-400/[0.04] p-2">
          <div className="grid grid-cols-3 items-end gap-2">
            <Field label="Stop ₹" value={String(form.tsl_sl_points ?? 20)} onChange={(v) => set("tsl_sl_points", v)} />
            <Field label="Trail every ₹" value={String(form.tsl_trail_points ?? 10)} onChange={(v) => set("tsl_trail_points", v)} />
            <Field label="Target ₹" value={String(form.tsl_target_points ?? 0)} onChange={(v) => set("tsl_target_points", v)} />
          </div>
          <p className="mt-2 text-[11px] leading-snug text-slate-400">
            Stop ₹ is the distance from your entry; Target ₹ 0 means no target. A buy at ₹1,000 with stop ₹
            {String(form.tsl_sl_points ?? 20)} starts its stop at ₹
            {(1000 - Number(form.tsl_sl_points ?? 20)).toLocaleString("en-IN")}. Each ₹{String(form.tsl_trail_points ?? 10)} the
            price gains past its best so far moves the stop up ₹{String(form.tsl_trail_points ?? 10)}; it never moves back. A
            sell is the mirror. {Number(form.tsl_target_points ?? 0) > 0
              ? `The trade also closes at ₹${String(form.tsl_target_points)} profit per share.`
              : "No target: the trailing stop, an opposite cross or square-off closes the trade."}{" "}
            In LIVE the bot moves your Groww stop order in place, so the position always has a stop.
          </p>
        </div>
      ) : null}
      <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={form.use_adx_filter}
          onChange={(e) => set("use_adx_filter", e.target.checked)}
          className="accent-[#10B981]"
        />
        ADX trend filter (block entries when ADX is below the threshold)
      </label>
      <p className="mt-3 text-[11px] leading-snug text-slate-400">
        These apply only when checked, on a crossover and on Force order. An unchecked box is ignored. A
        close still happens on the opposite cross.
      </p>
      <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={Boolean(form.use_vwap)}
          onChange={(e) => set("use_vwap", e.target.checked)}
          className="accent-[#10B981]"
        />
        VWAP. Buy at or above today’s VWAP. Sell at or below it.
      </label>
      <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={Boolean(form.use_volume)}
          onChange={(e) => set("use_volume", e.target.checked)}
          className="accent-[#10B981]"
        />
        Volume. The closed candle must be at least this multiple of the previous 20 candles.
      </label>
      <Field
        label="Volume multiple"
        value={String(form.volume_min_ratio ?? 1)}
        onChange={(v) => set("volume_min_ratio", v)}
      />
      <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={Boolean(form.use_density)}
          onChange={(e) => set("use_density", e.target.checked)}
          className="accent-[#10B981]"
        />
        Density. The candle body must cover at least this percent of its high-to-low range.
      </label>
      <Field
        label="Density %"
        value={String(form.density_min_pct ?? 50)}
        onChange={(v) => set("density_min_pct", v)}
      />
      <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={Boolean(form.use_rsi)}
          onChange={(e) => set("use_rsi", e.target.checked)}
          className="accent-[#10B981]"
        />
        RSI(14). A buy must sit in the buy range. A sell must sit in the sell range.
      </label>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <Field label="Buy RSI from" value={String(form.rsi_long_min ?? 40)} onChange={(v) => set("rsi_long_min", v)} />
        <Field label="Buy RSI to" value={String(form.rsi_long_max ?? 70)} onChange={(v) => set("rsi_long_max", v)} />
        <Field label="Sell RSI from" value={String(form.rsi_short_min ?? 30)} onChange={(v) => set("rsi_short_min", v)} />
        <Field label="Sell RSI to" value={String(form.rsi_short_max ?? 60)} onChange={(v) => set("rsi_short_max", v)} />
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button
          disabled={busy}
          onClick={save}
          className="rounded-md bg-white/10 px-3 py-1.5 text-sm font-medium text-slate-100 hover:bg-white/15"
        >
          Save
        </button>
        {msg && (
          <span role="status" className={msg.startsWith("Saved") ? "text-xs text-slate-400" : "text-xs font-semibold text-amber-300"}>
            {msg}
          </span>
        )}
      </div>
    </section>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const [text, setText] = useState(value);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(value);
  }, [value]);
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-slate-400">{label}</span>
      <input
        inputMode="decimal"
        value={text}
        onFocus={() => {
          focused.current = true;
        }}
        onBlur={() => {
          focused.current = false;
        }}
        onChange={(e) => {
          setText(e.target.value);
          onChange(e.target.value);
        }}
        className="mt-0.5 w-full rounded-md border border-white/10 bg-black/40 px-2 py-1.5 font-mono text-sm text-[#f8fafc] outline-none focus:border-[#10B981]/50"
      />
    </label>
  );
}
