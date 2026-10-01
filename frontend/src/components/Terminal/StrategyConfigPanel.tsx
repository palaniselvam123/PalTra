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
      <div className="text-[11px] uppercase tracking-[0.14em] text-slate-500">Strategy & risk</div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <Field label="Symbol" value={form.symbol} onChange={(v) => set("symbol", v.toUpperCase())} />
        <Field label="Quantity" value={String(form.qty)} onChange={(v) => set("qty", v)} />
        <Field label="Fast MA" value={String(form.sma_fast)} onChange={(v) => set("sma_fast", v)} />
        <Field label="Slow MA" value={String(form.sma_slow)} onChange={(v) => set("sma_slow", v)} />
        <Field label="ATR period" value={String(form.atr_period)} onChange={(v) => set("atr_period", v)} />
        <Field label="ATR SL ×" value={String(form.atr_multiplier)} onChange={(v) => set("atr_multiplier", v)} />
        <Field label="ADX threshold" value={String(form.adx_threshold)} onChange={(v) => set("adx_threshold", v)} />
        <Field label="Max daily loss ₹" value={String(form.max_daily_loss)} onChange={(v) => set("max_daily_loss", v)} />
        <Field label="Max trades / day" value={String(form.max_trades_per_day)} onChange={(v) => set("max_trades_per_day", v)} />
        <Field label="Square-off" value={form.square_off_time} onChange={(v) => set("square_off_time", v)} />
      </div>
      <p className="mt-2 text-[11px] leading-snug text-slate-500">
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
        Exchange stop-loss at {form.atr_multiplier}× ATR. Uncheck to enter with no stop order.
      </label>
      <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={form.use_adx_filter}
          onChange={(e) => set("use_adx_filter", e.target.checked)}
          className="accent-[#10B981]"
        />
        ADX trend filter (block entries when ADX is below the threshold)
      </label>
      <p className="mt-3 text-[11px] leading-snug text-slate-500">
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
        {msg && <span className="text-xs text-slate-400">{msg}</span>}
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
      <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
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
