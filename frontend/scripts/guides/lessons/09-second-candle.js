const L = require("../lib");

// 1-minute walk after a green 2nd 5-minute candle: entry at 09:25, dip ₹3, run to +₹18, fade to +₹12 by 15:15.
const walk = [1000, 1001.5, 1000.8, 999.6, 998.9, 997.4, 998.2, 999.8, 1001.6, 1003.5, 1005.9, 1008.2, 1010.4, 1012.9, 1015.5, 1017.8, 1018.4, 1016.9, 1015.2, 1013.6, 1012.5];
module.exports = {
  id: "scalp-second-candle",
  file: "scalp-second-candle-guide.pdf",
  title: "Second candle test: does \"buy the 2nd candle\" give ₹10?",
  html: () =>
    L.page({
      title: "Second candle test: does \"buy the 2nd candle\" give ₹10?",
      lead: "A simple rule many beginners use: wait for the day's second 5-minute candle to close; if it is green, buy; if red, sell short; aim for ₹10 a share. This tab measures the rule on every stock and past day you pick: how far the trade went, how far it went against you first, and what a target, a stop or a trailing stop would have made.",
      body: `
${L.box("blue", "The rule in one line", `The first candle of the day (09:15-09:20) is wild. The second (09:20-09:25) shows the real direction. Trade with it at 09:25, with the busiest stocks of the day (highest volume vs usual) first.`)}

<h2>1. The picture: one trade, measured</h2>
${L.fig(L.candles({
  title: "After a green 2nd candle: entry ₹1,000 at 09:25, 1-minute candles",
  candles: L.series(walk, 0.5),
  levels: [{ v: 1000, text: "entry", color: "#0284c7", dash: false }, { v: 1010, text: "target +₹10", color: "#059669" }, { v: 997.4, text: "worst −₹2.6", color: "#dc2626" }],
  marks: [{ i: 5, text: "against before target", color: "#dc2626", y: "below" }, { i: 12, text: "target reached", color: "#059669" }, { i: 16, text: "best +₹18.4", color: "#059669" }],
  xLabels: [[0, "09:25"], [10, "10:35"], [20, "15:15"]],
  height: 240,
}), "What the test records for this trade: went against you ₹2.60 before the target (so a ₹3 stop survived, a ₹2 stop did not), reached ₹10 at 10:50, the best it ever gave was ₹18.40 at 11:30, and holding to the square-off gave ₹12.50.")}

<h2>2. What each number means</h2>
${L.table(["Number", "Meaning", "Why you care"], [
  ["Vol vs usual / Place", "Volume vs usual at the signal candle's close, and the stock's rank among that day's stocks by it.", "The owner's routine takes the busiest stocks first; the <i>Place</i> filter shows whether the top 5 do better than the rest."],
  ["Side", "LONG after a green 2nd candle, SHORT after a red one. A doji (no body) or a tiny body is skipped.", ""],
  ["With the trade (best)", "The most the price went your way, in ₹ and %, and when.", "How much was on the table."],
  ["Against (worst)", "The most it went against you, and when.", "Where a stop would have been hit."],
  ["Reached / minutes to target", "Whether the ₹ target was reached and after how many minutes.", "The hit rate of the rule."],
  ["Against before target", "How far it went against you <b>before</b> it reached the target.", "The stop a winning trade needed: the most useful number here."],
  ["Square-off ₹", "What holding to 15:15 would have made.", "The do-nothing alternative."],
  ["Trail columns", "What a trailing stop of 0.25 %, 0.5 % … of the entry behind the best price would have made, judged on 1-minute candles the cautious way.", "Which trail distance keeps the gains."],
])}

<h2>3. How to use it</h2>
${L.screen("Second candle test", [
  `${L.chip("Stocks: Nifty 50", true)} ${L.chip("Last 10 days")} ${L.chip("Candle 5 min")} ${L.chip("Candle no. 2")} ${L.chip("Top 20 by volume")} ${L.chip("Target ₹10")} ${L.chip("Square-off 15:15")} ${L.btn("Run the test", true)}`,
  `<b>Answer:</b> <span>37 of 60 trades (62%) reached ₹10; on average +₹14.5 with, ₹7.5 against. Before the target the trades went against you ₹2.8 on average and ₹6.5 at most. Best trailing stop: 0.75% (₹41 a share in all). Best stop with a ₹10 target: ₹8.</span>`,
  `${L.chip("Place: top 5")} ${L.chip("Side: both")} ${L.chip("Reached: all")} ${L.chip("Target between 09:25-11:00")} ${L.chip("Stop test: target ₹10 / stop ₹5")} <span>→ 21 target first, 9 stop first, 5 neither · +₹165 a share</span>`,
])}
${L.steps([
  "Pick the <b>stocks</b>, the number of <b>days</b> (10-20 to trust a percentage), the candle size and <b>which candle</b> (the 2nd by default), the <b>target</b> and the trailing distances. Press <b>Run the test</b> after hours (on the desk it is refused in market hours unless backtests are allowed then).",
  "Read the <b>answer lines</b> first: hit rate, average with / against, against-before-target, the best trailing stop and the best stop for your target.",
  "Use the <b>filters</b>: Place (top 5 / 10 / 20 by volume), Side, Day, Reached, time windows. Every number recomputes from the rows, so you can ask \"do the top 5 by volume do better?\" in one click.",
  "Try the <b>target + stop test</b>: type any target and stop; the table says how many hit the target first, the stop first, or neither, and the total ₹ a share. The per-level minute grids make this instant.",
  "Sort the <b>per-stock table</b> to see which stocks the rule suits; <b>Arm ticked</b> puts them on a practice bot. To trade the rule itself, set the bot's <i>Enter on: Second candle</i> in Settings (its own lesson).",
])}

<h2>4. Reading the example above</h2>
${L.box("", "", `62 % of trades reached ₹10 and the average trade went ₹14.5 with you but ₹7.5 against. The key line: <b>before reaching the target the winners went against you ₹2.8 on average and ₹6.5 at most</b>. A ₹3 stop would have cut many winners; a ₹8 stop would have kept them (the best-stop table confirms ₹8). The trailing table says a 0.75 % trail (₹7.50 on a ₹1,000 stock) kept the most of the ₹14.5 average. So the owner's rule, with a ₹8 stop and a 0.75 % trail, would have made about ₹41 a share over these 60 trades, versus ₹36 holding to the square-off.`)}
${L.box("amber", "Before you trust it", `Ten days is the minimum; twenty is better. One wild day (a budget day, a result day) can make a rule look brilliant or hopeless. Check the <i>By day</i> table: a rule that only won on one day is not a rule.`)}

<h2>5. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Turns \"I think the second candle works\" into numbers on your own stocks and days.</li><li>Tells you the stop a winner needed, which no chart shows.</li><li>Any target / stop pair is judged without a new run.</li></ul>`],
  ["Limits", `<ul><li>Fills are assumed at the next candle's open; real fills slip a little.</li><li>No charges are taken off the ₹ a share; a ₹2 win is a loss after charges.</li><li>Practice arithmetic only; nothing is ordered.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["Second candle", "The 09:20-09:25 candle on 5-minute candles. Green = buy, red = sell short, at its close."],
  ["Volume vs usual", "Shares traded by the signal candle's close against the same minutes on earlier days."],
  ["Against before target", "How far the trade went against you before it reached the target: the stop a winner needed."],
  ["Trailing stop", "A stop that follows the best price at a fixed distance and never moves back."],
  ["Square-off", "Closing the trade at 15:15 whatever the price."],
])}
`,
    }),
};
