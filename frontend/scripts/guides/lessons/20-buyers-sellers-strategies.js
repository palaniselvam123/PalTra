const L = require("../lib");

const KICKER = "PalTra · Learn · Strategy settings";
const FOOT =
  "This guide only explains. Nothing changes until you press Save on the Settings page. Every setting here works the same in PAPER (practice), in LIVE and in a replay, so try a change in a replay of the same days first. The BSE, KAYNES and SOLARINDS numbers are read from the owner's charts; the ₹1,000 stock is made up to keep the arithmetic easy.";
const FLOW_CSS = `<style>table{break-inside:auto} tr{break-inside:avoid} thead{display:table-header-group}</style>`;

/** A 0-100 % strip with labelled lines per candle: lines [{name, color, dash, vals:[]}], marks [{i, text, color}]. */
function strip({ lines, labels, marks = [], levels = [], width = 720, height = 250, title = "" }) {
  const padL = 34, padR = 110, padT = 44, padB = 26;
  const n = labels.length;
  const x = (i) => padL + ((i + 0.5) / n) * (width - padL - padR);
  const y = (v) => padT + (1 - v / 100) * (height - padT - padB);
  let out = `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${title || "Buyers and sellers strip"}">`;
  [0, 25, 50, 75, 100].forEach((v) => {
    out += `<line x1="${padL}" x2="${width - padR}" y1="${y(v)}" y2="${y(v)}" stroke="${v === 50 ? "#cbd5e1" : "#eef2f7"}"/>`;
    out += `<text x="${padL - 4}" y="${y(v) + 3}" font-size="9" fill="#64748b" text-anchor="end">${v}%</text>`;
  });
  levels.forEach((l) => {
    out += `<line x1="${padL}" x2="${width - padR}" y1="${y(l.v)}" y2="${y(l.v)}" stroke="${l.color}" stroke-dasharray="5 3" stroke-width="1.2"/>`;
    out += `<text x="${padL + 4}" y="${y(l.v) - 4}" font-size="9" fill="${l.color}" font-weight="700" stroke="#fff" stroke-width="3" paint-order="stroke">${l.text}</text>`;
  });
  const used = [];
  lines.forEach((ln) => {
    const pts = [];
    ln.vals.forEach((v, i) => {
      if (v == null) return;
      if (pts.length) pts.push(`${(x(i) - (x(1) - x(0)) / 2).toFixed(1)},${pts[pts.length - 1].split(",")[1]}`);
      if (pts.length) pts.push(`${(x(i) - (x(1) - x(0)) / 2).toFixed(1)},${y(v).toFixed(1)}`);
      else pts.push(`${(x(i) - (x(1) - x(0)) / 2).toFixed(1)},${y(v).toFixed(1)}`);
      pts.push(`${(x(i) + (x(1) - x(0)) / 2).toFixed(1)},${y(v).toFixed(1)}`);
    });
    out += `<polyline points="${pts.join(" ")}" fill="none" stroke="${ln.color}" stroke-width="2"${ln.dash ? ' stroke-dasharray="5 3"' : ""}/>`;
    const last = ln.vals[ln.vals.length - 1];
    let ly = y(last) + 3;
    while (used.some((u) => Math.abs(u - ly) < 11)) ly += 11;
    used.push(ly);
    out += `<text x="${width - padR + 4}" y="${ly}" font-size="9" fill="${ln.color}" font-weight="700">${ln.name}</text>`;
  });
  marks.forEach((m) => {
    out += `<line x1="${x(m.i)}" x2="${x(m.i)}" y1="${padT - 4}" y2="${height - padB}" stroke="${m.color}" stroke-width="1" stroke-dasharray="2 2"/>`;
    out += `<text x="${x(m.i) + 3}" y="${padT - 8 - (m.row || 0) * 12}" font-size="9.5" font-weight="700" fill="${m.color}" stroke="#fff" stroke-width="3" paint-order="stroke">${m.text}</text>`;
  });
  labels.forEach((t, i) => {
    if (t) out += `<text x="${x(i)}" y="${height - 9}" font-size="9" fill="#475569" text-anchor="middle">${t}</text>`;
  });
  if (title) out += `<text x="${padL}" y="11" font-size="10" font-weight="700" fill="#334155">${title}</text>`;
  return out + `</svg>`;
}

