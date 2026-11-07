// Shared pieces for the Scalp page lesson PDFs: the page CSS, small HTML helpers and drawn figures (SVG).
// Every lesson module exports { id, file, title, pages?, html() }. build.js renders them with Playwright.

const CSS = `
  @page { size: A4; margin: 14mm 14mm 16mm 14mm; }
  :root {
    --ink: #0f172a; --soft: #475569; --mute: #64748b; --line: #e2e8f0; --card: #f8fafc;
    --green: #059669; --green-bg: #ecfdf5; --red: #dc2626; --red-bg: #fef2f2; --blue: #0284c7; --blue-bg: #f0f9ff;
    --amber: #b45309; --amber-bg: #fffbeb; --violet: #7c3aed; --violet-bg: #f5f3ff;
  }
  * { box-sizing: border-box; }
  body { font-family: "Segoe UI", Inter, Roboto, Arial, sans-serif; color: var(--ink); font-size: 10.5pt; line-height: 1.45; margin: 0; }
  h1 { font-size: 21pt; margin: 0 0 4px; letter-spacing: -0.01em; }
  h2 { font-size: 14pt; margin: 18px 0 6px; padding-bottom: 4px; border-bottom: 2px solid var(--line); break-after: avoid; }
  h3 { font-size: 11.5pt; margin: 12px 0 4px; break-after: avoid; }
  p { margin: 4px 0 8px; }
  .lead { color: var(--soft); font-size: 11pt; margin-bottom: 10px; }
  .tag { display: inline-block; font-size: 8pt; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--blue); }
  .box { border: 1px solid var(--line); background: var(--card); border-radius: 8px; padding: 9px 12px; margin: 8px 0; break-inside: avoid; }
  .box.green { background: var(--green-bg); border-color: #a7f3d0; }
  .box.amber { background: var(--amber-bg); border-color: #fde68a; }
  .box.blue { background: var(--blue-bg); border-color: #bae6fd; }
  .box.red { background: var(--red-bg); border-color: #fecaca; }
  .box.violet { background: var(--violet-bg); border-color: #ddd6fe; }
  .box b.t { display: block; margin-bottom: 2px; }
  table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 9.5pt; break-inside: avoid; }
  th, td { border-bottom: 1px solid var(--line); padding: 5px 7px; text-align: left; vertical-align: top; }
  th { background: #f1f5f9; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .04em; color: var(--soft); }
  td.n, th.n { text-align: right; font-family: Consolas, "Courier New", monospace; white-space: nowrap; }
  .g { color: var(--green); font-weight: 600; } .r { color: var(--red); font-weight: 600; } .m { color: var(--mute); } .b { color: var(--blue); font-weight: 600; } .a { color: var(--amber); font-weight: 600; }
  .pill { display: inline-block; border-radius: 4px; padding: 0 6px; font-size: 8.5pt; font-weight: 700; }
  .p-green { background: #d1fae5; color: #047857; } .p-blue { background: #e0f2fe; color: #0369a1; }
  .p-grey { background: #e2e8f0; color: #334155; } .p-red { background: #fee2e2; color: #b91c1c; } .p-amber { background: #fef3c7; color: #92400e; }
  .col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .col3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
  .colcard { border: 1px solid var(--line); border-radius: 8px; padding: 8px 11px; break-inside: avoid; }
  .colcard h3 { margin-top: 0; }
  .colcard .ex { background: var(--card); border-radius: 6px; padding: 5px 8px; margin-top: 4px; font-size: 9.5pt; }
  .num { display: inline-block; width: 20px; height: 20px; border-radius: 50%; background: var(--blue); color: #fff; text-align: center; font-weight: 700; font-size: 9pt; line-height: 20px; margin-right: 6px; }
  .steps { margin: 6px 0 10px; padding: 0; list-style: none; }
  .steps li { display: flex; gap: 8px; margin: 5px 0; break-inside: avoid; }
  .steps li .num { flex: none; margin-top: 2px; }
  .page { break-before: page; }
  svg text { font-family: "Segoe UI", Inter, Arial, sans-serif; }
  figure { margin: 8px 0 10px; break-inside: avoid; }
  figcaption { font-size: 9pt; color: var(--soft); margin-top: 3px; }
  ul { margin: 4px 0 8px 18px; padding: 0; } li { margin: 2px 0; }
  .words dt { font-weight: 700; margin-top: 6px; } .words dd { margin: 1px 0 0 0; color: var(--soft); }
  .words dd .ex { color: var(--blue); }
  .foot { color: var(--mute); font-size: 8.5pt; margin-top: 14px; border-top: 1px solid var(--line); padding-top: 6px; }
  .screen { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #fff; font-size: 9pt; break-inside: avoid; }
  .screen .bar { background: #f1f5f9; padding: 5px 9px; font-weight: 700; color: var(--soft); font-size: 8.5pt; text-transform: uppercase; letter-spacing: .04em; }
  .screen .row { display: flex; gap: 8px; padding: 4px 9px; border-top: 1px solid var(--line); align-items: baseline; }
  .screen .row span { white-space: nowrap; }
  .screen .btn { display: inline-block; border: 1px solid #cbd5e1; border-radius: 5px; padding: 1px 7px; font-weight: 600; font-size: 8.5pt; background: #fff; }
  .screen .btn.p { background: var(--blue); border-color: var(--blue); color: #fff; }
  .screen .chip { display: inline-block; border-radius: 10px; padding: 0 7px; font-size: 8.5pt; background: #e2e8f0; }
  .screen .chip.on { background: #dbeafe; color: #1d4ed8; font-weight: 700; }
`;

