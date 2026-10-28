"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Bell, ExternalLink, ListPlus, Loader2, Newspaper, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { api, type NewsAlertRow, type NewsFeedItem, type NewsWatchList, type NewsWatchStock } from "@/lib/api";
import { directionClass, toneClass, toneWord } from "@/lib/newsTone";
import { GrowwLink } from "@/components/ui/growwLink";
import { useArming } from "@/components/Scalp/useArming";
import { StockNewsPanel, validTill } from "@/components/Terminal/StockNewsPanel";
import { WatchNewsDialog } from "@/components/News/WatchNewsDialog";
import { NewsCheckPanel } from "@/components/News/NewsCheck";

type Tab = { kind: "list"; id: number } | { kind: "bots" } | { kind: "feed" } | { kind: "alerts" } | { kind: "picked" } | { kind: "check" };

const card = "terminal-dark rounded-xl border border-white/10 p-4";
const field = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";

function rupees(v: number | null | undefined): string {
  return v ? `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
}

function when(at: string | null | undefined): string {
  if (!at) return "";
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

function symbolsOf(text: string): string[] {
  return Array.from(new Set(text.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)));
}

/**
 * The News page: news and its impact for the stocks you care about. Watch lists (any number, any NSE stocks),
 * the bots' armed / held stocks, every F&O stock's NSE filings, and the alerts already sent. Each headline counts
 * until the next trading day's close (`valid_until`). Information only: nothing here trades; Arm only puts a stock
 * on a bot's Trade list.
 */
export function NewsPage() {
  const [lists, setLists] = useState<NewsWatchList[]>([]);
  const [watch, setWatch] = useState<NewsWatchStock[]>([]);
  const [tab, setTab] = useState<Tab>({ kind: "bots" });
  const [picked, setPicked] = useState<string[]>([]);
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [showExpired, setShowExpired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newSymbols, setNewSymbols] = useState("");
  const [addText, setAddText] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { armNote, arm, loadDesks, dialogs } = useArming();
  useEffect(() => loadDesks(), [loadDesks]);

  const load = useCallback(async () => {
    try {
      const [l, w] = await Promise.all([api.newsLists(), api.newsWatch()]);
      setLists(l.lists);
      setWatch(w.stocks);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not load the news");
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 60_000);
    return () => clearInterval(id);
  }, [load]);

  // /news/?symbols=A,B (from the Scalp page's Watch news) opens those stocks.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("symbols");
    const names = q ? symbolsOf(q) : [];
    if (names.length) {
      setPicked(names);
      setTab({ kind: "picked" });
      setOpen(names[0]);
    }
  }, []);

  const current = tab.kind === "list" ? lists.find((l) => l.id === tab.id) ?? null : null;
  const rows: { symbol: string; entry: NewsWatchStock | null }[] = useMemo(() => {
    const by = new Map(watch.map((w) => [w.symbol, w]));
    if (tab.kind === "list") return (current?.symbols ?? []).map((s) => ({ symbol: s, entry: by.get(s) ?? null }));
    if (tab.kind === "picked") return picked.map((s) => ({ symbol: s, entry: by.get(s) ?? null }));
    if (tab.kind === "bots") return watch.filter((w) => w.armed.length || w.held.length).map((w) => ({ symbol: w.symbol, entry: w }));
    return [];
  }, [tab, current, picked, watch]);

  const shown = rows.map((r) => r.symbol).join(",");
  useEffect(() => {
    const names = shown ? shown.split(",") : [];
    if (!names.length) return;
    api.newsPrices(names.slice(0, 100)).then((r) => setPrices((p) => ({ ...p, ...r.prices }))).catch(() => undefined);
  }, [shown]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await load();
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const tabs: { key: string; label: string; tab: Tab }[] = [
    { key: "bots", label: "Bots' stocks", tab: { kind: "bots" } },
    ...lists.map((l) => ({ key: `l${l.id}`, label: `${l.name} (${l.symbols.length})`, tab: { kind: "list", id: l.id } as Tab })),
    ...(picked.length ? [{ key: "picked", label: `From Scalp (${picked.length})`, tab: { kind: "picked" } as Tab }] : []),
    { key: "feed", label: "All F&O filings", tab: { kind: "feed" } },
    { key: "alerts", label: "Alerts", tab: { kind: "alerts" } },
    { key: "check", label: "Did it move?", tab: { kind: "check" } },
  ];
  const keyOf = (t: Tab) => (t.kind === "list" ? `l${t.id}` : t.kind);

  return (
    <div className="space-y-4">
      <section className={card}>
        <h1 className="inline-flex items-center gap-2 text-lg font-semibold text-slate-100">
          <Newspaper size={18} className="text-amber-300" /> News and impact
        </h1>
        <p className="mt-1 text-xs text-slate-400">
          NSE company filings and Google News headlines, each read as positive / negative with its impact (AI when on, else
          the rule list). A headline counts until <b>15:30 on the next trading day</b> after it came out (Friday&apos;s news
          until Monday&apos;s close); after that it is shown as expired and no longer moves the stock&apos;s view or alerts.
          Every watch list&apos;s stocks are read on each news pass with the bots&apos; stocks (up to 120). Information only:
          nothing is traded from this page.
        </p>
        <form
          className="mt-3 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const name = newName.trim();
            if (!name) return;
            void run(async () => {
              const row = await api.createNewsList(name, symbolsOf(newSymbols));
              setNewName("");
              setNewSymbols("");
              setTab({ kind: "list", id: row.id });
            });
          }}
        >
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            New watch list
            <input value={newName} maxLength={40} onChange={(e) => setNewName(e.target.value)} placeholder="Name, e.g. Banks" className={clsx(field, "w-44")} />
          </label>
          <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-xs text-slate-400">
            Stocks (any NSE symbols, comma or space separated)
            <input value={newSymbols} onChange={(e) => setNewSymbols(e.target.value)} placeholder="HDFCBANK, SBIN, ICICIBANK" className={field} />
          </label>
          <button type="submit" disabled={busy || !newName.trim()} className="inline-flex min-h-9 items-center gap-1 rounded-md bg-amber-500/15 px-3 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/25 disabled:opacity-50">
            <Plus size={13} /> Create
          </button>
          <button type="button" onClick={() => void load()} className="inline-flex min-h-9 items-center gap-1 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5">
            <RefreshCw size={13} /> Refresh
          </button>
        </form>
        {error ? <p className="mt-2 text-xs text-rose-300">{error}</p> : null}
        {armNote ? <div className="mt-2 text-xs">{armNote}</div> : null}
      </section>

      <div role="tablist" className="flex flex-wrap gap-1.5">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            type="button"
            aria-selected={keyOf(tab) === t.key}
            onClick={() => setTab(t.tab)}
            className={clsx(
              "min-h-8 rounded-md px-3 text-xs font-semibold ring-1 ring-inset",
              keyOf(tab) === t.key ? "bg-amber-500/15 text-slate-100 ring-amber-400/60" : "text-slate-400 ring-white/15 hover:bg-white/5"
            )}
          >
            {t.label}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1.5 text-xs text-slate-400">
          <input type="checkbox" checked={showExpired} onChange={(e) => setShowExpired(e.target.checked)} className="accent-amber-400" />
          Show expired news
        </label>
      </div>

      {tab.kind === "list" && current ? (
        <section className={clsx(card, "flex flex-wrap items-end gap-2")}>
          {renaming !== null ? (
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void run(() => api.updateNewsList(current.id, { name: renaming }).then(() => setRenaming(null)));
              }}
            >
              <input value={renaming} maxLength={40} onChange={(e) => setRenaming(e.target.value)} className={clsx(field, "w-44")} aria-label="List name" />
              <button type="submit" className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40">Save</button>
            </form>
          ) : (
            <button type="button" onClick={() => setRenaming(current.name)} className="inline-flex min-h-9 items-center gap-1 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5">
              <Pencil size={12} /> Rename
            </button>
          )}
          <form
            className="flex flex-1 items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const names = symbolsOf(addText);
              if (!names.length) return;
              void run(() => api.updateNewsList(current.id, { add: names }).then(() => setAddText("")));
            }}
          >
            <input value={addText} onChange={(e) => setAddText(e.target.value)} placeholder="Add stocks: TCS, INFY" className={clsx(field, "min-w-[180px] flex-1")} aria-label="Add stocks" />
            <button type="submit" disabled={busy} className="inline-flex min-h-9 items-center gap-1 rounded-md px-3 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10 disabled:opacity-50">
              <ListPlus size={13} /> Add
            </button>
          </form>
          <button
            type="button"
            onClick={() => {
              if (!window.confirm(`Delete the watch list "${current.name}"? Its stocks stay armed wherever they are.`)) return;
              void run(() => api.deleteNewsList(current.id).then(() => setTab({ kind: "bots" })));
            }}
            className="inline-flex min-h-9 items-center gap-1 rounded-md px-3 text-xs text-rose-300 ring-1 ring-inset ring-rose-400/30 hover:bg-rose-500/10"
          >
            <Trash2 size={12} /> Delete list
          </button>
        </section>
      ) : null}

      {tab.kind === "check" ? (
        <NewsCheckPanel />
      ) : tab.kind === "feed" ? (
        <FeedTable showExpired={showExpired} onOpen={setOpen} onArm={(s) => arm(s, "News")} onAdded={() => void load()} />
      ) : tab.kind === "alerts" ? (
        <AlertsTable onOpen={setOpen} />
      ) : (
        <StocksTable
          rows={rows}
          prices={prices}
          open={open}
          onOpen={(s) => setOpen(open === s ? null : s)}
          onArm={(s) => arm(s, "News")}
          onRemove={tab.kind === "list" && current ? (s) => void run(() => api.updateNewsList(current.id, { remove: [s] })) : undefined}
          onAdded={() => void load()}
          empty={
            tab.kind === "bots"
              ? "No stocks armed or held on Bots 1-4."
              : tab.kind === "list"
                ? "This list is empty: add stocks above, or tick stocks on the Scalp page and press Watch news."
                : "No stocks."
          }
        />
      )}
      {open && (tab.kind === "feed" || tab.kind === "alerts") ? (
        <div className="overflow-hidden rounded-xl border border-white/10">
          <StockNewsPanel symbol={open} onClose={() => setOpen(null)} />
        </div>
      ) : null}
      {dialogs}
    </div>
  );
}

function StocksTable({
  rows,
  prices,
  open,
  onOpen,
  onArm,
  onRemove,
  onAdded,
  empty,
}: {
  rows: { symbol: string; entry: NewsWatchStock | null }[];
  prices: Record<string, number>;
  open: string | null;
  onOpen: (symbol: string) => void;
  onArm: (symbol: string) => void;
  onRemove?: (symbol: string) => void;
  onAdded: () => void;
  empty: string;
}) {
  const [watchOne, setWatchOne] = useState<string | null>(null);
  if (!rows.length) return <p className={clsx(card, "text-sm text-slate-400")}>{empty}</p>;
  return (
    <section className={clsx(card, "overflow-x-auto p-0")}>
      <table className="w-full min-w-[760px] text-left text-sm">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="px-3 py-2 font-medium">Stock</th>
            <th className="px-3 py-2 font-medium">Price</th>
            <th className="px-3 py-2 font-medium">News view</th>
            <th className="px-3 py-2 font-medium">New entry</th>
            <th className="px-3 py-2 font-medium">Latest headline</th>
            <th className="px-3 py-2 font-medium">Read</th>
            <th className="px-3 py-2 font-medium" />
          </tr>
        </thead>
        <tbody>
          {rows.map(({ symbol, entry }) => {
            const l = entry?.latest ?? null;
            return (
              <FragmentRow key={symbol} open={open === symbol} symbol={symbol}>
                <td className="px-3 py-2 font-semibold text-slate-100">
                  <GrowwLink symbol={symbol} />
                  {entry?.held.length ? <span className="ml-1 rounded bg-sky-500/15 px-1 text-[10px] text-sky-200">held</span> : null}
                  {entry?.armed.length ? <span className="ml-1 text-[10px] font-normal text-slate-500">{entry.armed.join(", ")}</span> : null}
                </td>
                <td className="px-3 py-2 font-mono text-slate-200">{rupees(prices[symbol])}</td>
                <td className="px-3 py-2">
                  {l ? (
                    <span className={clsx("rounded px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset", toneClass(l.overall.sentiment))}>
                      {l.overall.critical ? "⚠ " : ""}
                      {toneWord(l.overall.sentiment)} · {l.overall.impact.toLowerCase()}
                    </span>
                  ) : (
                    <span className="text-xs text-slate-500">next news pass</span>
                  )}
                </td>
                <td className={clsx("px-3 py-2 text-xs font-semibold", directionClass(l?.action.code))} title={l?.action.why}>
                  {l?.action.label ?? "—"}
                </td>
                <td className="max-w-[320px] truncate px-3 py-2 text-xs text-slate-300" title={l?.top ?? undefined}>
                  {l?.top ?? "—"}
                </td>
                <td className="px-3 py-2 text-xs text-slate-500">{l ? when(l.at) : ""}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">
                  <button type="button" onClick={() => onOpen(symbol)} className="min-h-8 rounded-md px-2 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10">
                    {open === symbol ? "Hide" : "News"}
                  </button>
                  <button type="button" onClick={() => onArm(symbol)} className="ml-1 min-h-8 rounded-md px-2 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10">
                    Arm
                  </button>
                  {onRemove ? (
                    <button type="button" onClick={() => onRemove(symbol)} aria-label={`Remove ${symbol}`} className="ml-1 rounded p-1 text-slate-500 hover:bg-white/5 hover:text-rose-300">
                      <X size={13} />
                    </button>
                  ) : (
                    <button type="button" onClick={() => setWatchOne(symbol)} aria-label={`Add ${symbol} to a watch list`} title="Add to a watch list" className="ml-1 rounded p-1 text-slate-500 hover:bg-white/5 hover:text-amber-300">
                      <ListPlus size={13} />
                    </button>
                  )}
                </td>
              </FragmentRow>
            );
          })}
        </tbody>
      </table>
      {watchOne ? <WatchNewsDialog symbols={[watchOne]} onClose={() => setWatchOne(null)} onAdded={onAdded} /> : null}
    </section>
  );
}

function FragmentRow({ open, symbol, children }: { open: boolean; symbol: string; children: React.ReactNode }) {
  return (
    <>
      <tr className="border-t border-white/5">{children}</tr>
      {open ? (
        <tr>
          <td colSpan={7} className="p-0">
            <StockNewsPanel symbol={symbol} onClose={() => undefined} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function FeedTable({
  showExpired,
  onOpen,
  onArm,
  onAdded,
}: {
  showExpired: boolean;
  onOpen: (s: string) => void;
  onArm: (s: string) => void;
  onAdded: () => void;
}) {
  const [items, setItems] = useState<NewsFeedItem[] | null>(null);
  const [only, setOnly] = useState<"all" | "medium">("medium");
  const [watchOne, setWatchOne] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const get = () =>
      api
        .newsFeed(300, !showExpired, true)
        .then((r) => alive && setItems(r.items))
        .catch(() => alive && setItems([]));
    void get();
    const id = setInterval(get, 60_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [showExpired]);
  const list = (items ?? []).filter((it) => only === "all" || it.impact !== "LOW" || it.critical);
  return (
    <section className={clsx(card, "overflow-x-auto")}>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <span>Every F&amp;O stock&apos;s NSE filings from the news passes, newest first.</span>
        <select value={only} onChange={(e) => setOnly(e.target.value === "all" ? "all" : "medium")} className={clsx(field, "ml-auto")}>
          <option value="medium">Medium and high impact</option>
          <option value="all">All filings</option>
        </select>
      </div>
      {items === null ? (
        <p className="text-sm text-slate-400"><Loader2 size={14} className="mr-1 inline animate-spin" />Loading…</p>
      ) : !list.length ? (
        <p className="text-sm text-slate-400">No filings yet: the news watcher fills this on its passes (Settings → News alerts, All F&amp;O).</p>
      ) : (
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="text-xs text-slate-500">
            <tr>
              <th className="py-1.5 pr-2 font-medium">Time</th>
              <th className="py-1.5 pr-2 font-medium">Stock</th>
              <th className="py-1.5 pr-2 font-medium">Price</th>
              <th className="py-1.5 pr-2 font-medium">Read</th>
              <th className="py-1.5 pr-2 font-medium">Filing</th>
              <th className="py-1.5 pr-2 font-medium">Valid till</th>
              <th className="py-1.5 font-medium" />
            </tr>
          </thead>
          <tbody>
            {list.map((it) => (
              <tr key={`${it.symbol}-${it.url}-${it.title}`} className={clsx("border-t border-white/5", it.valid === false && "opacity-50")}>
                <td className="whitespace-nowrap py-1.5 pr-2 text-xs text-slate-400">{when(it.at)}</td>
                <td className="py-1.5 pr-2 font-semibold text-slate-100"><GrowwLink symbol={it.symbol} /></td>
                <td className="py-1.5 pr-2 font-mono text-xs text-slate-200">{rupees(it.price)}</td>
                <td className="py-1.5 pr-2">
                  <span className={clsx("rounded px-1.5 py-px text-[10px] font-semibold ring-1 ring-inset", toneClass(it.sentiment))}>
                    {it.critical ? "⚠ " : ""}
                    {toneWord(it.sentiment)} · {it.impact.toLowerCase()}
                  </span>
                </td>
                <td className="max-w-[360px] py-1.5 pr-2 text-xs">
                  <a href={it.url} target="_blank" rel="noreferrer" className="text-slate-200 hover:underline">
                    {it.title} <ExternalLink size={10} className="inline opacity-50" />
                  </a>
                </td>
                <td className="whitespace-nowrap py-1.5 pr-2 text-xs text-slate-400">
                  {it.valid === false ? "expired" : it.valid_until ? validTill(it.valid_until) : "—"}
                </td>
                <td className="whitespace-nowrap py-1.5 text-right">
                  <button type="button" onClick={() => onOpen(it.symbol)} className="min-h-8 rounded-md px-2 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10">News</button>
                  <button type="button" onClick={() => onArm(it.symbol)} className="ml-1 min-h-8 rounded-md px-2 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10">Arm</button>
                  <button type="button" onClick={() => setWatchOne(it.symbol)} aria-label={`Add ${it.symbol} to a watch list`} title="Add to a watch list" className="ml-1 rounded p-1 text-slate-500 hover:bg-white/5 hover:text-amber-300">
                    <ListPlus size={13} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {watchOne ? <WatchNewsDialog symbols={[watchOne]} onClose={() => setWatchOne(null)} onAdded={onAdded} /> : null}
    </section>
  );
}

function AlertsTable({ onOpen }: { onOpen: (s: string) => void }) {
  const [rows, setRows] = useState<NewsAlertRow[] | null>(null);
  useEffect(() => {
    api.newsAlerts(0, 100).then((r) => setRows(r.alerts)).catch(() => setRows([]));
  }, []);
  return (
    <section className={clsx(card, "overflow-x-auto")}>
      {rows === null ? (
        <p className="text-sm text-slate-400"><Loader2 size={14} className="mr-1 inline animate-spin" />Loading…</p>
      ) : !rows.length ? (
        <p className="text-sm text-slate-400">No news alerts yet.</p>
      ) : (
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="text-xs text-slate-500">
            <tr>
              <th className="py-1.5 pr-2 font-medium">Sent</th>
              <th className="py-1.5 pr-2 font-medium">Stock</th>
              <th className="py-1.5 pr-2 font-medium">Price then</th>
              <th className="py-1.5 pr-2 font-medium">Read</th>
              <th className="py-1.5 pr-2 font-medium">Headline</th>
              <th className="py-1.5 pr-2 font-medium">What to do</th>
              <th className="py-1.5 pr-2 font-medium">Valid till</th>
              <th className="py-1.5 font-medium" />
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id} className="border-t border-white/5">
                <td className="whitespace-nowrap py-1.5 pr-2 text-xs text-slate-400">
                  {a.critical ? <Bell size={11} className="mr-1 inline text-rose-300" /> : null}
                  {when(a.created_at)}
                </td>
                <td className="py-1.5 pr-2 font-semibold text-slate-100"><GrowwLink symbol={a.symbol} /></td>
                <td className="py-1.5 pr-2 font-mono text-xs text-slate-200">{rupees(a.price)}</td>
                <td className="py-1.5 pr-2">
                  <span className={clsx("rounded px-1.5 py-px text-[10px] font-semibold ring-1 ring-inset", toneClass(a.sentiment))}>
                    {toneWord(a.sentiment)} · {a.impact.toLowerCase()}
                  </span>
                </td>
                <td className="max-w-[320px] truncate py-1.5 pr-2 text-xs text-slate-200" title={a.title}>{a.title}</td>
                <td className={clsx("py-1.5 pr-2 text-xs font-semibold", directionClass(a.action))} title={a.action_why ?? undefined}>{a.action_label ?? a.action}</td>
                <td className="whitespace-nowrap py-1.5 pr-2 text-xs text-slate-400">{a.valid_until ? validTill(a.valid_until) : "—"}</td>
                <td className="py-1.5 text-right">
                  <button type="button" onClick={() => onOpen(a.symbol)} className="min-h-8 rounded-md px-2 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10">News</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
