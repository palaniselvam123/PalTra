/**
 * One backtest (replay run) as a PDF: settings, totals, day-wise P&L and
 * every trade. Built in the browser; jsPDF loads only when asked for.
 */
import { istStamp } from "@/lib/format";
import { pnlAtPrice, type ReplayRun, type TradeRow } from "@/lib/smaApi";

/** The PDF's built-in font has no ₹, ≥ or arrows; spell them out. */
export function pdfText(value: string): string {
  return value
    .replace(/₹\s?/g, "Rs ")
    .replace(/≥/g, ">=")
    .replace(/≤/g, "<=")
    .replace(/→/g, "to")
    .replace(/↑/g, " up")
    .replace(/↓/g, " down")
    .replace(/—/g, "-")
    .replace(/−/g, "-");
}

export function pdfMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}Rs ${Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function price(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function signedPts(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

function day(iso: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", {
    timeZone: "UTC",
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

const GREEN: [number, number, number] = [4, 120, 87];
const RED: [number, number, number] = [190, 18, 60];
const INK: [number, number, number] = [15, 23, 42];
const MUTED: [number, number, number] = [100, 116, 139];

export type BacktestPdfInput = {
  run: ReplayRun;
  trades: TradeRow[];
  strategy: string;
  settings: [string, string][];
  reasons: Record<string, string>;
};

export async function downloadBacktestPdf({ run, trades, strategy, settings, reasons }: BacktestPdfInput): Promise<void> {
  const [{ jsPDF }, { autoTable }] = await Promise.all([import("jspdf"), import("jspdf-autotable")]);
  const doc = new jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
  const width = doc.internal.pageSize.getWidth();
  const margin = 36;
  const t = run.totals;
  const tone = (value: number | null | undefined) => (value == null || value === 0 ? INK : value > 0 ? GREEN : RED);
  const after = () => (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY;

  // What the run would have made had every trade closed at its max high, its
  // max low, its best price or its worst price (before charges).
  const ranged = trades.filter((r) => r.max_high != null && r.max_low != null);
  const sum = (pick: (r: TradeRow) => number | null) => ranged.reduce((acc, r) => acc + (pick(r) ?? 0), 0);
  const atHigh = sum((r) => pnlAtPrice(r, r.max_high));
  const atLow = sum((r) => pnlAtPrice(r, r.max_low));
  const best = sum((r) => Math.max(pnlAtPrice(r, r.max_high) ?? 0, pnlAtPrice(r, r.max_low) ?? 0));
  const worst = sum((r) => Math.min(pnlAtPrice(r, r.max_high) ?? 0, pnlAtPrice(r, r.max_low) ?? 0));
  const rangeNote = ranged.length === trades.length ? "" : ` (${ranged.length} of ${trades.length} trades have a range)`;
  const rangeRows: [string, string][] = ranged.length
    ? [
        [`All closed at max high, before charges${rangeNote}`, pdfMoney(atHigh)],
        ["All closed at max low", pdfMoney(atLow)],
        ["Best case (each at its best price)", pdfMoney(best)],
        ["Worst case (each at its worst price)", pdfMoney(worst)],
      ]
    : [["Max high / low amounts", "not recorded for these trades"]];
  const rangeTone: Record<string, number> = {
    [`All closed at max high, before charges${rangeNote}`]: atHigh,
    "All closed at max low": atLow,
    "Best case (each at its best price)": best,
    "Worst case (each at its worst price)": worst,
  };

  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  doc.setTextColor(...INK);
  doc.text(`Backtest #${run.id}`, margin, 48);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(10);
  doc.setTextColor(...MUTED);
  const range = run.end_date !== run.start_date ? `${day(run.start_date)} to ${day(run.end_date)}` : day(run.start_date);
  doc.text(
    pdfText(`${range} · from ${run.start_time} · ${run.symbols.join(", ")} · ${run.status.toLowerCase()} · ${run.days_done}/${run.days_total} days`),
    margin,
    66
  );
  doc.text(
    pdfText(
      `Practice replay on Groww 1-minute candles. No order was sent. Generated ${new Date().toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
      })} IST.`
    ),
    margin,
    80
  );
  doc.setTextColor(...INK);
  doc.setFont("helvetica", "bold");
  doc.text(pdfText(strategy), margin, 98, { maxWidth: width - margin * 2 });

  // Totals, then the settings the run used, side by side.
  autoTable(doc, {
    startY: 112,
    margin: { left: margin, right: width / 2 + 6 },
    theme: "grid",
    head: [["Result", ""]],
    body: [
      ["Trades", `${t.trades} (${t.wins} won, ${t.losses} lost)`],
      ["Win rate", `${t.win_rate.toFixed(1)}%`],
      ["Total profit", pdfMoney(t.profit)],
      ["Total loss", pdfMoney(t.loss)],
      ["Net P&L (after charges)", pdfMoney(t.net)],
      ["Max drawdown", pdfMoney(t.max_drawdown)],
      ["Green / red days", `${t.green_days} / ${t.red_days}`],
      ...rangeRows,
    ],
    styles: { fontSize: 9, cellPadding: 4 },
    headStyles: { fillColor: [30, 41, 59] },
    columnStyles: { 0: { fontStyle: "bold", cellWidth: 150 } },
    didParseCell: (cell) => {
      if (cell.section !== "body" || cell.column.index !== 1) return;
      const label = String(cell.row.raw instanceof Array ? cell.row.raw[0] : "");
      if (label.startsWith("Net")) cell.cell.styles.textColor = tone(t.net);
      if (label === "Total profit") cell.cell.styles.textColor = GREEN;
      if (label === "Total loss" || label === "Max drawdown") cell.cell.styles.textColor = RED;
      if (label in rangeTone) cell.cell.styles.textColor = tone(rangeTone[label]);
    },
  });
  const resultEnd = after();
  autoTable(doc, {
    startY: 112,
    margin: { left: width / 2 + 6, right: margin },
    theme: "grid",
    head: [["Setting", "Value"]],
    body: settings.map(([k, v]) => [k, pdfText(v)]),
    styles: { fontSize: 9, cellPadding: 4 },
    headStyles: { fillColor: [30, 41, 59] },
    columnStyles: { 0: { fontStyle: "bold", cellWidth: 130 } },
  });
  let y = Math.max(resultEnd, after()) + 22;

  const stocks = run.stocks ?? [];
  if (stocks.length > 1) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(12);
    doc.text("By stock", margin, y);
    autoTable(doc, {
      startY: y + 6,
      margin: { left: margin, right: margin },
      theme: "striped",
      head: [["Stock", "Trades", "Won", "Lost", "Win %", "Profit", "Loss", "Gross", "Charges", "Net", "Max DD"]],
      body: stocks.map((s) => [
        s.symbol,
        s.totals.trades,
        s.totals.wins,
        s.totals.losses,
        s.totals.trades ? `${s.totals.win_rate.toFixed(1)}%` : "-",
        pdfMoney(s.totals.profit),
        pdfMoney(s.totals.loss),
        pdfMoney(s.totals.gross),
        pdfMoney(s.totals.charges).replace("+", ""),
        pdfMoney(s.totals.net),
        pdfMoney(s.totals.max_drawdown),
      ]),
      styles: { fontSize: 8.5, cellPadding: 3.5 },
      headStyles: { fillColor: [30, 41, 59] },
      columnStyles: Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((i) => [i, { halign: "right" as const }])),
      didParseCell: (cell) => {
        if (cell.section === "head" && cell.column.index > 0) cell.cell.styles.halign = "right";
        if (cell.section !== "body") return;
        const s = stocks[cell.row.index];
        if (cell.column.index === 0) cell.cell.styles.fontStyle = "bold";
        if (cell.column.index === 9) cell.cell.styles.textColor = tone(s.totals.net);
        if (cell.column.index === 5) cell.cell.styles.textColor = GREEN;
        if (cell.column.index === 6) cell.cell.styles.textColor = RED;
      },
    });
    y = after() + 22;
  }

  const days = run.days ?? [];
  doc.setFont("helvetica", "bold");
  doc.setFontSize(12);
  doc.text("Day-wise P&L", margin, y);
  autoTable(doc, {
    startY: y + 6,
    margin: { left: margin, right: margin },
    theme: "striped",
    head: [["Date", "Trades", "Won", "Lost", "Profit", "Loss", "Gross", "Charges", "Net", "Cumulative"]],
    body: days.length
      ? days.map((d) => [
          day(d.date),
          d.trades,
          d.wins,
          d.losses,
          pdfMoney(d.profit),
          pdfMoney(d.loss),
          pdfMoney(d.gross),
          pdfMoney(d.charges).replace("+", ""),
          pdfMoney(d.net),
          pdfMoney(d.cumulative),
        ])
      : [[{ content: "No closed trades yet.", colSpan: 10 }]],
    styles: { fontSize: 8.5, cellPadding: 3.5 },
    headStyles: { fillColor: [30, 41, 59] },
    columnStyles: Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => [i, { halign: "right" as const }])),
    didParseCell: (cell) => {
      if (cell.section === "head" && cell.column.index > 0) cell.cell.styles.halign = "right";
      if (cell.section !== "body" || !days.length) return;
      const d = days[cell.row.index];
      if (cell.column.index === 8) cell.cell.styles.textColor = tone(d.net);
      if (cell.column.index === 9) cell.cell.styles.textColor = tone(d.cumulative);
      if (cell.column.index === 4) cell.cell.styles.textColor = GREEN;
      if (cell.column.index === 5) cell.cell.styles.textColor = RED;
    },
  });
  y = after() + 22;

  const rows = [...trades].sort((a, b) => a.id - b.id);
  if (y > doc.internal.pageSize.getHeight() - 80) {
    doc.addPage();
    y = 48;
  }
  doc.setFont("helvetica", "bold");
  doc.setFontSize(12);
  doc.text(`Trades (${rows.length})`, margin, y);
  autoTable(doc, {
    startY: y + 6,
    margin: { left: margin, right: margin },
    theme: "striped",
    head: [["#", "Stock", "Side", "Qty", "Entry time", "Entry", "Exit time", "Exit", "Max high (P&L there)", "Max low (P&L there)", "Points", "Exit reason", "Charges", "Net P&L"]],
    body: rows.length
      ? rows.map((r) => [
          r.id,
          r.symbol,
          r.direction === "LONG" ? "Long" : r.direction === "SHORT" ? "Short" : r.direction,
          r.qty,
          pdfText(istStamp(r.entry_time)),
          price(r.entry_price),
          pdfText(r.exit_time ? istStamp(r.exit_time) : "open"),
          price(r.exit_price),
          r.max_high == null ? "-" : `${price(r.max_high)}\n${pdfMoney(pnlAtPrice(r, r.max_high))}`,
          r.max_low == null ? "-" : `${price(r.max_low)}\n${pdfMoney(pnlAtPrice(r, r.max_low))}`,
          signedPts(r.points),
          r.exit_price == null ? "Open" : reasons[r.exit_reason || ""] ?? (r.exit_reason || "Closed"),
          r.brokerage_and_taxes == null ? "-" : pdfMoney(r.brokerage_and_taxes).replace("+", ""),
          pdfMoney(r.net_pnl),
        ])
      : [[{ content: "This run has no trades in the loaded book.", colSpan: 14 }]],
    styles: { fontSize: 7.5, cellPadding: 3 },
    headStyles: { fillColor: [30, 41, 59] },
    columnStyles: Object.fromEntries([0, 3, 5, 7, 8, 9, 10, 12, 13].map((i) => [i, { halign: "right" as const }])),
    didParseCell: (cell) => {
      if (cell.section === "head" && [0, 3, 5, 7, 8, 9, 10, 12, 13].includes(cell.column.index)) cell.cell.styles.halign = "right";
      if (cell.section !== "body" || !rows.length) return;
      const r = rows[cell.row.index];
      if (cell.column.index === 13 || cell.column.index === 2) cell.cell.styles.textColor = tone(r.net_pnl);
      if (cell.column.index === 10) cell.cell.styles.textColor = tone(r.points);
      if (cell.column.index === 8) cell.cell.styles.textColor = tone(pnlAtPrice(r, r.max_high));
      if (cell.column.index === 9) cell.cell.styles.textColor = tone(pnlAtPrice(r, r.max_low));
    },
  });

  const pages = doc.getNumberOfPages();
  for (let i = 1; i <= pages; i += 1) {
    doc.setPage(i);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(...MUTED);
    doc.text(`PalTra · Backtest #${run.id} · page ${i} of ${pages}`, margin, doc.internal.pageSize.getHeight() - 18);
  }
  doc.save(`backtest-${run.id}-${run.start_date}${run.end_date !== run.start_date ? `_to_${run.end_date}` : ""}.pdf`);
}
