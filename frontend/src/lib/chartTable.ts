/**
 * The chart's candles as a table: prices, indicators, the candle's pattern
 * name, and for candles inside a trade the P&L from the entry at the close,
 * the high and the low. Pure functions; the component only renders them.
 */
import { candlePattern, type PatternBias } from "@/lib/candlePatterns";
import { pdfText } from "@/lib/backtestPdf";
import { parseClock } from "@/lib/format";
import type { Candle, ChartPayload, TradeRow } from "@/lib/smaApi";

type Marker = ChartPayload["markers"][number];

export type TableRow = {
  /** Unique per row (a candle can hold an exit and a new entry). */
  key: string;
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  change: number | null;
  changePct: number | null;
  volume: number | null;
  sma9: number | null;
  sma21: number | null;
  gapPct: number | null;
  gapChange: number | null;
  vwap: number | null;
  vsVwapPct: number | null;
  rsi: number | null;
  atr: number | null;
  pattern: string;
  bias: PatternBias;
  cross: string;
  tradeRef: string;
  side: string;
  stage: string;
  qty: number | null;
  entryPrice: number | null;
  exitPrice: number | null;
  exitReason: string;
  /** Points / rupees from the entry to this candle's close (the exit price on the exit candle). */
  pnlPts: number | null;
  pnlRs: number | null;
  /** If closed at this candle's high / low. */
  highPts: number | null;
  highRs: number | null;
  lowPts: number | null;
  lowRs: number | null;
  /** Best and worst since the entry, up to and including this candle. */
  bestPts: number | null;
  bestRs: number | null;
  worstPts: number | null;
  worstRs: number | null;
  tradeNet: number | null;
};

type Trade = {
  ref: string;
  direction: string;
  entryTime: number;
  entryPrice: number;
  exitTime: number | null;
  exitPrice: number | null;
  exitReason: string;
  net: number | null;
  qty: number | null;
};

function tradesFromMarkers(markers: Marker[], lookup: TradeRow[], snap: (sec: number) => number): Trade[] {
  const byRef = new Map<string, TradeRow>();
  for (const t of lookup) if (t.trade_ref) byRef.set(t.trade_ref, t);
  const exits = new Map<string, Marker>();
  for (const m of markers) if (m.kind === "EXIT" && m.trade_ref) exits.set(m.trade_ref, m);
  const out: Trade[] = [];
  for (const m of markers) {
    if (m.kind !== "ENTRY") continue;
    const ref = m.trade_ref ?? "";
    const exit = ref ? exits.get(ref) : undefined;
    let row = ref ? byRef.get(ref) : undefined;
    if (!row) {
      // Older rows without an id: same side, fill price and bar.
      row = lookup.find((t) => {
        if (t.direction !== m.direction || Math.abs(t.entry_price - m.price) > 1e-6) return false;
        const when = t.entry_time ? parseClock(t.entry_time) : null;
        return when != null && snap(Math.floor(when.getTime() / 1000)) === snap(m.time);
      });
    }
    out.push({
      ref: ref || (row?.trade_ref ?? ""),
      direction: m.direction,
      entryTime: snap(m.time),
      entryPrice: m.price,
      exitTime: exit ? snap(exit.time) : null,
      exitPrice: exit ? exit.price : null,
      exitReason: exit?.reason ?? "",
      // P&L before charges (the screens' basis); older servers send only the net.
      net: row?.net_pnl ?? exit?.gross_pnl ?? m.gross_pnl ?? exit?.net_pnl ?? m.net_pnl ?? null,
      qty: row?.qty ?? null,
    });
  }
  return out.sort((a, b) => a.entryTime - b.entryTime);
}

const round = (v: number, d = 2) => Math.round(v * 10 ** d) / 10 ** d;

