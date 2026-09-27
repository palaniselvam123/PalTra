"use client";

import { useEffect, useState } from "react";
import { smaApi, type SmaConfig } from "@/lib/smaApi";

type Props = { config: SmaConfig | null; onChanged: () => void };

export function StrategyConfigPanel({ config, onChanged }: Props) {
  const [form, setForm] = useState<SmaConfig | null>(config);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setForm(config), [config]);
  if (!form) return null;

  const set = (key: keyof SmaConfig, value: string | boolean) => {
    setForm((prev) => {
      if (!prev) return prev;
      if (typeof prev[key] === "boolean") return { ...prev, [key]: Boolean(value) };
      if (typeof prev[key] === "number") return { ...prev, [key]: Number(value) };
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
        adx_threshold: Number(form.adx_threshold),
        max_daily_loss: Number(form.max_daily_loss),
        max_trades_per_day: Number(form.max_trades_per_day),
        square_off_time: form.square_off_time,
      });
      setMsg("Saved");
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
        <Field label="Max trades" value={String(form.max_trades_per_day)} onChange={(v) => set("max_trades_per_day", v)} />
        <Field label="Square-off" value={form.square_off_time} onChange={(v) => set("square_off_time", v)} />
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={form.use_adx_filter}
          onChange={(e) => set("use_adx_filter", e.target.checked)}
          className="accent-[#10B981]"
        />
        ADX trend filter (block entries when ADX is below the threshold)
      </label>
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
  return (
    <label className="block">
      <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-0.5 w-full rounded-md border border-white/10 bg-black/30 px-2 py-1.5 font-mono text-sm text-slate-100 outline-none focus:border-[#10B981]/50"
      />
    </label>
  );
}
