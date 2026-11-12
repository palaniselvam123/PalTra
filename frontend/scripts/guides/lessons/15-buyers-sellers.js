const L = require("../lib");

module.exports = {
  id: "buyers-sellers",
  file: "buyers-sellers-guide.pdf",
  title: "Buyers vs sellers: the % column, the day list, the test and the alert",
  html: () =>
    L.page({
      title: "Buyers vs sellers: the % column, the day list, the test and the alert",
      lead: "The idea: when most of the orders waiting to trade are buy orders, the price should go up, and when most are sell orders it should go down. PalTra shows that share for every busy stock, lists the stocks that reached your % on a day and whether the price then moved, tests the idea over many days, and can send a Telegram. This lesson explains every column with real rows, how to read the answer, and how to use it.",
      body: `
${L.box("blue", "Information only", `Nothing on these screens buys or sells. They tell you how often the idea was right. Arming a stock on a bot is always your decision, and a practice (PAPER) bot is the place to try it first.`)}

<h2>1. What "buyers 75 %" means</h2>
<p>Every stock has a queue of orders waiting to trade: people who want to buy at a price a little below the market (bids) and people who want to sell a little above it (asks). Groww tells us the <b>total quantity waiting on each side</b>. PalTra divides:</p>
${L.box("", "", `<b>Buyers %</b> = shares waiting to <b>buy</b> ÷ (shares waiting to buy + shares waiting to sell) × 100.<br>
Example: 1,20,000 shares waiting to buy and 40,000 waiting to sell → 1,20,000 ÷ 1,60,000 = <b>75 % buyers</b> (so <b>25 %</b> sellers).<br>
<b>Sellers 75 %</b> is the mirror: buyers 25 %, sellers 75 %.`)}
${L.cols([
  ["What it is", `<ul><li>A count of orders <b>waiting</b> in the order book, at that moment.</li><li>Read on every Most active scan (every few minutes), and saved with the scan.</li><li>Shown on the Most active card as the <b>Buyers vs sellers</b> bar and %.</li></ul>`],
  ["What it is not", `<ul><li>Not the trades that already happened: Groww does not split traded volume into buys and sells.</li><li>Not permanent: big orders can be placed and pulled in seconds.</li><li>Not a price target: it says who is waiting, not how far the price will go.</li></ul>`],
])}

<h2>2. The day list: "Which stocks reached it that day, and did the price then move?"</h2>
<p>Scalp page → <b>Most active</b> → view <b>Buyers / sellers test</b> → the first box. You type two numbers and pick a day:</p>
${L.screen("Which stocks reached it that day", [
  `Buyers / sellers at least ${L.chip("75 %", true)} &nbsp; Price must move at least ${L.chip("0.5 %", true)} &nbsp; Day ${L.chip("2026-11-11", true)} ${L.btn("Scan the day", true)} <span class="m">1 day · 75 saved scans · 75 stocks reached 75%</span>`,
])}
${L.table(["Box", "Meaning", "A good start"], [
  ["<b>Buyers / sellers at least</b>", "The share a stock must reach in any saved scan of the day to be listed. Any number 50-99.", "75. Try 70 and 85 too."],
  ["<b>Price must move at least</b>", "How far the price must go <i>its way</i> afterwards to count as \"correct\": up after buyers, down after sellers. Any number 0.05-20.", "0.5 %. Lower (0.3) for scalping, higher (1) for bigger moves."],
  ["<b>Day</b>", "One saved day, or \"All saved days (last 20)\".", "Yesterday first, then all 20 days."],
  ["<b>Scan the day</b>", "Reads every saved Most active scan of that day. No Groww call, nothing is ordered.", ""],
])}
<p>The grey line after the button says how much it read: <i>1 day · 75 saved scans · 75 stocks reached 75 %</i> means 75 scans were saved that day and 75 stock-and-side pairs reached your % at least once.</p>

<h3>The answer lines and the three cards</h3>
${L.box("green", "From 11 Nov, 75 % and 0.5 %", `15 times buyers reached 75%: the price then went up 0.5% or more 2 times (13.3% correct), taking about 21 minutes.<br>
60 times sellers reached 75%: the price then went down 0.5% or more 16 times (26.7% correct), taking about 116 minutes.<br>
Overall your assumption was right 24% of the time at a 0.5% move (18 of 75).`)}
${L.table(["On each card", "Meaning", "11 Nov example (Buyers → price up)"], [
  ["<b>N % correct</b>", "Of the stocks that reached your %, how many then moved your % their way before the day's last scan.", "13 % correct = 2 of 15."],
  ["<b>N of M moved X %</b>", "The same as a count.", "2 of 15 moved 0.5 %."],
  ["<b>about N min</b>", "For the ones that did move: the average minutes from first reaching the % to reaching the move.", "about 21 min."],
  ["<b>Best move on average</b>", "Average of each stock's best move its way after it first reached the % (even the ones that never got to your move).", "+0.23 %: on average the best they managed was a quarter of a percent."],
  ["<b>ended the right side</b>", "How many were above (buyers) or below (sellers) their price-then at the day's last scan.", "27 %: three in four ended on the wrong side."],
])}
${L.box("amber", "How to judge the answer", `Ask: <b>is "correct" well above what any stock does anyway?</b> Plenty of stocks move 0.5 % at some point in a day without any signal, so a low % correct (13-27 % on 11 Nov) says the share did not pick the movers well that day. The <b>test</b> below (section 4) measures what any stock does over the same time ("Any stock") and gives the difference as the "Edge". Look at more days ("All saved days") before deciding; one day is a first look, not proof.`)}

<h3>The filters above the table</h3>
${L.table(["Filter", "Choices"], [
  ["<b>Who</b>", "Buyers and sellers · Buyers only · Sellers only."],
  ["<b>Show</b>", "All · Price moved (correct) · Price did not move (wrong). \"15 shown\" counts the rows left."],
])}

<h2>3. Every column of the day list, with real rows</h2>
${L.table(["Column", "What it says", "How it is worked out"], [
  ["<b>Stock</b>", "The stock. The ↗ opens it on Groww.", ""],
  ["<b>Who</b>", "Buyers or Sellers, and the share at the first scan that reached your %.", "\"Buyers 82%\" = 82 % of waiting orders were buys at that scan."],
  ["<b>First reached</b>", "The time of the first saved scan that day in which the share was at or above your %.", "Only the first time counts; later scans do not reset it."],
  ["<b>Highest · scans</b>", "The highest share seen that day, and in how many saved scans it was at or above your %.", "\"83% · 47\" = it peaked at 83 % and held 75 %+ in 47 scans."],
  ["<b>Price then</b>", "The last traded price at that first scan. Everything after is measured from here.", "The price saved with the scan."],
  ["<b>Moved X %?</b>", "Yes (with the time and minutes taken) if the price went your % its way at a later saved scan before the day's last one; No if it never did.", "Up X % after buyers, down X % after sellers."],
  ["<b>Best after</b>", "The furthest it went its way after the first scan, and when. Minus = it never went its way at all.", "Signed: + always means \"the way the idea says\"."],
  ["<b>Worst after</b>", "The furthest it went against the idea afterwards.", "Signed: − = against."],
  ["<b>At the end</b>", "Where it was at the day's last saved scan, against price then, signed the idea's way.", ""],
])}

<h3>Row by row: three examples from 11 Nov (buyers 75 %, move 0.5 %)</h3>
${L.table(["Stock", "Reading", "Verdict"], [
  ["<b>KALYANKJIL</b> · Buyers 100 % at 09:20 · 100 % · 3 scans · ₹672.10 · <b>Yes · 09:56 (36 min)</b> · best +2.37 % 09:56 · worst −1.75 % · end −1.50 %",
   "Buyers held every waiting order for 3 scans. 0.5 % up from ₹672.10 is ₹675.46; it got there at 09:56 and went as far as +2.37 % (about ₹688). Then it fell to −1.50 % by the close.",
   "<b>Correct</b>, but only for a quick trade: a target of ₹675-688 worked, holding to the close lost money."],
  ["<b>PATANJALI</b> · Buyers 82 % at 09:35 · 83 % · 47 scans · ₹433.00 · <b>No</b> · best +0.22 % 12:17 · worst −2.89 % · end −0.69 %",
   "0.5 % up from ₹433.00 needs ₹435.17. The best it did was +0.22 % (about ₹433.95) at 12:17; the chart's highest candles after 09:35 are near ₹434-434.5, still short. It later fell 2.89 %.",
   "<b>Wrong</b>, even though buyers stayed above 75 % in 47 scans: a big buy queue that never lifted the price."],
  ["<b>ADANIENSOL</b> · Buyers 76 % at 15:17 · ₹1414.00 · <b>Yes · 15:23 (6 min)</b> · best +0.99 %",
   "Reached late in the day and moved 0.5 % six minutes later.",
   "Correct, but at 15:17 there is little time left to trade it."],
])}
${L.box("", "Why PATANJALI says No while the chart rose a little", `"Moved" needs the full % <b>from the price at the first scan</b> (₹433.00), not from the day's low. And the list reads the price <b>saved with each scan</b>, every few minutes: a one-minute spike between two scans that came straight back is not counted. A long run of buyer-heavy scans (47) is not the same as a rising price.`)}

<h2>4. The test over many days: "Does the price follow the buyers or sellers?"</h2>
<p>The same view, further down. Instead of one day it looks at every saved day (1-60) and compares the stocks that <b>newly</b> reached your % with <b>any stock at any time</b>.</p>
${L.table(["Column", "Meaning"], [
  ["<b>When</b>", "5, 15, 30, 60 minutes after the signal, and at the close."],
  ["<b>Signals</b>", "How many times a stock newly reached your % (the same stock and side counts again only after the \"gap\" minutes, so one long stretch is one signal)."],
  ["<b>Right</b>", "How often the price had moved the idea's way by then (up after buyers, down after sellers)."],
  ["<b>Any stock</b>", "How often any stock moved that way over the same time: the base rate, what you would get by picking at random."],
  ["<b>Edge</b>", "Right − Any stock, in points. The only number that says the idea helps."],
  ["<b>Avg move</b>", "The average move the idea's way."],
  ["<b>Verdict</b>", "Strong (edge ≥ +10), Some (≥ +5), Weak, None, or Few (under 30 signals: not enough to judge)."],
])}
${L.fig(L.bars([
  { label: "Right (buyers 75%)", value: 54, color: "#059669", text: "54 %" },
  { label: "Any stock", value: 47, color: "#94a3b8", text: "47 %" },
  { label: "Edge", value: 7, color: "#0284c7", text: "+7 points → Some" },
], { max: 60 }), "Made-up example, 30 minutes later: the buyer signals went up 54 % of the time while any stock went up 47 %. The edge is +7 points: a little help, not a lot.")}
<p><b>Which share works best?</b> The second table runs the same test at 60, 65, 70, 75, 80, 85 and 90 %, 30 minutes later. Pick the level with the biggest edge <i>and</i> enough signals; a 90 % level with 12 signals and +15 is luck until it has 30.</p>
<p><b>The signals</b> table lists each one (day, time, stock, who, price then, the move at each time and the close): green = the price followed the idea.</p>

<h2>5. The Telegram alert</h2>
<p>Scalp page → <b>Alerts</b> tab → <b>Buyers or sellers in control</b>. After each finished Most active scan in market hours, every stock whose buyers (or sellers) hold at least your % (any number, 70 by default) is sent with its price, share, day move and volume vs usual, each stock and side once a day, at most 15 names a message. Off until you switch it on.</p>
${L.box("green", "Telegram", `SBIN · 🟢 Buyers 74% · ₹812.40 · day +1.10% · volume 2.3× usual`)}

<h2>6. How to use it, step by step</h2>
${L.steps([
  "Run the <b>day list</b> on the last few days at 75 % and 0.5 %. Note the % correct for buyers and for sellers separately.",
  "Run the <b>test</b> over all saved days. Look at the <b>Edge</b> at 15 and 30 minutes. Strong or Some on enough signals (30+) means the idea is worth trying; Weak / None means skip it.",
  "Look at the <b>Which share works best?</b> table and pick the level with the best edge that still has 30+ signals.",
  "If it helps, combine: buyers ≥ your % <b>and</b> Bias Long <b>and</b> volume ≥ 1.5× usual on the Most active card. Use <b>Trade a saved scan</b> to see what those stocks would have made.",
  "Switch on the <b>alert</b> at that level, and arm the stocks it sends on a <b>practice bot</b> with a stop. Check after two weeks with the day list whether it still holds.",
])}
${L.box("amber", "Read the timing", `The \"about N min\" on the cards matters: on 11 Nov the buyer moves came in about 21 minutes and then often reversed (KALYANKJIL: +2.37 % then −1.50 %). If the idea works, it suits a quick target or a trailing stop, not holding to the close.`)}

<h2>7. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>Tests a popular idea on your own saved data instead of believing it.</li><li>Any % and any move can be tried without a new download.</li><li>Shows when it worked and how fast, so you can size the target.</li></ul>`],
  ["Limits", `<ul><li>Waiting orders, not trades: they can be pulled.</li><li>Prices are read every few minutes, not every tick.</li><li>Only days the Most active scan ran and saved count; past order books cannot be downloaded.</li><li>Before charges, and one day is never proof.</li></ul>`],
])}

<h2>8. Words used here</h2>
${L.words([
  ["Order book", "The list of buy and sell orders waiting to trade at each price.", "1,20,000 shares bid, 40,000 offered."],
  ["Buyers %", "Shares waiting to buy ÷ all shares waiting, × 100.", "75 %."],
  ["Price then", "The last traded price at the first scan that reached your %.", "₹433.00."],
  ["Moved X %", "The price went X % the idea's way at a later scan the same day.", "₹433.00 → ₹435.17 for 0.5 %."],
  ["Best / worst after", "The furthest the price went with / against the idea after that first scan.", "+0.22 % / −2.89 %."],
  ["Base rate (Any stock)", "How often any stock moved that way over the same time.", "47 %."],
  ["Edge", "Right − Any stock, in points.", "54 − 47 = +7."],
  ["Signal", "A stock newly reaching your % (not every scan of a long stretch).", ""],
])}
`,
    }),
};
