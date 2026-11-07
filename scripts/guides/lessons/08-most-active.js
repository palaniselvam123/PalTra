const L = require("../lib");

module.exports = {
  id: "scalp-most-active",
  file: "scalp-most-active-guide.pdf",
  title: "Most active: where the money is trading now",
  html: () =>
    L.page({
      title: "Most active: where the money is trading now",
      lead: "The busiest F&O stocks right now, ranked by the column you choose: volume vs usual, money traded, the biggest move, distance from the day's average price, speed or range. The card the owner's daily routine starts from, with saved history, an accuracy test and a backtest built in.",
      body: `
${L.box("blue", "Why \"busy\" matters", `A day trader needs stocks other traders are busy in: the price moves, orders fill at once, and a move has weight behind it. This card measures \"busy\" several ways and lets you sort by any of them. Its most useful column for a beginner is <b>Volume vs usual</b>: a stock trading three times its normal volume by 10:00 is where the news and the money are today.`)}

<h2>1. The four views</h2>
${L.table(["View", "What it shows", "When to use it"], [
  ["<b>Live</b>", "The scan of the busiest stocks right now, re-run every 5 minutes by the server (Auto scan) or when you press Scan now.", "During market hours."],
  ["<b>History</b>", "Any saved scan from any past day: pick the day and the time, step through with ‹ ›, download a CSV, or press Check accuracy to see what each stock did afterwards.", "After the close, to grade the day."],
  ["<b>Which column finds the trend?</b>", "A test over saved scans: sort by each column, take the top stocks, and see whether they kept moving their way an hour later and at the close. Its own lesson explains every number.", "Weekly."],
  ["<b>Backtest</b>", "Plays past days: at a chosen time the top N stocks by your column are picked and a practice bot trades them to the square-off. Compare pick times shows which time of day works best.", "After hours, before trusting a column."],
])}

<h2>2. The columns, in plain words</h2>
${L.table(["Column", "Meaning", "Good for"], [
  ["Vol vs usual", "Shares traded so far today compared with the same time on earlier days. 1× = normal.", "Finding today's stocks: 2× and above."],
  ["Money traded (₹ cr)", "Shares × price so far today, in crores.", "Stocks you can get in and out of easily."],
  ["Day move %", "Today's change against yesterday's close.", "The biggest movers."],
  ["vs avg (VWAP) %", "Distance of the price from today's average price. Above = buyers winning.", "Trend direction and strength."],
  ["Buyers vs sellers", "The order book's imbalance right now (live only; not kept in history).", "Who is pushing at this moment."],
  ["Speed %/min", "Change per minute since the previous scan (none on the first scan of the day or after a long gap).", "What is moving now."],
  ["Day range %", "Today's high minus low, as % of the open.", "How lively the stock is."],
  ["Bias", "LONG (up) / SHORT (down) from today's readings.", "Which side to expect from the bot."],
  ["Deliv % / vs usual", "NSE's delivery share for the last published day and its change against the average.", "Whether real buyers held the shares (evening data)."],
  ["Trend / Crosses / Phase", "Added by <i>Read trend quality</i>: the Trend quality card's labels for the stocks on screen.", "Dropping the choppy ones without leaving the card."],
])}

<h2>3. The daily routine, step by step</h2>
${L.screen("Where traders are now — most active F&O stocks", [
  `${L.chip("Live", true)} ${L.chip("History")} ${L.chip("Which column finds the trend?")} ${L.chip("Backtest")} ${L.chip("Auto scan every 5 min ✓")} ${L.btn("Scan now", true)}`,
  `${L.chip("Rank by: Vol vs usual", true)} ${L.chip("Bias: Any")} ${L.chip("Price 100-3000")} ${L.chip("Vol vs usual ≥ 2×")} ${L.chip("Min speed 0")} ${L.btn("Read trend quality")} ${L.btn("Arm ticked")} ${L.btn("Watch news")}`,
  `<span>☐</span><b>SBIN</b> <span class="g">+1.4%</span> <span><b>4.2×</b> usual</span> <span>₹310 cr</span> <span class="g">vs avg +0.6%</span> <span>speed +0.05%/min</span> <span class="g">LONG</span> <span class="pill p-green">TRENDING</span> 📰`,
  `<span>☐</span><b>TATAMOTORS</b> <span class="r">−2.1%</span> <span><b>3.8×</b> usual</span> <span>₹280 cr</span> <span class="r">vs avg −0.9%</span> <span>speed −0.08%/min</span> <span class="r">SHORT</span> <span class="pill p-green">STRONG ↓</span> 📰`,
])}
${L.steps([
  "At 10:00 set <b>Rank by</b> to <i>Vol vs usual</i> and the filter <b>Vol vs usual at least</b> to 2×. Keep a <b>price range</b> you can afford a few hundred shares of.",
  "Tick the top 10 and press <b>Read trend quality</b> (1/3/5/15-minute candles). Untick the ones labelled <i>Choppy</i>.",
  "Press the <b>📰</b> on a row for the stock's news; <b>Watch news</b> adds the ticked stocks to a news list.",
  "Tick 3-5 and press <b>Arm ticked</b>: pick a practice bot and keep the suggested stop and trail. The bot trades on its own signal.",
  "Keep <b>Auto scan</b> on: the server re-runs the scan every 5 minutes and saves it, so History and the tests have data even when no browser is open.",
  "After the close, open <b>History</b> → today's 10:00 scan → <b>Check accuracy</b>: did the top stocks keep moving their way? Then run the <b>Backtest</b> view with the same pick time to see what a bot would have made.",
])}

<h2>4. A worked example</h2>
${L.box("", "", `10:00, ranked by Vol vs usual: SBIN 4.2× (₹310 cr, +1.4 %, above VWAP, LONG, Trending) and TATAMOTORS 3.8× (−2.1 %, below VWAP, SHORT, Strong down). Both are busy, both are trending, one up and one down. Arm both on Bot 1: it buys SBIN on its next bullish cross and sells TATAMOTORS short on its next bearish cross, each with the trailing stop you accepted in the Arm box.
<br><br>At 15:35, History → 10:00 scan → Check accuracy: SBIN +0.9 % an hour later and +1.6 % at the close (kept going); TATAMOTORS −1.1 % an hour later and −0.4 % at the close (kept going, then faded). The Backtest view with pick time 10:00 and top 5 by Vol vs usual shows what Bot 1 would have made on days like this.`)}

<h2>5. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>One card for find → check → arm → grade.</li><li>Saved scans turn every day into evidence for the tests.</li><li>Vol vs usual is normalised per stock, so a quiet stock on a loud day still shows up.</li></ul>`],
  ["Limits", `<ul><li>Busy is not the same as trending: always read the trend labels.</li><li>Buyers vs sellers is a snapshot and is not kept in history.</li><li>Nothing here places an order.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["Vol vs usual (rvol)", "Today's volume so far against the same time on earlier days.", "3× = three times normal."],
  ["VWAP", "The day's volume-weighted average price."],
  ["Speed", "Price change per minute since the previous scan."],
  ["Bias", "LONG or SHORT from today's readings."],
  ["Delivery %", "Share of traded shares that were kept overnight (NSE evening data)."],
  ["Check accuracy", "Re-reads the day's candles after the close and shows what each scanned stock did afterwards."],
])}
`,
    }),
};
