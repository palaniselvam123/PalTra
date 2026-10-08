# CLAUDE.md

PalTra is an NSE intraday trading app built around paper trading against real
Groww market data. Two trading engines live in this repo and share one frontend:

1. **ORB Desk** – the main app (`backend/app/`). Opening-range-breakout bot,
   scanner, movers, reports, and a manual trade desk. Virtual money by default.
2. **SMA Terminal** – a second engine (top-level `backend/*.py`). SMA(9, 21)
   crossover with a 1.5× ATR stop on 1-minute candles. See `SMA_TERMINAL.md`.

## Rules (read before changing anything)

- **Never place real orders while testing.** Keep the SMA terminal in `PAPER`,
  keep the manual desk on `paper` execution, and use the simulated feed or a
  stub broker in tests. Never send `confirm_live=true` to `/api/mode` or
  `/api/manual/execution`. Never call `GrowwClient.place_order`,
  `place_entry`, `place_exit`, `place_sl`, or `modify_sl` against a real session.
- **Keep API keys out of code.** Groww API keys, secrets, TOTP seeds, access
  tokens, `ENCRYPTION_KEY`, and Telegram tokens come from `backend/.env`
  (gitignored), the encrypted DB row, or `fly secrets`. Never hard-code them,
  commit them, log them, or put them in the Docker image. `.env.example` holds
  placeholders only.
- **Every change must keep PAPER mode working.** PAPER is the boot default and
  must stay the default everywhere (`DEFAULT_MODE=paper`, `trading_mode="PAPER"`,
  `state.manual_live = False`). The app must still start and trade virtually
  with no Groww credentials on the simulated feed. Run the backend tests and
  open the app on the simulated feed before you call a change done.

## Merging rules

- **UI-only or docs-only changes** (only files under `frontend/`, or `.md`
  files): once CI is green (backend tests and the frontend typecheck plus
  static export), merge to `main` yourself without asking.
- **Anything touching order placement, the strategy engine, risk limits,
  stop-loss, or LIVE mode**: open a PR and wait for the owner's OK before
  merging. This includes a `frontend/` change that alters a LIVE or order
  safeguard, such as the LIVE confirmation, CONFIRM typing, or the
  kill-switch dialog.
- When a change fits both, the stricter rule wins. Changes that fit neither
  (CI, tests, other backend code) also wait for the owner's OK.
- **Never stack PRs.** Every PR targets `main` directly. Merge with a merge
  commit, and delete the branch after it is merged.
- A merge to `main` deploys (outside 09:00–15:45 IST, Mon–Fri), so only merge
  what is ready to run in production.

## Tech stack

- **Backend:** Python 3.13, FastAPI (async), SQLAlchemy 2.0 on SQLite
  (aiosqlite), WebSockets, pydantic-settings, pandas/numpy. Use 3.13, not
  3.14 (`pydantic-core` has no 3.14 wheels).
- **Broker SDK:** `growwapi` (pinned in `backend/requirements.txt`).
- **Frontend:** Next.js 15 (App Router), React 19, TypeScript, Tailwind,
  TradingView `lightweight-charts`, framer-motion, lucide-react.
- **Deploy:** Docker image on Fly.io. The FastAPI process also serves the
  statically exported frontend.

## Folder structure

