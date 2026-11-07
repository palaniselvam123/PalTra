const L = require("../lib");

module.exports = {
  id: "scalp-start-here",
  file: "scalp-start-here-guide.pdf",
  title: "The Scalp page, start here",
  html: () =>
    L.page({
      title: "The Scalp page, start here",
      lead: "What each tab is for, in plain words, which ones to open first as a beginner, and the handful of words you need to read any of them. Nothing on this page buys or sells; it finds stocks and tests ideas with practice money.",
      body: `
${L.box("blue", "The one idea behind the whole page", `A day trade needs a stock that is <b>moving</b> (not stuck), <b>busy</b> (lots of shares changing hands, so you can get in and out) and <b>going one way</b> for a while (a trend, not a zig-zag). Every tab here is a different way of finding such stocks today, or of checking on past days whether a way of finding them actually makes money.`)}

<h2>1. The tabs, in the order a beginner should learn them</h2>
${L.table(["Tab", "What it does, in one line", "When to open it"], [
  ["<b>Most active</b>", "Shows the busiest stocks right now: most money traded, most volume vs usual, fastest moving.", "First thing in the morning and every hour: this is where the action is."],
  ["<b>Trend quality</b>", "For each stock: is it trending (going one way) or choppy (zig-zagging) today? Counts how often its two average lines crossed.", "Before arming a stock: skip the choppy ones."],
  ["<b>Cross scan</b>", "Stocks whose two average lines are about to cross: the bot's buy / sell signal may come next.", "To see which stocks may signal in the next few minutes."],
  ["<b>09:30 Morning</b>", "Five quick checks on the first three 5-minute candles: which stocks look like runners for the day.", "At 09:30 exactly, once a day."],
  ["<b>Turning</b>", "Stocks whose candles have turned against their trend, before the average lines cross: an early reversal warning.", "Mid-morning and afternoon, to catch turns early."],
  ["<b>Carry-over</b>", "Did yesterday's trend carry on into today? Stocks that kept going versus ones that reversed.", "Just after the open."],
  ["<b>52-week high</b>", "Stocks at or near their highest price of the last year (or lowest).", "When you want breakouts with no sellers above."],
  ["<b>Second candle</b>", "Tests the simple rule \"buy the day's 2nd candle if green, sell if red\" on past days: how many rupees it gave, where a stop would have survived.", "After hours, to decide if the rule suits you."],
  ["<b>Backtest</b>", "Plays past days through the bots with practice money: strong-trend picks, which pick time works best, which stocks your strategy wins on.", "After hours, before changing a setting."],
  ["<b>Strategy lab</b>", "Tick entry checks, exits and stops and see the P&amp;L change at once on past days. The fast way to find settings worth a real backtest.", "After hours, when tuning the bot."],
  ["<b>Sideways test</b>", "Does \"it was choppy in the last hour\" predict \"it will stay choppy\"? Tells you which stocks to skip or hand to Bot 2.", "Weekly, to sort stocks between bots."],
  ["<b>Morning test</b>", "Does a bullish or bearish morning tell you the rest of the day? Checked on your stocks' past days.", "Weekly, to test a hunch."],
  ["<b>History</b>", "Every past scan, saved: open any one and check how accurate it was after the close.", "After the close, to grade today's scans."],
  ["<b>Alerts</b>", "Telegram messages when a stock becomes scalp-ready, with a cooldown.", "Set once; leave it on during market hours."],
  ["<b>Monitor</b>", "The older, simpler screen: scalp-ready stocks by a score. Most of what it does is now on Most active.", "Optional."],
])}

<h2>2. A beginner's daily routine with these tabs</h2>
${L.fig(L.timeline([
  { at: "09:15", text: "Market opens; wait", color: "#64748b" },
  { at: "09:30", text: "09:30 Morning: runners?", color: "#b45309" },
  { at: "10:00", text: "Most active + Trend quality: arm 3-5 stocks", color: "#0284c7" },
  { at: "12:00", text: "Turning: any reversals?", color: "#7c3aed" },
  { at: "15:15", text: "Bots square off", color: "#059669" },
  { at: "15:30", text: "History: check accuracy; Backtest / Lab", color: "#334155" },
], 720, "The scans find candidates. Arming a stock hands it to a bot; the bot still waits for its own signal before it trades."), "One day on the Scalp page. Everything before 15:30 is watching and arming; everything after is checking and tuning.")}

${L.steps([
  "<b>Pick the busy ones.</b> Open <b>Most active</b>, sort by <i>Volume vs usual</i> or <i>Money traded</i>. The top 10 are where traders are today.",
  "<b>Drop the choppy ones.</b> Tick those stocks and press <i>Read trend quality</i> (or open the <b>Trend quality</b> tab). Keep <i>Trending</i> or <i>Strong trend</i>; skip <i>Choppy</i>.",
  "<b>Arm a few.</b> Tick 3-5 stocks and press <i>Arm ticked</i>. Choose a practice bot (Bot 1) and keep the suggested stop and trail. Nothing is bought yet: the bot waits for its own signal.",
  "<b>Watch the terminal.</b> The SMA Terminal page shows the bot's trades, with the stop and target drawn on the chart.",
  "<b>After the close, grade yourself.</b> <b>History</b> → <i>Check accuracy</i> shows what each scanned stock did afterwards. <b>Backtest</b> and <b>Strategy lab</b> tell you which settings would have done better.",
])}

<h2>3. The six words you need for every tab</h2>
${L.cols([
  ["Candle", `One bar of the chart: open, high, low, close for a stretch of time (1, 5 or 15 minutes). <b>Green</b> closed higher than it opened, <b>red</b> lower. Only a <b>closed</b> candle (its time is over) counts; the one still forming can change.`],
  ["SMA 9 / SMA 21", `Two smoothed lines: the average of the last 9 closes and of the last 21. SMA 9 reacts fast, SMA 21 slowly. When the fast line crosses <b>above</b> the slow one the bot buys; crossing <b>below</b>, it sells short.`],
  ["Trend vs chop", `<b>Trend</b>: the price keeps going one way with small pauses. <b>Chop</b>: it zig-zags in a band and the two lines keep crossing. Trends pay; chop gives false signals.`],
  ["Volume vs usual", `Shares traded so far today compared with normal days at the same time. <b>3×</b> means three times the usual interest: moves carry further and the stock is easy to trade.`],
  ["Stop, trail, target", `<b>Stop</b>: the price where a losing trade is closed to keep the loss small. <b>Trailing stop</b>: a stop that follows the price up in steps and locks in profit. <b>Target</b>: the price where profit is banked.`],
  ["Arm / bot / practice", `<b>Arming</b> puts a stock on a bot's list. A <b>bot</b> is the trading program (five copies with their own settings). <b>Practice</b> (PAPER) money means nothing goes to Groww; LIVE means real orders.`],
], 2)}