// ---- BSE, 6 Nov, 15-minute candles: a short held while the trading turned -------------------------------------
const bseLabels = ["09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00"];
const bseFig = () =>
  strip({
    labels: bseLabels,
    lines: [
      { name: "Sellers waiting", color: "#dc2626", vals: [62, 70, 67, 66, 64, 63, 60] },
      { name: "Buyers executed", color: "#0284c7", dash: true, vals: [66, 48, 59, 62, 44, 41, 55] },
    ],
    levels: [{ v: 55, text: "55 % level", color: "#b45309" }],
    marks: [
      { i: 0, text: "short opened", color: "#0f172a", row: 1 },
      { i: 3, text: "10:15: the 10:00 candle is complete", color: "#7c3aed" },
    ],
    title: "BSE short, 6 Nov: sellers kept most of the waiting orders while buyers did most of the trading",
  });

// ---- ₹1,000 stock: entry on executed volume ---------------------------------------------------------------------
const entryFig = () =>
  strip({
    labels: ["10:00", "10:03", "10:06", "10:09", "10:12", "10:15"],
    lines: [
      { name: "Buyers executed", color: "#0284c7", dash: true, vals: [48, 52, 57, 63, 66, 61] },
      { name: "Buyers waiting", color: "#059669", vals: [50, 54, 58, 60, 62, 64] },
    ],
    levels: [{ v: 60, text: "trigger 60 %", color: "#b45309" }],
    marks: [{ i: 3, text: "buy: executed 63 % ≥ 60, waiting 60 % ≥ 55", color: "#059669" }],
    title: "₹1,000 stock, 3-minute windows: the first window where buyers did 60 % of the trading starts the buy",
  });