const esc = (s) => String(s).replace(/&(?!amp;|lt;|gt;|quot;|#)/g, "&amp;");

function page({ title, lead, kicker = "PalTra · Learn · Scalp page", body }) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${esc(title)}</title><style>${CSS}</style></head>
<body><span class="tag">${kicker}</span><h1>${title}</h1><p class="lead">${lead}</p>${body}
<div class="foot">Practice money and candles only: nothing on the Scalp page places an order. The bots trade only the stocks you arm, in PAPER (practice) mode unless you switch one to LIVE yourself.</div>
</body></html>`;
}

const box = (kind, title, body) => `<div class="box ${kind}">${title ? `<b class="t">${title}</b>` : ""}${body}</div>`;
const steps = (items) => `<ol class="steps">${items.map((t, i) => `<li><span class="num">${i + 1}</span><div>${t}</div></li>`).join("")}</ol>`;
const table = (head, rows) => `<table><thead><tr>${head.map((h) => `<th${/^[-+₹\d]/.test(String(h)) || h.endsWith("#") ? ' class="n"' : ""}>${h}</th>`).join("")}</tr></thead><tbody>${rows
  .map((r) => `<tr>${r.map((c) => (typeof c === "object" && c !== null ? `<td class="${c.cls || ""}">${c.v}</td>` : `<td${/^[-+₹\d%.,×]+$|^[-+]?₹?[\d.,]+[%×]?$/.test(String(c)) ? ' class="n"' : ""}>${c}</td>`)).join("")}</tr>`)
  .join("")}</tbody></table>`;
const words = (items) => `<dl class="words">${items.map(([t, m, ex]) => `<dt>${t}</dt><dd>${m}${ex ? ` <span class="ex">Example: ${ex}</span>` : ""}</dd>`).join("")}</dl>`;
const cols = (cards, n = 2) => `<div class="${n === 3 ? "col3" : "col"}">${cards.map(([h, b]) => `<div class="colcard"><h3>${h}</h3>${b}</div>`).join("")}</div>`;
const fig = (svg, caption) => `<figure>${svg}${caption ? `<figcaption>${caption}</figcaption>` : ""}</figure>`;

/** A mock of a screen card: title bar, then rows of controls / cells. */
const screen = (bar, rows) => `<div class="screen"><div class="bar">${bar}</div>${rows.map((r) => `<div class="row">${r}</div>`).join("")}</div>`;
const btn = (t, primary) => `<span class="btn${primary ? " p" : ""}">${t}</span>`;
const chip = (t, on) => `<span class="chip${on ? " on" : ""}">${t}</span>`;

// ---- drawn figures -------------------------------------------------------------------------------

/**
 * A candle chart. candles: [{o,h,l,c}], optional lines {sma9:[], sma21:[], vwap:[]} (values per candle, null = skip),
 * marks: [{i, text, color, y?: "above"|"below"}], levels: [{v, text, color, dash}], width/height in px.
 */
function candles({ candles: cs, lines = {}, marks = [], levels = [], width = 720, height = 220, xLabels = [], title = "" }) {
  const padL = 40, padR = 70, padT = 18, padB = 30;
  const n = cs.length;
  const all = [];
  cs.forEach((c) => all.push(c.h, c.l));
  Object.values(lines).forEach((arr) => arr.forEach((v) => v != null && all.push(v)));
  levels.forEach((l) => all.push(l.v));
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = hi - lo || 1;
  const x = (i) => padL + ((i + 0.5) / n) * (width - padL - padR);
  const y = (v) => padT + (1 - (v - lo) / span) * (height - padT - padB);
  const cw = Math.max(3, ((width - padL - padR) / n) * 0.55);
  let out = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(title || "Candle chart")}">`;
  // y grid
  for (let k = 0; k <= 4; k++) {
    const v = lo + (span * k) / 4;
    out += `<line x1="${padL}" x2="${width - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="#eef2f7"/>`;
    out += `<text x="${padL - 4}" y="${(y(v) + 3).toFixed(1)}" font-size="9" fill="#64748b" text-anchor="end">${v.toFixed(v > 100 ? 0 : 1)}</text>`;
  }
  cs.forEach((c, i) => {
    const up = c.c >= c.o;
    const col = up ? "#059669" : "#dc2626";
    out += `<line x1="${x(i).toFixed(1)}" x2="${x(i).toFixed(1)}" y1="${y(c.h).toFixed(1)}" y2="${y(c.l).toFixed(1)}" stroke="${col}" stroke-width="1.2"/>`;
    const top = y(Math.max(c.o, c.c)), bot = y(Math.min(c.o, c.c));
    out += `<rect x="${(x(i) - cw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${cw.toFixed(1)}" height="${Math.max(1.2, bot - top).toFixed(1)}" fill="${col}"/>`;
  });
  const lineColor = { sma9: "#d6455d", sma21: "#2f6fd1", vwap: "#0891b2", ema: "#7c3aed" };
  const usedLabelYs = [];
  Object.entries(lines).forEach(([name, arr]) => {
    const pts = arr.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean);
    if (pts.length > 1) out += `<polyline points="${pts.join(" ")}" fill="none" stroke="${lineColor[name] || "#64748b"}" stroke-width="1.8"${name === "vwap" ? ' stroke-dasharray="4 3"' : ""}/>`;
    const last = arr.map((v, i) => [v, i]).filter(([v]) => v != null).pop();
    if (last) {
      let ly = y(last[0]) + 3;
      while (usedLabelYs.some((u) => Math.abs(u - ly) < 11)) ly += 11;
      usedLabelYs.push(ly);
      out += `<text x="${width - padR + 4}" y="${ly.toFixed(1)}" font-size="9" fill="${lineColor[name] || "#64748b"}" font-weight="700">${name === "sma9" ? "SMA 9" : name === "sma21" ? "SMA 21" : name.toUpperCase()}</text>`;
    }
  });
  levels.forEach((l) => {
    out += `<line x1="${padL}" x2="${width - padR}" y1="${y(l.v).toFixed(1)}" y2="${y(l.v).toFixed(1)}" stroke="${l.color || "#b45309"}" stroke-width="1.3"${l.dash === false ? "" : ' stroke-dasharray="5 3"'}/>`;
    out += `<text x="${width - padR + 4}" y="${(y(l.v) + 3).toFixed(1)}" font-size="9" fill="${l.color || "#b45309"}" font-weight="700">${l.text}</text>`;
  });
  marks.forEach((m) => {
    const c = cs[m.i];
    const above = m.y !== "below";
    const yy = above ? y(c.h) - 8 : y(c.l) + 14;
    out += `<text x="${x(m.i).toFixed(1)}" y="${yy.toFixed(1)}" font-size="9.5" font-weight="700" fill="${m.color || "#0f172a"}" text-anchor="middle">${m.text}</text>`;
    out += `<line x1="${x(m.i).toFixed(1)}" x2="${x(m.i).toFixed(1)}" y1="${(above ? yy + 2 : yy - 10).toFixed(1)}" y2="${(above ? y(c.h) - 1 : y(c.l) + 1).toFixed(1)}" stroke="${m.color || "#0f172a"}" stroke-width="1"/>`;
  });
  xLabels.forEach(([i, t]) => {
    out += `<text x="${x(i).toFixed(1)}" y="${height - 10}" font-size="9" fill="#475569" text-anchor="middle">${t}</text>`;
  });
  if (title) out += `<text x="${padL}" y="12" font-size="10" font-weight="700" fill="#334155">${title}</text>`;
  out += `</svg>`;
  return out;
}

