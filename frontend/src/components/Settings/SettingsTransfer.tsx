"use client";

import { useRef, useState } from "react";
import { Download, Upload } from "lucide-react";
import { smaApi, type BotSummary, type SettingsFile } from "@/lib/smaApi";

const DESK_KEYS = ["1", "2", "3", "4", "5", "research"] as const;

function label(desk: string, bots: BotSummary[]): string {
  if (desk === "research") return "Research";
  const n = Number(desk);
  return bots.find((b) => b.bot === n)?.name ?? `Bot ${n}`;
}

const BTN =
  "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold ring-1 ring-inset disabled:opacity-50";

/**
 * Export / import settings: a JSON file of the bots' strategy settings, each stock's own settings, the Trade lists
 * and the fast-mover card (backend main.export_settings / import_settings). An import never changes a bot's
 * PAPER/LIVE switch or name, checks every value like Save, and asks again before touching a LIVE bot.
 */
export function SettingsTransfer({ bots, onImported }: { bots: BotSummary[]; onImported: () => void }) {
  const [open, setOpen] = useState(false);
  const [exportDesks, setExportDesks] = useState<string[]>([...DESK_KEYS]);
  const [withFast, setWithFast] = useState(true);
  const [file, setFile] = useState<SettingsFile | null>(null);
  const [fileName, setFileName] = useState("");
  const [importDesks, setImportDesks] = useState<string[]>([]);
  const [withStocks, setWithStocks] = useState(true);
  const [withLists, setWithLists] = useState(false);
  const [importFast, setImportFast] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  const toggle = (list: string[], set: (v: string[]) => void, d: string) =>
    set(list.includes(d) ? list.filter((x) => x !== d) : [...list, d]);

  const doExport = async () => {
    setBusy(true);
    setNote(null);
    try {
      const data = await smaApi.exportSettings(exportDesks, withFast);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
      a.href = url;
      a.download = `paltra-settings-${data.server ?? "desk"}-${stamp}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setNote(`Exported ${Object.keys(data.desks).length} bot(s)${data.fast_bot ? " and the fast-mover card" : ""}.`);
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not export");
    } finally {
      setBusy(false);
    }
  };

  const readFile = async (picked: File | undefined) => {
    setNote(null);
    setFile(null);
    if (!picked) return;
    try {
      const data = JSON.parse(await picked.text()) as SettingsFile;
      if (data?.kind !== "paltra-settings" || typeof data.desks !== "object") throw new Error("not a PalTra settings file");
      setFile(data);
      setFileName(picked.name);
      setImportDesks(Object.keys(data.desks).filter((d) => (DESK_KEYS as readonly string[]).includes(d)));
      setImportFast(Boolean(data.fast_bot));
    } catch (err: unknown) {
      setNote(`That file cannot be read: ${err instanceof Error ? err.message : "not JSON"}`);
    } finally {
      if (picker.current) picker.current.value = "";
    }
  };

  const doImport = async () => {
    if (!file) return;
    const names = importDesks.map((d) => label(d, bots));
    const live = importDesks.filter((d) => d !== "research" && bots.find((b) => b.bot === Number(d))?.mode === "LIVE");
    const bot5Live = bots.find((b) => b.bot === 5)?.mode === "LIVE";
    const what = [
      names.length ? `strategy settings of ${names.join(", ")}` : "",
      withStocks && names.length ? "each stock's own settings" : "",
      withLists && names.length ? "add the file's stocks to the Trade lists" : "",
      importFast && file.fast_bot ? "the fast-mover card" : "",
    ]
      .filter(Boolean)
      .join("; ");
    if (!window.confirm(`Import from ${fileName}: ${what}? PAPER/LIVE switches and names stay as they are.`)) return;
    const needLive = live.length > 0 || (importFast && Boolean(file.fast_bot?.on) && bot5Live);
    if (
      needLive &&
      !window.confirm(
        `${live.map((d) => label(d, bots)).join(", ") || "Bot 5"} is LIVE and trades real money. The imported settings apply to its next orders. Import anyway?`
      )
    )
      return;
    setBusy(true);
    setNote(null);
    try {
      const res = await smaApi.importSettings({
        file,
        desks: importDesks,
        stock_settings: withStocks,
        trade_lists: withLists,
        fast_bot: importFast,
        confirm_live: needLive,
      });
      const lines = res.desks.map((r) => {
        const parts = [`${label(r.desk, bots)}: ${r.changed.length} setting(s) changed`];
        if (r.added.length) parts.push(`${r.added.length} stock(s) armed`);
        return [...parts, ...r.notes].join(", ");
      });
      if (res.fast_bot) lines.push("Fast-mover card imported");
      setNote(lines.join(" · ") || "Imported: nothing was different.");
      onImported();
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not import");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2 rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-300">
      <button type="button" onClick={() => setOpen((v) => !v)} className="font-semibold text-sky-300 hover:underline">
        {open ? "▾" : "▸"} Export / import settings
      </button>
      {open ? (
        <div className="mt-2 space-y-3">
          <section className="space-y-2">
            <p className="text-slate-400">
              Download every ticked bot&apos;s strategy settings, each stock&apos;s own settings and Trade list, and the fast-mover
              card as one file. Keep it as a backup, or import it on the other server (main desk ↔ backtest server).
            </p>
            <div className="flex flex-wrap items-center gap-3">
              {DESK_KEYS.map((d) => (
                <label key={d} className="flex items-center gap-1">
                  <input type="checkbox" checked={exportDesks.includes(d)} onChange={() => toggle(exportDesks, setExportDesks, d)} />
                  {label(d, bots)}
                </label>
              ))}
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={withFast} onChange={(e) => setWithFast(e.target.checked)} />
                Fast-mover card
              </label>
              <button
                type="button"
                disabled={busy || !exportDesks.length}
                onClick={doExport}
                className={`${BTN} text-emerald-200 ring-emerald-400/40 hover:bg-emerald-500/10`}
              >
                <Download size={12} aria-hidden /> Export
              </button>
            </div>
          </section>
          <section className="space-y-2 border-t border-white/10 pt-2">
            <div className="flex flex-wrap items-center gap-2">
              <input ref={picker} type="file" accept="application/json,.json" className="hidden" onChange={(e) => readFile(e.target.files?.[0])} />
              <button
                type="button"
                disabled={busy}
                onClick={() => picker.current?.click()}
                className={`${BTN} text-sky-200 ring-sky-400/40 hover:bg-sky-500/10`}
              >
                <Upload size={12} aria-hidden /> Choose a settings file
              </button>
              {file ? (
                <span className="text-slate-400">
                  {fileName} · exported {file.exported_at?.replace("T", " ").slice(0, 16)}
                  {file.server ? ` from the ${file.server === "desk" ? "main desk" : `${file.server} server`}` : ""}
                </span>
              ) : null}
            </div>
            {file ? (
              <>
                <p className="text-slate-400">
                  Each bot in the file goes onto the same bot here. PAPER/LIVE switches and names are never changed, every value is
                  checked like Save, and nothing is written if one is wrong.
                </p>
                <div className="flex flex-wrap items-center gap-3">
                  {DESK_KEYS.filter((d) => file.desks[d]).map((d) => (
                    <label key={d} className="flex items-center gap-1">
                      <input type="checkbox" checked={importDesks.includes(d)} onChange={() => toggle(importDesks, setImportDesks, d)} />
                      {label(d, bots)}
                      {bots.find((b) => d !== "research" && b.bot === Number(d))?.mode === "LIVE" ? (
                        <span className="rounded bg-rose-500/20 px-1 text-[10px] font-semibold text-rose-200">LIVE</span>
                      ) : null}
                    </label>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <label className="flex items-center gap-1" title="Replace each bot's per-stock settings with the file's">
                    <input type="checkbox" checked={withStocks} onChange={(e) => setWithStocks(e.target.checked)} />
                    Each stock&apos;s own settings
                  </label>
                  <label className="flex items-center gap-1" title="Adds the file's stocks to each PAPER bot's Trade list; never removes one. LIVE bots are skipped.">
                    <input type="checkbox" checked={withLists} onChange={(e) => setWithLists(e.target.checked)} />
                    Add the file&apos;s stocks to the Trade lists (PAPER bots)
                  </label>
                  {file.fast_bot ? (
                    <label className="flex items-center gap-1">
                      <input type="checkbox" checked={importFast} onChange={(e) => setImportFast(e.target.checked)} />
                      Fast-mover card
                    </label>
                  ) : null}
                  <button
                    type="button"
                    disabled={busy || (!importDesks.length && !importFast)}
                    onClick={doImport}
                    className={`${BTN} text-amber-200 ring-amber-400/40 hover:bg-amber-500/10`}
                  >
                    <Upload size={12} aria-hidden /> Import
                  </button>
                </div>
              </>
            ) : null}
          </section>
          {note ? <p className="text-slate-300">{note}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
