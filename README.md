# ORB Intraday Trading Bot

An intraday algorithmic trading app built for **forward paper trading**: real
NSE market data from Groww, but entirely virtual money. Orders are simulated
locally and **never** sent to your broker, so you can measure whether a
strategy actually makes money before risking any.

Design priority throughout is capital preservation: a server-side risk engine
gates every entry, brackets are enforced tick-by-tick, and a daily loss
circuit breaker squares everything off and locks the platform.

## The two independent switches

These are separate on purpose — this is the core safety model:

| Switch | Options | What it controls |
|---|---|---|
| **Data source** | `SIMULATED` / `LIVE NSE` | Where prices come from |
| **Execution** | `VIRTUAL MONEY` (locked) | Always simulated. Not user-changeable |

Connecting your Groww API key changes only the prices. It cannot cause a real
order: `place_paper_entry()` is the sole entry path and it fills against the
local paper engine. There is no code path from a strategy signal to
`GrowwClient.place_order()`.

## Stack

- **Backend:** FastAPI (async) + SQLAlchemy 2.0 (SQLite) + WebSockets, Python 3.13
- **Frontend:** Next.js 15 (App Router) + React 19 + Tailwind + TradingView `lightweight-charts`
- **Broker:** `growwapi` 1.5.0 — used for market data and auth only

## Setup

