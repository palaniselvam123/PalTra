"use client";

import clsx from "clsx";
import { AlertTriangle, FlaskConical, Layers, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Navbar } from "@/components/Navbar";
import { Explain } from "@/components/ui/Explain";
import { StatTile } from "@/components/ui";
import { Toggle } from "@/components/ui/tableTools";
import { OptionBook } from "@/components/Options/OptionBook";
import { OptionChainTable, type PickedLeg } from "@/components/Options/OptionChainTable";
import { OptionTicket } from "@/components/Options/OptionTicket";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type OptionBook as Book, type OptionChain, type OptionExpiries } from "@/lib/api";
import { num } from "@/lib/format";

const REFRESH_MS = 5_000;
const STRIKE_WINDOWS = [10, 15, 20, 30, 0] as const; // strikes each side of ATM; 0 = all
const STORE = "options.page.v1";

type Saved = { underlying: string; window: number; greeks: boolean; auto: boolean };

function loadSaved(): Saved {
  const base: Saved = { underlying: "NIFTY", window: 15, greeks: false, auto: true };
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) || "null");
    if (raw && typeof raw === "object") {
      if (typeof raw.underlying === "string" && raw.underlying) base.underlying = raw.underlying;
      if (STRIKE_WINDOWS.includes(raw.window)) base.window = raw.window;
      if (typeof raw.greeks === "boolean") base.greeks = raw.greeks;
      if (typeof raw.auto === "boolean") base.auto = raw.auto;
    }
  } catch {
    /* private window: defaults */
  }
  return base;
}

const SELECT =
  "min-h-10 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400";

