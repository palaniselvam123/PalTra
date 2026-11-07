const L = require("../lib");

module.exports = {
  id: "scalp-monitor",
  file: "scalp-monitor-guide.pdf",
  title: "Monitor: scalp-ready stocks by a score",
  html: () =>
    L.page({
      title: "Monitor: scalp-ready stocks by a score",
      lead: "The simplest screen on the page: every streaming stock scored 0-100 on how good it is for a quick in-and-out trade right now. What the score is made of, what the filters do, and how to use it with the other tabs.",
      body: `
${L.box("blue", "What a \"scalp\" is", `A scalp is a very short trade: in and out within minutes for a few rupees a share. It only works on a stock that <b>moves enough per minute</b>, is <b>cheap to trade</b> (the gap between buyers' and sellers' prices is tiny) and has <b>plenty of money flowing</b> so your order fills at once. The Monitor checks exactly those three things.`)}

<h2>1. What you see</h2>
${L.screen("Monitor · LIVE NSE · 38 of 180 streaming stocks scalp-ready · Last scan 10:42:10", [
  `${L.chip("Min ATR %/min 0.03")} ${L.chip("Max spread 0.10%")} ${L.chip("Min traded today ₹20 cr")} ${L.chip("Min score 60")} ${L.chip("Scalp-ready only", true)} ${L.btn("Refresh")}`,
  `<span>☐</span><b>SBIN</b> <span class="m">₹812.40</span> <span class="g">+0.9%</span> <span>score <b>82</b></span> <span>ATR 0.06%/min</span> <span>spread 0.02%</span> <span>₹142 cr</span> <span class="g">bias LONG</span> ${L.btn("Arm")}`,
  `<span>☐</span><b>TATAMOTORS</b> <span class="m">₹921.10</span> <span class="r">−1.2%</span> <span>score <b>74</b></span> <span>ATR 0.05%/min</span> <span>spread 0.03%</span> <span>₹98 cr</span> <span class="r">bias SHORT</span> ${L.btn("Arm")}`,
])}
<p class="m">A mock of the table. The real one updates every 10 seconds while scans are on, and every stock's name opens its Groww page.</p>

<h2>2. The columns, in plain words</h2>
${L.table(["Column", "What it means", "What is good"], [
  ["Score", "0-100: how scalp-ready the stock is right now, adding up the checks below.", "Higher is better; 60+ is worth a look, 80+ is lively and cheap to trade."],
  ["ATR %/min", "How much the price typically moves in one minute, as % of its price.", "At least 0.03%: on a ₹1,000 stock that is ₹0.30 of movement a minute. Below that, the charges eat the scalp."],
  ["Spread", "The gap between the best buy price and the best sell price, in %. You pay it twice (in and out).", "Under 0.10%. A 0.05% spread on ₹1,000 costs ₹0.50 a share on the way in and again on the way out."],
  ["Traded today (₹ cr)", "Money that has flowed through the stock today, in crores.", "₹20 cr or more means your own order will not move the price."],
  ["Bias", "Which way today's readings lean: LONG (up, buy) or SHORT (down, sell first).", "A clear bias is easier to trade than NONE."],
  ["LTP", "The last traded price (the live price).", "Use the LTP range box to keep stocks in a price band you are comfortable with."],
])}

<h2>3. How to use it, step by step</h2>
${L.steps([
  "Make sure the top bar says <b>LIVE NSE</b>. On the simulated feed the prices are invented and the scores mean nothing.",
  "Keep <b>Scalp-ready only</b> ticked. The filters above the table are the three checks; the defaults are sensible for a beginner.",
  "Sort by <b>Score</b> (click the header). The top rows are the liveliest, cheapest-to-trade stocks right now.",
  "Tick two or three and press <b>Arm ticked</b>, or <b>Arm</b> on one row. Pick a practice bot and keep the suggested stop and trail. The bot waits for its own signal; nothing is bought now.",
  "Come back every 15-30 minutes. A stock that was scalp-ready at 10:00 can go quiet by 11:00; the score drops and it leaves the list.",
])}

<h2>4. A worked example</h2>
${L.box("", "", `SBIN at 10:42: price ₹812.40, ATR 0.06%/min, spread 0.02%, ₹142 cr traded, score 82, bias LONG.
<ul>
<li>0.06% of ₹812 is about <b>₹0.49 of movement a minute</b>: in 10 minutes it typically wanders ₹5. A ₹3 scalp is realistic.</li>
<li>The spread costs 0.02% × 2 = 0.04% round trip, about <b>₹0.32 a share</b>. Brokerage and taxes add roughly another ₹0.40. A ₹3 move leaves most of it.</li>
<li>₹142 cr traded: an order of 100 shares (₹81,000) is a drop in the ocean; it fills at once.</li>
<li>Bias LONG: today's readings lean up, so the bot's next buy signal is the one to expect.</li>
</ul>`)}
${L.box("red", "Compare: a stock that looks busy but is not scalp-ready", `A ₹60 stock with ATR 0.02%/min moves about ₹0.012 a minute. Even a good 10-minute move is ₹0.12 a share, less than the charges. The Monitor gives it a low score however many shares trade.`)}

<h2>5. Benefits, and what it cannot do</h2>
${L.cols([
  ["Why it helps", `<ul><li>One number sums up three things you would otherwise check by hand.</li><li>Filters stop you from arming a stock that cannot pay its own charges.</li><li>The bias tells you which side to expect.</li></ul>`],
  ["What it does not know", `<ul><li>It does not know whether the stock is <b>trending or choppy</b>: a choppy stock can score 80. Check <b>Trend quality</b> before arming.</li><li>It does not place orders. The bot does, on its own signal, only on armed stocks.</li></ul>`],
])}

<h2>6. Words used here</h2>
${L.words([
  ["Scalp", "A quick trade for a few rupees a share, closed within minutes."],
  ["ATR % per minute", "The stock's typical movement per minute as a percentage of its price.", "0.05% on ₹1,000 ≈ ₹0.50 a minute."],
  ["Spread", "Best sell price minus best buy price. Paid on the way in and the way out.", "Bid ₹500.00 / ask ₹500.20 = 0.04%."],
  ["Money traded (₹ cr)", "Shares × price, today, in crores."],
  ["Bias", "The side today's readings favour: LONG (buy) or SHORT (sell first)."],
  ["Arm", "Put a stock on a bot's Trade list. The bot trades it only on its own signal."],
])}
`,
    }),
};
