const L = require("../lib");

const closes = [940, 948, 955, 951, 962, 970, 966, 975, 981, 978, 988, 995, 992, 1001, 1008, 1015];
module.exports = {
  id: "scalp-52-week-high",
  file: "scalp-52-week-high-guide.pdf",
  title: "52-week high: stocks at their yearly high or low",
  html: () =>
    L.page({
      title: "52-week high: stocks at their yearly high or low",
      lead: "A stock trading at its highest price of the last year has no recent buyers sitting above it waiting to sell at break-even, so a push there can run. This tab lists how far each stock is from its yearly high and low, which ones broke out today, and how fresh the high is.",
      body: `
${L.box("blue", "The idea in one picture", `Think of everyone who bought the stock in the last year. If the price is below last year's high, many of them are losing and will sell when the price gets back to their buy price: resistance. At a <b>new 52-week high</b> nobody is waiting to get out, so the only sellers are profit-takers. The mirror holds at a 52-week low for a short.`)}

<h2>1. The picture</h2>
${L.fig(L.candles({
  title: "Daily candles: a breakout above the yearly high",
  candles: L.series(closes, 4),
  levels: [{ v: 1001, text: "old 52-week high", color: "#7c3aed" }],
  marks: [{ i: 13, text: "touches the high", color: "#7c3aed" }, { i: 15, text: "new high: breakout", color: "#059669" }],
  xLabels: [[0, "3 weeks ago"], [8, "last week"], [15, "today"]],
  height: 220,
}), "Daily candles (one per day). The purple line is the highest high of the 52 weeks before today. Today's high reaching it is a breakout; the row says \"new 52-week high today\".")}

<h2>2. The columns, in plain words</h2>
${L.table(["Column", "Meaning", "How to read it"], [
  ["52-week high / low", "The highest daily high and lowest daily low in the 52 weeks before today, with their dates (Groww's daily candles).", "A high set last week is a live level; one from 11 months ago is history."],
  ["From high / From low", "How far the last price is from each level, in %. Negative \"from high\" = above it (a new high today).", "Within 1-2 % of the high = knocking on the door; negative = through it."],
  ["Since high", "Trading sessions since the high was set.", "0-5: fresh. 100+: the stock has been under its high for months."],
  ["Day % / Year %", "Today against the previous close; today against the close a year ago.", "A big year % with a fresh high is a stock in a long up trend."],
  ["New high / new low today", "Today's high reached the old 52-week high (or today's low the old low).", "Breakouts are listed first."],
])}

<h2>3. How to use it</h2>
${L.screen("52-week high — stocks at or near their yearly high", [
  `${L.chip("Stocks: All F&O", true)} ${L.chip("Show: New 52-week high today")} ${L.chip("Band: within 2%")} ${L.btn("Scan now", true)}`,
  `<span>☐</span><b>BEL</b> <span class="g">new 52-week high today</span> <span>high ₹398.50 (today)</span> <span>from high −0.4%</span> <span>since high 0</span> <span class="g">day +2.8%</span> <span class="g">year +61%</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>HAL</b> <span>within the band</span> <span>high ₹5,120 (12 days ago)</span> <span>from high 1.1%</span> <span>since high 12</span> <span class="g">day +1.2%</span> ${L.btn("Arm")}`,
])}
${L.steps([
  "Press <b>Scan now</b>. Each stock needs one daily-candle download, so a full F&amp;O scan takes a couple of minutes (Stop keeps what was read).",
  "<b>Show</b>: <i>New 52-week high today</i> for breakouts, <i>within the band</i> for stocks a few % under the high (or above the low), <i>every stock read</i> for the full list. <b>Band</b> sets the %.",
  "Prefer a <b>fresh</b> high with a good day % and volume (check Most active): a breakout nobody is trading is weak.",
  "<b>Arm</b> on a practice bot. With the bot's <i>Enter only at the 52-week high</i> check on (Settings → 2. Checks before entering), it buys only a candle that closes at the yearly high, on its own signal.",
])}

<h2>4. A worked example</h2>
${L.box("", "", `BEL: 52-week high ₹398.50 set today; last price ₹400.10 (from high −0.4 %, so above it); since high 0 sessions; day +2.8 %; year +61 %. A clean breakout on a stock in a long up trend. Arm it; the bot's own cross and checks decide the entry, and the stop decides the risk. If instead the row said \"from high 1.1 %, since high 12\" (HAL), the stock is near the level but has not broken it: wait for the breakout or let the bot's 52-week check do the waiting.`)}

<h2>5. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Breakouts above a yearly high have no overhead sellers: moves can run.</li><li>\"Since high\" separates fresh levels from stale ones.</li><li>The bot has a matching entry check, so the idea can be traded by rule.</li></ul>`],
  ["Limits", `<ul><li>Daily candles: the level is a day-level idea, not a minute-level timing tool. Combine with Trend quality for the timing.</li><li>A breakout can fail the same day; the stop is the protection.</li><li>Nothing is ordered.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["52-week high / low", "The highest / lowest price of the last year, from daily candles."],
  ["Breakout", "Today's price going through the old high (or low)."],
  ["From high %", "Distance below the high; negative = above it."],
  ["Since high", "Trading days since the high was set."],
  ["Enter only at the 52-week high", "The bot's own check that takes a buy only on a close at the yearly high."],
])}
`,
    }),
};
