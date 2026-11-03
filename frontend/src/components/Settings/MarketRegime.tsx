"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { ApiError, smaApi, type MarketRegimeView } from "@/lib/smaApi";

const CHECKS: { key: string; label: string }[] = [
  { key: "outside_prev_range_or_narrow_cpr", label: "Outside yesterday, or a narrow CPR" },
  { key: "breadth_aligned", label: "Breadth" },
  { key: "sector_confluence", label: "Banks and IT with the basket" },
  { key: "vwap_separation", label: "VWAP separation" },
  { key: "moderate_opening_gap", label: "Moderate opening gap" },
  { key: "adx_and_chop_confirm", label: "ADX and chop" },
];

const REGIME_LABEL: Record<string, string> = {
  TREND_DAY: "Trend day",
  SELECTIVE_STOCK_SPECIFIC: "Selective",
  SIDEWAYS_CHOP: "Sideways",
};

function sizeWord(mult: number | undefined): string {
  if (mult == null) return "";
  if (mult >= 1) return "full size";
  if (mult > 0) return "half size";
  return "trend entries paused";
}

/**
 * 09:45 / 11:15 / 13:30 market regime (market_regime.py). Off until the switch is on.
 * The basket is an equal-weight proxy, not the official Nifty.
 */
export function MarketRegime() {
  const [view, setView] = useState<MarketRegimeView | null>(null);
  const [on, setOn] = useState(false);
  const [rvol, setRvol] = useState("1.5");
  const [atr, setAtr] = useState("0.25");
  const [note, setNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback((fill: boolean) => {
    smaApi
      .marketRegime()
      .then((v) => {
        setView(v);
        if (fill) {
          setOn(v.on);
          setRvol(String(v.rvol_min));
          setAtr(String(v.atr_pct_min));
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load(true);
    const poll = setInterval(() => load(false), view?.running ? 2000 : 10000);
    return () => clearInterval(poll);
  }, [load, view?.running]);

  const liveConfirm = async (turningOn: boolean) => {
    if (!turningOn) return true;
    const live = await smaApi.bots().then((rows) => rows.some((row) => row.mode === "LIVE")).catch(() => false);
    if (!live) return true;
    return window.confirm(
      "A bot is LIVE. With this on, the next moving-average or breakout entry can be halved, or paused, on the real Groww account. Save?"
    );
  };

  const save = async () => {
    const rvolMin = Math.max(0.5, Number(rvol) || 1.5);
    const atrMin = Math.max(0.05, Number(atr) || 0.25);
    if (!(await liveConfirm(on))) return;
    setSaving(true);
    try {
      const saved = await smaApi.saveMarketRegime({ on, rvol_min: rvolMin, atr_pct_min: atrMin, confirm_live: on });
      setView(saved);
      setOn(saved.on);
      setRvol(String(saved.rvol_min));
      setAtr(String(saved.atr_pct_min));
      setNote(saved.on ? "Saved. The next checkpoint sizes trend entries." : "Saved: off. Order size is unchanged.");
    } catch (err: unknown) {
      setNote(err instanceof ApiError || err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  const run = async () => {
    if (view?.on && !(await liveConfirm(true))) return;
    setSaving(true);
    try {
      await smaApi.runMarketRegime(Boolean(view?.on));
      setNote("Reading the basket. This takes a minute.");
      load(false);
    } catch (err: unknown) {
      setNote(err instanceof ApiError || err instanceof Error ? err.message : "Could not start");
    } finally {
      setSaving(false);
    }
  };

  const reading = view?.reading;
  const regime = reading?.regime || "";
  const rules = view?.rules;

  return (
    <section id="market-regime" aria-label="Market regime" className="terminal-dark mb-4 scroll-mt-20 rounded-xl border border-white/10 p-4">
      <h2 className="text-base font-semibold text-slate-100">Market regime at 9:45, 11:15 and 1:30</h2>
      <p className="mt-1 text-xs leading-relaxed text-slate-400">
        Scores six opening checks and sets the size of the next moving-average or breakout entry on Bots 1–5.
        {rules ? (
          <>
            {" "}
            {rules.trend_at}–6 is a trend day (full size). {rules.selective_at}–{rules.trend_at - 1} is selective: half
            size, and only a stock whose relative volume is at least the floor and whose 5-minute ATR is at least the
            floor percent of price. Below that, those entries are paused. A choppiness index above {rules.chop_hard} on
            the basket or on the stock refuses the entry either way.
          </>
        ) : null}{" "}
        News, Force, a hand-off, a fast-move and candle pattern or colour entries are left alone. Replays and the
        research desk are not included. Off until this switch is on.
      </p>
      <p className="mt-2 text-xs leading-relaxed text-slate-500">
        Groww does not serve the Nifty index here, so the series is an equal-weight basket
        {view ? ` (${view.basket.banks.length} banks, ${view.basket.it.length} IT, ${view.basket.other.length} others)` : ""}.
        Breadth and the two sectors are counted inside that basket. It is not the official Nifty or the NSE advance/decline line.
      </p>

      <label className="mt-3 flex items-center gap-2 text-sm text-slate-200">
        <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} className="h-4 w-4 accent-sky-400" />
        Use the regime for trend entries
      </label>

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Selective: relative volume at least
          <input
            inputMode="decimal"
            value={rvol}
            onChange={(e) => setRvol(e.target.value)}
            className="min-h-9 w-28 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Selective: 5-minute ATR at least (% of price)
          <input
            inputMode="decimal"
            value={atr}
            onChange={(e) => setAtr(e.target.value)}
            className="min-h-9 w-28 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          />
        </label>
        <button
          type="button"
          disabled={saving}
          onClick={save}
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
        >
          Save
        </button>
        <button
          type="button"
          disabled={saving || view?.running}
          onClick={run}
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-white/20 hover:bg-white/5 disabled:opacity-50"
        >
          {view?.running ? "Reading…" : "Run now"}
        </button>
      </div>
      {note ? <p className="mt-2 text-xs text-slate-400">{note}</p> : null}

      {reading?.regime ? (
        <div className="mt-4 rounded-lg border border-white/10 bg-black/20 p-3">
          <p className="text-sm text-slate-100">
            <span
              className={clsx(
                "font-semibold",
                regime === "TREND_DAY" && "text-emerald-300",
                regime === "SELECTIVE_STOCK_SPECIFIC" && "text-amber-300",
                regime === "SIDEWAYS_CHOP" && "text-slate-300"
              )}
            >
              {REGIME_LABEL[regime] || regime}
            </span>{" "}
            {reading.score}/{reading.max_score} at {reading.checkpoint} · {sizeWord(reading.position_size_multiplier)}
            {view?.applies ? "" : " · switch off, not applied"}
          </p>
          <p className="mt-1 font-mono text-xs text-slate-400">
            ADX {reading.adx_value ?? "—"} · chop {reading.chop_value ?? "—"} · CPR {reading.cpr_width_pct ?? "—"}%
          </p>
          <ul className="mt-2 space-y-1 text-xs">
            {CHECKS.map(({ key, label }) => {
              const passed = reading.breakdown?.[key];
              return (
                <li key={key} className={passed ? "text-emerald-300/90" : "text-slate-500"}>
                  {passed ? "Yes" : "No"} · {label}
                  {reading.notes?.[key] ? <span className="text-slate-500"> — {reading.notes[key]}</span> : null}
                </li>
              );
            })}
          </ul>
          {reading.last_error ? <p className="mt-2 text-xs text-amber-300/90">Last reading failed: {reading.last_error}</p> : null}
        </div>
      ) : reading?.error ? (
        <p className="mt-3 text-xs text-amber-300/90">{reading.error}</p>
      ) : (
        <p className="mt-3 text-xs text-slate-500">No reading yet today. The first one runs at 9:45 while the switch is on, or press Run now.</p>
      )}
    </section>
  );
}