module.exports = {
  id: "buyers-sellers-strategies",
  file: "buyers-sellers-strategies-guide.pdf",
  title: "Buyers & sellers: every entry and exit strategy, with examples",
  html: () =>
    L.page({
      kicker: KICKER,
      foot: FOOT,
      title: "Buyers &amp; sellers: entry and exit strategies",
      lead: "The bots can read who is in control of a stock in two ways: the <b>orders waiting</b> to buy and sell, and the <b>shares actually traded</b>. This guide lists every setting that uses either one, how to get into a trade with them, how to get out, how they combine (OR / AND), and ready-made recipes, each with an example. It ends with the BSE short of 6 Nov worked through step by step.",
      body: `${FLOW_CSS}
${L.box("blue", "Where they are", `Settings → Strategy. Entries are in <b>1. When to enter</b> and <b>2. Checks before entering</b>; exits are in <b>3. When to exit</b>. The <b>Buyers &amp; sellers overview</b> card at the top of group 1 lists every one of them on one screen, with the ones that repeat each other named. Each setting can be set for all stocks or for one stock, on any bot.`)}

<h2>1. Two readings that look alike</h2>
${L.table(["", "Waiting orders (buyers % / sellers %)", "Executed volume (buyers executed % / sellers executed %)"], [
  ["What it is", "Buy orders waiting ÷ all orders waiting, from Groww's order book", "Shares bought at market ÷ all shares traded, over a window of minutes"],
  ["What it tells you", "<b>Intentions</b>: who is lining up. Orders can be pulled at any time.", "<b>What happened</b>: who was hitting the other side and moving the price."],
  ["Where it comes from", "The Most active scan (every 1-5 minutes, as its auto scan is set)", "The bots' own per-second prices (tick rule), else the candle's shape"],
  ["Which stocks", "Only stocks armed from <b>Most active</b> (auto-arm or the card's Arm)", "<b>Any</b> stock"],
  ["How fresh", "A read counts for 20 minutes; with scans paused there is none", "Every closed minute"],
  ["On the chart", "Solid green / red lines: Buyers waiting %, Sellers waiting %", "Dashed blue / pink lines: Buyers executed %, Sellers executed %"],
])}
${L.box("amber", "Why you need both", `On <b>SOLARINDS</b> buyers held 79 % of the waiting orders while sellers kept selling into those bids, and the price fell. On <b>KAYNES</b> sellers held 78 % of the waiting orders while buyers did 71 % of the trading. The waiting orders show who wants to trade; the executed volume shows who actually did. The strongest signals are when both point the same way.`)}

<h3>The window: which minutes are read</h3>
<p>Every executed volume setting has a window: the last <b>1, 2, 3, 5, 10 or 15</b> closed minutes (or any number typed, up to 60), or <b>"the exit candle"</b> (0) = the last closed candle of the bot's size, counted from 09:15. Tick <b>"Read the same candle as the chart"</b> to use the candle: the % the chart shows on that candle is then exactly what the bot read. Remember that a 15-minute candle's numbers are complete only when it closes: the 10:00 candle is read at 10:15.</p>

<h2 class="page">2. Getting in: the five entry settings</h2>
${L.table(["Setting (where)", "Reads", "Kind", "Default"], [
  ["<b>A. Most active stocks: when to enter</b> (1)", "Waiting", "Check, or starts the trade", "Off · 65 %"],
  ["<b>B. Enter on executed volume</b> (1)", "Executed, then waiting", "Starts the trade", "Off · 60 % · 3 min · 55 %"],
  ["<b>C. Enter on: Executed volume</b> (1, the \"Enter on\" menu)", "Executed only", "The only way in", "Off"],
  ["<b>D. Enter only when the order's own side is doing the trading</b> (2)", "Executed", "Check", "Off · 55 % · 3 min"],
  ["<b>E. Candle colour: let buyers / sellers overrule it</b> (1)", "Waiting (+ executed)", "Changes the side", "Off · 65 %"],
])}
<p>A <b>check</b> only says yes or no to a signal the bot already has (a cross, a colour candle). Something that <b>starts the trade</b> is a signal itself.</p>

${L.cols([
  ["A. Most active stocks: when to enter", `<p>Three choices:</p><ul>
<li><b>Off</b>: the bot's own signal decides.</li>
<li><b>Only while the buyers / sellers still hold</b>: every entry needs the order's side at the % or more in a read of the last 20 minutes; otherwise refused.</li>
<li><b>Enter at once while they hold</b>: the same check, and the bot also buys (buyers hold) or sells (sellers hold) straight away, once a day per stock, without waiting for a signal.</li></ul>
<div class="ex">At 65 %: a stock armed with buyers at 78 % is bought at once. At 11:00 buyers are 61 %: a green colour candle's buy is refused.</div>`],
  ["B. Enter on executed volume", `<p>The other way round from A: the <b>trading</b> starts the trade, the waiting orders check it. A flat stock is bought once buyers did the % of the shares traded in the window (sold once sellers did), then the order's side must hold the second % of the waiting orders (0 = not checked; no fresh read = no order). Every other entry check, the stop and the caps still apply.</p>
<div class="ex">At 60 % / 55 %: in the 10:06-10:09 window buyers did 63 % and buyers hold 60 % of the waiting orders → buy (figure below).</div>`],
  ["C. Enter on: Executed volume", `<p>Turns B on by itself and makes it the <b>only</b> way in: no cross, no colour candle, no waiting-orders check. A and D are then not read (they would only repeat B). The stop, target, ticked exits and the square-off close the trade.</p>
<div class="ex">Use it to test "trade only on what was traded" against your colour strategy in a replay of the same days.</div>`],
  ["D. Executed volume entry check", `<p>A check on any signal: a buy goes in only when buyers did at least the % of the shares traded in the window (a sell: sellers). Too little volume refuses too. Late entry can try again on the next candles. A's "Enter at once" also waits for it.</p>
<div class="ex">KAYNES: sellers 78 % waiting, buyers 71 % executed. With D at 55 %, the sell is refused (sellers did only 29 %).</div>`],
])}
${L.cols([
  ["E. Candle colour: let buyers / sellers overrule it", `<p>Candle colour mode, Most active stocks only. When the <b>other</b> side holds the % of the waiting orders and the price agrees (the candle closed under today's VWAP to sell, over it to buy), the bot goes that way instead of the colour's way. Optionally the executed volume must agree too (the box under it).</p>
<div class="ex">SOLARINDS-type case: a green candle closes under the VWAP while sellers hold 71 % → the bot sells instead of buying.</div>`],
  ["Which entry to pick", `<ul>
<li>You trust the <b>scanner</b> and want in fast: A "Enter at once" + D as a safety check.</li>
<li>You want the <b>trading</b> to start the trade: B (keep the waiting check at 55 %).</li>
<li>You want <b>only</b> the trading: C.</li>
<li>Your colour / cross strategy is good but sometimes enters against the flow: keep it and add D.</li></ul>`],
])}
${L.fig(entryFig(), "B at 60 % on 3-minute windows: the bot judges each window once when it closes. 10:09 is the first where buyers did 63 % of the shares traded and buyers also hold 60 % of the waiting orders (over 55 %), so the buy goes in at the start of the next minute.")}

