const L = require("../lib");

module.exports = {
  id: "scalp-alerts",
  file: "scalp-alerts-guide.pdf",
  title: "Alerts: a Telegram when a stock is ready, and the other alerts on this page",
  html: () =>
    L.page({
      title: "Alerts: a Telegram when a stock is ready, and the other alerts on this page",
      lead: "You cannot watch 200 stocks. The Alerts tab sends a Telegram once a minute, during market hours on live prices, when a stock on the Monitor becomes scalp-ready with a score at least your minimum. The other cards on this page send their own alerts too: quiet runners, trend ranks, turning stocks, fast moves and news. This lesson lists them all, what each message means, and what to do when one arrives.",
      body: `
${L.box("blue", "Watch only", `Every alert on this page is a heads-up, never an order. A message says \"look at this stock now\"; you decide whether to arm it on a practice bot. Nothing is bought or sold because an alert went out.`)}

<h2>1. The Scalp alert, step by step</h2>
${L.screen("Telegram scalp alerts", [
  `${L.chip("Min score 60", true)} ${L.chip("Cooldown 30 min")} ${L.btn("Turn on", true)} <span class="m">last run 10:42 · last sent SBIN 10:41</span>`,
])}
${L.table(["Setting", "Meaning", "A good start"], [
  ["Min score", "The Monitor's score (0-100) a stock needs before it is sent. The score adds up how much the stock moves per minute (ATR), how much money is trading, and takes off for a wide spread.", "60. Raise it to 75 if you get too many."],
  ["Cooldown (min)", "After a stock is sent, it is not sent again for this many minutes, even if it stays ready.", "30 minutes."],
  ["Turn on / off", "Starts and stops the once-a-minute check. It turns itself off when the server restarts, so check it in the morning.", "On from 09:30."],
])}
${L.box("", "", `<b>Scalp-ready</b> means the Monitor's three checks pass: the stock moves enough per minute (ATR above the minimum), the spread between buyer and seller is tight, and enough money is trading today. The score then ranks the ready ones. A stock is sent only when it is ready <i>and</i> its score is at least the minimum. Simulated prices and a closed market never send anything.`)}

<h2>2. A message, and how to read it</h2>
${L.box("green", "Telegram · Scalp alert 10:41", `<b>SBIN</b> scalp-ready, score 74 · ₹812.40 · +1.2 % today · ATR 0.09 %/min · spread 0.01 % · ₹310 cr traded · above VWAP`)}
${L.steps([
  "Open the <b>Monitor</b> (or the Most active card) and find the stock: is it still ready, and which way is it leaning (vs VWAP, bias)?",
  "Open <b>Trend quality</b> for it (or press Read trend quality on Most active): a <i>Trending</i> or <i>Strong</i> label is good; <i>Choppy</i> means pass.",
  "Press 📰 for its news: an alert during a results announcement is a different animal.",
  "If it looks right, <b>Arm</b> it on a practice bot with a stop. The bot takes the entry on its own signal; you do not have to be fast.",
])}

<h2>3. The other alerts on this page</h2>
${L.table(["Alert", "Where it is switched on", "What it says", "Channel"], [
  ["<b>Scalp alert</b>", "This tab.", "A stock became scalp-ready with a score at least your minimum.", "Trade channel."],
  ["<b>Quiet runner</b>", "Trend quality card (quiet minutes, price range).", "A stock crossed earlier today and has stayed quiet for an hour or more: the trend is holding. One message per scan, each stock once a day.", "Quiet-runner channel."],
  ["<b>Trend rank</b>", "Trend quality card → Trend rank alert.", "The top-ranked trends (young trend, wide SMA gap) after each scan in market hours, each stock and side once a day; can also ring.", "Quiet-runner channel / call."],
  ["<b>Turning</b>", "Turning tab → \"Telegram me new ones\".", "Stocks whose candles started turning against the SMA trend, each stock and way once a day.", "Quiet-runner channel."],
  ["<b>Fast move</b>", "Settings → Fast-move alerts.", "An armed or held stock moved 0.5 % in 3 minutes; or, after a Trend quality scan, stocks moving 0.10 %/min or faster.", "Fast channel / call."],
  ["<b>News</b>", "Settings → News alerts; Watch news on any table.", "A fresh headline on an armed, held or watched stock with impact at least your minimum; a critical one on a held stock rings.", "News / critical channel."],
])}
${L.fig(L.timeline([
  { at: "09:35", text: "Fast move: BEL +0.6% in 3 min", color: "#b45309" },
  { at: "10:00", text: "Trend rank: BEL #1, HAL #2", color: "#7c3aed" },
  { at: "10:41", text: "Scalp alert: SBIN score 74", color: "#059669" },
  { at: "11:30", text: "Quiet runner: ASIANPAINT quiet 85 min", color: "#0284c7" },
  { at: "14:15", text: "Turning: ICICIBANK ↑ 3 green", color: "#dc2626" },
], 720, "A typical day's alerts. Each kind can have its own Telegram bot, so the phone shows at a glance which is which."), "")}

<h2>4. Setting up Telegram (once)</h2>
${L.steps([
  "On Telegram, open <b>BotFather</b>, send <i>/newbot</i>, and copy the token it gives you.",
  "Start a chat with your new bot (press Start) so it may message you, and find your chat id (send it a message and read it from the bot's updates, or use a chat-id bot).",
  "In PalTra, <b>Settings → alert cards</b>: paste the token and chat id in the <i>trade</i> card and press <b>Test</b>. A test message should arrive.",
  "Optional: a second bot for the quiet-runner / signal / fast / news kinds, so each has its own chat. A kind without its own bot falls back to the trade bot.",
  "Optional: the phone-call card (Twilio) for critical alerts and the trend-rank / fast-move calls. Calls ring only 09:00-15:45 on weekdays and at most once per 5 minutes.",
])}
${L.box("amber", "Too many messages?", `Raise the min score, lengthen the cooldown, narrow the price range on the Trend quality card, and keep the fast-move scan alert off until you want it. One useful message an hour beats twenty you ignore.`)}

<h2>5. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>The server watches every stock every minute; you watch your phone.</li><li>Each alert names the numbers behind it, so you can judge it in ten seconds.</li><li>Separate bots per kind keep the important ones visible.</li></ul>`],
  ["Limits", `<ul><li>An alert is a snapshot; by the time you look the move may be over.</li><li>The Scalp alert turns off on a server restart.</li><li>Watch only: nothing is ordered, and arming is your decision.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["Scalp-ready", "Passes the Monitor's checks: moves enough per minute, tight spread, enough money trading."],
  ["Score", "0-100, the Monitor's ranking of ready stocks."],
  ["Cooldown", "Minutes before the same stock may be sent again."],
  ["Channel", "Which Telegram bot (or phone call) a kind of alert goes to; set in Settings."],
  ["Quiet runner", "A stock that crossed earlier and has held its side for an hour or more."],
  ["Critical alert", "Bot down, a position without a stop, or bad news on a held stock: it also rings."],
])}
`,
    }),
};
