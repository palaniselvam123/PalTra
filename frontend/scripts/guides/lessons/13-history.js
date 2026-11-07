const L = require("../lib");

module.exports = {
  id: "scalp-history",
  file: "scalp-history-guide.pdf",
  title: "History: every saved scan, and did it come true?",
  html: () =>
    L.page({
      title: "History: every saved scan, and did it come true?",
      lead: "Every finished Cross scan and Trend quality scan is saved with its time and kept for 90 days. This tab shows exactly what any past scan said, lets you download it, and after the close grades it: what each stock did 15, 30 and 60 minutes later and at the close, and whether the cross it promised came.",
      body: `
${L.box("blue", "Why keep old scans", `A scan is a prediction: \"these stocks are trending\", \"this one crosses in 4 minutes\". Without the record you only remember the hits. With it, you can grade every call honestly, and the Most active card's \"Which column finds the trend?\" test and the Trend quality card's \"First seen\" column both read from this history.`)}

<h2>1. What is saved, and where</h2>
${L.table(["Scan", "Saved here?", "Note"], [
  ["Cross scan", "Yes, every finished pass (manual or auto).", "With its candle size and the SMA periods used."],
  ["Trend quality", "Yes, every finished pass, including the 30-minute auto re-scan.", "With the quiet minutes and price range."],
  ["Most active", "In its own card: the Live / History switch on the Most active tab.", "Same store, different place on screen."],
  ["Made on the backtest server", "Copied to the desk as they finish; <b>Pull from backtest server</b> fetches any that were missed (last 30 days).", "So the desk's History is complete even when that server is stopped."],
])}

<h2>2. Check accuracy: the picture</h2>
${L.fig(L.timeline([
  { at: "10:00", text: "scan saved: SBIN trending ↑ · ITC cross ↓ in 6 min", color: "#0284c7" },
  { at: "10:15", text: "+15 min: SBIN +0.3%", color: "#059669" },
  { at: "10:30", text: "+30 min: SBIN +0.6% · ITC crossed ↓ at 10:08 ✓", color: "#059669" },
  { at: "11:00", text: "+60 min: SBIN +0.9%", color: "#059669" },
  { at: "15:30", text: "close: SBIN +1.4% (best +1.7%, worst −0.2%)", color: "#334155" },
], 720, "The check downloads that day's 1-minute candles again and reads each stock from the scan's minute onward."), "Each call is graded on its own side: a trending-up call by the rise after it, a cross-down call by whether the cross came as predicted and within double the estimate.")}

<h2>3. The accuracy columns, in plain words</h2>
${L.table(["Column", "Meaning", "Good"], [
  ["Call", "What the scan said: the side (Cross scan: the cross it expected; Trend quality: the fast SMA's side on that candle).", ""],
  ["15 min / 30 min / 1 hour / At close", "The move from the scan's minute, signed the call's way (positive = it went as called).", "Positive and growing."],
  ["Best / Worst", "The furthest it went with the call and against it, before the close.", "A small worst and a big best: the call was tradeable."],
  ["Next cross", "Did the next SMA cross come as predicted (same side, within double the estimated minutes), and how long the trend held after.", "Crossed as predicted."],
  ["Summary by group", "Hit rates for all stocks, quiet runners, strong trend, trending, about to cross / just crossed.", "The group that hits most is the one to trade."],
])}

<h2>4. How to use it</h2>
${L.screen("Scan history", [
  `${L.chip("Day: Thu 6 Nov · 14 scans", true)} ${L.chip("Scanner: Trend quality")} ${L.chip("Time: 10:00")} ${L.btn("‹")} ${L.btn("›")} ${L.btn("CSV")} ${L.btn("PDF")} ${L.btn("Check accuracy", true)} ${L.btn("Pull from backtest server")}`,
  `<span>☐</span><b>SBIN</b> <span class="pill p-green">TRENDING</span> <span>call ↑</span> <span class="g">15 min +0.3%</span> <span class="g">30 min +0.6%</span> <span class="g">1 h +0.9%</span> <span class="g">close +1.4%</span> <span>best +1.7% · worst −0.2%</span>`,
  `<span>☐</span><b>ITC</b> <span class="pill p-red">CHOPPY</span> <span>call ↓</span> <span class="r">15 min +0.2%</span> <span class="r">30 min +0.4%</span> <span class="r">close +0.3%</span> <span>crossed back 10:40 ✗</span>`,
  `<b>Summary:</b> <span>all 38 stocks: 55% kept going at 1 h · strong trend (9): 78% · trending (15): 67% · choppy (14): 36%</span>`,
])}
${L.steps([
  "Pick the <b>day</b> and the <b>scanner</b>; the time list shows every saved pass. ‹ › steps through them.",
  "The table is the scan as it was, with the same sorting and search as the live card. <b>CSV</b> / <b>PDF</b> download it.",
  "After the close press <b>Check accuracy</b> (refused in market hours on the desk, to spare the bots' Groww limit; the backtest server runs it any time). The columns fill in and the summary gives the hit rate per group.",
  "Compare days: a group that hits 70 % on most days is worth a Scan &amp; arm schedule; one that hits 50 % is a coin toss.",
  "Tick rows and <b>Arm ticked</b> if a saved scan's stocks are still interesting today (arming only).",
])}

<h2>5. A worked example</h2>
${L.box("", "", `Thursday's 10:00 Trend quality scan, checked Friday morning. Of 38 stocks, 55 % were still going the called way an hour later. The <b>strong trend</b> group did much better: 7 of 9 (78 %), average +0.8 % at the close. The <b>choppy</b> group did worst, 36 %, as the label promised. Reading: at 10:00, trade the strong trends and leave the choppy ones; the Backtest tab's strong-trend picks test then confirms it with the real bot.`)}
${L.box("amber", "One day is one day", `Grade a week of scans before you change a routine. Groww's candles are downloaded again for the check, so a day whose candles Groww no longer serves shows no result.`)}

<h2>6. Benefits and limits</h2>
${L.cols([
  ["Why it helps", `<ul><li>An honest record of every call, not only the ones you remember.</li><li>Grades by group, so you learn which label to trust.</li><li>Feeds the \"Which column finds the trend?\" test and \"First seen\".</li></ul>`],
  ["Limits", `<ul><li>A scan is graded on candles, not on a trade: no stop, no charges.</li><li>The check needs the day's candles from Groww; after hours only on the desk.</li><li>Information only; nothing here places an order.</li></ul>`],
])}

<h2>7. Words used here</h2>
${L.words([
  ["Saved scan", "One finished pass of a scanner, with its time, settings and every row."],
  ["Check accuracy", "Reading the day's candles after the close to see what each scanned stock did afterwards."],
  ["Call", "What the scan predicted for a stock: its side or its coming cross."],
  ["Kept going", "The price moved the called way at the checked time."],
  ["Hit rate", "The share of calls that kept going."],
  ["Pull", "Copying scans made on the backtest server into the desk's history."],
])}
`,
    }),
};
