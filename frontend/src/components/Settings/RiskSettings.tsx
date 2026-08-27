"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { api } from "@/lib/api";

type FieldDef = {
  key: string;
  label: string;
  step: number;
  // Which rupee-equivalent field from the API mirrors this input.
  valueKey?: string;
  // How the rupee preview is derived. Percentages take a slice of capital;
  // leverage is a multiplier, so treating it as a percentage rendered 5× as
  // ₹5,000 instead of ₹5,00,000.
  previewMode?: "pct" | "multiple";
  hint?: string;
};

const FIELDS: FieldDef[] = [
  { key: "account_capital", label: "Account Capital (₹)", step: 1000 },
  {
    key: "risk_per_trade_pct",
    label: "Risk Per Trade (%)",
    step: 0.1,
    valueKey: "risk_per_trade_value",
    hint: "Position size is derived from this and your stop-loss distance.",
  },
  {
    key: "daily_max_loss_pct",
    label: "Daily Max Loss (%)",
    step: 0.1,
    valueKey: "daily_max_loss_value",
    hint: "Squares off everything and locks trading for the day.",
  },
  {
    key: "daily_profit_target_pct",
    label: "Daily Profit Target (%)",
    step: 0.1,
    valueKey: "daily_profit_target_value",
    hint: "Stops for the day once reached, locking the gain. 0 disables it.",
  },
  { key: "max_trades_per_day", label: "Max Trades Per Day", step: 1 },
  { key: "max_spread_pct", label: "Max Bid-Ask Spread (%)", step: 0.01 },
  {
    key: "max_leverage",
    label: "Max Leverage (×)",
    step: 0.5,
    valueKey: "max_position_value",
    previewMode: "multiple",
    hint: "Ceiling on position value. The risk rule alone sizes only on stop distance and never asks what the position costs, so a tight stop can demand far more exposure than the account can carry. Real MIS equity leverage is about 5×.",
  },
  {
    key: "min_edge_multiple",
    label: "Min Edge (× costs)",
    step: 0.1,
    hint: "A target must beat round-trip slippage and charges by this multiple. At 1.0 a winning trade merely breaks even, so anything below that is rejected as unwinnable.",
  },
];

export function RiskSettings() {
  const [config, setConfig] = useState<Record<string, number>>({});
  const [derived, setDerived] = useState<Record<string, number>>({});
  const [dayState, setDayState] = useState<any>(null);
  const [saved, setSaved] = useState(false);

  const load = () => {
    api.getRiskConfig().then((res: any) => {
      const { state, square_off_time_ist, ...rest } = res;
      const editable: Record<string, number> = {};
      const values: Record<string, number> = {};
      for (const [k, v] of Object.entries(rest)) {
        if (k.endsWith("_value")) values[k] = v as number;
        else editable[k] = v as number;
      }
      setConfig(editable);
      setDerived(values);
      setDayState(state);
    });
  };

  useEffect(load, []);

  const update = (key: string, value: number) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  const save = async () => {
    await api.setRiskConfig(config);
    setSaved(true);
    load();
  };

  const capital = config.account_capital ?? 0;
  const preview = (key: string, valueKey?: string, mode: "pct" | "multiple" = "pct") => {
    if (!valueKey) return null;
    // Recompute locally so the rupee figure tracks edits before saving.
    const input = config[key];
    if (input === undefined || !capital) return derived[valueKey];
    const rupees = mode === "multiple" ? capital * input : capital * (input / 100);
    return Math.round(rupees * 100) / 100;
  };

  const lockTone = dayState?.lock_kind === "PROFIT_TARGET" ? "profit" : "loss";

  return (
    <div className="rounded-lg border border-border bg-surface p-5 space-y-4">
      <h2 className="text-sm font-medium text-slate-200">Risk Management Engine</h2>
      <p className="text-xs text-slate-500">
        Enforced server-side before every order — the bot and the manual form both go through it, and neither can
        bypass it. The 15:30 IST auto square-off (the NSE close) is fixed and applies on live market data.
      </p>

      <div className="grid grid-cols-2 gap-3">
        {FIELDS.map((f) => {
          const rupees = preview(f.key, f.valueKey, f.previewMode);
          return (
            <label key={f.key} className="text-xs text-slate-400 flex flex-col gap-1">
              <span className="flex items-baseline justify-between gap-2">
                {f.label}
                {rupees !== null && rupees !== undefined && (
                  <span className="font-mono text-[11px] text-bot">
                    {f.key === "daily_max_loss_pct" ? "−" : ""}₹{Math.abs(rupees).toLocaleString("en-IN")}
                  </span>
                )}
              </span>
              <input
                type="number"
                step={f.step}
                value={config[f.key] ?? ""}
                onChange={(e) => update(f.key, parseFloat(e.target.value))}
                className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 font-mono"
              />
              {f.hint && <span className="text-[10px] text-slate-600 leading-snug">{f.hint}</span>}
            </label>
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          className="px-3 py-1.5 rounded-md bg-bot/20 text-bot text-xs font-medium hover:bg-bot/30 transition"
        >
          Save Risk Config
        </button>
        {saved && <span className="text-xs text-profit">Saved</span>}
      </div>

      {dayState && (
        <div className="border-t border-border pt-3 text-xs space-y-1 font-mono">
          <div className="text-slate-400">Trades taken today: {dayState.trades_taken}</div>
          <div className={dayState.realized_pnl < 0 ? "text-loss" : "text-profit"}>
            Realized P&amp;L: ₹{dayState.realized_pnl.toFixed(2)}
          </div>
          {dayState.locked && (
            <div
              className={clsx(
                "rounded-md px-2 py-1.5 mt-1 font-sans",
                lockTone === "profit" ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
              )}
            >
              {dayState.lock_reason}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
