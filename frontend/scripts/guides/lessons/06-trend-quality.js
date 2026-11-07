const L = require("../lib");

const trendy = [1000, 1001.2, 1002.5, 1003.1, 1004.4, 1005.9, 1006.6, 1007.8, 1009, 1009.8, 1011, 1012.3, 1013.1, 1014.5, 1015.2, 1016.6];
const choppy = [1000, 1002, 999.5, 1001.8, 999, 1001.5, 1000.2, 998.8, 1001.6, 999.4, 1001.9, 999.8, 1001.2, 999.6, 1001.7, 1000.1];
module.exports = {
  id: "scalp-trend-quality",
  file: "scalp-trend-quality-guide.pdf",
  title: "Trend quality: trending or choppy today?",
  html: () =>
    L.page({
      title: "Trend quality: trending or choppy today?",
      lead: "The most useful check before arming anything. A trending stock moves one way and pays a simple bot; a choppy one zig-zags and gives false signal after false signal. This tab reads each stock's day and labels it, with a strong-trend score, the phase it is in now, and alerts for the quiet runners.",
      body: `
${L.box("blue", "Why crosses are the clue", `The bot trades the cross of two average lines. In a trend the lines cross once and then stay apart for an hour or more. In chop they cross every ten minutes and the bot buys the top and sells the bottom. So <b>counting today's crosses</b> is a direct measure of how the bot would have fared on that stock today.`)}

<h2>1. The picture: trending versus choppy</h2>
${L.fig(L.candles({ title: "Trending: 1 cross all morning, long run since", candles: L.series(trendy, 0.3), lines: { sma9: L.sma(trendy, 3), sma21: L.sma(trendy, 7) }, xLabels: [[0, "09:30"], [8, "10:10"], [15, "10:45"]], height: 200 }), "")}
${L.fig(L.candles({ title: "Choppy: the lines cross again and again", candles: L.series(choppy, 0.3), lines: { sma9: L.sma(choppy, 3), sma21: L.sma(choppy, 7) }, xLabels: [[0, "09:30"], [8, "10:10"], [15, "10:45"]], height: 200 }), "Same averages, two moods. The bot makes money on the first and loses on the second.")}

<h2>2. The labels and columns, in plain words</h2>
${L.table(["Column", "Meaning", "What is good"], [
  ["Score", "<b>Trending</b>: 0-1 crosses today and a long run (over an hour) since the last one. <b>Choppy</b>: 4 or more crosses, or crosses on average less than 15 minutes apart. <b>Mixed</b>: everything else.", "Trending for the SMA bot; Choppy only for the sideways bot (Bot 2)."],
  ["Crosses today", "How many times the two lines crossed on today's closed candles, on the candle size chosen.", "0 or 1."],
  ["Last cross (min)", "Minutes since the last cross.", "Longer is better: the current side has held."],
  ["Avg gap (min) / Avg move %", "Average minutes and average price move between consecutive crosses.", "Big moves between few crosses."],
  ["Strong trend", "<b>Strong bullish</b> / <b>Strong bearish</b> when 6 of 7 checks pass (see below), with a 0-100 score.", "A strong trend is the cleanest kind of trending."],
  ["Phase", "The stretch the stock is in now (bull / bear / chop), since when, and a strip of the whole day.", "A bull or bear phase that started recently."],
  ["Trend age / SMA gap / Rank", "With <i>Trend on</i> ticked: how long every ticked candle size has pointed the same way, the average gap, and a rank (young trend with a wide gap ranks first).", "Low rank number = fresh and wide."],
  ["First seen", "The earliest saved scan today in which the stock passed the filters you have set now.", "Tells you how long it has qualified."],
])}
<h3>The seven strong-trend checks (for an up trend; a down trend is the mirror)</h3>
<ul>
<li><b>Stack</b>: price above the fast average, fast above the slow.</li>
<li><b>VWAP</b>: price and the slow average above today's average price.</li>
<li><b>Slope</b>: both averages rising over the last 3 candles.</li>
<li><b>Fan</b>: the gap at least 0.10 % and not shrinking by a third.</li>
<li><b>Hold</b>: at least 75 % of the last 12 closes above the fast average.</li>
<li><b>Structure</b>: higher highs and higher lows.</li>
<li><b>Run</b>: no cross in the last 12 candles.</li>
</ul>
<p>Before 10:00 a shorter <i>morning</i> version runs on today's candles only (volume since 09:15 counts instead of structure, and 5 of 7 is enough).</p>

<h2>3. How to use it</h2>
${L.screen("Trend quality — how choppy each stock has looked today on the 5-minute candle", [
  `${L.chip("Stocks: Armed", true)} ${L.chip("Candle: 5 min")} ${L.chip("Show: Trending only")} ${L.chip("Trend on: 5 min · 15 min · Same way")} ${L.chip("Auto re-scan every 30 min ✓")} ${L.btn("Scan now", true)}`,
  `<span>☐</span><b>ASIANPAINT</b> <span class="pill p-green">TRENDING</span> <span>1 cross</span> <span>last cross 85 min</span> <span class="g">STRONG BULLISH 86</span> <span>phase BULL since 10:05 +1.2%</span> <span>rank 2</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>ITC</b> <span class="pill p-red">CHOPPY</span> <span>6 crosses</span> <span>avg 11 min apart</span> <span>avg move 0.08%</span> <span>phase CHOP</span> ${L.btn("Arm")}`,
])}
${L.steps([
  "Choose the <b>candle</b> your bot trades on and the <b>stock lists</b> (Armed stocks by default: quick enough to re-run every 30 minutes).",
  "Press <b>Scan now</b>, or tick <b>Auto re-scan</b> and the server repeats it every N minutes during market hours without a browser open.",
  "Set <b>Show</b> to <i>Trending only</i> or <i>Strong trend</i>. Add <b>Trend on</b> with two candle sizes and <i>Same way</i> to keep only stocks trending on both, with a rank.",
  "Arm the top of the list; <b>unarm</b> armed stocks that have turned Choppy (the bot's Stocks table on the terminal shows the same label).",
  "<b>Quiet runner alert</b>: a stock that had crosses earlier and has now been quiet for an hour gets a 🔔 and a Telegram; <b>Trend rank alert</b> sends the top ranks once a day. Both are watch-only.",
  "For a trend hand-off, Bot 1's Settings → 7. Advanced can move a stock to Bot 3 on a strong trend, and the choppy ones to Bot 2.",
])}

<h2>4. A worked example</h2>
${L.box("", "", `ITC at 11:30 on 5-minute candles: 6 crosses today, on average 11 minutes apart, average move between crosses 0.08 %. Label: <b>CHOPPY</b>. A bot that traded every cross would have opened six trades of about ₹0.30 a share each way, most of them losing after charges. Unarm it, or hand it to Bot 2 (gap mode, which waits for the lines to open up before it trades).
<br><br>ASIANPAINT at the same time: 1 cross at 10:05, 85 minutes ago, and all seven strong-trend checks pass (score 86). Label: <b>TRENDING, STRONG BULLISH</b>. The one trade the bot opened at 10:10 is still running.`)}

<h2>5. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Keeps the bot off the stocks that would whipsaw it.</li><li>The strong-trend score and the phase strip show how clean the move is, not just that it exists.</li><li>Auto re-scan and the alerts work without a browser.</li></ul>`],
  ["Limits", `<ul><li>A label describes today so far; a trend can end at any minute.</li><li>Counts need enough candles: early in the day most stocks read Mixed.</li><li>Information only; nothing is ordered.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["Cross", "The fast average crossing the slow one."],
  ["Trending / Mixed / Choppy", "Few crosses and a long run / in between / many crosses close together."],
  ["Strong trend", "6 of the 7 checks pass; the score is 0-100."],
  ["Phase", "The stretch the stock is in now: bull, bear or chop, from today's candles."],
  ["Quiet runner", "A stock that crossed earlier today and has been quiet for an hour or more since."],
  ["Hand-off", "Moving a stock to another bot with that bot's settings (Bot 2 for chop, Bot 3 for trends)."],
])}
`,
    }),
};
