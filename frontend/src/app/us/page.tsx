"use client";

import clsx from "clsx";
import { AlertTriangle, FlaskConical, Globe, KeyRound, Loader2, Play, Power, Search, Square } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Navbar } from "@/components/Navbar";
import { Explain } from "@/components/ui/Explain";
import { StatTile } from "@/components/ui";
import { UsChart } from "@/components/US/UsChart";
import { useTradingState } from "@/hooks/useTradingState";
import { api, type UsChart as Chart, type UsConfig, type UsGrowwResult, type UsState, type UsTrade } from "@/lib/api";

const REFRESH_MS = 5_000;
const CANDLES = [1, 2, 3, 5, 10, 15, 30];
const INPUT =
  "min-h-10 rounded-md border border-border bg-base px-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400";
const BUTTON =
  "flex min-h-10 items-center gap-1.5 rounded-md px-3 text-sm font-semibold ring-1 ring-inset disabled:opacity-50";

const usd = (v: number | null | undefined, sign = false) =>
  v == null
    ? "—"
    : `${sign ? (v > 0 ? "+" : v < 0 ? "−" : "") : v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;
const hhmm = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";

type Draft = Omit<UsConfig, "symbols" | "running"> & { symbols: string };

function draftOf(c: UsConfig): Draft {
  const { running: _r, symbols, ...rest } = c;
  return { ...rest, symbols: symbols.join(", ") };
}

export default function UsDeskPage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();
  const [st, setSt] = useState<UsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const [chart, setChart] = useState<Chart | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [keys, setKeys] = useState({ key_id: "", secret: "" });
  const [check, setCheck] = useState<UsGrowwResult | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await api.usState();
      setSt(s);
      setError(null);
      setDraft((d) => d ?? draftOf(s.config));
      setSymbol((cur) => cur ?? s.config.symbols[0] ?? s.positions[0]?.symbol ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(() => document.visibilityState === "visible" && load(), REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  useEffect(() => {
    if (!symbol) return;
    let stop = false;
    const get = () =>
      api
        .usChart(symbol)
        .then((c) => !stop && setChart(c))
        .catch(() => undefined);
    get();
    const id = window.setInterval(() => document.visibilityState === "visible" && get(), REFRESH_MS * 2);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [symbol]);

  const act = async (what: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    try {
      await what();
      if (done) setNote(done);
      await load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = () =>
    draft &&
    act(async () => {
      const { symbols, ...rest } = draft;
      const saved = await api.usSaveConfig({ ...rest, symbols: symbols.split(/[\s,;]+/).filter(Boolean) });
      setDraft(draftOf(saved));
    }, "Settings saved.");

  const m = st?.market;
  const k = st?.kpis;
  const sim = st?.feed === "simulated";
  const armed = st?.config.symbols ?? [];
  const tabs = Array.from(new Set([...armed, ...(st?.positions.map((p) => p.symbol) ?? [])]));

  const field = (key: keyof Draft, label: string, type: "number" | "text" = "number", step?: string) => (
    <label className="flex flex-col gap-1 text-xs text-slate-400">
      {label}
      <input
        type={type}
        step={step}
        value={draft ? String(draft[key]) : ""}
        onChange={(e) => draft && setDraft({ ...draft, [key]: type === "number" ? Number(e.target.value) : e.target.value })}
        className={clsx(INPUT, "w-28")}
      />
    </label>
  );
  const box = (key: "stop_on" | "allow_short", label: string) => (
    <label className="flex min-h-10 items-center gap-2 text-sm text-slate-300">
      <input
        type="checkbox"
        checked={Boolean(draft?.[key])}
        onChange={(e) => draft && setDraft({ ...draft, [key]: e.target.checked })}
        className="accent-[#10B981]"
      />
      {label}
    </label>
  );

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
            <Globe size={20} className="text-bot" aria-hidden />
            US practice desk
          </h1>
          <Explain lead="The SMA cross strategy on US stocks, on New York time, with practice money.">
            A buy when SMA fast crosses above SMA slow on a closed candle, a sell (short) when it crosses below; the
            opposite cross closes the trade and opens the reverse. Each trade has an ATR stop and is squared off before
            the close. Prices come from Alpaca (the broker behind Groww US) with a free Alpaca key, else from a simulator.
            The US market is open 09:30–16:00 New York time: 19:00–01:30 IST until 1 Nov, 20:00–02:30 IST after the US
            clocks go back.
          </Explain>
        </header>

        <p className="flex items-start gap-2 rounded-lg border border-accentSky/30 bg-accentSky/10 px-4 py-2 text-sm text-accentSky">
          <FlaskConical size={16} className="mt-0.5 shrink-0" aria-hidden />
          Practice only: every fill is booked on this desk at the last price, in US dollars. No order goes to Groww,
          Alpaca or any other broker.
        </p>

        <div className="flex flex-wrap items-center gap-3">
          {m ? (
            <span
              className={clsx(
                "rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset",
                m.open ? "text-profit ring-profit/40" : "text-slate-400 ring-white/15"
              )}
            >
              {m.open ? `US market open · closes ${m.close_et} NY (${m.close_ist} IST)` : `US market closed · opens ${m.next_open_ist.slice(5)} IST`}
            </span>
          ) : null}
          {m ? (
            <span className="text-xs text-slate-400">
              New York {m.et.slice(11)} · India {m.ist.slice(11)}
            </span>
          ) : null}
          {st ? (
            <span
              className={clsx(
                "rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset",
                sim ? "text-amber-300 ring-amber-300/40" : "text-profit ring-profit/40"
              )}
              title={sim ? "No Alpaca key: simulated prices that trade around the clock" : "Alpaca market data, IEX feed"}
            >
              {sim ? "Simulated prices (trades any time)" : "Alpaca prices (IEX)"}
            </span>
          ) : null}
          <div className="ml-auto flex items-center gap-2">
            {st?.running ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => act(() => api.usRun(false), "Desk stopped.")}
                className={clsx(BUTTON, "text-slate-200 ring-white/20 hover:bg-white/5")}
              >
                <Square size={14} aria-hidden /> Stop
              </button>
            ) : (
              <button
                type="button"
                disabled={busy || !st}
                onClick={() => act(() => api.usRun(true), "Desk started.")}
                className={clsx(BUTTON, "text-profit ring-profit/40 hover:bg-profit/10")}
              >
                <Play size={14} aria-hidden /> Start
              </button>
            )}
            <button
              type="button"
              disabled={busy || !st}
              onClick={() => {
                if (window.confirm("Stop the US desk and close every open practice trade at the last price?"))
                  act(() => api.usCloseAll(), "Desk stopped and practice trades closed.");
              }}
              className={clsx(BUTTON, "text-loss ring-loss/40 hover:bg-loss/10")}
            >
              <Power size={14} aria-hidden /> Close all
            </button>
          </div>
        </div>

        {error || st?.error ? (
          <div role="alert" className="flex items-start gap-2 rounded-lg border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
            <span>{error || st?.error}</span>
          </div>
        ) : null}
        {note ? (
          <p role="status" className="text-xs text-slate-400">
            {note}
          </p>
        ) : null}

        {k ? (
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-card border border-border bg-surface p-4 sm:grid-cols-3 lg:grid-cols-6">
            <StatTile label="Closed P&L today" value={usd(k.closed_pnl, true)} tone="auto" numericValue={k.closed_pnl} sub="before charges" />
            <StatTile label="Open P&L" value={usd(k.open_pnl, true)} tone="auto" numericValue={k.open_pnl} />
            <StatTile label="Trades today" value={`${k.trades}${draft ? ` / ${draft.max_trades_per_day}` : ""}`} />
            <StatTile label="Wins · losses" value={`${k.wins} · ${k.losses}`} />
            <StatTile label="Charges (est.)" value={usd(k.charges)} sub="SEC + FINRA on sells" />
            <StatTile label="Trading day" value={k.day} sub="New York date" />
          </div>
        ) : null}

        <section className="rounded-card border border-border bg-surface p-4">
          <div className="mb-2 flex flex-wrap gap-2">
            {tabs.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSymbol(s)}
                className={clsx(
                  "min-h-9 rounded-md px-3 text-xs font-semibold ring-1 ring-inset",
                  s === symbol ? "bg-accentSky/15 text-accentSky ring-accentSky/50" : "text-slate-300 ring-white/15 hover:bg-white/5"
                )}
              >
                {s} <span className="font-mono font-normal text-slate-400">{st?.prices[s] != null ? `$${st.prices[s]?.toFixed(2)}` : ""}</span>
              </button>
            ))}
            {!tabs.length ? <span className="text-sm text-slate-500">No stocks armed. Add some under Settings below.</span> : null}
          </div>
          <UsChart chart={chart} />
          <p className="mt-1 text-[11px] text-slate-500">
            {chart ? `${chart.candle_minutes}-minute candles, New York time.` : ""} Blue: SMA fast · amber: SMA slow.
          </p>
        </section>

        <div className="grid gap-4 lg:grid-cols-2">
          <TradeTable
            title="Open practice trades"
            rows={st?.positions ?? []}
            open
            busy={busy}
            onClose={(id) => act(() => api.usCloseTrade(id), "Trade closed.")}
          />
          <TradeTable title="Today's trades" rows={st?.trades ?? []} />
        </div>

        <section className="rounded-card border border-border bg-surface p-4">
          <h2 className="text-sm font-semibold">Desk log</h2>
          <ul className="mt-2 max-h-48 space-y-1 overflow-auto text-xs">
            {(st?.events ?? []).map((e, i) => (
              <li key={i} className={clsx(e.level === "WARN" ? "text-amber-300" : "text-slate-300")}>
                <span className="font-mono text-slate-500">{e.at}</span> {e.text}
              </li>
            ))}
            {!st?.events.length ? <li className="text-slate-500">Nothing yet{st?.last_pass ? ` (last pass ${st.last_pass} NY)` : ""}.</li> : null}
          </ul>
        </section>

        <section className="rounded-card border border-border bg-surface p-4">
          <h2 className="text-sm font-semibold">Settings</h2>
          <p className="mt-1 text-xs text-slate-400">Times are New York time. Size is in dollars, whole shares only.</p>
          {draft ? (
            <div className="mt-3 space-y-3">
              <label className="flex flex-col gap-1 text-xs text-slate-400">
                Stocks to trade (US tickers, comma-separated, up to 30)
                <input value={draft.symbols} onChange={(e) => setDraft({ ...draft, symbols: e.target.value })} className={INPUT} placeholder="AAPL, MSFT, NVDA" />
              </label>
              <div className="flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1 text-xs text-slate-400">
                  Candle
                  <select
                    value={draft.candle_minutes}
                    onChange={(e) => setDraft({ ...draft, candle_minutes: Number(e.target.value) })}
                    className={INPUT}
                  >
                    {CANDLES.map((c) => (
                      <option key={c} value={c}>
                        {c} min
                      </option>
                    ))}
                  </select>
                </label>
                {field("sma_fast", "SMA fast")}
                {field("sma_slow", "SMA slow")}
                {field("atr_period", "ATR period")}
                {field("atr_mult", "Stop × ATR", "number", "0.1")}
                {box("stop_on", "ATR stop")}
                {box("allow_short", "Allow shorts")}
              </div>
              <div className="flex flex-wrap items-end gap-3">
                {field("dollars_per_trade", "$ per trade")}
                {field("max_trades_per_day", "Max trades / day")}
                {field("daily_loss_limit", "Day loss limit $")}
                {field("entry_start", "Entries from", "text")}
                {field("entry_cutoff", "Entry cut-off", "text")}
                {field("square_off", "Square-off", "text")}
                <button type="button" disabled={busy} onClick={saveDraft} className={clsx(BUTTON, "text-sky-300 ring-sky-400/40 hover:bg-sky-500/10")}>
                  Save settings
                </button>
              </div>
            </div>
          ) : null}
        </section>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-card border border-border bg-surface p-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <KeyRound size={14} aria-hidden /> Alpaca price key
            </h2>
            <p className="mt-1 text-xs text-slate-400">
              Real US prices need a free Alpaca account (alpaca.markets → Paper Trading → API keys). The key only reads
              prices: the desk checks it against Alpaca&apos;s paper account and never sends an order. It is saved
              encrypted and never shown again.{" "}
              {st?.key_source === "env"
                ? "A key is set on the server (fly secrets)."
                : st?.key_source === "saved"
                  ? "A key is saved."
                  : "No key yet: the desk runs on simulated prices."}
            </p>
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <input
                value={keys.key_id}
                onChange={(e) => setKeys({ ...keys, key_id: e.target.value })}
                placeholder="API key ID"
                autoComplete="off"
                className={clsx(INPUT, "min-w-[180px] flex-1")}
              />
              <input
                type="password"
                value={keys.secret}
                onChange={(e) => setKeys({ ...keys, secret: e.target.value })}
                placeholder="Secret key"
                autoComplete="new-password"
                className={clsx(INPUT, "min-w-[180px] flex-1")}
              />
              <button
                type="button"
                disabled={busy || !keys.key_id || !keys.secret}
                onClick={() =>
                  act(async () => {
                    const r = await api.usSaveKeys(keys.key_id.trim(), keys.secret.trim());
                    setKeys({ key_id: "", secret: "" });
                    setNote(`Alpaca key works (paper account ${r.account.status}). Real US prices from the next pass.`);
                  })
                }
                className={clsx(BUTTON, "text-sky-300 ring-sky-400/40 hover:bg-sky-500/10")}
              >
                Check and save
              </button>
              {st?.key_source === "saved" ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => act(() => api.usDeleteKeys(), "Key removed: simulated prices.")}
                  className={clsx(BUTTON, "text-slate-300 ring-white/15 hover:bg-white/5")}
                >
                  Remove
                </button>
              ) : null}
            </div>
          </section>

          <section className="rounded-card border border-border bg-surface p-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Search size={14} aria-hidden /> Groww US check
            </h2>
            <p className="mt-1 text-xs text-slate-400">
              Asks Groww&apos;s trading API for a US price with your Groww login (read-only, no order). Today the API
              serves NSE, BSE and MCX only. The desk re-checks every 6 hours and sends one Telegram the first time Groww
              answers.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    setCheck(await api.usGrowwCheck(st?.groww.symbol));
                  })
                }
                className={clsx(BUTTON, "text-slate-200 ring-white/15 hover:bg-white/5")}
              >
                {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
                Check now ({st?.groww.symbol ?? "AAPL"})
              </button>
              {st ? (
                <span
                  className={clsx(
                    "rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset",
                    st.groww.ok ? "text-profit ring-profit/40" : "text-slate-400 ring-white/15"
                  )}
                >
                  {st.groww.checked_at
                    ? `${st.groww.ok ? "Groww answers US prices" : "Not yet"} · checked ${new Date(st.groww.checked_at).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}`
                    : st.groww.session
                      ? "Not checked yet"
                      : "Needs a Groww login on the desk"}
                </span>
              ) : null}
            </div>
            {(check?.note ?? st?.groww.note) ? <p className="mt-2 break-words text-xs text-slate-400">{check?.note ?? st?.groww.note}</p> : null}
          </section>
        </div>
      </main>
    </div>
  );
}

function TradeTable({
  title,
  rows,
  open,
  busy,
  onClose,
}: {
  title: string;
  rows: UsTrade[];
  open?: boolean;
  busy?: boolean;
  onClose?: (id: number) => void;
}) {
  return (
    <section className="min-w-0 rounded-card border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">{title}</h2>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 pr-2 font-medium">Stock</th>
              <th className="py-1 pr-2 font-medium">Side</th>
              <th className="py-1 pr-2 text-right font-medium">Qty</th>
              <th className="py-1 pr-2 text-right font-medium">Entry</th>
              <th className="py-1 pr-2 text-right font-medium">{open ? "Last" : "Exit"}</th>
              <th className="py-1 pr-2 text-right font-medium">{open ? "Stop" : "Why"}</th>
              <th className="py-1 pr-2 text-right font-medium">P&amp;L</th>
              {open ? <th className="py-1" /> : <th className="py-1 text-right font-medium">Time (NY)</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5 font-mono tabular-nums">
            {rows.map((r) => {
              const pnl = open ? r.open_pnl : r.pnl;
              return (
                <tr key={r.id}>
                  <td className="py-1.5 pr-2 font-sans font-semibold text-slate-100">{r.symbol}</td>
                  <td className={clsx("py-1.5 pr-2 font-sans", r.direction === "LONG" ? "text-profit" : "text-loss")}>
                    {r.direction === "LONG" ? "Long" : "Short"}
                  </td>
                  <td className="py-1.5 pr-2 text-right">{r.qty}</td>
                  <td className="py-1.5 pr-2 text-right">{r.entry_price.toFixed(2)}</td>
                  <td className="py-1.5 pr-2 text-right">{(open ? r.last : r.exit_price)?.toFixed(2) ?? "—"}</td>
                  <td className="py-1.5 pr-2 text-right font-sans text-slate-400">
                    {open ? (r.stop_price?.toFixed(2) ?? "none") : r.status === "OPEN" ? "open" : (r.exit_reason ?? "")}
                  </td>
                  <td className={clsx("py-1.5 pr-2 text-right", pnl == null ? "" : pnl >= 0 ? "text-profit" : "text-loss")}>
                    {usd(pnl, true)}
                  </td>
                  {open ? (
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => onClose?.(r.id)}
                        className="rounded px-2 py-1 font-sans text-[11px] font-semibold text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
                      >
                        Close
                      </button>
                    </td>
                  ) : (
                    <td className="py-1.5 text-right font-sans text-slate-400">
                      {hhmm(r.opened_at)}
                      {r.closed_at ? `–${hhmm(r.closed_at)}` : ""}
                    </td>
                  )}
                </tr>
              );
            })}
            {!rows.length ? (
              <tr>
                <td colSpan={8} className="py-3 text-center font-sans text-slate-500">
                  None.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}
