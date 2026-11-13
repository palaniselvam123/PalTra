const L = require("../lib");
const F = require("../figs-settings");

const KICKER = "PalTra · Learn · Strategy settings";
const FOOT =
  "This guide only explains. Nothing changes until you press Save on the Settings page, and every setting works the same in PAPER (practice) and in a replay, so try a change there first. Numbers in the examples are made up to keep the arithmetic easy.";

// ---- chart: the extra ways in, on one fall-then-rise ------------------------------------------------
const warm = [];
for (let i = 0; i < 21; i++) warm.push(1034 - i * 0.2);
const body = [1029, 1027.5, 1026, 1024, 1022.5, 1021, 1019, 1017.5, 1015.5, 1014, 1012, 1010.5, 1009, 1008, 1009.5, 1011.5, 1013.5, 1015, 1017, 1019, 1021, 1022.5, 1024, 1026, 1027, 1029, 1030, 1031.5];
const allCloses = warm.concat(body);
const s9 = L.sma(allCloses, 9).slice(21);
const s21 = L.sma(allCloses, 21).slice(21);
const vwap = body.map((_, i) => 1031 - i * 0.19);

const waysIn = () =>
  L.candles({
    candles: L.series(body, 0.5),
    lines: { sma9: s9, sma21: s21, vwap },
    marks: [
      { i: 16, text: "① Early reversal", color: "#7c3aed", y: "below", anchor: "start", lift: 4 },
      { i: 18, text: "② SMA 9 turn", color: "#b45309", anchor: "end", lift: 6 },
      { i: 22, text: "③ Cross", color: "#0284c7", y: "below", anchor: "start", lift: 4 },
      { i: 24, text: "④ Late entry", color: "#059669", anchor: "end", lift: 8 },
    ],
    xLabels: [[0, "10:00"], [7, "10:35"], [13, "11:05"], [22, "11:50"], [27, "12:15"]],
    height: 250,
    title: "One fall and one rise on 5-minute candles: four different settings would buy at four different candles",
  });

// ---- chart: the same 30 minutes as 15-minute and as 5-minute candles ----------------------------------
const five = [
  { o: 1000, h: 1003.4, l: 999.6, c: 1003 },
  { o: 1003, h: 1003.5, l: 1000.6, c: 1001 },
  { o: 1001, h: 1004.4, l: 1000.8, c: 1004 },
  { o: 1004, h: 1006.5, l: 1003.6, c: 1006 },
  { o: 1006, h: 1006.4, l: 1004.5, c: 1005 },
  { o: 1005, h: 1008.4, l: 1004.8, c: 1008 },
];
const fifteen = [
  { o: 1000, h: 1004.4, l: 999.6, c: 1004 },
  { o: 1004, h: 1008.4, l: 1003.6, c: 1008 },
];