### Backend

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# paste that key into .env as ENCRYPTION_KEY
uvicorn app.main:app --reload --port 8000
```

Use Python 3.13, not 3.14 — `pydantic-core` has no 3.14 wheels yet and will
try to compile from source.

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Open http://localhost:3000. It starts on the simulated feed, so it works
immediately with no credentials.

## Connecting real market data

1. Create an API key in Groww (a **TOTP** key or an **approval** key both work).
2. Settings → enter API Key, and either the TOTP secret (TOTP key) or API Secret (approval key). Credentials are Fernet-encrypted before hitting the database.
3. Click **Test Connection** to confirm the TOTP secret generates a valid code.
4. Click **Connect Live Data** to run the login and get a daily access token.
5. On the dashboard, switch the data source pill to **LIVE NSE**.

Tokens are daily — repeat step 4 each morning.

If login or quotes fail with a method error, `GET /api/marketdata/probe`
lists what your installed SDK actually exposes. Only `app/brokers/groww_client.py`
talks to the SDK, so that's the single file to adjust.

## Troubleshooting live data

Run **Settings → Live Data Diagnostics** (or `GET /api/marketdata/diagnose`).
It probes each capability separately, because Groww permissions account
access and market data independently:

| Verdict | Meaning |
|---|---|
| `READY` | Market data works — switch the source to LIVE NSE |
| `NO_MARKET_DATA_ENTITLEMENT` | Login works and account endpoints succeed, but `get_ltp`/`get_quote`/`get_ohlc` return **403 Access forbidden**. Your Groww API subscription has no market-data plan. Enable the market-data / Live Data add-on in Groww's API portal, then reconnect |
| `MARKET_DATA_ERROR` | Not a permission problem — check the per-call errors |
| `SESSION_BROKEN` | Re-run login |
| `NO_SESSION` | Connect Live Data first |

**Logging in is not the same as switching the feed.** "Connect Live Data"
only establishes the broker session; the dashboard stays on simulated prices
until you click **LIVE NSE**. Whenever the source is simulated, a banner says
so — if you see it, the prices on screen are synthetic and will not match
NSE/BSE.

Restarting the backend no longer costs you the session: the stored access
token is decrypted and the Groww SDK rebuilt at startup, so the connection
survives until the token expires that night. Only an expired token needs the
login run again. The **data source** is still not persisted, though — the feed
always starts on SIMULATED, so flip the pill back to LIVE NSE after a restart.

Saved credentials are write-only by design: the API never returns a decrypted
key, so the Settings fields stay blank even when keys are stored. The
**KEYS SAVED** badge and the session line above the form are how you tell.

## Session rules on live data

Live quotes outside 09:15–15:30 IST are frozen at last close. Filling against
frozen prices would invent trades that could never have happened, so on live
data the app **blocks new entries outside market hours** and when the feed
goes stale (no price change for 90s during the session). Existing brackets
still track. The simulated feed has no such restriction, so you can exercise
the engine at any hour.

Weekday check only — NSE trading holidays are not encoded. On a holiday the
feed will look open but frozen, which the staleness check catches.

## What runs automatically

- **Bracket enforcement** — every tick, bot on or off. Stop-loss and target auto-exit; a Supertrend(10,3) trailing stop ratchets the SL in your favour and never loosens it. Open positions are rebuilt from the ledger at startup, so a restart no longer leaves unprotected risk on the book with an `OPEN` row nothing can close.
- **ORB strategy bot** — builds the opening range, filters by relative volume, then enters on a breakout close. Start/stop from the dashboard.
- **Risk gate** — 1% position sizing bounded by a leverage cap, 2% daily-loss circuit breaker, daily trade cap, bid-ask spread guard, minimum-edge filter. The bot and the manual order form go through the identical path; the bot has no privileged route.

### Why sizing has two limits, not one

The 1% rule alone answers "how many shares before I lose my budget at the
stop". That formula contains no price term — it has no idea what the position
costs. A 27-paisa stop on a ₹1 lakh account asks for 3,703 shares of a ₹731
stock: **₹27 lakh of exposure, 27× leverage**, an order no broker would accept.
It happened here, and the charges on that turnover turned a ₹703 price move
into a ₹3,591 loss.

So size is `min(risk-based, leverage-based)`, where the leverage ceiling is
`account_capital × max_leverage` (default 5×, roughly real MIS equity
leverage). When the leverage limb binds, the console says so — it means the
stop was unusually tight for the price and the trade carries *less* than the
configured risk.

Two related rules ride along:

- **Sizing uses the expected fill, not the signal price.** They differ by the
  slippage haircut, and on a tight stop that gap is a large fraction of the
  risk being sized on. A trade sized for ₹1,000 of risk actually carried
  ₹2,370 before this was fixed.
- **A target that cannot clear its own costs is rejected.** Slippage and
  charges are both paid twice, so a target nearer than that hurdle loses money
  even when hit exactly — an arithmetic certainty, not a risk to manage. The
  target must beat round-trip costs by `min_edge_multiple` (default 1.5×).

Both limits are editable in Settings → Risk Management Engine.
- **15:30 IST hard cut-off** — squares off everything and stops the bot at the NSE close (live data only; skipped on the synthetic feed so the sandbox stays testable after hours). Because the cut-off now sits on the closing bell rather than 15 minutes before it, the exit fills against the last tick of the session — thin, volatile prices. Move it earlier in `RiskConfig.square_off_time_ist` if you would rather leave a margin.
- **AI entry gate** — off by default. When on, the expert can veto a breakout entry; it can never originate one.

## Light and dark themes

The toggle sits in the nav (sun/moon). Preference is stored in `localStorage`;
with nothing stored the app follows the OS `prefers-color-scheme`.

The app was originally dark-only — six custom colours plus ~390 hardcoded
`slate-*` utilities across every component. Rewriting all of those into
semantic tokens would have touched nearly every file for a purely visual
change, so instead:

- the six custom colours became CSS variables (channel triplets, so
  `bg-bot/20` and friends keep working), swapped by a `data-theme` attribute
- the `slate-*` scale is **inverted** under `[data-theme="light"]` in
  `globals.css` — `text-slate-200` is near-white primary text in dark mode, so
  in light mode it must become near-black or the most important text on the
  page becomes the faintest
- green and red are darkened in light mode; the dark-mode values are tuned for
  a near-black background and fail contrast on white
- hover washes written as white tints become dark tints of the same weight,
  otherwise they are invisible on a light surface

Two things that are easy to get wrong and are handled:

**No flash on load.** An inline, synchronous script in `layout.tsx` sets the
attribute before first paint. A deferred script or a React effect runs after
the first render, so light-mode users would see a dark flash on every page
load.

**The chart is told separately.** `lightweight-charts` paints its own canvas,
which CSS variables cannot reach, so axis/grid/label colours are passed in as
props and the chart is rebuilt when the theme changes.

## Charts

**Charts** in the nav opens `/chart`; the dashboard uses the same component.

| | |
|---|---|
| Timeframes | 1m, 5m, 15m, 1h, 1d |
| Overlays | EMA 9, EMA 21, VWAP, Bollinger Bands, Supertrend |
| Oscillators | RSI (with 30/70 bands), MACD (with histogram), ADX (with +DI/-DI and the bot's own 20 threshold drawn) |
| Also | volume histogram, crosshair OHLC legend, stop-loss and target lines, real trade entry/exit arrows |

Three things are deliberate:

**Indicators are computed server-side**, by the same `app/services/indicators.py`
the strategy engine uses. A chart drawing its own JavaScript ADX would
eventually disagree with the Python ADX the entry gate actually rejected on,
and then the chart is quietly misrepresenting why a trade did or did not
happen. One implementation, one answer.

**Warm-up periods render as gaps, not zeros.** An indicator that needs 29
candles has no value before then, and drawing a flat line at zero would invent
history. Series arrive aligned to the candles with `null` in the warm-up, and
the chart simply starts the line later.

**Candle history is keyed by data source and never mixed.** Backfilling real
NSE history into a symbol that had been ticking on the simulated feed once
produced a single candle with a synthetic open of 2784 and a real low of 1317
— a 2x range that never happened. Simulated and live prices are series about
different worlds. The store keeps them apart, and the tick loop labels each
tick with the feed that actually produced it rather than whichever source was
selected at that instant.

History comes from Groww's historical candle API when the feed is LIVE. On the
simulated feed there is genuinely no past before the app started, and the
chart says so in a banner instead of drawing something invented.

## Automated Scanner & Alert Engine

**Scanner** in the nav opens `/scanner`. It watches a universe for moving-average
crossovers and sends a WhatsApp alert when one fires.

**It observes; it never trades.** There is no code path from a scanner signal to
`place_paper_entry()`, and it has its own config table rather than sharing the
ORB bot's — sharing one would imply a coupling that must not exist.

### Architecture

| Class | File | Responsibility |
|---|---|---|
| `StrategyEngine` | `services/scanner_engine.py` | Crossover detection + trend/volume filters. Stateless |
| `AlertNotifier` | `services/alert_notifier.py` | WhatsApp delivery, retries, dedup/cooldown |
| `ScannerWorker` | `services/scanner_worker.py` | Background loop, candle-close gating, persistence |
| API | `api/routes_scanner_engine.py` | Mounted at `/api/scan` |
| UI | `app/scanner/page.tsx` | Config, controls, live signal log |

### Signals fire on candle close, once

Enforced structurally, not by convention. The candle store's last bar is always
the one still forming, so the worker slices it off and evaluates only completed
bars; it also records the last bar timestamp it judged per symbol/timeframe.
The loop wakes every 5 seconds, but a given bar is judged exactly once.

Without this a fast MA computed from a forming bar wobbles across the slow MA
repeatedly inside a single candle, firing a burst of contradictory alerts.

### Indicators come from `indicators.py`, not pandas-ta

The original spec called for `pandas_ta`. It is deliberately not used: the
chart, the ORB entry gate and this scanner all read the same EMA. A second
library would give the scanner its own subtly different moving average
(different seeding, different warm-up), and an alert would then disagree with
the chart the user checks it against. If you want pandas-ta, swap
`_moving_average()` in `scanner_engine.py` — it is the only call site.

### Candlestick patterns

`services/patterns.py` detects eleven patterns: hammer, hanging man, inverted
hammer, shooting star, doji (plus dragonfly and gravestone), bullish/bearish
engulfing, and bullish/bearish marubozu.

**Every pattern is geometry *plus context*, never geometry alone.** A hammer
and a hanging man are the *same shape* — the only difference is whether the
preceding bars were falling or rising. A detector that ignores trend cannot
tell them apart, so it would be reporting a coin flip with a confident name
attached. Verified against identical input:

| Same candle, different context | Result |
|---|---|
| after a decline | `HAMMER` (bullish) |
| after an advance | `HANGING_MAN` (bearish) |
| in a flat range | *nothing* — refuses to guess |

Sizes are measured against the candle's own range and recent average range,
never absolute rupees: a ₹2 body is enormous on a ₹50 stock and invisible on a
₹3,000 one.

**A doji never confirms a direction.** It is reported as `INDECISION` — it says
neither side kept control, which is not agreement with a directional entry.

Two places they appear:

- **Chart** — the `Patterns` toggle marks them on closed bars. The forming bar
  is excluded; its shape is still changing, so labelling it would show a
  pattern that vanishes on the next tick.
- **Scanner** — an optional confirmation filter. A crossover must coincide with
  a supporting pattern within `pattern_lookback` bars (default 3).

**ADX trend filter.** A moving-average crossover is a *trend-following*
signal. In a flat range it does the opposite of what you want: it sells the dip
and buys the bounce. That is whipsaw, and it is the single most common way this
strategy loses money.

A real example from this app: ULTRACEMCO fired SELL at ₹11,717 and BUY at
₹11,750 twenty-five minutes later — sell low, buy high. The stock had chopped
between 11,675 and 11,767, a 0.79% range, and **ADX sat at 9.9–12.1 the whole
time**. Below 20 there is no trend to follow, and both signals were noise.

ADX measures trend strength regardless of direction, so it rejects exactly this
case. On real NSE data it removed **45%** of crossovers on its own. The ORB bot
has had this filter since earlier; the scanner did not, which is how those two
signals got through.

**Price band.** `min_price` / `max_price` skip symbols outside a rupee range
before any history is fetched. A ₹1 lakh account cannot meaningfully size a
position in a ₹13,000 stock, so scanning it only produces alerts that cannot be
acted on. Either side may be left at 0 for no limit, and the status line
reports how many symbols were skipped.

**The log draws the actual candle**, not a per-pattern icon. Each signal
stores its bar's OHLC and the log renders a small SVG to true proportion —
wick from high to low, body from open to close, green or red by direction — so
a marginal hammer looks marginal instead of textbook. Proportions are
normalised to the bar's own range, since an absolute rupee scale would make a
₹150 stock's candle invisible beside a ₹13,000 one. The expanded panel shows a
larger one with the raw OHLC beside it.

**The signal log leads with the CONFIRMING pattern**, not the signal bar's own
shape. With a lookback window the confirming candle often sits a bar or two
earlier, so showing the signal bar first made a properly confirmed signal read
as "no named pattern".

**Every signal records what its candle looked like**, named pattern or not.
Most bars are not a hammer — they are "red candle, small body with a long upper
wick — price was pushed up and then rejected". Reporting those as a blank dash
reads like missing data when the bar did have a describable shape.

On real NSE 15-minute data across five symbols, the filter kept **20%** of
crossovers at a 3-bar window and only **7%** when the pattern was required on
the signal bar exactly. Same-bar-only is technically correct and practically
useless, which is why the window exists.

### Data ingestion

Candles come from the existing `CandleStore`, fed by the same tick loop
everything else uses, with history backfilled from Groww via
`services/backfill.py`. The spec asked for a dedicated WebSocket feed; the app
currently polls, and `generate_socket_token` has never been exercised here.
Building the scanner on an untested parallel ingestion path would have risked
both. The WebSocket upgrade is a separate, worthwhile task — it is what would
lift the universe from ~20 symbols to 150+.

### Alerts

| Provider | Needs | Notes |
|---|---|---|
| CallMeBot | phone + API key | WhatsApp `+34 623 75 84 18` with exactly "I allow callmebot to send me messages" to get a key. No account. If it does not reply in 2 min the bot is likely down — CallMeBot publishes a [backup bot](https://www.callmebot.com/?ae_global_templates=setup-whatsapp-for-dead-bot) at `+34 623 78 64 49`. Numbers have changed before, so verify against [the setup page](https://www.callmebot.com/blog/free-api-whatsapp-messages/) |
| Twilio | `account_sid:auth_token`, from + to numbers | Sandbox or approved sender |

Credentials are Fernet-encrypted at rest and never returned by the API. Only
one channel is enabled at a time, so a signal cannot fan out unexpectedly.

Message format:

```
🚨 [BUY SIGNAL] - RELIANCE
Timeframe: 5m | Trigger Price: ₹1,317.45
Signal: EMA9 crossed above EMA21
Time: 2026-08-26 10:30:00 IST
• volume 2.10x the 20-bar average
```

**Dedup:** a cooldown per (symbol, timeframe, side) plus an optional
once-per-session cap. That state is in memory and resets on restart — the
honest reading of "per session"; persisting it would silently turn
`once_per_session` into "once ever".

**Nothing disappears silently.** A signal with no channel configured, or one
suppressed by cooldown, is still written to the log with status `SKIPPED` and
the reason. Failed sends are `FAILED` with the provider error attached.

### Error handling

- **Network/timeouts** — 3 attempts with exponential backoff. A 4xx is *not*
  retried: a bad API key stays bad, and retrying risks a rate-limit ban on top.
- **Rate limits (429) and 5xx** — retried with backoff.
- **Feed disconnects** — the tick loop already supervises and reconnects; the
  scanner reads from the store and simply finds no new closed bar meanwhile.
- **Frozen feed** — outside market hours live quotes sit at last close, so
  candles have zero range and the two averages drift together until they cross
  by a few paise. That manufactures signals out of a dead feed, so scanning
  pauses on live data when the market is closed or the feed is stale. The
  simulated sandbox keeps scanning, since that is what it is for.
- **Per-symbol isolation** — one malformed series cannot stop the rest of the
  universe from being scanned.
- **Backfill failures** — retried at most once every 5 minutes per symbol, so a
  persistently failing symbol cannot turn the loop into a retry storm.

## Manual Trading Desk

**Trade** in the nav opens `/trade`: a broker-style order ticket over a second,
completely independent virtual wallet.

Two accounts, never mixed:

| | Strategy / Bot (`AUTO`) | Manual Desk (`MANUAL`) |
|---|---|---|
| Wallet | own capital, balance, P&L | own capital, balance, P&L |
| Position size | 1% rule, leverage-capped | **you type the quantity** |
| Daily loss circuit breaker | yes | no — you are the risk manager |
| Reports | account switcher on `/reports` | same, filtered to the desk |

Separate `PaperEngine` instances back the two, so a position in one can never
be closed or double-counted by the other, and each trade row carries the
`account` it belongs to.

The one thing the desk does *not* relax is **affordability**. Quantity is
checked against free margin (`balance × max_leverage`), because a position the
account cannot carry is not a risk preference — it is an order a real broker
would reject. The ticket previews expected fill, order value, charges for the
leg and round-trip charges before you commit, so the cost the move has to beat
is visible up front rather than discovered in the P&L.

Stop-loss and target are optional here. When set, they are enforced on every
tick by the same loop that protects the strategy account — a stop typed into a
form is only a number in a table unless something checks it. The 15:30 IST
cut-off squares off both wallets.

## Trade alerts

The **ALERTS** toggle in the nav turns on a notification for every entry and
exit, on both accounts, plus the circuit breaker and kill switch.

Each alert carries **why**, not just what. A notification is read in isolation,
seconds after it fires and usually without the app in front of you, so
"SELL 3703 HDFCBANK" on its own is useless. A bot entry names the breakout:

> ORB breakout: the 5-min candle closed ₹731.13 — below the 15-min opening
> range [731.55, 732.00], clearing ₹731.55. RVOL 2.93 vs the 2.0 threshold.
> AI expert BEARISH with conviction 72 (sentiment NEGATIVE).

An exit spells out the arithmetic, because a "TARGET HIT" sitting next to a
negative P&L is the most confusing thing this app can show:

> BUY 5 · ₹593.24 → ₹592.44 (-0.80/share) · net loss -₹7.17
> MANUAL CLOSE. Gross -4.00 less 3.17 charges.

When costs are what turned a winning move into a loss, the alert says so
outright rather than leaving you to work it out.

Alerts arrive two ways. Desktop notifications need browser permission; if that
is denied the toggle still works and falls back to **in-app toasts**, because a
blocked permission silently swallowing every alert is worse than a toast. The
toggle shows amber in that state. The preference is stored in `localStorage`,
and notifications are tagged per symbol so a burst on one stock replaces itself
instead of stacking.

Note this is an in-page mechanism: it needs a tab open. Alerts to a phone with
the app closed would need Web Push with a service worker and VAPID keys, which
is not implemented.

## Transaction history and reports

**Reports** in the nav (or **Detailed Report** on the dashboard's Trade History
card) opens the full ledger at `/reports`.

Filter by date range, symbol, side, manual-vs-bot, open/closed and outcome;
everything below the filter bar recomputes, and **Export CSV** exports exactly
the rows you are looking at.

Three tabs:

| Tab | What it holds |
|---|---|
| Overview | Headline stats, the equity curve with its running-peak drawdown envelope, and P&L by day and by symbol |
| Breakdowns | P&L by side, source, exit reason, strategy, weekday and entry hour |
| Transactions | Every trade; click a row for its full forensics |

Per trade you get planned risk and reward, realised R-multiple, gross vs net
P&L, both charge legs and what percentage of gross they ate, holding time,
exit reason, and any AI view recorded against it.

Two things worth knowing about the numbers:

- All buckets are **IST**. Timestamps are stored in UTC, so a 19:00 IST close
  falls on the previous UTC day — bucketing without converting would misstate
  daily P&L for exactly the evening hours you review in.
- Trades closed before this release have **no per-leg charge record**. Their
  net P&L is still after costs; only the breakdown is missing. Those rows are
  marked `charges_recorded: false` and left out of charge totals rather than
  being counted as zero-cost.

### Bot timing modes

| Mode | Candle | Opening range | Use |
|---|---|---|---|
| `MARKET TIMING` | 5 min | 09:15–09:30 (15 min) | Real sessions |
| `DEMO TIMING` | 15 s | 60 s from bot start | Testing the machinery out of hours |

Demo timing is a test harness, not a strategy — don't read its P&L as signal.

**Late starts.** `MARKET TIMING` anchors its range to the 09:15 open, so a bot
started after 09:30 was never running for the window it is supposed to measure.
Rather than sitting `ARMED` with an empty range and silently taking nothing all
day, it now measures a 15-minute range from the moment you start, on the same
real 5-minute candles, and hunts breakouts from there. The bot card and the
console both label this a **late start**: it is a mid-session range, not an
opening range, and the classic ORB edge is not what you are testing. Start
before 09:15 for that.

If a range window ever does close with no usable candles, the status is
`NO_RANGE`, not `ARMED` — the bot cannot detect a breakout without a range, and
saying otherwise hides a bot that will never trade.

## The AI trading expert

Settings → **AI Trading Expert (OpenAI)**. Paste an OpenAI API key, pick a
model, **Test Connection**. The key is Fernet-encrypted at rest in the same
vault as the broker credentials and is never returned by any endpoint after
you save it.

It is deliberately **not** a second chartist. The prompt forbids price-action
analysis outright — no patterns, levels, moving averages or indicators —
because the ORB engine already owns price structure, and running it twice
would launder the same signal through a non-deterministic layer. What the
expert is asked for is what the engine is blind to:

news flow and headline sentiment · earnings, guidance and management
commentary · analyst upgrades/downgrades and target revisions · block deals,
promoter pledging, insider activity, filings and rating moves · sector and
peer read-across · macro, policy and regulatory backdrop · FII/DII flows and
positioning · retail and social sentiment · event risk in the next 24 hours

With **web search** on it retrieves and cites real current articles; with it
off it answers from training data only, which will be stale — the panel says
which happened for every view.

### The entry gate

Off by default. Switched on, the ORB bot consults the expert before each
breakout and skips the trade unless the view clears your conviction floor and
(optionally) agrees with the signal direction.

The gate can only **veto**. There is no path from a model response to an
order: `place_paper_entry` is still the only entry path, and the risk engine
still gates whatever the expert passes.

Two consequences to expect:

- **Fewer entries.** A breakout with no fresh view yet is skipped and
  re-checked on the next candle. The gate never blocks waiting for the API —
  web search takes tens of seconds and the tick loop also carries bracket
  enforcement, so stalling it would leave open positions unprotected.
- **A real API bill.** One web-search call per symbol per freshness window.
  Views are cached for `cache_ttl_sec` (default 900s) and the shortlist is
  pre-fetched when the opening range locks, so the cost scales with symbols
  and session length, not with candles.

Every view is persisted, including the ones the gate acted on, with its
stance, conviction, sources and the gate's verdict — visible per trade in the
Transactions tab. A skipped entry that you cannot explain later is worse than
no gate at all.

Deleting the key also switches the gate off; leaving it armed with no key
would silently block every bot entry.

## Ask the bot

The **Ask the bot** button sits bottom-right on every page. It answers
questions about the bot's own behaviour — *why did you buy that stock*, *why
did you sell it*, *why didn't that trade make a profit*, *why is trading
locked* — using the same OpenAI key as the expert.

It is grounded, not conversational-from-memory. Every answer is drawn from a
factual snapshot assembled in `app/services/chat_advisor.py`: the actual trade
rows with their derived costs and R-multiples, the opening ranges the bot
measured, the risk config and its lock reason, open positions, and the bot's
own console log lines. The prompt forbids inventing anything outside that
snapshot and requires it to say what it does not have recorded.

That constraint is the whole point. An assistant that answers "why did you
sell HDFCBANK?" from the model's imagination produces confident, plausible
reasons for trades that happened for entirely different ones, and you have no
way to tell the two apart. `GET /api/chat/context` returns the exact snapshot
the model was given, so any answer can be checked against its source.

The snapshot also carries the engine's mechanics — slippage rate, the charge
formula, the position-sizing rule — because most "why didn't I make money"
questions are answered by arithmetic the dashboard doesn't show: a target
narrower than the slippage charged on exit, or a position whose costs dwarf
its edge.

Console log lines are now persisted to the `strategy_logs` table. They used to
be ephemeral, which made any decision unexplainable once the page refreshed.

## Current limitations — read before trusting a number

- **RVOL on the simulated feed is cross-sectional** (a symbol's range volume vs the universe median), not a true 20-day RVOL, because the synthetic feed has no history. `GrowwClient.get_daily_volumes()` fetches real 20-day volumes and is ready to wire into the scanner for live sessions; the scanner does not call it yet.
- **Depth is polled, not streamed.** Bid/ask refreshes every ~10 poll cycles, so the spread guard can act on slightly stale depth. LTP refreshes every poll (2s default).
- **Live order dispatch is disabled by design** and returns 501. Enabling it is a deliberate code change in `app/services/execution.py`, not a toggle.
- **Zerodha / Angel One** store credentials but have no adapter — Groww only.
- **Telegram alerts** are not implemented; alert lines only appear in the in-app console.
- **The AI expert is an opinion, not a datafeed.** It reports what its search returns; coverage of mid- and small-caps is thinner than large-caps, and a confident-sounding view on a quiet name is exactly the failure mode the prompt tries to suppress but cannot eliminate. Read the cited sources before weighting a view. Its conviction number is not a probability.
- **The gate has not been measured.** Whether it improves results is an empirical question this app can now answer — filter Reports by source and compare gated bot trades against ungated ones — but nothing here has established it does.
- **`npm audit`** flags transitive `sharp`/`postcss` advisories via Next.js image tooling. This app never calls `next/image`, so exposure is low, but resolve them before any public deployment.

## Evaluating a strategy honestly

The paper engine charges 0.05% slippage against you on both legs plus
approximate NSE intraday costs (brokerage, STT, exchange, GST, SEBI, stamp).
A strategy that is only profitable before costs is not profitable.

Run it on live data across several weeks of real sessions before drawing any
conclusion. A handful of trades tells you nothing — the daily trade cap of 5
means a statistically meaningful sample takes months, not days.

## Project structure

```
trading-app/
├── backend/app/
│   ├── api/          # auth, orders, bot, marketdata, scanner, reports, ai,
│   │                 # websocket routes
│   ├── core/         # config, encryption, risk_manager, market_clock
│   ├── brokers/      # BrokerClient interface + GrowwClient
│   ├── strategies/   # ORB strategy + scanner
│   ├── services/     # market_data, tick_feed, candle_builder, strategy_runner,
│   │                 # execution, paper_engine, trade_ledger, reports,
│   │                 # ai_advisor, safety, broadcaster
│   └── models/       # SQLAlchemy models
└── frontend/src/
    ├── app/          # dashboard + reports + settings pages
    ├── components/   # Dashboard/*, Reports/*, Settings/*, Navbar
    ├── hooks/        # useWebSocket, useTradingState
    └── lib/          # api.ts, format.ts
