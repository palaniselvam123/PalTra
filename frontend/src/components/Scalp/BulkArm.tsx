"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import clsx from "clsx";
import { ExternalLink, FlaskConical, Loader2, X } from "lucide-react";
import { api, type LinkServer } from "@/lib/api";
import { replayActive, smaApi, type SmaConfig } from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";
import { ArmServerSwitch, backtestTerminalUrl, type ArmDesk, type ArmServerChoice } from "@/components/Scalp/ArmPicker";

// The trailing stop in ₹ (points) or % of the entry price (tsl_mode).
type Unit = "POINTS" | "PERCENT";
type Fields = { qty: string; sl: string; trail: string; target: string; unit: Unit };
type Result = { symbol: string; ok: boolean; why?: string };

const BACKTEST_DAYS = Array.from({ length: 30 }, (_, i) => i + 1);

function fieldsOf(c: Partial<SmaConfig>, unit: Unit = c.tsl_mode === "PERCENT" ? "PERCENT" : "POINTS"): Fields {
  const pct = unit === "PERCENT";
  return {
    qty: String(c.qty ?? 1),
    sl: String(pct ? c.tsl_sl_pct ?? 1 : c.tsl_sl_points ?? 20),
    trail: String(pct ? c.tsl_trail_pct ?? 0.5 : c.tsl_trail_points ?? 10),
    target: String(pct ? c.tsl_target_pct ?? 0 : c.tsl_target_points ?? 0),
    unit,
  };
}

/**
 * Arm several ticked stocks on one bot at once, each with the same quantity,
 * stop loss, trail and target (the bot's settings to start with). The numbers
 * are saved as each stock's own trailing stop, so a backtest of these stocks
 * on that bot uses them. Arming only puts them on the bot's Trade list: they
 * order on that bot's next SMA cross, not now. A LIVE bot asks once more first,
 * and a stock another LIVE bot already trades is skipped (one LIVE bot per stock).
 *
 * When done on a PAPER bot (on the trading desk), "Backtest these" replays the
 * stocks on past days with that bot's settings: practice money, no order.
 *
 * On the scanner with a backtest server, `server` adds the Main desk / Backtest server switch:
 * on the backtest server the stocks go on its PAPER copies of the bots, and the result links there
 * to run the backtest. On the backtest server the same switch arms the desk's bots (over the link)
 * or this server's own PAPER copies. `linkTo` says where the calls go: over the link to that
 * server, or null for this server's own terminal.
 */
