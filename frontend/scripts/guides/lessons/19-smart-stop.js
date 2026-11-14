const L = require("../lib");

const KICKER = "PalTra · Learn · Strategy settings";
const FOOT =
  "This guide only explains. Nothing changes until you press Save on the Settings page. The smart stop works the same in PAPER (practice), in LIVE and in a replay, so try a change in a replay first. The AMBER numbers are read from the chart of 23 Oct; the candles before 10:15 are drawn approximately. The ₹1,000 examples are made up to keep the arithmetic easy.";
const FLOW_CSS = `<style>table{break-inside:auto} tr{break-inside:avoid} thead{display:table-header-group}</style>`;

// ---- AMBER, 23 Oct, 15-minute colour candles: the short, its smart stop and the exit -------------------------
const amber = [
  { o: 6120, h: 6128, l: 6085, c: 6090 }, // 09:30 red: the colour signal
  { o: 6074, h: 6080, l: 6020, c: 6030 }, // 09:45 sold at the open, 6,074
  { o: 6030, h: 6040, l: 5990, c: 5998 }, // 10:00
  { o: 5996.5, h: 6002, l: 5945, c: 5998.5 }, // 10:15 low 5,945 then back to 6,002: stop hit at 6,000.50
  { o: 5998, h: 6008, l: 5985, c: 5990 }, // after: sideways
  { o: 5990, h: 6004, l: 5982, c: 6000 },
  { o: 6000, h: 6012, l: 5992, c: 5996 },
];
const amberFig = () =>
  L.candles({
    candles: amber,
    lines: { stop: [null, 6083.7, 6051.2, 6000.5, null, null, null] },
    styles: { stop: { color: "#dc2626", label: "Smart stop", step: true } },
    levels: [
      { v: 6074, text: "Sold 6,074", color: "#64748b" },
      { v: 5945, text: "Best 5,945", color: "#059669" },
    ],
    marks: [
      { i: 1, text: "S: sold at 6,074", color: "#0f172a", anchor: "start", lift: 6 },
      { i: 3, text: "stop hit 6,000.50 (+73.50)", color: "#dc2626", anchor: "start", lift: 18 },
    ],
    xLabels: [[0, "09:30"], [1, "09:45"], [2, "10:00"], [3, "10:15"], [4, "10:30"], [5, "10:45"], [6, "11:00"]],
    height: 250,
    title: "AMBER short, 15-minute colour candles: the stop came down after every new low",
  });

// ---- How the room is measured: dips on a buy ----------------------------------------------------------------
const dipCloses = [1000, 1003, 1006, 1004, 1008, 1011, 1009, 1007.5, 1012, 1015, 1013, 1017, 1020, 1018, 1022];
const dipFig = () =>
  L.candles({
    candles: L.series(dipCloses, 0.6),
    marks: [
      { i: 3, text: "dip 0.3 %", color: "#b45309", y: "below", lift: 2 },
      { i: 7, text: "dip 0.4 %", color: "#b45309", y: "below", lift: 2 },
      { i: 10, text: "dip 0.25 %", color: "#b45309", y: "below", lift: 2 },
      { i: 13, text: "dip 0.2 %", color: "#b45309", y: "below", lift: 2 },
      { i: 5, text: "new high", color: "#059669", lift: 2 },
      { i: 8, text: "new high", color: "#059669", lift: 2 },
    ],
    xLabels: [[0, "earlier day"], [14, "entry minute"]],
    height: 210,
    title: "A buy's room is read from dips: a new high, the lowest point after it, then a new high again",
  });

// ---- Sideways after the exit: colour flips every candle ------------------------------------------------------
const chop = [
  { o: 6000, h: 6010, l: 5990, c: 5992 },
  { o: 5992, h: 6012, l: 5988, c: 6008 },
  { o: 6008, h: 6014, l: 5996, c: 5998 },
  { o: 5998, h: 6016, l: 5994, c: 6010 },
  { o: 6010, h: 6012, l: 5990, c: 5994 },
  { o: 5994, h: 6006, l: 5990, c: 6004 },
  { o: 6004, h: 6008, l: 5986, c: 5990 },
];
const chopFig = () =>
  L.candles({
    candles: chop,
    marks: [
      { i: 1, text: "B", color: "#059669", lift: 2 },
      { i: 2, text: "X  S", color: "#dc2626", lift: 2 },
      { i: 3, text: "X  B", color: "#059669", lift: 2 },
      { i: 4, text: "X  S", color: "#dc2626", lift: 2 },
      { i: 5, text: "X  B", color: "#059669", lift: 2 },
      { i: 6, text: "X  S", color: "#dc2626", lift: 2 },
    ],
    xLabels: [[0, "11:15"], [3, "12:00"], [6, "12:45"]],
    height: 190,
    title: "Sideways: every candle is the other colour, so the colour rule closes (X) and reverses on each one",
  });

