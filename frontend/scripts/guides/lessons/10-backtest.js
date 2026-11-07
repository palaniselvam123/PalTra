const L = require("../lib");

module.exports = {
  id: "scalp-backtest",
  file: "scalp-backtest-guide.pdf",
  title: "Backtest: prove a routine on past days before you trust it",
  html: () =>
    L.page({
      title: "Backtest: prove a routine on past days before you trust it",
      lead: "A backtest replays past days with practice money: the real bot, its real settings, the real candles, but nothing is ordered. This tab has three tools: backtest the day's strong-trend picks by themselves, compare pick times side by side, and rank your stocks by how well your strategy did on each of them.",
      body: `
${L.box("blue", "Why backtest at all", `A trading idea feels right on the one day you watched it. A backtest asks the same question on 10 or 20 days you did not watch, and gives you numbers: how often it won, how much it made, how bad the worst stretch was. If the idea fails on past days, it has no business with your money today.`)}

<h2>1. The three tools on this tab</h2>
${L.table(["Tool", "What it does", "When to use it"], [
  ["<b>Strong-trend picks</b>", "On each past day, at the pick time you choose, every stock in your lists is read as the Trend quality card would have read it then (the same 7 checks, only the candles closed by that minute). The strong bullish / bearish ones are ranked and the top N become that day's picks. The bot you choose trades only them, from the pick minute to the square-off, with its own settings.", "To test the routine \"at 10:00 take the top 5 strong trends\"."],
  ["<b>Compare pick times</b>", "The same test run 2-8 times with different pick times (09:20 to 14:30), then shown side by side: P&amp;L, trades, win %, green days, best / worst day, biggest drop, and which time did best on most days.", "To find the time of day the routine works best."],
  ["<b>Which stocks does my strategy win on?</b>", "A bot's own settings backtested on every stock you pick over the same past days, then each stock ranked: trades, win %, green days, ₹ before and after charges, profit factor, and a verdict.", "Every week or two, to keep the stocks that suit the strategy and drop the rest."],
])}
${L.box("", "", `All three use the bot's <b>replay player</b>, the same engine that trades the live desk. That is the point: a backtest here is the real bot on the real candles, not a rough arithmetic.`)}

<h2>2. One backtest day, in a picture</h2>
${L.fig(L.timeline([
  { at: "10:00", text: "pick time: read every stock, keep the top 5", color: "#0284c7" },
  { at: "10:25", text: "Bot 3 enters SBIN on its cross", color: "#059669" },
  { at: "12:40", text: "trailing stop closes it +₹1,840", color: "#059669" },
  { at: "15:15", text: "square-off: everything closed", color: "#dc2626" },
], 720, "Every past day is played this way, one after another, at full speed. The trades go into the Backtests tab of the terminal."), "A strong-trend pick day: nothing is traded before the pick time; after it the bot trades the picks with its own settings until the square-off.")}

<h2>3. The numbers, in plain words</h2>
${L.table(["Number", "Meaning", "What is good"], [
  ["P&amp;L (before charges)", "What the trades made in ₹ before brokerage and taxes. The screens show this figure.", "Positive, and still positive <b>after charges</b>."],
  ["After charges", "The same minus Groww's intraday charges on every trade.", "Many small wins turn into a loss here: always look."],
  ["Trades / Win %", "How many trades, and the share that made money.", "Win % alone misleads: 40 % can be fine with big wins and small losses."],
  ["Green days", "Days that ended with a profit, e.g. 7 of 10.", "A rule that wins on most days is easier to live with."],
  ["Per day / Best day / Worst day", "The average day, and the two extremes.", "A best day much bigger than the rest means one lucky day carried the result."],
  ["Biggest drop", "The largest fall of the running total from a high to the next low: the worst stretch you would have sat through.", "Small compared with the total profit."],
  ["Profit factor (won ÷ lost)", "All the money won divided by all the money lost. 1.0 = break-even.", "1.5 or more is good; under 1.2 is weak."],
  ["Avg return / trade", "The average trade's result as a % of its value (qty × entry price).", "Over about 0.3 % to clear charges."],
  ["Verdict (stock ranking)", "<b>Strong</b>: profit factor ≥ 1.5 and ≥ 55 % green days. <b>Good</b>: ≥ 1.2. <b>Weak</b>: profitable but thin. <b>Avoid</b>: lost money. <b>Few</b>: under 5 trades or 3 days, not enough to judge.", "Keep Strong and Good; re-run every week or two."],
])}

<h2>4. Compare pick times: a picture</h2>
${L.fig(L.bars([
  { label: "Pick at 09:45", value: 4200, color: "#0284c7", text: "+₹4,200 · 6 of 10 green" },
  { label: "Pick at 10:00", value: 6800, color: "#059669", text: "+₹6,800 · 8 of 10 green · best" },
  { label: "Pick at 10:30", value: 3100, color: "#0284c7", text: "+₹3,100 · 6 of 10 green" },
  { label: "Pick at 11:00", value: 900, color: "#dc2626", text: "−₹900 · 4 of 10 green" },
], { max: 7000 }), "Four pick times, the same 10 days, stocks, bot and settings. 10:00 made the most and was green on the most days; 11:00 lost. Later picks catch trends that are already tired.")}

<h2>5. How to use it</h2>
${L.screen("Strong-trend picks: backtest them by themselves", [
  `${L.chip("Stocks: Nifty 100", true)} ${L.chip("Last 10 trading days")} ${L.chip("Pick at 10:00")} ${L.chip("Strong bullish and bearish")} ${L.chip("Top 5")} ${L.chip("Candle 5 min")} ${L.chip("Bot 3")} ${L.chip("Play hand-offs ✓")} ${L.btn("Backtest", true)}`,
  `<span>Every trading day, by itself:</span> ${L.chip("On ✓")} ${L.chip("Run at 15:45")} <span class="m">the server backtests each day's picks after the close and keeps the result in Backtests</span>`,
])}
${L.steps([
  "<b>Strong-trend picks</b>: choose the stock lists, how many past days (10-20), the pick time, the direction, top N, the candle and the bot. Keep <b>Play hand-offs</b> ticked if that bot's Settings move stocks to Bot 2 / Bot 3: the backtest then plays those moves too. Press <b>Backtest</b> and watch the replay bar on the terminal.",
  "Read the finished run in the terminal's <b>Backtests</b> tab (trades, day-wise P&amp;L, the PDF). The picks of each day are saved on the run.",
  "<b>Compare pick times</b>: tick 2-8 times and press Compare. Read the <b>answer lines</b> first (which time made the most, on how many days it was best), then the day-by-day table. A warning says when the bot's settings changed between the runs or there are under 10 days.",
  "<b>Which stocks does my strategy win on?</b>: pick the lists, days and the bot's strategy, press <b>Rank</b>. Keep the Strong and Good ones (Show → keep); tick them and <b>Arm ticked</b>. Nothing is armed until you press it.",
  "Tick <b>Every trading day, by itself</b> → On: after each close the server backtests that day's picks and the report builds up without you pressing anything.",
])}

<h2>6. A worked example</h2>
${L.box("", "", `Nifty 100, last 10 days, pick at 10:00, top 5 strong trends, Bot 3 (the trend bot). Result: +₹6,800 before charges, +₹4,900 after, 38 trades, win 47 %, 8 of 10 green days, biggest drop ₹1,600, profit factor 1.7. The worst day (−₹1,100) was a budget day. Compare pick times says 10:00 beat 09:45, 10:30 and 11:00. Stock ranking on the same days: BEL and HAL Strong, SBIN Good, ITC Avoid (it lost on 6 of 8 days). So: arm BEL, HAL and SBIN on Bot 3, keep the 10:00 Scan &amp; arm schedule, drop ITC, and re-rank in two weeks.`)}
${L.box("amber", "Before you trust it", `Ten days is the minimum, twenty is better. A result carried by one big day is luck, not a rule. Charges turn many small wins into a loss: judge the <b>after charges</b> figure. And a backtest fills at the candle's prices with no slippage, so the live result will be a little worse.`)}

<h2>7. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Turns \"I think 10:00 is the best time\" into numbers on your own stocks and days.</li><li>The real bot plays it, with its real stop, trail, exits and hand-offs.</li><li>The daily run builds a track record by itself.</li></ul>`],
  ["Limits", `<ul><li>Past days are not tomorrow; a rule that worked for a month can stop.</li><li>No slippage, and the practice fills are a little kind.</li><li>Practice money only; nothing here places an order, and arming is a separate button.</li></ul>`],
])}

<h2>8. Words used here</h2>
${L.words([
  ["Backtest / replay", "Playing past days through the real bot with practice money."],
  ["Pick time", "The minute of the day at which the stocks are read and chosen."],
  ["Top N", "How many of the ranked stocks become that day's picks."],
  ["Play hand-offs", "Let the bot's own rules move a stock to Bot 2 (chop) or Bot 3 (trend) inside the backtest."],
  ["Green day", "A day that ended with a profit."],
  ["Biggest drop (drawdown)", "The worst fall of the running total from a high to the next low."],
  ["Profit factor", "Money won ÷ money lost; 1.5 means ₹1.50 won for every ₹1 lost."],
  ["Verdict", "Strong / Good / Weak / Avoid / Few: the stock ranking's one-word summary per stock."],
])}
`,
    }),
};