/** Make a candle series from a list of closes (first open = first close). Each candle's range wobbles a little. */
function series(closes, wick = 0.3) {
  const out = [];
  let prev = closes[0];
  closes.forEach((c, i) => {
    const o = i === 0 ? c - (closes[1] - c) * 0.3 : prev;
    const h = Math.max(o, c) + wick * (0.6 + ((i * 7) % 5) / 10);
    const l = Math.min(o, c) - wick * (0.6 + ((i * 3) % 5) / 10);
    out.push({ o, h, l, c });
    prev = c;
  });
  return out;
}

/** Simple moving average of closes with period p (null while forming). */
function sma(closes, p) {
  return closes.map((_, i) => (i + 1 < p ? null : closes.slice(i + 1 - p, i + 1).reduce((a, b) => a + b, 0) / p));
}

/** A horizontal timeline of a trading day with labelled points. Labels stack into rows so close points never overlap. */
function timeline(points, width = 720, note = "") {
  const padL = 30, padR = 30, y = 40, rowH = 28;
  const t2x = (hhmm) => {
    const [h, m] = hhmm.split(":").map(Number);
    const mins = h * 60 + m - (9 * 60 + 15);
    return padL + (mins / 375) * (width - padL - padR);
  };
  // Lay each label in the first row where it does not touch the previous label of that row.
  const rows = [];
  const laid = points.map((p) => {
    const x = t2x(p.at);
    const w = Math.max(p.at.length, p.text.length) * 5.4 + 8;
    const anchor = x - w / 2 < padL ? "start" : x + w / 2 > width - padR ? "end" : "middle";
    const left = anchor === "start" ? x : anchor === "end" ? x - w : x - w / 2;
    let row = 0;
    while (rows[row] != null && rows[row] > left) row++;
    rows[row] = left + w;
    return { ...p, x, anchor, row };
  });
  const nRows = rows.length;
  const h = y + 20 + nRows * rowH + (note ? 18 : 6);
  let out = `<svg viewBox="0 0 ${width} ${h}" width="100%" role="img" aria-label="Timeline of one trading day">`;
  out += `<rect x="${padL}" y="${y - 5}" width="${width - padL - padR}" height="10" rx="5" fill="#e2e8f0"/>`;
  out += `<text x="${padL}" y="${y - 12}" font-size="10" fill="#475569">09:15 open</text><text x="${width - padR}" y="${y - 12}" font-size="10" fill="#475569" text-anchor="end">15:30 close</text>`;
  laid.forEach((p) => {
    const col = p.color || "#0284c7";
    const ty = y + 24 + p.row * rowH;
    if (p.row > 0) out += `<line x1="${p.x.toFixed(1)}" x2="${p.x.toFixed(1)}" y1="${y + 8}" y2="${ty - 10}" stroke="${col}" stroke-width="1" stroke-dasharray="2 2"/>`;
    out += `<circle cx="${p.x.toFixed(1)}" cy="${y}" r="7" fill="${col}"/>`;
    const halo = 'stroke="#fff" stroke-width="3" paint-order="stroke"';
    out += `<text x="${p.x.toFixed(1)}" y="${ty}" font-size="10" font-weight="700" fill="${col}" text-anchor="${p.anchor}" ${halo}>${p.at}</text>`;
    out += `<text x="${p.x.toFixed(1)}" y="${ty + 13}" font-size="9.5" fill="#0f172a" text-anchor="${p.anchor}" ${halo}>${p.text}</text>`;
  });
  if (note) out += `<text x="${padL}" y="${h - 6}" font-size="9.5" fill="#475569">${note}</text>`;
  out += `</svg>`;
  return out;
}

