const L = require("../lib");

// A bullish morning that kept going: 09:15 → 10:30 up 0.8 %, then on to +1.6 % at the close.
const bull = [1000, 1001.5, 1002.4, 1004.0, 1005.2, 1006.8, 1008.0, 1007.1, 1009.0, 1010.5, 1012.2, 1013.8, 1014.6, 1016.0];
// A bearish morning bought back: down 0.9 % by 10:30, low at 11:30, then back up to the open by the close.
const bear = [1000, 998.6, 997.2, 996.0, 994.5, 993.0, 991.0, 990.2, 988.5, 989.8, 992.0, 994.6, 997.1, 999.8];
module.exports = {
  id: "scalp-morning-test",
  file: "scalp-morning-test-guide.pdf",
  title: "Morning test: does the morning tell you the rest of the day?",
  html: () =>
    L.page({
      title: "Morning test: does the morning tell you the rest of the day?",
      lead: "The owner's idea: a stock that rises from the open keeps going (good to hold, even overnight), and a stock that falls from the open gets bought back up before the close. This tab checks the idea on past days of your own stocks and gives the answer in plain lines, with the numbers behind it.",
      body: `
${L.box("blue", "The question, put simply", `At 10:30 you can see whether a stock had a good or a bad morning. Is that worth anything? If bullish mornings end higher on 7 days out of 10, the morning is a signal; if they end higher on 5 of 10, it is a coin toss and you should not trade it.`)}

<h2>1. The pictures</h2>
${L.fig(L.candles({
  title: "A bullish morning that kept going: +0.8% by 10:30, +1.6% at the close",
  candles: L.series(bull, 0.4),
  lines: { sma9: L.sma(bull, 3), sma21: L.sma(bull, 7) },
  marks: [{ i: 5, text: "10:30: BULLISH +0.8%", color: "#059669" }, { i: 13, text: "close +1.6%", color: "#059669" }],
  xLabels: [[0, "09:15"], [5, "10:30"], [13, "15:30"]],
  height: 220,
}), "")}
${L.fig(L.candles({
  title: "A bearish morning bought back: −0.9% by 10:30, low at 11:30, back to the open by the close",
  candles: L.series(bear, 0.4),
  lines: { sma9: L.sma(bear, 3), sma21: L.sma(bear, 7) },
  marks: [{ i: 5, text: "10:30: BEARISH −0.9%", color: "#dc2626", y: "below" }, { i: 8, text: "day's low 11:30", color: "#dc2626", y: "below" }, { i: 13, text: "bounced +1.1% off the low", color: "#059669" }],
  xLabels: [[0, "09:15"], [5, "10:30"], [13, "15:30"]],
  height: 220,
}), "Two mornings, two endings. The test counts how often each ending happens on your stocks and days, and how big the moves were.")}

<h2>2. The three steps the test takes</h2>
${L.table(["Step", "What is read", "Why"], [
  ["1. Label the morning", "From 09:15 to the time you pick (10:00, 10:30, 11:00 or 12:00): <b>bullish</b> when the price rose at least 0.5 % (your choice) from the open, SMA 9 is above SMA 21 on the last closed candle, and the path was fairly straight (efficiency ≥ 0.3); <b>bearish</b> is the mirror; everything else <b>sideways</b>.", "A clean, one-way morning is the idea being tested; a wobble is not."],
  ["2. Watch the rest of the day", "From the pick time to the close: did it end higher? The low and high after, and when. The bounce from the day's low (and the give-back from the high).", "Answers \"keeps going\" and \"bought back\"."],
  ["3. And the next day", "The next session's gap at the open and its close against today's close (only once it has closed); NSE's delivery % for the day.", "What holding overnight (delivery or MTF) would have done, and whether real buyers kept the shares."],
])}

<h2>3. The summary and the answer lines</h2>
${L.table(["Line", "Meaning", "Example"], [
  ["Days the price ended higher than at 10:30", "Of the bullish (or bearish) mornings, the share that closed above the pick-time price.", "Bullish: 7 of 10 (70 %). Bearish: 4 of 9 (44 %)."],
  ["Average / median move to the close", "The usual move from the pick time to the close, signed. The median ignores one wild day.", "Bullish +0.6 % average, +0.4 % median."],
  ["Bounce from the day's low / days it climbed 1 % or more off its low", "How far the price came back up from its lowest point after the pick time, and how often that was 1 % or more.", "Bearish: average bounce 0.9 %, 5 of 9 days ≥ 1 %."],
  ["When the low / high came", "Half-hour buckets of the day's low and high times.", "Bearish mornings: the low came 11:00-12:00 on 6 of 9 days."],
  ["Next day gap / close", "The overnight gap and the next close, for the hold-overnight question.", "Bullish: next open +0.3 %, next close +0.5 %."],
  ["Delivery %", "NSE's share of traded shares that were kept that day, and the closed-higher split above / below the median delivery.", "Days with delivery over 45 % closed higher more often."],
])}
${L.box("", "", `<b>An answer line reads like a sentence</b>: \"Bullish mornings ended higher than at 10:30 on 7 of 10 days (70 %); average move +0.6 %.\" A warning appears under 5 days: too few to mean anything.`)}

<h2>4. How to use it</h2>
${L.screen("Morning test", [
  `${L.chip("Stocks: Nifty 50", true)} ${L.chip("Last 10 days")} ${L.chip("Morning until 10:30")} ${L.chip("Candle 5 min")} ${L.chip("Min move 0.5%")} ${L.chip("Delivery % (NSE) ✓")} ${L.btn("Run the test", true)}`,
  `<b>Answer:</b> <span>Bullish mornings (31): ended higher on 21 (68%), avg +0.6%. Bearish mornings (24): ended higher on 11 (46%), bounced ≥1% off the low on 14 (58%), low mostly 11:00-12:00.</span>`,
  `<span>☐</span><b>HDFCBANK</b> <span>8 days</span> <span class="g">bullish 5 · ended higher 4</span> <span class="r">bearish 2 · bounced 2</span> <span>deliv 52%</span> ${L.btn("Arm")}`,
])}
${L.steps([
  "Pick the <b>stocks</b>, the number of <b>days</b> (10-30), the <b>morning cut-off</b> (10:30 is a good start), the candle and the <b>minimum move</b>. Tick <b>Delivery %</b> for the overnight picture. Press <b>Run the test</b> (after hours on the desk; any time on the backtest server).",
  "Read the <b>answer lines</b>. A share near 50 % means the morning tells you nothing; 65 %+ on 10+ days is worth acting on.",
  "Open <b>How to read these numbers (an example)</b> on the tab the first time: it walks through one row.",
  "Sort the <b>per-stock table</b> (search, \"days at least\"), tick the stocks where the idea holds and <b>Arm ticked</b> on a practice bot with \"Join the morning trend\" on, so the bot enters a running morning trend.",
  "Try two cut-offs (10:00 and 11:00): a later one is surer but leaves less of the day to earn.",
])}

<h2>5. A worked example</h2>
${L.box("", "", `Nifty 50, 10 days, morning until 10:30. Bullish mornings: 31, ended higher on 21 (68 %), average +0.6 %, next day close +0.4 %. Bearish mornings: 24, ended higher on only 11 (46 %), but bounced 1 % or more off the low on 14 (58 %) and the low came 11:00-12:00 on most days. Reading: buying a bullish morning at 10:30 and holding to the close worked about two days in three; shorting a bearish morning did not pay, but buying the 11:00-12:00 dip of a bearish morning often caught a 1 % bounce. Trade the first with Bot 3 and \"Join the morning trend\"; the second needs a stop under the day's low.`)}
${L.box("amber", "Before you trust it", `Ten days of one month is one mood of the market. Run it again next month. A share of 60 % on 10 days is 6 against 4: one more bad day makes it 50 %. Fills, slippage and charges are not in these percentages.`)}

<h2>6. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Tests a common belief on your own stocks instead of on a tip.</li><li>Tells you <i>when</i> the low usually comes, not only whether it bounced.</li><li>The next-day and delivery columns answer the overnight question too.</li></ul>`],
  ["Limits", `<ul><li>A label at 10:30 is only as good as the morning's candles: a news spike can fake a trend.</li><li>Past days only; no slippage or charges.</li><li>Practice arithmetic; arming is a separate button and nothing is ordered.</li></ul>`],
])}

<h2>7. Words used here</h2>
${L.words([
  ["Bullish / bearish / sideways morning", "Up at least the minimum move with the fast SMA above the slow one / the mirror / neither."],
  ["Efficiency", "How straight the path was: net move ÷ the sum of every candle's move (1 = a straight line, 0.3 = wobbly)."],
  ["Bounce from the low", "How far the price rose from its lowest point after the pick time."],
  ["Give-back from the high", "How far it fell from its highest point after the pick time."],
  ["Delivery %", "NSE's evening figure: the share of traded shares that were kept overnight."],
  ["MTF", "Margin trading facility: holding a stock overnight on borrowed money through the broker."],
])}
`,
    }),
};