export default function OptionsPage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();

  const [saved, setSaved] = useState<Saved>({ underlying: "NIFTY", window: 15, greeks: false, auto: true });
  const [ready, setReady] = useState(false);
  const [lists, setLists] = useState<{ indices: string[]; stocks: string[] }>({
    indices: ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"],
    stocks: [],
  });
  const [expiries, setExpiries] = useState<OptionExpiries | null>(null);
  const [expiry, setExpiry] = useState("");
  const [chain, setChain] = useState<OptionChain | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<PickedLeg | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [filled, setFilled] = useState<string | null>(null);

  useEffect(() => {
    setSaved(loadSaved());
    setReady(true);
    api
      .optionUnderlyings()
      .then((r) => setLists({ indices: r.indices, stocks: r.stocks }))
      .catch(() => undefined);
  }, []);
  const update = useCallback((patch: Partial<Saved>) => {
    setSaved((prev) => {
      const next = { ...prev, ...patch };
      try {
        localStorage.setItem(STORE, JSON.stringify(next));
      } catch {
        /* this page only */
      }
      return next;
    });
  }, []);

  const underlying = saved.underlying;

  // Expiries of the chosen underlying; the nearest one is picked.
  useEffect(() => {
    if (!ready) return;
    let stop = false;
    setExpiries(null);
    setExpiry("");
    setChain(null);
    setPicked(null);
    api
      .optionExpiries(underlying)
      .then((r) => {
        if (stop) return;
        setExpiries(r);
        setExpiry(r.expiries[0]?.date ?? "");
        setError(r.expiries.length ? null : r.error || `No upcoming expiries for ${underlying}.`);
      })
      .catch((e) => !stop && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      stop = true;
    };
  }, [underlying, ready]);

  const loadChain = useCallback(
    async (force = false) => {
      if (!expiry) return;
      setLoading(true);
      try {
        const c = await api.optionChain(underlying, expiry, force);
        setChain(c);
        setError(c.error);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [underlying, expiry]
  );
  const loadBook = useCallback(() => {
    api
      .optionBook()
      .then(setBook)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    setChain(null);
    setPicked(null);
    loadChain();
  }, [loadChain]);
  useEffect(() => {
    loadBook();
  }, [loadBook]);
  useEffect(() => {
    if (!saved.auto || !expiry) return;
    const id = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      loadChain();
      loadBook();
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [saved.auto, expiry, loadChain, loadBook]);

  // Keep the ticket's price fresh as the chain refreshes.
  useEffect(() => {
    if (!picked || !chain) return;
    const row = chain.rows.find((r) => r.strike === picked.strike);
    const leg = picked.type === "CE" ? row?.ce : row?.pe;
    if (leg && leg.ltp !== picked.leg.ltp) setPicked({ ...picked, leg });
  }, [chain, picked]);

  const rows = useMemo(() => {
    if (!chain) return [];
    if (!saved.window || chain.summary.atm == null) return chain.rows;
    const at = chain.rows.findIndex((r) => r.strike === chain.summary.atm);
    if (at < 0) return chain.rows;
    return chain.rows.slice(Math.max(0, at - saved.window), at + saved.window + 1);
  }, [chain, saved.window]);

  const lotSize = expiries?.expiries.find((e) => e.date === expiry)?.lot_size ?? null;
  const s = chain?.summary;
  const source = chain?.source ?? expiries?.source;
  const stockOptions = lists.stocks.filter((x) => !lists.indices.includes(x));

  return (
    <div className="min-h-screen bg-base text-slate-100">
      <Navbar
        connected={connected}
        totalPnl={summaryLoad === "ok" ? summary.total_pnl : null}
        killSwitchActive={killSwitchActive}
        onKillSwitch={killSwitch}
        onResetKillSwitch={resetKillSwitch}
        feed={feed}
        onFeedChanged={setFeed}
        botRunning={bot?.enabled ?? false}
      />

      <main className="mx-auto max-w-7xl space-y-4 px-4 py-6">
        <header className="space-y-1">
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Layers size={20} className="text-bot" aria-hidden />
            Options
          </h1>
          <Explain lead="Groww's option chain for an index or F&O stock, with open interest, IV and Greeks, and a practice book to trade it.">
            Prices, open interest and Greeks come from Groww&apos;s option-chain API (it needs Groww&apos;s paid API plan).
            Without a Groww login the page runs on a simulated chain so you can still practise. PCR is total put OI ÷
            call OI; max pain is the expiry price at which option buyers would be paid least; the biggest call OI is
            often read as resistance and the biggest put OI as support.
          </Explain>
        </header>

        <p className="flex items-start gap-2 rounded-lg border border-accentSky/30 bg-accentSky/10 px-4 py-2 text-sm text-accentSky">
          <FlaskConical size={16} className="mt-0.5 shrink-0" aria-hidden />
          Practice only: orders on this page are paper fills at the chain&apos;s last price. Nothing is sent to Groww.
        </p>

        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Underlying
            <select value={underlying} onChange={(e) => update({ underlying: e.target.value })} className={SELECT}>
              <optgroup label="Indices">
                {lists.indices.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </optgroup>
              {stockOptions.length ? (
                <optgroup label="Stocks">
                  {stockOptions.map((u) => (
                    <option key={u} value={u}>
                      {u}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              {!lists.indices.includes(underlying) && !stockOptions.includes(underlying) ? (
                <option value={underlying}>{underlying}</option>
              ) : null}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Expiry
            <select
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
              disabled={!expiries?.expiries.length}
              className={SELECT}
            >
              {(expiries?.expiries ?? []).map((e) => (
                <option key={e.date} value={e.date}>
                  {new Date(`${e.date}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}
                  {e.lot_size ? ` · lot ${e.lot_size}` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Strikes
            <select
              value={saved.window}
              onChange={(e) => update({ window: Number(e.target.value) })}
              className={SELECT}
            >
              {STRIKE_WINDOWS.map((w) => (
                <option key={w} value={w}>
                  {w ? `${w} each side of ATM` : "All strikes"}
                </option>
              ))}
            </select>
          </label>
          <Toggle label="Greeks" checked={saved.greeks} onChange={(v) => update({ greeks: v })} />
          <Toggle
            label={`Auto refresh ${REFRESH_MS / 1000}s`}
            checked={saved.auto}
            onChange={(v) => update({ auto: v })}
          />
          <button
            type="button"
            onClick={() => {
              loadChain(true);
              loadBook();
            }}
            disabled={!expiry || loading}
            className="flex min-h-10 items-center gap-1.5 rounded-md px-3 text-sm text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
          >
            {loading ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <RefreshCw size={14} aria-hidden />}
            Refresh
          </button>
          {source ? (
            <span
              className={clsx(
                "ml-auto rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset",
                source === "groww" ? "text-profit ring-profit/40" : "text-amber-300 ring-amber-300/40"
              )}
              title={source === "groww" ? "Live from Groww's option-chain API" : "No Groww login: simulated prices"}
            >
              {source === "groww" ? "Groww live" : "Simulated chain"}
            </span>
          ) : null}
        </div>

        {error && (
          <div role="alert" className="flex items-start gap-2 rounded-lg border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
            <span>{error}</span>
          </div>
        )}
        {chain?.note && <p className="text-xs text-amber-300">{chain.note}</p>}
        {filled && (
          <p role="status" className="rounded-lg border border-profit/40 bg-profit/10 px-4 py-2 text-sm text-profit">
            {filled}
          </p>
        )}

        {chain && s ? (
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-card border border-border bg-surface p-4 sm:grid-cols-4 lg:grid-cols-8">
            <StatTile
              label="Spot"
              value={chain.spot != null ? chain.spot.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}
            />
            <StatTile label="ATM strike" value={s.atm != null ? s.atm.toLocaleString("en-IN") : "—"} />
            <StatTile
              label="PCR (OI)"
              value={num(s.pcr)}
              sub={s.pcr == null ? undefined : s.pcr > 1 ? "more puts written" : "more calls written"}
            />
            <StatTile label="Max pain" value={s.max_pain != null ? s.max_pain.toLocaleString("en-IN") : "—"} />
            <StatTile label="Resistance" value={s.resistance != null ? s.resistance.toLocaleString("en-IN") : "—"} sub="most call OI" />
            <StatTile label="Support" value={s.support != null ? s.support.toLocaleString("en-IN") : "—"} sub="most put OI" />
            <StatTile label="ATM IV" value={s.atm_iv != null ? `${num(s.atm_iv, 1)}%` : "—"} />
            <StatTile
              label="ATM straddle"
              value={num(s.straddle)}
              sub={
                s.straddle != null && chain.spot
                  ? `± ${num((s.straddle / chain.spot) * 100, 2)}% by expiry${chain.days_to_expiry != null ? ` · ${num(chain.days_to_expiry, 1)} d` : ""}`
                  : undefined
              }
            />
          </div>
        ) : null}

        <div className={clsx("grid gap-4", picked && "lg:grid-cols-[1fr_320px]")}>
          <div className="min-w-0">
            {chain && rows.length ? (
              <OptionChainTable chain={chain} rows={rows} greeks={saved.greeks} picked={picked} onPick={setPicked} />
            ) : (
              <div className="rounded-card border border-border bg-surface px-4 py-10 text-center text-sm text-slate-500">
                {loading || (!chain && !error) ? "Loading the option chain…" : "No strikes to show."}
              </div>
            )}
            {chain ? (
              <p className="mt-1 text-[11px] text-slate-500">
                Shaded cells are in the money. Click a price to buy or sell it in the practice book. Updated{" "}
                {new Date(chain.fetched_at).toLocaleTimeString("en-IN")}.
              </p>
            ) : null}
          </div>
          {picked && chain ? (
            <div className="lg:sticky lg:top-24 lg:self-start">
              <OptionTicket
                underlying={underlying}
                expiry={expiry}
                lotSize={lotSize}
                picked={picked}
                feed={chain.source}
                onClose={() => setPicked(null)}
                onFilled={(row) => {
                  setFilled(
                    `Practice ${row.side === "BUY" ? "bought" : "sold"} ${row.quantity} ${row.underlying} ${row.strike} ${row.option_type} at ${num(row.entry_price)}.`
                  );
                  window.setTimeout(() => setFilled(null), 6000);
                  loadBook();
                }}
              />
            </div>
          ) : null}
        </div>

        <OptionBook book={book} onChanged={loadBook} />
      </main>
    </div>
  );
}
