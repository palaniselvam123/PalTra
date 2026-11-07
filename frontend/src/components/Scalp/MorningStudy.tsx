"use client";

import clsx from "clsx";
import { Loader2, Play, Square, Sunrise } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists } from "@/components/Scalp/ScanLists";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { GrowwLink } from "@/components/ui/growwLink";
import { TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { smaApi, type MorningGroup, type MorningLabel, type MorningStock, type MorningStockPart, type MorningStudy as Study, type MorningTimes } from "@/lib/smaApi";

/**
 * Morning test (backend morning_study.py): the owner's idea — a stock bullish from the morning keeps going (good for
 * intraday, delivery and MTF), a stock bearish from the morning is bought back up before the close. For past days of
 * the picked stocks: label each morning (09:15 to the picked time), then measure the rest of the day, the next day and
 * the delivery %. Read-only: no order, nothing in the strategy reads it.
 */
const POLL_MS = 3000;
const DAYS = [5, 10, 15, 20, 30];
const CUTS = ["10:00", "10:30", "11:00", "12:00"];
const SIZES = [1, 3, 5, 10, 15];
const MOVES = [0.3, 0.5, 0.75, 1, 1.5];
const HALF_HOURS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"];
const LABELS: { key: MorningLabel; title: string; tone: string; ring: string }[] = [
  { key: "BULLISH", title: "Bullish mornings", tone: "text-emerald-300", ring: "border-emerald-500/40" },
  { key: "BEARISH", title: "Bearish mornings", tone: "text-rose-300", ring: "border-rose-500/40" },
  { key: "SIDEWAYS", title: "Sideways mornings", tone: "text-slate-300", ring: "border-slate-600" },
];

const pct = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`);
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "text-slate-300" : v > 0 ? "text-emerald-300" : "text-rose-300");

export function MorningStudy({
  armed,
  arming,
  onArm,
  onArmMany,
  paused,
  onStatus,
}: {
  armed: Map<string, string[]>;
  arming?: string | null;
  onArm?: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
  paused?: boolean;
  onStatus?: OnScanStatus;
}) {
  const [lists, setLists] = useScanLists("sma.morningtest.lists", ["nifty50"]);
  const [days, setDays] = useState(20);
  const [at, setAt] = useState("10:30");
  const [minutes, setMinutes] = useState(5);
  const [minMove, setMinMove] = useState(0.5);
  const [delivery, setDelivery] = useState(true);
  const [data, setData] = useState<Study | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const v = await smaApi.morningStudy();
      setData(v);
      onStatus?.({ when: v.finished_at, running: v.status === "RUNNING" });
    } catch {
      /* the server may be waking up */
    }
  }, [onStatus]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (data?.status !== "RUNNING") return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [data?.status, load]);

  const run = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const symbols = await resolveScanSymbols(lists, armed.keys());
      if (!symbols.length) {
        setMsg("No stocks in the picked lists.");
        return;
      }
      setData(await smaApi.morningStudyStart({ symbols: symbols.slice(0, 200), days, at, minutes, min_move: minMove, delivery }));
      if (symbols.length > 200) setMsg(`Only the first 200 of ${symbols.length} stocks are read.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    try {
      setData(await smaApi.morningStudyStop());
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const running = data?.status === "RUNNING";
  const rep = data?.report;
  const cut = data?.params.at ?? at;
  return (
    <section className="terminal-dark space-y-4 rounded-xl border border-border bg-surface p-3 shadow-card sm:p-4">
      <div className="max-w-3xl">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
          <Sunrise size={18} className="text-amber-300" aria-hidden /> Morning test
        </h2>
        <p className="mt-0.5 text-sm text-slate-300">
          Does the morning tell you the rest of the day? Your idea: a stock that rises from the morning keeps going (good to
          hold, even as delivery or MTF), and a stock that falls from the morning gets bought back up before the close. This
          checks it on past days of your stocks. Nothing is bought or sold.
        </p>
      </div>

      <ol className="grid gap-2 sm:grid-cols-3">
        <Step n={1} title="Label the morning">
          From 09:15 to {cut}: <b className="text-emerald-300">bullish</b> if the price rose at least {data?.params.min_move ?? minMove}% from
          the open with SMA 9 above SMA 21 and a fairly straight path, <b className="text-rose-300">bearish</b> the mirror, else{" "}
          <b>sideways</b>.
        </Step>
        <Step n={2} title="Watch the rest of the day">
          From {cut} to the close: did it end higher? How far did it bounce from the day&apos;s low, and when was the low / high?
        </Step>
        <Step n={3} title="And the next day">
          The next morning&apos;s gap and the next close — what holding it overnight (delivery / MTF) would have done — plus
          NSE&apos;s delivery % that day.
        </Step>
      </ol>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-white/10 bg-black/20 p-3">
        <label className="block">
          <span className="block text-[11px] text-slate-400">Stocks</span>
          <ScanListPicker value={lists} onChange={setLists} />
        </label>
        <Pick label="Past trading days" value={days} options={DAYS} onChange={setDays} disabled={running} />
        <label className="block">
          <span className="block text-[11px] text-slate-400">The morning ends at</span>
          <select
            value={at}
            disabled={running}
            onChange={(e) => setAt(e.target.value)}
            className="mt-0.5 min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          >
            {CUTS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <Pick label="Candle for the SMAs" value={minutes} options={SIZES} onChange={setMinutes} disabled={running} suffix=" min" />
        <Pick label="Morning move at least" value={minMove} options={MOVES} onChange={setMinMove} disabled={running} suffix="%" />
        <label className="inline-flex min-h-9 items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={delivery} onChange={(e) => setDelivery(e.target.checked)} className="accent-[#10B981]" />
          Delivery % (NSE)
        </label>
        {running ? (
          <button
            type="button"
            onClick={() => void stop()}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-sm font-semibold text-rose-200 ring-1 ring-inset ring-rose-400/40 hover:bg-rose-500/10"
          >
            <Square size={14} aria-hidden /> Stop
          </button>
        ) : (
          <button
            type="button"
            disabled={busy || paused}
            onClick={() => void run()}
            title={paused ? "Scans are paused at the top of the page" : undefined}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-600 px-3 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
          >
            {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Play size={14} aria-hidden />} Run the test
          </button>
        )}
      </div>
      {msg ? <p className="text-sm text-amber-300">{msg}</p> : null}
      {running && data ? (
        <div>
          <p className="text-xs text-slate-400">
            Reading {data.done} of {data.total} stocks…
          </p>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-white/10">
            <div className="h-full bg-sky-500 transition-all" style={{ width: `${data.total ? (100 * data.done) / data.total : 0}%` }} />
          </div>
        </div>
      ) : null}

      {rep ? (
        <>
          <div className="rounded-lg border border-sky-500/40 bg-sky-500/10 p-3">
            <h3 className="text-sm font-semibold text-sky-200">The answer</h3>
            <p className="mt-0.5 text-[11px] text-slate-400">
              {rep.samples} stock-days from {rep.stocks} stocks · morning to {cut} · {data?.params.days} past days
              {data?.finished_at ? ` · ${new Date(data.finished_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}` : ""}
              {rep.stopped ? " · stopped early" : ""}
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-100">
              {rep.answer.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
          <HowToRead cut={cut} />
          <div className="grid gap-3 lg:grid-cols-3">
            {LABELS.map((l) => (
              <GroupCard key={l.key} title={l.title} tone={l.tone} ring={l.ring} g={rep.groups[l.key]} cut={cut} kind={l.key} />
            ))}
          </div>
          <ByStock rows={rep.by_stock} cut={cut} armed={armed} arming={arming} onArm={onArm} onArmMany={onArmMany} />
        </>
      ) : !running ? (
        <p className="text-sm text-slate-400">No result yet. Pick the stocks and press Run the test (it can take a few minutes).</p>
      ) : null}
      {data?.errors?.length ? (
        <details className="text-xs text-slate-400">
          <summary className="cursor-pointer">{data.errors.length} note(s)</summary>
          <ul className="mt-1 space-y-0.5">
            {data.errors.map((e, i) => (
              <li key={i}>
                {e.symbol ? <b>{e.symbol}: </b> : null}
                {e.error}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="rounded-lg border border-white/10 bg-black/20 p-3 text-sm text-slate-300">
      <span className="mr-2 inline-flex h-6 w-6 items-center justify-center rounded-full bg-sky-500/20 text-xs font-bold text-sky-200">{n}</span>
      <b className="text-slate-100">{title}</b>
      <p className="mt-1 text-xs leading-relaxed">{children}</p>
    </li>
  );
}

function Pick({
  label,
  value,
  options,
  onChange,
  disabled,
  suffix = "",
}: {
  label: string;
  value: number;
  options: number[];
  onChange: (v: number) => void;
  disabled?: boolean;
  suffix?: string;
}) {
  return (
    <label className="block">
      <span className="block text-[11px] text-slate-400">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-0.5 min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
            {suffix}
          </option>
        ))}
      </select>
    </label>
  );
}

/** A 0-100 % bar with the 50 % mark (a coin toss). */
function Share({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <div className="flex justify-between text-xs text-slate-300">
        <span>{label}</span>
        <b className={clsx(value == null ? "text-slate-500" : value >= 60 ? "text-emerald-300" : value <= 40 ? "text-rose-300" : "text-amber-300")}>
          {value == null ? "—" : `${value.toFixed(0)}%`}
        </b>
      </div>
      <div className="relative mt-0.5 h-2 overflow-hidden rounded-full bg-white/10">
        <div
          className={clsx("h-full", value == null ? "" : value >= 60 ? "bg-emerald-500" : value <= 40 ? "bg-rose-500" : "bg-amber-400")}
          style={{ width: `${value ?? 0}%` }}
        />
        <div className="absolute inset-y-0 left-1/2 w-px bg-white/40" title="50% = a coin toss" />
      </div>
    </div>
  );
}

function Times({ label, times }: { label: string; times: MorningTimes }) {
  if (!times.length) return null;
  const top = Math.max(...times.map((t) => t.count));
  const got = new Map(times.map((t) => [t.at, t]));
  return (
    <div>
      <p className="text-[11px] text-slate-400">{label}</p>
      <div className="mt-1 flex h-12 items-end gap-0.5">
        {HALF_HOURS.map((at) => {
          const t = got.get(at);
          return (
            <div key={at} className="flex flex-1 flex-col items-center" title={`${at}–: ${t ? `${t.count} day(s), ${t.pct}%` : "none"}`}>
              <div className={clsx("w-full rounded-t", t ? "bg-sky-400/80" : "bg-white/10")} style={{ height: `${t ? Math.max(4, (40 * t.count) / top) : 2}px` }} />
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-[10px] text-slate-500">
        <span>09:15</span>
        <span>12:00</span>
        <span>15:30</span>
      </div>
    </div>
  );
}

function GroupCard({ title, tone: t, ring, g, cut, kind }: { title: string; tone: string; ring: string; g: MorningGroup; cut: string; kind: MorningLabel }) {
  return (
    <div className={clsx("space-y-2 rounded-lg border p-3", ring)}>
      <div className="flex items-baseline justify-between">
        <h4 className={clsx("text-sm font-semibold", t)}>{title}</h4>
        <span className="text-xs text-slate-400">
          {g.days} days · {g.stocks} stocks
        </span>
      </div>
      {g.days ? (
        <>
          {g.days < 5 ? (
            <p className="rounded-md bg-amber-500/10 px-2 py-1 text-[11px] text-amber-300">
              Only {g.days} day{g.days === 1 ? "" : "s"} — too few to trust. Pick more stocks or more days.
            </p>
          ) : null}
          <Share label={`Days the price ended higher than at ${cut}`} value={g.closed_higher_pct} />
          <div className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
            <span className="text-slate-400">Average move from {cut} to the close</span>
            <b className={clsx("text-right tabular-nums", tone(g.avg_rest_pct))}>{pct(g.avg_rest_pct)}</b>
            <span className="text-slate-400">Average climb from the day&apos;s lowest price to the close</span>
            <b className="text-right tabular-nums text-emerald-300">{pct(g.avg_bounce_pct)}</b>
            <span className="text-slate-400">Average drop from the day&apos;s highest price to the close</span>
            <b className="text-right tabular-nums text-rose-300">{pct(g.avg_giveback_pct)}</b>
          </div>
          <Share label="Days it climbed 1% or more off its low" value={g.bounce_big_pct} />
          <Times label={kind === "BULLISH" ? "What time the day's highest price came (taller bar = more days)" : "What time the day's lowest price came (taller bar = more days)"} times={kind === "BULLISH" ? g.high_times : g.low_times} />
          <div className="rounded-md bg-black/20 p-2 text-xs">
            <p className="font-semibold text-slate-200">If you had held it overnight (delivery / MTF)</p>
            <Share label="Next morning it opened higher" value={g.next_gap_up_pct} />
            <div className="mt-1 flex justify-between text-slate-400">
              <span>Next day: average open · average close</span>
              <span className="tabular-nums">
                <b className={tone(g.avg_next_gap_pct)}>{pct(g.avg_next_gap_pct)}</b> · <b className={tone(g.avg_next_day_pct)}>{pct(g.avg_next_day_pct)}</b>
              </span>
            </div>
          </div>
          {g.avg_deliv_pct != null ? (
            <p className="text-xs text-slate-400">
              Average delivery {g.avg_deliv_pct.toFixed(1)}%
              {g.deliv_split
                ? ` · with ${g.deliv_split.at_pct.toFixed(0)}%+ delivery closed higher ${g.deliv_split.high.closed_higher_pct?.toFixed(0) ?? "—"}% vs ${g.deliv_split.low.closed_higher_pct?.toFixed(0) ?? "—"}% with less`
                : ""}
            </p>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-slate-500">No days like this.</p>
      )}
    </div>
  );
}

function HowToRead({ cut }: { cut: string }) {
  return (
    <details className="rounded-lg border border-white/10 bg-black/20 p-3 text-sm text-slate-300">
      <summary className="cursor-pointer font-semibold text-slate-100">How to read these numbers (an example)</summary>
      <div className="mt-2 space-y-1.5 text-xs leading-relaxed">
        <p>
          Say a stock&apos;s row shows <b className="text-rose-300">Bearish mornings: 3 days</b>, <b>Ended higher: 3 of 3 (100%)</b>,{" "}
          <b className="text-emerald-300">Average move after: +0.49%</b>. That means:
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>On 3 of the past days, this stock fell in the morning (09:15 to {cut}) — a bearish morning.</li>
          <li>On all 3 of those days (100%), the price at 15:30 was higher than at {cut}: it was bought back up.</li>
          <li>On average it rose 0.49% from {cut} to the close.</li>
        </ul>
        <p>
          3 days is very few: one more day could change 100% into 75%. Trust a result more when it has 10 or more days. 50% means it
          is a coin toss — the morning did not tell you anything.
        </p>
      </div>
    </details>
  );
}

type StockKey =
  | "symbol"
  | "bullDays"
  | "bullUp"
  | "bullMove"
  | "bearDays"
  | "bearUp"
  | "bearMove"
  | "sideDays"
  | "deliv";

function ByStock({
  rows,
  cut,
  armed,
  arming,
  onArm,
  onArmMany,
}: {
  rows: NonNullable<Study["report"]>["by_stock"];
  cut: string;
  armed: Map<string, string[]>;
  arming?: string | null;
  onArm?: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [minDays, setMinDays] = useState(0);
  const ticks = useTicks();
  const filtered = rows.filter(
    (r) => matchesText(query, r.symbol) && ticks.shows(r.symbol) && r.bullish.days + r.bearish.days >= minDays
  );
  const { sorted, sort, onSort } = useSort<MorningStock, StockKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "bullDays":
        return r.bullish.days;
      case "bullUp":
        return r.bullish.days ? r.bullish.closed_higher_pct : null;
      case "bullMove":
        return r.bullish.days ? r.bullish.avg_rest_pct : null;
      case "bearDays":
        return r.bearish.days;
      case "bearUp":
        return r.bearish.days ? r.bearish.closed_higher_pct : null;
      case "bearMove":
        return r.bearish.days ? r.bearish.avg_rest_pct : null;
      case "sideDays":
        return r.sideways.days;
      case "deliv":
        return r.avg_deliv_pct;
    }
  });
  if (!rows.length) return null;
  const ended = (p: MorningStockPart) =>
    p.days ? (
      <span className="tabular-nums">
        <b className={p.closed_higher_pct == null ? "" : p.closed_higher_pct >= 60 ? "text-emerald-300" : p.closed_higher_pct <= 40 ? "text-rose-300" : "text-amber-300"}>
          {Math.round(((p.closed_higher_pct ?? 0) * p.days) / 100)} of {p.days}
        </b>{" "}
        <span className="text-slate-500">({p.closed_higher_pct?.toFixed(0)}%)</span>
      </span>
    ) : (
      <span className="text-slate-600">—</span>
    );
  const move = (p: MorningStockPart) => (p.days ? <span className={clsx("tabular-nums", tone(p.avg_rest_pct))}>{pct(p.avg_rest_pct)}</span> : <span className="text-slate-600">—</span>);
  const days = (n: number) => (n ? <span className="tabular-nums text-slate-200">{n}</span> : <span className="text-slate-600">0</span>);
  return (
    <div className="space-y-2 rounded-lg border border-white/10 p-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-200">Each stock</h3>
        <p className="mt-0.5 text-xs text-slate-400">
          <b>Days</b> = how many past days had that kind of morning. <b>Ended higher</b> = on how many of those days the price at
          15:30 was above the price at {cut}. <b>Move after</b> = the average change from {cut} to the close. Click a header to sort.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <TextFilter value={query} onChange={setQuery} />
        <label className="block text-xs text-slate-400">
          <span className="block text-[11px]">Bullish + bearish days at least</span>
          <select
            value={minDays}
            onChange={(e) => setMinDays(Number(e.target.value))}
            className="mt-0.5 min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          >
            {[0, 3, 5, 10].map((n) => (
              <option key={n} value={n}>
                {n === 0 ? "Any" : n}
              </option>
            ))}
          </select>
        </label>
        <span className="text-xs text-slate-500">{sorted.length} shown</span>
      </div>
      {onArmMany ? <TickBar ticks={ticks} onArm={onArmMany} /> : null}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead className="whitespace-nowrap text-xs uppercase text-slate-500">
            <tr className="[&>th]:px-2 [&>th]:pb-1">
              {onArmMany ? <TickHead ticks={ticks} symbols={sorted.map((r) => r.symbol)} /> : null}
              <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text />
              <SortTh label={<span className="text-emerald-300">Bullish · days</span>} k="bullDays" sort={sort} onSort={onSort} num />
              <SortTh label="Ended higher" k="bullUp" sort={sort} onSort={onSort} num />
              <SortTh label="Move after" k="bullMove" sort={sort} onSort={onSort} num />
              <SortTh label={<span className="text-rose-300">Bearish · days</span>} k="bearDays" sort={sort} onSort={onSort} num />
              <SortTh label="Ended higher (bought back)" k="bearUp" sort={sort} onSort={onSort} num />
              <SortTh label="Move after" k="bearMove" sort={sort} onSort={onSort} num />
              <SortTh label="Sideways · days" k="sideDays" sort={sort} onSort={onSort} num />
              <SortTh label="Delivery" k="deliv" sort={sort} onSort={onSort} num title="NSE's average delivery % on these days" />
              {onArm ? <th className="text-right font-medium">Arm</th> : null}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const on = armed.get(r.symbol.toUpperCase()) ?? [];
              return (
                <tr key={r.symbol} className="whitespace-nowrap border-t border-white/5 [&>td]:px-2 [&>td]:py-1">
                  {onArmMany ? <TickCell ticks={ticks} symbol={r.symbol} /> : null}
                  <td className="font-semibold text-slate-100">
                    <GrowwLink symbol={r.symbol} />
                  </td>
                  <td className="text-right">{days(r.bullish.days)}</td>
                  <td className="text-right">{ended(r.bullish)}</td>
                  <td className="text-right">{move(r.bullish)}</td>
                  <td className="text-right">{days(r.bearish.days)}</td>
                  <td className="text-right">{ended(r.bearish)}</td>
                  <td className="text-right">{move(r.bearish)}</td>
                  <td className="text-right">{days(r.sideways.days)}</td>
                  <td className="text-right tabular-nums text-slate-300">{r.avg_deliv_pct != null ? `${r.avg_deliv_pct.toFixed(1)}%` : "—"}</td>
                  {onArm ? (
                    <td className="text-right">
                      <span className="inline-flex items-center justify-end gap-1.5">
                        {on.length ? <span className="text-[11px] font-semibold text-emerald-300">{on.join(" · ")}</span> : null}
                        <button
                          type="button"
                          disabled={arming === r.symbol}
                          onClick={() => onArm(r.symbol)}
                          aria-label={`Arm ${r.symbol} on a bot`}
                          title="Arm on a bot (asks which). Arming only: the bot orders on its own signal."
                          className="min-h-8 rounded-md px-2.5 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
                        >
                          {arming === r.symbol ? "…" : "Arm"}
                        </button>
                      </span>
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
