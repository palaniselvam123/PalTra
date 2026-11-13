const L = require("../lib");
const F = require("../figs-settings");

const KICKER = "PalTra · Learn · Strategy settings";
const FOOT =
  "This guide only explains. Nothing changes until you press Save on the Settings page, and every setting works the same in PAPER (practice) and in a replay, so try a change there first. Numbers in the examples are made up to keep the arithmetic easy.";
const FLOW_CSS = `<style>table{break-inside:auto} tr{break-inside:avoid} thead{display:table-header-group}</style>`;

// ---- chart: stop, target and an exit rule on one buy ---------------------------------------------------
const basicCloses = [1000, 1001, 1003, 1002, 1005, 1007, 1006, 1009, 1008, 1006, 1004.5];
const basicFig = () =>
  L.candles({
    candles: L.series(basicCloses, 0.4),
    levels: [
      { v: 1012, text: "Target ₹1,012", color: "#059669" },
      { v: 1000, text: "Entry ₹1,000", color: "#64748b" },
      { v: 994, text: "Stop ₹994", color: "#dc2626" },
    ],
    marks: [{ i: 10, text: "Exit rule: 3 red candles closed → sell ₹1,004.50", color: "#b45309", y: "below", anchor: "end", lift: 8 }],
    xLabels: [[0, "buy"], [7, "best ₹1,009"], [10, "exit"]],
    height: 230,
    title: "One buy at ₹1,000: the stop and the target are prices; the exit rule is read when a candle closes",
  });

// ---- chart: the same trade under three stops ------------------------------------------------------------
const runCloses = [1000, 1001.5, 1003, 1002.5, 1004.5, 1006, 1005, 1007.5, 1009, 1008, 1010.5, 1012.5, 1010.5, 1008.5, 1006.5, 1005];
const runCandles = L.series(runCloses, 0.4);
let best = 1000;
const tslLine = runCandles.map((c) => {
  best = Math.max(best, c.h);
  return 994 + Math.floor((best - 1000) / 2 + 1e-9) * 2;
});
best = 1000;
let stage = 0;
const trail = [null, null, null, null, null, null, null, null, null, null, null, 1004, 1004.8, 1004.8, 1004.8, 1004.8];
const planLine = runCandles.map((c, i) => {
  best = Math.max(best, c.h);
  if (best >= 1012) stage = 2;
  else if (best >= 1009 && stage < 1) stage = 1;
  return stage === 2 ? Math.max(1000.6, trail[i] ?? 1000.6) : stage === 1 ? 1000.6 : 994;
});
const atrLine = runCandles.map(() => 994);
const stopsFig = () =>
  L.candles({
    candles: runCandles,
    lines: { atr: atrLine, plan: planLine, tsl: tslLine },
    styles: {
      atr: { color: "#dc2626", label: "ATR stop", dash: true },
      tsl: { color: "#0284c7", label: "Trailing (TSL)", step: true },
      plan: { color: "#7c3aed", label: "Staged plan", step: true },
    },
    marks: [
      { i: 8, text: "+1.5R: plan → breakeven", color: "#7c3aed", anchor: "end", lift: 10 },
      { i: 11, text: "+2R: plan follows Supertrend", color: "#7c3aed", lift: 4, anchor: "end" },
      { i: 15, text: "TSL hit ₹1,006: +₹6 kept", color: "#0284c7", y: "below", anchor: "end", lift: 4 },
    ],
    xLabels: [[0, "buy ₹1,000"], [11, "best ₹1,013"], [15, "₹1,005"]],
    height: 270,
  });