export function BulkArm({
  symbols,
  desks,
  linkTo,
  source,
  onDone,
  onClose,
  server,
  emptyReason,
}: {
  symbols: string[];
  desks: ArmDesk[];
  linkTo: LinkServer | null;
  source?: string | null;
  onDone: () => void;
  onClose: () => void;
  server?: ArmServerChoice;
  /** Why no bot could be listed (the scanner's link error), shown instead of the plain message. */
  emptyReason?: string | null;
}) {
  const where = server?.value ?? "desk";
  const names = symbols.map((s) => s.toUpperCase());
  const [desk, setDesk] = useState<ArmDesk | null>(null);
  const [f, setF] = useState<Fields | null>(null);
  // The settings the boxes came from, so switching ₹ / % shows that unit's saved numbers.
  const [loaded, setLoaded] = useState<Partial<SmaConfig>>({});
  const [applyAll, setApplyAll] = useState(true);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  const [results, setResults] = useState<Result[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [btDays, setBtDays] = useState(10);
  const [btNote, setBtNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [btBusy, setBtBusy] = useState(false);

  // The bot's own settings fill the boxes (on the scanner, the first stock's settings on that desk bot).
  useEffect(() => {
    if (!desk) return;
    let alive = true;
    setF(null);
    setError(null);
    const t = desk.target;
    const load: Promise<Partial<SmaConfig>> = linkTo
      ? api.deskStock(t, names[0], linkTo)
      : t === "research"
        ? smaApi.researchConfig()
        : smaApi.configOn(t);
    load
      .then((c) => {
        if (!alive) return;
        setLoaded(c);
        setF(fieldsOf(c));
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setF(fieldsOf({}));
        setError(`Could not read ${desk.name}'s settings (${e instanceof Error ? e.message : "no answer"}); the boxes show plain defaults.`);
      });
    return () => {
      alive = false;
    };
    // `names` follows `symbols`, fixed for the life of the dialog.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desk, linkTo]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const num = (v: string) => (v.trim() === "" ? NaN : Number(v));
  const bad = (() => {
    if (!f || !applyAll) return null;
    const qty = num(f.qty);
    if (!Number.isInteger(qty) || qty < 1) return "Quantity is a whole number, 1 or more.";
    const u = f.unit === "PERCENT" ? "0%" : "₹0";
    if (!(num(f.sl) > 0)) return `Stop loss is more than ${u}.`;
    if (!(num(f.trail) > 0)) return `Trail is more than ${u}.`;
    if (!(num(f.target) >= 0)) return `Target is ${u} (none) or more.`;
    if (f.unit === "PERCENT" && (num(f.sl) > 50 || num(f.trail) > 50 || num(f.target) > 100)) return "Percent stops are at most 50% (target 100%).";
    return null;
  })();

  const live = desk?.mode === "LIVE";
  const liveOwner = (s: string) => desks.find((d) => d.mode === "LIVE" && d.target !== desk?.target && d.armed.includes(s));

  const armAll = async () => {
    if (!desk || !f || bad) return;
    if (live) {
      const ok = window.confirm(
        `Arm ${names.length} stock${names.length === 1 ? "" : "s"} on ${desk.name} for LIVE SMA orders? ${desk.name} can buy or sell each of them with real money on its next SMA cross.`
      );
      if (!ok) return;
    }
    const body: Partial<SmaConfig> = applyAll
      ? {
          qty: num(f.qty),
          stop_type: "TSL",
          use_stop: true,
          tsl_mode: f.unit,
          ...(f.unit === "PERCENT"
            ? { tsl_sl_pct: num(f.sl), tsl_trail_pct: num(f.trail), tsl_target_pct: num(f.target) }
            : { tsl_sl_points: num(f.sl), tsl_trail_points: num(f.trail), tsl_target_points: num(f.target) }),
        }
      : {};
    setBusy(true);
    setDone(0);
    setError(null);
    const out: Result[] = [];
    // One at a time: each arm re-reads and re-saves the bot's settings row.
    for (const s of names) {
      const owner = live ? liveOwner(s) : undefined;
      if (owner) {
        out.push({ symbol: s, ok: false, why: `${owner.name} trades it LIVE — one LIVE bot per stock` });
      } else {
        try {
          if (Object.keys(body).length) {
            if (linkTo) await api.deskSaveStock(desk.target, s, body, live, linkTo);
            else await smaApi.saveStockConfigOn(desk.target, s, body);
          }
          if (!desk.armed.includes(s)) {
            if (linkTo) await api.deskArm({ target: desk.target, symbol: s, source: source ?? null, confirm_live: live }, linkTo);
            else await smaApi.setTradeSymbolOn(desk.target, s, true, source ?? null);
          }
          out.push({ symbol: s, ok: true, why: desk.armed.includes(s) ? "already armed; numbers saved" : undefined });
        } catch (e: unknown) {
          out.push({ symbol: s, ok: false, why: e instanceof Error ? e.message : "could not arm" });
        }
      }
      setDone((n) => n + 1);
    }
    setResults(out);
    setBusy(false);
    onDone();
  };

  const armedOk = results?.filter((r) => r.ok).map((r) => r.symbol) ?? [];
  const canBacktest = !linkTo && desk != null && desk.target !== "research" && !live && armedOk.length > 0;

  const backtest = async () => {
    if (!desk || desk.target === "research" || !armedOk.length) return;
    const bot = desk.target as 1 | 2 | 3 | 4;
    const days = lastClosedWeekdays(btDays);
    setBtBusy(true);
    setBtNote(null);
    try {
      const all = await smaApi.replayAll().catch(() => null);
      const current = all?.[String(bot)];
      if (current && replayActive(current) && current.status !== "FINISHED") {
        if (!window.confirm(`${desk.name} is already replaying. Stop it and start this backtest instead?`)) return;
      }
      await smaApi.replayStart(days[0], "09:15", 300, days[days.length - 1], armedOk, bot);
      setBtNote({
        ok: true,
        text: `Backtest started on ${desk.name}: ${armedOk.length} stock${armedOk.length === 1 ? "" : "s"} · ${days.length === 1 ? days[0] : `${days[0]} → ${days[days.length - 1]}`}, with the numbers above. Practice money, no orders. Results build up in the SMA terminal's Backtests tab.`,
      });
    } catch (e: unknown) {
      setBtNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the backtest" });
    } finally {
      setBtBusy(false);
    }
  };

  const terminalHref = desk && desk.target !== 1 && desk.target !== "research" ? `/terminal/?desk=bot${desk.target}` : "/terminal";

  const field = (label: string, key: Exclude<keyof Fields, "unit">, hint: string) => (
    <label className="grid grid-cols-[1fr_auto] items-center gap-2 py-1.5">
      <span>
        <span className="block text-slate-200">{label}</span>
        <span className="block text-xs text-slate-500">{hint}</span>
      </span>
      <input
        type="text"
        inputMode={key === "qty" ? "numeric" : "decimal"}
        value={f?.[key] ?? ""}
        disabled={!f || busy || !applyAll || results != null}
        onChange={(e) => f && setF({ ...f, [key]: e.target.value })}
        aria-label={label}
        className="min-h-9 w-28 rounded-md border border-white/15 bg-black/30 px-2 text-right font-mono text-slate-100 disabled:opacity-50"
      />
    </label>
  );

  return createPortal(
    <div
      className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Arm ${names.length} stocks`}
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-border bg-surface p-4 text-sm shadow-xl"
      >
        <div className="mb-1 flex items-center justify-between">
          <h2 className="font-semibold text-slate-100">
            Arm {names.length} stock{names.length === 1 ? "" : "s"}
            {desk ? <span className="font-normal text-slate-400"> on {desk.name}</span> : null}
            {live ? <span className="ml-2 rounded bg-rose-500/20 px-1.5 py-0.5 text-xs font-bold text-rose-300">LIVE</span> : null}
          </h2>
          <button type="button" aria-label="Close" disabled={busy} onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mb-2 break-words text-xs text-slate-400">{names.join(", ")}</p>

        {!desk ? (
          <>
            <p className="mb-2 text-xs text-slate-400">Which bot? They go on its Trade list and order on its next SMA cross.</p>
            {server ? <ArmServerSwitch choice={server} /> : null}
            {where === "backtest" && server?.loading ? null : desks.length === 0 ? (
              <p className="text-xs text-amber-300">
                {emptyReason
              ? `No bot can be armed right now: ${emptyReason}`
              : "The SMA terminal is not answering, so no bot can be armed right now."}
              </p>
            ) : (
              <ul className="space-y-1.5">
                {desks.map((d) => (
                  <li key={String(d.target)}>
                    <button
                      type="button"
                      onClick={() => setDesk(d)}
                      className={clsx(
                        "flex min-h-11 w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm ring-1 ring-inset",
                        d.mode === "LIVE" ? "ring-loss/50 hover:bg-loss/10" : "ring-slate-500/30 hover:bg-slate-500/10"
                      )}
                    >
                      <span className="min-w-0">
                        <span className="block font-semibold text-slate-100">{d.name}</span>
                        <span className="block text-[11px] text-slate-400">
                          {d.target === "research"
                            ? "practice money only"
                            : where === "backtest"
                              ? "practice copy on the backtest server"
                              : d.mode === "LIVE"
                              ? "real money — asks you to confirm; cannot be backtested while LIVE"
                              : "practice money · can backtest after arming"}
                          {names.some((s) => d.armed.includes(s))
                            ? ` · ${names.filter((s) => d.armed.includes(s)).length} already armed here`
                            : ""}
                        </span>
                      </span>
                      <span
                        className={clsx(
                          "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold",
                          d.mode === "LIVE" ? "bg-loss/20 text-loss" : "bg-profit/15 text-profit"
                        )}
                      >
                        {d.target === "research" ? "RESEARCH" : d.mode}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <>
            <label className="mb-1 flex cursor-pointer items-center gap-2 text-xs text-slate-300">
              <input
                type="checkbox"
                checked={applyAll}
                disabled={busy || results != null}
                onChange={(e) => setApplyAll(e.target.checked)}
                className="accent-sky-500"
              />
              Use these numbers for every ticked stock (saved as each stock&apos;s own trailing stop)
            </label>
            {!applyAll ? (
              <p className="mb-1 text-xs text-slate-500">Off: each stock is armed with the settings it already has.</p>
            ) : null}
            {f ? (
              <div className="divide-y divide-white/5">
                {field("Quantity", "qty", "Shares per order")}
                <div className="grid grid-cols-[1fr_auto] items-center gap-2 py-1.5">
                  <span>
                    <span className="block text-slate-200">Trailing stop in</span>
                    <span className="block text-xs text-slate-500">₹ points, or % of each entry price</span>
                  </span>
                  <div className="flex overflow-hidden rounded-md border border-white/15" role="group" aria-label="Trailing stop unit">
                    {(["POINTS", "PERCENT"] as const).map((u) => (
                      <button
                        key={u}
                        type="button"
                        aria-pressed={f.unit === u}
                        disabled={busy || !applyAll || results != null}
                        onClick={() => f.unit !== u && setF({ ...fieldsOf(loaded, u), qty: f.qty })}
                        className={clsx(
                          "h-8 px-2.5 text-xs disabled:opacity-50",
                          f.unit === u ? "bg-accentSky/15 font-semibold text-accentSky" : "text-slate-300 hover:bg-white/5"
                        )}
                      >
                        {u === "POINTS" ? "₹" : "%"}
                      </button>
                    ))}
                  </div>
                </div>
                {field("Stop loss", "sl", f.unit === "PERCENT" ? "% below / above the entry price" : "₹ from the entry price")}
                {field("Trail stop loss", "trail", f.unit === "PERCENT" ? "Moves the stop this % per same % gained" : "Moves the stop ₹ per ₹ gained")}
                {field("Target", "target", f.unit === "PERCENT" ? "% from entry, 0 = none" : "₹ from entry, 0 = none")}
              </div>
            ) : (
              <p className="flex items-center gap-2 py-4 text-slate-400">
                <Loader2 size={14} className="animate-spin" /> Loading {desk.name}&apos;s settings…
              </p>
            )}

            {error ? <p className="mt-2 text-xs text-amber-300">{error}</p> : null}
            {bad ? <p className="mt-2 text-xs text-amber-300">{bad}</p> : null}

            {results ? (
              <div className="mt-3 rounded-md bg-white/5 px-2 py-2 text-xs">
                <p className="mb-1 font-semibold text-slate-200">
                  {armedOk.length} of {results.length} armed on {desk.name}
                  {where === "backtest" ? " on the backtest server" : ""}
                  {live ? " (LIVE)" : ""}.{" "}
                  {where === "backtest"
                    ? linkTo
                      ? "Run the backtest there."
                      : "Backtest them below."
                    : "They order on the next SMA cross, not now."}
                </p>
                {where === "backtest" && server?.url && armedOk.length ? (
                  <a
                    href={backtestTerminalUrl(server.url, desk.target)}
                    target="_blank"
                    rel="noreferrer"
                    className="mb-1 inline-flex items-center gap-1 font-semibold text-accentViolet hover:underline"
                  >
                    Open {desk.name} on the backtest server <ExternalLink size={11} aria-hidden />
                  </a>
                ) : null}
                <ul className="space-y-0.5">
                  {results
                    .filter((r) => !r.ok || r.why)
                    .map((r) => (
                      <li key={r.symbol} className={r.ok ? "text-slate-400" : "text-amber-300"}>
                        {r.symbol}: {r.why}
                      </li>
                    ))}
                </ul>
              </div>
            ) : null}

            {canBacktest ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-accentViolet/30 bg-accentViolet/[0.06] px-2 py-2 text-xs text-slate-300">
                <label className="flex items-center gap-1.5">
                  Backtest over the last
                  <select
                    value={btDays}
                    onChange={(e) => setBtDays(Number(e.target.value))}
                    className="rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200"
                  >
                    {BACKTEST_DAYS.map((d) => (
                      <option key={d} value={d}>
                        {d} trading day{d === 1 ? "" : "s"}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={btBusy}
                  onClick={() => void backtest()}
                  className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md bg-accentViolet/15 px-3 font-semibold text-accentViolet ring-1 ring-inset ring-accentViolet/40 hover:bg-accentViolet/25 disabled:opacity-40"
                >
                  {btBusy ? <Loader2 size={13} className="animate-spin" /> : <FlaskConical size={13} />} Backtest these
                </button>
                {btNote ? (
                  <p className={clsx("w-full", btNote.ok ? "text-accentViolet" : "text-amber-300")}>
                    {btNote.text}{" "}
                    {btNote.ok ? (
                      <Link href={terminalHref} className="font-semibold text-accentSky underline-offset-2 hover:underline">
                        Open the SMA terminal →
                      </Link>
                    ) : null}
                  </p>
                ) : null}
              </div>
            ) : null}

            <div className="mt-3 flex flex-wrap justify-end gap-2">
              {results ? (
                <button
                  type="button"
                  onClick={onClose}
                  className="min-h-9 rounded-md px-3 text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5"
                >
                  Done
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setDesk(null);
                      setError(null);
                    }}
                    className="min-h-9 rounded-md px-3 text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
                  >
                    Other bot
                  </button>
                  <button
                    type="button"
                    disabled={busy || !f || bad != null}
                    onClick={() => void armAll()}
                    className={clsx(
                      "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 font-semibold text-white disabled:opacity-50",
                      live ? "bg-rose-600 hover:bg-rose-500" : "bg-emerald-600 hover:bg-emerald-500"
                    )}
                  >
                    {busy ? (
                      <>
                        <Loader2 size={14} className="animate-spin" /> {done}/{names.length}
                      </>
                    ) : (
                      `Arm ${names.length}${live ? " LIVE" : ""}`
                    )}
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>,
    document.body
  );
}

