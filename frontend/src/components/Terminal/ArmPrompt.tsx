"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, X } from "lucide-react";
import { inr, smaApi, type ArmTarget, type SmaConfig, type StockConfig } from "@/lib/smaApi";

type Fields = { qty: string; sl: string; trail: string; target: string };

function fieldsOf(c: StockConfig): Fields {
  return {
    qty: String(c.qty ?? ""),
    sl: String(c.tsl_sl_points ?? 20),
    trail: String(c.tsl_trail_points ?? 10),
    target: String(c.tsl_target_points ?? 0),
  };
}

/** What stop the stock uses now, in words. */
function stopInUse(c: StockConfig): string {
  if (c.use_stop === false) return "Stop is OFF";
  if (c.stop_type === "TSL") return "Trailing stop (₹)";
  if (c.stop_type === "SMA_GAP") return `SMA gap stop (×${c.gap_sl_mult ?? 1})`;
  return `ATR stop (${c.atr_multiplier ?? 1.5}× ATR)`;
}

/**
 * Asked when a stock is armed: quantity, stop loss, trail and target, filled
 * from the settings (this stock's own, else the shared ones). "Okay" arms with
 * them as they are; "Change" edits them for this stock only, then arms.
 *
 * `target` names the desk (Scalp page); without it the page's own desk is used.
 * `onArm(save)` arms the stock: it may ask its own LIVE question first, then
 * must call `save()` before arming so a changed stop is in place first.
 */
