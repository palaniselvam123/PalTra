const L = require("../lib");

const closes = [500, 499.6, 499.1, 498.7, 498.5, 498.6, 498.9, 499.3, 499.8, 500.2, 500.7, 501.1, 501.4, 501.9, 502.3, 502.8];
module.exports = {
  id: "scalp-cross-scan",
  file: "scalp-cross-scan-guide.pdf",
  title: "Cross scan: which stocks may signal next",
  html: () =>
    L.page({
      title: "Cross scan: which stocks may signal next",
      lead: "The bot buys or sells when its two average lines cross. This tab lists the stocks whose lines are closing in on each other, soonest first, so you can arm a stock before its signal instead of after it.",
      body: `
${L.box("blue", "In one sentence", `Two smooth lines follow the price: a fast one (SMA 9, the average of the last 9 candles) and a slow one (SMA 21). When the fast line crosses <b>above</b> the slow one the bot buys; when it crosses <b>below</b>, the bot sells short. The Cross scan measures how far apart the lines are now and how fast the gap is closing, and says \"a cross in about N minutes\".`)}

<h2>1. The picture</h2>
${L.fig(L.candles({
  title: "A gap closing towards a bullish cross",
  candles: L.series(closes, 0.3),
  lines: { sma9: L.sma(closes, 3), sma21: L.sma(closes, 7) },
  marks: [{ i: 7, text: "gap closing", color: "#b45309" }, { i: 12, text: "cross: the signal", color: "#059669" }],
  xLabels: [[0, "10:00"], [7, "10:35"], [15, "11:15"]],
  height: 230,
}), "The fast (red) line was below the slow (blue) line while the stock drifted down. As the price turns up the gap closes; the scan lists the stock while the gap is still closing and says roughly when the lines will meet. (Short averages are drawn so the picture fits.)")}

<h2>2. How it works, in plain words</h2>
<ul>
<li>Groww gives 1-minute candles. The scan groups them into the candle size you choose (5 minutes by default) and works out the two averages <b>exactly as the bot does</b>, on closed candles only.</li>
<li><b>Gap %</b> is the distance between the lines as a percentage of the price. <b>Closing / candle</b> is how much of that gap closes each candle, judged on the last few candles.</li>
<li><b>In</b> = gap ÷ closing speed, in minutes: an estimate of when the lines meet <i>if the pace holds</i>. A stock can turn away and never cross.</li>
<li><b>Just-crossed</b> stocks (the lines met in the last few candles) are listed too, marked separately, with the direction of the cross.</li>
<li>Each row also shows the stock's <b>speed</b> (% per minute over the last 10 minutes) and <b>volume</b> today, from the same candles.</li>
</ul>

<h2>3. How to use it, step by step</h2>
${L.screen("Cross scan — Nifty 50 about to cross on the 5-minute candle", [
  `${L.chip("Candle: 5 min", true)} ${L.chip("Direction: Both")} ${L.chip("Crossing within: 15 min")} ${L.chip("Fast movers only")} ${L.btn("Scan now", true)} ${L.btn("Stop")}`,
  `<span>☐</span><b>HDFCBANK</b> <span class="g">▲ bullish</span> <span>in <b>4 min</b></span> <span>gap −0.06%</span> <span>closing 0.02%/candle</span> <span>speed +0.08%/min</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>ITC</b> <span class="r">▼ bearish</span> <span>in <b>11 min</b></span> <span>gap +0.12%</span> <span>closing 0.01%/candle</span> <span>speed −0.03%/min</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>SBIN</b> <span class="g">just crossed ▲ 10:35</span> <span>gap +0.04%</span> ${L.btn("Arm")}`,
])}
${L.steps([
  "Pick the <b>stocks</b> (Armed, All F&amp;O, Nifty 50 …) and the <b>candle</b> size. Use the same candle size your bot trades on, or the scan's \"in N min\" will not match the bot's signal.",
  "Press <b>Scan now</b>. The stocks are read one at a time (a few seconds each), so a full F&amp;O scan takes a couple of minutes; <b>Stop</b> keeps what has been read so far.",
  "Read the top rows: smallest <b>In</b> first. <b>Direction</b> filters bullish (buy signals) or bearish (sell signals); <b>Crossing within</b> hides stocks whose cross is far off.",
  "Before arming, check the stock is not choppy: open <b>Trend quality</b> or tick it and press <i>Read trend quality</i> from Most active. A stock that crosses five times a day gives five false starts.",
  "<b>Arm</b> the ones you like (or tick several and <b>Arm ticked</b>). The bot still waits for the cross on its own candle; arming never buys by itself.",
  "After the close, <b>History</b> keeps this scan: press <i>Check accuracy</i> to see whether the predicted crosses came and how far the stocks moved after them.",
])}

<h2>4. A worked example</h2>
${L.box("", "", `HDFCBANK at 10:31 on 5-minute candles: SMA 9 ₹1,640.10, SMA 21 ₹1,641.10, so the gap is −₹1.00, which is −0.06 % of the price. Over the last three candles the gap closed by about ₹0.33 a candle (0.02 %).
<ul>
<li>₹1.00 ÷ ₹0.33 per candle ≈ 3 candles ≈ <b>15 minutes</b>; the table rounds the pace and says \"in 4 min\" if the last candle closed most of it.</li>
<li>If the price keeps rising, the lines meet around 10:45 and the bot (if HDFCBANK is armed and the bot's checks agree) buys at the start of the next candle.</li>
<li>If the price stalls at ₹1,640, the gap stops closing, the stock drops out of the list, and nothing happens. The scan is a heads-up, not a promise.</li>
</ul>`)}

<h2>5. Benefits, and what it cannot do</h2>
${L.cols([
  ["Why it helps", `<ul><li>You arm a stock a few minutes <b>before</b> its signal, so the bot is already watching when it comes.</li><li>It uses the bot's own arithmetic, so what it calls a cross, the bot calls a cross.</li><li>Speed and volume on each row stop you from arming a sleepy stock.</li></ul>`],
  ["What it cannot do", `<ul><li>It cannot know the cross will happen: a gap can stop closing.</li><li>It does not judge chop. Pair it with Trend quality.</li><li>It never places an order; only the bot does, and only on an armed stock, on its own candle.</li></ul>`],
])}
${L.box("amber", "Common beginner mistakes", `<ul><li>Scanning on 1-minute candles and arming a bot that trades 5-minute candles: the bot sees a different cross at a different time.</li><li>Arming every row. Three to five stocks is enough; more only hides which ones worked.</li><li>Treating \"in 2 min\" as certain and buying by hand. Let the bot take its own signal with its stop in place.</li></ul>`)}

<h2>6. Words used here</h2>
${L.words([
  ["SMA 9 / SMA 21", "The average close of the last 9 / 21 candles, drawn as the fast and slow line."],
  ["Cross", "The fast line crossing the slow one: above = bullish (buy), below = bearish (sell short)."],
  ["Gap %", "Distance between the two lines as % of the price; sign tells which is on top.", "−0.06 % means the fast line is just under the slow one."],
  ["Closing / candle", "How much of the gap closes each candle, judged on the last few."],
  ["In (minutes)", "Gap ÷ closing pace: when the lines meet if the pace holds."],
  ["Just-crossed", "The lines met within the last few candles."],
  ["Speed", "Price change over the last 10 minutes, % per minute, with its sign."],
  ["Closed candle", "A candle whose time is over; the one still forming is never read."],
])}
`,
    }),
};
