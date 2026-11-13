// Drawn figures (SVG) used only by the strategy-settings lessons (lessons/17-*, 18-*): a flow of boxes with arrows,
// a plain line chart, one big candle with price levels across it, and the map of hand-offs between the bots.

const esc = (s) => String(s).replace(/&(?!amp;|lt;|gt;|quot;|#)/g, "&amp;");
const ARROW = (id, color = "#64748b") =>
  `<defs><marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${color}"/></marker></defs>`;

const TONES = {
  blue: ["#f0f9ff", "#7dd3fc", "#0369a1"],
  green: ["#ecfdf5", "#6ee7b7", "#047857"],
  amber: ["#fffbeb", "#fcd34d", "#92400e"],
  red: ["#fef2f2", "#fca5a5", "#b91c1c"],
  violet: ["#f5f3ff", "#c4b5fd", "#6d28d9"],
  grey: ["#f8fafc", "#cbd5e1", "#334155"],
};

/** A row of boxes joined by arrows. boxes: [{title, lines: [..], tone, tag}]. */
function flow(boxes, { width = 720, boxH = 78, gap = 20, label = "Flow" } = {}) {
  const n = boxes.length;
  const bw = (width - gap * (n - 1)) / n;
  const h = boxH + 18;
  let out = `<svg viewBox="0 0 ${width} ${h}" width="100%" role="img" aria-label="${esc(label)}">${ARROW("fa")}`;
  boxes.forEach((b, i) => {
    const [bg, bd, ink] = TONES[b.tone || "grey"];
    const x = i * (bw + gap);
    out += `<rect x="${x.toFixed(1)}" y="14" width="${bw.toFixed(1)}" height="${boxH}" rx="8" fill="${bg}" stroke="${bd}" stroke-width="1.3"/>`;
    if (b.tag) {
      out += `<rect x="${(x + 8).toFixed(1)}" y="5" width="${(b.tag.length * 5.2 + 12).toFixed(1)}" height="16" rx="8" fill="${ink}"/>`;
      out += `<text x="${(x + 14).toFixed(1)}" y="16.5" font-size="8.5" font-weight="700" fill="#fff">${esc(b.tag)}</text>`;
    }
    out += `<text x="${(x + bw / 2).toFixed(1)}" y="38" font-size="10.5" font-weight="700" fill="${ink}" text-anchor="middle">${esc(b.title)}</text>`;
    (b.lines || []).forEach((t, k) => {
      out += `<text x="${(x + bw / 2).toFixed(1)}" y="${52 + k * 11.5}" font-size="8.6" fill="#334155" text-anchor="middle">${esc(t)}</text>`;
    });
    if (i < n - 1) {
      out += `<line x1="${(x + bw + 3).toFixed(1)}" x2="${(x + bw + gap - 3).toFixed(1)}" y1="${14 + boxH / 2}" y2="${14 + boxH / 2}" stroke="#64748b" stroke-width="1.5" marker-end="url(#fa)"/>`;
    }
  });
  return out + `</svg>`;
}

/**
 * A plain line chart. series: [{values, color, label, dash, width}], levels: [{v, text, color}],
 * marks: [{i, s (series index), text, color, y: "above"|"below"}], bands: [{from, to, color, text}] (x ranges).
 */
function lineChart({ series, levels = [], marks = [], bands = [], width = 720, height = 210, xLabels = [], title = "", fmt = (v) => v.toFixed(2), zero = false }) {
  const padL = 46, padR = 118, padT = 34, padB = 28;
  const n = Math.max(...series.map((s) => s.values.length));
  const all = [];
  series.forEach((s) => s.values.forEach((v) => v != null && all.push(v)));
  levels.forEach((l) => all.push(l.v));
  if (zero) all.push(0);
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo || 1) * 0.08;
  lo -= pad; hi += pad;
  const x = (i) => padL + (i / (n - 1)) * (width - padL - padR);
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (height - padT - padB);
  let out = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(title || "Line chart")}">`;
  bands.forEach((b) => {
    out += `<rect x="${x(b.from).toFixed(1)}" y="${padT}" width="${(x(b.to) - x(b.from)).toFixed(1)}" height="${height - padT - padB}" fill="${b.color || "#ecfdf5"}"/>`;
    if (b.text) out += `<text x="${((x(b.from) + x(b.to)) / 2).toFixed(1)}" y="${height - padB - 5}" font-size="9" font-weight="700" fill="${b.ink || "#047857"}" text-anchor="middle">${esc(b.text)}</text>`;
  });
  for (let k = 0; k <= 4; k++) {
    const v = lo + ((hi - lo) * k) / 4;
    out += `<line x1="${padL}" x2="${width - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="#eef2f7"/>`;
    out += `<text x="${padL - 4}" y="${(y(v) + 3).toFixed(1)}" font-size="9" fill="#64748b" text-anchor="end">${fmt(v)}</text>`;
  }
  if (zero) out += `<line x1="${padL}" x2="${width - padR}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" stroke="#94a3b8" stroke-width="1"/>`;
  levels.forEach((l) => {
    out += `<line x1="${padL}" x2="${width - padR}" y1="${y(l.v).toFixed(1)}" y2="${y(l.v).toFixed(1)}" stroke="${l.color || "#b45309"}" stroke-width="1.3" stroke-dasharray="5 3"/>`;
    out += `<text x="${width - padR + 4}" y="${(y(l.v) + 3).toFixed(1)}" font-size="9" fill="${l.color || "#b45309"}" font-weight="700">${esc(l.text)}</text>`;
  });
  series.forEach((s) => {
    const pts = s.values.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean);
    out += `<polyline points="${pts.join(" ")}" fill="none" stroke="${s.color || "#0284c7"}" stroke-width="${s.width || 2}"${s.dash ? ' stroke-dasharray="4 3"' : ""} stroke-linejoin="round"/>`;
    if (s.dots) s.values.forEach((v, i) => v != null && (out += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.2" fill="${s.color || "#0284c7"}"/>`));
    if (s.label) {
      const last = s.values.map((v, i) => [v, i]).filter(([v]) => v != null).pop();
      out += `<text x="${(x(last[1]) + 5).toFixed(1)}" y="${(y(last[0]) + 3).toFixed(1)}" font-size="9" font-weight="700" fill="${s.color || "#0284c7"}">${esc(s.label)}</text>`;
    }
  });
  marks.forEach((m) => {
    const v = series[m.s || 0].values[m.i];
    const above = m.y !== "below";
    const lift = m.lift || 0;
    const ty = above ? y(v) - 12 - lift : y(v) + 20 + lift;
    const col = m.color || "#0f172a";
    out += `<circle cx="${x(m.i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4.2" fill="#fff" stroke="${col}" stroke-width="2"/>`;
    out += `<line x1="${x(m.i).toFixed(1)}" x2="${x(m.i).toFixed(1)}" y1="${(above ? ty + 3 : ty - 10).toFixed(1)}" y2="${(above ? y(v) - 5 : y(v) + 5).toFixed(1)}" stroke="${col}" stroke-width="1"/>`;
    out += `<text x="${x(m.i).toFixed(1)}" y="${ty.toFixed(1)}" font-size="9.5" font-weight="700" fill="${col}" text-anchor="${m.anchor || "middle"}" stroke="#fff" stroke-width="3" paint-order="stroke">${esc(m.text)}</text>`;
  });
  xLabels.forEach(([i, t]) => {
    out += `<text x="${x(i).toFixed(1)}" y="${height - 8}" font-size="9" fill="#475569" text-anchor="middle">${esc(t)}</text>`;
  });
  if (title) out += `<text x="${padL}" y="12" font-size="10" font-weight="700" fill="#334155">${esc(title)}</text>`;
  return out + `</svg>`;
}

/**
 * One big candle (the signal candle) with price levels drawn across it and a label for each on the right.
 * candle: {o,h,l,c}; levels: [{v, name, note, color}] (any order).
 */
function levelsFig({ candle, levels, width = 720, height = 250, title = "" }) {
  const padT = 26, padB = 16, cx = 70, cw = 46, lineL = 20, lineR = 250;
  const all = [candle.h, candle.l, ...levels.map((l) => l.v)];
  const lo = Math.min(...all) - 0.8, hi = Math.max(...all) + 0.8;
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (height - padT - padB);
  let out = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(title || "Price levels on a candle")}">`;
  if (title) out += `<text x="0" y="12" font-size="10" font-weight="700" fill="#334155">${esc(title)}</text>`;
  const up = candle.c >= candle.o;
  const col = up ? "#059669" : "#dc2626";
  out += `<line x1="${cx}" x2="${cx}" y1="${y(candle.h).toFixed(1)}" y2="${y(candle.l).toFixed(1)}" stroke="${col}" stroke-width="2"/>`;
  const top = y(Math.max(candle.o, candle.c)), bot = y(Math.min(candle.o, candle.c));
  out += `<rect x="${cx - cw / 2}" y="${top.toFixed(1)}" width="${cw}" height="${(bot - top).toFixed(1)}" fill="${col}" opacity="0.85"/>`;
  out += `<text x="${cx}" y="${(height - 2).toFixed(1)}" font-size="9" fill="#475569" text-anchor="middle">signal candle</text>`;
  // Labels spread so they never overlap, each joined to its level by a thin line.
  const sorted = [...levels].sort((a, b) => b.v - a.v);
  let lastY = -100;
  sorted.forEach((l) => {
    const ly = y(l.v);
    const ty = Math.max(ly, lastY + 27);
    lastY = ty;
    const c = l.color || "#0284c7";
    out += `<line x1="${lineL}" x2="${lineR}" y1="${ly.toFixed(1)}" y2="${ly.toFixed(1)}" stroke="${c}" stroke-width="1.4" stroke-dasharray="5 3"/>`;
    out += `<line x1="${lineR}" x2="${lineR + 22}" y1="${ly.toFixed(1)}" y2="${(ty - 3).toFixed(1)}" stroke="${c}" stroke-width="1"/>`;
    out += `<text x="${lineR + 26}" y="${ty.toFixed(1)}" font-size="10" font-weight="700" fill="${c}">₹${l.v.toFixed(2)} · ${esc(l.name)}</text>`;
    if (l.note) out += `<text x="${lineR + 26}" y="${(ty + 11.5).toFixed(1)}" font-size="8.8" fill="#475569">${esc(l.note)}</text>`;
  });
  return out + `</svg>`;
}

