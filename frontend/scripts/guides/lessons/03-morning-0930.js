const L = require("../lib");

// Three 5-minute candles 09:15, 09:20, 09:25 for a runner: strong open, then two green candles above VWAP.
const runner = [
  { o: 1000, h: 1004, l: 998.5, c: 1003.2 },
  { o: 1003.2, h: 1006.5, l: 1002.8, c: 1006 },
  { o: 1006, h: 1009, l: 1005.5, c: 1008.4 },
  { o: 1008.4, h: 1011, l: 1008, c: 1010.2 },
];
const chop = [
  { o: 1000, h: 1003, l: 997, c: 999 },
  { o: 999, h: 1002.5, l: 998, c: 1001.5 },
  { o: 1001.5, h: 1002, l: 998.2, c: 998.8 },
  { o: 998.8, h: 1001, l: 997.5, c: 1000.1 },
];
module.exports = {
  id: "scalp-morning-0930",
  file: "scalp-morning-0930-guide.pdf",
  title: "09:30 Morning: runners or chop, from the first three candles",
  html: () =>
    L.page({
      title: "09:30 Morning: runners or chop, from the first three candles",
      lead: "At 09:30 a stock has only three 5-minute candles, too few for the other scans. This tab asks five quick questions of those three candles and tells you which stocks look like runners for the day.",
      body: `
${L.box("blue", "The idea", `The first 15 minutes of the day are the market's opening battle. A stock that comes out of it <b>above its opening range, on heavy volume, above the day's average price, with its averages already fanning out and green candles</b> has every early sign of a trend day. A stock that fails most of these is in the \"chop zone\": wait for it.`)}

<h2>1. The picture: a runner and a chopper at 09:30</h2>
${L.fig(L.candles({ title: "Runner: 4 of 5 checks pass", candles: runner, lines: { vwap: [1001.5, 1003.6, 1005.4, 1007.0] }, levels: [{ v: 1004, text: "09:15-09:30 high", color: "#7c3aed" }], marks: [{ i: 3, text: "break above the range", color: "#059669" }], xLabels: [[0, "09:15"], [1, "09:20"], [2, "09:25"], [3, "09:30"]], height: 210 }), "Each candle closes above the dashed VWAP line, the 09:30 price is above the 09:15-09:30 high, two of three candles are green and the second and third close higher than the first.")}
${L.fig(L.candles({ title: "Chop: 1 of 5 checks pass", candles: chop, lines: { vwap: [1000.2, 1000.4, 1000.3, 1000.2] }, levels: [{ v: 1003, text: "range high", color: "#7c3aed" }, { v: 997, text: "range low", color: "#7c3aed" }], xLabels: [[0, "09:15"], [1, "09:20"], [2, "09:25"], [3, "09:30"]], height: 210 }), "Candles cross the VWAP line both ways, the price is still inside the 09:15-09:30 range, colours alternate. Nothing to do yet.")}

<h2>2. The five checks, in plain words</h2>
${L.table(["Check", "What passes (for a long; a short is the mirror)", "Why it matters"], [
  ["<b>Break</b>", "The latest 1-minute price after 09:30 is above the high of 09:15-09:30 (the opening range).", "A stock that has already left its opening range has buyers behind it."],
  ["<b>Volume</b>", "Shares traded 09:15-09:30 are at least 1.8× the same 15 minutes on the last ten sessions (needs at least two earlier sessions).", "A move on unusual volume is one many traders are joining."],
  ["<b>VWAP</b>", "Each candle closes above the day's average price (VWAP); the 09:20 and 09:25 candles also open above it and no 1-minute dip in them touches it.", "Price staying above the average price means the buyers are winning the morning."],
  ["<b>SMA gap</b>", "On 5-minute candles the fast average is at least 0.10 % above the slow one on the last closed candle.", "The averages have started to fan out: the trend has a shape, not just a jump."],
  ["<b>Candles</b>", "At least two of the three are green, and the 09:25 candle closes above the 09:15 close and above the fast average.", "Follow-through: the move continued after the first burst."],
])}
<p><b>4 or more of 5 = a runner</b> (the row says <span class="pill p-green">RUNNER</span>). Each stock is judged for a long and for a short; the side with more passes is shown. Before 09:30 a row says <i>waiting</i>.</p>

<h2>3. How to use it, step by step</h2>
${L.screen("09:30 Morning — runners or chop, from the first three 5-minute candles", [
  `${L.chip("Stocks: All F&O", true)} ${L.chip("Show: Runners")} ${L.chip("Side: Both")} ${L.btn("Scan now", true)} ${L.btn("Stop")}`,
  `<span>☐</span><b>BAJFINANCE</b> <span class="g">LONG</span> <span class="pill p-green">RUNNER 5/5</span> <span>break ✓ volume ✓ 2.6× VWAP ✓ gap ✓ +0.14% candles ✓</span> <span class="m">range 1,000-1,004</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>HINDALCO</b> <span class="r">SHORT</span> <span class="pill p-green">RUNNER 4/5</span> <span>break ✓ volume ✓ 2.1× VWAP ✓ gap ✗ candles ✓</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>ITC</b> <span class="m">LONG</span> <span class="pill p-grey">2/5</span> <span>break ✗ volume ✗ VWAP ✓ gap ✗ candles ✓</span> ${L.btn("Arm")}`,
])}
${L.steps([
  "Open the tab at <b>09:30</b> (a minute or two after is fine). Earlier, every row only says <i>waiting</i>.",
  "Pick the stock lists and press <b>Scan now</b>. The first scan of the day downloads 15 days of candles per stock to learn its usual opening volume, so it takes a few minutes; later scans are quick.",
  "Set <b>Show</b> to <i>Runners</i>. Read the top rows: 5/5 first, then 4/5. The ✓ / ✗ marks tell you which checks passed.",
  "Tick the two or three you like and <b>Arm ticked</b> on a practice bot. The bot trades them on its own signal; with \"Join the morning trend\" on in its Settings it can enter a trend that is already running.",
  "Come back at 10:00 with <b>Trend quality</b>: a 09:30 runner that has gone choppy by 10:00 should be unarmed.",
])}

<h2>4. A worked example</h2>
${L.box("", "", `BAJFINANCE at 09:31: opening range ₹1,000 to ₹1,004; the 09:30 price is ₹1,008.40 (<b>break ✓</b>). Shares traded 09:15-09:30: 2.6 lakh against a usual 1.0 lakh (<b>volume ✓</b>, 2.6×). All three candles closed above VWAP and the second and third opened above it (<b>VWAP ✓</b>). Fast average ₹1,005.9, slow ₹1,004.4: gap +0.15 % (<b>gap ✓</b>). Two green candles, the 09:25 close above the 09:15 close (<b>candles ✓</b>). <b>5 of 5: RUNNER, LONG.</b>
<br><br>What it means for you: the stock has every early sign of a trend day. Arm it; the bot buys on its own signal and the stop guards the trade. What it does not mean: that it will keep rising. The <b>Morning test</b> tab measures, on past days, how often a morning like this really did keep going.`)}

<h2>5. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>A disciplined 09:30 routine instead of chasing whatever jumped at 09:16.</li><li>Five checks a beginner can read as ticks and crosses.</li><li>Volume vs usual uses the stock's own history, so a quiet stock on a loud day still stands out.</li></ul>`],
  ["Limits", `<ul><li>Three candles are a thin base: a 5/5 runner can still reverse at 10:00.</li><li>Needs at least two earlier sessions of candles for the volume check.</li><li>It never places an order.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["Opening range", "The high and low of 09:15 to 09:30. Above the high = breakout up, below the low = breakout down."],
  ["Volume vs usual", "Shares traded in these 15 minutes compared with the same 15 minutes on earlier days.", "2.6× = two and a half times the usual."],
  ["VWAP", "The average price everyone paid today so far, weighted by volume. Above it: buyers are winning."],
  ["SMA gap", "Distance between the fast and slow average lines as % of the price."],
  ["Runner", "A stock that passes 4 or 5 of the five checks."],
  ["Arm", "Put the stock on a bot's Trade list; the bot trades only on its own signal."],
])}
`,
    }),
};
