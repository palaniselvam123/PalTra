/**
 * Plain words for the trading terms the Scalp page uses, for a beginner. Each entry says what the word means in
 * everyday language, gives one small example with numbers, and says why it matters on this page. The Scalp tabs open
 * the ones they use through components/Scalp/GuideBar.tsx ("What do these words mean?"). Display only.
 */
export type PlainWord = {
  /** The word as it appears on the screen. */
  term: string;
  /** What it means, in one or two plain sentences. */
  means: string;
  /** One small example with numbers. */
  example?: string;
  /** Why it matters here. */
  why?: string;
};

export const PLAIN_WORDS: Record<string, PlainWord> = {
  candle: {
    term: "Candle (1-minute, 5-minute …)",
    means:
      "One bar on a price chart that sums up a stretch of time: where the price opened, the highest and lowest it went, and where it closed. A 5-minute candle covers five minutes. Green means it closed higher than it opened, red means lower.",
    example: "A 5-minute candle from 09:20 to 09:25: opened ₹500, went up to ₹503, down to ₹499, closed ₹502. It is green.",
    why: "Every scan and bot on this page reads candles. A bigger candle (15 minutes) is calmer but slower to react; a small one (1 minute) reacts fast but is noisy.",
  },
  closed_candle: {
    term: "Closed candle",
    means: "A candle whose time is over, so its close price is final. The candle still forming can change until its last minute ends.",
    why: "The bot and the scans only read closed candles, so a signal never comes from a half-finished candle that could still turn.",
  },
  sma: {
    term: "SMA 9 and SMA 21 (moving averages)",
    means:
      "The average close price of the last 9 candles (SMA 9) and the last 21 candles (SMA 21), drawn as two smooth lines on the chart. They smooth out the jumps so you can see which way the price is really going.",
    example: "If the last 9 closes average ₹502 and the last 21 average ₹498, SMA 9 is above SMA 21: the recent prices are higher than the older ones, so the stock is drifting up.",
    why: "SMA 9 is the fast line (reacts quickly), SMA 21 the slow one. The whole SMA bot is built on where these two lines sit.",
  },
  cross: {
    term: "SMA cross (bullish / bearish)",
    means:
      "The moment the fast line (SMA 9) crosses the slow line (SMA 21). Fast crossing above slow is a bullish cross (prices picking up): the bot buys. Fast crossing below slow is a bearish cross: the bot sells (goes short).",
    example: "At 10:15 SMA 9 was ₹499.5 and SMA 21 ₹500. At 10:20 SMA 9 is ₹500.4 and SMA 21 ₹500.1. The fast line crossed above the slow one: a bullish cross.",
    why: "A cross is the bot's basic signal. Stocks that cross often are choppy (lots of false starts); stocks that cross rarely and then run are the ones that make money on a cross.",
  },
  gap_pct: {
    term: "SMA gap %",
    means: "How far apart the two average lines are, as a percentage of the price. Positive when SMA 9 is above SMA 21, negative when below.",
    example: "SMA 9 ₹502, SMA 21 ₹500: the gap is +0.4%. A gap shrinking towards 0 means a cross is coming.",
    why: "A wide gap means a trend with room; a gap near zero means the lines are tangled and the stock is going sideways.",
  },
  trend: {
    term: "Trend / trending",
    means: "The price keeps moving mostly one way, up or down, for a while, with only small pauses.",
    example: "A stock that goes from ₹500 to ₹512 over two hours with small dips on the way is trending up.",
    why: "Trends are where a simple buy-and-hold-the-move strategy makes money.",
  },
  chop: {
    term: "Choppy / sideways",
    means: "The price goes up and down inside a narrow band and gets nowhere. The SMA lines keep crossing each other.",
    example: "A stock that moves between ₹498 and ₹503 all morning, crossing its averages five times, is choppy.",
    why: "Choppy stocks give false signals: each cross looks like the start of a move and then fades. Skip them, or give them to the sideways bot (Bot 2).",
  },
  strong_trend: {
    term: "Strong trend (the 7 checks)",
    means:
      "A stock passes most of seven simple checks: price above both averages (for an up trend), above the day's average price (VWAP), both averages rising, the gap wide enough, most recent closes on the right side, higher highs and lows, and no cross for a while. 6 of 7 is a strong trend.",
    why: "It is a stricter test than \"the lines have crossed\": it picks the stocks that are moving cleanly, not just the ones that twitched.",
  },
  phase: {
    term: "Phase (bull / bear / chop)",
    means: "Which kind of stretch the stock is in right now: moving up cleanly (bull), moving down cleanly (bear), or going sideways (chop). The strip shows how the day was split into these stretches.",
    why: "A stock can be choppy at 10:00 and trending at 13:00. The phase tells you which it is now.",
  },
  vwap: {
    term: "VWAP (volume-weighted average price)",
    means:
      "The average price everyone paid today, counting big trades more than small ones. Price above VWAP means today's buyers are on average in profit; below means sellers are winning.",
    example: "If most of the day's shares changed hands near ₹500 and the price is now ₹504, the stock is above VWAP: buyers are in control.",
    why: "Many professional traders use VWAP as the line between \"strong today\" and \"weak today\".",
  },
  rvol: {
    term: "Volume vs usual (rvol)",
    means:
      "How many shares traded so far today compared with the same time on normal days. 1× is a normal day; 3× means three times the usual interest.",
    example: "By 09:25 a stock usually trades 1 lakh shares. Today it has traded 4 lakh: volume vs usual is 4×.",
    why: "Big volume means many traders are involved, so moves tend to carry further and the price is easier to get in and out of.",
  },
  volume: {
    term: "Volume / shares traded",
    means: "How many shares changed hands. On this page it is today's running total, or the number in the last few minutes.",
    why: "A move on tiny volume is easy to reverse; a move on heavy volume has weight behind it.",
  },
  value_cr: {
    term: "Money traded (₹ cr)",
    means: "Shares traded multiplied by the price, in crores of rupees. It says how much money flowed through the stock today.",
    example: "2 lakh shares at ₹500 = ₹10 crore traded.",
    why: "Stocks with little money flowing are hard to trade: your own order can move the price.",
  },
  atr: {
    term: "ATR (average true range)",
    means: "How much the price typically moves inside one candle, averaged over the last 14 candles. It measures how lively the stock is.",
    example: "An ATR of ₹2 on a ₹500 stock means a typical candle swings about ₹2 from high to low.",
    why: "Stops are often set as a multiple of ATR (1.5× ATR = ₹3 here), so a normal wobble does not knock you out but a real reversal does.",
  },
  atr_pct_min: {
    term: "ATR % per minute",
    means: "The stock's typical movement per minute, as a percentage of its price. Higher = livelier.",
    example: "0.05% per minute on a ₹1,000 stock is about ₹0.50 of movement every minute.",
    why: "A scalp needs a stock that actually moves; one with 0.01% per minute will not pay for its charges.",
  },
  spread: {
    term: "Spread (bid / ask)",
    means: "The gap between the best price buyers offer (bid) and the best price sellers ask. You pay the spread every time you get in and out.",
    example: "Bid ₹500.00, ask ₹500.20: the spread is ₹0.20, or 0.04%.",
    why: "A wide spread eats a scalp's profit before the price has moved at all.",
  },
  rsi: {
    term: "RSI (relative strength index)",
    means: "A 0-100 reading of how hard the price has been pushed up or down recently. Above 70 is \"stretched up\", below 30 \"stretched down\", around 50 is neutral.",
    why: "The bot can refuse to buy when RSI is already stretched up, so it does not buy at the top of a burst.",
  },
  adx: {
    term: "ADX (trend strength)",
    means: "A 0-100 reading of how strong the current move is, regardless of its direction. Under 20 is weak or sideways; above 25 is a real trend.",
    why: "A cross with ADX at 12 is probably noise; the same cross with ADX at 30 has a trend behind it.",
  },
  efficiency: {
    term: "Efficiency",
    means: "How straight the path was: the net move divided by the total distance travelled. 1.0 is a straight line; 0.2 is a zig-zag that went nowhere.",
    example: "Up ₹2, down ₹2, up ₹2, down ₹1: it travelled ₹7 to end ₹1 higher. Efficiency 1 ÷ 7 = 0.14, very choppy.",
    why: "Trends are efficient; chop is not. It is one of the clues the Sideways test checks.",
  },
  speed: {
    term: "Speed (% per minute)",
    means: "How fast the price has moved over the last 10 minutes, as a percentage per minute, with its direction.",
    example: "+0.15% per minute on ₹1,000 means it climbed about ₹15 in ten minutes.",
    why: "A fast mover can give a quick scalp, but it can also turn just as fast.",
  },
  arm: {
    term: "Arm / Trade list",
    means: "Put a stock on a bot's Trade list so the bot watches it and may trade it on its own signal. Arming never places an order by itself.",
    why: "The scans find candidates; arming hands them to a bot. Only armed stocks are ever traded.",
  },
  bot: {
    term: "Bot 1 … Bot 5",
    means: "Five copies of the SMA trading program, each with its own settings and its own list of stocks. Bot 1 is the main desk; Bot 2 is set up for sideways stocks, Bot 3 for trending ones, Bot 5 takes fast movers.",
    why: "Different stocks suit different settings, so a stock can be handed from one bot to another.",
  },
  paper: {
    term: "Practice (PAPER) money",
    means: "Trades recorded in the app with pretend money at real prices. Nothing is sent to Groww. LIVE is the opposite: real orders with real money.",
    why: "Every test on this page is practice only; the bots start in practice mode and stay there until you switch one to LIVE on purpose.",
  },
  stop: {
    term: "Stop-loss",
    means: "A price at which a losing trade is closed automatically so the loss stays small.",
    example: "Buy at ₹500 with a stop at ₹497: if the price falls to ₹497 the shares are sold and the loss is ₹3 a share.",
    why: "It is the one setting that decides how much a bad trade can cost you.",
  },
  trail: {
    term: "Trailing stop (TSL)",
    means: "A stop-loss that moves up behind a rising price (or down behind a falling one) in steps, and never moves back. Profit gets locked in as the trade goes your way.",
    example: "Buy ₹500, stop ₹498, trail every ₹1. Price reaches ₹501: the stop moves to ₹499. Price ₹503: stop ₹501, so you cannot lose any more.",
    why: "It lets a winner run while protecting what it has already made.",
  },
  target: {
    term: "Target",
    means: "A price at which a winning trade is closed to bank the profit.",
    example: "Buy ₹500 with a ₹10 target: sold at ₹510.",
    why: "A target takes money off the table before a move fades; a trailing stop is the alternative that rides it further.",
  },
  square_off: {
    term: "Square-off",
    means: "Closing every open intraday position before the market closes. The bots do it at a set time (15:15 by default).",
    why: "Intraday trades cannot be carried overnight, so anything still open is closed at the square-off.",
  },
  handoff: {
    term: "Hand-off (to Bot 2 / Bot 3)",
    means: "Moving a stock from one bot's list to another's, with that bot's settings, when it starts behaving differently: a choppy stock goes to Bot 2, a trending one to Bot 3.",
    why: "One set of settings cannot suit every mood of every stock; hand-offs let the right bot trade it.",
  },
  backtest: {
    term: "Backtest / replay",
    means: "Playing a past day's real prices through the bot as if it were live, with practice money, to see what it would have done. A replay runs minute by minute; the Strategy lab is a faster shortcut.",
    why: "The only honest way to know whether a setting helps before risking money on it.",
  },
  pnl_before: {
    term: "P&L before charges / after charges",
    means: "Profit and loss. Before charges is the raw price difference times the shares; after charges takes off brokerage, taxes and fees. The screens show before charges by default.",
    example: "Buy ₹500, sell ₹503, 100 shares: ₹300 before charges, about ₹250 after.",
    why: "Small scalps can look green before charges and red after. Always check the after-charges number before trusting a strategy.",
  },
  win_pct: {
    term: "Winning trades %",
    means: "The share of trades that made money.",
    why: "A high win rate with tiny wins and big losses can still lose; read it next to the P&L.",
  },
  profit_factor: {
    term: "Profit factor (won ÷ lost)",
    means: "All the money won divided by all the money lost. 1.0 is break-even; 1.5 means ₹1.50 won for every ₹1 lost.",
    why: "It is a better single number than win % because it counts the size of wins and losses.",
  },
  green_days: {
    term: "Green days",
    means: "Days that ended with a profit. \"6 of 10 (60%)\" means six profitable days out of ten.",
    why: "A strategy that wins on most days is easier to live with than one that makes a month's profit in one lucky day.",
  },
  drawdown: {
    term: "Biggest drop (drawdown)",
    means: "The largest fall of the running total from a high point to the next low point. It is the worst stretch you would have sat through.",
    example: "The running total reached ₹8,000, fell to ₹5,000, then rose again: the biggest drop was ₹3,000.",
    why: "It tells you how painful the strategy gets, not just how it ends.",
  },
  per_trade: {
    term: "Per trade (average)",
    means: "The average profit or loss of one trade.",
    why: "Compare it with the charges per trade: if it is not comfortably bigger, the strategy is working for the broker.",
  },
  delivery: {
    term: "Delivery %",
    means: "Of all the shares traded in a day, the share that was actually delivered to new owners rather than bought and sold within the day. NSE publishes it in the evening.",
    example: "Delivery 60% means most of the day's buying was by people who kept the shares.",
    why: "High delivery with a rise suggests real buyers, not just day traders passing shares around.",
  },
  high52: {
    term: "52-week high / low",
    means: "The highest (or lowest) price the stock has touched in the last year.",
    why: "A stock breaking above its yearly high has no recent sellers waiting above it, so moves there can run.",
  },
  orb: {
    term: "Opening range (09:15 to 09:30)",
    means: "The high and low of the first 15 minutes of the day. A break above that high (or below that low) is an opening-range breakout.",
    why: "The first minutes set the day's early battle lines; a clean break out of them is a classic start of a move.",
  },
  bias: {
    term: "Bias (long / short)",
    means: "Which way the stock looks set to go on today's readings: long means up (buy), short means down (sell first, buy back later).",
    why: "The bot can buy and sell short; the bias says which side the scan favours.",
  },
  scalp_ready: {
    term: "Scalp-ready / score",
    means: "A stock passes the Monitor's quick checks: it moves enough per minute, its spread is tight, enough money is trading, and the score (0-100) sums these up.",
    why: "A scalp is a quick in-and-out trade; it needs a stock that moves and is cheap to trade.",
  },
  second_candle: {
    term: "Second candle rule",
    means: "Wait for the day's second 5-minute candle (09:20 to 09:25) to close. If it is green, buy; if red, sell short. The first candle is often wild, the second shows the real direction.",
    why: "It is a simple rule many beginners use; the Second candle test measures how well it has worked on real days.",
  },
  pullback: {
    term: "Pullback",
    means: "A short dip inside an up trend (or a short bounce inside a down trend) before the move continues.",
    why: "Buying on a pullback gets a cheaper price than buying at the top of a burst.",
  },
  pivot: {
    term: "Pivot points (P, R1, R2, S1, S2)",
    means: "Price levels worked out from yesterday's high, low and close. R1 and R2 are levels above where sellers often appear; S1 and S2 are below, where buyers appear.",
    why: "They give a target or a support line that many traders watch, so the price often reacts there.",
  },
  bollinger: {
    term: "Bollinger bands",
    means: "A band drawn around the average price, wider when the stock is wild and narrower when it is calm. Price at the upper band is stretched up.",
    why: "The bot can take profit at the far band or refuse to buy when the price is already stretched.",
  },
  supertrend: {
    term: "Supertrend",
    means: "A line that sits below a rising price and above a falling one and flips side when the price turns. Green under the price = up, red above it = down.",
    why: "A simple trend line that can also be used as a moving stop.",
  },
  heikin: {
    term: "Heikin Ashi candles",
    means: "Smoothed candles that average each candle with the one before, so a trend shows as a run of one colour with fewer flickers.",
    why: "Colour-based rules give fewer false turns on Heikin Ashi candles.",
  },
  fno: {
    term: "F&O stocks",
    means: "The stocks that have futures and options on NSE (about 180 to 200 of them). They are the big, liquid names most day traders use.",
    why: "The scans read this list by default: enough volume to get in and out quickly.",
  },
  cutoff: {
    term: "Entry cut-off",
    means: "The time after which the bot opens no new trade (15:00 by default), so nothing is bought just before the square-off.",
  },
  slope: {
    term: "SMA 9 slope / turn / V-turn",
    means: "How steeply the fast average line is rising or falling. A turn is the line changing direction; a V-turn is a sharp one right after a steep run.",
    why: "The line turning against your trade is an early warning, before the two averages cross.",
  },
  cvd: {
    term: "Cumulative delta (CVD)",
    means: "A running estimate of buying pressure minus selling pressure through the day, worked out from each candle's volume and shape.",
    why: "Price making a new high while the delta does not is a warning that the buyers are thinning out.",
  },
  macd: {
    term: "MACD",
    means: "Another pair of lines built from averages (12 and 26 candles) and a signal line; above zero leans bullish, below leans bearish.",
  },
  ema: {
    term: "EMA (exponential moving average)",
    means: "Like an SMA, but recent prices count more, so the line reacts faster.",
  },
  ltp: {
    term: "LTP (last traded price)",
    means: "The price of the most recent trade: the live price.",
  },
  flip: {
    term: "Flip",
    means: "Every signal is traded the other way round: a buy signal sells short and a sell signal buys. For stocks that keep doing the opposite of the lines.",
    why: "Test it on a replay first; it is a strong setting.",
  },
  gap_mode: {
    term: "SMA gap mode",
    means: "Instead of trading the cross itself, the bot waits until the two averages have opened a gap of a set size, and closes when the gap fades back.",
    why: "Skips the many tiny crosses of a choppy stock; enters a little later in a real move.",
  },
  recovery: {
    term: "Recovery hold / hand-off",
    means: "When a losing trade would be closed by an opposite cross, the bot can hold it a few candles hoping it recovers, and then move the stock to Bot 2 (the choppy-stock bot) with that bot's settings.",
  },
  review: {
    term: "1-minute review",
    means: "When a trade looks uncertain (the gap narrowing towards a cross), the bot asks you on screen and Telegram: exit or wait. No answer keeps the trade.",
  },
  join_trend: {
    term: "Join the morning trend",
    means: "A stock that opens already trending has no fresh cross, so the bot would never trade it. With this on it enters on the side the averages already point, after a set time.",
  },
  daily_loss: {
    term: "Max daily loss",
    means: "The most the bot may lose in a day (₹). When it is reached the bot closes everything and stops for the day.",
    why: "The one limit that protects your account from a bad day; set it before anything else.",
  },
  qty: {
    term: "Quantity",
    means: "How many shares each trade buys or sells. With a trailing stop of ₹5 and 100 shares, one stopped-out trade loses about ₹500 plus charges.",
  },
  live: {
    term: "LIVE mode",
    means: "Real orders on your Groww account with real money. Each bot has its own PAPER / LIVE switch, and switching needs a typed confirmation.",
    why: "Stay in PAPER until a backtest and a few practice days look right.",
  },
};

/** The words for a list of keys, unknown keys skipped. */
export function plainWords(keys: readonly string[]): PlainWord[] {
  return keys.map((k) => PLAIN_WORDS[k]).filter((w): w is PlainWord => Boolean(w));
}

/** Every word, alphabetically by term. */
export function allPlainWords(): PlainWord[] {
  return Object.values(PLAIN_WORDS).sort((a, b) => a.term.localeCompare(b.term));
}