```

The schema migrates itself forward on startup: missing tables are created and
missing columns are added with `ALTER TABLE`, so an existing `trading.db`
keeps its rows across an upgrade.

## Security notes

- Credentials are Fernet-encrypted at rest — broker keys and the OpenAI key alike. The Fernet key lives in `backend/.env` (gitignored) — back it up; losing it makes stored credentials unrecoverable.
- The OpenAI key buys analysis only. It is never sent anywhere but OpenAI, and no code path connects a model response to an order.
- `backend/.env` and `backend/trading.db` are gitignored. Never commit them.
- Treat your Groww API key as a live credential even though this app only reads with it.

- **The daily risk lock does not survive a restart.** It lives in memory; the `daily_risk_state` table exists but nothing writes to it. Restarting the backend clears a loss-limit lock and the kill switch.

## Course coach — your own curriculum, cited

A fourth explanation layer, sitting alongside the three that already exist.
The distinction matters, because none of them overlap:

| Layer | Owns | Source of truth |
|---|---|---|
| `indicators.py` / `patterns.py` | price structure and candle geometry | deterministic math |
| `explain.py` | translating those numbers into plain English | computed values |
| `ai_advisor.py` | news, earnings, analyst actions, macro | OpenAI + web search |
| **`course_coach.py`** | **what your uploaded course teaches** | **your PDFs, cited by page** |

`patterns.py` already says a hammer is *"a small body with a long lower wick
after a decline"*. True, and generic. What it cannot say is what **your**
curriculum teaches about trading it — where the stop belongs and why, the
risk-taker versus risk-averse entry, what invalidates the setup. That is the
gap this fills, with citations back to the page.

### Two load-bearing rules

1. **The RAG never re-detects.** `patterns.py` owns detection. The `PatternHit`
   is sent to the knowledge base as ground truth via its `pattern` field, and
   its own detector is skipped. Two detectors over the same bars would
   eventually disagree, and this engine's answer is the one the charts,
   scanner and backtests are built on.
2. **Advisory only.** Unlike `ai_advisor`, this layer does not even have a
   veto. It cannot open, size, block or influence a position — it talks to the
   user, not to the order path. Every call degrades to `None` on failure, so an
   unreachable knowledge base changes nothing about trading.

### Setup

Run the RAG service (the `Tradingapp-RAG` project), upload your course PDFs,
then set in `backend/.env`:

```bash
COURSE_RAG_ENABLED=true
COURSE_RAG_URL=http://localhost:3001
COURSE_RAG_API_KEY=<must match RAG_API_KEYS in the RAG service>
```

Endpoints: `GET /api/course/status`, `GET /api/course/explain?symbol=&interval=`,
`POST /api/course/ask`, `GET /api/course/search?q=` (retrieval only — no LLM
call, so it costs nothing).

The UI lives on the chart page: a verdict line (entry / wait / no setup / manage
position), the risk, the confidence, and what is still missing — with the full
reasoning collapsed behind a link, because a wall of text at the moment a candle
closes is worse than nothing.
