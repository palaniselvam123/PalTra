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
  app/                    Pages: / (dashboard), chart, trade, movers, scanner, news,
                          reports, settings, terminal (SMA terminal UI)
  components/             UI by feature (Dashboard, Trade, Terminal, Chart, ...)
  hooks/                  useTradingState, useWebSocket, useNotifications, useTheme
  lib/api.ts              ORB Desk client (NEXT_PUBLIC_API_BASE, default :8000)
  lib/smaApi.ts           SMA Terminal client (NEXT_PUBLIC_SMA_API_URL; else
                          <origin>/sma off localhost, 127.0.0.1:8001 on localhost)
scripts/run_sma_terminal.sh   Starts the SMA Terminal backend on :8001 in PAPER
Dockerfile, fly.toml, .dockerignore   Fly.io deployment
fly.scanner.toml, fly.backtest.toml   Scanner and backtest servers (SCANNER.md, BACKTEST.md)
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
  Units are **₹ (points)** or **% of entry price** via `tsl_mode`
  (`POINTS` default | `PERCENT`): with PERCENT, `tsl_sl_pct`,
  `tsl_trail_pct` and `tsl_target_pct` are read instead, and
  `tsl_settings(cfg, entry_price=fill)` turns them into ₹ at trade
  entry so the trail / target maths is unchanged. The display always
  reports both sets of numbers plus `tsl_mode`.
  The Scalp page's Arm prompt and Arm ticked (`ArmPrompt`, `BulkArm`) have a ₹ / % switch that saves
  `tsl_mode` with the matching numbers as the stock's own (the scanner link's `StockChange` takes them too).