export function ArmPrompt({
  symbol,
  deskName,
  target,
  price,
  live = false,
  onArm,
  onClose,
}: {
  symbol: string;
  deskName?: string;
  target?: ArmTarget;
  price?: number | null;
  live?: boolean;
  onArm: (save: () => Promise<void>) => Promise<void>;
  onClose: () => void;
}) {
  const [cfg, setCfg] = useState<StockConfig | null>(null);
  const [f, setF] = useState<Fields | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    (target === undefined ? smaApi.stockConfig(symbol) : smaApi.stockConfigOn(target, symbol))
      .then((c) => {
        if (!alive) return;
        setCfg(c);
        setF(fieldsOf(c));
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : "Could not load the settings"));
    return () => {
      alive = false;
    };
  }, [symbol, target]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && close.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy]);

  useEffect(() => {
    if (editing) box.current?.querySelector<HTMLInputElement>("input")?.focus();
  }, [editing]);

  const num = (v: string) => (v.trim() === "" ? NaN : Number(v));
  const bad = (() => {
    if (!f) return null;
    const qty = num(f.qty);
    if (!Number.isInteger(qty) || qty < 1) return "Quantity is a whole number, 1 or more.";
    if (!(num(f.sl) > 0)) return "Stop loss is more than ₹0.";
    if (!(num(f.trail) > 0)) return "Trail is more than ₹0.";
    if (!(num(f.target) >= 0)) return "Target is ₹0 (none) or more.";
    return null;
  })();

  /** Only what was changed is saved; a changed stop number puts this stock on the trailing stop. */
  const changes = (): Partial<SmaConfig> => {
    if (!cfg || !f) return {};
    const base = fieldsOf(cfg);
    const body: Partial<SmaConfig> = {};
    if (num(f.qty) !== num(base.qty)) body.qty = num(f.qty);
    const stopChanged = f.sl !== base.sl || f.trail !== base.trail || f.target !== base.target;
    if (stopChanged) {
      Object.assign(body, {
        stop_type: "TSL",
        use_stop: true,
        tsl_sl_points: num(f.sl),
        tsl_trail_points: num(f.trail),
        tsl_target_points: num(f.target),
      });
    }
    return body;
  };

  const go = async (withChanges: boolean) => {
    if (withChanges && bad) {
      setError(bad);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = withChanges ? changes() : {};
      await onArm(async () => {
        if (!Object.keys(body).length) return;
        if (target === undefined) await smaApi.saveStockConfig(symbol, body);
        else await smaApi.saveStockConfigOn(target, symbol, body);
      });
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not arm the stock");
    } finally {
      setBusy(false);
    }
  };

  const stopChanged = (() => {
    if (!cfg || !f) return false;
    const base = fieldsOf(cfg);
    return f.sl !== base.sl || f.trail !== base.trail || f.target !== base.target;
  })();
  const own = cfg?.own ?? {};
  const fromStock = (keys: (keyof SmaConfig)[]) => keys.some((k) => k in own);

  const row = (label: string, key: keyof Fields, hint: string, ownKeys: (keyof SmaConfig)[], unit = "₹") => (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2 py-1.5">
      <div>
        <div className="text-slate-200">{label}</div>
        <div className="text-[11px] text-slate-500">
          {hint}
          {fromStock(ownKeys) ? " · this stock's own" : " · from Settings"}
        </div>
      </div>
      {editing && f ? (
        <input
          type="text"
          inputMode={key === "qty" ? "numeric" : "decimal"}
          value={f[key]}
          onChange={(e) => setF({ ...f, [key]: e.target.value })}
          aria-label={label}
          className="min-h-9 w-28 rounded-md border border-white/15 bg-black/30 px-2 text-right font-mono text-slate-100"
        />
      ) : (
        <span className="font-mono font-semibold text-slate-100">
          {f ? (unit === "₹" ? `₹${f[key]}` : f[key]) : "…"}
          {key === "target" && f && num(f.target) === 0 ? <span className="ml-1 text-xs font-normal text-slate-400">(none)</span> : null}
        </span>
      )}
    </div>
  );

  return createPortal(
    <div
      className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label={`Arm ${symbol}`}
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-border bg-surface p-4 text-sm shadow-xl"
      >
        <div className="mb-1 flex items-center justify-between">
          <h2 className="font-semibold text-slate-100">
            Arm {symbol}
            {deskName ? <span className="font-normal text-slate-400"> on {deskName}</span> : null}
            {live ? <span className="ml-2 rounded bg-rose-500/20 px-1.5 py-0.5 text-[10px] font-bold text-rose-300">LIVE</span> : null}
          </h2>
          <button type="button" aria-label="Close" disabled={busy} onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mb-2 text-xs text-slate-400">
          {editing ? "Change any number for this stock only. Settings stay as they are for the others." : "These come from the settings. Okay arms with them, or Change them for this stock."}
        </p>

        {cfg && f ? (
          <div className="divide-y divide-white/5">
            {row("Quantity", "qty", "Shares per order", ["qty"], "")}
            {row("Stop loss", "sl", "₹ from the entry price", ["tsl_sl_points", "stop_type", "use_stop"])}
            {row("Trail stop loss", "trail", "Moves the stop ₹ per ₹ gained", ["tsl_trail_points"])}
            {row("Target", "target", "₹ from entry, 0 = none", ["tsl_target_points"])}
            <div className="grid grid-cols-[1fr_auto] items-center gap-2 py-1.5">
              <div>
                <div className="text-slate-200">Entry price</div>
                <div className="text-[11px] text-slate-500">The bot enters at the market price when its signal fires</div>
              </div>
              <span className="font-mono text-slate-300">{price != null && price > 0 ? `now ${inr(price)}` : "at market"}</span>
            </div>
          </div>
        ) : error ? null : (
          <p className="flex items-center gap-2 py-4 text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Loading the settings…
          </p>
        )}

        {cfg ? (
          <p className="mt-2 rounded-md bg-white/5 px-2 py-1.5 text-xs text-slate-300">
            Stop in use for {symbol}: <b>{stopInUse(cfg)}</b>.
            {cfg.stop_type !== "TSL" || cfg.use_stop === false
              ? editing && stopChanged
                ? " Saving puts this stock on the trailing stop above."
                : " The stop, trail and target above apply only on the trailing stop; Change one to switch this stock to it."
              : null}
          </p>
        ) : null}

        {error ? (
          <p role="alert" className="mt-2 text-xs text-rose-300">
            {error}
          </p>
        ) : null}

        <div className="mt-3 flex flex-wrap justify-end gap-2">
          {editing ? (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  if (cfg) setF(fieldsOf(cfg));
                  setEditing(false);
                  setError(null);
                }}
                className="min-h-9 rounded-md px-3 text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
              >
                Back to settings
              </button>
              <button
                type="button"
                disabled={busy || !cfg || bad != null}
                onClick={() => void go(true)}
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-600 px-3 font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
              >
                {busy ? <Loader2 size={14} className="animate-spin" /> : null} Save &amp; arm
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                disabled={busy || !cfg}
                onClick={() => setEditing(true)}
                className="min-h-9 rounded-md px-3 text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
              >
                Change
              </button>
              <button
                type="button"
                disabled={busy || !cfg}
                onClick={() => void go(false)}
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-emerald-600 px-3 font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {busy ? <Loader2 size={14} className="animate-spin" /> : null} Okay, arm
              </button>
            </>
          )}
        </div>
        {editing && bad ? <p className="mt-2 text-right text-[11px] text-amber-300">{bad}</p> : null}
      </div>
    </div>,
    document.body
  );
}