export function buildTableRows(
  candles: Candle[],
  markers: Marker[],
  lookup: TradeRow[],
  snap: (sec: number) => number
): TableRow[] {
  const trades = tradesFromMarkers(markers, lookup, snap);
  const last = candles.length ? candles[candles.length - 1].time : 0;
  const best = new Map<string, { best: number; worst: number }>();
  const out: TableRow[] = [];
  let prevGap: number | null = null;
  candles.forEach((c, i) => {
    const prev = i > 0 ? candles[i - 1] : null;
    const gap = c.sma9 != null && c.sma21 != null && c.sma21 !== 0 ? ((c.sma9 - c.sma21) / c.sma21) * 100 : null;
    const prevSide = prev && prev.sma9 != null && prev.sma21 != null ? Math.sign(prev.sma9 - prev.sma21) : 0;
    const side = c.sma9 != null && c.sma21 != null ? Math.sign(c.sma9 - c.sma21) : 0;
    const cross = prevSide < 0 && side > 0 ? "Bull cross" : prevSide > 0 && side < 0 ? "Bear cross" : "";
    const pattern = candlePattern(candles, i);
    const base = {
      time: c.time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      change: prev ? round(c.close - prev.close) : null,
      changePct: prev && prev.close ? round(((c.close - prev.close) / prev.close) * 100, 3) : null,
      volume: c.volume ?? null,
      sma9: c.sma9,
      sma21: c.sma21,
      gapPct: gap == null ? null : round(gap, 4),
      gapChange: gap != null && prevGap != null ? round(gap - prevGap, 4) : null,
      vwap: c.vwap ?? null,
      vsVwapPct: c.vwap ? round(((c.close - c.vwap) / c.vwap) * 100, 3) : null,
      rsi: c.rsi14 ?? null,
      atr: c.atr14,
      pattern: pattern.name,
      bias: pattern.bias,
      cross,
    };
    prevGap = gap;
    const active = trades.filter((t) => t.entryTime <= c.time && (t.exitTime ?? last) >= c.time);
    const empty = {
      tradeRef: "",
      side: "",
      stage: "",
      qty: null,
      entryPrice: null,
      exitPrice: null,
      exitReason: "",
      pnlPts: null,
      pnlRs: null,
      highPts: null,
      highRs: null,
      lowPts: null,
      lowRs: null,
      bestPts: null,
      bestRs: null,
      worstPts: null,
      worstRs: null,
      tradeNet: null,
    };
    if (!active.length) {
      out.push({ key: `${c.time}`, ...base, ...empty });
      return;
    }
    for (const t of active) {
      const sign = t.direction === "LONG" ? 1 : -1;
      const isEntry = t.entryTime === c.time;
      const isExit = t.exitTime === c.time;
      const at = isExit && t.exitPrice != null ? t.exitPrice : c.close;
      const pts = round(sign * (at - t.entryPrice));
      const hi = round(sign * (c.high - t.entryPrice));
      const lo = round(sign * (c.low - t.entryPrice));
      const key = t.ref || `${t.entryTime}`;
      const seen = best.get(key) ?? { best: -Infinity, worst: Infinity };
      seen.best = Math.max(seen.best, hi, lo);
      seen.worst = Math.min(seen.worst, hi, lo);
      best.set(key, seen);
      const rs = (p: number) => (t.qty != null ? round(p * t.qty) : null);
      out.push({
        key: `${c.time}-${key}`,
        ...base,
        tradeRef: t.ref,
        side: t.direction === "LONG" ? "Long" : "Short",
        stage: isEntry && isExit ? "Entry + exit" : isEntry ? "Entry" : isExit ? "Exit" : t.exitTime == null && c.time === last ? "Open" : "Holding",
        qty: t.qty,
        entryPrice: t.entryPrice,
        exitPrice: isExit ? t.exitPrice : null,
        exitReason: isExit ? t.exitReason : "",
        pnlPts: pts,
        pnlRs: rs(pts),
        highPts: hi,
        highRs: rs(hi),
        lowPts: lo,
        lowRs: rs(lo),
        bestPts: seen.best,
        bestRs: rs(seen.best),
        worstPts: seen.worst,
        worstRs: rs(seen.worst),
        tradeNet: isExit ? t.net : null,
      });
    }
  });
  return out;
}