<h2 class="page">3. Getting out: the exit settings</h2>
${L.table(["Setting (3. When to exit)", "Closes a buy when…", "Kind", "Default"], [
  ["<b>F. Most active stocks: exit when buyers / sellers fade</b>", "buyers' share of the <b>waiting</b> orders falls to the %", "Exit by itself", "Off · 55 %"],
  ["<b>G. …Executed volume must agree</b> (box inside F)", "F fires <b>and</b> sellers did the % of the <b>trading</b>", "Holds F", "Off · 50-95 %"],
  ["<b>H. …Let it overrule the counter-candle exit</b> (box inside F)", "counter candles are ignored while buyers still hold the %", "Holds another exit", "Off"],
  ["<b>I. Executed volume exit — close the trade by itself</b>", "sellers did the % of the <b>trading</b> in the window", "Exit by itself", "Off · 60 % · 3 min"],
  ["<b>J. Executed volume exit — use it to confirm the other exits</b>", "a colour turn, cross, counter candles… fire <b>and</b> sellers did the %", "Holds other exits", "same switch as I"],
])}
<p>For a sell, swap buyers and sellers everywhere.</p>
${L.cols([
  ["F. The fade exit", `<p>Closes when the side that started the trade loses its hold on the waiting orders. It arms only once that side was above the level, so a trade that started without the pressure is not closed by it.</p>
<div class="ex">At 55 %: a buy armed when buyers were 75 % closes at the read where buyers drop to 55 %.</div>`],
  ["G. Executed volume must agree", `<p>Waiting orders can turn for a minute and come back. With G, the fade exit closes only when the trading turned too; otherwise the trade is held and judged again at the next read. Too little volume to read lets the exit go.</p>
<div class="ex">Buyers waiting fall to 52 %, but buyers still did 64 % of the trading → held. Next read: sellers did 61 % → closed.</div>`],
  ["I. Executed volume exit (by itself)", `<p>Closes as soon as the other side does the trading, even while the waiting orders and the candle colour still favour the trade. Two extra ticks: <b>Only while the trade is losing</b> (a winner is left to its stop / target), and <b>The waiting orders must agree too</b> (the other side also above 50 % waiting).</p>
<div class="ex">A short where buyers did 62 % of the last candle's trading is closed, whatever the colour.</div>`],
  ["J. Confirm the other exits", `<p>The executed volume never closes by itself; instead every <b>signal</b> exit (colour turn, counter candles, SMA cross, SMA 9 exits, Supertrend, Bollinger, EMA, MACD…) must be agreed by it. Stops, targets, the square-off and the kill switch are never held.</p>
<div class="ex">A red candle turns a buy, but buyers did 58 % of that candle's trading (needs sellers ≥ 60 %) → held; the next red candle with sellers 66 % closes it.</div>`],
])}
${L.box("", "H in one line", `For Most active stocks, a run of candles against the trade does not close it while a fresh read still shows the trade's own side above F's %. When that side falls to the %, F closes it. With no fresh read the counter-candle exit works as usual.`)}

<h2 class="page">4. OR or AND: what happens when several are ticked</h2>
${L.box("violet", "The rule", `Separate exits are <b>OR</b>: each one closes the trade by itself, and the first to fire wins. That includes F and I together, and every stop and target. An <b>AND</b> is made by a box <i>inside</i> an exit, which makes that exit wait for the other reading.`)}
${L.table(["What you tick", "How they combine", "Closes a buy when…"], [
  ["F (fade) + I (executed exit)", "<b>OR</b>", "buyers waiting fall to F's % <b>or</b> sellers did I's % of the trading"],
  ["F with G ticked", "<b>AND</b>", "buyers waiting fall to F's % <b>and</b> sellers did G's % of the trading"],
  ["I with \"The waiting orders must agree too\"", "<b>AND</b>", "sellers did I's % of the trading <b>and</b> sellers hold over 50 % of the waiting orders"],
  ["I with \"Only while the trade is losing\"", "<b>AND</b>", "sellers did I's % <b>and</b> the trade is under water"],
  ["J (confirm) + colour turn", "<b>AND</b>", "a red candle closed <b>and</b> sellers did J's % of its trading"],
  ["F + G + I", "(F AND G) <b>OR</b> I", "I alone already closes it; G only slows F down"],
])}
${L.box("amber", "A common trap", `If you want an AND, tick the box inside one exit and leave the other exit off. With I on by itself, ticking G changes nothing in the cases where I already fires. The "How exits combine" setting (OR / AND groups) can group the executed volume exit with the signal exits, but not the fade exit.`)}
<h3>Same idea for entries</h3>
<p>Checks always combine with <b>AND</b>: the signal, then every ticked check, must agree. A (Only while they hold) + D = the waiting orders <b>and</b> the trading must both be on the order's side. B already reads both: executed first, then the waiting check.</p>

