const L = require("../lib");

// Short sellers buying back: falls most of the day, then jumps in the last hour.
const cover = [500, 499.2, 498.5, 497.9, 497.1, 496.6, 496.0, 495.4, 495.0, 494.6, /* 14:30 */ 495.8, 497.6, 499.4, 500.8, 501.6];
// Real demand: rises through the day and closes at the high.
const demand = [500, 501.2, 502.0, 503.1, 503.9, 505.0, 505.6, 506.8, 507.5, 508.4, 509.0, 509.9, 510.6, 511.5, 512.4];

module.exports = {
  id: "scalp-after-close",
  file: "scalp-after-close-guide.pdf",
  title: "After the close: real buyers, or short sellers buying back?",
  html: () =>
    L.page({
      title: "After the close: real buyers, or short sellers buying back?",
      lead: "Sometimes a stock jumps in the last part of the day, and after the market shuts Groww shows far more buy orders waiting than sell orders. This tab finds those stocks and tells you, in plain words, whether it looks like real buyers or short sellers buying back what they sold earlier.",
      body: `
${L.box("blue", "In one line", `<b>Many buyers still waiting + shares taken home = real demand.</b> <b>A fall all day, a jump at the end, shares not taken home = short sellers buying back.</b>`)}

<h2>1. Two reasons a stock jumps at the end of the day</h2>
${L.cols([
  ["Real demand", `People want the stock. They keep buying, and many cannot get it, so <b>buy orders are still waiting</b> after the market shuts. Many of them <b>take the shares home</b> (delivery). The rise often carries on the next day.`],
  ["Short sellers buying back", `A short seller sold shares in the morning hoping to buy them back cheaper. In intraday trading they <b>must buy back before about 15:20</b>, or the broker does it for them. All that buying pushes the price up at the end, even with no new real buyer. The shares are not taken home, and the jump often <b>fades the next day</b>.`],
])}

<h2>2. What each one looks like on a chart</h2>
${L.fig(L.candles({
  title: "Short sellers buying back: down all day, up in the last hour",
  candles: L.series(cover, 0.5),
  marks: [{ i: 9, text: "14:30", color: "#334155" }, { i: 13, text: "jump before 15:20", color: "#b45309" }],
  xLabels: [[0, "09:15"], [5, "12:00"], [9, "14:30"], [14, "15:30"]],
  height: 210,
}), "Falls until 14:30, then rises fast on heavy volume. After the close, few buy orders are left waiting: the buying already happened.")}
${L.fig(L.candles({
  title: "Real demand: up through the day, closes at the high",
  candles: L.series(demand, 0.5),
  marks: [{ i: 14, text: "closes at the high", color: "#047857" }],
  xLabels: [[0, "09:15"], [5, "12:00"], [9, "14:30"], [14, "15:30"]],
  height: 210,
}), "Rises steadily and closes at the top. After the close, buy orders waiting are several times the sell orders.")}

<h2>3. Buy orders waiting vs sell orders waiting</h2>
${L.fig(L.bars([
  { label: "Buy orders waiting", value: 90000, color: "#059669", text: "90,000 shares" },
  { label: "Sell orders waiting", value: 15000, color: "#dc2626", text: "15,000 shares" },
]), "Buyers waiting are 6× the sellers. The tab calls 3× or more \"unusual buy orders\".")}

<h2>4. The columns, in plain words</h2>
${L.table(["Column", "What it means", "Points to"], [
  ["Buy orders waiting", "Shares people still want to buy when you scanned (Groww's order book).", "More = more people who could not get it."],
  ["Sell orders waiting", "Shares people still want to sell. When Groww sends it, how many orders sit at the best 5 prices.", "Few sellers = little supply left."],
  ["Buyers vs sellers", "Buy orders waiting ÷ sell orders waiting. \"No sellers\" when nobody is selling.", "3× or more: <b>real demand</b>."],
  ["Waiting vs day volume", "Buy orders waiting as % of all shares traded today.", "5% or more: unusually big queue."],
  ["Day move / Last hour", "The day's change, and the move from 14:30 to the close (with the move before 14:30 under it).", "Down all day then up late: <b>short sellers</b>."],
  ["End volume", "Volume per minute after 14:45 compared with the day's usual pace.", "2× or more while the price rises: rush buying, often <b>short sellers</b>."],
  ["Delivery", "Share of today's volume people took home (NSE publishes it about 18:00), and its usual.", "High: <b>real demand</b>. Low: <b>short sellers</b> (intraday)."],
  ["F&amp;O OI", "For F&amp;O stocks, the change in open contracts today.", "Price up + OI down: <b>short sellers</b>. Price up + OI up: <b>new buyers</b>."],
  ["Sold short", "Shares sold short that day as % of volume (NSE's file, out the next day).", "Big: those shares must be bought back."],
  ["Answer", "<span class=\"pill p-green\">Real demand</span> <span class=\"pill p-amber\">Short sellers buying back</span> <span class=\"pill p-blue\">Both signs</span> <span class=\"pill p-grey\">Nothing unusual</span>", "Click the row to see every reason."],
])}

<h2>5. How to use it</h2>
${L.screen("After the close: real buyers, or short sellers buying back?", [
  `${L.chip("Show: Unusual buy orders", true)} ${L.chip("Stocks: All F&amp;O")} ${L.chip("Also: ALICON")} ${L.btn("Scan now", true)}`,
  `<b>ABC</b> <span class="pill p-green">Real demand</span> <span class="g">buy 92,400</span> <span class="r">sell 14,100</span> <b>6.6×</b> <span>last hour +1.7%</span> <span>delivery 61% (usual 38%)</span> ${L.btn("Arm")}`,
  `<b>XYZ</b> <span class="pill p-amber">Short sellers buying back</span> <span class="g">buy 3,200</span> <span class="r">sell 2,900</span> <b>1.1×</b> <span>before 14:30 −1.4%, last hour +2.1%</span> <span>OI −7%</span> ${L.btn("Arm")}`,
])}
${L.steps([
  "Open the Scalp page, group <b>4 Look back</b>, tab <b>After the close</b>.",
  "Pick the stocks: tick <b>All F&amp;O</b> or a Nifty list, and type any other stock in <b>Also these stocks</b> (for example ALICON).",
  "Press <b>Scan now</b> after <b>15:30</b>. For the surest answer, scan again after <b>18:00</b>, when NSE's delivery % is out. Each scan is saved, so you can open it later from <b>Saved scans</b>.",
  "Set <b>Show</b> to <i>Unusual buy orders</i> to see only the stocks with far more buyers than sellers waiting.",
  "Read the <b>Answer</b>. Click a row to see the reasons for each side in plain words.",
  "Real demand = a candidate to watch tomorrow (arm it on a practice bot if you like). Short sellers buying back = be careful chasing tomorrow's open; the jump often fades.",
])}

<h2>6. Two worked examples</h2>
${L.box("green", "Real demand", `After the close: 92,400 shares of buy orders waiting, 14,100 of sell orders (6.6×). It rose all day and closed at the top of its range. In the evening NSE shows delivery 61% against a usual 38%.<br><br>Read it as: \"people wanted it, many could not get it, and buyers took the shares home.\" The tab says <b>Real demand</b>.`)}
${L.box("amber", "Short sellers buying back", `It fell 1.4% until 14:30, then jumped 2.1% before the close, with volume after 14:45 three times the usual pace. After the close buyers and sellers waiting were about equal (1.1×). It is an F&amp;O stock and its open interest fell 7% while the price rose. Delivery was 22% against a usual 45%.<br><br>Read it as: \"the late buying was short sellers closing; nobody is still queuing to buy.\" The tab says <b>Short sellers buying back</b>.`)}

<h2>7. Good to know</h2>
${L.cols([
  ["Why it helps", `<ul><li>Spots stocks where buyers were left waiting after the close.</li><li>Separates a real-demand jump from a short-covering jump, which often fades.</li><li>Every scan is saved, so you can check the next day what happened.</li></ul>`],
  ["Limits", `<ul><li>It is an estimate: nobody publishes who bought.</li><li>Delivery % comes about 18:00 and the short-selling file the next day; until then the answer is less sure (the tab says what is missing).</li><li>Open interest exists only for F&amp;O stocks.</li><li>Scanned during the day, it shows the live order book, not the after-close one.</li><li>Information only: nothing is traded.</li></ul>`],
])}

<h2>8. Words used here</h2>
${L.words([
  ["Buy / sell orders waiting", "Orders placed but not filled yet, as shares.", "90,000 shares of buy orders waiting after the close."],
  ["Short seller", "Someone who sells shares first and buys them back later, hoping the price falls."],
  ["Buying back (short covering)", "The short seller buying the shares back. Intraday, it must happen before about 15:20."],
  ["Delivery %", "The share of the day's traded shares people took home instead of selling the same day."],
  ["Open interest (OI)", "Futures and options contracts still open. Falls when people close positions."],
])}
`,
    }),
};