// ---- columns ------------------------------------------------------------------------------

export type ColumnKind = "num" | "text" | "time";
export type Column = {
  id: string;
  label: string;
  kind: ColumnKind;
  get: (r: TableRow) => number | string | null;
  digits?: number;
  money?: boolean;
  signed?: boolean;
  /** Shown before the user changes the column set. */
  visible: boolean;
  title?: string;
};

export const COLUMNS: Column[] = [
  { id: "time", label: "Time (IST)", kind: "time", get: (r) => r.time, visible: true },
  { id: "open", label: "Open", kind: "num", get: (r) => r.open, digits: 2, visible: true },
  { id: "high", label: "High", kind: "num", get: (r) => r.high, digits: 2, visible: true },
  { id: "low", label: "Low", kind: "num", get: (r) => r.low, digits: 2, visible: true },
  { id: "close", label: "Close", kind: "num", get: (r) => r.close, digits: 2, visible: true },
  { id: "change", label: "Chg", kind: "num", get: (r) => r.change, digits: 2, signed: true, visible: false, title: "Close minus the previous close" },
  { id: "changePct", label: "Chg %", kind: "num", get: (r) => r.changePct, digits: 3, signed: true, visible: true },
  { id: "volume", label: "Volume", kind: "num", get: (r) => r.volume, digits: 0, visible: true },
  { id: "sma9", label: "SMA 9", kind: "num", get: (r) => r.sma9, digits: 2, visible: true },
  { id: "sma21", label: "SMA 21", kind: "num", get: (r) => r.sma21, digits: 2, visible: true },
  { id: "gapPct", label: "SMA gap %", kind: "num", get: (r) => r.gapPct, digits: 3, signed: true, visible: true, title: "(SMA 9 − SMA 21) ÷ SMA 21 × 100" },
  { id: "gapChange", label: "Gap Δ", kind: "num", get: (r) => r.gapChange, digits: 3, signed: true, visible: false, title: "Change of the SMA gap % since the previous candle (widening or narrowing)" },
  { id: "cross", label: "Cross", kind: "text", get: (r) => r.cross, visible: true, title: "SMA 9 crossed SMA 21 on this candle" },
  { id: "vwap", label: "VWAP", kind: "num", get: (r) => r.vwap, digits: 2, visible: true },
  { id: "vsVwapPct", label: "vs VWAP %", kind: "num", get: (r) => r.vsVwapPct, digits: 3, signed: true, visible: false, title: "Close against the session VWAP" },
  { id: "rsi", label: "RSI 14", kind: "num", get: (r) => r.rsi, digits: 1, visible: true },
  { id: "atr", label: "ATR 14", kind: "num", get: (r) => r.atr, digits: 2, visible: false },
  { id: "pattern", label: "Candle", kind: "text", get: (r) => r.pattern, visible: true, title: "Candlestick pattern name of this candle" },
  { id: "tradeRef", label: "Trade ID", kind: "text", get: (r) => r.tradeRef, visible: true },
  { id: "side", label: "Side", kind: "text", get: (r) => r.side, visible: true },
  { id: "stage", label: "Stage", kind: "text", get: (r) => r.stage, visible: true, title: "Entry, holding or exit candle of the trade" },
  { id: "qty", label: "Qty", kind: "num", get: (r) => r.qty, digits: 0, visible: false },
  { id: "entryPrice", label: "Entry", kind: "num", get: (r) => r.entryPrice, digits: 2, visible: true },
  { id: "exitPrice", label: "Exit", kind: "num", get: (r) => r.exitPrice, digits: 2, visible: true },
  { id: "pnlPts", label: "P&L pts", kind: "num", get: (r) => r.pnlPts, digits: 2, signed: true, visible: true, title: "From the entry to this candle's close (the exit price on the exit candle)" },
  { id: "pnlRs", label: "P&L ₹", kind: "num", get: (r) => r.pnlRs, digits: 2, money: true, visible: true, title: "P&L pts × quantity, before charges" },
  { id: "highRs", label: "P&L @ high ₹", kind: "num", get: (r) => r.highRs, digits: 2, money: true, visible: true, title: "If closed at this candle's high" },
  { id: "lowRs", label: "P&L @ low ₹", kind: "num", get: (r) => r.lowRs, digits: 2, money: true, visible: true, title: "If closed at this candle's low" },
  { id: "highPts", label: "@ high pts", kind: "num", get: (r) => r.highPts, digits: 2, signed: true, visible: false },
  { id: "lowPts", label: "@ low pts", kind: "num", get: (r) => r.lowPts, digits: 2, signed: true, visible: false },
  { id: "bestRs", label: "Best so far ₹", kind: "num", get: (r) => r.bestRs, digits: 2, money: true, visible: false, title: "Best price since the entry (max high for a buy, min low for a sell)" },
  { id: "worstRs", label: "Worst so far ₹", kind: "num", get: (r) => r.worstRs, digits: 2, money: true, visible: false, title: "Worst price since the entry" },
  { id: "exitReason", label: "Exit reason", kind: "text", get: (r) => r.exitReason, visible: false },
  { id: "tradeNet", label: "Trade P&L ₹", kind: "num", get: (r) => r.tradeNet, digits: 2, money: true, visible: false, title: "The trade's P&L before charges, on its exit candle" },
];