/** The hand-offs between the bots: Bot 1 in the middle-left, Bots 2, 3 and 5 on the right, arrows with their reasons. */
function botsMap({ width = 720 } = {}) {
  const h = 318;
  const box = (x, y, w, hh, tone, title, lines) => {
    const [bg, bd, ink] = TONES[tone];
    let s = `<rect x="${x}" y="${y}" width="${w}" height="${hh}" rx="9" fill="${bg}" stroke="${bd}" stroke-width="1.4"/>`;
    s += `<text x="${x + w / 2}" y="${y + 18}" font-size="11" font-weight="700" fill="${ink}" text-anchor="middle">${esc(title)}</text>`;
    lines.forEach((t, k) => (s += `<text x="${x + w / 2}" y="${y + 32 + k * 11.5}" font-size="8.8" fill="#334155" text-anchor="middle">${esc(t)}</text>`));
    return s;
  };
  const arrow = (x1, y1, x2, y2, color, id) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="1.6" marker-end="url(#${id})"/>`;
  const label = (x, y, lines, color) =>
    lines.map((t, k) => `<text x="${x}" y="${y + k * 11}" font-size="8.8" font-weight="${k === 0 ? 700 : 400}" fill="${k === 0 ? color : "#334155"}" stroke="#fff" stroke-width="3" paint-order="stroke">${esc(t)}</text>`).join("");
  let out = `<svg viewBox="0 0 ${width} ${h}" width="100%" role="img" aria-label="Map of hand-offs between the bots">`;
  out += ARROW("ma", "#b45309") + ARROW("mg", "#047857") + ARROW("mv", "#6d28d9") + ARROW("mb", "#64748b");
  out += box(10, 100, 170, 84, "blue", "Bots 1-4 (your bot)", ["trades the stocks you arm", "on its own settings", "(Bot 1 is the main desk)"]);
  out += box(520, 6, 190, 70, "amber", "Bot 2 · the choppy bot", ["flipped, SMA gap mode,", "VWAP + RSI checks"]);
  out += box(520, 110, 190, 70, "green", "Bot 3 · the trend bot", ["its own settings;", "enters on the breakout's side"]);
  out += box(520, 214, 190, 70, "violet", "Bot 5 · the fast-mover bot", ["its own size and stop;", "buys / sells at once"]);
  out += arrow(180, 118, 517, 44, "#b45309", "ma");
  out += label(196, 22, ["It went wrong here → Bot 2", "Recovery hold ended · V-turn exit at a loss ·", "N losing trades today · stop-loss (if picked)"], "#b45309");
  out += arrow(180, 144, 517, 144, "#047857", "mg");
  out += label(196, 136, ["It is trending → Bot 3"], "#047857");
  out += label(196, 158, ["", "A breakout / strong trend (or stop-loss → Bot 3)"], "#047857");
  out += arrow(180, 170, 517, 246, "#6d28d9", "mv");
  out += label(196, 232, ["It is moving fast, in profit → Bot 5", "Settings → Bots → \"Fast movers → Bot 5\""], "#6d28d9");
  out += `<path d="M 520 268 C 330 300, 150 280, 95 190" fill="none" stroke="#64748b" stroke-width="1.4" stroke-dasharray="5 3" marker-end="url(#mb)"/>`;
  out += label(150, 310, ["Back home: \"send stocks back at the end of the day\", stop-loss \"Back to the bot it came from\""], "#475569");
  return out + `</svg>`;
}