<h2 class="page">5. Worked case: the BSE short of 6 Nov</h2>
${L.fig(bseFig(), "Bot 1, 15-minute Heikin Ashi colour candles, Most active stock. The short opened at 09:30. From 10:00 buyers did most of the trading while sellers still held about two thirds of the waiting orders. The values are rounded from the chart.")}
${L.table(["Setting in that backtest", "What it needed for a short", "At 10:00 (read at 10:15)", "Result"], [
  ["F, fade exit at 30 %", "sellers waiting ≤ 30 %", "sellers waiting 67 %", "hold"],
  ["I, executed volume exit", "(was <b>off</b>)", "buyers executed 59 %", "not read"],
  ["Candle colour exit rule", "3 green Heikin Ashi candles, each body ≥ 0.7 %", "one green, body about 0.49 %", "hold"],
  ["SMA 9 V-turn", "a close ≥ ₹3,871.99", "close ₹3,776.10", "hold"],
  ["Trailing stop", "2 % above the entry, about ₹75 away", "not reached", "hold"],
])}
<p><b>To close it at 10:15:</b> tick I, <b>55 %</b>, "Read the same candle as the chart", "Close the trade by itself", "The waiting orders must agree too" <b>off</b> (the waiting orders still favoured the short, so with it on I would never fire). At 60 % it would still have held: buyers did 59 %, one point short. Optional: "Only while the trade is losing" (the short was under water at 10:00).</p>
<p><b>To not enter it at all:</b> the entry dot read buyers 66 % / sellers 35 % executed at 09:27-09:30. D at 55 % would have refused the short: sellers did only 35 % of the trading.</p>

<h2 class="page">6. Recipes</h2>
${L.table(["I want to…", "Tick", "Numbers to start with"], [
  ["enter Most active stocks fast, but not against the trading", "A \"Enter at once\" + D", "A 65 % · D 55 %, the exit candle"],
  ["let the trading start every trade", "B (or C for trading only)", "60 % · 3 min · waiting 55 %"],
  ["keep my colour / cross strategy, but skip entries against the flow", "D", "55 %, Read the same candle as the chart"],
  ["exit when the trading turns, even if the waiting orders still favour me (BSE)", "I, close by itself; waiting must agree off", "55 %, the exit candle"],
  ["exit when the waiting orders turn, but not on a one-scan blip", "F + G", "F 55 % · G 55 %, 3 min"],
  ["stop the colour exit shaking me out while the trading is still with me", "J (confirm)", "60 %, the exit candle"],
  ["cut only losing trades on the trading turning", "I + Only while losing", "55-60 %"],
  ["hold Most active trades through a pullback while the orders still back them", "F + H", "F 55 %"],
])}
${L.box("green", "How to test a change", `${L.steps([
  "Change one setting at a time on a practice bot (or the backtest server).",
  "Replay the same days with \"As armed live\" from the replay bar.",
  "Open Compare backtests with the old run: the trade-by-trade view shows which trades the change closed earlier or refused.",
  "Check the chart: the exit reason (Executed volume / Buyers or sellers faded) and the signal line (\"… exit held — executed volume does not agree\").",
])}`)}

<h2>7. Good to know</h2>
<ul>
<li>The waiting-orders settings (A, E, F, H, B's waiting check and I's "waiting orders must agree") work only on stocks armed from <b>Most active</b>, and only while that scan runs. Set its auto scan to 1-2 minutes on the server that trades; a replay reads the saved scans of that server only.</li>
<li>Groww gives no list of trades, so the executed split is an <b>estimate</b>: per-second price ticks (up = buy, down = sell) where the bots recorded them, else each candle's shape. Days older than 10 days, and stocks no bot watched, use the candle shape.</li>
<li>Exits by the executed volume are judged once per closed window, and only on minutes after the entry.</li>
<li>A one-sided order book (0 % or 100 %) is a bad quote and is read as unknown.</li>
<li>The chart's tick <b>Mark what the bot read at each entry and exit</b> draws "in B66 S35" / "out B60 S40" dots: the numbers the bot judged.</li>
</ul>

<h2>8. Words used here</h2>
${L.words([
  ["Buyers waiting %", "Buy orders waiting ÷ all orders waiting, in the latest Most active read.", "67 % sellers waiting = 33 % buyers waiting."],
  ["Buyers executed %", "Shares bought at market ÷ all shares traded in the window.", "Buyers executed 59 % on BSE's 10:00 candle."],
  ["Window", "The closed minutes the executed % is read over; 0 = the last closed candle of the bot's size.", "3 min, or the 15-minute candle."],
  ["Check", "Says yes or no to a signal the bot already has; never trades by itself.", "D refuses the KAYNES sell."],
  ["Hold", "An exit fired but a box inside it says wait; it is judged again next time.", "\"… exit held — executed volume does not agree\"."],
  ["OR / AND", "OR: any one closes. AND: all must agree.", "F + I is OR; F with G is AND."],
])}
`,
    }),
};