export const COLUMN_BY_ID = new Map(COLUMNS.map((c) => [c.id, c]));

export function istStampOf(sec: number): string {
  return new Date(sec * 1000).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** The cell as text on screen and in the PDF. */
export function cellText(col: Column, r: TableRow): string {
  const v = col.get(r);
  if (v == null || v === "") return col.kind === "text" ? "" : "—";
  if (col.kind === "time") return istStampOf(Number(v));
  if (typeof v === "string") return v;
  const digits = col.digits ?? 2;
  const body = Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const sign = v < 0 ? "-" : (col.signed || col.money) && v > 0 ? "+" : "";
  return col.money ? `${sign}₹${body}` : `${sign}${body}`;
}

// ---- filters and sorting --------------------------------------------------------------------

/**
 * A column filter: `>0.05`, `<= 30`, `10..20` (inclusive), `=12`, `!=Exit`, or text
 * that the cell must contain. Empty passes everything.
 */
export function matchesFilter(col: Column, r: TableRow, raw: string): boolean {
  const text = raw.trim();
  if (!text) return true;
  const value = col.get(r);
  const range = text.match(/^(-?\d+(?:\.\d+)?)\s*\.\.\s*(-?\d+(?:\.\d+)?)$/);
  const op = text.match(/^(>=|<=|!=|>|<|=)\s*(.+)$/);
  if (col.kind !== "text" && (range || op)) {
    if (value == null || value === "") return false;
    const num = Number(value);
    if (range) return num >= Number(range[1]) && num <= Number(range[2]);
    const target = Number(op![2]);
    if (Number.isNaN(target)) return false;
    switch (op![1]) {
      case ">":
        return num > target;
      case "<":
        return num < target;
      case ">=":
        return num >= target;
      case "<=":
        return num <= target;
      case "=":
        return num === target;
      default:
        return num !== target;
    }
  }
  const shown = cellText(col, r).toLowerCase();
  if (op && op[1] === "!=") return !shown.includes(op[2].trim().toLowerCase());
  if (op && op[1] === "=") return shown === op[2].trim().toLowerCase();
  return shown.includes(text.toLowerCase());
}

export type Quick = "all" | "trade" | "stages" | "cross" | "pattern";
export const QUICK: { id: Quick; label: string }[] = [
  { id: "all", label: "All candles" },
  { id: "trade", label: "In a trade" },
  { id: "stages", label: "Entries and exits" },
  { id: "cross", label: "SMA crosses" },
  { id: "pattern", label: "Named patterns" },
];

const PLAIN = /^(long |small )?(bullish|bearish|flat)$/i;

export function quickMatch(q: Quick, r: TableRow): boolean {
  if (q === "trade") return r.tradeRef !== "" || r.stage !== "";
  if (q === "stages") return r.stage.includes("Entry") || r.stage.includes("Exit");
  if (q === "cross") return r.cross !== "";
  if (q === "pattern") return !PLAIN.test(r.pattern);
  return true;
}

export type Sort = { id: string; dir: "asc" | "desc" } | null;

export function sortRows(rows: TableRow[], sort: Sort): TableRow[] {
  if (!sort) return rows;
  const col = COLUMN_BY_ID.get(sort.id);
  if (!col) return rows;
  const mul = sort.dir === "asc" ? 1 : -1;
  return rows
    .map((r, i) => ({ r, i, v: col.get(r) }))
    .sort((a, b) => {
      const av = a.v;
      const bv = b.v;
      const aEmpty = av == null || av === "";
      const bEmpty = bv == null || bv === "";
      // Blanks always last, whichever way the column sorts.
      if (aEmpty || bEmpty) return aEmpty && bEmpty ? a.i - b.i : aEmpty ? 1 : -1;
      const diff = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return diff * mul || a.i - b.i;
    })
    .map((x) => x.r);
}

// ---- export ---------------------------------------------------------------------------------

/** 2026-09-29 10:13 in IST: a date spreadsheets read, with the year. */
function csvTime(sec: number): string {
  const d = new Date((sec + 19_800) * 1000);
  return d.toISOString().slice(0, 16).replace("T", " ");
}

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Raw values (no ₹ or thousands commas), so a spreadsheet reads them as numbers. */
export function toCsv(cols: Column[], rows: TableRow[]): string {
  const head = cols.map((c) => csvCell(c.label.replace(/₹/g, "Rs"))).join(",");
  const body = rows.map((r) =>
    cols
      .map((c) => {
        const v = c.get(r);
        if (v == null) return "";
        if (c.kind === "time") return csvTime(Number(v));
        if (typeof v === "number") return String(c.digits != null ? round(v, Math.max(c.digits, 2)) : v);
        return csvCell(v);
      })
      .join(",")
  );
  return [head, ...body].join("\n");
}

export function saveFile(name: string, data: BlobPart, type: string): void {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function toPdf(title: string, subtitle: string, cols: Column[], rows: TableRow[], fileName: string): Promise<void> {
  const [{ jsPDF }, { autoTable }] = await Promise.all([import("jspdf"), import("jspdf-autotable")]);
  // Wide tables get a bigger page so the numbers stay readable.
  const format = cols.length > 16 ? "a3" : "a4";
  const doc = new jsPDF({ orientation: "landscape", unit: "pt", format });
  const margin = 28;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(14);
  doc.text(pdfText(title), margin, 34);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(100, 116, 139);
  doc.text(pdfText(subtitle), margin, 50);
  const tone = (col: Column, r: TableRow): [number, number, number] | undefined => {
    if (!(col.money || col.signed)) return undefined;
    const v = col.get(r);
    if (typeof v !== "number" || v === 0) return undefined;
    return v > 0 ? [4, 120, 87] : [190, 18, 60];
  };
  autoTable(doc, {
    startY: 60,
    margin: { left: margin, right: margin },
    theme: "striped",
    styles: { fontSize: cols.length > 20 ? 6 : 7, cellPadding: 2 },
    headStyles: { fillColor: [30, 41, 59], fontSize: cols.length > 20 ? 6 : 7 },
    head: [cols.map((c) => pdfText(c.label))],
    body: rows.map((r) => cols.map((c) => pdfText(cellText(c, r)))),
    didParseCell: (data) => {
      if (data.section !== "body") return;
      const col = cols[data.column.index];
      const row = rows[data.row.index];
      const color = col && row ? tone(col, row) : undefined;
      if (color) data.cell.styles.textColor = color;
      if (col?.kind === "num") data.cell.styles.halign = "right";
    },
  });
  doc.save(fileName);
}