/** Horizontal bars: [{label, value, color, text}], values on one scale. */
function bars(items, { width = 720, max = null, unit = "" } = {}) {
  const m = max ?? Math.max(...items.map((i) => Math.abs(i.value)));
  const rowH = 26, padL = 150, padR = 90;
  let out = `<svg viewBox="0 0 ${width} ${items.length * rowH + 10}" width="100%" role="img" aria-label="Bar chart">`;
  items.forEach((it, i) => {
    const y = 6 + i * rowH;
    const w = (Math.abs(it.value) / (m || 1)) * (width - padL - padR);
    out += `<text x="${padL - 8}" y="${y + 14}" font-size="10" fill="#0f172a" text-anchor="end">${it.label}</text>`;
    out += `<rect x="${padL}" y="${y + 3}" width="${w.toFixed(1)}" height="16" rx="4" fill="${it.color || "#0284c7"}"/>`;
    out += `<text x="${(padL + w + 6).toFixed(1)}" y="${y + 15}" font-size="10" font-weight="700" fill="#0f172a">${it.text ?? `${it.value}${unit}`}</text>`;
  });
  out += `</svg>`;
  return out;
}

module.exports = { page, box, steps, table, words, cols, fig, screen, btn, chip, candles, series, sma, timeline, bars };