module.exports = {
  id: "settings-exits",
  file: "strategy-settings-exits-guide.pdf",
  title: "Strategy settings 2: getting out. Exits, stops, targets, reverse and hand-offs told apart",
  html: () =>
    L.page({
      kicker: KICKER,
      foot: FOOT,
      title: "Strategy settings 2: getting out",
      lead: "Part 1 covered the signal, the checks and how the order is placed. This part covers everything after the trade is open: the difference between a stop, a target and an exit rule; the five stop types; the four SMA 9 exits; the seven settings with \"reverse\" or \"flip\" in the name; which setting moves a stock to which bot; and the limits that look alike. Each family gets a picture, a side-by-side table and one worked example.",
      body: `${FLOW_CSS}
${L.box("blue", "Same rules as part 1", `All examples use a ₹1,000 stock, so 0.1 % = ₹1, and an ATR of ₹4. "Group N" is the numbered group on Settings → Strategy. Everything is <b>off by default</b> unless the table says otherwise; the two things that are on from the start are the <b>ATR stop</b> and the <b>SMA cross exit</b>.`)}

<h2>1. Stop, target, exit rule: three different things that all close a trade</h2>
${L.fig(basicFig(), "The stop (₹994) and target (₹1,012) are lines on the price. The exit rule is a condition about candles: here \"3 candles against\", which closed the trade at ₹1,004.50 while neither line had been touched.")}
${L.table(["", "Stop-loss", "Target", "Exit rule"], [
  ["<b>Where</b>", "Group 5", "Group 5 (and Bollinger \"Band target\" in group 3)", "Group 3 (and gap mode's fade in group 4)"],
  ["<b>What it is</b>", "A price on the losing side.", "A price on the winning side.", "A condition: a cross, candle colours, a line bending…"],
  ["<b>Checked</b>", "On every price, about once a second.", "On every price.", "Only when a candle <b>closes</b>."],
  ["<b>Known in advance?</b>", "Yes: you can see the line on the chart.", "Yes.", "No: it depends on what the candles do."],
  ["<b>Job</b>", "Cap the loss.", "Bank a win.", "Leave when the reason for the trade is gone."],
  ["<b>In LIVE</b>", "A real stop order rests at the exchange.", "The bot watches the price and sends an exit.", "The bot sends an exit; the resting stop is cancelled first."],
])}
${L.box("amber", "Whichever comes first wins", `All three run together. A trade closes on the <b>first</b> one that fires; the others are dropped. If you untick the stop, untick the SMA cross exit and tick no other exit, the trade only ends at the square-off time. The Settings page warns you when no exit is on.`)}

<h2>2. Five stop types: where the stop sits and whether it moves</h2>
<p>Group 5 → <b>Stop type</b>. It is one menu: a trade has exactly one stop. They differ in <b>what sets the distance</b> and <b>what moves it</b>.</p>
${L.fig(stopsFig(), "The same buy at ₹1,000 (ATR ₹4) under three stop settings. ATR stop: stays at ₹994. Trailing stop (₹6 behind, a step every ₹2): climbs to ₹1,006 and is hit there, keeping ₹6. Staged plan: ₹994 → breakeven at +₹9 → follows the Supertrend line from +₹12.")}
${L.table(["Stop type", "Starts at (a buy at ₹1,000)", "Moves?", "Good for", "Mind"], [
  ["<b>ATR — fixed stop</b> (default)", "Entry − \"ATR stop ×\" × ATR. With 1.5 × ₹4 = ₹6: <b>₹994</b>.", "Never.", "A simple first set-up. Fits any stock because ATR does.", "A big winner can come all the way back to ₹994."],
  ["<b>Trailing stop (TSL)</b>", "Entry − \"Stop\". With ₹6: <b>₹994</b>.", "Up by \"Trail every\" for each full step of profit. Never back.", "Locking in profit as the price climbs.", "A tight trail is knocked out by ordinary wiggles."],
  ["<b>Supertrend</b>", "On the Supertrend line if it is below the price, else the ATR stop.", "Once per closed candle, to the new line. Tighter only.", "Riding a long trend.", "The line can be far away at the entry."],
  ["<b>VWAP + ATR buffer</b>", "VWAP − buffer × ATR. VWAP ₹997, buffer 0.5: <b>₹995</b>.", "Once per closed candle, as VWAP rises. Tighter only.", "Trades that should hold while the price is above VWAP.", "If VWAP is above your buy price it uses the ATR stop until it is not."],
  ["<b>SMA gap — moving stop + target</b>", "Gap % × \"Stop × gap\" below the price (part 1, section 6).", "Each closed candle. Tighter only.", "Gap mode.", "Practice (PAPER) only. LIVE uses the ATR stop."],
])}

<h3>The trailing stop, step by step</h3>
<p>Settings: <b>Stop ₹6</b>, <b>Trail every ₹2</b>, <b>Target ₹0</b> (none). Bought at ₹1,000.</p>
${L.table(["Best price since the buy", "Full ₹2 steps gained", "Stop is at", "If hit now you keep"], [
  ["₹1,000", "0", "₹994", { v: "−₹6", cls: "n r" }],
  ["₹1,001.90", "0 (not a full step yet)", "₹994", { v: "−₹6", cls: "n r" }],
  ["₹1,002", "1", "₹996", { v: "−₹4", cls: "n r" }],
  ["₹1,006", "3", "₹1,000", { v: "₹0", cls: "n" }],
  ["₹1,009", "4", "₹1,002", { v: "+₹2", cls: "n g" }],
  ["₹1,013", "6", "₹1,006", { v: "+₹6", cls: "n g" }],
])}
${L.box("", "\"Stop\" vs \"Trail every\"", `<b>Stop</b> is how far behind the stop <i>starts</i>. <b>Trail every</b> is the size of each <i>step</i> it then takes. They are often set equal, but need not be: Stop ₹6 / Trail ₹2 keeps the stop ₹6 to ₹8 behind the best price all the way.`)}

<h3>Four places where "ATR ×" is a different number</h3>
${L.table(["Name on the screen", "Where", "Meaning", "With ATR ₹4"], [
  ["<b>ATR stop ×</b>", "Group 5", "The fixed stop's distance. Used by stop type ATR (and as the fallback of Supertrend / VWAP).", "1.5 → ₹6"],
  ["<b>ATR target × (0 = no target)</b>", "Group 5", "A target on the profit side. Works with stop type ATR, Supertrend or VWAP.", "3 → ₹12 → ₹1,012"],
  ["Trailing units <b>ATR multiples</b>: Stop / Trail every / Target", "Group 5, stop type TSL", "The trailing stop's three numbers as multiples of ATR instead of ₹ or %.", "1.5 / 0.5 / 3 → ₹6 / ₹2 / ₹12"],
  ["<b>Buffer below / above VWAP</b>", "Group 5, stop type VWAP", "How far beyond VWAP the stop sits.", "0.5 → ₹2"],
])}
${L.box("green", "₹, % or ATR multiples?", `The trailing stop's numbers can be typed three ways. <b>₹</b>: ₹6 is tight on a ₹3,000 stock and wide on a ₹100 one. <b>%</b>: 0.6 % is ₹6 on ₹1,000 and ₹18 on ₹3,000, the same for a calm and a wild stock. <b>ATR multiples</b>: 1.5 × ATR is sized from how much <i>that</i> stock actually moves. If one bot trades many different stocks, use % or ATR multiples.`)}

<h3>The staged stop plan (a stop that changes its rule)</h3>
<p>Tick <b>Staged stop plan: breakeven, then a Supertrend trail</b> (group 5, needs stop type ATR). <b>R</b> is the first risk: buy ₹1,000, stop ₹994, so R = ₹6.</p>
${L.fig(
  F.flow(
    [
      { tag: "Stage 0", title: "ATR stop", tone: "red", lines: ["Stop at ₹994.", "Risk R = ₹6."] },
      { tag: "+1.5 R = ₹1,009", title: "Breakeven", tone: "amber", lines: ["Stop → entry + charges,", "about ₹1,000.60.", "The trade can no longer lose."] },
      { tag: "+2 R = ₹1,012", title: "Supertrend trail", tone: "green", lines: ["Stop follows the Supertrend", "line each candle,", "never under breakeven."] },
    ],
    { boxH: 74, gap: 34, label: "The staged stop plan's three stages" },
  ),
  "\"Breakeven at (R)\" and \"Supertrend trail from (R)\" are the two boxes. Stages only go forward.",
)}

<h2>3. Five things called a "target"</h2>
${L.table(["Name on the screen", "Where", "The target price is", "Works with"], [
  ["<b>ATR target ×</b>", "Group 5", "Entry + N × ATR, fixed at the entry. 3 × ₹4 → ₹1,012.", "Stop type ATR, Supertrend, VWAP"],
  ["Trailing stop's <b>Target</b>", "Group 5", "Entry + your ₹ / % / ATR number. 0 = none.", "Stop type TSL only"],
  ["SMA gap stop's <b>Target × gap</b>", "Group 5", "Gap % × the multiple above the price; moves each candle.", "Stop type SMA gap only (PAPER)"],
  ["<b>Pivot target</b>: R1 / R2", "Group 5", "Yesterday's pivot levels: R1 for a buy, S1 for a sell (R2 / S2 if the entry is already past R1).", "Any stop type"],
  ["Bollinger exit: <b>Band target</b>", "Group 3", "Not a fixed price: a candle <i>closing</i> at the far Bollinger band.", "Any stop type"],
])}
${L.box("amber", "Why your target box seems to do nothing", `Each target only works with its own stop type. Typing an <b>ATR target ×</b> while the stop type is <b>Trailing (TSL)</b> has no effect: the trailing stop uses its own Target box. Check the stop type first.`)}

<h2>4. Leaving on a cross: five look-alikes</h2>
${L.table(["Name on the screen", "Where", "Closes a buy when", "Then"], [
  ["<b>SMA cross exit</b> (on by default)", "Group 3, Main exit", "SMA 9 crosses below SMA 21 on the bot's own candle.", "Opens a sell at once (close <i>and reverse</i>)."],
  ["<b>Read the cross exit on</b> (another candle size)", "Group 3, Main exit", "The same cross, but on the candle size you pick. The cross on the bot's own candle is then ignored for exits.", "No reverse."],
  ["<b>Watch smaller candles</b> → SMA cross against the trade", "Group 3", "The cross happens on a <i>smaller</i> candle first.", "No reverse (unless picked in \"Reverse instead of only closing\")."],
  ["<b>Close when a candle closes beyond the slow SMA</b>", "Group 3", "A candle <i>closes below SMA 21</i>. The price crosses the slow line; the two averages need not have crossed.", "No reverse."],
  ["<b>Close when the EMAs cross against the trade</b>", "Group 3", "A second pair of lines (EMA 9 / 200 by default) crosses. The SMA pair is untouched.", "No reverse."],
])}
${L.cols([
  ["SMA cross exit OFF: what happens?", `<p>An opposite cross is <b>ignored while you hold a trade</b>. Crosses only open trades when the stock is flat. The trade then needs another way out: a stop, a target or a ticked exit, or it runs to the square-off.</p>`],
  ["Price crossing a line vs two lines crossing", `<p><b>Slow SMA exit</b>: the <i>price</i> closes through SMA 21. Early.<br><b>SMA cross exit</b>: <i>SMA 9</i> goes through SMA 21. Later, because an average of 9 candles turns slower than the price.</p>`],
])}

<h2>5. The four SMA 9 line exits</h2>
<p>Group 3 → <b>SMA 9 line exits</b>. All four watch the <b>slope</b> of the SMA 9 line (how much it moves per candle) and try to leave before the averages cross. They differ in <b>how early</b> they act. The lesson "SMA 9 slope: Turn exit and V-turn" has the full arithmetic.</p>
${L.fig(
  F.curveFig({
    points: [{ x: 0, y: 8 }, { x: 12, y: 26 }, { x: 24, y: 50 }, { x: 36, y: 68 }, { x: 48, y: 80 }, { x: 58, y: 86 }, { x: 68, y: 88 }, { x: 80, y: 84 }, { x: 92, y: 72 }, { x: 100, y: 60 }],
    marks: [
      { i: 2, text: "steepest climb", sub: "the trade is working", color: "#059669", y: "below", anchor: "start" },
      { i: 4, text: "① Deceleration guard", sub: "slope halved, still bending", color: "#7c3aed", y: "below", anchor: "start" },
      { i: 5, text: "② Bend exit", sub: "slope 60 % off its steepest", color: "#0284c7" },
      { i: 7, text: "③ Turn exit", sub: "line now falling", color: "#dc2626" },
    ],
    title: "The SMA 9 line during a buy that runs out of steam (a slow, rounded top)",
    height: 210,
  }),
  "On a slow top the exits fire in this order. The earlier ones keep more profit and also leave more trades that would have carried on.",
)}
${L.fig(
  F.curveFig({
    points: [{ x: 0, y: 10 }, { x: 14, y: 32 }, { x: 28, y: 56 }, { x: 40, y: 78 }, { x: 48, y: 88 }, { x: 56, y: 74 }, { x: 68, y: 50 }, { x: 84, y: 28 }, { x: 100, y: 14 }],
    marks: [
      { i: 3, text: "Steep run (with the trade)", color: "#059669", y: "below", anchor: "start" },
      { i: 5, text: "④ V-turn exit", sub: "turned back hard within 6 candles", color: "#b45309", anchor: "start" },
    ],
    title: "The SMA 9 line making a sharp V (upside down for a buy)",
    height: 180,
  }),
  "The V-turn needs both halves: a steep run with the trade and then a steep turn back, close together. A slow top like the first picture never counts as a V.",
)}
${L.table(["Name on the screen", "Fires when", "Typical use"], [
  ["<b>① Deceleration guard</b>: close when the line bends, before it turns", "The slope has dropped by half from its peak and has kept dropping for 2 candles. Measured in ATR per candle.", "The earliest warning. Tick \"Only when the candle also closes through the SMA 9\" for fewer early exits."],
  ["<b>② Bend exit</b> at % off the steepest", "The slope is 60 % lower than the steepest it has been in this trade. The line is still rising, just much slower.", "Leave a trend that has gone flat."],
  ["<b>③ Turn exit</b>", "The line is actually <i>falling</i> by at least \"Turn at\" (0.02 % a candle = 20 paise on ₹1,000).", "One level on its own: any fall that steep."],
  ["<b>④ V-turn exit</b>", "A steep run with the trade, then a steep turn back, within N candles.", "Only sharp reversals; ignores slow drifting."],
])}
${L.box("", "Three names nearby that are not exits", `<b>SMA 9 turn</b> in group 1 uses the same V to <i>enter</i>. <b>Read the SMA 9 slope on</b> picks the candle size for all four. <b>After a V-turn exit at a loss, move the stock to Bot 2</b> is a hand-off (section 9).`)}

<h2>6. "How exits combine": any one, or several together</h2>
${L.cols([
  ["OR (default): the first one closes", `<p>Every ticked exit works alone. With <b>Supertrend exit</b> and <b>Counter candles 3</b> ticked, the trade closes when <i>either</i> happens.</p><div class="ex">3 red candles close → trade closed, even though the Supertrend is still green.</div>`],
  ["AND: they must agree", `<p>The exits you put in the group only <b>vote</b>. The trade closes when enough of them have fired within a few candles of each other.</p><div class="ex">3 red candles close → "exit noted, holding". Two candles later the Supertrend flips → both agree → trade closed.</div>`],
])}
${L.box("amber", "AND never holds back a stop", `Only exit rules can be grouped. Every stop, every target, gap mode's fade, the square-off, the loss limit and the kill switch always act on their own. "Must fire within (candles)" is how close together the votes must be: 3 by default.`)}

<h2>7. Seven settings with "flip" or "reverse" in the name</h2>
${L.fig(
  F.flow(
    [
      { title: "Signal: BUY", tone: "blue", lines: ["SMA 9 crosses", "above SMA 21"] },
      { title: "Checks pass", tone: "violet", lines: ["read for a BUY,", "as always"] },
      { tag: "Flip strategy ON", title: "Order sent: SELL", tone: "amber", lines: ["the order goes", "the other way"] },
      { title: "Stop guards the SELL", tone: "red", lines: ["above the price,", "the real position"] },
      { title: "Closes like the BUY", tone: "green", lines: ["when the buy would", "have closed"] },
    ],
    { boxH: 70, label: "What the flip strategy does to one signal" },
  ),
  "Flip strategy: nothing about <i>when</i> changes, only the side of the order. It is for a stock on which the strategy keeps being wrong.",
)}
${L.table(["Name on the screen", "Where", "When it acts", "What it does"], [
  ["<b>Flip strategy</b> — buy signals sell, sell signals buy", "Group 1, Order direction", "On <b>every</b> entry.", "Sends each order the opposite way. Timing unchanged."],
  ["<b>SMA cross exit</b> — an opposite cross closes and reverses", "Group 3", "On an opposite cross.", "Closes the buy and opens a sell: the normal way the strategy turns round."],
  ["<b>Reverse instead of only closing</b>", "Group 3", "After the exits you tick there: V-turn, turn, counter candles, smaller candles.", "Those exits normally just close. With this, they also open the other side (every check still applies)."],
  ["<b>After a losing trade, enter the opposite way</b>", "Group 1, under Always in a trade", "After any trade that lost money.", "The next entry goes against the last trade's side, whatever the averages say."],
  ["<b>Reverse</b> button", "On the chart and Open positions", "When you press it.", "Closes the trade and opens the other side now. A manual order; no setting."],
  ["Ask me → <b>Exit and reverse</b>", "Group 7", "When the averages are uncertain.", "The automatic answer to the \"Ask me\" prompt: close and open the other side."],
  ["<b>Bot 2 orders at once</b>: the opposite of this bot's trade", "Group 7", "After a hand-off to Bot 2.", "Bot 2 immediately takes the other side of the trade this bot just closed."],
])}
${L.box("green", "Flip vs reverse in one line", `<b>Flip</b> changes which way an order goes <i>when it is opened</i>. <b>Reverse</b> opens a <i>second, opposite</i> trade <i>after one closes</i>. A flipped stock can also reverse: both can be on.`)}

<h2>8. Holding on instead of closing</h2>
<p>Some settings do the opposite of an exit: they stop a trade from being closed yet.</p>
${L.table(["Name on the screen", "Where", "What is held", "Until"], [
  ["<b>SMA cross exit</b> unticked", "Group 3", "Every trade, through an opposite cross.", "A stop, target or another exit."],
  ["<b>Recovery hold</b>: close a winner, hold a loser for its recovery", "Group 7", "Only a trade that is <i>losing</i> at an opposite cross. A winner closes and reverses as usual.", "It gets back near its entry (within 0.15 %) or touches SMA 21; or gives up after 6 candles or a deeper loss."],
  ["<b>SMA 9 turns and SMA 9 exits close a trade only on a candle of the other colour</b>", "Group 1", "An SMA 9 exit that fires while the candle is still the trade's own colour.", "The first candle of the other colour."],
  ["How exits combine: <b>AND</b>", "Group 3", "A trade where only one grouped exit has fired.", "The others agree."],
  ["<b>Ask me when the SMAs are uncertain</b>", "Group 7", "Nothing by itself: no answer keeps the trade.", "You tap EXIT, or the normal exits act."],
])}
${L.box("red", "Recovery hold needs a stop", `It deliberately keeps a losing trade open, hoping for a bounce. Keep the stop-loss on, and leave its two floors alone until you have replayed it: "Loss floor" (1.5 × the loss at the cross) and "ATR floor" (2.5 × ATR) are what end a hold that keeps getting worse.`)}

<h2>9. Moving a stock to another bot: which setting sends it where</h2>
<p>The idea: Bot 1 trades many stocks the standard way. A stock that turns out <b>choppy</b> goes to Bot 2 (set up for chop), one that is <b>trending</b> goes to Bot 3, one that is <b>moving fast</b> goes to Bot 5. Each arrow below is a separate tick box, all off by default.</p>
${L.fig(F.botsMap(), "A hand-off takes the stock off one bot's Trade list and arms it on another. It places no order by itself, except where the table says so. Two LIVE bots never hold the same stock.")}
${L.table(["Name on the screen", "Where", "Trigger", "Goes to", "Does the new bot order at once?"], [
  ["<b>After a recovery, move the stock to Bot 2</b>", "Group 7", "A recovery hold ended at its target.", "Bot 2", "No, it waits for its next cross (yes with \"Bot 2 orders at once\")."],
  ["<b>After a V-turn exit at a loss, move the stock to Bot 2</b>", "Group 3", "The V-turn exit closed a losing trade.", "Bot 2", "Same."],
  ["<b>Move to Bot 2 after this many losing trades</b> on a stock today", "Group 7", "N losing trades on that stock today.", "Bot 2", "Same."],
  ["<b>After a stop-loss, move to</b>", "Group 5", "A stop (not a target) closed the trade.", "Your pick: back where it came from, Bot 2 or Bot 3", "No."],
  ["<b>On a breakout, move the stock on</b>", "Group 7", "One tall candle, or a strong trend (your pick).", "Back where it came from, else Bot 3", "Yes: Bot 3 enters on the breakout's side, waiting for a small pullback."],
  ["<b>Fast movers → Bot 5</b>", "Settings → Bots (not the strategy form)", "A stock a bot holds moves fast in the trade's favour, already in profit.", "Bot 5", "Yes: the same side, at once."],
  ["<b>Send stocks back at the end of the day</b>", "Settings → Bots", "After 15:35.", "The bot each stock started on", "No. Arming only."],
])}
${L.cols([
  ["Two words for the same Bot 2 move", `<p>The screen says <b>"(flipped, gap mode)"</b> after a move to Bot 2. That is Bot 2's recipe for a choppy stock: orders flipped, entries by SMA gap mode, VWAP and RSI checks. You do not set those on the sending bot.</p>`],
  ["Breakout: \"whenever\" vs \"only in profit\"", `<p><b>Whenever it shows</b>: the stock moves even if this bot never traded it. <b>Only while this bot's trade rides it in profit</b>: this bot keeps its winning trade, and the trend bot opens the same side too.</p>`],
])}

<h2>10. Limits and times that look alike</h2>
${L.fig(L.timeline([
  { at: "09:20", text: "first entries", color: "#059669" },
  { at: "09:30", text: "join trend / opening checks", color: "#0284c7" },
  { at: "15:00", text: "No new entries after", color: "#b45309" },
  { at: "15:15", text: "Square-off", color: "#dc2626" },
]), "The default day. Between 15:00 and 15:15 an open trade can still close, but nothing new opens.")}
${L.table(["Name on the screen", "Where", "Counts", "When it is reached"], [
  ["<b>Max trades / day</b>", "Group 6", "Every entry on this bot, all stocks together.", "No more entries on this bot today. Open trades run to their own exits."],
  ["<b>Max trades per stock / day</b>", "Group 6", "Entries on one stock.", "No more entries on that stock today. The bot carries on with the others."],
  ["<b>Re-entries per SMA run</b>", "Group 3, counter candles", "Re-entries after a counter-candle exit, within one trend.", "No more re-entries until the next cross."],
  ["<b>Max daily loss</b>", "Group 6", "This bot's loss today, after charges.", "This bot closes everything and stops."],
  ["<b>All bots: day target and stop loss</b>", "Settings → Bots", "Bots 1-4 added together, before charges.", "Every bot closes everything and stops."],
  ["<b>Stop-loss</b>", "Group 5", "One trade's price.", "That one trade closes. The bot carries on."],
  ["<b>No new entries after</b>", "Group 6", "The clock.", "No new trade opens. Open trades stay."],
  ["<b>Square-off</b>", "Group 6", "The clock.", "Every open trade is closed."],
])}
${L.box("amber", "Daily loss limit vs stop-loss", `The <b>stop-loss</b> protects one trade: ₹6 a share on 100 shares = ₹600. The <b>daily loss limit</b> protects the day: with ₹5,000, the bot stops after losing that much in total, however many trades it took. Set both.`)}

<h2>11. Who does a setting apply to? Four levels</h2>
<div class="box" style="background:#f5f3ff;border-color:#ddd6fe">
  <b class="t">All bots together · Settings → Bots</b>
  Day target and stop loss for Bots 1-4, P&amp;L alerts, fast movers → Bot 5, send stocks back.
  <div class="box" style="background:#f0f9ff;border-color:#bae6fd">
    <b class="t">One bot · the bot tabs at the top of Settings → Strategy</b>
    Each bot (1-5 and Research) has its own full copy of groups 1-7. Changing Bot 1 never changes Bot 3.
    <div class="box" style="background:#ecfdf5;border-color:#a7f3d0">
      <b class="t">Shared settings · "Settings for: All stocks"</b>
      What every stock on that bot uses unless it has its own value.
      <div class="box" style="background:#fffbeb;border-color:#fde68a;margin-bottom:0">
        <b class="t">One stock's own settings · "Settings for: SBIN"</b>
        Only the boxes you change for that stock, marked <span class="pill p-amber">own</span>. Everything else still follows the shared settings.
      </div>
    </div>
  </div>
</div>
${L.box("green", "Example", `Bot 1's shared stop is ATR × 1.5. You pick "Settings for: RELIANCE" and set a trailing stop of 0.5 %. Now RELIANCE on Bot 1 trails at 0.5 %; every other stock on Bot 1 keeps ATR × 1.5; RELIANCE on Bot 3 is not affected at all. Later you change the shared stop to ATR × 2: RELIANCE keeps its own trailing stop.`)}

<h2>12. A safe first set-up, and what to add next</h2>
${L.steps([
  "<b>Group 1</b>: Enter on SMA cross, candle interval 5 minutes. Nothing else ticked.",
  "<b>Group 5</b>: stop on, stop type Trailing, units % (for example Stop 0.6 %, Trail every 0.2 %), or leave the ATR stop at 1.5.",
  "<b>Group 6</b>: quantity, Max daily loss, Max trades per stock / day 3, No new entries after 15:00, Square-off 15:15.",
  "<b>Group 3</b>: leave the SMA cross exit on. Add <i>one</i> earlier exit only after a replay shows trades giving back profit: counter candles 3, or the Supertrend exit.",
  "<b>Group 2</b>: add one check at a time (VWAP first), replaying the same days after each.",
  "Leave groups 4 and 7 until the rest has run for a few weeks in PAPER.",
])}
${L.table(["I want to…", "Use this", "Group"], [
  ["lock in profit as the price runs", "Stop type Trailing (TSL), or the Staged stop plan", "5"],
  ["take a fixed profit", "ATR target × (ATR stop) or the trailing stop's Target", "5"],
  ["get out before the averages cross back", "Counter candles, the SMA 9 exits, or Watch smaller candles", "3"],
  ["stop being shaken out by one bad candle", "How exits combine: AND, or the \"other colour\" hold", "3, 1"],
  ["stop losing on a choppy stock", "Max trades per stock / day; Move to Bot 2 after N losing trades", "6, 7"],
  ["trade the opposite of a stock that is always wrong", "Flip strategy, set for that one stock", "1"],
  ["stop the whole day at a loss", "Max daily loss (one bot), All bots: day stop loss", "6, Bots"],
])}

<h2>13. Words used here</h2>
${L.words([
  ["Stop-loss", "A price on the losing side at which the trade is closed.", "Buy ₹1,000, stop ₹994."],
  ["Trailing stop", "A stop that follows the price up in steps and never comes back down.", "Stop ₹6, trail every ₹2."],
  ["R", "The first risk of a trade: entry minus the first stop.", "₹1,000 − ₹994 = ₹6. +2R = ₹1,012."],
  ["Breakeven", "The price at which the trade neither wins nor loses after charges.", "About ₹1,000.60."],
  ["Slope", "How much the SMA 9 line moves per candle, in % of the price.", "0.02 % on ₹1,000 = 20 paise a candle."],
  ["Reverse", "Close a trade and open the opposite one.", "Sell the 100 bought, then sell 100 more short."],
  ["Hand-off", "Moving a stock from one bot's Trade list to another's.", "Bot 1 → Bot 2 after a recovery."],
  ["Square-off", "Closing every open trade at a set time.", "15:15."],
])}
`,
    }),
};
