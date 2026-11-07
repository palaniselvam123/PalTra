const L = require("../lib");

// A cross entry with VWAP agreeing, then a Bollinger-band exit.
const closes = [1000, 999.2, 998.5, 999.4, 1000.6, 1001.9, 1003.2, 1004.1, 1005.8, 1007.4, 1009.9, 1012.1, 1011.2, 1009.8, 1010.6, 1008.9];
const vwap = closes.map((_, i) => 999.5 + i * 0.35);
module.exports = {
  id: "scalp-strategy-lab",
  file: "scalp-strategy-lab-guide.pdf",
  title: "Strategy lab: which checks and exits make money?",
  html: () =>
    L.page({
      title: "Strategy lab: which checks and exits make money?",
      lead: "The bot has dozens of switches: entry checks (VWAP, RSI, ADX…), exits (Bollinger, Supertrend, SMA 9 turn…), stops and targets. Which ones help on your stocks? The lab reads your stocks' past days once, keeps every SMA cross as a trade with every rule's answer already worked out, and then lets you tick rules and see the P&L change at once, no replay needed.",
      body: `
${L.box("blue", "The idea in one line", `A replay takes minutes per setting. The lab does the slow part once (read the candles, work out every indicator, note every cross) and the fast part as often as you like (which crosses a check would have refused, where each exit would have fired). Ticking a box is arithmetic on a table, so the answer is instant.`)}

<h2>1. The picture: one base trade, every rule answered</h2>
${L.fig(L.candles({
  title: "A buy on the SMA cross, VWAP agreeing; two possible exits",
  candles: L.series(closes, 0.35),
  lines: { sma9: L.sma(closes, 3), sma21: L.sma(closes, 7), vwap },
  marks: [{ i: 4, text: "cross: trade opens", color: "#0284c7", y: "below" }, { i: 11, text: "Bollinger band exit", color: "#059669" }, { i: 15, text: "SMA cross exit", color: "#dc2626", y: "below" }],
  xLabels: [[0, "09:30"], [8, "10:10"], [15, "10:45"]],
  height: 240,
}), "The lab keeps this cross as a base trade and writes next to it: VWAP check = allowed (price above the dotted VWAP line), RSI check = allowed, ADX = refused (too weak), Bollinger band exit = candle 12 at ₹1,012, SMA cross exit = candle 16 at ₹1,009, ATR stop = never hit, square-off = 15:15. Tick a rule and the trade's end moves to the earliest of the ticked exits.")}

<h2>2. What is on the screen</h2>
${L.table(["Part", "What it is", "How to read it"], [
  ["Run inputs", "Stocks (scan lists), Last N days, Numbers from Bot 1-4 (its SMA periods, stop and quantity), Candle (the bot's own or 1-15 minutes), a name. <b>Read the days</b> starts a run.", "One run per stock list and candle; later runs are listed and can be reopened."],
  ["Entry checks box", "VWAP, volume, density, RSI, Bollinger, SMA gap, candle direction, EMA, MACD, cumulative delta, Supertrend, level, ADX.", "A ticked check throws away the crosses it would have refused. Fewer trades, hopefully better ones."],
  ["Exits box", "SMA cross, Bollinger band / middle, Supertrend, EMA, MACD, CVD divergence, counter candles, slow SMA, SMA 9 turn / bend / V-turn, deceleration guard.", "The trade ends at the <b>earliest</b> ticked exit, the stop, a target or the square-off."],
  ["Stop and targets", "Stop: none / ATR / trailing / Supertrend / VWAP. Targets: trailing target, pivot R1 / R2.", "Stops are judged on each minute's high / low: the cautious way."],
  ["Enter between / Qty", "Only crosses inside those hours count; qty 0 = the bot's own.", "Try 09:30-11:00 against the whole day."],
  ["Totals", "P&amp;L before and after charges, trades, win %, green days, profit factor, biggest drop, skipped counts.", "After charges is the number that matters."],
  ["What does each rule add?", "Every rule flipped one at a time against your choice: what it would add or cost in ₹. <b>Use the best found</b> applies a greedy search's best set.", "A rule that adds ₹2,000 on 20 days is worth a replay; ₹150 is noise."],
  ["Running total, By stock / day / exit reason, Trades", "The equity line and the tables behind the totals; CSV.", "A line that climbs in steps is healthy; one big jump is one lucky day."],
  ["Backtest this in the replay", "Saves the ticked choice to the bot and starts a real replay on the same stocks and days.", "The proof: the lab is a sieve, the replay is the bot."],
])}

<h2>3. What each rule adds: a picture</h2>
${L.fig(L.bars([
  { label: "VWAP check", value: 2100, color: "#059669", text: "+₹2,100 (refuses 9 of 31 trades)" },
  { label: "Bollinger band exit", value: 1400, color: "#059669", text: "+₹1,400" },
  { label: "ADX check", value: 650, color: "#059669", text: "+₹650" },
  { label: "RSI check", value: 600, color: "#dc2626", text: "−₹600" },
  { label: "Supertrend exit", value: 300, color: "#dc2626", text: "−₹300" },
], { max: 2500 }), "Against the current choice, each rule on its own. Green bars help; red ones would cost money here. The best found: VWAP + ADX + Bollinger band exit, +₹4,900 after charges over 10 days.")}

<h2>4. How to use it</h2>
${L.screen("Strategy lab: which checks and exits make money?", [
  `${L.chip("Stocks: Nifty 50", true)} ${L.chip("Last 10 days")} ${L.chip("Numbers from Bot 1")} ${L.chip("Candle: 5 minutes")} ${L.chip("Name: Nifty 50, 5-min")} ${L.btn("Read the days", true)}`,
  `<span>Entry checks:</span> ${L.chip("VWAP ✓")} ${L.chip("ADX ✓")} ${L.chip("RSI")} <span>Exits:</span> ${L.chip("SMA cross ✓")} ${L.chip("Bollinger band ✓")} <span>Stop:</span> ${L.chip("ATR")} ${L.chip("Enter 09:30-14:00")}`,
  `<b>Totals:</b> <span class="g">+₹6,200 before</span> <span class="g">+₹4,900 after charges</span> <span>22 trades</span> <span>win 55%</span> <span>7 of 10 green</span> <span>profit factor 1.8</span> ${L.btn("What does each rule add?")} ${L.btn("Backtest this in the replay", true)}`,
])}
${L.steps([
  "Pick the <b>stocks</b>, the <b>days</b> (10-20), the <b>bot</b> whose numbers to use and the <b>candle</b>. Press <b>Read the days</b> and wait (one download per stock, cached on disk for the next run).",
  "The run opens with the bot's own switches ticked. Read the <b>totals</b>: that is what the strategy, as set, would have made.",
  "Press <b>What does each rule add?</b>. Tick the rules with big green bars, untick the red ones, and watch the totals change at once.",
  "Check <b>By day</b>: did the profit come from many days or one? Check <b>By exit reason</b>: which exit takes the profits and which takes the losses.",
  "Press <b>Backtest this in the replay</b>. The real bot now plays the same days with the ticked settings. If the replay agrees, keep the settings; if not, the lab's shortcut did not hold on those stocks.",
])}

<h2>5. A worked example</h2>
${L.box("", "", `Nifty 50, 10 days, Bot 1's numbers, 5-minute candles. As set: +₹2,400 before, +₹300 after charges, 31 trades. \"What does each rule add?\": VWAP +₹2,100 (it refuses 9 trades, mostly losers), ADX +₹650, Bollinger band exit +₹1,400, RSI −₹600, Supertrend exit −₹300. Ticked VWAP + ADX + Bollinger band: +₹6,200 before, +₹4,900 after, 22 trades, 7 of 10 green days, profit factor 1.8. The replay on the same days gave +₹4,400 after charges: close enough. Saved on Bot 1.`)}
${L.box("amber", "What the lab is not", `It is not the bot. It works on closed candles, fills at the next candle's open, and plays no pullback waits, gap mode, hand-offs, reviews, fast movers or news. Only the SMA cross opens trades here. So it finds the switches worth trying; the replay decides.`)}

<h2>6. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Tries a setting in a second instead of a minutes-long replay.</li><li>Shows what each rule adds in ₹, so you keep the few that matter.</li><li>One button turns the finding into a real replay.</li></ul>`],
  ["Limits", `<ul><li>A sieve, not the bot (see above).</li><li>Ten days can flatter a rule; twenty are safer.</li><li>Practice money; nothing is armed, no order is sent.</li></ul>`],
])}

<h2>7. Words used here</h2>
${L.words([
  ["Base trade", "An SMA cross kept as a trade, with every rule's answer already noted."],
  ["Entry check", "A rule that can refuse a cross (VWAP, RSI, ADX…)."],
  ["Exit rule", "A rule that can close a trade before the opposite cross (Bollinger, Supertrend, SMA 9 turn…)."],
  ["Earliest exit", "The trade ends at whichever ticked exit, stop, target or square-off comes first."],
  ["Greedy search", "Add the rule that helps most, then the next, until nothing helps: \"the best found\"."],
  ["Sieve", "A quick filter that keeps candidates; the replay is the real test."],
])}
`,
    }),
};