```
backend/
  app/                    ORB Desk (FastAPI app: app.main:app)
    main.py               App factory, lifespan, router mounting, static site at /
    state.py              Process-wide state (engines, quotes, manual_live flag)
    sma_host.py           Mounts the SMA terminal at /sma in production
    api/                  REST + WebSocket routes (routes_*.py)
    brokers/              base.py (OrderRequest/broker interface), groww_client.py
    core/                 config, encryption (Fernet), risk_manager, market_clock, desk_lock
    services/             paper_engine, execution, manual_desk, market_data, tick_feed,
                          strategy_runner, scanner, backtester, reports, notifications ...
    strategies/           orb_strategy.py, gainer_momentum.py, scanner.py
    models/database.py    SQLAlchemy models + session
    research/             Offline research / hypothesis-testing tools
  main.py                 SMA Terminal FastAPI app (REST + /ws/stream)
  strategy_engine.py      SMA/ATR bot logic
  groww_client.py         SMA Terminal broker wrapper (PAPER + LIVE paths, simulator)
  indicators.py           SMA, Wilder ATR/ADX/RSI, entry filters
  tick_sizes.py           Per-stock Groww tick size; every order/SL price is rounded here
  config.py, database.py, models.py, charges.py   SMA Terminal settings, DB, cost model
  tests/                  pytest suite (both engines)
frontend/src/
  app/                    Pages: / (dashboard), chart, trade, movers, scanner,
                          reports, settings, terminal (SMA terminal UI)
  components/             UI by feature (Dashboard, Trade, Terminal, Chart, ...)
  hooks/                  useTradingState, useWebSocket, useNotifications, useTheme
  lib/api.ts              ORB Desk client (NEXT_PUBLIC_API_BASE, default :8000)
  lib/smaApi.ts           SMA Terminal client (NEXT_PUBLIC_SMA_API_URL; else
                          <origin>/sma off localhost, 127.0.0.1:8001 on localhost)
scripts/run_sma_terminal.sh   Starts the SMA Terminal backend on :8001 in PAPER
Dockerfile, fly.toml, .dockerignore   Fly.io deployment
README.md, SMA_TERMINAL.md, PLAN.md   Product docs
```

## Where the SMA/ATR bot logic lives

- `backend/strategy_engine.py` – `StrategyEngine`: the 1-minute loop (`run`,
  `tick`, `on_minute`), crossover signals on closed candles (`iloc[-3]` vs
  `iloc[-2]`; the forming bar is never a signal), entry filters
  (`_entry_block`), stop-and-reverse (`apply_signal`, `_apply_locked`), ATR
  stop placement (`_open`, `_sl_price`, `_watch_stop`), daily loss / trade
  caps, square-off at `square_off_time`, and the kill switch.
- `backend/gap_trail.py` – optional SMA-gap moving stop and target
  (`stop_type = "SMA_GAP"`, PAPER only): levels from the SMA 9/21 gap % ×
  `gap_sl_mult` / `gap_tp_mult` (floored at `gap_min_pct`), recalculated each
  closed candle by `StrategyEngine._trail_gap_levels`; the stop only tightens.
  Exits are `GAP_SL_HIT` / `TARGET_HIT`. LIVE always uses the ATR stop.
- `backend/tsl.py` – Groww-style trailing stop (`stop_type = "TSL"`, PAPER
  and LIVE): stop `tsl_sl_points` ₹ from entry, moved `tsl_trail_points` ₹
  for each full step the price gains past its best since entry (never back);
  optional `tsl_target_points` ₹ target (0 = none). Trailed every tick by
  `StrategyEngine._trail_tsl`. In LIVE the exchange stop is moved in place
  with `GrowwClient.modify_sl` (Groww `modify_order`); a refused modify keeps
  the old stop, and a position restored after a restart (no stop id) stays at
  its saved stop. A LIVE target cancels the exchange stop before the exit.
  Exits are `TSL_HIT` / `TARGET_HIT`.
- Bollinger exit (`bb_exit`: `OFF` default | `BAND` | `MIDDLE` | `BOTH`,
  PAPER and LIVE): `indicators.bollinger_exit`, read once per closed candle
  after the entry by `StrategyEngine._watch_bollinger` on `bb_period` /
  `bb_std`. `BAND` books a close at the far band (`BB_TARGET`); `MIDDLE`
  exits a close back across the middle band once a close has been on the
  trade's side (`BB_MIDDLE`). Exits go through `_exit_now`, which in LIVE
  cancels the exchange stop first, like a target; a LIVE position restored
  without its stop id is left to that stop.
