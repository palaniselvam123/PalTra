"use client";

import { NewTag } from "@/components/ui/NewTag";
import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Layers, Loader2 } from "lucide-react";
import { ApiError, currentDesk, smaApi, type SmaConfig, type TslBasketResult } from "@/lib/smaApi";

const SIZES = [0, 1, 2, 3, 5, 10, 15] as const;
const AUTO_SEC = 60;
const input = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";

function inr(v: number | null | undefined): string {
  return v == null ? "—" : `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function deskParam(): string {
  const d = currentDesk();
  return d === "live" ? "1" : d === "research" ? "research" : d.replace("bot", "");
}

const FIT: Record<string, { text: string; cls: string; why: string }> = {
  tight: { text: "too tight", cls: "text-amber-300", why: "under 1 ATR: normal wiggle can hit it" },
  wide: { text: "too wide", cls: "text-rose-300", why: "over 3 ATR: a hit loses far more than needed" },
  fits: { text: "fits", cls: "text-emerald-300", why: "between 1 and 3 ATR" },
  unknown: { text: "—", cls: "text-slate-500", why: "no ATR yet" },
};

/**
 * One trailing stop, trail and target for every stock the bot has armed or holds (tsl_calc.basket, GET
 * /api/tsl-calc/basket). Each stock's own ATR on the bot's candle gives its ₹ and % numbers; the card then offers two
 * common settings for all of them: one % of the price (the median), with a warning for the stocks it fits badly, and
 * ATR multiples (each stock sized from its own ATR at entry). Either fills the form; nothing changes until Save.
 */
export function BasketStopCalculator({
  form,
  set,
  smartMode = false,
}: {
  form: SmaConfig;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  /** Shown under the Smart stop: only each stock's own numbers, no buttons that switch to another stop type. */
  smartMode?: boolean;
}) {
  const [minutes, setMinutes] = useState<number>(0);
  const [stopMult, setStopMult] = useState(1.5);
  const [trailMult, setTrailMult] = useState(0.5);
  const [targetMult, setTargetMult] = useState(0);
  const [atrPeriod, setAtrPeriod] = useState(Number(form.atr_period ?? 14) || 14);
  const [out, setOut] = useState<TslBasketResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [updated, setUpdated] = useState<string>("");
  const [auto, setAuto] = useState(true);
  const inFlight = useRef(false);

  const run = useCallback(async (quiet = false) => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (!quiet) setBusy(true);
    setNote(null);
    try {
      setOut(await smaApi.tslBasket(deskParam(), minutes, atrPeriod, stopMult, trailMult, targetMult));
      setUpdated(new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    } catch (err: unknown) {
      if (!quiet) setOut(null);
      setNote(err instanceof ApiError || err instanceof Error ? err.message : "Could not read the candles");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, [minutes, atrPeriod, stopMult, trailMult, targetMult]);

  // Worked out by itself: when the card opens, when a number changes, and every minute while it is on screen.
  useEffect(() => {
    if (!auto) return;
    void run();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void run(true);
    }, AUTO_SEC * 1000);
    return () => window.clearInterval(timer);
  }, [auto, run]);

  const usePercent = () => {
    const p = out?.percent;
    if (!p) return;
    set("use_stop", true);
    set("stop_type", "TSL");
    set("tsl_mode", "PERCENT");
    set("tsl_sl_pct", String(p.stop_pct));
    set("tsl_trail_pct", String(p.trail_pct));
    set("tsl_target_pct", String(p.target_pct));
    setNote(`Filled the trailing stop with ${p.stop_pct}% / ${p.trail_pct}% / ${p.target_pct || "no"} target for every stock. Press Save to keep it.`);
  };

  const useAtr = () => {
    set("use_stop", true);
    set("stop_type", "TSL");
    set("tsl_mode", "ATR");
    set("tsl_sl_atr", String(stopMult));
    set("tsl_trail_atr", String(trailMult));
    set("tsl_target_atr", String(targetMult));
    setNote(`Filled the trailing stop with ${stopMult} / ${trailMult} / ${targetMult || "no"} × ATR. Each stock's ₹ is worked out from its own ATR when its trade opens. Press Save to keep it.`);
  };

  const num = (setter: (n: number) => void, lo: number, hi: number) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setter(Math.max(lo, Math.min(hi, Number(e.target.value) || 0)));

  const p = out?.percent;
  return (
    <section aria-label="Stop for all armed stocks" className="mt-3 rounded-lg border border-sky-400/25 bg-sky-500/[0.04] p-3">
      <h4 className="flex items-center gap-1.5 text-sm font-semibold text-slate-100">
        <Layers size={14} aria-hidden /> One stop for all armed stocks <NewTag id="set-basket-stop" />
      </h4>
      <p className="mt-1 text-xs text-slate-400">
        A fixed ₹20 stop is 2% of a ₹1,000 stock but 0.4% of a ₹5,000 one, so one ₹ number never fits a mixed list. This reads
        every stock the bot has armed or holds, on its own candles, and shows two settings that do fit them all: one{" "}
        <b className="text-slate-200">% of the price</b>, or <b className="text-slate-200">ATR multiples</b> (each stock gets a stop
        sized to how far its own candles move, worked out when the trade opens: the better choice for a mixed list). To follow a
        trend: stop 1.5 × ATR, trail every 0.5 × ATR, no target (the trail takes the profit). Nothing changes until Save.
      </p>
      <div className="mt-2 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Candle
          <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className={clsx(input, "w-32")}>
            {SIZES.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? "Bot's own" : `${n} min`}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Stop (× ATR)
          <input inputMode="decimal" value={String(stopMult)} onChange={num(setStopMult, 0, 20)} className={clsx(input, "w-20")} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Trail every (× ATR)
          <input inputMode="decimal" value={String(trailMult)} onChange={num(setTrailMult, 0, 20)} className={clsx(input, "w-20")} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Target (× ATR, 0 = none)
          <input inputMode="decimal" value={String(targetMult)} onChange={num(setTargetMult, 0, 20)} className={clsx(input, "w-20")} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          ATR period
          <input inputMode="numeric" value={String(atrPeriod)} onChange={num((n) => setAtrPeriod(Math.round(n) || 14), 2, 100)} className={clsx(input, "w-20")} />
        </label>
        <button
          type="button"
          disabled={busy || stopMult <= 0 || trailMult <= 0}
          onClick={() => void run()}
          title="Work it out again now"
          className="flex min-h-9 items-center gap-1 rounded-md bg-sky-600 px-3 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} aria-hidden className="animate-spin" /> : <Layers size={13} aria-hidden />} Refresh now
        </button>
        <label className="flex min-h-9 items-center gap-2 text-xs text-slate-300">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="accent-[#10B981]" />
          Work it out by itself every minute{updated ? ` · updated ${updated}` : ""}
        </label>
      </div>

      {out ? (
        <div className="mt-3 space-y-3">
          <div className="text-xs text-slate-400">
            {out.bot_name} ({out.mode}) · {out.minutes}-min candles{out.minutes === out.bot_candle ? " (the bot's own)" : ""} · {out.sized} of{" "}
            {out.stocks.length} stocks sized
            {out.downloaded.length ? ` · downloaded ${out.downloaded.length} from Groww` : ""}
            {Object.keys(out.failed).length ? ` · could not read ${Object.keys(out.failed).join(", ")}` : ""}
          </div>
          {out.stocks.length === 0 ? <p className="text-xs text-amber-200">This bot has no armed or held stocks.</p> : null}

          {smartMode ? (
            <p className="text-xs text-slate-400">
              The Smart stop sizes every stock on its own, from its own candles, and works it out again after every candle while
              a trade is open. The &quot;Smart room&quot; column is what each stock would get if it opened now; nothing here needs to
              be saved.
            </p>
          ) : null}
          {p && !smartMode ? (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-md border border-white/10 bg-black/20 p-3">
                <div className="text-xs font-semibold text-slate-200">A. Same % for every stock</div>
                <div className="mt-1 font-mono text-sm text-slate-100">
                  Stop {p.stop_pct}% · trail every {p.trail_pct}% · {p.target_pct ? `target ${p.target_pct}%` : "no target"}
                </div>
                <p className="mt-1 text-[11px] text-slate-400">
                  The middle of the stocks&apos; own numbers. The stop reaches your entry (no loss left) once the price has gone{" "}
                  {p.breakeven_after_pct ?? "—"}% your way.
                  {p.tight.length ? <span className="text-amber-300"> Too tight for {p.tight.join(", ")}.</span> : null}
                  {p.wide.length ? <span className="text-rose-300"> Too wide for {p.wide.join(", ")}.</span> : null}
                </p>
                <button type="button" onClick={usePercent} className="mt-2 rounded px-2 py-1 text-[11px] font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10">
                  Use this % for all
                </button>
              </div>
              <div className="rounded-md border border-emerald-400/30 bg-emerald-500/[0.05] p-3">
                <div className="text-xs font-semibold text-slate-200">
                  B. Sized by each stock&apos;s ATR <span className="ml-1 rounded bg-emerald-500/20 px-1.5 text-[10px] text-emerald-200">recommended</span>
                </div>
                <div className="mt-1 font-mono text-sm text-slate-100">
                  Stop {out.multiples.stop} × ATR · trail every {out.multiples.trail} × ATR · {out.multiples.target ? `target ${out.multiples.target} × ATR` : "no target"}
                </div>
                <p className="mt-1 text-[11px] text-slate-400">
                  A calm stock gets a small stop, a jumpy one a wider stop, each from its own candles when the trade opens. The ₹
                  each would get today is in the table below.
                </p>
                <button type="button" onClick={useAtr} className="mt-2 rounded px-2 py-1 text-[11px] font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10">
                  Use ATR multiples for all
                </button>
              </div>
            </div>
          ) : null}

          {out.stocks.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-xs">
                <thead className="text-[11px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Stock</th>
                    <th className="py-1 pr-2 text-right font-medium">Price</th>
                    <th className="py-1 pr-2 text-right font-medium">ATR</th>
                    <th className="py-1 pr-2 text-right font-medium">B: stop</th>
                    <th className="py-1 pr-2 text-right font-medium">B: trail</th>
                    <th className="py-1 pr-2 text-right font-medium">B: target</th>
                    <th className="py-1 pr-2 text-right font-medium" title="How far the price must go your way before the stop reaches the entry">
                      No loss after
                    </th>
                    <th className="py-1 pr-2 text-right font-medium">A: stop in ₹</th>
                    <th className="py-1 pr-2 font-medium">A fits?</th>
                    <th className="py-1 pr-2 text-right font-medium" title="The smart stop's room for this stock: its own dips it came back from">
                      Smart room (buy / sell)
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {out.stocks.map((r) => {
                    const a = p?.per_stock.find((x) => x.symbol === r.symbol);
                    const fit = FIT[a?.fit ?? "unknown"];
                    return (
                      <tr key={r.symbol}>
                        <td className="py-1.5 pr-3 font-semibold text-slate-200">{r.symbol}</td>
                        <td className="py-1.5 pr-2 text-right font-mono text-slate-300">{inr(r.price)}</td>
                        {r.stop == null ? (
                          <td colSpan={8} className="py-1.5 text-slate-500">
                            {r.note || "Not enough candles yet."}
                          </td>
                        ) : (
                          <>
                            <td className="py-1.5 pr-2 text-right font-mono text-slate-300">
                              {inr(r.atr)} ({r.atr_pct}%)
                            </td>
                            <td className="py-1.5 pr-2 text-right font-mono text-rose-200">
                              {inr(r.stop)} ({r.stop_pct}%)
                            </td>
                            <td className="py-1.5 pr-2 text-right font-mono text-amber-200">{inr(r.trail)}</td>
                            <td className="py-1.5 pr-2 text-right font-mono text-emerald-200">{r.target ? inr(r.target) : "none"}</td>
                            <td className="py-1.5 pr-2 text-right font-mono text-slate-300">+{inr(r.breakeven_after)}</td>
                            <td className="py-1.5 pr-2 text-right font-mono text-slate-300">
                              {a ? `${inr(a.stop)}${a.stop_atr != null ? ` (${a.stop_atr} ATR)` : ""}` : "—"}
                            </td>
                            <td className={clsx("py-1.5 pr-2", fit.cls)} title={fit.why}>
                              {fit.text}
                            </td>
                            <td className="py-1.5 pr-2 text-right font-mono text-sky-200">
                              <SmartRoom v={r.smart_long} /> / <SmartRoom v={r.smart_short} />
                            </td>
                          </>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-1 text-[11px] text-slate-500">
                Smart room: what the Smart stop would give this stock if a trade opened now (* = too few dips read, a
                fallback). &quot;No loss after&quot;: the trailing stop moves up one trail step for each full step the price gains, so it reaches
                your entry after about stop ÷ trail steps. Before that a turn costs at most the stop; after it, the worst case is a
                small win.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
      {note ? <p className="mt-2 text-xs text-amber-200">{note}</p> : null}
    </section>
  );
}

function SmartRoom({ v }: { v?: { pct: number; rs: number; samples: number; source: string } }) {
  if (!v) return <span className="text-slate-500">—</span>;
  const how = v.source === "dips" ? `${v.samples} dips read` : v.source === "atr" ? "too few dips: ATR distance" : "no candles: smallest room";
  return (
    <span title={how}>
      {inr(v.rs)} ({v.pct}%){v.source === "dips" ? "" : "*"}
    </span>
  );
}
