"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronRight, Loader2, Moon, RefreshCw } from "lucide-react";
import { api, type CloseOrderRow, type CloseOrderRun, type CloseOrders as CloseData } from "@/lib/api";
import { ScanListPicker, cleanLists, resolveScanSymbols, type ScanList } from "@/components/Scalp/ScanLists";
import { StopScan } from "@/components/Scalp/StopScan";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { FilterField, FilterRow, SelectFilter, TextFilter, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { GrowwLink } from "@/components/ui/growwLink";
import { GuideBar } from "./GuideBar";

const POLL_MS = 3_000;
const STORE = "scalp.closeorders";

type Props = {
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
  paused?: boolean;
};

type Show = "ALL" | "UNUSUAL" | CloseOrderRow["verdict"];
type SortKey = "symbol" | "verdict" | "ratio" | "buy" | "sell" | "vsvol" | "day" | "late" | "rush" | "deliv" | "oi" | "short";

export const VERDICT: Record<CloseOrderRow["verdict"], { label: string; cls: string; tip: string }> = {
  DEMAND: { label: "Real demand", cls: "bg-profit/15 text-profit ring-profit/40", tip: "The signs say real buyers wanted it" },
  SHORT_COVER: { label: "Short sellers buying back", cls: "bg-amber-500/15 text-amber-300 ring-amber-400/40", tip: "The signs say the end-of-day buying was short sellers closing" },
  MIXED: { label: "Both signs", cls: "bg-sky-500/15 text-sky-300 ring-sky-400/40", tip: "Some signs point each way" },
  QUIET: { label: "Nothing unusual", cls: "bg-white/5 text-slate-400 ring-white/10", tip: "No strong sign either way" },
};

const qty = (v: number | null | undefined) =>
  v == null ? "—" : v >= 1e7 ? `${(v / 1e7).toFixed(2)} Cr` : v >= 1e5 ? `${(v / 1e5).toFixed(2)} L` : Math.round(v).toLocaleString("en-IN");
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}%`);
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "text-slate-300" : v > 0 ? "text-profit" : "text-loss");
const times = (v: number | null | undefined) => (v == null ? "—" : v >= 999 ? "no sellers" : `${v.toFixed(1)}×`);
const clock = (at: string | null | undefined) =>
  at ? new Date(at).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }) : "";

/**
 * After the close (desk `close_orders.py`): for each stock, the buy and sell orders still waiting in Groww's book, the
 * last hour of the day, delivery %, F&O open interest and NSE's short selling, read into plain points for "real demand"
 * and for "short sellers buying back". Read-only; Arm only puts a stock on a bot's Trade list.
 */
export function CloseOrders({ armed, arming, onArm, onArmMany, paused = false }: Props) {
  const [data, setData] = useState<CloseData | null>(null);
  const [saved, setSaved] = useState<{ run: CloseOrderRun; rows: CloseOrderRow[] } | null>(null);
  const [history, setHistory] = useState<{ days: { day: string; runs: number }[]; day: string | null; runs: CloseOrderRun[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState<Show>("ALL");
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState("");
  const [lists, setLists] = useState<ScanList[]>(["fno"]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    try {
      const s = JSON.parse(localStorage.getItem(STORE) || "null");
      if (s?.show) setShow(s.show);
      if (typeof s?.extra === "string") setExtra(s.extra);
      setLists(cleanLists(s?.lists, ["fno"]));
    } catch {
      /* private mode */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ show, extra, lists }));
    } catch {
      /* not kept */
    }
  }, [show, extra, lists]);

  const loadHistory = useCallback((day?: string) => {
    api.closeOrdersHistory(day).then(setHistory).catch(() => undefined);
  }, []);
  useEffect(() => {
    api.closeOrders().then(setData).catch(() => undefined);
    loadHistory();
  }, [loadHistory]);
  useEffect(() => {
    if (!data?.running) return;
    const id = setInterval(() => {
      api
        .closeOrders()
        .then((d) => {
          setData(d);
          if (!d.running) loadHistory();
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Lost the scan"));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [data?.running, loadHistory]);

  const start = async () => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const typed = extra.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      let names: string[] = [];
      if (lists.length) names = await resolveScanSymbols(lists, armed.keys()).catch((e) => (typed.length ? [] : Promise.reject(e)));
      const symbols = Array.from(new Set([...typed, ...names])).slice(0, 250);
      if (!symbols.length) throw new Error("Pick a stock list or type some stocks.");
      await api.closeOrdersScan(symbols);
      setData(await api.closeOrders());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not start the scan");
    } finally {
      setBusy(false);
    }
  };

  const openRun = async (id: number) => {
    try {
      const run = await api.closeOrdersRun(id);
      setSaved({ run, rows: run.rows });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not open the saved scan");
    }
  };

  const all = useMemo(() => (saved ? saved.rows : data?.rows ?? []), [saved, data?.rows]);
  const counts = useMemo(
    () => ({
      UNUSUAL: all.filter((r) => r.unusual_buy).length,
      DEMAND: all.filter((r) => r.verdict === "DEMAND").length,
      SHORT_COVER: all.filter((r) => r.verdict === "SHORT_COVER").length,
      MIXED: all.filter((r) => r.verdict === "MIXED").length,
    }),
    [all]
  );
  const ticks = useTicks();
  const filtered = useMemo(
    () =>
      all.filter((r) => {
        if (!matchesText(query, r.symbol) || !ticks.shows(r.symbol)) return false;
        if (show === "UNUSUAL") return r.unusual_buy;
        return show === "ALL" || r.verdict === show;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [all, query, show, ticks.only, ticks.ticked]
  );
  const order = { DEMAND: 0, SHORT_COVER: 1, MIXED: 2, QUIET: 3 };
  const { sorted: rows, sort, onSort } = useSort<CloseOrderRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "verdict":
        return order[r.verdict];
      case "ratio":
        return r.buy_sell_ratio;
      case "buy":
        return r.buy_qty;
      case "sell":
        return r.sell_qty;
      case "vsvol":
        return r.buy_vs_volume_pct;
      case "day":
        return r.day_move_pct ?? null;
      case "late":
        return r.last_hour_pct ?? null;
      case "rush":
        return r.end_rush ?? null;
      case "deliv":
        return r.delivery_pct ?? null;
      case "oi":
        return r.oi_change_pct;
      case "short":
        return r.short_pct ?? null;
    }
  });
  const head = (k: SortKey, label: string, title: string, left = false) => (
    <SortTh k={k} label={label} sort={sort} onSort={onSort} num={!left} text={left && k === "symbol"} title={title} />
  );

  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Moon size={15} className="text-accentSky" /> After the close: real buyers, or short sellers buying back?
        </h2>
        <button
          type="button"
          onClick={() => void start()}
          disabled={busy || data?.running || paused}
          title={paused ? "Scans are paused at the top of the page" : undefined}
          className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
        >
          {busy || data?.running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
        </button>
        <StopScan running={data?.running} stop={() => api.closeOrdersStop()} onStopped={() => void api.closeOrders().then(setData)} />
      </div>
      <GuideBar
        lesson="scalp-after-close"
        terms={["orders_waiting", "short_covering", "delivery", "open_interest", "volume", "arm"]}
        note="Run it after 15:30 (best after 18:00, when NSE's delivery % is out). Green = real buyers. Amber = short sellers buying back."
        className="mb-2"
      />
      <ul className="mb-3 max-w-3xl list-disc space-y-0.5 pl-5 text-sm text-slate-400">
        <li>
          <b className="text-slate-200">Buy orders waiting far above sell orders</b> after the market shut: people wanted the stock and could not
          get it.
        </li>
        <li>
          <b className="text-slate-200">Real demand</b>: many buyers still waiting, high delivery %, F&amp;O open interest rising with the price.
        </li>
        <li>
          <b className="text-slate-200">Short sellers buying back</b>: it fell during the day, then jumped in the last hour on heavy volume, low
          delivery %, F&amp;O open interest falling, and few buyers left waiting.
        </li>
      </ul>

      <FilterRow
        status={
          saved
            ? `Saved scan of ${clock(saved.run.as_of)}`
            : data?.running
              ? `Reading ${data.done} of ${data.total}…`
              : data?.as_of
                ? `Last scan ${clock(data.as_of)} · ${all.length} stocks · ${counts.UNUSUAL} with unusual buy orders`
                : "No scan yet"
        }
      >
        <SelectFilter
          label="Show"
          value={show}
          onChange={(v) => setShow(v as Show)}
          options={[
            { value: "ALL", label: `All (${all.length})` },
            { value: "UNUSUAL", label: `Unusual buy orders (${counts.UNUSUAL})` },
            { value: "DEMAND", label: `Real demand (${counts.DEMAND})` },
            { value: "SHORT_COVER", label: `Short sellers buying back (${counts.SHORT_COVER})` },
            { value: "MIXED", label: `Both signs (${counts.MIXED})` },
            { value: "QUIET", label: "Nothing unusual" },
          ]}
          className="w-64"
        />
        <TextFilter value={query} onChange={setQuery} />
        <FilterField label="Stocks">
          <ScanListPicker value={lists} onChange={setLists} />
        </FilterField>
        <FilterField label="Also these stocks">
          <input
            value={extra}
            onChange={(e) => setExtra(e.target.value)}
            placeholder="ALICON, HAVELLS"
            className="min-h-9 w-44 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
          />
        </FilterField>
        {history?.days.length ? (
          <FilterField label="Saved scans">
            <span className="inline-flex gap-1">
              <select
                value={history.day ?? ""}
                onChange={(e) => loadHistory(e.target.value)}
                className="min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
              >
                {history.days.map((d) => (
                  <option key={d.day} value={d.day}>
                    {d.day} ({d.runs})
                  </option>
                ))}
              </select>
              <select
                value={saved?.run.id ?? ""}
                onChange={(e) => (e.target.value ? void openRun(Number(e.target.value)) : setSaved(null))}
                className="min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
              >
                <option value="">Latest scan</option>
                {history.runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    {clock(r.as_of)}
                  </option>
                ))}
              </select>
            </span>
          </FilterField>
        ) : null}
      </FilterRow>

      {error && <p className="mb-2 text-xs text-loss">{error}</p>}
      {!saved && data?.error && <p className="mb-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{data.error}</p>}
      {!saved && data?.notes?.length ? (
        <ul className="mb-2 space-y-0.5 text-xs text-amber-300/90">
          {data.notes.map((n) => (
            <li key={n}>• {n}</li>
          ))}
        </ul>
      ) : null}

      {rows.length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-500">
          {data?.running ? "Reading the order book…" : all.length ? "No stock matches these filters." : "Nothing is read until you press Scan now."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          {onArmMany ? <TickBar ticks={ticks} onArm={onArmMany} /> : null}
          <table className="w-full min-w-[1150px] text-sm">
            <thead className="text-left text-xs text-slate-400">
              <tr>
                <TickHead ticks={ticks} symbols={rows.map((r) => r.symbol)} />
                {head("symbol", "Stock", "Stock", true)}
                {head("verdict", "Answer", "Real demand / short sellers buying back / both / nothing unusual. Click a row for why.", true)}
                {head("buy", "Buy orders waiting", "Shares people still want to buy (Groww's book when scanned)")}
                {head("sell", "Sell orders waiting", "Shares people still want to sell")}
                {head("ratio", "Buyers vs sellers", "Buy orders waiting ÷ sell orders waiting")}
                {head("vsvol", "Waiting vs day volume", "Buy orders waiting as % of all shares traded today")}
                {head("day", "Day move", "Close vs yesterday's close")}
                {head("late", "Last hour", "Move from 14:30 to the close")}
                {head("rush", "End volume", "Volume per minute after 14:45 vs the day's usual pace")}
                {head("deliv", "Delivery", "Share of today's volume taken home (NSE, about 18:00), vs its usual")}
                {head("oi", "F&O OI", "Open interest change today (F&O stocks only)")}
                {head("short", "Sold short", "Shares sold short as % of the day's volume (NSE's file, next day)")}
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const v = VERDICT[r.verdict];
                const armedOn = armed.get(r.symbol) ?? [];
                const isOpen = open === r.symbol;
                return (
                  <Fragment key={r.symbol}>
                    <tr className="cursor-pointer border-t border-slate-800/70 hover:bg-white/[0.03]" onClick={() => setOpen(isOpen ? null : r.symbol)}>
                      <TickCell ticks={ticks} symbol={r.symbol} />
                      <td className="whitespace-nowrap py-1.5 font-semibold">
                        {isOpen ? <ChevronDown size={12} className="mr-1 inline" /> : <ChevronRight size={12} className="mr-1 inline" />}
                        <GrowwLink symbol={r.symbol} />
                        {r.fno ? <span className="ml-1 text-[10px] font-normal text-slate-500">F&amp;O</span> : null}
                      </td>
                      <td className="whitespace-nowrap py-1.5">
                        <span title={v.tip} className={clsx("rounded px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset", v.cls)}>
                          {v.label}
                        </span>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpen(isOpen ? null : r.symbol);
                          }}
                          aria-expanded={isOpen}
                          className="ml-1 rounded px-1 text-[11px] text-accentSky hover:underline"
                        >
                          {isOpen ? "Hide why" : "Why?"}
                        </button>
                        {r.unusual_buy ? <div className="text-[10px] text-profit">unusual buy orders</div> : null}
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-profit">
                        {qty(r.buy_qty)}
                        {r.buy_orders_top5 != null ? <div className="text-[10px] text-slate-500">{r.buy_orders_top5} orders at best 5</div> : null}
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-loss">
                        {qty(r.sell_qty)}
                        {r.sell_orders_top5 != null ? <div className="text-[10px] text-slate-500">{r.sell_orders_top5} orders at best 5</div> : null}
                      </td>
                      <td className={clsx("py-1.5 text-right tabular-nums font-semibold", (r.buy_sell_ratio ?? 0) >= 3 ? "text-profit" : "text-slate-200")}>
                        {times(r.buy_sell_ratio)}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">{r.buy_vs_volume_pct == null ? "—" : `${r.buy_vs_volume_pct}%`}</td>
                      <td className={clsx("py-1.5 text-right tabular-nums", tone(r.day_move_pct))}>{pct(r.day_move_pct)}</td>
                      <td className={clsx("py-1.5 text-right tabular-nums", tone(r.last_hour_pct))}>
                        {pct(r.last_hour_pct)}
                        {r.move_to_1430_pct != null ? <div className="text-[10px] text-slate-500">before: {pct(r.move_to_1430_pct)}</div> : null}
                      </td>
                      <td className={clsx("py-1.5 text-right tabular-nums", (r.end_rush ?? 0) >= 2 ? "text-amber-300" : "text-slate-300")}>
                        {r.end_rush == null ? "—" : `${r.end_rush}×`}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {r.delivery_pct == null ? <span className="text-slate-500">later</span> : `${r.delivery_pct}%`}
                        {r.delivery_usual_pct != null ? <div className="text-[10px] text-slate-500">usual {r.delivery_usual_pct}%</div> : null}
                      </td>
                      <td className={clsx("py-1.5 text-right tabular-nums", tone(r.oi_change_pct))}>{r.fno ? pct(r.oi_change_pct) : "—"}</td>
                      <td className="py-1.5 text-right tabular-nums">{r.short_pct == null ? <span className="text-slate-500">—</span> : `${r.short_pct}%`}</td>
                      <td className="py-1.5 text-right" onClick={(e) => e.stopPropagation()}>
                        {armedOn.length ? <span className="mr-1 text-[11px] font-semibold text-profit">{armedOn.join(" · ")}</span> : null}
                        <button
                          type="button"
                          disabled={arming === r.symbol}
                          onClick={() => onArm(r.symbol)}
                          className="min-h-9 rounded-md px-2.5 text-xs font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/10 disabled:opacity-50"
                        >
                          Arm
                        </button>
                      </td>
                    </tr>
                    {isOpen ? (
                      <tr className="bg-white/[0.02]">
                        <td />
                        <td colSpan={13} className="py-2 pr-2 text-xs">
                          <Why r={r} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-slate-500">
            An estimate from public numbers: nobody publishes who bought. Information only; nothing is traded. Arm only puts a stock on a bot&apos;s
            Trade list.
          </p>
        </div>
      )}
    </section>
  );
}

function Why({ r }: { r: CloseOrderRow }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div>
        <p className="mb-1 font-semibold text-profit">Signs of real demand ({r.demand_points} points)</p>
        {r.demand_why.length ? (
          <ul className="list-disc space-y-0.5 pl-4 text-slate-300">
            {r.demand_why.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : (
          <p className="text-slate-500">None.</p>
        )}
      </div>
      <div>
        <p className="mb-1 font-semibold text-amber-300">Signs of short sellers buying back ({r.cover_points} points)</p>
        {r.cover_why.length ? (
          <ul className="list-disc space-y-0.5 pl-4 text-slate-300">
            {r.cover_why.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : (
          <p className="text-slate-500">None.</p>
        )}
      </div>
      {r.missing.length ? <p className="text-slate-500 sm:col-span-2">Not known yet: {r.missing.join("; ")}.</p> : null}
    </div>
  );
}