${L.fig(L.candles({
  title: "What a bullish SMA cross looks like",
  candles: L.series([500, 499.4, 498.8, 498.5, 498.9, 499.6, 500.4, 501.2, 501.9, 502.3, 503.1, 503.8, 504.2, 504.9, 505.6, 506.1, 506.8, 507.2, 507.9, 508.4], 0.35),
  lines: { sma9: L.sma([500, 499.4, 498.8, 498.5, 498.9, 499.6, 500.4, 501.2, 501.9, 502.3, 503.1, 503.8, 504.2, 504.9, 505.6, 506.1, 506.8, 507.2, 507.9, 508.4], 3), sma21: L.sma([500, 499.4, 498.8, 498.5, 498.9, 499.6, 500.4, 501.2, 501.9, 502.3, 503.1, 503.8, 504.2, 504.9, 505.6, 506.1, 506.8, 507.2, 507.9, 508.4], 7) },
  marks: [{ i: 8, text: "fast line crosses above: buy", color: "#059669" }],
  xLabels: [[0, "10:00"], [9, "10:45"], [19, "11:35"]],
  height: 230,
}), "The red line (fast average) crosses above the blue line (slow average) as the price turns up. That is the bot's buy signal; the mirror image is its sell signal. (Short averages are drawn here so the picture fits.)")}

<h2>4. Three habits that keep a beginner safe</h2>
${L.box("green", "Stay in practice mode until a strategy has proved itself", `Every bot starts in PAPER (practice) mode. Run it for two or three weeks, read the Backtests and the Learn lessons, and only then think about LIVE. Switching to LIVE needs a deliberate confirmation; nothing on the Scalp page can do it.`)}
${L.box("amber", "Fewer stocks, clearer lessons", `Three to five armed stocks are enough. With twenty you cannot tell which setting helped and which hurt.`)}
${L.box("red", "A stop-loss on every trade", `The bot's stop (ATR or trailing) is the one setting that decides how much a bad day costs. Never switch it off to \"give the trade room\".`)}

<h2>5. Where the lessons are</h2>
<p>Every tab has a <b>Guide (PDF)</b> button that opens its lesson on the Learn page, and a <b>What do these words mean?</b> button that explains the words that tab uses, with an example each. The Learn page (top menu, <i>Learn</i>) lists all the lessons with a topic filter; tick <i>Mark as done</i> as you finish each one.</p>
`,
    }),
};
