"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  History,
  CalendarDays,
  Clock,
  Loader2,
  RefreshCw,
  Search,
  Send,
  Zap,
} from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { useTradingState } from "@/hooks/useTradingState";
import {
  api,
  type AlertScanResponse,
  type FastMoversResponse,
  type MoverRow,
  type MoversResponse,
  type PriceAtResponse,
  type RecorderStatus,
} from "@/lib/api";

const REFRESH_MS = 15_000;

export default function MoversPage() {
  const { connected, summary, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();

  const [recorder, setRecorder] = useState<RecorderStatus | null>(null);
  const [movers, setMovers] = useState<MoversResponse | null>(null);
  const [fast, setFast] = useState<FastMoversResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // "as it stood at" — the whole reason the prices are recorded.
  // The window is edited freely and applied on Query, so a half-typed "1" in
  // the From box never fires a request for 01:00.
  const [fromTime, setFromTime] = useState("09:15");
  const [toTime, setToTime] = useState("");
  const [window_, setWindow_] = useState<{ since: string; at: string }>({ since: "09:15", at: "" });
  const asOf = window_.at;
  // Empty means today. Any past date is answered from stored broker history.
  const [day, setDay] = useState("");
  const [availableDays, setAvailableDays] = useState<any>(null);
  // Peak scans the whole session for each symbol's fastest window. "Right now"
  // is the useful question live; reviewing a past morning needs the other one.
  const [peak, setPeak] = useState(false);
  const [top, setTop] = useState(15);

  // Point-in-time lookup
  const [lookupSymbol, setLookupSymbol] = useState("RELIANCE");
  const [lookupTime, setLookupTime] = useState("11:00");
  const [fetching, setFetching] = useState<string | null>(null);
  const [lookup, setLookup] = useState<PriceAtResponse | null>(null);

  const [alerts, setAlerts] = useState<AlertScanResponse | null>(null);

  const refresh = useCallback(() => {
    api.moversRecorder().then(setRecorder).catch(() => {});
    api
      .movers({
        top,
        at: window_.at || undefined,
        since: window_.since || undefined,
        day: day || undefined,
      })
      .then((m) => {
        setMovers(m);
        setError(null);
      })
      .catch((e) => setError(String(e?.message ?? e)));
    api
      .moversFast({ day: day || undefined, peak: peak || undefined, until: peak ? "11:00" : undefined })
      .then(setFast)
      .catch(() => {});
    api.moversDays().then((d: any) => setAvailableDays(d.available)).catch(() => {});
  }, [window_, day, peak, top]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // A whole-universe fetch takes minutes, so it reports progress rather than
  // spinning silently; the rankings reload once it lands.
  const fetchWholeDay = async () => {
    setFetching("Starting…");
    try {
      await api.moversFetchDay(day || new Date().toISOString().slice(0, 10), "5m");
      for (;;) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await api.moversFetchDayStatus();
        setFetching(`${st.done}/${st.total}…`);
        if (!st.running) break;
      }
      refresh();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setFetching(null);
    }
  };

  // Times are applied only here, so the tables always match the boxes that
  // were actually submitted rather than whatever was mid-keystroke.
  // "FROM OPEN" is only true for a window that starts at the open; a 10:00
  // start measures from 10:00 and the header has to say so.
  const windowed = !!movers?.window_start_ist && movers.window_start_ist !== "09:15";
  const fromLabel = windowed ? `From ${movers!.window_start_ist}` : "From open";
  const openLabel = windowed ? `At ${movers!.window_start_ist}` : "Open";
  const emptyMsg = (dir: "above" | "below") =>
    windowed
      ? `No stock is ${dir} its ${movers!.window_start_ist} price in this window.`
      : `No stock is ${dir} its open yet.`;

  const runQuery = () => setWindow_({ since: fromTime.trim(), at: toTime.trim() });

  const runLookup = async () => {
    setBusy(true);
    try {
      setLookup(await api.moversPriceAt({ symbol: lookupSymbol.trim().toUpperCase(), at: lookupTime, day: day || undefined }));
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const runAlertScan = async (dryRun: boolean) => {
    setBusy(true);
    try {
      setAlerts(await api.moversAlertScan({ dry_run: dryRun }));
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const widen = async () => {
    setBusy(true);
    try {
      await api.moversWidenUniverse();
      await api.moversFlush();
      refresh();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const store = recorder?.store;

  return (
    <div className="min-h-screen bg-bg text-slate-100">
      <Navbar
        connected={connected}
        totalPnl={summary.total_pnl}
        killSwitchActive={killSwitchActive}
        onKillSwitch={killSwitch}
        onResetKillSwitch={resetKillSwitch}
        feed={feed}
        onFeedChanged={setFeed}
        botRunning={bot?.enabled ?? false}
      />

      <main className="mx-auto max-w-7xl px-4 py-6 space-y-6">
        <header className="space-y-1">
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Zap size={20} className="text-bot" />
            Morning Movers
          </h1>
          <p className="max-w-3xl text-sm text-slate-400">
            Every tracked stock&apos;s price is recorded once a minute while the market is open and kept on
            disk, so the morning can still be examined in the evening. Ranking is by move from the
            session open; &quot;fast&quot; is how much of that move arrived in the last few minutes.
          </p>
        </header>

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* ---- recorder state ---- */}
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Recorder"
            value={recorder?.running ? "Running" : "Stopped"}
            tone={recorder?.running ? "good" : "warn"}
            sub={recorder ? `every ${recorder.interval_sec}s · session ${recorder.session}` : "—"}
          />
          <Stat
            label="Prices on disk"
            value={store ? store.points.toLocaleString() : "—"}
            sub={store ? `${store.symbols} symbols` : "—"}
          />
          <Stat
            label="Latest recorded"
            value={store?.last_time_ist ?? "—"}
            sub={store?.last ?? "no data yet"}
          />
          <Stat
            label="Feed"
            value={recorder?.source ?? "—"}
            tone={recorder?.source === "live" ? "good" : "warn"}
            sub={recorder?.source === "simulated" ? "synthetic prices — not NSE" : "real NSE quotes"}
          />
        </section>

        <div className="flex flex-wrap items-center gap-2">
          <button onClick={refresh} className={btn}>
            <RefreshCw size={14} /> Refresh
          </button>
          <button onClick={widen} disabled={busy} className={btn}>
            Track full universe
          </button>
          <div className="flex items-center gap-1 text-sm">
            <span className="text-slate-400">Show top</span>
            {[15, 25, 50].map((n) => (
              <button
                key={n}
                onClick={() => setTop(n)}
                className={clsx(
                  "rounded-md border px-2 py-1 text-xs",
                  top === n
                    ? "border-bot bg-bot/15 text-bot"
                    : "border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-700/60",
                )}
              >
                {n}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-2 text-sm">
            <CalendarDays size={14} className="text-slate-500" />
            <label className="text-slate-400">Day</label>
            <input
              type="date"
              value={day}
              onChange={(e) => {
                setDay(e.target.value);
                // A past session is over: "what is moving now" has no answer
                // there, so default to "what moved fast that morning".
                if (e.target.value) setPeak(true);
              }}
              className={input + " w-40"}
            />
            {day && (
              <button onClick={() => setDay("")} className="text-xs text-slate-400 underline">
                today
              </button>
            )}
            <span className="mx-1 text-slate-700">|</span>
            <Clock size={14} className="text-slate-500" />
            <label className="text-slate-400">From</label>
            <input
              value={fromTime}
              onChange={(e) => setFromTime(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runQuery()}
              placeholder="09:15"
              className={input + " w-20"}
            />
            <label className="text-slate-400">to</label>
            <input
              value={toTime}
              onChange={(e) => setToTime(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runQuery()}
              placeholder="15:30"
              className={input + " w-20"}
            />
            <button
              onClick={runQuery}
              className="rounded bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-500"
            >
              Query
            </button>
            {(window_.since !== "09:15" || window_.at) && (
              <button
                onClick={() => {
                  setFromTime("09:15");
                  setToTime("");
                  setWindow_({ since: "09:15", at: "" });
                }}
                className="text-xs text-slate-400 underline"
              >
                whole session
              </button>
            )}
          </div>
        </div>

        {movers && movers.origin === "broker_history_5m" && movers.symbols_tracked > 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-sky-500/40 bg-sky-500/10 px-4 py-3 text-sm text-sky-300">
            <History size={16} className="mt-0.5 shrink-0" />
            <span>
              Showing <strong>{movers.day}</strong> from stored broker history at{" "}
              {movers.resolution_min}-minute resolution.{" "}
              {!movers.live_coverage?.covered
                ? "The live minute record does not cover this day."
                : movers.live_coverage.in_session === false
                ? `The recorder only ran ${movers.live_coverage.first_ist}–${movers.live_coverage.last_ist}, after the close, so it saw only frozen quotes — history answered instead.`
                : `The live minute record for this day only runs ${movers.live_coverage.first_ist}–${movers.live_coverage.last_ist}, so history answered instead.`}{" "}
              Percentages are from the true session open.
            </span>
          </div>
        )}

        {movers && movers.empty_reason && (
          <div className="flex items-start justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
            <div className="flex items-start gap-2">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span>{movers.empty_reason}</span>
            </div>
            <button
              onClick={fetchWholeDay}
              disabled={fetching !== null}
              className="shrink-0 rounded border border-amber-500/50 px-3 py-1 text-xs font-medium hover:bg-amber-500/20 disabled:opacity-50"
            >
              {fetching ?? "Fetch this day"}
            </button>
          </div>
        )}

        {movers && movers.origin !== "broker_history_5m" && !movers.baseline_is_session_open && movers.symbols_tracked > 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>
              The recorder was not running at the 09:15 open, so these percentages are measured
              from the first price it observed
              {movers.gainers[0]?.first_time_ist ? ` (${movers.gainers[0].first_time_ist})` : ""} —
              not from the session open.
            </span>
          </div>
        )}

        {/* ---- fast movers ---- */}
        <section className="rounded-xl border border-slate-800 bg-card p-4">
          <div className="mb-1 flex flex-wrap items-center gap-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Zap size={15} className="text-bot" />
              {peak ? "Moved fastest that morning" : "Moving fast right now"}
            </h2>
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-400">
              <input
                type="checkbox"
                checked={peak}
                onChange={(e) => setPeak(e.target.checked)}
                className="accent-bot"
              />
              Peak of the session (to 11:00)
            </label>
          </div>
          <p className="mb-3 text-xs text-slate-500">
            {peak
              ? `Each symbol's fastest ${fast?.window_min ?? 10}-minute stretch up to 11:00, and when it happened. Measuring "right now" on a finished session only ever describes the last few minutes before the close.`
              : `Rate of change over the last ${fast?.window_min ?? 10} minutes, not the size of the move. A stock up 3% over two hours does not appear here; one that did it in ten minutes does.`}
          </p>
          {!fast || fast.movers.length === 0 ? (
            <Empty>
              {peak
                ? `Nothing reached ${fast?.min_speed_pct_per_min ?? 0.1}%/min during that session.`
                : `Nothing is moving faster than ${fast?.min_speed_pct_per_min ?? 0.1}%/min right now.`}
            </Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr>
                    <Th>Symbol</Th>
                    <Th right>Speed</Th>
                    <Th right>{peak ? "Best window" : `Last ${fast.window_min}m`}</Th>
                    <Th right>From open</Th>
                    <Th right>Price</Th>
                    <Th right>{peak ? "Peaked at" : "At"}</Th>
                  </tr>
                </thead>
                <tbody>
                  {fast.movers.map((f) => (
                    <tr key={f.symbol} className="border-t border-slate-800/70">
                      <Td className="font-medium">{f.symbol}</Td>
                      <Td right>
                        <Pct value={f.speed_pct_per_min} suffix="%/min" digits={3} />
                      </Td>
                      <Td right>
                        <Pct value={f.window_move_pct} />
                      </Td>
                      <Td right>
                        <Pct value={f.pct_from_open} />
                      </Td>
                      <Td right className="tabular-nums">{f.last_price.toFixed(2)}</Td>
                      <Td right className="text-slate-400">{f.last_time_ist}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* ---- gainers / losers ---- */}
        <div className="grid gap-4 lg:grid-cols-2">
          <MoverTable
            title={`Top ${top} gainers`}
            icon={<ArrowUpRight size={15} className="text-profit" />}
            rows={movers?.gainers ?? []}
            total={movers?.gainers_total ?? 0}
            requested={top}
            tracked={movers?.symbols_tracked ?? 0}
            fromLabel={fromLabel}
            openLabel={openLabel}
            emptyText={emptyMsg("above")}
          />
          <MoverTable
            title={`Top ${top} losers`}
            icon={<ArrowDownRight size={15} className="text-loss" />}
            rows={movers?.losers ?? []}
            total={movers?.losers_total ?? 0}
            requested={top}
            tracked={movers?.symbols_tracked ?? 0}
            fromLabel={fromLabel}
            openLabel={openLabel}
            emptyText={emptyMsg("below")}
          />
        </div>

        {/* ---- point-in-time lookup ---- */}
        <section className="rounded-xl border border-slate-800 bg-card p-4">
          <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
            <Search size={15} className="text-bot" /> What was it at…
          </h2>
          <p className="mb-3 text-xs text-slate-500">
            The same question the chatbot answers. Answered from the minute record or stored
            history where available; otherwise the day is fetched from Groww on the spot and kept,
            so the next ask is instant.
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Symbol">
              <input
                value={lookupSymbol}
                onChange={(e) => setLookupSymbol(e.target.value)}
                className={input + " w-40 uppercase"}
              />
            </Field>
            <Field label="Time (IST)">
              <input
                value={lookupTime}
                onChange={(e) => setLookupTime(e.target.value)}
                placeholder="11:00"
                className={input + " w-28"}
              />
            </Field>
            <button onClick={runLookup} disabled={busy} className={btnPrimary}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
              {busy ? "Fetching…" : "Look up"}
            </button>
          </div>

          {lookup && (
            <div className="mt-4 rounded-lg border border-slate-800 bg-bg/60 p-4 text-sm">
              {lookup.found ? (
                <div className="space-y-1">
                  <div className="text-lg font-semibold tabular-nums">
                    {lookup.symbol} — ₹{lookup.price?.toFixed(2)}
                  </div>
                  <div className="text-slate-400">
                    recorded at {lookup.recorded_at_ist} IST on {lookup.day}
                    {lookup.recorded_at_ist !== lookup.asked_for && (
                      <span className="ml-1 text-amber-400">
                        (asked for {lookup.asked_for} — nearest recorded minute shown)
                      </span>
                    )}
                  </div>
                  {lookup.origin && (
                    <div className="text-xs text-slate-500">
                      from{" "}
                      {lookup.origin === "live_minute_record"
                        ? "the live minute record"
                        : "stored broker history"}
                      {lookup.resolution_min ? ` · ${lookup.resolution_min}-minute resolution` : ""}
                      {lookup.fetched_now && (
                        <span className="ml-1 text-bot">· fetched from Groww just now and cached</span>
                      )}
                    </div>
                  )}
                  {lookup.pct_from_open != null && (
                    <div className="text-slate-400">
                      <Pct value={lookup.pct_from_open} /> from the session open of ₹
                      {lookup.open_price?.toFixed(2)}
                    </div>
                  )}
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="flex items-start gap-2 text-amber-400">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                    <span>{lookup.reason}</span>
                  </div>
                  {/* What IS available, so the next attempt can succeed. */}
                  {(lookup.live_points || lookup.history_bars) && (
                    <div className="pl-6 text-xs text-slate-500">
                      Available for {lookup.symbol} on {lookup.day}:
                      {lookup.live_points ? (
                        <span className="ml-1">
                          live minute record {lookup.live_first_ist}–{lookup.live_last_ist}
                        </span>
                      ) : null}
                      {lookup.history_bars ? (
                        <span className="ml-1">
                          {lookup.live_points ? "· " : ""}broker history {lookup.history_first_ist}–
                          {lookup.history_last_ist}
                        </span>
                      ) : null}
                    </div>
                  )}
                  {lookup.in_history_universe === false && lookup.in_live_universe === false && (
                    <div className="pl-6 text-xs text-slate-500">
                      Try a symbol from the tracked universe — the tables above list the ones with
                      prices.
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </section>

        {availableDays && (
          <p className="text-xs text-slate-500">
            Queryable: live minute record for{" "}
            {availableDays.live_minute_days?.length
              ? availableDays.live_minute_days.join(", ")
              : "no days yet"}
            {availableDays.history_range?.length === 2 &&
              ` · broker history ${availableDays.history_range[0]} to ${availableDays.history_range[1]} at 5-minute resolution`}
          </p>
        )}

        {/* ---- alerts ---- */}
        <section className="rounded-xl border border-slate-800 bg-card p-4">
          <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
            <Send size={15} className="text-bot" /> Fast-mover alerts
          </h2>
          <p className="mb-3 text-xs text-slate-500">
            A dry run shows exactly what would be sent without sending it. Each symbol alerts at most
            once per direction per day, so a stock hovering at the threshold cannot spam the channel.
          </p>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => runAlertScan(true)} disabled={busy} className={btn}>
              Dry run
            </button>
            <button onClick={() => runAlertScan(false)} disabled={busy} className={btnPrimary}>
              <Send size={14} /> Send alerts
            </button>
          </div>

          {alerts && (
            <div className="mt-4 space-y-2 text-sm">
              <div className="text-slate-400">
                {alerts.candidates} fast mover{alerts.candidates === 1 ? "" : "s"} ·{" "}
                {alerts.alerted} to alert · {alerts.already_alerted_today} already alerted today
                {alerts.dry_run && <span className="ml-2 text-amber-400">DRY RUN — nothing sent</span>}
              </div>
              {alerts.results.map((r) => (
                <div key={r.symbol + r.direction} className="rounded-lg border border-slate-800 bg-bg/60 p-3">
                  <pre className="whitespace-pre-wrap font-sans text-xs text-slate-300">{r.message}</pre>
                  <div className="mt-2 text-xs text-slate-500">
                    {typeof r.delivery === "string" ? (
                      r.delivery
                    ) : (
                      <DeliveryBadge delivery={r.delivery} />
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

/* ---------- small presentational pieces ---------- */

const btn =
  "inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-700/60 disabled:opacity-50";
const btnPrimary =
  "inline-flex items-center gap-1.5 rounded-lg bg-bot px-3 py-1.5 text-sm font-medium text-slate-900 hover:brightness-110 disabled:opacity-50";
const input =
  "rounded-lg border border-slate-700 bg-bg px-2 py-1.5 text-sm outline-none focus:border-bot";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "good" | "warn";
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div
        className={clsx(
          "mt-1 text-lg font-semibold",
          tone === "good" && "text-profit",
          tone === "warn" && "text-amber-400",
        )}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

function MoverTable({
  title,
  icon,
  rows,
  total,
  requested,
  tracked,
  emptyText,
  fromLabel = "From open",
  openLabel = "Open",
}: {
  title: string;
  icon: React.ReactNode;
  fromLabel?: string;
  openLabel?: string;
  rows: MoverRow[];
  total: number;
  requested: number;
  tracked: number;
  emptyText: string;
}) {
  // A short list can mean a quiet day or a small universe. Those call for
  // different responses, so say which it is rather than just showing fewer rows.
  const short = rows.length < requested;
  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
        {icon} {title}
        <span className="font-normal text-slate-500">
          showing {rows.length} of {total}
        </span>
      </h2>
      {short && rows.length > 0 && (
        <p className="mb-2 text-xs text-slate-500">
          {total < requested && tracked < requested * 2
            ? `Only ${tracked} symbols have prices for this day — use "Track full universe" for a wider list.`
            : `Only ${total} moved this way today.`}
        </p>
      )}
      {rows.length === 0 ? (
        <Empty>{emptyText}</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr>
                <Th>#</Th>
                <Th>Symbol</Th>
                <Th right>{fromLabel}</Th>
                <Th right>Price</Th>
                <Th right>{openLabel}</Th>
                <Th right>At</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m, i) => (
                <tr key={m.symbol} className="border-t border-slate-800/70">
                  <Td className="tabular-nums text-slate-500">{i + 1}</Td>
                  <Td className="font-medium">{m.symbol}</Td>
                  <Td right>
                    <Pct value={m.pct_from_open} />
                  </Td>
                  <Td right className="tabular-nums">{m.last_price.toFixed(2)}</Td>
                  <Td right className="tabular-nums text-slate-400">{m.open_price.toFixed(2)}</Td>
                  <Td right className="text-slate-400">{m.last_time_ist}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Pct({ value, suffix = "%", digits = 2 }: { value: number; suffix?: string; digits?: number }) {
  return (
    <span className={clsx("tabular-nums", value > 0 ? "text-profit" : value < 0 ? "text-loss" : "text-slate-400")}>
      {value > 0 ? "+" : ""}
      {value.toFixed(digits)}
      {suffix}
    </span>
  );
}

function DeliveryBadge({ delivery }: { delivery: any }) {
  if (!delivery) return <span>—</span>;
  const ok = delivery.delivery_confirmed;
  const accepted = delivery.ok;
  return (
    <span className={clsx(ok ? "text-profit" : accepted ? "text-amber-400" : "text-loss")}>
      {delivery.provider ?? "no provider"} · {delivery.classification ?? "—"}
      {accepted && !ok && " (accepted, delivery unconfirmed)"}
      {delivery.error && ` · ${delivery.error}`}
      {delivery.skipped_reason && ` · ${delivery.skipped_reason}`}
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-800 px-4 py-6 text-center text-sm text-slate-500">{children}</div>;
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={clsx("pb-2 font-medium", right ? "text-right" : "text-left")}>{children}</th>;
}

function Td({
  children,
  right,
  className,
}: {
  children: React.ReactNode;
  right?: boolean;
  className?: string;
}) {
  return <td className={clsx("py-1.5", right && "text-right", className)}>{children}</td>;
}