// ---- chart: the SMA gap over time ----------------------------------------------------------------------
const gap = [-0.08, -0.06, -0.04, -0.02, 0.01, 0.03, 0.06, 0.09, 0.12, 0.15, 0.16, 0.14, 0.11, 0.08, 0.05, 0.02, 0.0];
const gapFig = () =>
  F.lineChart({
    series: [{ values: gap, color: "#7c3aed", dots: true }],
    levels: [
      { v: 0.05, text: "enter at gap ≥ 0.05 %", color: "#059669" },
      { v: 0.02, text: "exit at gap ≤ 0.02 %", color: "#dc2626" },
    ],
    marks: [
      { i: 4, text: "Cross: gap +0.01 %", color: "#0284c7", y: "below", lift: 14 },
      { i: 6, text: "Gap mode buys here", color: "#059669", anchor: "end" },
      { i: 10, text: "widest +0.16 %", color: "#475569" },
      { i: 15, text: "Gap fades: gap mode sells", color: "#dc2626", y: "below", lift: 4, anchor: "end" },
    ],
    bands: [{ from: 6, to: 15, color: "#ecfdf5", text: "gap mode is in the trade" }],
    zero: true,
    fmt: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`,
    xLabels: [[0, "candle 1"], [4, "5"], [8, "9"], [12, "13"], [16, "17"]],
    height: 230,
    title: "The SMA gap (SMA 9 minus SMA 21, in % of SMA 21) candle by candle",
  });

// ---- chart: candle-colour rules around one trade -------------------------------------------------------
const colourCloses = [1000.5, 1000, 1001.5, 1003, 1005, 1007, 1008.5, 1010, 1009, 1007.5, 1006];
const colourFig = () =>
  L.candles({
    candles: L.series(colourCloses, 0.35),
    marks: [
      { i: 3, text: "Signal: last 2 candles rose ✓", color: "#0284c7", y: "below", anchor: "start", lift: 4 },
      { i: 4, text: "Entry candle goes up ✓", color: "#059669", anchor: "end", lift: 4 },
      { i: 10, text: "3 red in a row → counter-candle exit", color: "#dc2626", y: "below", anchor: "end", lift: 6 },
    ],
    xLabels: [[3, "signal"], [4, "entry"], [10, "exit"]],
    height: 200,
    width: 720,
    title: "Three colour rules on one buy: one before the entry, one at the entry, one to get out",
  });

module.exports = {
  id: "settings-entries",
  file: "strategy-settings-entries-guide.pdf",
  title: "Strategy settings 1: getting in. Names that look alike, told apart",
  html: () =>
    L.page({
      kicker: KICKER,
      foot: FOOT,
      title: "Strategy settings 1: getting in",
      lead: "The Settings → Strategy page has over 200 boxes, and many of them share a word: three different \"gap\" settings, eight \"candle size\" menus, five kinds of \"volume\", a dozen rules about candle colour. This guide walks through the first half of a trade (the signal, the checks and how the order is placed) and puts every look-alike side by side with one small worked example and a picture. Part 2 covers getting out: exits, stops, targets, reverse and moving stocks between bots.",
      body: `<style>table{break-inside:auto} tr{break-inside:avoid} thead{display:table-header-group} h2.page{break-before:auto}</style>
${L.box("blue", "How to read this guide", `Each section is one family of names that get mixed up. It starts with a picture, then a table: <b>the name on the screen</b>, <b>where it is</b> (the numbered group on Settings → Strategy), <b>what it does in one line</b>, and <b>an example with numbers</b>. All examples use a ₹1,000 stock, so 0.1 % = ₹1. Everything here is <b>off by default</b> unless the table says otherwise.`)}

<h2>1. The map: what each numbered group decides</h2>
<p>A trade always goes through the same five steps. Every setting on the page belongs to exactly one of them. When a name confuses you, first ask <b>which step it is in</b>: the same word means a different thing in a different step.</p>
${L.fig(
  F.flow(
    [
      { tag: "Group 1", title: "Signal", tone: "blue", lines: ["What opens a trade:", "SMA cross, pattern,", "colour, second candle,", "extra ways in"] },
      { tag: "Group 2", title: "Checks", tone: "violet", lines: ["May this signal trade?", "VWAP, RSI, ADX,", "volume, gap range…", "one No = no trade"] },
      { tag: "Group 1", title: "Placing", tone: "amber", lines: ["Order now, or wait", "for a better price?", "pullback, limit,", "entry candle"] },
      { tag: "Groups 3, 4, 5", title: "In the trade", tone: "green", lines: ["Stop + target (5):", "price levels.", "Exit rules (3, 4):", "read on candle close"] },
      { tag: "Group 7", title: "After it closes", tone: "red", lines: ["Reverse? Enter", "again? Move the", "stock to another", "bot?"] },
    ],
    { boxH: 86, label: "The five steps of a trade and the settings group for each" },
  ),
  "Group 6 (size, daily limits and times) sits over all five steps: how many shares, how many trades, and the hours the bot may trade.",
)}
${L.table(["Group", "The question it answers", "Beginner?"], [
  ["<b>1. When to enter</b>", "What is the signal, on which candle size, and is the order sent at once or does it wait for a better price?", "<span class=\"pill p-green\">Start here</span>"],
  ["<b>2. Checks before entering</b>", "The signal came. Is the stock in a state worth trading? Each ticked check can say No.", "Add one at a time"],
  ["<b>3. When to exit</b>", "Rules that close a trade when a candle closes a certain way (cross back, colours, SMA 9 bending…).", "The SMA cross exit is on already"],
  ["<b>4. SMA gap mode</b>", "A different way to enter and exit: wait for the two averages to move apart, leave when they come back.", "<span class=\"pill p-grey\">Advanced</span>"],
  ["<b>5. Stop-loss and target</b>", "Price levels that close the trade the moment the price touches them.", "<span class=\"pill p-green\">Start here</span>"],
  ["<b>6. Size, daily limits and times</b>", "Quantity, daily loss limit, trade caps, last entry time, square-off time.", "<span class=\"pill p-green\">Start here</span>"],
  ["<b>7. Advanced</b>", "Holding a losing trade for a recovery, moving stocks between bots, asking you before an exit.", "<span class=\"pill p-grey\">Advanced</span>"],
])}

<h3>The same indicator in three places: check, exit, stop</h3>
<p>This is the biggest source of mix-ups. An indicator's name can appear up to three times, and each tick box does a different job. A <b>check</b> is read once, when the signal comes. An <b>exit</b> is read each time a candle closes while you are in the trade. A <b>stop</b> is a price watched every second.</p>
${L.table(["Indicator", "As a check (group 2): may I enter?", "As an exit (group 3): close the trade", "As a stop / target (group 5)"], [
  ["<b>VWAP</b>", "\"Price on the right side of VWAP\": buy only above it, sell only below.", "—", "Stop type \"VWAP + ATR buffer\": the stop sits a little beyond VWAP and moves each candle."],
  ["<b>Bollinger</b>", "\"Bollinger: skip spikes and squeezes\": no buy above the upper band, no trade while the bands are tight.", "\"Bollinger exit\": book profit at the far band, or leave on a close back across the middle.", "—"],
  ["<b>Supertrend</b>", "\"Buy only while it is green, sell only while red\".", "\"Close when the Supertrend flips against the trade\".", "Stop type \"Supertrend\": the stop sits on the line and follows it."],
  ["<b>EMA</b>", "\"Buy only while the fast EMA is above the slow one\".", "\"Close when the EMAs cross against the trade\".", "—"],
  ["<b>MACD</b>", "\"Buy only while MACD is above its signal line\".", "\"Close when MACD crosses its signal line against the trade\".", "—"],
  ["<b>Cumulative delta</b>", "\"Buy only while the cumulative delta is rising\".", "\"Close on a cumulative delta divergence\".", "—"],
  ["<b>SMA gap</b>", "\"SMA gap range\": the gap at the cross must be inside your range.", "Gap mode's fade (group 4).", "Stop type \"SMA gap\" (practice only)."],
  ["<b>ATR</b>", "Only as a ruler: \"tall when height ≥ × ATR\".", "Only as a ruler: \"min body × ATR\".", "\"ATR stop ×\" and \"ATR target ×\"."],
])}
${L.box("amber", "The lengths are typed once", `The Bollinger period and width, the EMA lengths, the MACD numbers and the Supertrend length and factor are typed in <b>group 2</b> and used by the exit and the stop too. That is why the exit boxes in group 3 say "(settings in step 2)". Ticking the check does <b>not</b> turn on the exit, and ticking the exit does not turn on the check: they are separate switches sharing one set of numbers.`)}

<h2 class="page">2. Eight "candle size" menus, and what each one changes</h2>
<p>A candle is one slice of time. The same half hour can be drawn as two 15-minute candles or six 5-minute candles. Bigger candles give fewer, calmer signals; smaller candles react sooner and also get fooled more often.</p>
<div class="col">
${L.fig(L.candles({ candles: fifteen, width: 350, height: 190, xLabels: [[0, "09:30-09:45"], [1, "09:45-10:00"]], title: "As 15-minute candles: 2 green" }), "A 15-minute bot sees two green candles and nothing else.")}
${L.fig(L.candles({ candles: five, width: 350, height: 190, marks: [{ i: 1, text: "red", color: "#dc2626" }, { i: 4, text: "red", color: "#dc2626" }], xLabels: [[0, "09:30"], [3, "09:45"], [5, "09:55"]], title: "The same 30 minutes as 5-minute candles: 4 green, 2 red" }), "The two red candles are inside the green ones. Only a setting that reads smaller candles can see them.")}
</div>
${L.table(["Name on the screen", "Where", "What it changes", "What it does NOT change"], [
  ["<b>Candle interval</b> (1, 2, 3, 5, 10, 15 min)", "Group 1", "The bot's own candle. The averages, ATR, every check, most exits and the chart are all read on it. This is the main one.", "—"],
  ["<b>Candle size</b> under \"Enter on: Candle patterns / Candle colour\"", "Group 1", "The candle whose pattern or colour is the signal (1 to 60 min). In these two modes the exits read this candle too.", "The candle interval box is ignored in these modes (the bot steps every minute underneath)."],
  ["<b>Candle size</b> under \"Enter on: Second candle\"", "Group 1", "Which candle of the morning is read (for example the 2nd 5-minute candle = 09:20-09:25).", "—"],
  ["<b>Read the cross exit on</b>", "Group 3, Main exit", "Only where the <i>SMA cross exit</i> is read. Enter on 1-minute candles, leave on a 15-minute cross.", "Entries, checks, the stop and the other exits stay on the candle interval."],
  ["<b>Watch smaller candles while in a trade</b>", "Group 3", "An extra, earlier exit: a 15-minute bot also watches 5-minute candles for a cross, an SMA 9 turn or candles against the trade.", "Entries. It can only close a trade sooner."],
  ["<b>Colours on smaller candles</b>", "Group 3", "Reads the <i>colour</i> of smaller candles: as an entry check (green to buy) and to confirm a colour-turn exit.", "It has no SMA cross or slope in it; that is \"Watch smaller candles\"."],
  ["<b>Read the SMA 9 slope on</b>", "Group 3, SMA 9 line exits", "Only the SMA 9 exits (turn, bend, V-turn, deceleration) are read on this size.", "Entries and every other exit."],
  ["<b>Check candle</b> (Ask me)", "Group 7", "A second opinion for the \"Ask me\" prompt: is the trend on this candle size with or against the trade?", "Nothing trades on it."],
  ["<b>The chart's candle-size menu</b> and <b>Drill down</b>", "On the chart", "Only what you see.", "The bot. Changing the chart never changes a trade."],
])}
${L.box("green", "A worked example", `Candle interval <b>15 minutes</b>. You bought at 09:45. At 09:55 the second red 5-minute candle closes (the right-hand picture).<br>
• With nothing else ticked, the bot cannot react until its own candle closes at <b>10:00</b>.<br>
• With <b>Watch smaller candles: 5 min</b> and "Candles in a row against: 2", it would need two red 5-minute candles in a row; here the reds are not in a row, so the trade stays.<br>
• With <b>Read the cross exit on: 5 min</b>, the trade leaves only when SMA 9 crosses under SMA 21 on the 5-minute candles, which can come several candles before the 15-minute cross.`)}

<h2>3. "Enter on": four signals, pick one</h2>
<p>Group 1 → <b>How a trade opens</b> → the <b>Enter on</b> menu. This is a choice of one, not tick boxes. Everything in the rest of group 1 hangs off it.</p>
${L.table(["Enter on", "Opens a trade when", "Closes it when", "Trades a day", "Use it for"], [
  ["<b>SMA cross</b> (default)", "The fast average crosses the slow one on a closed candle. Above = buy, below = sell.", "The opposite cross (if the SMA cross exit is on), a stop, a target or a ticked exit.", "As many as there are crosses, up to your caps.", "The standard strategy. Start here."],
  ["<b>Candle patterns</b>", "A named pattern (engulfing, hammer…) closes. Bullish buys, bearish sells.", "At the end of the <i>next</i> candle, always. It is a one-candle trade.", "Many, short ones.", "Quick scalps on one candle."],
  ["<b>Candle colour</b>", "A candle closes green (buy) or red (sell).", "The first candle of the other colour, and the bot turns round at once.", "Many: it is nearly always in a trade.", "Riding runs of same-colour candles."],
  ["<b>Second candle</b>", "Once a day: the day's Nth candle (2nd by default) closes green or red.", "Only a stop, target, a ticked exit or the square-off. Nothing opens a second trade.", "One per stock.", "The \"read the morning, take one trade\" routine."],
])}
${L.box("amber", "Do not mix these up", `<b>Candle colour</b> (an "Enter on" choice: the colour <i>is</i> the signal) is not the same as <b>Last candles must move the trade's way</b> (a check in group 2: the SMA cross is still the signal and the colours only say Yes or No). Section 7 lists every colour rule.`)}

<h2 class="page">4. "Extra ways in": six ways to enter without waiting for a fresh cross</h2>
<p>With "Enter on: SMA cross" the bot waits for a cross. That is safe but late, and it misses a stock that was already trending when you armed it. The extra ways in fill those holes. The picture shows where four of them would buy on the same move.</p>
${L.fig(waysIn(), "The red line is SMA 9, the blue line SMA 21, the dashed line VWAP. The price fell, turned and rose. ① ₹1,013.50, ② ₹1,017, ③ ₹1,024, ④ ₹1,027: the earlier the entry, the better the price, and the more often it turns out to be a false start.")}
${L.table(["Name on the screen", "It buys when", "In the picture", "Needs a cross?", "Watch out"], [
  ["<b>③ The plain SMA cross</b> (no tick box)", "SMA 9 closes above SMA 21.", "11:50, about ₹1,024.", "It is the cross.", "Late: the rise started ₹16 lower."],
  ["<b>① Early reversal</b>: enter before the cross after N candles against the trend", "While SMA 9 is still below SMA 21: N green candles in a row, the last closing above SMA 9.", "11:20, ₹1,013.50 (3 green).", "No, it comes before it.", "A 3-candle bounce in a downtrend often fails."],
  ["<b>② SMA 9 turn</b>: order at once when the fast SMA makes a V", "The SMA 9 line was falling steeply and now rises (a V).", "11:30, about ₹1,017.", "No, it comes before it.", "Needs a sharp V. A slow rounding only counts with \"Also count a rounded U turn\"."],
  ["<b>④ Late entry</b>: keep a refused cross armed for N minutes", "A check said No at the cross, and says Yes a few candles later.", "The cross closed under VWAP (refused). Two candles later the price is above VWAP: buy at ₹1,027.", "Yes, a refused one.", "Does nothing unless a check in group 2 is ticked."],
  ["<b>Join the morning trend</b>", "Once a day, some minutes after 09:15: the averages are <i>already</i> one way since before the open.", "Not shown: there is no cross today at all.", "No.", "Only the trend already running at that time. Not re-entered after a stop."],
  ["<b>Always in a trade</b>", "Any moment an armed stock is flat: buys if SMA 9 is above SMA 21, sells if below.", "It would already be short on the way down, and buy at ③.", "No.", "Re-enters right after every stop. Set \"Max trades per stock / day\"."],
])}
${L.cols([
  ["Late entry vs Join the morning trend", `<p>Both enter "late". The difference is <b>why there was no trade earlier</b>.</p><ul><li><b>Late entry</b>: there <i>was</i> a cross, and a check refused it. Wait up to N minutes for the check to agree.</li><li><b>Join the morning trend</b>: there was <i>no</i> cross today (it happened yesterday). Join the running trend once.</li></ul>`],
  ["Early reversal vs SMA 9 turn", `<p>Both enter <i>before</i> the cross. The difference is <b>what they read</b>.</p><ul><li><b>Early reversal</b> counts <i>candles</i>: N in a row against the trend.</li><li><b>SMA 9 turn</b> reads the <i>line</i>: steep one way, then back the other way within a few candles.</li></ul>`],
])}
${L.box("", "Three more names in this corner", `<b>Telegram me when the SMA 9 turns</b> only sends a message; it never orders. <b>Only take turns toward the 9/21 cross</b> keeps the turns that point at the coming cross and ignores the rest. <b>After a losing trade, enter the opposite way</b> belongs to "Always in a trade" and is explained with the other reverse settings in part 2.`)}

<h2 class="page">5. "How the entry order is placed": now, or at a better price?</h2>
<p>These settings do not decide <i>whether</i> to trade. The signal and the checks already said yes. They decide <b>at what price</b>: at once, or wait for the price to come back a little. If it never comes back in time, there is <b>no trade</b>.</p>
${L.fig(
  F.levelsFig({
    candle: { o: 1000, h: 1011, l: 999, c: 1010 },
    levels: [
      { v: 1010.5, name: "Entry-candle check", note: "wait until the next candle is 0.05 % above its own open (₹1,010)", color: "#0284c7" },
      { v: 1010, name: "No waiting (default)", note: "buy at once when the signal candle closes", color: "#475569" },
      { v: 1008.49, name: "Day high/low guard", note: "0.15 % back from the close (nearer than SMA 9)", color: "#b45309" },
      { v: 1005, name: "Tall-candle pullback", note: "half-way down the candle (or SMA 9 if higher)", color: "#7c3aed" },
      { v: 1004, name: "Limit entry, anchor \"Fast SMA (if nearer)\"", note: "SMA 9 is at ₹1,004", color: "#059669" },
      { v: 999, name: "Limit entry at the candle's low", note: "the cheapest, and the least likely to fill", color: "#dc2626" },
    ],
    height: 270,
    title: "A buy signal on a tall green candle: open ₹1,000, close ₹1,010, low ₹999, ATR ₹4, SMA 9 at ₹1,004, the day's high ₹1,011",
  }),
  "Each setting waits at a different price. The lower the wait, the better the price and the fewer the fills.",
)}
${L.table(["Name on the screen", "When it applies", "Where it waits (a buy)", "Gives up after"], [
  ["<b>Tall candle: wait for a pullback</b>", "Only when the signal candle is tall: height ≥ 1.5 × ATR. Here ₹12 ≥ ₹6, so yes.", "The higher of SMA 9 and half-way down the candle: ₹1,005.", "2 candles"],
  ["<b>Limit entry at the signal candle's low / high</b>", "Every signal.", "The candle's low, ₹999. Or SMA 9 if nearer, ₹1,004.", "2 candles, or the price breaks the other side, or the SMAs cross back"],
  ["<b>Don't buy at the day's high</b> (day high/low guard)", "Only when the buy is within 0.15 % of today's high. Here ₹1,010 is ₹1 from ₹1,011, so yes.", "0.15 % back (₹1,008.49) or SMA 9, whichever is nearer.", "3 candles"],
  ["<b>Enter only while the entry candle goes the trade's way</b>", "Every signal.", "<i>Above</i> the next candle's open by 0.05 %: it waits for strength, not for a dip.", "2 candles"],
  ["<b>Wait for a pullback</b> (candle colour mode only)", "Only with \"Enter on: Candle colour\".", "Half-way back, the candle's low, or SMA 9.", "1-3 colour candles; can then buy at the market instead"],
])}
${L.cols([
  ["If several are ticked, who wins?", `<p>Only one wait runs per signal. In order:</p><ol style="margin:0 0 0 18px;padding:0"><li><b>Limit entry</b> at the low / high</li><li><b>Tall-candle pullback</b> (if the candle is tall)</li><li><b>Day high/low guard</b> (if near the high / low)</li><li><b>Entry-candle check</b></li></ol>`],
  ["Three of them wait for a dip, one for strength", `<p>The pullback, the limit and the guard all want a <b>cheaper</b> price than the close. The entry-candle check wants the opposite: proof that the <b>next candle is going up</b> before buying. Ticking it will not get you a better price; it gets you fewer entries on candles that turn red.</p>`],
])}
${L.box("", "\"Hand-off orders to this bot\" is a different menu", `It sits in the same block but only matters for a stock that <b>another bot sent here</b> (part 2, hand-offs). It decides whether that arriving order goes at once, waits for a candle going its way, or follows the forming candle's colour.`)}

<h2 class="page">6. The "gap" family: one number, six settings</h2>
<p>The <b>SMA gap</b> is how far apart the two averages are, as a percentage: <b>(SMA 9 − SMA 21) ÷ SMA 21 × 100</b>. Example: SMA 9 = ₹1,002 and SMA 21 = ₹1,000 gives <b>+0.20 %</b>. It is positive while SMA 9 is on top, negative while it is underneath, and exactly zero at a cross. A widening gap means the trend is getting stronger; a shrinking gap means it is tiring.</p>
${L.fig(gapFig(), "At a cross the gap is tiny (+0.01 %). It widens as the trend runs and shrinks as it tires. On a ₹1,000 stock, 0.05 % means SMA 9 is 50 paise above SMA 21.")}
${L.table(["Name on the screen", "Where", "What it is", "On the picture"], [
  ["<b>SMA gap range</b>: Buy (LONG) / Sell (SHORT), Min and Max", "Group 2", "A <b>check</b>. Read once, on the cross candle: the gap must be inside your range or the cross is skipped.", "At the cross the gap is +0.01 %. A range of 0.05 to 0.30 would refuse this cross, like most crosses."],
  ["<b>SMA gap mode</b>", "Group 4", "A different <b>way to trade</b>. The cross only arms the trade. The order goes when the gap has widened to \"enter at\"; the trade closes when it fades back to \"exit at\".", "Buys at candle 7 (gap ≥ 0.05 %), sells at candle 16 (gap ≤ 0.02 %)."],
  ["Stop type <b>SMA gap — moving stop + target</b>", "Group 5", "A <b>stop and target</b> sized from the gap: stop = gap % × \"Stop × gap\", target = gap % × \"Target × gap\". Practice (PAPER) only.", "Gap 0.16 %, but the floor \"Min gap\" is 0.2 %: stop 0.2 % = ₹2 away, target 0.4 % = ₹4 away."],
  ["<b>SMA spread ≥ %</b> (opening)", "Group 2, opening range", "A <b>check</b> for the first minutes only: after 09:30 the gap must be at least this and still widening.", "—"],
  ["<b>Breakout SMA gap ≥ %</b>", "Group 7", "Part of deciding a <b>breakout</b>, which moves the stock to another bot. Not an entry check.", "—"],
  ["<b>Ask when the SMA gap is within ±</b>", "Group 7", "The band for the <b>Ask me</b> prompt: when the gap is this small and shrinking, the bot asks you EXIT or WAIT.", "Around candle 16-17, as the gap nears zero."],
])}
${L.cols([
  ["Gap range check vs gap mode", `<ul><li><b>Check</b>: one look, at the cross. Says Yes or No. The trade still opens on the cross candle.</li><li><b>Mode</b>: keeps looking, candle after candle, and opens the trade <i>later</i>, when the gap is wide enough.</li><li>With gap mode on, the check is read when the order goes, so only its <b>Max</b> adds anything ("no entry if the gap is already too wide").</li></ul>`],
  ["Two more \"gaps\" that are not this one", `<ul><li>The <b>opening gap</b> on the Scalp page's Carry-over tab is a <i>price</i> gap: today's open against yesterday's close. Not a setting.</li><li><b>Gap mode's fade options</b> ("Ride out pullbacks", "Check the fade every second", "…every 1-minute candle") only change <i>how</i> gap mode judges its exit. They do nothing with gap mode off.</li></ul>`],
])}

<h2 class="page">7. Rules about candle colour: before, at, and after the entry</h2>
<p>Many settings read whether candles are green or red. They differ in <b>when</b> they look and <b>what they do</b> with the answer.</p>
${L.fig(colourFig(), "A buy on an SMA cross. The check looks back at the candles before the signal, the entry-candle rule watches the candle being formed, the counter-candle exit counts red candles afterwards.")}
${L.table(["When", "Name on the screen", "Where", "In one line"], [
  [{ v: "<b>Before</b> the entry", cls: "b" }, "<b>Last candles must move the trade's way</b>", "Group 2", "Check: the last N closed candles before a buy must be rising (closes), green (colour) or both."],
  [{ v: "", cls: "" }, "<b>Colours on smaller candles</b> → Entry check", "Group 3", "Check: the last N <i>smaller</i> candles must be green to buy, red to sell."],
  [{ v: "<b>As</b> the signal", cls: "b" }, "<b>Enter on: Candle colour</b>", "Group 1", "The colour is the signal: green buys, red sells and turns the trade round."],
  [{ v: "", cls: "" }, "<b>Early reversal</b>", "Group 1", "N candles against the SMA trend buy before the cross (section 4)."],
  [{ v: "<b>At</b> the entry", cls: "b" }, "<b>Enter only while the entry candle goes the trade's way</b>", "Group 1", "Wait until the candle being formed is green (for a buy) before the order goes (section 5)."],
  [{ v: "<b>After</b> the entry", cls: "b" }, "<b>Counter candles</b>: exit after N candles against", "Group 3", "Exit: N red candles in a row after a buy close it, before the averages cross."],
  [{ v: "", cls: "" }, "<b>Re-enter if the move carries on</b>", "Group 3", "After a counter-candle exit, buy again if a later candle breaks the exit candle's high."],
  [{ v: "", cls: "" }, "<b>A separate rule to close a trade</b> (colour mode)", "Group 1", "Lets a colour trade close on, say, 2 red candles while it still opens on 1 green."],
  [{ v: "", cls: "" }, "<b>Close a turn trade on the first candle of the other colour</b>", "Group 1", "Exit, only for trades opened by the SMA 9 turn."],
  [{ v: "", cls: "" }, "<b>SMA 9 turns and SMA 9 exits close a trade only on a candle of the other colour</b>", "Group 1", "A <i>hold</i>: an SMA 9 exit that fires on a green candle (in a buy) waits for a red one."],
  [{ v: "<b>All of them</b>", cls: "a" }, "<b>Read candle colours from Heikin Ashi candles</b>", "Group 2", "Changes what \"green\" and \"red\" mean for every rule above. Prices, orders and stops stay real."],
])}
${L.box("green", "Example: 'candles' and 'body' numbers", `A candle opens ₹1,000, closes ₹1,002, high ₹1,003, low ₹999. ATR is ₹4.<br>
• <b>Colour</b>: green (close above open).  • <b>Body</b>: ₹2.  • <b>Range</b> (height): ₹4.<br>
• <b>Smallest body, % of price</b>: 2 ÷ 1,002 = <b>0.2 %</b>. Used by the colour and second-candle modes.<br>
• <b>Body × ATR</b>: 2 ÷ 4 = <b>0.5 × ATR</b>. Used by "Exit min body × ATR" (counter candles) and "Smallest body (× ATR)".<br>
• <b>Density</b>: body ÷ range = 2 ÷ 4 = <b>50 %</b>. Used by "Solid candle body (skip doji)".<br>
• <b>Tall?</b> Height ₹4 = 1 × ATR, under 1.5: not tall, so the tall-candle pullback does not apply.`)}

<h2>8. Five kinds of "volume", and one that is not volume</h2>
${L.table(["Name on the screen", "Where", "Compares what with what", "Example"], [
  ["<b>Volume above average</b> (Volume multiple)", "Group 2", "The signal candle's volume against the average of the <b>20 candles before it</b> (today's pace).", "Multiple 1.5: the candle traded 30,000 shares, the last 20 averaged 18,000 → 1.67×, passes."],
  ["<b>Volume at least × the usual for this time</b> (fresh trend)", "Group 2", "Today's total volume so far against the <b>same time on earlier days</b>.", "By 10:15 today 6 lakh shares; usually 3 lakh by 10:15 → 2×, passes 1.5."],
  ["<b>Volume vs usual at least</b> (second candle)", "Group 1", "The same \"vs earlier days\" measure, at the Nth candle of the morning.", "At 09:25 today 2× the usual 09:25 volume."],
  ["<b>Volume ≥ × usual (opening)</b>", "Group 2, opening range", "The 09:15-09:30 volume against the same 15 minutes on earlier days.", "Needs 2× by default."],
  ["<b>Volume (× avg)</b> under breakout", "Group 7", "Part of what counts as a breakout candle (≥ 1.5× the 20 candles before). Moves a stock, never an entry check.", "—"],
  ["<b>Solid candle body (skip doji)</b>, \"Density\"", "Group 2", "<b>Not volume.</b> The candle's body as a share of its range.", "Body ₹2 of a ₹4 range = 50 %."],
])}
${L.box("amber", "Vs the candles before, or vs earlier days?", `"<b>Above average</b>" asks: is this candle busier than the last hour or two? "<b>Vs usual</b>" asks: is today busier than a normal day at this time? Early in the morning every candle is busy compared with yesterday's quiet close, so "vs usual" is the fairer test before about 10:00.`)}

<h2 class="page">9. "Is it trending?": six names, three different jobs</h2>
${L.table(["Name on the screen", "Where", "Job", "It says yes when"], [
  ["<b>Only when the trend is strong (ADX)</b>", "Group 2", "Check", "ADX (a 0-100 trend-strength number) is at or above your threshold, 20 by default. It does not say which way."],
  ["<b>Enter only on a fresh trend</b>", "Group 2", "Check", "The averages crossed within the last 3 candles, price and SMA 9 are on the right side of VWAP, the gap is widening and volume is at least 1.5× the usual. A <i>new</i> trend."],
  ["<b>Supertrend</b>: buy only while it is green", "Group 2", "Check", "The Supertrend line is under the price (for a buy)."],
  ["<b>Join the morning trend</b>", "Group 1", "Entry", "It is an order, not a check: it enters a trend that was already running at the open (section 4)."],
  ["Breakout: <b>A strong trend (you pick the checks)</b>", "Group 7", "Moves the stock", "At least N of 7 trend checks pass. The stock is then handed to Bot 3. It does not open or block a trade on this bot."],
  ["<b>Trend quality</b> / \"Strong trend\" chip", "Scalp page", "Information", "The same 7 checks, for choosing which stocks to arm. Nothing in the strategy reads it."],
])}
${L.box("", "Fresh trend vs strong trend", `<b>Fresh</b> = it has only just started (a cross in the last 3 candles). <b>Strong</b> = it has been running and is orderly (no cross for 12 candles, price holding above SMA 9). A stock cannot be both at once. Fresh is an <i>entry check</i>; strong is used to <i>move a stock to the trend bot</i>.`)}

<h2>10. "The morning": four settings that all mention the open</h2>
${L.table(["Name on the screen", "Where", "What it does", "Time"], [
  ["The 09:20 start (no box)", "Built in", "No entry before 09:20, in PAPER and LIVE.", "09:15-09:20"],
  ["<b>Opening range</b>: volume, VWAP, SMA spread, break", "Group 2", "Four <b>checks</b>. While any is ticked, nothing is entered before 09:30, and after that the first 15 minutes must have looked right.", "Window 09:15-09:30"],
  ["<b>Join the morning trend</b>", "Group 1", "An <b>entry</b> once a day into a trend running since before the open.", "From 09:30 (15 min after the open, your number)"],
  ["<b>Enter on: Second candle</b>", "Group 1", "The <b>whole signal</b>: one trade a day from the Nth candle's colour.", "09:25 for the 2nd 5-minute candle"],
])}

<h2>11. "I want to…": which box to use</h2>
${L.table(["I want to…", "Use this", "Group"], [
  ["trade fewer false crosses", "Candle interval 5 min instead of 1, then one check: VWAP or ADX 20", "1, 2"],
  ["not miss a stock that was already running when I armed it", "Join the morning trend", "1"],
  ["get in when the check agrees a few candles after the cross", "Late entry, 10-15 minutes", "1"],
  ["get in before the cross", "Early reversal (candles) or SMA 9 turn (the line)", "1"],
  ["stop buying the very top of a big candle", "Tall candle: wait for a pullback", "1"],
  ["stop buying at the day's high", "Don't buy at the day's high", "1"],
  ["skip crosses where the averages are almost flat", "SMA gap range (a check), or SMA gap mode (waits for the gap)", "2 or 4"],
  ["skip dull, sideways stocks", "ADX check, or Bollinger: skip squeezes", "2"],
  ["trade only one stock once in the morning", "Enter on: Second candle", "1"],
])}
${L.box("green", "Add one thing at a time", `Each extra check removes trades, good and bad. Tick <b>one</b>, run a replay on the same days (terminal → Replay a past day), and compare the trades and the P&L with the run before. The Scalp page's <b>Strategy lab</b> does this for every check at once: "What does each rule add?".`)}

<h2>12. Words used here</h2>
${L.words([
  ["Signal", "The event that asks for a trade.", "SMA 9 closing above SMA 21."],
  ["Check (filter)", "A rule that can refuse a signal. Read once, when the signal comes.", "\"Price on the right side of VWAP\"."],
  ["Closed candle", "A candle whose time is over. The bot only judges closed candles; the one still forming is never a signal.", "At 10:03 the 10:00-10:05 candle is still forming."],
  ["SMA gap", "(SMA 9 − SMA 21) ÷ SMA 21 × 100.", "1,002 vs 1,000 = +0.20 %."],
  ["ATR", "The average size of recent candles, in rupees. A ruler that fits any stock.", "ATR ₹4: a ₹6 move is 1.5 × ATR."],
  ["VWAP", "Today's average traded price, weighted by volume.", "Price above VWAP = buyers are in profit today."],
  ["Pullback", "A small move back against the trend, used to enter at a better price.", "Signal at ₹1,010, buy at ₹1,005."],
  ["Flat", "Holding no position in that stock.", ""],
])}
`,
    }),
};