/** The SMA 9 line drawn as a curve with the points where each SMA 9 exit fires. pts: [{x,y}] in 0-100 units. */
function curveFig({ points, marks, width = 720, height = 200, title = "", note = "" }) {
  const padL = 20, padR = 20, padT = 30, padB = 30;
  const X = (v) => padL + (v / 100) * (width - padL - padR);
  const Y = (v) => padT + (1 - v / 100) * (height - padT - padB);
  let out = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(title || "The SMA 9 line")}">`;
  if (title) out += `<text x="${padL}" y="12" font-size="10" font-weight="700" fill="#334155">${esc(title)}</text>`;
  // Smooth path through the points (Catmull-Rom to Bezier).
  const P = points.map((p) => [X(p.x), Y(p.y)]);
  let d = `M ${P[0][0].toFixed(1)} ${P[0][1].toFixed(1)}`;
  for (let i = 0; i < P.length - 1; i++) {
    const p0 = P[Math.max(0, i - 1)], p1 = P[i], p2 = P[i + 1], p3 = P[Math.min(P.length - 1, i + 2)];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C ${c1[0].toFixed(1)} ${c1[1].toFixed(1)}, ${c2[0].toFixed(1)} ${c2[1].toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  out += `<path d="${d}" fill="none" stroke="#d6455d" stroke-width="2.6"/>`;
  marks.forEach((m) => {
    const p = points[m.i];
    const col = m.color || "#0f172a";
    const above = m.y !== "below";
    const ty = above ? Y(p.y) - 24 : Y(p.y) + 22;
    out += `<circle cx="${X(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="5" fill="#fff" stroke="${col}" stroke-width="2.2"/>`;
    out += `<text x="${X(p.x).toFixed(1)}" y="${ty.toFixed(1)}" font-size="9.8" font-weight="700" fill="${col}" text-anchor="${m.anchor || "middle"}" stroke="#fff" stroke-width="3" paint-order="stroke">${esc(m.text)}</text>`;
    if (m.sub) out += `<text x="${X(p.x).toFixed(1)}" y="${(ty + 11).toFixed(1)}" font-size="8.6" fill="#475569" text-anchor="${m.anchor || "middle"}" stroke="#fff" stroke-width="3" paint-order="stroke">${esc(m.sub)}</text>`;
  });
  if (note) out += `<text x="${padL}" y="${height - 6}" font-size="9" fill="#475569">${esc(note)}</text>`;
  return out + `</svg>`;
}

module.exports = { flow, lineChart, levelsFig, botsMap, curveFig };
