/**
 * The Learn page's lessons (components/Learn/LearnPage.tsx). Each lesson is a guide file kept in
 * frontend/public/guides/, served with the rest of the site. Add a lesson by dropping its file there and
 * adding an entry here; the newest first. Display only.
 */
export type Lesson = {
  id: string;
  title: string;
  summary: string;
  /** Which part of the app it explains (the topic filter). */
  topic: "SMA strategy" | "Exits" | "Entries" | "Backtests" | "Scalp" | "Getting started";
  /** Site path of the guide file. */
  file: string;
  pages: number;
  minutes: number;
  added: string;
  /** Where in the app the setting lives, and the button text for it. */
  open?: { href: string; label: string };
  points: string[];
};

export const LESSONS: Lesson[] = [
  {
    id: "scalp-start-here",
    title: "The Scalp page, start here",
    summary:
      "What each tab of the Scalp page is for, in plain words, which ones to open first as a beginner, and the handful of words you need to read any of them. Nothing on this page buys or sells; it finds stocks and tests ideas with practice money.",
    topic: "Scalp",
    file: "/guides/scalp-start-here-guide.pdf",
    pages: 4,
    minutes: 8,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Find stocks (Monitor, Cross scan, 09:30 Morning, Turning, Carry-over, Trend quality, 52-week high, Most active), then test ideas (Second candle, Backtest, Strategy lab, Sideways test, Morning test), then look back (History) and get told (Alerts).",
      "Open Most active and Trend quality first; arm 3-5 trending stocks on a practice bot with a stop.",
      "Candle, SMA, cross, VWAP, volume vs usual and trending / choppy: the six words that unlock every tab.",
      "Every tab has a Guide (PDF) button and a \"What do these words mean?\" panel.",
    ],
  },
  {
    id: "scalp-monitor",
    title: "Monitor: scalp-ready stocks by a score",
    summary:
      "The simplest screen on the page: every streaming stock scored 0-100 on how good it is for a quick in-and-out trade right now. What the score is made of (movement per minute, money traded, a tight spread), what the filters do, and how to use it with the other tabs.",
    topic: "Scalp",
    file: "/guides/scalp-monitor-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Scalp-ready = moves enough per minute, a tight spread, enough money trading today.",
      "The score ranks the ready stocks; 60+ is worth a look, 75+ is busy.",
      "Read the direction (vs VWAP, bias) before arming; the score says busy, not which way.",
      "Watch only: arm on a practice bot and let the bot take the entry.",
    ],
  },
  {
    id: "scalp-cross-scan",
    title: "Cross scan: which stocks may signal next",
    summary:
      "The bot buys or sells when its two average lines cross. This tab lists the stocks whose lines are closing in on each other, soonest first, so you can arm a stock before its signal instead of after it, with a picture of a gap closing towards a bullish cross.",
    topic: "Scalp",
    file: "/guides/scalp-cross-scan-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "\"In N min\" is the bot's own heads-up arithmetic on the candle size you pick.",
      "Just-crossed stocks are listed too; the bot trades the next cross, not a cross from before arming.",
      "Shares today, last 10 minutes and speed tell you whether anyone is trading it.",
      "First seen says how long a stock has been on the list today.",
    ],
  },
  {
    id: "scalp-morning-0930",
    title: "09:30 Morning: runners or chop, from the first three candles",
    summary:
      "At 09:30 a stock has only three 5-minute candles, too few for the other scans. This tab asks five quick questions of those three candles (opening-range break, volume vs usual, VWAP, SMA gap, candle colours) and tells you which stocks look like runners for the day.",
    topic: "Scalp",
    file: "/guides/scalp-morning-0930-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Five checks, each for a long and a short; 4 of 5 = runner.",
      "Volume vs usual compares 09:15-09:29 with the same minutes on earlier days.",
      "Before 09:30 every row is \"not ready\"; run it at 09:31.",
      "Arm the runners on a practice bot; the bot still waits for its own signal.",
    ],
  },
  {
    id: "scalp-turning",
    title: "Turning: a reversal before the lines cross",
    summary:
      "A stock that has been falling all morning, whose last few candles turn green and climb back over its fast average line, is often turning up before the two average lines cross. This tab finds those stocks (and the mirror for a rising stock turning down), so you see a turn early.",
    topic: "Scalp",
    file: "/guides/scalp-turning-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Counter candles = N candles in a row against the trend, the last one past the fast SMA.",
      "Minutes to the cross estimates when the lines meet at the current pace.",
      "\"Telegram me new ones\" sends each new turn once a day.",
      "The bot's own Early reversal entry trades the same picture when ticked.",
    ],
  },
  {
    id: "scalp-carry-over",
    title: "Carry-over: did yesterday's trend carry on today?",
    summary:
      "A stock that closed yesterday in a clear trend often keeps going for a while this morning. This tab shows, for each stock, which way its average lines pointed at yesterday's close, how today opened against that, and whether the trend continued, reversed, or reversed and came back.",
    topic: "Scalp",
    file: "/guides/scalp-carry-over-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Continued / Reversed / Back: no cross against yesterday's side, crossed, or crossed and came back.",
      "A gap with the trend means overnight news agreed with it.",
      "Best / Worst with times show how deep the early dip was: the stop a trade needed.",
      "Run it 09:35-10:00; a bot with Join the morning trend enters a trend already running.",
    ],
  },
  {
    id: "scalp-after-close",
    title: "After the close: real buyers, or short sellers buying back?",
    summary:
      "Sometimes a stock jumps at the end of the day and, after the market shuts, Groww shows far more buy orders waiting than sell orders. This tab finds those stocks and says, in plain words, whether it looks like real buyers or short sellers buying back what they sold earlier.",
    topic: "Scalp",
    file: "/guides/scalp-after-close-guide.pdf",
    pages: 4,
    minutes: 6,
    added: "2026-11-12",
    open: { href: "/scalp/#afterclose", label: "Open the After the close tab" },
    points: [
      "Many buyers still waiting + shares taken home = real demand.",
      "A fall all day, a jump in the last hour, shares not taken home = short sellers buying back.",
      "Run it after 15:30; again after 18:00 when NSE's delivery % is out.",
      "F&O stocks: price up with open interest down = short sellers closing.",
    ],
  },
  {
    id: "scalp-trend-quality",
    title: "Trend quality: trending or choppy today?",
    summary:
      "The most useful check before arming anything. A trending stock moves one way and pays a simple bot; a choppy one zig-zags and gives false signal after false signal. This tab reads each stock's day and labels it, with a strong-trend score, the phase it is in now, and alerts for the quiet runners.",
    topic: "Scalp",
    file: "/guides/scalp-trend-quality-guide.pdf",
    pages: 4,
    minutes: 8,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Counting today's SMA crosses is a direct measure of how the bot would have fared.",
      "Trending = 0-1 crosses and a long run; Choppy = 4+ crosses or under 15 minutes apart.",
      "Strong trend = 6 of 7 checks (stack, VWAP, slope, fan, hold, structure, run) with a 0-100 score.",
      "Auto re-scan every 30 minutes and the quiet-runner alert work without a browser.",
    ],
  },
  {
    id: "scalp-52-week-high",
    title: "52-week high: stocks at their yearly high or low",
    summary:
      "A stock trading at its highest price of the last year has no recent buyers sitting above it waiting to sell at break-even, so a push there can run. This tab lists how far each stock is from its yearly high and low, which ones broke out today, and how fresh the high is.",
    topic: "Scalp",
    file: "/guides/scalp-52-week-high-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "From high %: negative means above it (a new high today).",
      "Since high: 0-5 sessions is fresh; 100+ means months under the level.",
      "Prefer a fresh breakout with a good day % and volume; check Most active.",
      "The bot's \"Enter only at the 52-week high\" check trades the idea by rule.",
    ],
  },
  {
    id: "scalp-most-active",
    title: "Most active: where the money is trading now",
    summary:
      "The busiest F&O stocks right now, ranked by the column you choose: volume vs usual, money traded, the biggest move, distance from the day's average price, speed or range. The card the daily routine starts from, with saved history, an accuracy test and a backtest built in.",
    topic: "Scalp",
    file: "/guides/scalp-most-active-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Volume vs usual is the beginner's column: 2× and above is where the news and the money are.",
      "Live, History, Which column finds the trend?, Backtest: the four views.",
      "Tick the top 10, Read trend quality, drop the Choppy ones, arm 3-5 on a practice bot.",
      "After the close: History → 10:00 scan → Check accuracy.",
    ],
  },
  {
    id: "scalp-second-candle",
    title: "Second candle test: does \"buy the 2nd candle\" give ₹10?",
    summary:
      "A simple rule many beginners use: wait for the day's second 5-minute candle to close; if it is green, buy; if red, sell short; aim for ₹10 a share. This tab measures the rule on every stock and past day you pick: how far the trade went, how far it went against you first, and what a target, a stop or a trailing stop would have made.",
    topic: "Scalp",
    file: "/guides/scalp-second-candle-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Against before target = the stop a winning trade needed: the most useful number here.",
      "The trailing-stop table says which trail distance keeps the gains.",
      "Any target / stop pair is judged without a new run.",
      "To trade the rule itself, set the bot's Enter on: Second candle.",
    ],
  },
  {
    id: "scalp-backtest",
    title: "Backtest: prove a routine on past days before you trust it",
    summary:
      "A backtest replays past days with practice money: the real bot, its real settings, the real candles, but nothing is ordered. The three tools: backtest the day's strong-trend picks by themselves, compare pick times side by side, and rank your stocks by how well your strategy did on each of them. Every number (win %, green days, biggest drop, profit factor, verdict) in plain words.",
    topic: "Scalp",
    file: "/guides/scalp-backtest-guide.pdf",
    pages: 4,
    minutes: 8,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "10 days minimum, 20 better; a result carried by one big day is luck.",
      "Judge the after-charges figure: many small wins turn into a loss.",
      "Verdicts: Strong (profit factor >= 1.5, 55%+ green days), Good, Weak, Avoid, Few.",
      "The daily run backtests each day's picks after the close by itself.",
    ],
  },
  {
    id: "scalp-strategy-lab",
    title: "Strategy lab: which checks and exits make money?",
    summary:
      "The lab reads your stocks' past days once, keeps every SMA cross as a trade with every rule's answer already worked out, and then lets you tick entry checks, exits, stops and targets and see the P&L change at once, no replay needed. What each rule adds in rupees, and how to prove the finding with the real bot.",
    topic: "Scalp",
    file: "/guides/scalp-strategy-lab-guide.pdf",
    pages: 4,
    minutes: 8,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Ticking a rule is arithmetic on a table, so the answer is instant.",
      "What does each rule add? shows each switch's rupees; keep the big green bars.",
      "A sieve, not the bot: closed candles, fills at the next open, no pullbacks or hand-offs.",
      "Backtest this in the replay is the proof.",
    ],
  },
  {
    id: "scalp-morning-test",
    title: "Morning test: does the morning tell you the rest of the day?",
    summary:
      "Does a stock that rises from the open keep going, and does one that falls from the open get bought back before the close? The test labels each past morning bullish / bearish / sideways at the time you pick, then measures the rest of the day, the bounce from the low, the next day and NSE's delivery %.",
    topic: "Scalp",
    file: "/guides/scalp-morning-test-guide.pdf",
    pages: 4,
    minutes: 8,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Bullish morning = up at least 0.5% with the fast SMA above the slow one and a fairly straight path.",
      "The answer lines read like sentences: \"ended higher on 7 of 10 days (70%)\".",
      "When the low came (half-hour buckets) tells you when a bearish morning usually bounces.",
      "Under 5 days means nothing; 10+ days and 65%+ is worth acting on.",
    ],
  },
  {
    id: "scalp-history",
    title: "History: every saved scan, and did it come true?",
    summary:
      "Every finished Cross scan and Trend quality scan is saved with its time and kept for 90 days. See exactly what any past scan said, download it, and after the close grade it: what each stock did 15, 30 and 60 minutes later and at the close, and whether the cross it promised came.",
    topic: "Scalp",
    file: "/guides/scalp-history-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "A scan is a prediction; the record lets you grade every call, not only the hits.",
      "Check accuracy groups the hit rate: all, quiet runners, strong trend, trending, about to cross.",
      "Pull from backtest server fetches scans made there while the desk was down.",
      "Grade a week before you change a routine.",
    ],
  },
  {
    id: "scalp-alerts",
    title: "Alerts: a Telegram when a stock is ready, and the other alerts on this page",
    summary:
      "The Alerts tab sends a Telegram once a minute during market hours when a stock becomes scalp-ready with a score at least your minimum. This lesson also lists the page's other alerts (quiet runner, trend rank, turning, fast move, news), what each message means, what to do when one arrives, and how to set up Telegram once.",
    topic: "Scalp",
    file: "/guides/scalp-alerts-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Min score 60 and a 30-minute cooldown is a good start; raise the score if there are too many.",
      "Every alert is a heads-up, never an order: look, read Trend quality, then arm on a practice bot.",
      "Each kind can have its own Telegram bot; a kind without one falls back to the trade bot.",
      "The Scalp alert turns off on a server restart: check it in the morning.",
    ],
  },
  {
    id: "buyers-sellers",
    title: "Buyers vs sellers: the % column, the day list, the test and the alert",
    summary:
      "What \"buyers 75 %\" means (waiting buy orders against sell orders), how the Most active card's day list decides Yes / No (the price saved with each scan must move your % its way before the day's last scan), every column with real rows as examples, the many-days test (signals, right %, any stock, edge, verdict), the Telegram alert, and how to use it step by step.",
    topic: "Scalp",
    file: "/guides/buyers-sellers-guide.pdf",
    pages: 6,
    minutes: 8,
    added: "2026-11-12",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Buyers % = waiting buy orders ÷ all waiting orders, read at each saved scan; it is not who bought.",
      "\"Moved\" uses the prices saved with each scan, not the chart's every tick, from the price at the first scan that reached your %.",
      "Judge it by the Edge (right % minus what any stock did), and over many days, not one.",
      "Every result is a heads-up, never an order: read Trend quality, then arm on a practice bot.",
    ],
  },
  {
    id: "most-active-columns",
    title: "Which column finds the trend? Every column explained",
    summary:
      "How to read the Most active card's \"Which column finds the trend?\" test: what it does (a picture of one day), what \"kept going\" means, the \"Best for …\" grid, each column of the big table (verdict, top kept going, edge vs all, weakest, top − weakest, one hour later, average move, readings) with real numbers as the example, how to read your own results, what to do next and a word list.",
    topic: "Scalp",
    file: "/guides/most-active-columns-guide.pdf",
    pages: 4,
    minutes: 7,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Sort by one column, take the top stocks, and see if they kept moving their way to the close.",
      "Edge = top kept going minus all stocks: 54% − 36% = +18 points means the column helps (Strong ≥ +10).",
      "Top − weakest must be positive too: if the weakest do as well as the top, the column is not telling them apart.",
      "Under 30 readings is too few to judge; re-run as more scans are saved, and prove it with a paper bot.",
    ],
  },
  {
    id: "sideways-test",
    title: "Sideways test: does sideways or trending hold?",
    summary:
      "How the Scalp page's Sideways test checks, on your own stocks and past days, whether SMA crosses, SMA 9 turns, ADX, efficiency, candle overlap or the SMA gap tell you what the next hour does, how to run it, how to read a signal card, and what to change in Settings when a signal works (or what to do when none does).",
    topic: "Backtests",
    file: "/guides/sideways-test-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-05",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "At 10:00-14:30 each past day it reads 6 chop signals on the last hour and checks if the next hour trended.",
      "A signal is useful only if its buckets are far apart (spread 20+ points = strong, under 10 = guessing).",
      "Turn a strong signal into a setting (trade cap, ADX, SMA gap), then prove it with a replay.",
      "Run it on the backtest server; it never places an order.",
    ],
  },
  {
    id: "day-high-low",
    title: "Day high/low guard: don't buy at the top",
    summary:
      "What the three boxes mean (near the high/low, wait for a pullback, wait at most), with candle charts of a dip that gives a cheaper buy, no dip so no trade, a signal far from the high, and the short version.",
    topic: "Entries",
    file: "/guides/day-high-low-guard-guide.pdf",
    pages: 3,
    minutes: 5,
    added: "2026-11-04",
    open: { href: "/settings/#sma-day-extreme", label: "Open the setting" },
    points: [
      "Signal within 0.15% of the day's high (₹1.50 on ₹1,000): don't buy yet.",
      "Wait for the price to dip 0.15% (or to the SMA 9 if nearer), then buy there.",
      "No dip within 3 candles: no trade, the move is let go.",
      "Shorts work the same upside down, near the day's low.",
    ],
  },
  {
    id: "sma9-slope",
    title: "SMA 9 slope: Turn exit and V-turn",
    summary:
      "What slope means in % and rupees, the difference between Turn exit, Steep run, Turn back and Turn back within, with candle charts of a V that fires, a slow top and a turn that comes too late.",
    topic: "Exits",
    file: "/guides/sma9-slope-guide.pdf",
    pages: 6,
    minutes: 8,
    added: "2026-11-04",
    open: { href: "/settings/#sma-slope-editor", label: "Open the slope settings" },
    points: [
      "Slope = how much the SMA 9 line moves per candle, in % of the price (0.02% on ₹1,000 = ₹0.20).",
      "V-turn = steep run + turn back + within N candles, all together.",
      "Turn exit = one level on its own: any fall that steep closes the trade.",
      "How to get fewer whipsaws, with a worked example of a real trade.",
    ],
  },
];