module.exports = {
  id: "smart-stop",
  file: "smart-stop-guide.pdf",
  title: "Smart stop: how it works, why it closed in profit, and the Advanced settings",
  html: () =>
    L.page({
      kicker: KICKER,
      foot: FOOT,
      title: "The smart stop, explained",
      lead: "The smart stop gives every stock its own stop distance, worked out from how that stock itself has been moving, and then follows the price to keep the profit. This guide answers two questions from the AMBER chart of 23 Oct (why it closed while the trade was in profit, and why trades still open and close on every candle) and then explains each Advanced setting with an example.",
      body: `${FLOW_CSS}
${L.box("blue", "Where it is", `Settings → Strategy → <b>5. Stop-loss and target</b> → the switch at the top, <b>Smart stop: each stock gets its own stop room</b>. Under it, <b>Advanced</b> holds six settings. It can also be switched on the terminal's Strategy card. It can be set for all stocks or for one stock ("Settings for"), on any bot.`)}

<h2>1. The smart stop in one minute</h2>
${L.steps([
  "<b>At the entry</b> the bot reads that stock's own candles of the last 3 days, in the candle size it trades on (15-minute colour candles are read as 15-minute candles).",
  "It measures every <b>dip the stock came back from</b> (for a buy) or every <b>bounce</b> (for a sell). The <b>room</b> is a size that most of those dips fit inside. It is never less than one candle's normal swing (1 ATR), and it is kept between 0.3 % and 2 % of the price.",
  "The stop starts <b>one room</b> away from the entry price.",
  "As the price moves your way, the stop <b>follows the best price</b> at one room behind it. It only ever moves closer, never back.",
  "Once the trade has gained one full room, the stop is at least the entry price plus charges: <b>no loss is left</b>.",
  "The further the trade runs, the <b>closer the stop follows</b> (down to half a room), so more of a big move is kept.",
  "The price touching the stop closes the trade: <b>SMART_SL_HIT</b>, shown as \"Smart stop: the price came back more than this stock's usual dip from its best since the entry\".",
])}

<h2>2. Your AMBER trade: why it closed while it was in profit</h2>
${L.fig(amberFig(), "The red staircase is the smart stop at the end of each candle. It came down after each new low. In the 10:15 candle the price fell to 5,945 and then climbed back to 6,002 inside the same candle; it touched the stop at 6,000.50 at 10:25:40.")}
${L.table(["Time", "What happened", "Best price so far", "Profit at the best", "Smart stop"], [
  ["09:45", "Candle colour sell at the open", "6,074", "₹0", "about 6,137.70 (one room above)"],
  ["end of 09:45", "Price fell to about 6,020", "about 6,020", "₹54 (less than one room)", "about 6,083.70 (best + one room)"],
  ["end of 10:00", "Price fell to about 5,990", "about 5,990", "₹84 (more than one room)", "about 6,051.20: below the entry, so no loss left"],
  ["10:15 candle", "New low 5,945", "5,945", "₹129 (about two rooms)", "6,000.50 (room shrunk to about ₹55.50)"],
  [{ v: "10:25:40", cls: "r" }, "Price climbed back to 6,000.50", "", "", { v: "Hit: bought back at 6,000.50", cls: "r" }],
])}
<p>The room for this trade was about <b>₹63.70 (1.05 % of the price)</b>, a little more than one 15-minute ATR (₹51.62). Result: sold 6,074, bought back 6,000.50 = <b class="g">+₹73.50 a share, +₹22,050 on 300 shares</b>, before charges.</p>
${L.box("green", "This is what it is meant to do", `A stop that only waits below the entry would have given all the profit back. The smart stop's job is to <b>keep</b> profit: when the price comes back more than this stock normally comes back, it takes the profit that is left. In the 10:15 candle AMBER bounced ₹57 off its low, more than the ₹55.50 the stop allowed at that point, so the trade was closed with ₹73.50 a share kept. Afterwards the stock went sideways and later climbed to 6,100: holding the short would have cost money.`)}
${L.box("amber", "If you want it to give winners more room", `It closed because the bounce was bigger than the room at that moment. To hold through a bigger bounce: raise <b>Room covers this share of dips</b> (85 → 90 or 95), or untick <b>Follow closer as the profit grows</b> (the stop would then have stayed ₹63.70 behind 5,945, at 6,008.70, and the 6,002 high would not have touched it). Section 4 shows both. A wider room keeps more of a long trend and gives back more when the trend really ends: try it in a replay of the same days before using it.`)}

<h2 class="page">3. Why trades still open and close on every candle</h2>
${L.fig(chopFig(), "After 10:30 AMBER went sideways around 6,000. Each 15-minute candle was the other colour from the one before, so the colour rule closed the trade (X) and opened the other side on every candle.")}
<p>The smart stop is a <b>stop</b>: it decides how far the price may come back before a trade is closed. It does <b>not</b> switch off the rule that opens trades. You trade in <b>Enter on: Candle colour</b>. In that mode the candle colour does two jobs:</p>
${L.table(["The candle colour rule", "What it does", "Changed by the smart stop?"], [
  ["Opens a trade", "A green candle buys, a red candle sells, at the start of the next candle.", "No"],
  ["Closes a trade (<b>COLOUR_TURN</b>)", "The first candle of the other colour closes the trade and opens the other side.", "No"],
  ["The stop", "Smart stop: closes when the price comes back more than the stock's usual dip.", "This is the smart stop"],
])}
<p>Whichever comes first closes the trade. On a trending stock the colours stay the same for many candles, so the smart stop usually closes the trade. On a <b>sideways</b> stock the colour changes nearly every candle, so the colour turn comes first, long before the price gets near the stop. That is why you see B, X, S, X, B, X… on every candle after 10:30.</p>

<h3>Ways to stop the trade-every-candle pattern</h3>
${L.table(["Setting", "Where", "What it changes", "Example"], [
  ["<b>Use a separate rule to close a trade</b>", "1. When to enter, under the candle colour rules", "Trades are closed only by your exit rule, not by one candle of the other colour. Entries are unchanged.", "Candles of the other colour in a row <b>3</b>, smallest body <b>0.2 %</b>: one small green candle no longer closes a short; the smart stop guards it in between."],
  ["<b>Candles in a row</b> and <b>Smallest body</b>", "1. When to enter (candle colour)", "Needs 2 or more candles of the same colour, each with a real body, to enter or turn.", "2 in a row, body 0.1 %: a red then green then red day opens nothing."],
  ["<b>Read candle colours from Heikin Ashi candles</b>", "1. When to enter (candle colour)", "Heikin Ashi candles change colour much less often in a sideways market. Prices stay real.", "The six flips above might be two or three Heikin Ashi colour changes."],
  ["<b>Max trades per stock / day</b>", "6. Size, daily limits and times", "Stops new entries on that stock after N trades today.", "3: AMBER would have stopped after its third trade."],
  ["<b>ADX check</b>", "2. Checks before entering", "Refuses entries while the stock is not trending.", "ADX at least 20."],
  ["<b>Choppy after losing trades</b>", "7. Advanced", "After N losing trades, moves the stock to Bot 2 (the bot for choppy stocks).", "2 losing trades → Bot 2."],
])}
${L.box("blue", "A good first combination for colour candles plus the smart stop", `Smart stop <b>on</b> (defaults). <b>Use a separate rule to close a trade</b>: 3 candles in a row, body 0.2 %. Entry <b>Candles in a row</b> 2. <b>Max trades per stock / day</b> 3. Replay the last 10 days on that bot and compare with your current settings in the Backtests tab before you use it.`)}

<h2>4. The Advanced settings, one by one</h2>
<p>Open them with <b>Advanced (leave as is unless you are testing)</b> under the smart stop switch. The defaults suit most stocks; change one at a time and replay the same days after each change.</p>

<h3>4.1 Room covers this share of dips (default 85 %, 50-99)</h3>
<p>The bot sorts the stock's dips from small to big and picks the size that this share of them fits inside. Higher = a wider room = fewer stop-outs on normal dips, more given back when the trend really ends.</p>
${L.fig(dipFig(), "Each orange label is one dip: the price made a new high, came down, then made a new high again. Only dips that came back within about an hour (or 12 candles, on longer candles) are counted.")}
<p>Example: a ₹1,000 stock had 12 dips in the last 3 days: 0.12, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8 and 1.2 %.</p>
${L.table(["Setting", "Room", "On ₹1,000", "Means"], [
  ["70 %", "0.57 %", "₹5.70", "Tight: some normal dips will stop you out."],
  [{ v: "85 % (default)", cls: "b" }, { v: "0.74 %", cls: "n b" }, { v: "₹7.40", cls: "n b" }, "Most normal dips fit; an unusually big one closes the trade."],
  ["90 %", "0.79 %", "₹7.90", "A little more room."],
  ["95 %", "0.98 %", "₹9.80", "Wide: nearly every dip fits, including the rare 1.2 % one."],
])}

<h3>4.2 Days of candles read (default 3, 1-10)</h3>
<p>How many past sessions of the stock's candles are read to find its dips. Fewer days = follows how the stock moves <i>now</i> (after news, a stock can swing twice as much as last week). More days = a steadier room that changes less from day to day. With fewer than 5 dips found, the bot uses 1.5 × the ATR of the candles instead.</p>
${L.box("", "Example", `On 15-minute candles a session has only 25 candles, so 3 days may give only a few dips. If the trade's line in the terminal says "too few dips: 1.5 × the 15-min ATR", raise this to 5 or 6 days.`)}

<h3>4.3 Smallest room (default 0.3 %) and 4.4 Largest room (default 2 %)</h3>
<p>The two limits the room is kept between, in % of the price. The smallest room stops a calm stock from getting a stop that one tick of noise hits. The largest room caps the loss on a wild stock.</p>
${L.table(["Stock", "Room worked out", "With 0.3 % / 2 %", "On the stock"], [
  ["Calm ₹1,000 stock", "0.15 %", { v: "raised to 0.3 %", cls: "a" }, "₹3 instead of ₹1.50"],
  ["Normal ₹1,000 stock", "0.74 %", "0.74 % (inside the limits)", "₹7.40"],
  ["Wild ₹1,000 stock after news", "3.1 %", { v: "cut to 2 %", cls: "a" }, "₹20 instead of ₹31"],
])}
<p>The room is also never less than 1 ATR of the candles the bot trades on (one candle's normal swing). The terminal's line says "raised to one 15-min candle's swing (1 ATR)" when that happens. The smallest / largest limits still apply after it.</p>

<h3>4.5 Follow closer as the profit grows (on by default)</h3>
<p>Until the trade has gained one room, the stop stays one room behind the best price. After that, for every further room of profit, the distance shrinks by 1/8 of the room, down to half a room. Off = always one full room behind.</p>
<p>Example: buy at ₹1,000, room ₹10, charges about ₹0.60 a share.</p>
${L.table(["Best price", "Profit", "Stop with Follow on", "Kept if hit", "Stop with Follow off", "Kept if hit"], [
  ["₹1,005", "₹5", "₹995", { v: "−₹5", cls: "n r" }, "₹995", { v: "−₹5", cls: "n r" }],
  ["₹1,010", "₹10 (1 room)", "₹1,000.60 (no loss)", "₹0.60", "₹1,000.60 (no loss)", "₹0.60"],
  ["₹1,020", "₹20", "₹1,011.25 (8.75 behind)", { v: "+₹11.25", cls: "n g" }, "₹1,010", { v: "+₹10", cls: "n g" }],
  ["₹1,030", "₹30", "₹1,022.50 (7.50 behind)", { v: "+₹22.50", cls: "n g" }, "₹1,020", { v: "+₹20", cls: "n g" }],
  ["₹1,050", "₹50", "₹1,045 (5 behind)", { v: "+₹45", cls: "n g" }, "₹1,040", { v: "+₹40", cls: "n g" }],
  ["₹1,080", "₹80", "₹1,075 (half a room, the closest)", { v: "+₹75", cls: "n g" }, "₹1,070", { v: "+₹70", cls: "n g" }],
])}
${L.box("amber", "On or off?", `<b>On</b> keeps more of a big move, but a long trend that pauses with a bounce bigger than half a room closes early (this is what happened on AMBER at about two rooms of profit). <b>Off</b> rides deeper bounces of long trends and gives back one full room at the end. Untick it for slow, steady trenders; leave it on for fast movers.`)}

<h3>4.6 Work each stock's room out again after every candle of the trade (on by default)</h3>
<p>While a trade is open, each time one of its candles closes, the room is worked out again from the stock's candles up to then, including the trade's own swings, at today's price. The stop still only moves closer:</p>
<ul>
  <li>a <b>smaller</b> new room (the stock has calmed down) pulls the stop closer at once;</li>
  <li>a <b>larger</b> new room (the stock got wilder) does not move the stop back; it only pauses the stop until the price has moved far enough.</li>
</ul>
<p>A change of 10 % or more is written in the trade's stop log, for example "11:30 smart room → 52.40 (0.86%)", and the Trail tracker shows "re-sized 11:30". Off = the room from the entry is kept for the whole trade.</p>

<h2>5. Recipes</h2>
${L.table(["I want to…", "Change", "Mind"], [
  ["let winning trades run longer", "Room covers 90-95 %, or untick Follow closer", "More profit is given back when the trend ends."],
  ["take profit sooner", "Room covers 75-80 %, keep Follow closer on", "More stop-outs on normal dips."],
  ["stop a cheap, calm stock being stopped out by noise", "Smallest room 0.4-0.5 %", "A real turn costs a little more."],
  ["cap the loss on wild stocks", "Largest room 1-1.5 %", "Wild stocks will be stopped out more often."],
  ["use the room the stock has today, after news", "Days of candles read 1-2", "Fewer dips: may fall back to 1.5 × ATR."],
  ["keep the same stop for the whole trade", "Untick Work each stock's room out again", "The stop no longer tightens when the stock calms down."],
  ["stop trading every candle in candle colour mode", "Section 3: a separate exit rule, candles in a row, Heikin Ashi, per-stock cap", "These are entry / exit rules, not smart stop settings."],
])}

<h2>6. Where to see what the smart stop is doing</h2>
${L.table(["Place", "What it shows"], [
  ["Terminal signal line at entry", "\"AMBER smart stop 6,137.70 — 1.05% room (N dips on 15-min candles)\": the starting stop and how the room was found."],
  ["Terminal signal line while held", "\"AMBER smart stop → 6,051.20 (best 5,990.00, room 63.70)\" each time the stop moves."],
  ["Trail tracker (terminal, live tab)", "Entry, last price, best since entry, the stop, room to it, \"follows best − ₹…\", and \"re-sized HH:MM\"."],
  ["Chart trade card / blotter", "The exit reason: Smart stop (SMART_SL_HIT) and the stop log lines."],
  ["Settings → 5, the all-stocks card under the smart stop switch", "Each armed stock's buy / sell room today and the candle size it was read on, before any trade."],
])}
${L.box("", "Good to know", `<ul style="margin:2px 0 0 16px">
<li>The smart stop replaces the other stop types: the ATR target and the staged stop plan do not run with it.</li>
<li>In LIVE the stop is a real Groww stop order, moved with a modify at most every 2 seconds and only when it gains at least 1/10 of the room.</li>
<li>After a restart the room is worked out again from the candles up to the entry, and the stop carries on from its saved level.</li>
<li>Replays load about 500 one-minute candles, so their room can be read from fewer days than live.</li>
<li>The Strategy lab does not know the smart stop: it treats it as the ATR stop. Use a replay to test it.</li>
</ul>`)}

<h2>7. Words used here</h2>
${L.words([
  ["Room", "How far the price may come back from its best before the trade is closed, in ₹ and % of the price.", "₹63.70 = 1.05 % on AMBER."],
  ["Dip / bounce", "For a buy: a fall from a new high that came back to a new high. For a sell: a rise from a new low that came back below it.", "1,011 → 1,007.50 → 1,012 is a 0.35 % dip."],
  ["Percentile", "The size that a given share of the dips fit inside.", "85th: 85 out of 100 dips were this size or smaller."],
  ["ATR", "Average true range: one candle's normal high-to-low size.", "AMBER 15-min ATR 51.62."],
  ["Breakeven", "The entry price plus round-trip charges: the trade neither wins nor loses.", "Buy ₹1,000 → about ₹1,000.60."],
  ["COLOUR_TURN", "Candle colour mode's own exit: a candle of the other colour closed the trade.", "X on every candle in a sideways market."],
  ["SMART_SL_HIT", "The smart stop closed the trade.", "AMBER at 6,000.50, 10:25:40."],
])}
`,
    }),
};
