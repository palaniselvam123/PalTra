const L = require("../lib");

// Yesterday afternoon rising (fast above slow), a gap up at today's open, the trend continues.
const closes = [980, 981.5, 983, 984.2, 985.8, 987, 988.1, 989.5, 990.4, 991.6, /* today */ 996, 997.2, 998.5, 999.8, 1001, 1002.3];
module.exports = {
  id: "scalp-carry-over",
  file: "scalp-carry-over-guide.pdf",
  title: "Carry-over: did yesterday's trend carry on today?",
  html: () =>
    L.page({
      title: "Carry-over: did yesterday's trend carry on today?",
      lead: "A stock that closed yesterday in a clear trend often keeps going for a while this morning. This tab shows, for each stock, which way its average lines pointed at yesterday's close, how today opened against that, and whether the trend continued, reversed, or reversed and came back.",
      body: `
${L.box("blue", "The idea", `Trends do not reset at the closing bell. If the fast average was above the slow one when yesterday ended, buyers were in charge; the question at 09:30 is whether they still are. The scan answers it with the same averages the bot uses, run straight across the two days.`)}

<h2>1. The picture</h2>
${L.fig(L.candles({
  title: "Yesterday's up trend, a gap up at the open, continued today",
  candles: L.series(closes, 0.35),
  lines: { sma9: L.sma(closes, 3), sma21: L.sma(closes, 7) },
  marks: [{ i: 9, text: "yesterday's close", color: "#334155" }, { i: 10, text: "gap up ✓", color: "#059669" }],
  xLabels: [[2, "yesterday 14:30"], [9, "15:25"], [12, "today 09:30"], [15, "09:50"]],
  height: 230,
}), "The fast line was above the slow one at yesterday's close (trend up). Today opened higher (a gap with the trend) and the lines have not crossed against it: status CONTINUED. (Short averages are drawn so the picture fits.)")}

<h2>2. The columns, in plain words</h2>
${L.table(["Column", "Meaning", "How to read it"], [
  ["Yesterday", "UP or DOWN: which way the fast line pointed against the slow one on yesterday's last candle, and since when.", "A trend that started at 11:00 yesterday is more established than one that started at 15:10."],
  ["Gap at open", "Today's first price against yesterday's close, in %. ✓ when it went the trend's way.", "A gap with the trend says overnight news agreed with it."],
  ["Status", "<b>Continued</b>: no cross against yesterday's side on today's closed candles. <b>Reversed</b>: crossed against it (time shown) and still against. <b>Back</b>: crossed against it, then came back.", "Continued and Back are the ones to look at; Reversed means yesterday's story is over."],
  ["Deviation", "The last price against yesterday's closing fast average, in ₹ and %.", "How stretched the price is from where the averages were."],
  ["Moved its way", "Change since yesterday's close, signed the trend's way, and when it went that way.", "+0.8 % moved its way = it is up 0.8 % in an up trend."],
  ["Best / Worst", "The furthest today went with the trend and against it, from yesterday's close, with the times.", "Worst −0.3 % at 09:22 then Best +0.9 % at 10:05: a small early dip, then the move."],
  ["Deliv %", "NSE's delivery share for the last published day (evening data).", "High delivery with a rise suggests real buyers held the shares overnight."],
])}

<h2>3. How to use it</h2>
${L.screen("Carry-over — did yesterday's SMA trend carry on today?", [
  `${L.chip("Candle: 5 min", true)} ${L.chip("Show: Continued")} ${L.chip("Yesterday: Up ↑")} ${L.chip("Gap: with the trend")} ${L.chip("Deviation at least 0.3%")} ${L.btn("Scan now", true)}`,
  `<span>☐</span><b>LT</b> <span class="g">UP since 11:05</span> <span class="g">gap +0.45% ✓</span> <span class="pill p-green">CONTINUED</span> <span>moved its way +0.9% at 09:35</span> <span>best +1.1% 10:02 · worst −0.2% 09:18</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>ONGC</b> <span class="r">DOWN since yesterday 13:40</span> <span class="r">gap +0.6% ✗</span> <span class="pill p-red">REVERSED 09:35</span> <span>moved its way −0.8%</span> ${L.btn("Arm")}`,
])}
${L.steps([
  "Press <b>Scan now</b> a little after the open (09:35 to 10:00 is the useful window) on the candle size your bot trades.",
  "Set <b>Show</b> to <i>Continued</i>, <b>Gap</b> to <i>with the trend</i>. These are stocks where overnight and the opening both agreed with yesterday's direction.",
  "Use <b>Yesterday: Up</b> for buy candidates, <b>Down</b> for sell candidates, and <b>Deviation at least</b> to skip stocks that have barely moved.",
  "<b>Arm</b> the ones you like on a practice bot. A bot with \"Join the morning trend\" on can enter a trend that is already running; otherwise it waits for its next cross.",
  "After hours the scan reads the last two sessions, so you can study how yesterday's trends carried into today.",
])}

<h2>4. A worked example</h2>
${L.box("", "", `LT: at yesterday's close the fast average was above the slow one since 11:05 (UP). Today opened +0.45 % (gap with the trend ✓). By 09:50 no closed candle has crossed against it: CONTINUED. It moved its way +0.9 % at 09:35; the worst it went against the trend was −0.2 % at 09:18 and the best +1.1 % at 10:02.
<br><br>Read it as: \"the up trend survived the night and the open; a small 09:18 dip was the only scare.\" A bot armed on LT with Join the morning trend would have entered around 09:35; its stop below the 09:18 low would have held.`)}

<h2>5. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Finds trends that are already running at the open, before any fresh cross.</li><li>Best / Worst with times show how deep the early dip was: the stop a trade would have needed.</li><li>Delivery % adds the overnight picture.</li></ul>`],
  ["Limits", `<ul><li>A CONTINUED stock can still reverse at 10:30; the status is a snapshot.</li><li>Candles only; nothing is ordered.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["Gap at open", "Today's first price against yesterday's close, in %."],
  ["Continued / Reversed / Back", "No cross against yesterday's side / crossed against it / crossed against it and came back."],
  ["Deviation", "Last price minus yesterday's closing fast average, in ₹ and %."],
  ["Delivery %", "Share of a day's traded shares that were kept (delivered), from NSE's evening data."],
  ["Join the morning trend", "The bot's setting to enter a trend that was already running when it started, with every check."],
])}
`,
    }),
};