- `backend/research.py` – Research desk: a second, paper-only `ResearchEngine`
  (subclass of `StrategyEngine`) that runs next to the live bot during market
  hours. Own settings row (`BotConfig` id 2, `RESEARCH_CONFIG_ID`, first copied
  from row 1 with an empty Trade list, max 10 armed), own book (`RESEARCH`,
  ids `Q-n`), own P&L, caps and panic. Quotes come through the live client's
  `refresh` only; fills are local (`ResearchBroker`, built on `replay.LocalFills`,
  no Groww order path). No alerts. API mirrors the terminal under
  `/api/research/*` (state, chart, history, config, trade-symbols, bot); there is
  no research mode switch. UI: the terminal's Live desk / Research switch,
  remembered per browser or set with `/terminal/?desk=research`.
- `backend/bots.py` – Bots 2-4: more SMA bots next to the main desk (bot 1),
  each a `BotEngine` (subclass of `StrategyEngine`) that can trade LIVE. Own
  settings row (`BotConfig` id 3-5 via `config_id_for`; id 2 is research),
  first copied from bot 1 with an empty Trade list and PAPER, and its own
  `bot_name`. Own book: every trade row carries `TradeLog.bot` (1 = main
  desk, NULL on older rows), and the engine's queries filter by it
  (`bot_rows`), so trades, P&L, caps, panic and ids (`N2-1`, `P3-4`) are its
  own. Own order client (`BotGrowwClient`): its own PAPER/LIVE mode, paper
  fills and Groww SDK session, so one bot's mode never changes where another
  bot's order goes; quotes, candles and the per-second price call come from
  the main desk's client (`refresh_ltps` batches every bot's stocks).
  **A stock belongs to at most one LIVE bot** (`live_conflict`): checked when
  a stock is armed on a LIVE bot, when a bot is switched to LIVE, and in
  `StrategyEngine._open` just before every LIVE entry (`live_guard`).
  API: `/api/bots` (list), `/api/bots/kill-all`, and the terminal routes under
  `/api/bots/{2-4}/` (state, chart, history, config, trade-symbols, mode,
  bot/*). UI: the terminal's bot switch (`?desk=bot2`…), Settings picks the bot.
- `backend/replay.py` – "Replay a past day": a separate `ReplayEngine`
  (subclass of `StrategyEngine`) plays a past session's Groww 1-minute
  candles on its own clock (`_now`), through `ReplayBroker`, which fills
  locally and has no Groww SDK path. Trades are tagged `REPLAY` (own book
  and P&L; no daily trade cap, the loss limit still applies), no alerts are
  sent, and the live engine keeps running. The engine ticks on a fixed
  10-second grid of replay time (`_to_next_grid`), whatever the speed or server
  load, so the same day with the same settings always gives the same trades.
  A run covers one day or a range up to 45 calendar days, about 30 trading days (`parse_replay_range`, `MAX_RANGE_DAYS`), one
  fresh engine per day; each run is a `ReplayRun` row with a snapshot of the
  settings it used, and its trades carry `run_id` (day-wise P&L in
  `get_run`, shown in the blotter's Backtests tab).
  A replay plays one bot's settings and armed stocks (`ReplayStart.bot`, 1-4,
  default 1; `ReplayEngine(bot=)` reads that bot's settings row, never writes it,
  and tags its trades with that bot). Each terminal desk starts and follows only
  its own bot's replays. Each bot has its own replay player (`main.replays[1-4]`,
  `?bot=` on the `/api/replay*` routes; `main.replay` is bot 1's), so all four can
  replay together after market hours; playing replays split `replay.CPU_SHARE`,
  and stopping one closes only its own run's open rows.
  API: `/api/replay*`, `/api/replay/runs[/{id}]`. Refused while the replayed bot is LIVE.
  The chart endpoints (`/api/chart`, `/api/research/chart`, `/api/replay/chart`)
  take an optional `symbol` to draw another watched stock without moving the
  chart focus. The terminal shows stock tabs above the chart (the replay's
  stocks, or the armed/held ones); each opens `/terminal/chart/?symbol=…`
  (`&date=&run=` for a replay, `&desk=research`) in a new tab, a read-only
  chart that follows the replay while it plays and shows the replayed day
  afterwards. "Hold view" on the chart keeps its zoom, scroll and stock.
  The terminal's Strategy card (`StrategySummary`) switches each part of the
  strategy on or off for all stocks or one stock (gap mode, flip, entry
  filters, stop on/off and type, Bollinger exit, gap-mode exit options); each
  switch saves at once (`PUT /api/config` or `/api/config/stock/{symbol}`) and
  asks first before the stop goes off or, in LIVE, the flip changes. The
  numbers behind them are edited on the Settings page (`#sma-strategy`).
- `backend/sma_research/` – offline stock-selection research for the SMA bot
  (read-only; `python -m sma_research download|analyze`). `download` runs on
  the Fly machine after hours and only calls Groww's candle-history API;
  `analyze` replays the unchanged strategy per stock through `ReplayEngine`
  (`ResearchEngine`: fixed settings, no chart frame, minute steps while flat)
  into its own SQLite files and writes the evidence report. Driven by
  `.github/workflows/sma-stock-research.yml` (manual, refused in market hours).
- SMA gap range entry filter (`use_gap_long` / `use_gap_short`, off by
  default): `indicators._gap_reason` on the closed cross candle's signed
  gap % `(SMA fast − SMA slow) / SMA slow × 100`, inside `gap_long_min..max`
  for a buy or `gap_short_min..max` for a sell (negative values allowed).
  Wired through `_entry_block` like the other filters (chart ✕ "Gap",
  Telegram check line, per-stock settings).
- Candle direction entry filter (`use_candle_dir`, off by default):
  `indicators._direction_reason` on the last `candle_dir_count` closed
  candles; `candle_dir_rule` `CLOSES` (each close beyond the one before: up
  for a buy, down for a sell), `COLOUR` (green / red candles) or `BOTH`.
  Wired through `_entry_block` (chart ✕ "Candles", per-stock settings); in gap
  mode it is read when the order would go, not at the cross.
- `backend/gap_mode.py` – SMA gap mode (`use_gap_mode`, off by default,
  PAPER and LIVE): a cross only arms the trade (`StrategyEngine._gap_minute`,
  `_gap_pending`); the order goes on the first closed candle whose signed gap
  is ≥ `gap_entry_long` / ≤ `gap_entry_short`, after `gap_entry_delay_min`
  more minutes if set, given up after `gap_entry_window_min` (0 = until the
  next cross). `_watch_gap_fade` closes it (`GAP_FADE`, via `_exit_now`) when
  the gap fades back to `gap_exit_long` / `gap_exit_short` after clearing it,
  or gives back `gap_giveback_pct` of its widest. An exit level may sit beyond
  the entry level (a lock-in level, e.g. sell in at −0.08, out at −0.39): the
  exit then arms only once the gap has been that wide. An opposite cross closes at
  once (`_close_on_cross`) and the reverse waits for its own gap.
  Optional fade confirmations (`gap_mode.fade_confirmed`, off by default):
  `gap_fade_confirm_sma` holds a fade until a candle closes on the wrong side
  of the slow SMA (rides out a pullback), `gap_fade_min_candles` needs the gap
  to narrow N closed candles in a row. `gap_fade_intrabar` judges the fade
  about once a second on the live price as if that second closed the candle
  (`StrategyEngine._gap_fade_live`, probing a copy of the closed-candle state).
- `backend/candle_patterns.py` – candle-pattern entries (`entry_mode =
  "PATTERN"`, default `"SMA"`, per bot and per stock, PAPER and LIVE). Each
  time a candle of `pattern_tf` minutes (1/3/5, built from the 1-minute tape
  from 09:15) closes, `StrategyEngine._pattern_minute` first closes the open
  trade (`CANDLE_END`, via `_exit_now`, which cancels a LIVE stop first), then
  `pattern_call` reads the pattern (names identical to
  `frontend/src/lib/candlePatterns.ts`): a bullish one buys and a bearish one
  shorts at the start of the next candle through `apply_signal` (filters,
  stop, flip, cut-off, caps, one-LIVE-bot-per-stock all apply; gap mode does
  not). Options: `pattern_set` (`STRONG` | `ALL`), `pattern_trend` (only with
  the SMA fast/slow side), `pattern_min_edge` (skip when the last 14 candles'
  average range is under N× the round-trip charges for the qty; 0 = off). No
  Telegram for a refused pattern. The stock research baseline keeps `SMA`.
- Flip strategy (`flip_orders`, off by default, per-stock, PAPER and LIVE):
  every condition is unchanged, but `StrategyEngine._open` sends the order the
  other way (a buy signal sells, a sell signal buys). The position keeps
  `flipped` (saved on `TradeLog.flipped`, restored after a restart);
  `OpenPosition.signal_direction` is the signal's side, and the cross, gap
  mode/fade and Bollinger exits judge that, so a flipped trade opens and closes
  when the unflipped one would. Stops and targets guard the real position.
  The offline stock research baseline keeps it off.
- SMA cross exit (`cross_exit`, on by default, per bot and per stock, PAPER and
  LIVE; `strategy_engine.cross_exits`): on, an opposite cross closes the trade
  and opens the reverse (`MA_CROSS`). Off, `_apply_locked` and `_gap_minute`
  hold the position through an opposite cross (crosses only open trades while
  flat); the stop / target, gap fade, Bollinger exit and the square-off still
  close it, and with none of them on the trade runs to the square-off. The
  terminal's Strategy card and Settings warn when no exit is on
  (`strategyChecks.noExitOn`). Candle patterns ignore it (`CANDLE_END`). The
  stock research baseline keeps it on.
- `backend/paper_wallet.py` – practice wallet for bots 1-4 in PAPER, like one
  Groww account (`PaperWallet` row 1; `/api/wallet`, add / withdraw / repay /
  reset / margin; the WalletChip top right in the Navbar and terminal header).
  Off until money is loaded (₹1 to ₹100 crore a time); then each PAPER entry
  (`StrategyEngine._open`, `uses_wallet`) blocks `margin_pct` (default 20% =
  5×) of its value, and the margin comes back with the P&L before charges when
  the trade closes. Free balance = loaded + P&L of PAPER trades opened since the
  start − margin of open PAPER trades, worked out from the trade book. A short
  balance never stops the order: the wallet borrows the shortfall (`loan`) and
  the screens pop it up (`last_loan`) until it is repaid. Every add,
  withdrawal, loan, repayment, close and margin change is a `WalletEntry` row
  (`/api/wallet/statement`); each LOAN row is that loan's record (bot, stock,
  qty, price, margin needed, borrowed, repaid, due; `/api/wallet/loans`), and
  repayments clear the oldest loan first. No interest. Replays use the same
  wallet (owner's choice): REPLAY trades of runs started since the start
  (`ReplayRun.created_at`, since a replayed trade's times are the past day)
  block margin and return P&L like PAPER ones, and deleting a run takes its
  P&L back off. LIVE (real Groww balance) and the research engines
  (`uses_wallet = False`: Research desk, `sma_research`) never touch it.
- `backend/tick_store.py` – second-by-second prices: while the market is open
  the live engine fetches every watched stock's last trade in one batched
  Groww call a second (`GrowwClient.refresh_ltps`; the minute history still
  refreshes every 55 s) and records real Groww prices (`price_ticks`, one row
  per stock per second, kept 10 days). `/api/ticks` serves them to the data
  table, whose rows expand into their seconds. Groww's history has no seconds,
  so replays and earlier days have none; `SMA_RECORD_TICKS=all` also records
  simulator prices for a local test. The "1s ON/OFF" chip in the status bar
  (`PUT /api/ticks/feed`, `BotConfig.second_ticks` on row 1, default on) turns
  the per-second fetch and record off for both desks; quotes then come on the
  normal few-second interval.
- P&L on the SMA screens is shown **before charges** (owner's choice; display
  only). `frontend/src/lib/pnlBasis.ts` turns loaded trade lists and backtest
  runs to that basis (a trade's `net_pnl` then holds its gross), and the server
  sends gross figures next to the net ones (`books[].open_gross/closed_gross/
  day_gross`, `open_gross_total`, `kpis.gross_wins`, `/api/bots` `gross_today`,
  chart markers `gross_pnl`). Charges stay recorded and shown on their own; the
  daily loss limit, stored `net_pnl` and Telegram alerts still use the net.
- `backend/indicators.py` – `enrich()` adds `sma_fast`/`sma_slow`
  (`sma_9`/`sma_21`), Wilder `atr_14`, `adx_14`; plus RSI and
  `entry_filter_reason` for the optional VWAP/volume/density/RSI checks.
- `backend/main.py` – the terminal's API (`/api/state`, `/api/config`,
  `/api/mode`, `/api/bot/*`, `/api/trades`, `/ws/stream`) and
  `boot_engine`/`stop_engine`.
- `backend/config.py`, `backend/models.py` – settings and the `BotConfig` /
  trade tables. `backend/charges.py` – Groww intraday charges.
- Tests: `backend/tests/test_sma_atr_terminal.py`, `test_ta_filters.py`,
  `test_signal_regression.py`.

The ORB bot (a different strategy) is `backend/app/strategies/orb_strategy.py`,
driven by `backend/app/services/strategy_runner.py`.

## Where Groww orders are placed

Real orders reach Groww in only two places:

1. **SMA Terminal, LIVE mode** – `backend/groww_client.py`, `GrowwClient`:
   `place_entry`, `place_exit`, `place_sl` → `_live_limit` / `_live_order`
   (and `modify_sl`, which moves an open stop for the trailing stop)
   (MIS limit orders with a 0.20% protection buffer, plus an exchange `SL`).
   In PAPER these return local `PAPER…` ids and never touch the SDK.
   Called from `strategy_engine.py` (`_open`, `_close_position`,
   `_cancel_sl_verified`). Bots 2-4 send theirs through their own
   `bots.BotGrowwClient` (a `GrowwClient` with its own mode), never through
   the main desk's client.
2. **ORB Desk manual desk, "groww" execution** –
   `backend/app/services/manual_desk.py`, `_send_groww_order` →
   `backend/app/brokers/groww_client.py`, `GrowwClient.place_order`
   (also `square_off_all`, `cancel_order`). Only used when
   `state.manual_live` is true.

The ORB bot never sends real orders. Every bot and strategy-account entry goes
through `place_paper_entry` in `backend/app/services/execution.py`, which fills
against the local `paper_engine`. `backend/app/brokers/groww_client.py` is
otherwise used for login, quotes, candles, and margin.

## How PAPER vs LIVE works

There are separate switches. All of them boot safe.

- **ORB Desk data source** (`app/services/market_data.py`, `DataSource`):
  `SIMULATED` (default, random prices) or `LIVE` (real NSE quotes from Groww).
  This changes prices only, not execution.
- **ORB strategy account execution:** always virtual (`place_paper_entry`).
  `DEFAULT_MODE=paper` in `app/core/config.py`.
- **ORB manual desk execution** (`POST /api/manual/execution`): `paper`
  (default) or `groww`. `groww` needs `confirm_live=true` and sets
  `state.manual_live = True`. A live order is refused on simulated prices,
  without Groww margin, or without a Groww order id (a fill is booked only
  after Groww accepts it).
- **SMA Terminal mode** (`POST /api/mode` in `backend/main.py`): `PAPER`
  (default, `TRADING_MODE`/`trading_mode`) or `LIVE`. `LIVE` needs
  `confirm_live=true` from the UI confirmation modal and a saved Groww
  session/`GROWW_ACCESS_TOKEN`. In PAPER the client fills locally at the
  candle close/LTP, using real Groww candles when a token works and the market
  is open, otherwise the built-in `CandleSimulator`.
- **Market hours apply to PAPER too.** No new entry (cross or force order)
  outside the cash session, before 09:20, or after `entry_cutoff_time`
  (default 15:00, never later than `square_off_time`). After the cut-off an
  opposite cross still closes a position but does not open the reverse.
- **Only stocks on the Trade list (`trade_symbols`) are ordered.** The list is
  re-read just before each entry, Force order refuses an unarmed stock, and a
  removed stock is no longer quoted unless it is still held or on the chart. Practice
  positions are squared off at `square_off_time` even when the bot is paused,
  and a practice position from an earlier day is closed on the next tick.

- **Bots 2-4** (`backend/bots.py`): each has its own PAPER/LIVE switch
  (`POST /api/bots/{n}/mode`, same `confirm_live` and Groww-session rules) and
  boots in PAPER. Two LIVE bots never trade the same stock.
- **Research desk** (`backend/research.py`): always practice money, whatever
  the SMA Terminal mode is. It runs alongside a LIVE bot without touching it.

Trades are tagged with their mode (`PAPER`/`LIVE`/`REPLAY`/`RESEARCH`,
`paper`/`live`) so the books stay separate in reports and CSV exports.

## Running locally

```bash
# ORB Desk backend (:8000)
cd backend && python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # then set ENCRYPTION_KEY (Fernet key, see README)
.venv/bin/uvicorn app.main:app --reload --port 8000

# SMA Terminal backend (:8001), boots in PAPER
scripts/run_sma_terminal.sh

# Frontend (:3000; terminal UI at /terminal)
cd frontend && npm install && cp .env.local.example .env.local && npm run dev

# Tests (pytest-asyncio is required; without it every async test fails)
cd backend && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
```

No credentials are needed. The desk starts on the simulated feed.

## Deployment to Fly.io

- `fly.toml`: app `paltra`, region `sin`, `internal_port = 3000`, HTTPS
  forced, one machine always running (`auto_stop_machines = 'off'`,
  `min_machines_running = 1`), four shared CPUs (`shared-cpu-4x`, 4 CPUs,
  2 GB), and a volume `data` mounted at `/data`. Change the machine size in
  `[[vm]]`: every deploy applies it and undoes a resize made in the Fly
  dashboard.
- `Dockerfile` (`python:3.13-slim-bookworm`): installs
  `backend/requirements.txt`, copies `backend/` to `/app`, copies the
  **pre-built** static frontend from `frontend/out/` to `/app/static`, and
  copies `main.py` to `sma_terminal_main.py` so the SMA terminal can be
  imported next to the desk. It runs `uvicorn app.main:app` on
  `0.0.0.0:3000` as a non-root user, with a healthcheck on `/api/health`.
  It sets `DATABASE_URL=sqlite+aiosqlite:////data/trading.db` (kept on the
  volume) and `DEFAULT_MODE=paper`.
- At runtime one process serves everything: the desk API, the SMA terminal
  mounted at `/sma` (`app/sma_host.py`), and the static site at `/`.
  `rewrite_terminal_bundle()` rewrites `http://127.0.0.1:8001` in the exported
  JS to `https://paltra.fly.dev/sma`.
- `.dockerignore` keeps `.env` files, keys, databases, `node_modules`, tests,
  and `research_data/` out of the build context.
- **Deploys come from `main` only**, through
  `.github/workflows/test-and-deploy.yml`:
  - every pull request and push runs the backend tests (Python 3.13,
    `requirements-dev.txt`) and the frontend typecheck plus static export
  - a push to `main` that passes both runs `flyctl deploy --remote-only`
    with the exported `frontend/out/` (built in CI, not committed)
  - **no automatic deploy during NSE market hours** (09:00–15:45 IST,
    Mon–Fri): the `market-hours-gate` job skips the deploy with a warning.
    "Run workflow" (workflow_dispatch) on `main` deploys at any time, so use
    it after 15:45 to ship a push that was held back
  - it needs the repo secret `FLY_API_TOKEN`
    (`fly tokens create deploy -a paltra`); the deploy job runs in the
    `production` environment, which GitHub creates on first use (add required
    reviewers there for a manual approval step)
- Do not deploy from a laptop or a feature branch. If you must deploy by
  hand, do it from a clean checkout of `main`:

  ```bash
  cd frontend && NEXT_OUTPUT=export npm run build   # produces frontend/out/
  cd .. && fly deploy
  ```

  App secrets are set on the machine, never baked into the image:
  `fly secrets set ENCRYPTION_KEY=... FRONTEND_ORIGIN=https://paltra.fly.dev`.