- `backend/pivots.py` – classic daily pivot points (display + optional target, PAPER and LIVE): from the previous
  session's high / low / close in the stock's 1-minute tape (`_tapes`, else `_frames`; the earlier days the SMAs warm up
  on), P = (H + L + C) / 3, R1 = 2P − L, S1 = 2P − H, R2 = P + (H − L), S2 = P − (H − L) (`levels`, cached per stock and
  day in `StrategyEngine._pivot_cache` / `_pivots`). The chart payload carries `pivots` and `pivot_target`; the chart
  draws them as labelled lines (Indicators → "Pivot points", `chartStyle` key `pivots`, Auto = while the pivot target is
  on). **Pivot target** (`pivot_target`: `OFF` default | `R1` | `R2`, per bot and per stock): `_watch_pivot_target`, every
  price, closes a long at R1 (a short at S1), or at R2 / S2 when the entry is already beyond the first level (`R2` uses R2
  / S2 only; no level beyond the entry = no pivot target), via `_exit_now` (`PIVOT_TARGET`; a LIVE stop is cancelled
  first, a LIVE position restored without its stop id is left to that stop). Settings → 5. Stop-loss and target. Counts
  as an exit for `strategyChecks.noExitOn`. The stock research baseline keeps it off.
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
- `backend/bots.py` – Bots 2-5: more SMA bots next to the main desk (bot 1),
  each a `BotEngine` (subclass of `StrategyEngine`) that can trade LIVE. Own
  settings row (`BotConfig` id 3-6 via `config_id_for`; id 2 is research),
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
  `/api/bots/{2-5}/` (state, chart, history, config, trade-symbols, mode,
  bot/*). UI: the terminal's bot switch (`?desk=bot2`…`bot5`), Settings picks the bot.
- `backend/fast_bot.py` – fast-mover hand-off to **Bot 5** (`on`, off by default, PAPER and LIVE; `fast_bot.json` next to
  the SMA DB, `FAST_BOT_FILE`): Bots 1-4 trade many stocks small; a stock a source bot (`sources`, default 1-4) **holds**
  that moves fast **in that trade's favour** is cut there and Bot 5 opens the same side at once with its own quantity.
  `FastBot.run` (desk only, started in `boot_engine`, not with `server_role.no_orders()`) every `POLL_SEC` 5 s while NSE
  is open reads the bots' own last prices (`_ltps`, no extra Groww call). Fast (`rule`): `MOVE` (≥ `pct` 0.5 % its way
  within `minutes` 3, from the window's low for a long / high for a short), `SPEED` (≥ `speed` 0.15 %/min its way over
  `speed_minutes` 5) or `EITHER`, and only while the trade is already at least `min_profit_pct` (0.3, 0 = any) % in profit
  from its entry (`in_profit`; a losing trade that bounced off its low never moves; a replay run saved before the check
  plays with 0). `ready` first checks Bot 5 can take it (RUNNING, market hours, before its cut-off /
  square-off, under its trade cap, not already holding it; same mode only: a LIVE Bot 5 never takes a PAPER trade and
  a LIVE trade is never cut for a PAPER Bot 5; a LIVE position restored without its stop id stays), then the source's `close_symbol` (`FAST_MOVE_HANDOFF`, LIVE stop cancelled first),
  in LIVE a wait until Groww shows no shares (`FLAT_WAIT_SEC`), `recovery.handoff(to_bot=5)` (off the source's Trade list,
  onto Bot 5's; one LIVE bot per stock), and Bot 5's `direct_order` (entry reason `FAST_MOVE`: no SMA cross, VWAP / RSI
  or other entry check; Bot 5's stop and exits run it). After `bot5_trades` (1, 0 = all day) Bot 5 entries on the stock
  (`FastBot.gate`, set as Bot 5's `StrategyEngine.entry_gate`, checked in `_open` before every entry; a reverse past it is
  refused) and once Bot 5 is flat on it, `give_back` returns it to its source bot (`recovery.handoff(5, to_bot=source,
  chop=False)`: that bot's own settings, waits for its next cross; memory only, a restart leaves it on Bot 5). Each stock
  once a day, at most `max_day` (10) a day. `GET/PUT /api/bots/fast-mover` (turning it on while Bot 5 is LIVE needs `confirm_live`,
  409 without); Settings → "Fast movers → Bot 5" (`components/Settings/FastMoverBot.tsx`). **Replays too**: a new run
  of a source bot keeps `fast_bot.replay_snapshot` in its settings (`fast_bot`: these settings plus Bot 5's strategy
  fields and per-stock settings, so Resume plays the same); `ReplayEngine._fast_replay` (after each tick) cuts a held
  stock that moves fast its way (`FAST_MOVE_HANDOFF`, `close_symbol`) and opens the same side at once through
  `direct_order` (`FAST_MOVE`) with Bot 5's settings as that stock's override until its `bot5_trades` are done (`_fast_gate`, then the replayed
  bot's own settings; a run saved before the setting plays 0 = all day); a Bot 5 setting never saved is carried as its column default (`_column_default`), so the replayed
  bot's own value never leaks into Bot 5's trade
  (`_fast_moved`, merged in `load_config`; the feed gets enough bars for either candle size). Trades stay in the
  replayed bot's book; `replay._fast_move_part` gives the run's `fast_move` totals (shown on the Backtests run). The
  backtest server copies `fast_bot.json` from the desk with the bot settings (`export_bot_settings` → `sync`).
  Research never hands off.
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
  A run cut short survives a deploy: at boot `close_orphan_replay_rows(interrupted=True)`
  marks a run that was playing `INTERRUPTED` (a Stop leaves `STOPPED`), and
  `resumable_runs(bot)` lists runs with days left (not Scalp-pick runs).
  `ReplaySession.resume(run_id)` drops the unfinished day's partial trades and
  plays from that day with the run's saved settings (`ReplayEngine(settings=)`),
  keeping the finished days, so the run ends as if it never stopped. The replay
  bar asks to resume (`/api/replay/resumable`, `POST /api/replay/resume`), and
  the Backtests tab has a Resume button.
  API: `/api/replay*`, `/api/replay/runs[/{id}]`. Refused while the replayed bot is LIVE. **On the main desk a replay
  starts only outside market hours** (`main._refuse_desk_replay_in_market`, 09:00-15:45 IST Mon-Fri, 409; start, resume and
  Scalp-pick runs), so it never slows the live bots; the backtest server takes them then (`DESK_REPLAY_HOURS=any` lifts
  it; the test suite sets it).
  The chart endpoints (`/api/chart`, `/api/research/chart`, `/api/replay/chart`)
  take an optional `symbol` to draw another watched stock without moving the
  chart focus, and an optional `interval` (1, 2, 3, 5, 10, 15, 30, 60 or 240
  minutes) to draw the 1-minute tape (`engine._tapes`) in candles of that size
  instead of the bot's own: the chart's candle-size menu asks for it for sizes
  finer than the bot's candle (1 or 3 minutes for a 5-minute bot) or ones it does
  not divide into; bigger multiples are still merged in the browser. Display only
  (no refused-cross marks on another size; the bot's frames are untouched). The terminal shows stock tabs above the chart (the replay's
  stocks, or the armed/held ones); each opens `/terminal/chart/?symbol=…`
  (`&date=&run=` for a replay, `&desk=research`) in a new tab, a read-only
  chart that follows the replay while it plays and shows the replayed day
  afterwards. "Hold view" on the chart keeps its zoom, scroll and stock.
  The chart's **Indicators** panel (`components/Terminal/ChartIndicators.tsx`, `lib/chartStyle.ts`, kept in the
  browser) shows or hides SMA fast / slow, VWAP, EMA, Bollinger, Supertrend, ATR and RSI (`Auto` = while the strategy
  uses it, the old behaviour) and sets each line's colour, thickness and pattern and the candle colours; an EMA the
  strategy does not use is worked out in the browser (`emaValues`). Display only.
  Every bar that sticks to the top (desk menu bar, terminal header, blotter bar, Settings section links, Settings bot tabs
  (`settings-bot-tabs`, under the section links) and Save bar, chart page header) has a freeze / unfreeze pin (`components/ui/PinToggle.tsx`, `lib/usePinned.ts`, kept per browser).
  The terminal's Strategy card (`StrategySummary`) switches each part of the
  strategy on or off for all stocks or one stock (gap mode, flip, entry
  filters, stop on/off and type, Bollinger exit, gap-mode exit options); each
  switch saves at once (`PUT /api/config` or `/api/config/stock/{symbol}`) and
  asks first before the stop goes off or, in LIVE, the flip changes. The
  numbers behind them are edited on the Settings page (`#sma-strategy`).
  That page (`StrategyConfigPanel`) opens with **What's switched on** (`activeRules`: every rule that is on, in
  plain words, grouped, with Turn off; warns at 3+ extra exits or 4+ entry checks), then numbered groups:
  1 When to enter, 2 Checks before entering, 3 When to exit, 4 SMA gap mode, 5 Stop-loss and target, 6 Size,
  daily limits and times, 7 Advanced (recovery / hand-offs, 1-minute review). Layout only: `strategyBody()`
  saves the same fields.
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
  (`StrategyEngine._gap_fade_live`, probing a copy of the closed-candle state). `gap_fade_minute` does the
  same once per closed 1-minute candle inside a longer bot candle, from Groww's 1-minute history (`_tapes`),
  so it also works after hours and in replays (`StrategyEngine._gap_fade_minute`; both share `_gap_fade_probe`).
- `backend/candles.py` – candle interval (`candle_minutes`: 1 default, 2, 3, 5,
  10, 15; per bot and per stock, PAPER and LIVE). `StrategyEngine.tick` groups
  each stock's 1-minute Groww tape into candles of that length from 09:15 IST
  (`resample`; open / high / low / close, Groww's running volume total of the
  last minute) before it is stored in `_frames`, so the SMAs, ATR stop, filters,
  gap mode, Bollinger exit, chart and replays all read the longer candles and a
  signal is judged once each one closes (`_candle_is_behind(..., minutes)`).
  Candle patterns keep the 1-minute tape (`pattern_tf`). The chart is told the
  size (`candle_minutes` in the chart payload) and only merges up from it. The
  stock research baseline keeps 1.
- `backend/candle_patterns.py` – candle-pattern entries (`entry_mode =
  "PATTERN"`, default `"SMA"`, per bot and per stock, PAPER and LIVE). Each
  time a candle of `pattern_tf` minutes (1/3/5/10/15, built from the 1-minute tape
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
  **Candle colour** (`entry_mode = "COLOUR"`, `colour_call`, same `pattern_tf` candles): each
  time one closes, `StrategyEngine._colour_minute` reads its colour: green asks for a long, red
  a short, a doji nothing (the trade is held). A trade is held while the candles keep its
  colour (judged on `signal_direction`, so a flipped stock follows its signal); the first
  candle of the other colour closes it (`COLOUR_TURN`, via `_exit_now`, which cancels a LIVE
  stop first) and `apply_signal(..., why="CANDLE_COLOUR")` opens the other way at once (every
  check, the stop, flip, cut-off, caps and one-LIVE-bot-per-stock apply; after a stop-out the
  next candle's colour enters again). No Telegram for a refused candle.
  In both modes (`per_candle`) the bot's own candle is 1 minute (`candles.candle_minutes`),
  the order goes at the start of the next candle, and the tall-candle pullback, candle-extreme
  limit and entry-candle wait are skipped; gap mode and the 1-minute review do not apply.
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
- `backend/exit_candle.py` – mixed candles (`exit_candle_minutes`: 0 = off by default, 1/2/3/5/10/15/30/60; per bot and
  per stock, PAPER and LIVE): enter on the bot's own candle (`candle_minutes`), exit on the SMA cross of another candle size.
  While it is set and differs from `candle_minutes` (and `cross_exit` is on), `cross_exits` reads as off for the bot's own
  candle, so an opposite cross there neither closes nor reverses the trade. `StrategyEngine._watch_exit_candle`, once per
  newly closed exit candle (`pos.xc_bar`, memory only), groups the 1-minute tape (`_tapes`) into that size from 09:15
  (`closed_frame`), and `judge` closes the trade (`EXIT_CANDLE_CROSS`, via `_exit_now`, which cancels a LIVE stop first) when
  the exit candle's SMAs cross against the trade on a candle closed after the entry, or are already against it while the
  bot's own candle's SMAs are against it too (a trade entered against the exit candle's trend is not held once its own cross
  turns). No reverse. Stops, targets, the other exits and the square-off are unchanged. A LIVE position restored without its
  stop id is left to that stop. Settings → 3. When to exit ("Read the cross exit on"). The stock research baseline keeps it off.
- `backend/late_entry.py` – late entry (`late_entry_min`, 0 = off by default, per bot and
  per stock, PAPER and LIVE): a fresh cross refused only by a ticked entry filter or ADX
  (`late_entry.refused_by_filter`: `… ignored — …` / `closed on … — …`, never the cut-off or
  trade cap) stays armed (`StrategyEngine._late_pending`). On each later closed candle
  `_late_minute` drops it when the SMAs crossed back or `late_entry_min` minutes have passed
  since the cross candle, else re-runs `apply_signal` (alert off; every check, cut-off, cap,
  stop, flip and one-LIVE-bot-per-stock still apply) and the first candle the filters agree
  opens the trade. A new cross replaces it. Gap mode ignores it. Settings → 1. When to enter.
- `backend/join_trend.py` – join the morning trend (`join_trend`, off by default, and
  `join_after_min`, default 15; per bot and per stock, PAPER and LIVE): a stock that opens
  already trending has no fresh cross, so it is never traded. With it on, once per day per
  stock, from 09:15 + `join_after_min` while flat, `StrategyEngine._joinable` takes the side
  the SMAs are on (`join_trend.side_start`: the run's first closed candle, clipped to today)
  when that run began before the join time, and `_join_minute` asks `apply_signal` (alert off)
  on each closed candle until every check agrees; in gap mode it arms `_gap_pending` instead.
  Never twice a day (`_joined_today`) or on a run the bot already entered (`_side_entered`,
  set in `apply_signal`); a later cross is a normal cross. A cross printed before the stock was
  armed is still not traded as a cross. Settings → 1. When to enter.
- `backend/counter_candles.py` – counter candles (off by default, per bot and per stock, PAPER and LIVE):
  a run of closed candles against the trade turns it before the SMAs cross. **Exit**
  (`counter_exit_count` N, 0 = off, max 10): `StrategyEngine._watch_counter_candles` closes an open
  trade (`COUNTER_CANDLES`, via `_exit_now`, which cancels a LIVE stop first) once its last N
  candles closed after the entry all went against it (`counter_rule` `COLOUR` default | `CLOSES` |
  `BOTH`, read by `indicators._direction_reason`) and, with `counter_need_fast` (default on), the
  last close is past the fast SMA on that side, and, with `counter_min_body` (× ATR, 0 = off by default, max 5),
  each of those N candles has a body |close − open| of at least that × its ATR; once per closed candle (`pos.cc_bar`); a LIVE
  position restored without its stop id is left to that stop. **Early reversal entry**
  (`counter_entry`): while flat, `_counter_minute` takes the same run against the side the SMAs are
  on (fast below slow then N green closing above the fast SMA buys; N = the exit count, else 3)
  through `apply_signal` (alert off; every check, cut-off, cap, stop, flip and one-LIVE-bot-per-stock
  apply), once per SMA run (`_counter_done`, keyed on `join_trend.side_start`). Gap mode and candle
  patterns ignore it. **Re-enter** (`counter_reenter`, off by default, needs the exit; `counter_reenter_max` 1-10, default 3):
  after a counter-candle exit while the SMAs are still on the trade's side, `_watch_counter_candles` keeps the exit candle's
  low (short) / high (long) in `_reentry` (memory only); `_reentry_minute` re-enters through `apply_signal` (entry reason
  `COUNTER_REENTRY`, alert off, every check applies) on the first later closed candle that breaks it the trade's way
  (`counter_candles.resumes`: a red candle closing below that low / a green one above that high), at most
  `counter_reenter_max` times per SMA run (`_reentry_used`); dropped when the SMAs cross back, at the cut-off / trade cap,
  or when a fresh cross takes over. Settings → 1. When to enter (early entry) and 3. When to exit. The stock research
  baseline keeps all three off.
- `backend/ema_filter.py` – EMA cross check (off by default, per bot and per stock, PAPER and LIVE): a
  second pair of lines next to the SMA ones, like Groww's "EMA Cross 9 200" (`ema_fast` / `ema_slow`,
  defaults 9 / 200, 1-500, typed in Settings → 2. Checks before entering; the exit box is in 3. When to exit). The bot still trades the SMA
  cross. **Entry** (`use_ema`): wired through `_entry_block` like the other filters (check name `ema`,
  chart ✕ "EMA"): a buy only while EMA fast > EMA slow on the last closed candle, a sell only while
  below; refused while the slow EMA has fewer than `ema_slow` closed candles. **Exit** (`ema_exit`):
  `StrategyEngine._watch_ema_exit` closes an open trade (`EMA_CROSS`, via `_exit_now`, which cancels a
  LIVE stop first) on a candle that closed after the entry on which the EMAs crossed against it;
  once per closed candle (`pos.ema_bar`); a LIVE position restored without its stop id is left to
  that stop. EMAs are `ewm(adjust=False)` on the bot's own candles. The stock research baseline keeps
  both off.
- `backend/supertrend.py` – Supertrend (off by default, per bot and per stock, PAPER and LIVE): the same
  line as Groww's / TradingView's Supertrend (`st_period` ATR length, default 10, 1-100; `st_mult` factor,
  default 3, 0.5-10; Wilder ATR seeded with the SMA of the first `st_period` true ranges, bands on
  (high + low) / 2 that only tighten while the trend holds, a flip on a close beyond the other band), on the
  bot's own closed candles. **Entry** (`use_supertrend`): through `_entry_block` (check name `supertrend`,
  chart ✕ "ST"): a buy only while it is up, a sell only while down. **Exit** (`st_exit`):
  `StrategyEngine._watch_supertrend_exit` closes the trade (`SUPERTREND`, via `_exit_now`, which cancels a
  LIVE stop first) on a candle closed after the entry on which it flipped against the trade; once per closed
  candle (`pos.st_bar`); a LIVE position restored without its stop id is left to that stop. **Stop loss** (`stop_type = "SUPERTREND"`,
  with the stop on; Settings → 5. Stop type "Supertrend"): at entry the stop sits on the line of the last closed candle when
  it guards the real position (`supertrend.stop_level`: under a long, above a short, and beyond the fill), else the ATR
  stop until it does; `StrategyEngine._trail_supertrend`, once per newly closed candle (`pos.st_stop_bar`), moves it to the
  new line, tighter only, through `_move_stop` (shared with the TSL: LIVE modifies the Groww stop with `modify_sl`, 2 s
  apart, a refused modify keeps the old live stop, a LIVE position restored without its stop id stays at its saved stop;
  saved on `TradeLog.sl_trigger_price`). Hit on the price like any stop (`ST_SL_HIT`; in LIVE the exchange stop fills it,
  no second exit). A restart resumes it from the saved stop (`st_stop` restored from the stock's settings). The chart draws
  it (`stUp` green / `stDown` red rows from `candle_rows`, `chart_filters.st_line`) while either is on; EMA
  lines likewise (`emaFast` / `emaSlow`, `ema_lines`). The stock research baseline keeps both off.
- `backend/stop_plan.py` – staged stop plan (`stop_plan`, off by default, per bot and per stock, PAPER and LIVE, replays
  too; needs stop type ATR with the stop on, else `OpenPosition.plan_r` is None and nothing happens). R = |fill − initial
  ATR stop| (`atr_multiplier`, e.g. 2.5). `StrategyEngine._trail_stop_plan` (every tick, after the Supertrend stop, before
  `_watch_stop`) reads the best price since entry (`pos.high` / `pos.low`): at +`plan_be_r` R (1.5) the stop moves to
  breakeven = fill ± the round-trip charges per share (`stop_plan.breakeven`, `charges.calculate_charges`); at
  +`plan_trail_r` R (2.0) it follows the Supertrend line (`supertrend.stop_level`, `st_period` / `st_mult`, read once per
  closed candle, `pos.plan_bar` / `plan_line`), never below breakeven. Tighter only (`tighten`), stages only forward
  (`pos.plan_stage`), moved through `_move_stop` (LIVE `modify_sl`, 2 s apart, a refused modify keeps the old stop, a LIVE
  position restored without its stop id stays at its saved stop). A stage whose new stop the price is already through
  closes at once (`PLAN_STOP`, via `_exit_now`, which cancels a LIVE stop first); a hit of a moved stop is `PLAN_SL_HIT`
  (LIVE: Groww's stop fills it, no second exit). Each stage change is appended to `TradeLog.stop_log` ("11:42 stop →
  breakeven (+1.5R) at …"), shown under the exit reason in the blotter and in the CSV. A restart restores R from the ATR
  stop at entry and stage 1 when the saved stop is at or past the entry. Settings → 5. Stop-loss and target. The stock
  research baseline keeps it off.
- `backend/level_check.py` – price level entry check (`use_level`, off by default, per bot and per stock, PAPER and LIVE,
  entries only): through `_entry_block` (check name `level`, chart ✕ "Level") the closed signal candle must close beyond
  `level_line` on the trade's side: `BB_MIDDLE` (default; buy above / sell below the Bollinger middle band, `bb_period` /
  `bb_std`), `BB_UPPER` (buy above the upper band, sell below the lower), `PIVOT_P` (the day's pivot P, `pivots.levels` on
  the frame's previous session) or `PIVOT_R1` (buy above R1, sell below S1). A line not known yet refuses the entry.
  Settings → 2. Checks before entering. The stock research baseline keeps it off.
- `backend/slope_exit.py` – SMA 9 slope exit (`slope_exit`, off by default, per bot and per stock, PAPER and LIVE):
  the chart angle depends on zoom, so the fast SMA's slope is read in % per candle over `slope_lookback` (2) closed
  candles, signed with the trade's signal side. `StrategyEngine._watch_slope_exit`, once per candle closed after the
  entry (`pos.slope_bar`, steepest slope in `pos.slope_state`, memory only), closes the trade via `_exit_now` (a LIVE
  stop is cancelled first; a LIVE position restored without its stop id is left to that stop) as `SMA9_BEND` once the
  slope has dropped `slope_bend_pct` (60, 0 = off) % from its steepest since entry (only after it reached
  `slope_turn_pct`), or as `SMA9_TURN` (`slope_turn`, on) once it is at least `slope_turn_pct` (0.02) %/candle against
  the trade. **V-turn** (`vturn_exit`, off, its own tick box, independent of `slope_exit`; `slope_exit.judge_vturn`):
  closes as `SMA9_VTURN` once the slope is at least `vturn_turn_pct` (0.015) %/candle against the trade within
  `vturn_within` (6, max 10) candles of having been at least `vturn_move_pct` (0.02) %/candle with it (signed slopes since
  the entry in `pos.slope_state["hist"]`): a sharp V, never a slow U or a drift. **V-turn hand-off** (`vturn_handoff`, off,
  needs the V-turn exit; `slope_exit.vturn_handoff_on` / `lost`): a V-turn exit at a loss (before charges) moves the stock to Bot 2
  like a recovery (`StrategyEngine._hand_to_chop_bot` → `recovery.handoff`: flipped, gap mode; LIVE only once Groww shows no
  shares; a PAPER source never arms a LIVE Bot 2; replays drop the stock from the rest of the run); a winner stays. Same `slope_lookback`; the chart's slope strip
  shows while either is on (`slope_exit.watched`). **Slope candle** (`slope_minutes`, 0 = the bot's own candle, 1/2/3/5/10/15; per bot and per
  stock): `_watch_slope_exit` reads the slope, bend, turn and V-turn on closed candles of that size grouped from the 1-minute
  tape (`_tapes`, `exit_candle.closed_frame`), once per newly closed one, so a V shows sooner under a long bot candle; entries
  stay on the bot's candle, `slope_lookback` / `vturn_within` count these candles and the `turn_exit_candle` colour hold reads
  the last one. The research baseline keeps 0. Both count as an exit for `strategyChecks.noExitOn`. Settings →
  Supertrend / exits. The research baseline keeps both off.
- `backend/decel_guard.py` – SMA 9 deceleration guard (`decel_exit`, off by default, per bot and per stock, PAPER and LIVE,
  replays too): exit on the bend of the fast SMA before its slope turns. Slope in **ATR per candle** (`slopes`: (SMA9 −
  SMA9 `decel_lookback` (2) candles ago) ÷ (k × Wilder ATR 14), so one set of numbers fits any price and session) and its
  change `dslope`, both signed with the trade's signal side. `peak` = the steepest signed slope, counted from the first
  candle closed after the entry (`decel_peak_from` = `ENTRY`, default) or from the start of the current run (`RUN`:
  `run_start`, walking back from the entry while the signed slope stays > 0, inside the entry's IST session), so a trade
  entered late in a run can be warned on its first candle; flags are only raised on candles closed after the entry; `early_warning` (vectorised, closed candles only, backward windows only) flags a candle when the
  peak reached `decel_arm_atr` (0.10), the slope is ≤ peak × (1 − `decel_drop_pct` (50) / 100), the last `decel_candles` (2)
  dslopes are each ≤ −`decel_min_atr` (0.02), and with `decel_confirm` = `CLOSE` (default; `OFF`) the close is through the SMA 9
  against the trade by ≥ `decel_pierce_atr` (0) × ATR. `StrategyEngine._watch_slope_exit` (same slope candle as the other SMA
  9 exits, `slope_minutes`; same colour hold; `slope_exit.watched` includes it) calls `judge` on the closed frame and closes
  the trade as `SMA9_DECEL` via `_exit_now` (a LIVE stop is cancelled first; a LIVE position restored without its stop id is
  left to that stop). The V-turn / bend / turn exits are unchanged and can run next to it. Settings → 3. When to exit. The
  stock research baseline keeps it off.
- `backend/turn_entry.py` – SMA 9 turn alert and entry (`turn_alert` / `turn_entry`, off by default, per bot and per stock,
  PAPER and LIVE): act on the fast SMA's V before the 9/21 cross. Read once per closed candle of each armed stock of a
  RUNNING bot on its own candles (`StrategyEngine._turn_now`, called in `on_minute`), with the V-turn exit's numbers
  (`slope_lookback`, `vturn_move_pct`, `vturn_turn_pct`, `vturn_within`; `turn_entry.detect`): a turn is the candle where the
  slope first reaches the turn-back level one way after the steep-run level the other way within the window (rising then
  falling = SHORT, falling then rising = LONG); a slow rounding or drift never counts. The first candle read for a stock only
  sets the mark (`_turn_seen`), so a turn from before arming or a restart is ignored. **Alert**: one Telegram per turn
  (`turn_entry.alert_text`, `_alert`; replays and research send none). **Entry**: `_turn_minute` (in the `on_minute` chain
  after gap mode, when no cross is on that candle; a stale pre-arm cross does not block it) asks `apply_signal(...,
  alert=False, why="SMA9_TURN")` once per turn (`_turn_done`): every check, the stop, flip, cut-off, caps and one LIVE bot
  per stock apply; an opposite trade is closed and reversed like an opposite cross (held when the cross exit is off); the
  same side already held is left. Gap mode and candle patterns / colour take no turn entries. **Rounded U** (`uturn_on`,
  off; `turn_entry._detect_u`): also fires on the first candle whose slope is at least `uturn_min_pct` (0.005) the new way
  after `uturn_run` (5, 2-30) candles at least that the other way (up to `U_MAX_FLAT` 10 near-flat candles of rounding in
  between); a V is read first, and a turn the same way within `SAME_TURN_CANDLES` (6) candles of the last one is the same
  turn (`_turn_last`: one alert, one order). The note says `V:` or `U:`. **Only toward the cross** (`turn_toward_cross`, off;
  `turn_entry.toward_cross`): a turn opens a trade only when it points toward the 9/21 cross (down while SMA fast > slow, up
  while below); a turn the other way opens nothing and, against an open trade, only closes it (`_turn_act` → `_exit_now`, no
  reverse). **Close on a candle of the other colour** (`turn_exit_candle`, off; `turn_entry.candle_against`): a turn against an
  open trade read on a candle of the trade's own colour waits in `StrategyEngine._turn_wait`; `_turn_wait_minute` closes it (and
  reverses when allowed) on the first later closed candle of the other colour, and drops the wait when the SMA 9 slope swings
  back with the trade by `vturn_turn_pct` or the trade closes; the same switch holds the SMA 9 slope / V-turn exits
  (`_watch_slope_exit`) on a candle of the trade's own colour (judged again on the next candle). **Close a turn trade on the
  other colour** (`turn_colour_exit`, off; `StrategyEngine._watch_turn_colour`, `OpenPosition.entry_why` / `tc_bar`, restored
  from `TradeLog.entry_reason`): a trade whose entry reason starts `SMA9_TURN` closes (`COLOUR_TURN`, via `_exit_now`, no
  reverse) on the first candle of the other colour that opened with or after its entry (`TURN_COLOUR_GRACE_SEC` 60: a candle
  bought partway into is not counted). A trade a turn closes is saved as `SMA9_TURN_AGAINST` (also
  instead of recovery hold's `PROFITABLE_TREND_EXHAUSTION`); the alert says when it waits or points away. Settings → 1. When
  to enter (the V numbers are in 3. When to exit, V-turn). The stock research baseline keeps all of them off.
- `backend/opening_range.py` – opening-range checks (off by default, per bot and per stock, PAPER and LIVE,
  **entries only**): four tick boxes, all ticked must agree, read on the bot's closed candles of the session of
  the last closed candle (the window is always 09:15–09:30 IST). `or_volume` (`or_volume_ratio`, default 2): the
  window's per-minute volume at least that × the usual per-minute volume for the same window on earlier sessions
  in the frame (missing volume refuses); `or_vwap`: every window candle closed on the trade's side of
  `session_vwap_series`; `or_spread` (`or_spread_pct`, default 0.10): the SMA gap in % on the trade's side is at
  least that and wider than on the candle before; `or_break`: the first candle closed at or after 09:30 closes
  beyond the window high (buy) or low (sell). Nothing is checked before the window is complete (a candle at or
  after 09:30 has closed), so no entry in the window itself is taken with a check on. Wired through `_entry_block`
  (check names `or_volume` … `or_break`; chart ✕ "Opening …"). No exits. The stock research baseline keeps all off.
- `backend/tall_pullback.py` – tall-candle pullback entry (`tall_pullback`, off by default, per bot and per stock,
  PAPER and LIVE): when the closed signal candle's height (high − low) is ≥ `tall_atr_mult` (default 1.5) × its ATR,
  `apply_signal` does not order at once. `_apply_locked` arms a `PullbackOrder` instead (`_arm_pullback`, flat and
  reverse paths; the reverse closes the old trade first) at `max(SMA fast, low + ½ range)` for a buy or
  `min(SMA fast, high − ½ range)` for a sell. `StrategyEngine._watch_pullbacks` (every tick) sends the normal entry
  through `_open` on the first price at or beyond that level (every check, stop, cap, cut-off and one LIVE bot per stock
  apply; nothing rests on Groww's book, so nothing needs cancelling), and drops it once `tall_expiry_candles` (default 2)
  more candles have closed, the cut-off passes, a trade opens, the stock is unarmed or a new signal replaces it. A
  waiting pullback counts as entering the SMA run (`_side_entered`), so join-trend does not order it at once. A flipped
  stock orders at once. The stock research baseline keeps it off.
- `backend/extreme_entry.py` – candle-extreme limit entry (`extreme_entry`, off by default, per bot and per stock, PAPER
  and LIVE): every signal through `apply_signal` (cross, gap-mode entry, join, late / early-reversal entry; not candle
  patterns) waits as a `PullbackOrder` at the closed signal candle's favourable extreme on the side the real order goes (a
  flipped stock's buy signal sells): a buy at its low, a sell at its high, or with `extreme_anchor = "SMA_9"` at
  max(low, SMA fast) / min(high, SMA fast). `StrategyEngine._watch_pullbacks` (every tick) first drops it when the price
  breaks beyond the other side by more than `extreme_invalid_pct` (0.05) % (`SETUP_INVALIDATED_OPPOSITE_BREAK`), when
  `extreme_cancel_on_cross` (on) and a closed candle after the signal shows the SMAs crossed against the signal
  (`SETUP_INVALIDATED_SMA_CROSS`), or after `extreme_expiry_candles` (2) closed candles; else the first price at or beyond
  the limit sends the normal entry through `_open` (every check, stop, cap, cut-off and one LIVE bot per stock apply;
  nothing rests on Groww's book). Takes over from the tall-candle pullback; a breakout arrival's own level still wins.
  Settings → 1. When to enter. The stock research baseline keeps it off.
- `backend/entry_candle.py` – entry-candle colour check (`entry_candle_confirm`, off by default, per bot and per stock,
  PAPER and LIVE): the entry checks read closed candles, so a signal from a green candle can still fill on a candle that
  then closes red. With it on, every entry `apply_signal` would order at once (cross, gap-mode entry, join, late /
  early-reversal entry, re-entry; not candle patterns or candle colour) waits as a `PullbackOrder` (`confirm=True`) until the price is
  `entry_candle_pct` (0.05) % past the **forming** candle's open on the side the real order goes (above it for a buy,
  below for a sell; a flipped stock is judged on its real order). `StrategyEngine._watch_pullbacks` moves the level to
  each new forming candle's open, so the next candle can still fill; dropped after `entry_candle_max` (2) closed
  candles, when the SMAs cross against the signal, at the cut-off or trade cap. The tall-candle pullback, the
  candle-extreme limit and a breakout arrival take over from it. Entry reason suffix `:CANDLE_CONFIRM`. Settings → 1.
  When to enter (How the entry order is placed). The stock research baseline keeps it off.
- `backend/day_extreme.py` – day high/low guard (`day_extreme_guard`, off by default, per bot and per stock, PAPER and
  LIVE): in a strong trend the signal candle often closes at the day's high (buy) or low (short). With it on, an entry
  `apply_signal` would order at once (not candle patterns or candle colour) whose real side buys within `day_extreme_pct`
  (0.15) % of today's high (sells within that of today's low; `day_range` on the bot's closed candles of that IST session)
  waits as a `PullbackOrder` (`plan`) at the nearer of the fast SMA and `day_extreme_pull_pct` (0.15) % back from the
  close; `_watch_pullbacks` fills it on the first price at or past that level (every check applies), dropped after
  `day_extreme_candles` (3) closed candles, an SMA cross against it, the cut-off or trade cap: no pullback, no trade.
  Entry reason suffix `:PULLBACK`. The tall-candle pullback and candle-extreme limit take over from it; it takes over
  from the entry-candle check. Settings → 1. When to enter. The stock research baseline keeps it off.
- `backend/recovery.py` – structural exit, recovery hold and the trend-to-chop handoff (all off by default,
  per bot and per stock, PAPER and LIVE; meant for the trend bot, Bot 3). **Slow SMA exit** (`slow_sma_exit`):
  `StrategyEngine._watch_slow_sma` closes a trade on the first candle closed after the entry beyond the slow SMA
  against its signal side (`SLOW_SMA_BREACH`, via `_exit_now`); while on, the counter-candle exit is skipped, and a
  trade in recovery is left to the recovery rules. **Recovery hold** (`recovery_hold`, only where the SMA cross exit
  is on, `_apply_locked`; gap mode's `_close_on_cross` and candle patterns are not covered): on an opposite cross a
  trade in profit closes as `PROFITABLE_TREND_EXHAUSTION` and reverses as usual; one at a loss (or flat) is held
  (`_enter_recovery`: `OpenPosition.recovery_loss` per share and `recovery_bar`, saved on `TradeLog.recovery_loss` /
  `recovery_bar` and restored after a restart). `_watch_recovery`, once per closed candle, runs `recovery.judge`:
  loss within `recovery_tolerance_pct` (0.15) % of entry or the candle touched the slow SMA → `RECOVERY_TARGET_HIT`;
  loss ≥ `recovery_emergency_mult` (1.5) × the loss at the cross, ≥ `recovery_atr_mult` (2.5) × ATR, or
  `recovery_max_candles` (6) candles → `EMERGENCY_RECOVERY_ABORT` and the stock is blocked on that bot for the day
  (`_recovery_blocked`); still held at the entry cut-off → `SQUARE_OFF_CUTOFF`. A LIVE position restored without its
  stop id is left to that stop. **Handoff** (`recovery_handoff`, on by default once recovery hold is on): after a
  recovery target, on the live bots (`bots.register` sets `engine.handoff`) and in replays (`ReplaySession` sets
  `ReplayEngine._hand_off`, so the owner can check it after hours: practice money, so it never arms a LIVE Bot 2; the
  stock leaves the rest of the run, `ReplaySession.handed_off`; research never moves a stock), and in LIVE only once
  Groww shows no shares, `recovery.handoff` takes the stock off the source bot's Trade
  list and arms it on Bot 2 (`HANDOFF_BOT`) with its own settings (`HANDOFF_SETTINGS`: flipped, gap mode ±0.119 in /
  ±0.003 out with the slow-SMA fade confirmation, VWAP, RSI 40-70 / 30-60); Bot 2 waits for its next cross
  (`hold_for_next_cross`). A PAPER source never arms a LIVE Bot 2, and the one-LIVE-bot-per-stock rule applies (a
  refused handoff leaves the stock off both bots). **Bot 2 orders at once** (`handoff_now`, off by default, per bot and per stock, on
  the sending bot): after a recovery or a losing V-turn hand-off, `StrategyEngine._hand_to_chop_bot` calls `engine.handoff_order`
  (`recovery.order_now`, set by `bots.register` on the live bots only, so replays only move the stock), which runs Bot 2's
  `direct_order` as its own task the opposite way of the closed trade's real side (a losing short → Bot 2 buys; entry reason
  `HANDOFF`): no cross, gap mode, entry filter, ADX or flip is read; Bot 2 must be RUNNING, and market hours, its cut-off,
  trade cap, loss lock and one LIVE bot per stock (in `_open`) apply; the result goes to Bot 2's alert and signal line. The stock research baseline keeps them off.
- `backend/breakout.py` – trend breakout handoff, the reverse of the recovery handoff (`breakout_handoff`, off by
  default, per bot and per stock, PAPER and LIVE; meant for the chop bot, Bot 2; a hand-off does not tick it, so a
  moved stock follows Bot 2's own setting). On each newly closed candle of an armed stock (the bot's own candle; not the
  candle on the tape when the stock was armed), `StrategyEngine._watch_breakout` (before `on_minute`, in the armed loop)
  runs `breakout.read`: height ≥ `breakout_atr_mult` (1.8) × ATR and either |SMA fast − slow| / slow ≥ `breakout_gap_pct`
  (0.20) % or the candle's volume ≥ `breakout_volume_mult` (1.5) × the previous 20 candles' average (missing volume never
  counts). Then an open trade on the stock is cut (`BREAKOUT_STOP`, via `_exit_now`, which cancels a LIVE stop first; a
  LIVE position restored without its stop id is left to that stop) and, in LIVE only once Groww shows no shares,
  `engine.handoff(..., to_bot=breakout.TREND_BOT)` → `recovery.handoff(to_bot=3)` takes it off this bot's Trade list and
  arms it on Bot 3 with Bot 3's own settings. Never on Bot 3 itself. A PAPER source never arms a LIVE Bot 3, and the
  one-LIVE-bot-per-stock rule applies. **Bot 3 enters on the breakout's side at once**, no new cross needed: Bot 2
  sends `breakout.Arrival` (green candle buys, red sells; limit `tall_pullback.level` of the breakout candle: SMA fast
  or half-way, whichever is nearer the close) through `engine.breakout_arrival` (`breakout.arrive`, set by
  `bots.register` on live bots only) to Bot 3's `queue_breakout_entry`; on Bot 3's next pass `_enter_on_arrival` runs
  `apply_signal(..., alert=False, pullback_at=…)`, so every Bot 3 check (filters, ADX, cut-off, cap, one LIVE bot per
  stock) applies and the entry waits as a tall-candle `PullbackOrder` for Bot 3's `tall_expiry_candles` (2) candles,
  never a market order at the breakout's top (a flipped stock orders at once). An arrival older than
  `ARRIVAL_TTL_SEC` (15 min) is dropped. Replays move the stock (`ReplayEngine._hand_off`) but play one bot, so Bot 3's
  entry happens only on the live bots. The stock research baseline keeps it off.
- `backend/review.py` – 1-minute human review (`review_on`, off by default, per
  bot and per stock, PAPER and LIVE, replay and research too). The bot's own
  candle stays the strategy; nothing here trades. Once per newly closed candle
  of each held stock (`StrategyEngine._review_positions`, after every exit and
  cross of the tick), when the candle is 2 minutes or longer, an open trade whose
  signed SMA gap is within `review_gap_pct` and narrowing toward a cross (or,
  with the cross exit off, has just crossed inside the band) raises a review: a
  `ReviewLog` row (evidence: the candle's SMAs/gap and `one_minute_evidence` of
  the closed 1-minute tape kept in `_tapes`), a Telegram text, and the terminal's
  `ReviewCards` (EXIT / WAIT) in the sticky header. One review per uncertain
  stretch (ends at 1.5× the band), none within `review_cooldown_min` of the last
  on that trade. `answer_review`: EXIT marks the row first, then closes only
  that `trade_id` through `close_symbol(reason="USER_REVIEW_EXIT")`; WAIT only
  records; an answer after the trade closed sends nothing (`ALREADY_CLOSED`).
  The evidence carries a **1-minute trend verdict** (`review.verdict`, saved in
  `ReviewLog.one_min`, shown as a chip on the card and a line in the alert):
  five checks on the closed 1-minute tape (fast SMA slope, price vs the fast and
  slow SMA, price vs VWAP, net move of the last three closes), each with / against
  / flat for the trade's side; net score ≥ 3 is `WITH`, ≤ -3 `AGAINST`, else
  `MIXED`. The same verdict is read on an optional **check candle** (`review_check_minutes`
  1/2/3/5/10/15, built from the 1-minute tape by `review.check_candle_evidence`): under
  `review_check_mode = ONLY_IF_NOT_WITH` or `ONLY_IF_AGAINST` (default `ALWAYS`), the
  review is only raised when the check candle disagrees with the trade (an unreadable
  verdict always raises). Information only otherwise: nothing in the strategy reads it,
  and it never answers for the person.
  **Default answer** (`review_default_answer`, per bot and per stock): `PROMPT`
  (default, today's behaviour), `EXIT` (no card, no alert; the trade is closed at once
  with `AUTO_REVIEW_EXIT` through `close_symbol`, LIVE paths included) or `CONTINUE`
  (no card, no alert; the uncertain stretch is recorded as `AUTO_REVIEW_CONTINUE`).
  Switching a LIVE stock to `EXIT` asks for a confirm in the terminal. The stop,
  Bollinger exit, SMA cross exit, gap fade and square-off are untouched; they still
  guard the trade.
  **No answer never exits** under `PROMPT`: the row ends `NO_RESPONSE` when the trade closes or
  the stretch ends. A replay pauses at each review (`engine.on_review`); Play
  without answering = no answer. Routes: `POST /api/bot/review/{id}/{exit|wait}`
  (and the research, `/api/bots/{n}/bot/…` and `/api/replay/bot/…?bot=` forms).
  The stock research baseline keeps it off.
- `backend/chop_scan.py` – Chop/Trend scan (read-only; the Scalp page's "Trend quality"
  section): the same 1-minute Groww candles the Cross scan downloads are resampled to the
  user's chosen candle, enriched with SMA fast / slow, and today's **closed** SMA crosses
  are counted per stock. Each row carries `crosses_today`, `minutes_since_last_cross`,
  `avg_minutes_between`, `avg_move_pct` (abs close-to-close between consecutive crosses)
  and `activity` (volume, speed) from `cross_scan.activity`, and `chop_scan.score_for`
  labels the stock `TRENDING` (0-1 crosses, long run), `CHOPPY` (CHOP_MIN_CROSSES+ or
  average gap under CHOP_SHORT_GAP_MIN min) or `MIXED`. Information only: nothing in the
  strategy reads it. Lifecycle mirrors `CrossScanner` (manual Scan now, one pass at a
  time, `CROSS_SCAN_CONCURRENCY` downloads in parallel). `POST /api/chop-scan/start`
  (symbols, minutes, force) and `GET /api/chop-scan` on the SMA terminal.
  **Quiet runner alert**: when a stock had crosses earlier today and has stayed quiet
  for `quiet_min` minutes (default 60), its row shows a `🔔 Quiet Nm` chip and the page shows
  a dismissible popup (8 names, then "and N more"); one Telegram per scan through the quiet-runner
  channel (`alert_notifier.send_quiet`, else the trade one) (`quiet_runners_text` names the 15 longest-quiet and counts the rest), each stock at most
  once per IST day (`chop_scan._ALERTED_TODAY`, which keeps when each Telegram went). Each row carries
  `first_quiet_at` (the first market-hours scan today that read it as quiet, `_FIRST_QUIET`) and
  `alerted_at` (shown in the card's "First seen" tooltip, 📨 when the Telegram went). The card's **First seen** column
  (`components/Scalp/useFirstSeen.ts`, browser only) is the earliest of today's saved Trend quality scans (same
  candle size, up to the scan on screen) in which the stock passed the card's current filters (all but the stock
  search), so it changes with Show / crosses / price / speed; a stock no saved scan had dates to the scan on screen. A **price range** (`price_min` / `price_max` ₹ on
  `POST /api/chop-scan/start`, 0 = no bound; `chop_scan.in_price_range`) limits the alert to stocks
  priced inside it; the card's Price from / to boxes filter the table and pop-up the same way. The Scalp page's Trend quality card
  defaults to armed-stocks-only, so only the stocks on the Trade list are downloaded (quick enough to
  run quietly). **Auto re-scan** (`backend/chop_auto.py`, `ChopAuto`): the server itself starts the
  scan every `every_min` minutes (5/10/15/20/30/45/60, default 30) while NSE is open, no browser
  needed, with the card's last choices (candle, Armed only read fresh each time: the desk's link on
  the scanner, `armed_lists()` on the desk; quiet minutes, price range, Telegram). Settings in
  `chop_auto.json` next to the terminal's SQLite file (the Fly volume); `PUT /api/chop-scan/auto`,
  and `GET /api/chop-scan` carries `auto` (next_at, last error). On by default only on the scanner
  server; never runs on a desk with `SCANS=off` or while scans are paused. A manual Scan now counts as
  a run.
  **Strong trend** (`backend/trend_strength.py`, read by `chop_scan.analyze`): each row also
  gets `trend` = `STRONG_BULLISH` / `STRONG_BEARISH` / null, `trend_score` (0-100) and
  `trend_checks`, judged on the closed candles for the side of fast vs slow SMA: `stack` (close
  beyond fast beyond slow), `vwap` (close and slow SMA beyond today's 1-minute VWAP), `slope`
  (both SMAs moving with the trend over 3 candles), `fan` (|gap| ≥ 0.10% and not narrowed by a
  third), `hold` (≥ 75% of the last 12 closes beyond the fast SMA), `structure` (lower highs and
  lows, or higher), `run` (no cross in the last 12 candles). The first three are required and
  6 of 7 must pass. Plus `day_move_pct` (last close vs today's first open). The card's Show menu
  filters Strong trend / bearish / bullish, and "Fast movers" keeps |speed| ≥ N %/min.
  **Morning mode** (`trend_mode` = `MORNING`, before 10:00 IST; `trend_strength.read(..., now=, tape=)`):
  only today's candles since 09:15 are read. Seven checks: `stack`, `vwap`, `slope`, `fan` (≥ 0.10%),
  `hold` (all closes on the trend side up to 09:30 = 3 of 3, then ≥ 75%), `run` (a fresh cross today
  is allowed, yesterday never counts), `volume` (since 09:15 ≥ 1.5× the same minutes on earlier sessions
  in the 1-minute tape, up to 10 sessions, needs 2). Five or more of seven = `Strong` (`trend_days`,
  `rvol` and per-check `trend_notes` are sent). From 10:00 (`MIDDAY`) the 12-candle rules above apply
  unchanged. Information only: nothing in the strategy reads it.
  **Trend phases** (`backend/trend_phases.py`, read by `chop_scan.analyze`): today's closed candles split
  into `CHOP` / `BULL` / `BEAR` stretches (tuned on the owner's Bajaj Finserv 15 Oct example: 09:45-12:10
  choppy, 12:10-13:35 strong up, 13:35-14:40 strong down). Each candle is judged on its last `WINDOW` (6)
  candles: efficiency ratio |net| / sum |close moves| ≥ `MIN_EFFICIENCY` (0.5) and |net| ≥ `MIN_ATR_MOVE`
  (1) ATR; runs of ≥ `MIN_RUN` (5) such candles (gaps of `BRIDGE` 1 closed) become a phase, backdated to
  the window's turn; shorter bursts stay choppy. Rows carry `phase`, `phase_since_ts`, `phase_move_pct`,
  `phase_candles`, `phases` (all of today's) and `strong_phases`; the card's Phase column shows the phase
  now plus a 09:15-15:30 strip, and Show has "Phase now: strong / up / down / choppy". Information only.
  **Crosses today on** (`CROSS_RULES` in `ChopScan.tsx`: None, Exactly 1 / 1 ↑ / 1 ↓ / 2 / 3, At most 1-3,
  2 / 3 / 5 or more; multi-select 1/2/3/5/10/15 min): each row carries
  `crosses_by_minutes`, `side_by_minutes`, `last_cross_ts_by_minutes` and
  `last_cross_side_by_minutes`: today's closed-candle SMA crosses, the fast SMA's side, and the
  start and direction of the last cross on every candle size, read from the same 1-minute download
  (`chop_scan._crosses_on`; None while a size has too few candles). The card keeps only stocks whose count
  matches the chosen rule on every ticked size (a size with no count never matches; ↑ / ↓ also read
  `last_cross_side_by_minutes`) and shows a single cross as `@HH:MM ↑/↓`.
  **Trend on** (`TREND_DIRS` Bullish / Bearish / Same way, `TREND_KINDS` SMA side / Strong trend; multi-select
  1/2/3/5/10/15 min, kept in the browser): each row also carries `trend_by_minutes`, the strong-trend read
  (`trend_strength.read`, the same 7 checks as the card's own `trend`) on every candle size (`chop_scan._crosses_on`;
  None without a strong trend or with too few candles). The card keeps only stocks where every ticked size points the
  chosen way, by `side_by_minutes` (SMA side) or `trend_by_minutes` (Strong trend); a size with no read never matches.
  **Trend age, SMA gap and rank** (`backend/trend_rank.py`, `frontend/src/lib/trendRank.ts`, same arithmetic): each row
  also carries, per size, how long the current SMA side has run (`chop_scan._run_read`, across the download's days:
  `run_since_ts_by_minutes`, `run_candles_by_minutes`) and the signed SMA gap % now and its widest in that run
  (`gap_pct_by_minutes`, `gap_peak_pct_by_minutes`). With Trend on ticked, the card shows Rank, Trend age (market minutes
  all ticked sizes have pointed that way: the shortest `candles x size`, and since when) and SMA gap % (average |gap| over
  the sizes; ⚠ fading when a size's gap is under `FADE_SHARE` 0.5 of its widest), filters "Trend age at most", "SMA gap
  at least" and "Hide fading". Rank = average of the stock's place by age (youngest first) and by gap (widest first).
  **Trend rank alert** (`trend_rank.AlertSettings`, `trend_alerts.json` next to the SMA DB, off by default; `GET/PUT
  /api/chop-scan/trend-alerts`, the card's "Trend rank alert" group, "Use these filters" copies the Trend on sizes /
  direction / kind / age / gap / fading): after every finished Trend quality pass in market hours (`main._chop_done` →
  `trend_rank.after_scan`, manual or auto re-scan), the top `top_n` ranks inside the limits, each stock and side once
  per IST day (`_SENT`, memory), go out as one Telegram through the quiet-runner channel and / or a phone call
  (`place_call`: market hours, 5-minute cooldown). Information only: nothing in the strategy reads it.
- `backend/scan_arm.py` – **Scan & arm** (arming only, no order; desk, scanner and backtest server): saved recipes
  (`Preset`, `scan_arm.json` next to the SMA DB, `SCAN_ARM_FILE`, at most 20) that run one Trend quality pass on `lists`
  (F&O / Nifty 50 / 100 / Bank / armed, `main._auto_symbols`) through the shared `chop_scanner` (`main._scan_arm_scan`
  waits for a pass already running; `ChopScanner.passes` tells its own pass apart) and arm the best `top_n` of
  `trend_rank.ranked(rows, sizes, direction, kind)` (every ticked size the same way, `kind` SIDE = SMA side, STRONG = strong
  trend; optional `max_age_min`, `min_gap_pct`, `skip_fading`) on bot `bot` (1-4) with the source chip "Scan & arm: <name>".
  Already-armed stocks are left alone; nothing is unarmed. Arms go through `_set_trade_symbol` on the desk, or the desk link
  (`routes_link._to_desk` → `/api/link/arm`) on the scanner and on the backtest server with `SCANNER_LINK_TOKEN`
  (`main._scan_arm_remote`). **Schedule**: `times` (IST HH:MM, 09:15-15:30) with `on`, run by `Runner.start_loop` (every
  `CHECK_SEC` 20 s while scans are on and not paused) once per time per trading day (`fired`, saved); a time missed by more
  than `GRACE_MIN` (15) minutes is skipped; one run at a time; each scheduled run sends `scan_arm.text` through
  `alert_notifier.send`. **LIVE**: a scheduled or MCP run refuses a LIVE bot (`LiveRefused`, before scanning); the
  terminal's Run now asks the person and sends `confirm_live` (`POST /api/scan-arm/run`, `source` manual only). Routes:
  `GET /api/scan-arm`, `PUT /api/scan-arm/presets`, `DELETE /api/scan-arm/presets/{id}`, `POST /api/scan-arm/run`
  (`preset_id` or a one-off `preset`; runs in the background, poll GET). UI: terminal Stocks bar "Scan & arm"
  (`components/Terminal/ScanArm.tsx`: recipes, Run now, schedule switch, last runs, two templates: strong bullish/bearish
  at 10:00/10:15, and the same trend on 1m/5m/15m → Bot 3). MCP: `get_scan_arm`, `save_scan_arm`, `run_scan_arm` (PAPER
  bots only). The bot still trades on its next SMA cross; its "Join the morning trend" enters a running trend.
- `backend/morning_scan.py` – 09:30 morning checklist (read-only; the Scalp page's "09:30 Morning" tab,
  `components/Scalp/MorningScan.tsx`): at 09:30 a stock has only three 5-minute candles, too few for Trend
  quality, so five checks read just those (`read`), each for a long and a short: `orb` (last closed 1-minute
  close at/after 09:30 beyond the 09:15–09:30 high / low), `rvol` (Groww's running volume at the 09:29 candle
  ≥ `RVOL_MIN` 1.8× the same on up to `LOOKBACK_SESSIONS` 10 earlier sessions, needs `MIN_SESSIONS` 2), `vwap`
  (each candle closes beyond today's VWAP; the 09:20 / 09:25 candles also open beyond it and no 1-minute wick in
  them touches the VWAP line of that minute; the 09:15 candle on its close only), `gap` (SMA fast/slow gap on the
  last closed 5-minute candle ≥ +`GAP_MIN_PCT` 0.10% / ≤ −0.10%), `candles` (2 of 3 in the trend colour, the 09:25
  close beyond the 09:15 close and the fast SMA). The side with more passes is shown; `RUNNER_MIN` 4 of 5 = runner.
  Before 09:30 a row is `ready = False`. The first scan of the day downloads `LONG_DAYS` (15) of 1-minute candles per
  stock and keeps the earlier sessions' opening volume for the day (`_BASELINE`, in the scanner process), later
  scans `SHORT_DAYS` (4). `MorningScanner` mirrors `ChopScanner` (manual Scan now, one pass at a time, scanner
  process via `scan_worker.morning_job`, stopped by Pause scans). `POST /api/morning-scan/start` (symbols, force),
  `GET /api/morning-scan`. Arm / Arm ticked use the same routes as the other tabs. No order path; nothing in the
  strategy reads it.
- `backend/cross_scan.py` – Cross scan (read-only; the Scalp page's "Cross scan"
  section): F&O stocks whose SMA fast / slow are about to cross on the N-minute
  candle (1/2/3/5/10/15, default 5), soonest first, plus the ones that just
  crossed. Groww has no indicator feed, so each stock's 1-minute candles are
  downloaded (`candle_history.fetch_frame`, one at a time by default: `CROSS_SCAN_CONCURRENCY` 1-4; 4 days back so SMA 21 is
  formed at the open), grouped with `candles.resample` and averaged with
  `indicators.enrich` on the main desk's SMA periods: the same candles and
  arithmetic as the bot. Closed candles only (the forming one is dropped). The
  "in N min" estimate is the bot's heads-up slope arithmetic
  (`strategy_engine.minutes_until_cross`) × the candle length. A scan starts only when Scan now
  is pressed (opening the page just shows the last result). One pass at a time;
  the same closed candle is not scanned twice, a rescan waits a minute, and a pass
  that fails 5 times before any success stops and says why (e.g. no Groww login).
  `POST /api/cross-scan/start` (symbols, minutes, force) and `GET /api/cross-scan`
  on the SMA terminal; the stock list comes from `GET /api/scalp/universe` on the
  desk. Each row also carries the stock's activity from the same 1-minute candles
  (`cross_scan.activity`, no `enrich()`, so no volume warnings): shares traded today
  (Groww's running total), shares in the last 10 minutes, and the signed price speed
  in % per minute over those 10 minutes. No order path: Arm only opens the arm prompt.
  **Arm ticked** (`components/Scalp/BulkArm.tsx`, `TickArm.tsx`, `useArming().armSeveral`): the Monitor,
  Cross scan and Trend quality tables (and saved scans in the History tab) have tick boxes; Arm ticked picks one bot (or Research) and one set of
  qty / stop loss / trail / target (filled from that bot's settings), saves them as each stock's own trailing
  stop and arms each one through the same routes as the single Arm (desk link on the scanner). A LIVE bot asks
  once more first, and a stock another LIVE bot trades is skipped. On a PAPER bot (desk only) "Backtest these"
  then replays the armed stocks on that bot (`/api/replay/start` with `symbols` and `bot`). No order is placed.
  The Cross scan has the same **First seen** column as Trend quality (`useFirstSeen(..., "cross")`: the
  earliest of today's saved Cross scans on that candle size in which the stock passed the card's filters).
  Every Scalp table's stock name (Monitor, Cross scan, Trend quality, Most active, History) is a
  `GrowwLink` (`components/ui/growwLink.tsx`) to desk `GET /api/instruments/groww/{symbol}`, which
  302s to the stock's groww.in page: the slug comes from Groww's public website search
  (`app/services/groww_links.py`, exact NSE match, cached per symbol; else Groww's search page).
  Opens in a new tab with the browser's own Groww login. Read-only, no trading API, no order.
- **Scan lists** (`components/Scalp/ScanLists.tsx`, `ScanListPicker`): the Cross scan, 09:30 Morning and Trend quality
  cards each pick their stocks with tick boxes, joined without repeats: Armed stocks, All F&O, Nifty 50, Nifty 100,
  Nifty Bank (kept per card in the browser; Trend quality defaults to armed, the others to F&O). Index members come
  from NSE's constituent CSVs (`app/services/index_lists.py`, archives.nseindia.com, 12 h in memory plus a saved copy
  next to the desk DB for when NSE refuses). Desk `GET /api/scalp/universe?lists=fno,nifty50,…` (default `fno`; per-list
  `counts` / `errors`). The Trend quality auto re-scan keeps the card's `lists` (`chop_auto.AutoSettings.lists`,
  `main._auto_symbols`); MCP `start_scan` takes `stocks` nifty50 / nifty100 / niftybank. Read-only.
- `backend/scan_history.py` – scan history (read-only; the Scalp page's History tab): every
  finished Cross scan / Trend quality pass (`on_done` hook on both scanners, saved off the loop
  by `main._keep_scan`) is kept with its time, choices (`meta`: SMA periods, quiet minutes,
  price range, stocks, `source` manual/auto) and every row as gzip JSON in its own SQLite file
  next to the SMA DB (`scan_history.db`, `SCAN_HISTORY_FILE`), `SCAN_HISTORY_DAYS` (default 90)
  days. `GET /api/scan-history[?day=&kind=]`, `GET /api/scan-history/{id}`. The page shows any
  past run's table and downloads it as CSV or PDF. **Accuracy check** (`backend/scan_accuracy.py`,
  `POST /api/scan-history/{id}/check`, once the scan's day has closed): each stock's 1-minute
  candles for that day are downloaded again (`scan_worker.check_job`) and `evaluate` reads, with
  the scan's call (Cross scan: its side; Trend quality: the fast SMA's side on the scan's candle),
  the move 15 / 30 / 60 minutes later and at the close, the best and worst move, and the next SMA
  cross (crossed as predicted and within double the estimate; how long the trend held).
  **Most active history**: the desk's Most active scan (`app/services/active_stocks.py`) saves every finished pass
  (`ActiveScanner.on_done` → `routes_scalp.keep_active_scan`, set in the desk lifespan) into the same file as kind
  `active`; the terminal's History tab never lists it (`SCAN_KINDS`). Desk `GET /api/scalp/active/history[?day=]`,
  `GET /api/scalp/active/history/{id}`; the card's Live / History switch (day, scan time, ‹ ›, CSV; Rank by, Bias, filters
  and column sorting work on the saved scan). Each row also has `speed_pct_min` (`add_speed`: % per minute since the
  previous scan, signed; none on the day's first scan or after a gap over `SPEED_MAX_GAP_MIN` 30 min), a Speed column,
  "Min speed" filter and Rank by "Fastest move now" / "Furthest from average price". Read-only.
  `summarize` gives hit rates per group (all, quiet runners, strong trend, trending; about to
  cross / just crossed); saved with the run. On the desk it is refused in market hours (the
  bots' Groww limit) and with `SCANS=off`. No order path; nothing in the strategy reads it.
- `backend/open_pnl_log.py` – open P&L log (read-only): once a minute while NSE is open
  (`OpenPnlRecorder`, started in `boot_engine`), each desk's "Open" figure from the terminal's
  Stocks bar (bot 1, bots 2-4, research; `sample` uses `engine._open_pnl`, the screen's own
  arithmetic) is saved with its mode and every held stock (side, qty, entry, last price, open P&L)
  in its own SQLite file next to the SMA DB (`open_pnl.db`, `OPEN_PNL_FILE`, `OPEN_PNL_DAYS`
  default 90). Each point also keeps today's **closed** P&L of that desk's current book at the minute
  (`closed_gross` / `closed_net`, from the engine's `realized_gross` / `realized_net`, refreshed after every close by
  `_refresh_realized`; no extra DB read), and the API adds `total_gross` / `total_net` = closed + open: the log, its
  chart (blue = day total, dashed = open only), high / low / latest (`summary`, on `total_gross`) and CSV show the day's
  P&L, not just the open trades. Files from before get the two columns added (older rows read 0 closed). `GET /api/open-pnl?desk=1|2|3|4|research&day=` gives a day's points plus its high /
  low / latest. **Replays too**: each `ReplaySession` day gets an `open_pnl_log.ReplayRecorder`
  (run id, bot) that keeps the first tick of each replayed minute after `eng.tick` (`_note_pnl`) and
  saves in batches off the loop (`flush` every 30 points, at the day's end and on Stop) into
  `replay_pnl` (keyed by run and replayed minute; `delete_run` drops it). `GET /api/open-pnl/replays?bot=`
  lists a bot's runs, `GET /api/open-pnl/replay/{run_id}?day=` gives one replayed day.
  UI: the Stocks bar's "P&L log" button (`OpenPnlLog`: Market days / Replays switch, run and day
  pickers, chart with hover, table, CSV; opens on Replays while the desk shows a replay).
  **Per stock** (`open_pnl_log.by_stock`, `by_stock` on both routes): each traded stock's P&L at every logged minute
  (its trades closed by that minute, `gross_pnl` from the trade book, plus its open P&L in the point). The log's Stocks
  chips pick one or more: one line each plus their total, and the summary, table and CSV follow the pick.
  No order path; nothing in the strategy reads it.
- `backend/fast_move.py` – fast-move alerts (off by default; alerts only, never trades; `fast_moves.json` next to the
  SMA DB, `FAST_MOVE_FILE`): **armed and held** (`on`, desk only, `fast_move.watcher` started in `boot_engine`): every
  `POLL_SEC` (5 s) while NSE is open, the bots' own last prices (`main._fast_move_prices`, no extra Groww call) of every
  armed / held stock on Bots 1-4; a move of at least `pct` (0.5) % within `minutes` (3) alerts. **Scan** (`scan_on`):
  after each Trend quality pass in market hours (`main._chop_done` → `_fast_move_scan`), rows whose 10-minute speed is at
  least `scan_speed` (0.10) %/min either way, one message. Each stock once per `cooldown_min` (15); Telegram through
  `alert_notifier.send`, a call too with `call` (`place_call`: market hours, 5-minute cooldown). `GET/PUT /api/fast-moves`;
  Settings → "Fast-move alerts" (`components/Settings/FastMoveAlerts.tsx`).
- `backend/scan_worker.py` – scanner process: each stock's read for the Cross scan and the
  Trend quality scan (download + `scan_one` / `chop_scan.analyze`) runs in a separate process
  (one `spawn` worker at `nice` 10, started on the first scan, stopped in `stop_engine`), so the
  bots' event loop never crunches scan candles. The scanners take a `job` callable for this
  (`main._scan_job`); progress, caching and alerts stay in the main process. The worker paces
  Groww (one candle request per `MIN_GAP_SEC`) and retries a "rate limit" answer after 2, 4 and
  8 s; Groww's limit is per account, so this also spares the bots' own requests. The desk's
  token goes to the worker over the process pipe (never logged); a rejected session switches to
  the saved token like `fetch_frame`; a crashed worker restarts on the next stock.
  `SCAN_WORKER=off` reads in-process as before. Read-only, no order path.
  **Workers** (`scan_worker.workers()` = `CROSS_SCAN_CONCURRENCY`, 1 by default, 3 on the scanner server in
  `fly.scanner.toml`): that many worker processes; each spaces its own Groww calls `MIN_GAP_SEC` × workers, so all of
  them together keep the one-request-per-`MIN_GAP_SEC` pace while Groww's answer for one stock overlaps the next.
  **Earlier days kept** (`run_cached`, Cross scan and Trend quality): `cross_job` / `chop_job` return (row, the
  stock's candles before today); the main process keeps them in `_PAST` (memory only, keyed by stock and window start,
  cleared when the day changes) and hands them back, so later scans that day download only today
  (`_frame_with_past`). `chop_scan._crosses` counts crosses on whole columns (same result as the old row loop).
  **Pause all scans** (top of the Scalp page; memory only, a restart resumes): the page sets
  both switches. Terminal `PUT /api/scans/pause` refuses Cross / Trend quality starts (409)
  and stops a running pass (`scanner.stop`, the last rows stay); desk `POST /api/scalp/pause`
  (`active_scanner.paused`) stops the monitor's scoring and Groww history fill, Scalp alerts
  and the most-active scan (background and manual). The page stops its 10 s monitor poll,
  the Scan buttons and the 30-minute auto-rescan while paused.
- `backend/groww_pressure.py` – bots first on Groww (one account, one rate limit). Every
  blocking Groww SDK call in `groww_client.GrowwClient` (bots 1-4: quotes, candles, orders,
  stops) runs on `BOT_POOL`, a thread pool only the bots use (`run_bot_call`,
  `BOT_GROWW_THREADS`, default 12), so scan downloads on asyncio's default pool never queue
  ahead of a bot's call. A bot call refused for the rate limit (`note_bot_rate_limit`) makes
  the scans wait (`wait_for_bots`, until no refusal for `HOLD_SEC` 30 s, at most 120 s a step):
  `scan_worker.run` (Cross scan, Trend quality), the desk's most-active scan and the Scalp
  monitor's history fill. `/api/stall` reports it under `groww` and keeps the blocking frames
  of each stall (`recent[].where`). Bookkeeping only: no order is placed, changed or held back.
  The desk's live price feed (`app/services/market_data.LiveGrowwFeed`, Dashboard "Live", also on
  the scanner) asks bid/ask for `depth_per_poll` (1) symbol per poll in turn, never the whole
  universe in one burst; a "rate limit" answer pauses its calls `RATE_PAUSE_SEC` (20 s), depth
  `DEPTH_PAUSE_SEC` (120 s), shows "Groww rate limit: …", and counts as `feed_rate_limits`
  (`note_feed_rate_limit`), which makes the scans wait like a bot's refusal.
- `backend/server_role.py` – scanner server (`PALTRA_ROLE=scanner`, second Fly app
  `paltra-scanner`, `fly.scanner.toml`, setup in `SCANNER.md`): the same image runs the Scalp
  page's scans and alerts on its own machine and its own Groww API key. It **never orders**:
  `refuse_orders` in both Groww clients (`_live_order`, `modify_sl`, `cancel_order`; desk
  `place_order`, `cancel_order`, `square_off_all`), and 403 on the SMA `/api/mode` /
  `/api/bots/{n}/mode` LIVE switch and the manual desk's Groww execution. Its Arm arms the desk's
  bots over the link (`app/api/routes_link.py`, token `SCANNER_LINK_TOKEN`, exempt from the Google
  lock via `token_path`): `useArming` shows the desk's bots, `ArmPrompt` reads / saves the stock's qty
  and trailing stop through scanner `/api/link/desk-stock/{bot}/{symbol}` → desk
  `/api/link/stock/…`, then scanner `/api/link/desk-arm` → desk `POST /api/link/arm` calls the
  terminal's own trade-symbols route in-process. PAPER and LIVE: a change on a LIVE bot needs
  `confirm_live` (the page asks the same LIVE question as the desk first; without it 403), and the
  terminal's checks (one LIVE bot per stock) still apply. Only qty / TSL numbers can change, never the
  mode; no order is placed. Armed chips and "Armed only" come from desk `GET /api/link/armed` (scanner
  `GET /api/link/desk-armed`). No
  shutdown call from the scanner. `GET /api/role` tells the page which server it is on.
  `SCANS=off` on the desk (default on) keeps its Scalp scans paused for good: the most-active
  background scan does not start, and resuming scans answers 409. CI deploys the scanner only when
  the repo variable `DEPLOY_SCANNER` is `true` (secret `FLY_SCANNER_API_TOKEN`), after the desk's deploy job
  (the two shared the org's remote builder and the scanner's deploy hung after pushing layers), even if that
  one failed; each `flyctl deploy` attempt is stopped after 8 minutes and retried once (job limit 25 min).
- `backend/backtest_server.py` – backtest server (`PALTRA_ROLE=backtest`, third Fly app `paltra-backtest`,
  `fly.backtest.toml`, setup in `BACKTEST.md`): replays only, on a performance machine that starts on the first visit
  (`auto_start_machines`) and stops itself when idle (`run`: no replay `LOADING` / `PLAYING` and no page request,
  `note_request` via the desk app's middleware, for `idle_minutes()`, default 30, 10-240, `backtest_server.json` next to
  the SMA DB → SIGTERM → exit → Fly stops the machine; `[[restart]] on-failure`). **Never orders**
  (`server_role.no_orders` covers scanner and backtest: `refuse_orders`, the LIVE switch 403, the desk broker and manual
  desk) and runs no live bots, alerts or ORB feed (`main._boot_backtest_server`; `_start_bot` answers 409). **Scalp scans
  on demand** (`server_role.scans_enabled` is true here; no background most-active loop; the Trend quality auto re-scan,
  off by default, runs here too once turned on (`chop_autos.start_loop` in `_boot_backtest_server`), and while it or a
  scheduled Scan & arm preset is on the machine stays awake in market hours (`main._auto_scans_due` busy check)): they download with **this server's own Groww login** (`scan_session.OwnSession`, the token saved by its own
  Settings → Broker keys in its own trading.db via `groww_client._read_desk_token`; `main._scan_broker`), never the desk's
  session the replays borrow, so a scan never shares the live bots' rate limit; without a login a scan says to log in here.
  Three scan workers (`CROSS_SCAN_CONCURRENCY` in `fly.backtest.toml`), and a running Cross / Trend quality / 09:30 pass keeps
  the machine awake like a playing replay (`backtest_server.add_busy_check`). The accuracy check runs here in market hours too
  (only the desk refuses it). Its key must not be the scanner server's: logging one key in on two servers can end the other's
  session. Copies
  every `BotConfig` row (1-5) from the desk (`main.export_bot_settings` → desk `GET /api/link/backtest/settings` →
  `main.import_bot_settings`, PAPER forced) at boot and on "Sync from desk" (refused while a replay plays); never writes
  back. **Arms from the scanner**: with `SCANNER_LINK_TOKEN` on this server and `BACKTEST_URL` on the scanner, the scanner's
  Arm / Arm ticked has a Main desk / Backtest server switch (`useArming`, `ArmServerSwitch`; scanner link routes take
  `?server=backtest`, longer timeout since the call may start the machine). The desk-side link routes answer here too
  (bots are PAPER); an arm waits for the start-up copy (`backtest_server.wait_first_sync`) and is remembered
  (`note_scanner_arm`, `backtest_server.json`), so the start-up copy keeps it, with its own source and per-stock settings,
  on top of the desk's Trade list while it is still armed here (`import_bot_settings(keep=)`); Sync from desk replaces
  them (`clear_scanner_armed`). Past candles use the **desk's Groww session**: desk `GET /api/link/backtest/groww` every 10 min, memory only
  (`remote_token`, read by `groww_client.desk_session_token` on this role); it never logs in to Groww itself, so it
  cannot end the desk's session. Both link routes need `BACKTEST_LINK_TOKEN` (not the scanner's token), desk role only.
  **Market hours only** (`backtest_server.market_hours_only` / `stop_at`, off by default, saved in `backtest_server.json`;
  `PUT /api/backtest/hours`, the bar's "Market hours only, stop after"): outside 09:00 to `stop_at` (15:45) IST Mon-Fri
  (`after_hours`) page requests no longer keep it up; it stops once no backtest or scan has run for `AFTER_HOURS_MIN` (5)
  minutes (`idle_seconds_left`). **Scans copied to the desk**: every finished Cross scan / Trend quality / Most active pass
  and each accuracy check made here is also sent to desk `POST /api/link/backtest/scan-history` (`push_scan` / `push_check`
  from `main._save_scan` / `_save_check` and `routes_scalp._save_active`, in a worker thread, failures only logged), kept
  once in the desk's `scan_history.db` (`save_copy`; a check is matched by kind and time, `find`), so the desk's Scalp
  History tab and Most active History show them while this server is stopped.
  Terminal `GET /api/backtest/status`, `POST /api/backtest/sync|stop[?force=true]`, `PUT /api/backtest/idle`; UI
  `components/Terminal/BacktestServerBar.tsx` (on the desk: a link when `BACKTEST_URL` is set). **Arms the desk from its Scalp
  page**: with `SCANNER_LINK_TOKEN` set here too (`/api/role` `desk_arm`), the Arm / Arm ticked dialogs have a Main desk /
  This backtest server switch (`useArming`, opens on this server's own PAPER bots): Main desk goes over the same link as
  the scanner (`routes_link._link_caller`: the backtest server reaches the desk only, never `server=backtest`), so the
  desk's checks apply and a LIVE bot is confirmed first. CI deploys it only when
  the repo variable `DEPLOY_BACKTEST` is `true` (secret `FLY_BACKTEST_API_TOKEN`), after the desk and the scanner.
- `backend/auto_start.py` – auto-start for PAPER bots (`auto_start`, off by default, per bot 1-4, not per stock; Settings →
  6. Size, daily limits and times; in `COPY_SKIP`). The bots boot STOPPED after a deploy or restart and a finished day rolls
  back to STOPPED; with it on, `run` (started in `boot_engine` on the desk only, not with `server_role.no_orders()`) presses
  Start every `POLL_SEC` (15 s) while NSE is open for a bot that is STOPPED and in PAPER, through the same `_start_bot` as
  the button (a cross already on the tape is not traded; all-bots guard halts apply). Never a LIVE bot, a PAUSED one, or a
  HALTED / DAY_COMPLETED one (loss limit, trade cap, panic, square-off). No order is placed by it.
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
  refreshes every 55 s; a stock whose history download failed or came back short is asked again
  once a minute, `_history_tried`, so it is never left on a tape of its own prices) and records real Groww prices (`price_ticks`, one row
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
- `backend/all_bots_guard.py` – all-bots day target / stop loss (off by default: `all_bots_target` /
  `all_bots_stop` ₹ on `BotConfig` row 1, 0 = off; `GET/PUT /api/bots/guard`, Settings → "All bots: day target and
  stop loss", `components/Settings/AllBotsGuard.tsx`). While NSE is open the watcher (`run`, started in `boot_engine`,
  every `POLL_SEC` 5 s) adds today's P&L of bots 1-4 (`main._all_bots`; not research or replays), each in its own
  current book: closed gross (`_kpis`, read in a thread) plus open gross (`_open_pnl`, on the loop), before charges like
  the screens. At ≥ target or ≤ −stop every bot gets `StrategyEngine.kill` (the same panic as "Panic all bots": LIVE
  stop cancelled, positions squared off, HALTED, alert/call) with a reason starting `HALT_PREFIX`. `_start_bot` refuses
  such a bot for the rest of the day unless the saved limits no longer hit; the next session's roll clears it.
  A third setting, `all_bots_squareoff` ("HH:MM" IST, 09:15-15:30, empty = off; `parse_time`, `squareoff_at`), trips
  the same panic once that time of day is reached (`verdict(..., now, at)` → `"time"`), whatever the P&L; each bot's
  own `square_off_time` still applies. **Backtests too**: each `ReplayEngine` day (`day_guard`, off for the
  `sma_research` baseline) checks once per replayed minute (`_check_day_guard`) its own day's P&L (closed + open, before
  charges) against the target / stop and the replay clock against the square-off time, and on a hit `trip`s itself
  (squared off as `KILL_SWITCH`, HALTED for the rest of that replayed day; the next day starts fresh). A replay plays
  one bot, so it is judged on that bot's P&L alone.
- `backend/pnl_alerts.py` – all-bots P&L alerts (off by default; alerts only, never trades): `pnl_alert_profit` /
  `pnl_alert_loss` ₹ on `BotConfig` row 1 (0 = off), `pnl_alert_telegram` / `pnl_alert_call` (both on by default) and
  `pnl_alert_basis` (`GROSS` before charges, as the screens; `NET` after). While NSE is open `run` (started in
  `boot_engine`, every `POLL_SEC` 5 s) adds bots 1-4's P&L like the all-bots guard (`all_bots_guard.closed_by_bot` /
  `open_by_bot`, `net=` for the basis) and, once per level per IST day (`PnlAlerts.sent`, memory only), sends the text
  through `alert_notifier.send` (trade channel) and / or rings through `place_call` (market hours, 5-minute cooldown).
  `GET/PUT /api/bots/pnl-alerts`, `POST /api/bots/pnl-alerts/test` (test message + call at any time); Settings →
  "All bots: P&L alerts" (`components/Settings/PnlAlerts.tsx`). Never copied by Copy settings (`COPY_SKIP`).
- `backend/news_entry.py` – news entries (off by default; PAPER and LIVE): the bot picked in `news_entry_bot` (1-4 on
  `BotConfig` row 1, 0 = off) enters at once on a strong, fresh headline: positive buys, negative shorts, NEUTRAL or a headline
  mainly about another company (`about: False`) never. `news_entry_impact` LOW / MEDIUM / HIGH is the weakest that trades (critical
  counts as HIGH), `news_entry_max_age_min` (15) the oldest, `news_entry_max_day` (5) news entries per bot per IST day,
  `news_entry_scope` `FNO` (any F&O stock: put on the bot's Trade list with the source chip "News", `arm`; a stock another LIVE bot
  owns is refused) or `ARMED`. The desk's `NewsWatcher` hands every fresh headline (`_entries`: per-stock reads in `run_once`, and
  NSE's market-wide filings in `_market_pass`) to terminal `main.news_entry_consider` → `news_entry.consider`; while a news bot is
  set it also reads NSE's filings every `ENTRY_POLL_SEC` (60 s) from 09:15 to 15:30 (`entries_active`, even with News alerts off).
  The order goes through `StrategyEngine.news_order` → `direct_order` (entry reason `NEWS`): no SMA cross, candle pattern, entry filter, ADX,
  pullback or candle wait is read and the flip setting is not applied; only a RUNNING bot, market hours, the cut-off, the Trade
  list, the halt / loss lock, the trade cap and (in `_open`) one LIVE bot per stock apply. An opposite position is closed and
  reversed like an opposite cross (held when the SMA cross exit is off). Then the bot's own stop / TSL / target and exits run the
  trade. Each headline is acted on once (`_taken`, memory); `recent` keeps the last 50 decisions. `GET/PUT /api/bots/news-entry`
  (a LIVE bot needs `confirm_live`, 409 without), Settings → "News entries" (`components/Settings/NewsEntries.tsx`, asks before a
  LIVE bot). Never copied by Copy settings (`COPY_SKIP`). Replays and research never see news.
- `backend/strategy_presets.py` – saved strategies and a history of saves (`strategy_presets.json` next to the SMA DB,
  `STRATEGY_PRESETS_FILE`). **Presets**: a named copy of a desk's strategy fields (`strategy_only`: the `ConfigUpdate`
  fields minus `COPY_SKIP`, so never the Trade list, mode, name, per-stock or all-bots rows), at most 50, the same name
  on the same desk replaced in place. **History**: `main._put_config` keeps every Save's strategy fields per desk
  (`remember`, last 30, an unchanged save not repeated). `GET /api/config/presets?desk=1-5|research` (every preset and
  that desk's history, newest first), `POST /api/config/presets` (`name`, `desk`, `settings`), `DELETE
  /api/config/presets/{id}`. Settings → Strategy, "Saved strategies & history" (`components/Settings/StrategyPresets.tsx`,
  shared settings only): Save as…, each entry's fields that differ from the form now (old → new), Load (into the form;
  the usual Save then applies it with every check), Delete. No order is placed.
- Copy bot settings (`POST /api/bots/copy-settings` in `backend/main.py`: `from_desk` / `to_desk` "1"-"4" or
  "research", `stock_settings`, `confirm_live`): replaces the destination's strategy settings (every `BotConfig`
  column but `COPY_SKIP`: id, chart symbol, Trade list and sources, PAPER/LIVE, name, per-stock settings, the 1s feed,
  trade-count bookkeeping and the all-bots guard on row 1) with the source's, checked like Save; per-stock settings
  replace the destination's only when ticked. A LIVE destination needs `confirm_live` (409 without); an open trade
  blocks a qty change as on Save. Settings → SMA strategy, "Copy settings from" (`components/Settings/CopySettings.tsx`).
  No order is placed.
- Entry reason (`TradeLog.entry_reason`, display only): `apply_signal(..., why=)` → `_apply_locked` → `_open(why=)`
  saves why each trade opened (`strategy_engine.ENTRY_REASONS`: `SMA_CROSS`, `GAP_MODE`, `JOIN_TREND`, `LATE_ENTRY`,
  `EARLY_REVERSAL`, `COUNTER_REENTRY`, `CANDLE_PATTERN`, `BREAKOUT_ARRIVAL`, `FORCE_ORDER`, `NEWS`, `HANDOFF`, `SMA9_TURN`); a fill from a waiting
  `PullbackOrder` adds `:PULLBACK`, `:EXTREME_LIMIT` or `:CANDLE_CONFIRM`. NULL on older rows. Shown in the blotter's Entry reason column, the chart trade
  card and the CSVs (`frontend/src/lib/entryReason.ts`). Nothing in the strategy reads it.
- `backend/arm_log.py` – arm log (bookkeeping only): a SQLAlchemy `before_flush` hook writes an `ArmLog` row
  (`config_id` 1 / 2 research / 3-5, symbol, `ARM` / `UNARM`, source chip, PAPER/LIVE) for every change of a
  `BotConfig.trade_symbols` saved through the ORM (Settings, Arm switch, scanner link, copy settings, handoffs, chat /
  MCP); `baseline()` at boot adds an `ARMED` row for each armed stock whose last row is not an arm. `windows(config_id,
  day)` gives each stock's armed stretches that day. `GET /api/arm-log?desk=1-4|research&day=` (rows + stretches);
  terminal Stocks bar "Arm log" button (`components/Terminal/ArmLog.tsx`). **Replay as armed** (`ReplayStart.as_armed`,
  replay bar "As armed live", saved as `as_armed` in the run's settings so Resume / Run again keep it): the stocks the
  bot had armed on those days, from 09:15, each traded only inside its stretches (`ReplayEngine.arm_windows`,
  `_armed_now` feeds the Trade list); a stock armed mid-day waits for a fresh cross, as live. Still refused while the bot
  is LIVE, like every replay.
- `backend/indicators.py` – `enrich()` adds `sma_fast`/`sma_slow`
  (`sma_9`/`sma_21`), Wilder `atr_14`, `adx_14`; plus RSI and
  `entry_filter_reason` for the optional VWAP/volume/density/RSI checks.
- `backend/app/services/chat_actions.py` – chat assistant actions (the "Ask the bot" widget).
  The model may only *propose* changes, through two OpenAI function tools:
  `propose_trade_list_change` (arm / unarm stocks on an SMA bot 1-4) and
  `propose_stop_change` (stop on/off, type and ATR / TSL / SMA-gap numbers, all stocks or
  one). `chat_advisor.answer` checks each call against the terminal's own bounds and
  returns it as `proposals`; nothing is saved server-side. The widget shows each as a
  `ChatProposalCard` (old → new values, PAPER/LIVE badge) and saves only on Apply,
  through the same terminal routes as Settings; a LIVE bot needs a second "Confirm on
  LIVE" tap. There is no tool for orders, closing positions, the PAPER/LIVE switch,
  start/stop or panic.
- Alert channels (`AlertChannel.channel_type`, desk Settings → alert cards; `alert_notifier.CHANNEL_TYPES`):
  `trade` (fills, closes, refusals, hand-offs, warnings; `alert_notifier.send`), `critical` (bot down, app
  shutting down, no stop; `send_critical`), `news` (news alerts, `send_news`), `signal` (heads-ups that are
  not orders: a cross inside 3 minutes, an SMA 9 turn alert, a 1-minute review; `send_signal`,
  `StrategyEngine._alert(kind="signal")`), `fast` (fast price moves, `send_fast`), `quiet` (the Scalp page's
  quiet runners and trend-rank alerts, `send_quiet`; `main._chop_alert`) and `call` (provider `twilio_voice`:
  a Twilio phone call that reads the alert out). Every kind but `call` falls back to the trade channel
  when its own is not set (`send_to`). **Each kind has its own Telegram bot** (`DEDICATED_TELEGRAM`:
  providers `telegram_critical`, `telegram_news`, `telegram_signal`, `telegram_fast`, `telegram_quiet`, one
  `AlertChannel` row each, saved only in their own slot), so a second BotFather token never overwrites the
  trade bot (`telegram`), which with `twilio` / `callmebot` can still be assigned to trade or critical. The
  Settings cards: `components/Terminal/WhatsAppAlerts.tsx` (`OWN_BOTS`); `POST /api/scan/test-alert?channel_type=`
  sends through that slot only, never the fallback. The engine asks for a call
  (`_alert(critical=True, call=True)`) on HALTED / STOPPED and a position with no stop;
  never on DAY_COMPLETED or a `Manual…` panic. `place_call` rings only 09:00-15:45 IST
  Mon-Fri and at most once per 5 minutes (`CALL_COOLDOWN`); the Test call button ignores
  both. The app stopping (any deploy or restart) alerts and rings:
  `app.main` lifespan → `main.announce_shutdown_all` → `strategy_engine.announce_process_stop`
  → `alert_notifier.announce_shutdown` (one message listing every bot; the cooldown still
  applies). In market hours it always messages and rings; outside them each follows its switch
  (`AlertPrefs` row 1: `after_hours_stop_call`, `after_hours_stop_message`, both on by default;
  `GET/PUT /api/scan/alert-prefs`, the phone-call card in Settings). `fly.toml` `kill_timeout = '15s'` leaves time for it. Replay and research engines send nothing.
- `backend/app/services/stock_news.py` – news for one stock (read-only; Groww's trading API has no news): NSE's
  corporate announcements (`nse_announcements`, nseindia.com `api/corporate-announcements`, its home page first for the
  cookies) and Google News RSS headlines for the company's name from the instrument list (`google_news`, last 7 days),
  asked together, each source's error kept apart (`errors`), merged newest first, cached `CACHE_SEC` (5 min) per symbol.
  Desk `GET /api/instruments/news/{symbol}[?force=true]`; the chart's **News** button (`StockNewsPanel`); MCP
  `get_stock_news`. A **past day** (`stock_news(..., day=)`, `?date=YYYY-MM-DD` on the news routes): both sources are asked for
  that day's window (`window`: `LOOKBACK_DAYS` 2 days before it up to its 15:30 close; Google's `after:` / `before:`, NSE's
  `from_date` / `to_date`), items outside it are dropped (`in_window`), cached 6 h. No key, no order path; nothing in the
  strategy reads it.
- `backend/app/services/news_impact.py` – news impact and news alerts (information only, never trades): each headline from
  `stock_news` is scored by a rule list (`score_text`: Indian corporate-news phrases with weights, critical phrases such as
  fraud / raids / SEBI bans / defaults / insolvency / CEO exits / plant fires / open offers; plain share-price moves of the
  stock or its ADRs such as "ADRs fall 3%" / "shares rise", "under pressure", guidance cuts, a regulator's crackdown; a denial
  softens it). A headline that does not name the stock (`mark_relevance` / `about_stock`: symbol, company name or its first
  word; NSE filings always count) is marked "mainly about another company" (`about: False`): only `OFF_TARGET_WEIGHT`
  (25%) of its score counts in `overall`, and it never alerts or rings. Or, with an
  OpenAI key saved (Settings → AI Trading Expert) and `use_ai`, by OpenAI (`ai_read`, no web search, JSON schema, cached per
  headline set; a failure falls back to the rules, `ai_error`). `overall` weights recent headlines (half-life 24 h, 3 days
  max; MIXED when both ways); `action(view, side)` gives KEEP / EXIT / COVER / TIGHTEN for an open long or short and, while
  flat, the direction for a new entry (BUY / PREFER_LONG / NO_ACTION / PREFER_SHORT / SHORT: long and short both, never
  buy-only); `plan(view)` gives all three (flat, long, short) and is sent with every analysis and shown on the News panel and
  in alerts. `timing(view, items, now)` adds when to act while flat (`BUY_NOW` / `BUY_LATER` / `NO_BUY` / `DONT_BUY`, and
  `SHORT_*` the same way): strong news (|score| ≥ 0.5) newer than `FRESH_HOURS` (6 h) is "now", strong-but-older or a milder
  lean is "later" (a dip / the bot's next signal), no lean "don't X on news", the other way "don't X"; sent with every
  analysis and day-report row, and as a "New entry:" line in alerts for stocks you don't hold. **News on a date** (`day_report`, desk `GET /api/news-impact/day?date=&symbols=`, Settings → "News on a date",
  `components/Settings/NewsByDay.tsx`; the chart's News panel has a Date box too): every armed / held stock (or the typed
  ones, up to 40) read as of that day's close (`analyze(..., day=)`: `overall` and the AI read use that time), strongest news
  first; the card also shows Buy? / Short? (the timing), each stock as a `GrowwLink`, an Arm button (`useArming`, the Scalp
  page's arm prompt: Trade list only, no order) and "F&O stocks with news" (fills the stocks box from the all-F&O feed: F&O
  stocks with medium / high filings in that day's window, up to 40). Desk `GET /api/news-impact/stock/{symbol}` (the chart's News panel shows the verdict and each headline's
  chip). **News alerts**: `NewsWatcher` (started in the desk lifespan, not on the scanner) every `every_min` (15) minutes on
  trading days 07:30-20:00 IST reads every stock armed or held on Bots 1-4 (terminal `news_watch_list`) plus
  `extra_symbols`; a headline not alerted before (`NewsAlert.key`), at most `ALERT_MAX_AGE_H` (6 h) old, with impact ≥
  `min_impact` makes one `NewsAlert` row per stock (pop-up: `components/Notifications/NewsAlertPopups.tsx`, polled every 30 s
  on every page, critical ones stay until dismissed) and a Telegram (`alert_notifier.send`); a **critical** one on a held
  stock (critical phrase or HIGH impact against the position, `is_critical`) goes through `send_critical(call=True)` (phone
  call: market hours, 5-minute cooldown). Settings row `NewsWatchSettings` (`GET/PUT /api/news-impact/settings`), `GET
  /api/news-impact/watch|alerts`, `POST /api/news-impact/run|test`; Settings → "News alerts"
  (`components/Settings/NewsAlerts.tsx`). **All F&O** (`NewsWatchSettings.all_fno`, on by default): each pass also reads
  NSE's market-wide announcements in one request (`stock_news.nse_market_announcements` / `parse_nse_market`, not one per
  stock; Google News stays per stock for the armed / held ones), keeps the F&O stocks' filings (`_fno_set`) scored by the
  rules in `NewsWatcher.market` (last `MAX_AGE_DAYS`, at most `MARKET_KEEP`; `GET /api/news-impact/feed`, the card's "All F&O
  news feed"), and for a HIGH-impact or critical filing on a stock that is not armed or held saves a pop-up `NewsAlert`
  (`scope: "fno"`, `_market_alert`; Telegram only with `fno_telegram`, off by default; never AI, never a call). A failing
  market feed only sets `status.fno_error`; the armed / held alerts still go.
- **News page** (desk `/news`, `components/News/NewsPage.tsx`, nav "News"; information only, never trades): tabs for the
  bots' armed / held stocks, each **watch list**, stocks sent from the Scalp page (`/news/?symbols=A,B`), every F&O stock's
  NSE filings (`GET /api/news-impact/feed?valid_only=&prices=`) and the alerts sent (each with the price then). Rows show the
  last price, the watcher's latest view (`/api/news-impact/watch`, `latest`), the new-entry call, the top headline, a News
  button (`StockNewsPanel`), Arm (`useArming`, Trade list only) and add-to-list. **Watch lists** (`app/services/news_lists.py`,
  `NewsWatchList` rows: name + any NSE symbols, checked against the instrument list when it is loaded; at most `MAX_LISTS` 50
  lists of `MAX_PER_LIST` 200): `GET/POST /api/news-impact/lists`, `PUT/DELETE /api/news-impact/lists/{id}` (`name`, `symbols`,
  `add`, `remove`). `news_impact.watch_list(extra, lists)` adds every list's stocks after the bots' and the extra ones
  (entries carry `lists`), up to `MAX_WATCH` 120 a pass. **Validity** (`valid_until` / `is_valid` / `with_validity`): a
  headline counts until 15:30 IST on the next trading day (Mon-Fri, no holiday list) after the day it came out (Friday's
  news till Monday's close); past it, it no longer moves `overall`, is never alerted, and shows as expired (items carry
  `valid` / `valid_until`). **Prices** (`news_lists.prices`, `GET /api/news-impact/prices?symbols=`): the bots' own last
  prices (terminal `main.news_prices`), then the desk's live feed (`state.latest_quotes`), then one batched Groww LTP call
  on the desk's session (skipped while the bots are rate-limited), cached `PRICE_TTL_SEC` 30 s; the alert text, pop-up and
  `NewsAlert` body (`price`, `lists`, `valid_until`) carry it. **Scalp page**: every scan table row has a 📰 button (the
  stock's news, `TickArm.NewsModal`) and the tick bar a **Watch news** button (`components/News/WatchNewsDialog.tsx`: add the
  ticked stocks to a list or a new one, open their news). On the scanner server the lists are the desk's, through the link
  (`useNewsLists`; scanner `GET /api/link/desk-news-lists`, `POST /api/link/desk-news-lists/add` → desk `/api/link/news-lists`
  `[/add]`, `SCANNER_LINK_TOKEN`), and "Open their news" opens the desk's News page.
- `backend/app/services/news_validation.py` – news check, "Did it move?" (the News page's tab, `components/News/NewsCheck.tsx`;
  read-only, never trades): for a day, each headline (the `NewsAlert` rows saved that day via `from_alert`, plus the news of
  typed symbols and, with `armed`, every armed / held stock, `news_impact.analyze(use_ai=False, day=)`; out after the previous
  weekday's 15:30, `for_day`) is set against that day's 1-minute candles from the terminal's `main.news_candles` (today's armed
  stocks from the bots' `_tapes`, else `fetch_frame` on `_scan_broker()` one at a time after `groww_pressure.wait_for_bots`).
  `measure`: news before 09:15 from the previous close (the gap counts), in the session from the open of its minute; the move
  15 / 30 / 60 min later and at the close (in market hours: so far, `so_far`), best / worst move and minutes to the best;
  signed the news' way; verdict `WITH` / `AGAINST` (closed `min_move` %, default 0.5, either way), `FADED`, `NO_EFFECT`,
  `NEUTRAL`, `AFTER_CLOSE`, `PENDING`, `NO_DATA`. `summarize`: per positive / negative × high / medium / low (hit rate, average
  signed move at 30 min and the close, best, worst, minutes to the best). At most `MAX_STOCKS` 40 stocks a run. Desk
  `GET /api/news-impact/validate?date=&symbols=&alerts=&armed=&min_move=` (503 without the terminal in the process).
- `backend/app/services/options.py` – Options page (desk `/options`, `app/api/routes_options.py`, `components/Options/`):
  Groww's option chain for an index (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY on NSE; SENSEX on BSE) or any F&O stock.
  Expiries and lot sizes come from the instrument list (`instrument_master.option_expiries`, CE/PE rows), else Groww's
  `get_expiries`; the chain from Groww's `get_option_chain` (paid API plan; a refusal is shown as is), cached
  `CHAIN_TTL_SEC` (5 s), not refreshed while the bots are rate-limited (`groww_pressure.bots_under_pressure`). Without a
  Groww session the page runs on a simulated chain (Black-Scholes around a slowly moving spot, `source: "simulated"`).
  `summarize`: ATM, PCR (put OI ÷ call OI), max pain, most call OI (resistance) / put OI (support), ATM IV, ATM straddle.
  **Practice book only** (`OptionPaperTrade` table, `place` / `close` / `book`): fills at the chain's last price on the
  feed it was opened on (groww | simulated, never mixed); real Groww prices fill only while NSE is open; an expired
  position closes at intrinsic value from the last spot seen (`settle_expired`); P&L before charges, Groww option
  charges estimated (`charges`). There is **no live options order path**: nothing here calls Groww's order API.
  `GET /api/options/underlyings|expiries|chain|book`, `POST /api/options/order`, `POST /api/options/book/{id}/close`.
- `backend/app/services/us_desk.py` – US practice desk (desk `/us`, `app/api/routes_us.py`, `components/US/`): the SMA
  cross on US stocks, New York time (`us_market.py`: 09:30-16:00 ET = 19:00-01:30 IST in US summer time, 20:00-02:30 IST
  in winter; NYSE holidays and early closes listed per year, add the next year's). **Practice fills only, no order path**:
  every fill is a local `UsPaperTrade` row (US dollars, P&L before charges; SEC + FINRA TAF estimated on the sell);
  nothing calls Groww's, Alpaca's or any broker's order API. Prices: Alpaca market data (`alpaca.py`, the broker behind
  Groww US; free key, IEX feed, read-only, the key is checked against the paper host only) when a key is saved
  (`UsCredential`, Fernet-encrypted, write-only over the API) or set as `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`;
  else a simulator (`SimFeed`) that **trades around the clock**. `UsDesk` loop every 5 s (`US_DESK=off` in tests, not on
  the scanner): 1-minute bars grouped into `candle_minutes` candles from midnight ET, a cross on a closed candle buys /
  sells (shorts with `allow_short`), the opposite cross closes and reverses (`MA_CROSS`), `atr_mult` × ATR stop
  (`STOP_LOSS`), `dollars_per_trade` ÷ price whole shares, entries only `entry_start`..`entry_cutoff` ET, `square_off`
  (`SQUARE_OFF`), a trade from an earlier ET day closes on the next pass (`DAY_END`), `max_trades_per_day`,
  `daily_loss_limit`. A cross on the tape when a stock is first seen is not traded. Settings row `UsDeskConfig` id 1
  (`running` off by default). **Groww US check** (`run_groww_check`, read-only): Groww's `get_quote` / `get_ltp` with
  `exchange="US"` on the desk's Groww session (the SDK has the constant; the API serves NSE / BSE / MCX today), every
  6 h and on demand (`POST /api/us/groww-check`); one Telegram the first time it answers (`groww_alerted`).
  `GET /api/us/state|chart|trades|keys`, `PUT /api/us/config|keys`, `POST /api/us/run|close-all|trades/{id}/close`.
- `backend/app/api/routes_mcp.py` – MCP server for outside AI tools (n8n, Gemini, Claude):
  `POST /mcp` on the desk, streamable HTTP, stateless, JSON responses (hand-written
  JSON-RPC: `initialize`, `ping`, `tools/list`, `tools/call`). Off unless the `MCP_TOKEN`
  secret (24+ chars) is set; then every call needs it as `Authorization: Bearer …` or as
  `/mcp/<token>` (the access log shows `/mcp/<token>`, never the value). Exempt from the
  Google desk lock, which only takes a browser cookie. Read tools: `list_bots`,
  `get_bot_status`, `get_settings`, `get_trades`, `get_scans`, `list_backtests` (runs filtered by
  `settings.bot`; `/api/replay/runs?limit=` up to 500), `get_backtest`, `get_backtest_trades` (`/api/replay/runs/{id}/trades`, `replay.run_trades`:
  each trade's `best_points` / `worst_points` from `max_high` / `max_low`), `compare_backtests`.
  Scalp page: `get_scans` (`kind` cross / chop / morning / all, rows trimmed to `limit`), `get_scalp_monitor` and
  `get_most_active` (the desk's own `routes_scalp` functions, called in-process by `_desk_call`), `list_scan_history`,
  `get_scan_history`; read-only runs: `start_scan` (stocks = the F&O list, every bot's armed ones or `symbols`) and
  `check_scan_accuracy`, through the terminal's scan routes (paused / `SCANS=off` refusals apply).
  Write tools (`set_trade_list`,
  `set_stop`, validated by `chat_actions`; `save_scan_arm` / `run_scan_arm`, Scan & arm) only reach a bot in **PAPER**; a LIVE bot is
  refused. `start_backtest` starts a replay at full speed through `/api/replay/start` (PAPER
  bot only, refused 09:00-15:45 IST Mon-Fri and while that bot's replay is LOADING /
  PLAYING / PAUSED, since `ReplaySession.begin` would stop it). `set_stop` / `set_trade_list` are refused while that bot's replay
  is busy, because a fresh multi-day run re-reads the bot's settings row each day. The
  `initialize` instructions (`TUNING_GUIDE`) walk an AI through the stop/target tuning loop. Calls go through the terminal's own routes (in-process via ASGI on Fly, else
  `SMA_INTERNAL_URL`, default `127.0.0.1:8001`). No tool for orders, closing, the
  PAPER/LIVE switch, start/stop or panic. Setup steps: `MCP.md`.
- `backend/stall_tracer.py` – stall tracer (logging only; `STALL_TRACE=off` turns it
  off, `STALL_TRACE_SEC` sets the 5-second threshold). The desk, every bot, replays
  and all page requests share one event loop, so a call that blocks it freezes the
  whole site. A heartbeat task on the loop, a watchdog thread and `faulthandler`'s
  timer notice when the loop stops beating and log `STALL: …` with memory, CPU,
  threads and the stack of the thread running the loop (faulthandler adds every
  thread's stack to stderr), then `STALL over: …` with how long it lasted. Started
  first in `boot_engine`; `GET /api/stall` reports it. Search the Fly log for `STALL`.
- `scripts/health_watch.sh`, `.github/workflows/health-watch.yml` – outside watcher
  for the owner's phone. GitHub Actions asks `https://paltra.fly.dev/api/health/deep`
  every 5 minutes between 03:00 and 10:55 UTC (08:30-16:25 IST) on weekdays and, when
  the answer is not 200 for two runs in a row and the time is between 09:10 and 15:40
  IST, rings the owner through CallMeBot Telegram and, when the repo secrets
  `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM` / `TWILIO_TO` are set, a Twilio
  voice call (only the HTTP status is printed), repeating about every 30 minutes
  until the check passes. GitHub starts scheduled runs best-effort, so a run can be a
  few minutes late. Read-only (nothing but a GET), the Telegram handle is the repo
  secret `CALLMEBOT_USER` (never printed), and the owner's number is never in code.
  "Run workflow" with `test_call` ticked rings once to check the setup.
- `backend/deep_health.py` – deep health for an outside watcher (read-only, no
  secrets, no mode/stocks/money in the answer). `GET /api/health/deep` on the desk
  (public: listed in `desk_lock._api_public`; also `/sma/api/health/deep`) answers
  200 `{ok:true}` or 503 `{ok:false, reasons:[…]}`. It fails when the event loop is
  blocked right now (stall tracer heartbeat older than 10 s), when a bot's loop task
  has ended, or when a bot has not finished a pass (`StrategyEngine.last_tick_at`,
  stamped after every `run` pass) for 30 s in market hours or while RUNNING (180 s
  otherwise). A stall that already recovered is not a failure. `/api/health` still
  only means the web server replies. Not wired to Fly routing (one machine).
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
   `_cancel_sl_verified`); a news entry on a LIVE bot (`news_order`,
   `news_entry.py`) and a fast-mover hand-off to a LIVE Bot 5 (`fast_bot.py`, `direct_order`) go
   through the same `_open`. Bots 2-5 send theirs through their own
   `bots.BotGrowwClient` (a `GrowwClient` with its own mode), never through
   the main desk's client.
2. **ORB Desk manual desk, "groww" execution** –
   `backend/app/services/manual_desk.py`, `_send_groww_order` →
   `backend/app/brokers/groww_client.py`, `GrowwClient.place_order`
   (also `square_off_all`, `cancel_order`). Only used when
   `state.manual_live` is true.

**LIVE stop health** (`StrategyEngine._watch_stop` → `_stop_dead`): when Groww reports the exchange stop
`CANCELLED` / `REJECTED` / `EXPIRED` / `FAILED` (`STOP_DEAD`) and Groww's MIS book still holds the shares, the bot
sends a critical alert with a call (Groww's remark included), waits `STOP_DEAD_GRACE_SEC` (5 s: Groww's own square-off
cancels the stop just before it sells) and then closes at the market as `STOP_LOST`; when Groww's book is already flat,
the trade is booked as `SOLD_ON_GROWW` at Groww's real price and no order is sent. Exits Groww made are booked at
Groww's price (`_real_exit_price`: the stop's own fill, else `GrowwClient.closed_price`, the day's average closing
price from the positions book, read-only), never at the stop trigger unless Groww gives no price.

The US practice desk (`app/services/us_desk.py`) has no order path at all: its fills are local rows.

On the scanner server (`PALTRA_ROLE=scanner`) and the backtest server (`PALTRA_ROLE=backtest`)
both paths refuse before the SDK (`server_role.refuse_orders`, `server_role.no_orders`).

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
  `min_machines_running = 1`), two dedicated performance CPUs (`performance`,
  2 CPUs, 4 GB; never throttled in market hours), and a volume `data` mounted at `/data`. Change the machine size in
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
