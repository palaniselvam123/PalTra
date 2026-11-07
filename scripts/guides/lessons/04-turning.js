const L = require("../lib");

// A falling stock (fast under slow) whose last three candles turn green and close back above the fast line.
const closes = [1010, 1008.5, 1007.2, 1006, 1005.1, 1004.3, 1003.8, 1003.2, 1002.9, 1002.4, 1002.1, 1002.9, 1003.8, 1004.9, 1005.6];
module.exports = {
  id: "scalp-turning",
  file: "scalp-turning-guide.pdf",
  title: "Turning: a reversal before the lines cross",
  html: () =>
    L.page({
      title: "Turning: a reversal before the lines cross",
      lead: "A stock that has been falling all morning, whose last few candles turn green and climb back over its fast average line, is often turning up before the two average lines cross. This tab finds those stocks (and the mirror image for a rising stock turning down), so you see a turn early.",
      body: `
${L.box("blue", "The owner's example this was built on", `ICICI Bank one afternoon: the fast average (SMA 9) had been under the slow one (SMA 21) since the morning, so the trend was down. From 14:15 three green candles closed back above SMA 9. The lines only crossed at 14:40, twenty-five minutes later. The candles had turned first.`)}

<h2>1. The picture</h2>
${L.fig(L.candles({
  title: "Falling stock, candles turning up before the cross",
  candles: L.series(closes, 0.3),
  lines: { sma9: L.sma(closes, 3), sma21: L.sma(closes, 7) },
  marks: [{ i: 11, text: "1st green", color: "#059669" }, { i: 13, text: "3 in a row, back above SMA 9", color: "#059669" }],
  xLabels: [[0, "13:00"], [7, "13:35"], [14, "14:10"]],
  height: 230,
}), "The trend is down (red fast line under blue slow line). Three green candles in a row close back above the fast line: the stock is listed as turning up. The lines themselves have not crossed yet. (Short averages are drawn so the picture fits.)")}

<h2>2. How it works, in plain words</h2>
<ul>
<li><b>Trend</b>: which way the fast average points against the slow one on the candle size you pick, and how long it has (since the last cross, counted across days).</li>
<li><b>Turning</b>: the last N closed candles today (3 by default, up to 10) all went against that trend. The rule can be <i>colour</i> (green candles in a down trend), <i>closes</i> (each close higher than the one before) or <i>both</i>.</li>
<li><b>Past SMA 9</b> (on by default): the last close must also be back on the far side of the fast average. A bounce that stays under the line is not a turn.</li>
<li><b>Gap</b>: how far apart the lines are now and how far they were when the turn started; <b>closed</b> is how much of that gap the turn has eaten. <b>Cross within</b> estimates the minutes to the cross at this pace.</li>
<li>Also shown: the move since the turn, today's move, and the stock's speed and volume.</li>
</ul>

<h2>3. How to use it</h2>
${L.screen("Turning — candles turning against the trend before the SMAs cross", [
  `${L.chip("Candle: 5 min", true)} ${L.chip("Candles in a row: 3")} ${L.chip("Rule: colour")} ${L.chip("Past SMA 9 ✓")} ${L.chip("Telegram me new ones")} ${L.btn("Scan now", true)}`,
  `<span>☐</span><b>ICICIBANK</b> <span class="r">trend DOWN since 09:40</span> <span class="g">turning UP · 3 green</span> <span>gap 0.18% → 0.05% (72% closed)</span> <span>cross in ~10 min</span> <span>since the turn +0.4%</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>TATASTEEL</b> <span class="g">trend UP since yesterday</span> <span class="r">turning DOWN · 4 red</span> <span>gap 0.31% → 0.12% (61% closed)</span> <span>cross in ~18 min</span> ${L.btn("Arm")}`,
])}
${L.steps([
  "Pick the candle size your bot trades on and press <b>Scan now</b> (mid-morning and in the afternoon are the useful times; at 09:30 there is no trend yet).",
  "Read the rows soonest-cross first. <b>Way</b> filters turning up or down; <b>Trend at least N candles</b> keeps only well-established trends (a turn after a long trend means more); <b>Cross within</b> hides slow turns.",
  "Check the turn has volume and speed behind it (the row shows both). A turn on no volume is often a pause, not a reversal.",
  "<b>Arm</b> the stock on a practice bot. The bot's normal entry is the cross itself, which comes a little later. To trade the turn itself, the bot has its own <i>Early reversal entry</i> (Settings → 1. When to enter): it takes the same picture as a signal, with every check and the stop applying.",
  "Tick <b>Telegram me new ones</b> to be told of new turns during market hours (each stock once a day per direction).",
])}

<h2>4. A worked example</h2>
${L.box("", "", `ICICIBANK, 5-minute candles, 14:30: SMA 9 ₹1,380.2 has been under SMA 21 ₹1,382.7 since 09:40 (trend DOWN for 58 candles). The 14:15, 14:20 and 14:25 candles are green and the last close ₹1,381.6 is above SMA 9. Gap at the turn 0.18 %, gap now 0.05 %: 72 % of it has closed in three candles, so at this pace the cross is about two candles (10 minutes) away. Since the turn the price is up 0.4 %.
<br><br>Read it as: \"a down trend that lasted all day is bending up now; the lines will likely cross around 14:40.\" The bot armed on it buys on that cross; with Early reversal entry on, it would already have bought on the third green candle.`)}

<h2>5. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Shows reversals 10-25 minutes before the cross, so you are ready for the signal.</li><li>\"Closed %\" and \"cross within\" turn a hunch into a number.</li><li>The bot's own Early reversal entry trades the very same picture.</li></ul>`],
  ["Limits", `<ul><li>Three green candles in a down trend are often just a pause before the fall continues; the stop is what protects you.</li><li>Candles only; it never places an order.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["Trend (SMA side)", "Fast average above the slow one = up trend, below = down trend."],
  ["Counter candles", "Closed candles that go against the trend: green ones in a down trend, red in an up trend."],
  ["Past SMA 9", "The last close is back on the other side of the fast average line."],
  ["Gap closed %", "How much of the gap between the lines the turn has eaten so far."],
  ["Cross within", "Minutes until the lines meet at the current pace. An estimate."],
  ["Early reversal entry", "The bot's own setting that trades this picture as a signal, with every check and the stop."],
])}
`,
    }),
};
