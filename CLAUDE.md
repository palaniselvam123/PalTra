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

- **Every new feature goes on the What's new page** (owner's request: new and old features were hard to tell
  apart). A change that adds a feature to Trend quality, Most active, the SMA terminal or Settings adds one entry to
  `frontend/src/lib/features.ts` (`FEATURES`: id, title, area, the IST day it is added, its PR number, a link to it,
  where it is in words and its purpose in plain words) and puts `<NewTag id="…" />` (`components/ui/NewTag.tsx`) next
  to its control; inside a button or a `<summary>` use `link={false}`. A setting with its own `id="sma-…"` anchor that
  `strategyGroupOf` does not know gets a line in `ANCHOR_GROUP` (lib/features.ts).

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
- A merge to `main` deploys to the backtest server only. The main desk gets a version when the
  owner runs "Test and deploy" with target `main-desk` (see Deployment), so only merge what is
  ready to be tried there.

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
  app/                    Pages: / (P&L dashboard), orb (ORB desk), chart, trade, movers, scanner, news,
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
  **ATR multiples** (`tsl_mode = "ATR"`, `tsl_sl_atr` 1.5 / `tsl_trail_atr` 0.5 / `tsl_target_atr` 3.0 (0 = none), per bot
  and per stock, PAPER and LIVE, replays too; `tsl.tsl_atrs`): `tsl_settings(cfg, entry_price=fill, atr=atr)` turns the
  multiples into ₹ at entry from the stock's own ATR on the bot's candle (`OpenPosition.atr_at_entry`; a restart reads
  the saved one), so one setting fits every stock a bot is handed (meant for Bot 5's fast movers); without a formed ATR
  the ₹ points are used, so a trade is never left without a stop. Settings → 5. Stop-loss and target, stop type
  Trailing, units "ATR multiples": `StrategyConfigPanel.TslAtrSliders` (sliders plus boxes). `stop_points` carries the
  three multiples.
  **Sized from the candle** (`backend/tsl_calc.py`, `GET /api/tsl-calc?symbol=&sizes=1,3,5,15&atr_period=&stop_mult=&
  trail_mult=&target_mult=`; read-only, no order): the stock's 1-minute tape (the bots' `_tapes` for an armed stock, else
  one `fetch_frame` on the scans' session) grouped into each candle size (`candles.resample`, forming candle dropped),
  the Wilder ATR of that size (`indicators.enrich`) and the average range of the last 20 candles, and the multiples
  (defaults 1.5 / 0.5 / 3 × ATR) as a stop distance, trail step and target in ₹ (`tick_sizes.round_price`) and in % of
  the price. Settings → 5. Stop-loss and target, with stop type TSL: `components/Settings/TslCalculator.tsx` (stock,
  multiples, ATR period, sizes, Calculate; a row's "Use ₹" / "Use %" fills the TSL fields of the form, optionally the
  candle size too, and the normal Save applies them to the bot or the scoped stock).
  **One stop for all armed stocks** (`tsl_calc.basket`, `GET /api/tsl-calc/basket?desk=1-5|research&minutes=0&atr_period=&
  stop_mult=&trail_mult=&target_mult=`; read-only, no order): every stock the desk has armed or holds (its own `_tapes`,
  else `_tape_for`, else one `fetch_frame` on the scans' session) on the bot's candle (`minutes` 0) or a picked size: its ATR,
  the multiples in ₹ and %, and `breakeven_after` (₹ the price must go its way before the trailing stop reaches the entry,
  ceil(stop / trail) steps); plus `percent`, one common % for all (the median, 0.05 % steps) with each stock's ₹ and its
  stop in ATRs (`fit` tight < 1 ATR, wide > 3 ATR). Settings → 5, shared scope with stop type TSL:
  `components/Settings/BasketStopCalculator.tsx` ("Use this % for all" fills `tsl_mode` PERCENT, "Use ATR multiples for
  all" fills `tsl_mode` ATR; Save applies). **Trail tracker** (terminal live tab, `components/Terminal/TrailTracker.tsx`,
  display only): every held stock's entry, last, best since entry (`books[].best`: `tsl_best`, else the trade's high /
  low), stop, room to it, P&L if it filled now, the next trail price → new stop, target, and No stop / At risk / No loss
  / Profit locked.
- `backend/smart_stop.py` – **Smart stop** (`stop_type = "SMART"`, one switch at the top of Settings → 5. Stop-loss and
  target, `StrategyConfigPanel.SmartStopSwitch`, anchor `#sma-smart-stop`; also on the terminal's Strategy card; per bot and
  per stock, PAPER and LIVE, replays too; owner's request: one setting that fits every armed stock, not too tight, not too
  loose, and keeps the profit). At each entry (`StrategyEngine._open`) the stock's own 1-minute tape (`_tapes`) of the last
  `smart_days` (3) sessions before the entry minute is grouped into the candles the trade exits on
  (`StrategyEngine._exit_minutes`: the bot's candle, or `pattern_tf` in candle-colour / pattern mode; `_candles`, only
  candles closed before the entry) and read (`dips`): for a buy every dip from a new high that came back to a new high within
  max(`RECOVER_MINUTES` 60, `RECOVER_CANDLES` 12 candles), for a sell every bounce from a new low (dips under `MIN_DIP_PCT`
  0.03 % left out). The room (`room`) is the `smart_pct` (85th) percentile of them in % of the price, never under
  `ATR_FLOOR` (1) × the Wilder ATR 14 of those candles (`candle_atr`; owner's case: a 15-minute colour bot got a 1-minute
  room and was stopped out within minutes), kept between `smart_min_pct` (0.3) and `smart_max_pct` (2.0); under
  `MIN_SAMPLES` (5) dips 1.5 × that ATR is used (else the ATR stop's distance), inside the same bounds. The
  stop starts one room from the fill; `StrategyEngine._trail_smart` (every pass, after the VWAP stop, also while not
  RUNNING via `_guard_held`) follows the best price since the entry (`pos.high` / `pos.low`) at that room, tighter only
  (`want`): at least breakeven plus charges (`stop_plan.breakeven`) once the trade has run one room, and with `smart_follow`
  (on) the room behind the best price shrinks by 1/8 for each room of profit past the first, down to half. Moved only when
  it gains `MOVE_SHARE` (10 %) of the room (`worth_moving`), through `_move_stop` (LIVE `modify_sl`, 2 s apart, a refused
  modify keeps the old stop, a LIVE position restored without its stop id stays at its saved stop). Each stock's room is its
  own (`OpenPosition.smart_room` / `smart_info`); a restart works it out again from the candles up to the entry minute.
  **Live re-size** (`smart_live`, on by default; owner's request: no hand calculation, each stock re-sized while in the
  trade): `StrategyEngine._resize_smart`, once per newly closed exit candle of the trade (`pos.smart_bar`), works the room out
  again with `room` on the stock's candles up to then (the trade's own swings included) at the price now; the stop stays
  tighter only (a wider room only pauses it), and a change of `RESIZE_NOTE_SHARE` (10 %) or more is appended to
  `TradeLog.stop_log` ("11:30 smart room → …"). `books[]` carry `smart_resized_at` / `smart_atr_pct`; the Trail tracker says
  "follows best − ₹… (…%) · re-sized HH:MM". The all-stocks card works itself out (on open, on a change, every minute while
  visible; "Work it out by itself every minute", "Refresh now") and shows under the Smart stop switch too (`smartMode`: only
  the per-stock table, no buttons that switch the stop type). Hit:
  `SMART_SL_HIT` (in `_STOP_FILLS`, `stop_handoff.STOP_REASONS`). The staged stop plan and ATR target do not run with it.
  `books[]` carry `smart_room` / `smart_note` (the Trail tracker's "follows best − ₹…"), and the all-stocks calculator
  (`tsl_calc.basket`, cfg and the desk's `_exit_minutes` passed) shows each stock's buy / sell room today (`smart_long` /
  `smart_short`, with the candle size read). Replays read the
  ~500 one-minute bars they load, so their rooms can read fewer days than live. The Strategy lab does not know it (reads as
  the ATR stop). The stock research baseline keeps the ATR stop.
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
- **One-sided order books** (`pressure_exit.usable_share`, frontend `api.usableShare`; owner's KAYNES case: 0 buy
  orders read at 09:31 on a stock trading ₹500 cr armed it as "sellers 100%"): a buyers' share of exactly 0 or 1 (no buy
  or no sell orders waiting) is a bad quote and reads as unknown. The Most active scan saves it as None
  (`active_stocks.row_from_quote`), and every reader skips old saved ones: the alert (`scalp_alerts.pressure_picks`), the
  live auto-arm, the auto-arm replay (`auto_arm.active_hits`), the fade exit (`note_scan`), the chart
  (`scan_history.active_reads`), the buyers / sellers test, the column test, "Trade a saved scan" and the Most active
  History tables.
- `backend/pressure_exit.py` – **buyers / sellers fade exit for Most active stocks** (`pressure_exit`, off by default;
  `pressure_exit_pct` 55, 30-95; per bot and per stock, PAPER and LIVE; Settings → 3. When to exit, under the SMA cross
  exit, `StrategyConfigPanel.PressureExit`, anchor `#sma-pressure-exit`; owner's case: Most active trades held to the
  other-colour candle often closed a profit at a loss). Only stocks whose Trade list source says "Most active" (the
  auto-arm chip "Auto-arm: Most active" or the Most active card's Arm): `StrategyEngine._watch_pressure_fade` (held-stock
  loop, after the pivot target) closes a buy once the buyers' share of the waiting orders (`buy_share`) is at or below the
  % and a sell once the sellers' share is (`judge`: reads after the entry only, at most `MAX_AGE_SEC` 20 min old, armed
  only once that side was above the level from the last read before the entry on), via `_exit_now` (`PRESSURE_FADE`, LIVE
  stop cancelled first; a LIVE position restored without its stop id is left to that stop). The shares are the desk's own
  Most active scan's: every finished pass goes `routes_scalp._pressure_alert` → terminal `pressure_note` → `note_scan`
  (memory only, keyed by the row's `read_at`), so it reacts as often as that scan's auto interval; with scans paused or
  `SCANS=off` it never fires. The reads are the live ones joined with the day's saved Most active scans (`pressure_exit._rows`: this server's own and the copies other servers send, `scan_history.active_shares`, re-read at most every `ACTIVE_RECHECK_SEC` 15 s), so a desk whose scan runs on another server and a replay of a day with saved scans judge the same reads the chart strip draws; a replay sees only reads up to its own clock. The colour
  exit, stops and targets stay on; whichever comes first closes. `books[].pressure_note` carries the latest reading. **On the chart** (display only): the chart payloads (`/api/chart`, research, replay, `/api/history`) carry `pressure` (`pressure_exit.series`: the day's saved Most active scans, `scan_history.active_shares`, cached per day and read only for runs saved since, joined with the live reads, up to the engine's clock, at most `MAX_SERIES_DAYS` 3 days), drawn as a 0-100 "Buyers %" strip under the candles (`chartStyle.pressureValues`: each candle the latest read before it closed, held within the IST day; Indicators → "Buyers / sellers % (strip)", Auto = while a read exists), with a 50 line and, while the fade exit is on, its levels (`chart_filters.pressure_exit` / `pressure_exit_pct`), and Buyers / Sellers % on the hover line with the time of that read (`chartStyle.pressureReads`, "read 09:52": a long candle takes its reading from any scan inside it), plus that read's Vol vs usual × and money traded ₹ cr (points carry `rvol` / `value_cr` when the scan had them; `scan_history.active_reads`, `pressure_exit._EXTRA` for live reads). Points also carry `buy_qty` / `sell_qty` (Groww's bid / ask total waiting; `scan_history.active_qty`, `pressure_exit._QTY` for live reads), shown on the reading line as "(buy 2.15L · sell 2.20L)" and under the Most active Buyers % bar (`lib/qtyShort.ts`); display only. **Overrule the counter-candle exit** (`pressure_over_counter`, off by default, needs the fade exit; per bot and per stock, PAPER and LIVE): for these Most active stocks `_watch_counter_candles` holds a counter-candle hit (signal line "counter-candle exit held — …") while `pressure_exit.holds`: a read taken since the entry, at most `MAX_AGE_SEC` old, shows the trade's own side above `pressure_exit_pct`; at or below it the fade exit closes the trade, and with no fresh read (scans paused) the counter-candle exit works as usual. Settings: the tick box under the fade exit (`StrategyConfigPanel.PressureExit`). Replays read the day's saved scans the same way. **Executed volume must agree** (`pressure_exit_flow`, 0 = not checked, 50-95; `pressure_exit_flow_window` 3, 1-60 or 0 = the exit candle just closed; per bot and per stock, PAPER and LIVE, replays too; owner's BSE case: a short held while sellers still held 71 % of the waiting orders but buyers did 63 % of the trading): the fade exit closes only when the other side also did that % of the shares traded over the window (`StrategyEngine._fade_flow_hold`: `flow_exit.confirm_span` cut at the entry, `flow_exit.judge` on `trade_flow.minutes`); else it is held ("buyers / sellers exit held — executed volume does not agree") and judged again at the next pass; too little volume to read lets it close. Settings: `StrategyConfigPanel.FadeFlowCheck` under the fade exit. **Watch from the entry** (`pressure_exit_any`, off by default; per bot and per stock, PAPER and LIVE, replays too; owner's BSE case: a short opened with sellers at 63 % was never closed at a 70 % level): `pressure_exit.judge(..., from_entry=)` drops the "that side was above the level first" rule, so the first read since the entry at or below the level closes the trade (still through the executed volume check above); the box "Close even if that side was never above the %" under the fade exit. **Every armed stock** (`pressure_exit_all`, off by default; per bot and per stock, PAPER and LIVE, replays too; owner's BSE case: a stock armed by hand never had the exit): `pressure_exit.every_stock` drops the "armed from Most active" rule in `_watch_pressure_fade`, so any armed stock with that day's Most active reads is judged; the box "Use it on every armed stock, not only Most active ones". **Entries** (`pressure_entry` `OFF` default | `CHECK` | `AT_ARM`, `pressure_entry_pct` 65, 30-95; per bot and per stock, PAPER and LIVE, replays too; Settings → 1. When to enter, `StrategyConfigPanel.PressureEntry`, anchor `#sma-pressure-entry`; owner's case: a stock armed while buyers held most of the orders was traded on the bot's next signal, after the demand had gone): for Most active stocks, `CHECK` adds an entry check in `apply_signal` (`pressure_exit.entry_refusal`: the real order's side at least that % in a read at most `MAX_AGE_SEC` old, else refused like the other checks, no fresh read refuses too); `AT_ARM` adds the same check and `StrategyEngine._pressure_entry_pass` (armed loop, before the always-in pass) enters a flat stock at once through `direct_order` (entry reason `MOST_ACTIVE`, no cross / colour / entry check, flip not applied; market hours, cut-off, Trade list, caps, loss lock and one LIVE bot per stock apply) on the side `entry_side` finds at or above the %, once a day per stock (`_pe_done`, memory only). **Overrule the candle colour** (`pressure_flip`, off by
  default; `pressure_flip_pct` 65, 55-95; per bot and per stock, PAPER and LIVE, replays too; Settings → 1. When to enter,
  under the Most active entries, `StrategyConfigPanel.PressureFlip`, anchor `#sma-pressure-flip`; owner's SOLARINDS case: a
  green colour candle bought while sellers held 71 %): in candle-colour mode, for Most active stocks,
  `StrategyEngine._colour_minute` asks `pressure_exit.flip_side` for the colour's side: when the other side holds the % or
  more in a read at most `MAX_AGE_SEC` old **and the price agrees** (the last closed candle's close under today's VWAP,
  `_session_vwap`, to sell, over it to buy; unknown = no flip; owner's case: buyers 79 % on a strong red candle under the
  VWAP), the call goes that way instead (entry reason `PRESSURE_FLIP`; a trade already on
  that side is held, one on the other side turns); no fresh read = the colour decides. Every entry check, the stop, flip,
  cut-off, caps and one LIVE bot per stock apply. **Replayed picks count as Most active**: a pick run's stocks carry their
  source (`replay.pick_sources`: a Most active pick backtest's stocks and an auto-arm replay's Most active arms), merged over
  the bot's `trade_sources` in `ReplayEngine.load_config`, so every Most active rule (entry check, fade exit, flip) applies in
  those backtests.
  **Executed volume too** (`pressure_flip_flow`, 0 = not checked, 30-95; per bot and per stock; the box under the flip,
  `StrategyConfigPanel.PressureFlip`): the flip also needs the other side's share of the colour candle's executed volume
  (`trade_flow.share` of the minutes in the colour candle just closed, `pressure_exit.flip_side(..., flow, flow_level)`);
  unknown never flips.
- `backend/flow_exit.py` – **executed volume exit** (`flow_exit`, off by default; `flow_exit_pct` 60, 50-95;
  `flow_exit_window` 3 = the last N closed 1-minute candles (1/2/3/5/10/15, or any custom 1-`MAX_WINDOW` 60 typed under "custom minutes…", `StrategyConfigPanel.FlowWindowPick`; the entry check's `flow_entry_window` too) or 0 = the exit candle just closed
  (`_exit_minutes`, from 09:15); `flow_exit_loss` only while losing; `flow_exit_book` the waiting orders must agree (the
  other side above 50 % in a Most active read at most `pressure_exit.MAX_AGE_SEC` old, `book_note`); per bot and per
  stock, PAPER and LIVE, replays too; any stock, not only Most active; Settings → 3. When to exit, under the buyers /
  sellers fade exit, `StrategyConfigPanel.FlowExit`, anchor `#sma-flow-exit`; owner's SOLARINDS case): a buy closes once
  sellers did that % of the shares traded at market over the window (`judge` on `trade_flow.minutes` of the stock's
  `_tapes`), a sell once buyers did. `StrategyEngine._watch_flow_exit` (held-stock loop, after the fade exit) judges each
  window once (`_flow_seen`, entry time + window end), only once the window lies wholly after the entry (`span`,
  `first_minute`: the entry minute counts when the trade opened in its first `ENTRY_GRACE_SEC` 15 s) and the last closed
  minute is in the tape; under half the window's minutes with volume = no read. Closes via `_exit_now` (`FLOW_EXIT`, LIVE
  stop cancelled first; a LIVE position restored without its stop id is left to that stop); the latest reading is
  `books[].flow_note`. **Readings in a row** (`flow_exit_streak` 1-5, default 1; per bot and per stock, PAPER and LIVE, replays too; owner's request: one candle's estimated split swings back and forth): with 2+ the other side must be at the % on that many judged windows one after another (`flow_exit.streak`, `StrategyEngine._flow_run`, memory only, reset per trade; a reading the other way starts again, one with too little volume neither counts nor breaks it) before the exit closes; Settings: the "Close only after" menu on the card. **Chart** (display only): Indicators → "Executed lines: average over N candles" (`chartStyle.flowSmooth` 1-6, `smoothFlows`: the last N candles' bought and sold shares added up). Groupable in "How exits combine" (`exit_join` key `FLOW`). Counts as an exit for
  `strategyChecks.noExitOn`. The Strategy lab does not know it; the stock research baseline keeps it off.
  **Entry check too** (`flow_entry`, off by default; `flow_entry_pct` 55, 50-95; `flow_entry_window` 3 = the last N closed
  1-minute candles or 0 = one exit candle's length ending at the last closed minute; per bot and per stock, PAPER and LIVE,
  replays too; any stock; Settings → 2. Checks before entering, under the fresh trend check, `StrategyConfigPanel.FlowEntry`,
  anchor `#sma-flow-entry`): `apply_signal` (next to the smaller-candle and Most active entry checks) refuses an entry unless
  the real order's side (after `flip_orders`) did at least that % of the shares traded over the window
  (`flow_exit.entry_span` / `entry_refusal` on `trade_flow.minutes` of `_tapes`); too little volume refuses too. It joins
  `entry_block`, so late entry can retry it. Direct orders (Force, news, hand-offs, fast mover) never read it, except the
  Most active "Enter at once" (`_pressure_entry_pass`), which waits while the ticked check says no (owner's KAYNES case: a
  sell went in at once on sellers 78 % waiting while buyers did 71 % of the trading).
  **Confirm the other exits instead** (`flow_exit_mode` `EXIT` default | `CONFIRM`, per bot and per stock, PAPER and LIVE,
  replays too; owner's request: the executed volume as a check like the entry checks, not an exit of its own; the card's
  "Close the trade by itself" / "Use it to confirm the other exits" switch): with `CONFIRM` `_watch_flow_exit` never
  closes (`flow_exit.closes`), and `StrategyEngine._flow_hold` holds every signal exit in `flow_exit.CONFIRM_REASONS`
  (colour turn, counter candles, SMA cross / recovery profit exit, SMA 9 turn / bend / V-turn / deceleration / turn
  against, smaller candles, exit candle cross, Supertrend, Bollinger, EMA, MACD, CVD, slow SMA) unless the other side did
  at least `flow_exit_pct` % of the shares traded over `confirm_span` (the last `flow_exit_window` minutes, or 0 = the
  last closed exit candle; cut at the entry's first minute). Checked in `_exit_now` (after the AND vote), in
  `_colour_minute` before the colour turn (a held turn neither closes nor reverses) and in `_apply_locked` for the cross /
  SMA 9 turn. Too little volume to read lets the exit go; "only while losing" and "waiting orders agree" are not read;
  stops, targets, the square-off, recovery, gap fade and kill switch are never held. The held exit is judged again the
  next time it fires; `books[].flow_note` says confirmed / held.
  **Executed volume starts the trade** (`flow_trigger`, off by default; `flow_trigger_pct` 60, 50-95; `flow_trigger_window`
  3, 0 = one exit candle; `flow_trigger_book` 55, 0 = not checked; per bot and per stock, PAPER and LIVE, replays too; any
  stock; Settings → 1. When to enter, under the Most active entries, `StrategyConfigPanel.FlowTrigger`, anchor
  `#sma-flow-trigger`; owner's request: the Most active entry the other way round): `StrategyEngine._flow_trigger_pass`
  (armed loop, after `_pressure_entry_pass`), once per closed window end (`_ft_seen`), for a flat stock: buyers did at least
  the % of the shares traded over the window (`flow_exit.split` / `trigger_side`) → a buy, sellers → a sell; then the order's
  side must hold `flow_trigger_book` % of the waiting orders in a Most active read at most `pressure_exit.MAX_AGE_SEC` old
  (`book_refusal`; no fresh read = no order); the order goes through `apply_signal(..., why="EXECUTED_VOLUME")`, so every
  entry check, the stop, flip, cut-off, caps and one LIVE bot per stock apply. **Executed volume only** (`entry_mode =
  "FLOW"`, owner's request: trade on executed orders only, not waiting orders or candle colours; Settings → 1. When to
  enter → Enter on, the Strategy card's "Executed volume"): `flow_exit.only_mode` turns the trigger on by itself
  (`trigger_on`) with no waiting-orders check (`trigger_book` 0); `StrategyEngine.on_minute` returns before every cross /
  turn / join / re-entry / counter entry (an opposite cross neither closes nor reverses), and the Most active enter-at-once
  and always-in passes are skipped, so only `_flow_trigger_pass` opens trades (its % and window); `apply_signal` then reads neither the executed volume entry check (`flow_entry`, a duplicate of the trigger over another window) nor the Most active waiting-orders entry (`pressure_entry`; `only_flow`), and `chart_filters` reports `flow_entry` off; the stop, target, ticked
  exits (e.g. the executed volume exit) and square-off close them. The bot's own candle size stays (ATR, exits, chart). The chart's "why" line in this mode (`StrategyChart.flowWhy`, `chart_filters` carries `flow_trigger*`, `flow_entry*`, `candle_minutes`, `reentry_wait_candles`, `max_trades_per_stock`) says what the bot read at each candle close (window, buyers / sellers %, the level) and why no order followed (entry check with its own window, wait after an exit, per-stock cap), never an SMA cross; with the bot's own record it quotes "The bot's note at HH:MM: …" instead of guessing (`StrategyEngine._ft_note` keeps each judged window end per stock, `_ft_log`, memory only, last `FT_LOG_KEEP` 400: the % read, waits, not taken, the `apply_signal` result, holding a trade, outside entry hours; the chart payloads carry `flow_decisions`). **Readings at entry** (display only): `_open`
  saves `TradeLog.entry_flow` (JSON, `flow_exit.entry_reads`: executed buy % over the entry check's window before the order,
  else the last 3 minutes, and the latest fresh waiting-orders read with its time); `_trade_dict` carries it and the chart's
  trade card shows "Executed at entry" / "Waiting at entry", so the numbers match what the bot judged (the hover line shows a
  whole candle, which can include minutes after the order). The same is saved at the exit (`TradeLog.exit_flow`, from
  `_finalize_trade` over the executed volume exit's window, else 3 minutes): "Executed at exit" on the card, and with the
  Indicators tick **Mark what the bot read at each entry and exit** (`chartStyle.tradeReads`, on by default) dots on the
  buyers / sellers strip ("in B32 S68 09:15-09:30", "out …") on the first strip line that is on, for the chart's own book
  only (the replayed run, or the desk's mode; `trades`, not every book) and only for moments inside the candles on screen. **Same candle as the
  chart** (owner's request): window 0 of the entry check / executed volume entry (`flow_exit.entry_span`) is the last
  *closed* exit candle from 09:15 (the candle the chart draws at that size), no longer one candle's length back from the
  order; each executed volume card has a "Read the same candle as the chart" tick (`StrategyConfigPanel.SameCandle`, sets
  the window to 0).
- `backend/day_target.py` – **Day high / low target** (`day_target`, off by default; `day_target_min_pct` 0.2, 0-10;
  `day_target_early_pct` 0, 0-2; per bot and per stock, PAPER and LIVE, replays too; Settings → 5. Stop-loss and target, under
  the pivot target, anchor `#sma-day-target`): a long closes at the nearest of today's high made before the entry (1-minute
  candles that began before the entry minute) and yesterday's high (the last session before today in `_tapes`), whichever
  comes first; a short at the nearest low (`levels`, `target_for`). Only a level at least the min % beyond the entry counts
  (in profit); early % closes that much before it. Worked out once per trade (`StrategyEngine._day_goal`, from the candles,
  so a restart finds the same level), checked on every price by `_watch_day_target` (after the pivot target), closed via
  `_exit_now` (`DAY_LEVEL_TARGET`; LIVE stop cancelled first; a LIVE position restored without its stop id is left to that
  stop). Counts as an exit for `strategyChecks.noExitOn`. The Strategy lab does not know it. **Ignore early highs**
  (`day_target_skip_min`, 0 = off, 0-180; owner's DATAPATTNS case: a 09:30 buy closed 30 s later at the 09:28 top of the
  opening run): today's high / low counts only from candles that began N minutes or more after 09:15 (`levels(..., skip)`);
  yesterday's is unchanged.
- `backend/target_net.py` – **Profit safety net on every trade** (`target_net`, off by default; per bot and per stock, PAPER
  and LIVE, replays too; Settings → 5. Stop-loss and target, anchor `#sma-target-net`; owner's request: like the Bots page's
  profit safety net, per trade, for any profit). The net starts once the trade is in profit by the start distance
  (`target_net_start` `PCT` default = `target_net_start_pct` 0.25 % of the entry | `RS` = `target_net_start_rs` ₹500 for
  the trade's qty | `ATR` = `target_net_start_atr` 1.0 × `pos.atr_at_entry`; `start_level`). From then
  `StrategyEngine._watch_profit_net` (held-stock loop, after `_watch_day_target`) → `_target_net` keeps `target_net_keep`
  (70) % of the best profit per share (`floor`, best = `pos.high` / `low`, stateless, so a restart finds the same net), and
  `target_net_keep2` (75) % once the best profit is `target_net_at2` (2.0, 0 = none) × the start distance; a price at the
  floor closes (`TARGET_NET` via `_exit_now`, LIVE stop cancelled first) and, with the stop on, the stop moves up to it
  (`_move_stop`, tighter only; a hit there shows as the stop). The old mode that held a trade past the pivot / day target
  was removed (owner's request): those targets close as usual again, and a saved `TARGET` start reads as `PCT`
  (`target_net.start`, frontend `netStartOf`). Counts as an exit for `strategyChecks.noExitOn`.
- `backend/order_gate.py` – **Simple order-flow check** (owner's request: the waiting / executed settings were hard to set
  up together; a separate plain card, the old settings untouched; `og_entry_exec` / `og_entry_exec_pct` 60, `og_entry_wait` /
  `og_entry_wait_pct` 60, `og_exit_exec` / `og_exit_exec_pct` 45, `og_exit_wait` / `og_exit_wait_pct` 45, all off, % 1-99;
  per bot and per stock, PAPER and LIVE, replays too; any stock; Settings → 1. When to enter, top,
  `StrategyConfigPanel.OrderFlowCheck`, anchor `#sma-order-flow`). Two numbers for the real order's side: **executed** = its
  share of the shares traded in the last closed exit candle (`candle_span` = `flow_exit.entry_span(..., 0, _exit_minutes)`,
  `trade_flow.minutes`; too little volume = unknown) and **waiting** = its share in a Most active read at most
  `pressure_exit.MAX_AGE_SEC` old (`pressure_exit.fresh_read`; none = unknown). **Entry** (`apply_signal`, joins
  `entry_block`, so it waits and late entry can retry): every ticked number at least its %, unknown refuses
  (`entry_verdict`). **Exit** (`StrategyEngine._watch_order_gate`, held-stock loop after the executed volume exit): once per
  closed exit candle that began at or after the entry's first minute (`_og_seen`), closes (`ORDER_FLOW_EXIT` via `_exit_now`,
  LIVE stop cancelled first; a LIVE position restored without its stop id is left to that stop) when every ticked number is
  known and at or under its % (`exit_verdict`); else held. Each reading is kept per stock (`_og_note` → `_og_log`, memory,
  last `OG_LOG_KEEP` 400) and sent as the chart payload's `gate_notes`; the chart's why line (`StrategyChart.gateWhy`, any
  candle type, Heikin Ashi too) says why an entry was refused and, in a trade, why each candle did not close it. Counts as an
  exit for `strategyChecks.noExitOn`. Direct orders (Force, news, hand-offs) never read it.
- **Buyers & sellers overview** (`StrategyConfigPanel.BuyersSellersMap`, Settings → 1. When to enter, anchor
  `#sma-buyers-sellers`; display only, its buttons only change the form; owner's request: the waiting-orders and executed
  settings looked alike): every setting that reads the split, waiting orders (Most active: `pressure_entry`,
  `flow_trigger_book`, `pressure_flip`, `pressure_exit`, `pressure_over_counter`, `flow_exit_book`) against executed
  (`flow_trigger` / `entry_mode` FLOW, `flow_entry`, `pressure_flip_flow`, `flow_exit`), by job, with the duplicates named
  (a check not read in FLOW mode, executed volume or waiting orders read twice at the entry, two settings that start trades,
  waiting orders in two exits) and which kind to use. `activeRules` marks the two FLOW-mode duplicates "not used".
- `backend/trade_flow.py` – **executed buy / sell volume, estimated** (display and the flip check above; no order path;
  owner's SOLARINDS case: buyers held 79 % of the waiting orders while sellers kept hitting the bids). Groww's API has
  no trade-by-trade list and never says which side started a trade, so per 1-minute candle (`minutes`): its volume
  (`minute_volume`, else Groww's running total differenced) split by the tick rule over the per-second prices the bots
  record (`tick_store.between`, kept `TICK_KEEP_DAYS` 10; up = buy, down = sell, unchanged keeps the last side; `src`
  "T"), else by the candle's shape like the cumulative delta (`candle_share`: ½ + ½ × (close − open) ÷ (high − low);
  `src` "C"). Finished minutes are cached (`_CACHE`, `FINAL_AFTER_SEC` 70). The chart payloads (`/api/chart` and the
  research / replay forms via `StrategyEngine._flow_points` on `_tapes`, `/api/history` via `candle_history._flow`) carry
  `flow` [{time, buy, sell, src}]; the chart draws each candle's executed buy % as a dashed line on the buyers / sellers
  strip (`chartStyle.flowReads`, Indicators → "Executed buy % (strip, estimated)", key `flow`, Auto = with the buyers
  strip) and "Executed buy …% sell …% (ticks / candles)" on the reading line. Stocks no bot watched and days older than the
  tick store read the candle shape only. **Four lines** (owner's request; display only): the strip draws buyers waiting
  (`chartStyle` key `pressure`), sellers waiting (`pressureSell`, 100 − it), buyers executed (`flow`) and sellers executed
  (`flowSell`, 100 − it), each on / off / auto in Indicators (`StrategyChart` `pressureSellRef` / `flowSellRef`; the 50 line
  and fade-exit levels sit on the first line that is on); the reading line shows the four for the candle under the mouse and
  says why one is blank (no Most active read before the candle closed / no volume known). Indicators has a tick box per line (on / off) and
  a Check all / Uncheck all box with "All on Auto" (`ChartIndicators.setAll`). A minute is cached (`_CACHE`) only
  once a later minute is on the tape as well as `FINAL_AFTER_SEC` by the clock: a replay's tape ends in the forming minute
  (part of its volume), which the wall clock used to call finished, so replays kept partial numbers (blanks, wrong splits)
  that the chart, the entry check and the exit then read.
  **Over the candles** (Indicators → "Draw the buyers / sellers lines over the candles", `chartStyle.pressureOver`, on by
  default (owner's request; the old off-by-default key `pressureOnCandles` is no longer read); display only): the four lines' 0-100 scale spans the candle area (`StrategyChart` `CANDLE_MARGINS`) like the SMA
  lines, and the strips below no longer make room for them.
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
  a LIVE trade is never cut for a PAPER Bot 5, except with **practice bots watch, LIVE Bot 5 trades** (`paper_to_live`,
  off by default; ticking it while Bot 5 is LIVE needs `confirm_live`, 409 without): a PAPER source's fast mover then
  hands off to a LIVE Bot 5 (`ready(..., paper_to_live)`, `recovery.handoff(allow_paper_source=True)`), which places a
  real Groww order at its own size; a LIVE trade is still never cut for a PAPER Bot 5; a LIVE position restored without its stop id stays), then the source's `close_symbol` (`FAST_MOVE_HANDOFF`, LIVE stop cancelled first),
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
  replayed bot's book; `replay._fast_move_part` gives the run's `fast_move` totals (shown on the Backtests run).
  **The Bot 5 tab follows the replay** (owner's choice): with no replay of its own, Bot 5 follows the Bot 1-4 replay
  that plays the hand-off (`main._bot5_follows`: playing, else paused, else the last finished; `_replay_view`), so
  `/api/replay?bot=5` answers that run's info with `bot: 5` and `follows`, `/api/replay/state?bot=5` gives
  `replay.bot5_view` (only the handed-over stocks, their `FAST_MOVE` trades and P&L, `fast_log`: each fast move's time,
  stock, side, why and result, kept by `ReplayEngine._note_fast`), `/api/replay/chart?bot=5` that engine's chart and
  `/api/replay/control?bot=5` drives that run. The page shows `components/Terminal/FastFollow.tsx` (the hand-off list)
  and lists the run's `FAST_MOVE` rows on Bot 5's desk too. Display only: the trades stay in the replayed bot's book. The
  backtest server copies `fast_bot.json` from the desk with the bot settings (`export_bot_settings` → `sync`).
  Research never hands off. **Keep the trade** (`keep_trade`, off by default; Settings card tick box): the source
  trade is not cut; `recovery.handoff(..., keep_source=True)` arms the stock on Bot 5 while it stays on the source
  bot's Trade list ("armed on Bot 5 too"), Bot 5's `direct_order` opens its own order on the same side next to it,
  and both bots keep trading it; `handed[..]["keep"]` (snapshot `on_bot5[..].kept`), and `give_back` then only takes
  it off Bot 5 (`recovery.unarm`). A LIVE source trade is left alone in this mode (two LIVE bots never hold one
  stock). Replays play one bot, so in keep mode `ReplayEngine._fast_move` keeps the trade and only notes the move in
  the signal line (no Bot 5 order, no `fast_move` totals). **Bot 5's entry checks** (`entry_checks`, on by default): before the source trade is cut,
  `fast_bot.entry_refusal` reads Bot 5's own settings for the stock (`_target_cfg` → `_cfg_for`) on the source bot's
  enriched frame exactly as a cross would (the ADX gate, then `strategy_engine._entry_block`: VWAP, volume, density,
  RSI, EMA, Supertrend, level, opening range, gap, candle direction; never the cross, patterns, pullback or candle waits);
  a refusal leaves the trade on its bot ("not moved — Bot 5's entry check says no: …", listed in `recent`). **Flip** (`flip`, off by default; Settings card tick box, PAPER and LIVE, replays too): Bot 5
  opens the OTHER side of the trade that moved (`fast_bot.bot5_side`: a fast-rising long → Bot 5 sells, a fast-falling short
  → Bot 5 buys), and its entry checks are read for that side; `direct_order` itself still never applies Bot 5's own
  `flip_orders`. No candles
  = nothing to refuse on. Replays read the same with the run's snapshot of Bot 5's settings (`ReplayEngine._fast_move`;
  a run saved before the setting plays with it off). Off = Bot 5 enters whatever the checks say (the old behaviour).
- **Per-stock daily trade cap** (`max_trades_per_stock`, 0 = off by default; per bot and per stock, PAPER and LIVE,
  replays too; Settings → 6. Size, daily limits and times, next to "Max trades / day"): `StrategyEngine._stock_trades`
  counts today's entries per stock next to `trades_today` (incremented in `_open`, restored per stock by
  `restore_trades_today` from today's rows, cleared with the day roll / Reset / a paper halt release, uncounted with
  `_uncount_trade`). `_stock_cap_hit(cfg)` refuses the next entry on that stock in `_apply_locked` (a reverse closes flat:
  "closed on … — X has had its N trade(s) today (per-stock cap)"; flat: "… ignored — …") and cancels a waiting pullback;
  every entry path (cross, gap mode, turn, news, hand-offs, force) goes through it. The day cap still halts the bot; this
  one only stops that stock.
- `backend/reentry_wait.py` – **Wait N candles after an exit** (`reentry_wait_candles`, 0 = off by default, max 10; per bot
  and per stock, PAPER and LIVE, replays too; Settings → 6. Size, daily limits and times, next to "Max trades per stock /
  day"; owner's MANKIND case: a 15-minute colour bot reversed on every candle in a sideways market). Every closed trade
  (`StrategyEngine._finalize_trade`: stops, targets, colour turns, crosses, square-off…) notes the stock's exit time
  (`_last_exit`, cleared with the day roll, restored by `restore_trades_today` from today's rows of the current mode);
  `_reentry_wait_hit` refuses that stock's next entry next to `_stock_cap_hit` (the reverse after a close: "closed on … —
  … waits N candle(s)", flat: "… ignored — …", a waiting pullback is dropped) until N whole candles of `_exit_minutes`
  (the bot's candle, or `pattern_tf` in colour / pattern mode, from 09:15) have closed after the exit
  (`reentry_wait.ready_at`: the exit's own candle counts only within `GRACE_MAX_SEC` 90 s, at most half a candle, of its
  open, i.e. an exit on the previous candle's close processed late). The exit itself is never held back. The Strategy lab
  does not know it.
- `backend/backtest_compare.py` – **Compare backtests** (read-only; owner's request: which strategy, which stocks, which
  days, one run against many). The blotter's Backtests tab has a tick box per run (`BacktestRuns`, `COMPARE_MAX` 8, in the
  order ticked: the first is the base) and "Compare N runs" opens `/terminal/compare/?runs=&base=&basis=`
  (`components/Terminal/BacktestCompare.tsx`). `GET /api/replay/compare?runs=&base=&basis=gross|net` (2-8 runs,
  `MAX_RUNS`; 400 on a bad list) → `compare` → `load` (the runs and their closed trades) + `analyze`: every run measured on
  the **common ground** (the days all of them finished, `ReplayRun.days[:days_done]`, and the stocks all of them replayed;
  a played day or stock without a trade is ₹0; a filter is dropped with a note when nothing is common, e.g. pick runs);
  `REPLAY_STOPPED` rows and unfinished days are left out. `metrics` per run (P&L, wins, profit factor, per trade / day,
  green days, the day-by-day biggest drop, best / worst day) on the common ground and over everything it finished;
  ranking = average place over `WEIGHTS` (P&L ×2, green days %, biggest drop, profit factor, per trade, days won; a run
  without trades is last on the safety measures); `_verdict`: best, runner-up, lead, day-by-day `head_to_head` and
  `confidence` (clear / likely / close / too_few), most money / steadiest / best per trade, profit leaning on one stock or
  day (`LEAN_SHARE`), all lost; by stock (each run's P&L, best run, verdict ALL_WIN / ALL_LOSE / DEPENDS…, stocks another
  run beat the best on with ≥ `SUGGEST_MIN_TRADES` trades as per-stock setting hints, the hindsight each-stock-best total);
  by day (best run, days every run lost / won); by exit reason; settings that differ (`setting_differences`: snapshot
  fields with missing = column default, pick rules, the fast-mover hand-off, each stock's own settings; `in_use` marks a
  setting whose switch is off in a run as not used, e.g. trailing numbers without the trailing stop), what each run changed
  against the base, and (3+ runs) each differing setting's runs grouped with their average P&L (a hint, not proof). The
  page: the answer in plain words, the ranking (common ground / everything), the running total chart (one colour per run
  by its place, `.cmp-series` in globals.css, end labels up to 4 runs), the differences, by stock (filters, search, CSV), by
  day (CSV), by exit reason; base picker and before / after charges. Nothing is changed or ordered.
  **Trade by trade** (`backend/backtest_detail.py`, read-only; `GET /api/replay/compare/trades?runs=&base=&basis=` → `detail`,
  `GET /api/replay/compare/stock?runs=&symbol=&day=` → `stock_day`; `components/Terminal/CompareDetail.tsx`,
  `useCompareDetail` / `CompareDetailTop` / `CompareDetailRest`): every closed trade of each run (`load`; entry / exit time
  and reason, minutes in the trade, the buyers % of the latest saved Most active read before its entry,
  `add_reads`: day by day, each stock-day's reads once, since the saved-scan cache holds only a few days), per run `stats` (winners / losers, stocks up / down, average winner / loser, time in a
  trade, buys vs sells, by entry / exit reason), `overlap` of each run with the base (`match`: the same stock, day and side
  entered within `MATCH_MIN` 5 minutes = the same trade; overlap rate, same entry but a different exit, the same moment on
  the other side, trades and stocks only one run took, P&L per bucket, the biggest differences listed) and plain-words
  `takeaways`. The page: Key takeaways, Winners / losers / stocks cards, Each run's set-up (always shown: entry mode,
  candle size, Heikin Ashi colours, the pick's candles, stop…, differing rows amber), Did the runs take the same trades?,
  Stock by stock (stock and day picker, each run's trades side by side, an SVG line of each run's P&L on that stock through
  the day from the replay P&L log, else steps at each exit, with ▲ / ▼ entries and ● exits and a buyers % / sellers %
  strip), four scatter plots over every trade (time in the trade, entry time, own-side % at entry, best while open; each
  vs the result), entry / exit reasons, All trades CSV. **Save as PDF** = the browser's print dialog on the light theme
  with every folded list opened (`BacktestCompare.savePdf`, print rules under `.cmp-page` in globals.css, A3 landscape).
  The Backtests tab's Compare bar (ticked runs as chips, Compare, Clear) is pinned under the blotter bar
  (`--blotter-pin-top`, set by `TradeHistoryTable`).
  **Live against a backtest** (`backend/live_book.py`, read-only; owner's request: compare the bot's real trades of a day
  with a backtest of the same day and strategy): a bot's own book of real days is a **negative run id**
  (`encode`: -(first day YYYYMMDD × 10 000 + span × 100 + bot × 10 + book), book 1 LIVE / 2 PAPER / 3 RESEARCH; `decode`,
  `label` "Bot 1 NSE live · 17 Nov"; frontend `lib/liveBook.ts` the same arithmetic). `backtest_compare.load` and
  `backtest_detail.load` read its trades straight from the trade book (`live_book.trades`: that mode, that bot (bot 1 also
  NULL rows), those days, no `run_id`), `live_book.run` gives it a run's shape (settings = the most common strategy snapshot
  saved on its trades at entry, `settings_variants` how many there were; stocks = the ones traded plus the arm log's), and
  with a live book in the comparison `detail` reads every run on the live book's days only (`days`). `overlap` adds
  `entry_worse_rs` / `entry_worse_total` / `entry_later_min` / `exit_later_min` (B's fill against A's on the same trades) and
  `_live_takeaways` explains the gap ("Live against the backtest": trades both took, only live, only the backtest, fills
  ₹ a share worse / better). `stock_day` draws a live book's line from the market-day open P&L log (`open_pnl_log.points`,
  that mode). `GET /api/replay/compare/live-days?bot=&book=` lists the days a book traded with their ids; the blotter's
  Compare bar has **+ Add a real day** (`BacktestRuns.LiveDayPicker`), and the Compare page's **Backtest the same days**
  (`BacktestCompare.LiveBacktest`) starts that bot's replay of those days "As armed live" with hand-offs played at full
  speed (`smaApi.replayStart`, `replayInfoFor` polled) and adds the finished run; a LIVE bot cannot replay (refused as
  always), and the page says what to do. Nothing is ordered.
- `backend/replay.py` – "Replay a past day": a separate `ReplayEngine`
  (subclass of `StrategyEngine`) plays a past session's Groww 1-minute
  candles on its own clock (`_now`), through `ReplayBroker`, which fills
  locally and has no Groww SDK path. Trades are tagged `REPLAY` (own book
  and P&L; no daily trade cap, the loss limit still applies), no alerts are
  sent, and the live engine keeps running. The engine ticks on a fixed
  10-second grid of replay time (`_to_next_grid`), whatever the speed or server
  load, so the same day with the same settings always gives the same trades.
  A run covers one day or a range up to 45 calendar days, about 30 trading days (`parse_replay_range`, `MAX_RANGE_DAYS`), one
  fresh engine per day. **Candles are cached**: each stock's past days come from the Strategy lab's day cache
  (`strategy_lab.load_tape`, gzip files under `strategy_lab/candles/` next to the SMA DB, shared with the Second candle
  test), so only days not read before, and today, are downloaded; a re-run, a pick backtest or a stock ranking on the same
  days downloads nothing again; each run is a `ReplayRun` row with a snapshot of the
  settings it used, and its trades carry `run_id` (day-wise P&L in
  `get_run`, shown in the blotter's Backtests tab). The run detail and its PDF
  (`BacktestRuns.bot5Rows`, `lib/backtestPdf.ts`) list, after the strategy
  settings, the fast-mover hand-off settings and Bot 5's own strategy fields
  from the run's `fast_bot` snapshot when the run played with it. The PDF's trade table also carries each trade's
  **full value** (qty × entry price, what it would cost bought outright) and the **intraday margin** actually blocked
  (the practice wallet's `margin_pct`, 20 % = 5× by default; `backtestPdf.tradeValue`), and **Vol vs usual / money at entry** (each trade's latest saved Most active read before its entry,
  `pressure_exit.read_before` → `replay.run_market_reads`, `GET /api/replay/runs/{id}/market-reads`, `marketReadText`;
  "-" when no scan was saved on this server), and the Result table shows the
  largest trade's full value and margin and the most margin blocked at once (`backtestPdf.peakMargin`: overlapping
  trades add up). The blotter's summary PDF shows the same. A Scalp-pick run's PDF (auto-arm, strong-trend, Most active picks) ends with **How the stocks were picked** (`BacktestRuns.pickRuleRows`: kind, days, stocks read, source, arm window, trend read step and candle, strong-trend checks / side, buyers-sellers % / side / money floor, saved-scan days, price range, max a day; or pick time, top N, direction / rank / bias) and **Stocks armed each day** (`pickLogRows`: day, minute armed, stock, found by, side, why), from the run's `scalp_pick` snapshot (`backtestPdf` `pickRule` / `pickLog`).
  **Where a run came from** (`backend/run_source.py`, display only; owner's request): every start path saves `origin`
  (`run_source.ORIGINS`: replay bar, Scalp page Backtest these, Strategy lab, Trade a saved scan, stock ranking, Compare
  pick times, daily run, backtest queue / its schedule, the Compare page's Backtest the same days, MCP) on the run's
  settings (`ReplayStart.origin`, the pick starts' `origin`, `_start_trend_picks(origin=)`; unknown codes dropped), and
  `run_source.describe` reads it with the pick rule (`scalp_pick.kind` / auto-arm `source`: "Auto-arm · Most active",
  "Trend quality picks", "Most active saved scan"…; a pick run's starter as `via`) into `_run_dict`'s `source`. The
  Backtests list tags each run (`BacktestRuns.RunSourceTag`), filters by it ("Started from"), and the run detail / PDF
  ("Started from" row) show it; Run again keeps the origin. Runs saved before read from their rule, else "Replay".
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
  **Use this strategy on a bot** (`POST /api/replay/runs/{id}/apply`, `RunApply`: `to_desk` 1-5 / research, `reset`,
  `stock_settings`, `confirm_live`; the run detail's `components/Terminal/ApplyRunStrategy.tsx`): the run's saved strategy
  fields (ConfigUpdate minus `COPY_SKIP`, `run_apply_data`) written through `_import_desk` (dry run first, Save's checks);
  `reset` puts every field the run did not save back to its default first; per-stock settings come from the run (only
  `STOCK_FIELDS`; dropped ones named in `notes`), or with `reset` and them off are cleared. Trade list, mode and name stay;
  a LIVE bot needs `confirm_live` (409 without); refused while that bot's replay is loading / playing / paused (a fresh
  run reads the bot's row each day). No order is placed.
  **Bot 5's settings instead** (`RunApply.source` `run` default | `bot5`, `main.bot5_saved`; the card's Copy menu, shown only
  for a run with a `fast_bot` snapshot): copies the Bot 5 fields and per-stock settings the run's fast-mover hand-off
  played with (the run detail's "Bot 5 · …" rows), not the replayed bot's strategy; 400 for a run without the hand-off.
  **Rewind / fast-forward** (`ReplaySession.seek`, `POST /api/replay/control {action: "seek", at: "HH:MM"}`; only
  while PLAYING or PAUSED, the pause state kept; `info().seeking` while it moves): forward sets `_seek` and
  `_fast_forward` ticks the same 10-second grid as fast as its CPU share allows up to the target (same trades, only
  real time skipped; a target past 15:30 is clamped to the last grid step before it, and the day finishes as usual);
  back sets `_rewind`, the day loop drops that day's trades, reviews and P&L points (`_drop_unfinished_days`,
  `open_pnl_log.delete_replay_day`), builds a fresh engine from the day's first candle and fast-forwards to the target
  (a stock handed off earlier that day stays handed off). UI: `ReplayScrub` in `components/Terminal/ReplayBar.tsx`
  (slider 09:15-15:30 plus ± 5 / 15-minute buttons, in the replay bar and on the chart page header while it follows a
  replay; the top strip's `ReplayChip` has ± 5).
  API: `/api/replay*`, `/api/replay/runs[/{id}]`. Refused while the replayed bot is LIVE. **The main desk replays at any
  hour by default** (owner's choice: he decides when; `main.desk_market_hours_free`); the desk secret
  `DESK_REPLAY_HOURS=after` brings back the refusal in market hours (`main._refuse_desk_replay_in_market`, 09:00-15:45 IST
  Mon-Fri, 409; start, resume and Scalp-pick runs; the scan accuracy check follows the same switch). MCP `start_backtest`
  stays refused in market hours. **Today during the session** (`parse_replay_range(..., allow_today=)`, `main._replay_today_ok`:
  always on the backtest server, on the desk unless `DESK_REPLAY_HOURS=after`; from `replay.TODAY_FROM` 09:20): the
  candles are loaded as they stand and the day ends at the last minute closed then (`replay.session_cut`,
  `ReplaySession._loaded_at` / `_day_end`; the seek slider stops there too), then squares off as usual; a start time at or
  after that cut is refused (400).
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
  **Drill-down candles** (`components/Terminal/ChartDrill.tsx`, display only; the chart's "Drill down" switch, on by default,
  kept per browser as `sma.chart.drill`): clicking a candle asks for a smaller size that divides it (`drillSizes`: 1 / 2 / 3
  / 5 / 10 / 15 / 30 / 60 under the candle on screen) and whether to split the candle before it too (on by default); the chart
  then fetches the 1-minute tape (`smaApi.chart(2500, symbol, 1)`, which follows a replay; a past range asks `history` from 40
  smaller candles before), groups it from 09:15 (`resampleCandles`, `withIndicators`: the smaller size's own SMA 9 / 21 and
  ATR) and `mergeDrill` swaps only those candles for their smaller ones (the bot-size candle's lines stay on the last one, so
  the SMA lines run on); the rest stay. A dotted line draws the smaller candles' own SMA 9. `DrillPanel` lists each smaller
  candle (OHLC, SMA 9, slope in % per candle and in ATR per candle, flat < 0.01 / steep ≥ 0.03 %/candle) with the average and
  steepest slope and whether SMA 9 turned inside them, and "Back to N-min candles". A new stock, size or range clears it.
  **ADX bars** (display only): the ADX 14 strip draws bars under its line (`adxBarsRef`, `chartStyle.adxReads`: ADX with
  +DI / −DI, Wilder), green while +DI ≥ −DI, red while below, faint under the ADX check's level (20 when off); the reading
  line adds "+DI … −DI …". The MACD strip already has its histogram. Both strips show on Auto only while the bot uses them;
  Indicators → On shows them always.
  The chart's **Indicators** panel (`components/Terminal/ChartIndicators.tsx`, `lib/chartStyle.ts`, kept in the
  browser) shows or hides SMA fast / slow, VWAP, EMA, Bollinger, Supertrend, ATR and RSI (`Auto` = while the strategy
  uses it, the old behaviour) and sets each line's colour, thickness and pattern and the candle colours; an EMA the
  strategy does not use is worked out in the browser (`emaValues`). Display only. **ADX and MACD strips** (`chartStyle` keys
  `adx` / `macd`, Auto = while the ADX check / the MACD check or exit is on): ADX 14 from the rows' `adx14` (`candle_rows`,
  the bot's own `adx_14`; else `chartStyle.adxValues`, the same Wilder arithmetic) with the ADX check's level dashed, and
  MACD line / signal / histogram from the closes (`chartStyle.macdValues`, `macd.series`' arithmetic, lengths from
  `chart_filters` `macd_fast` / `macd_slow` / `macd_signal`; bars green above zero / red below, faint while shrinking; none
  on the forming candle); the reading line gives the candle's ADX (against the level) and MACD / signal / histogram.
  **Movable columns**: the blotter (every book, Replay included; `TradeHistoryTable` `OrderTable`, key `blotter.cols`)
  and the Backtests tables (runs `backtests.cols.runs`, day-wise `backtests.cols.days`, per-stock `backtests.cols.stocks`)
  use `components/ui/columnOrder.tsx` (`useColumnOrder`, `inOrder`, `ColumnHint`) like the Scalp tables: drag a header
  onto another to move the column, kept per browser, "Reset columns" to undo; the blotter's Trade ID / Stock columns
  stay pinned only while they are the first two. Display only.
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
  10, 15, 30, 60; per bot and per stock, PAPER and LIVE). `StrategyEngine.tick` groups
  each stock's 1-minute Groww tape into candles of that length from 09:15 IST
  (`resample`; open / high / low / close, Groww's running volume total of the
  last minute) before it is stored in `_frames`, so the SMAs, ATR stop, filters,
  gap mode, Bollinger exit, chart and replays all read the longer candles and a
  signal is judged once each one closes (`_candle_is_behind(..., minutes)`).
  Candle patterns keep the 1-minute tape (`pattern_tf`). The chart is told the
  size (`candle_minutes` in the chart payload) and only merges up from it. The
  stock research baseline keeps 1.
  **30 minutes and 1 hour** (owner's request): a day has only 13 or 7 such candles, so the slow SMA needs more than the
  five days of 1-minute candles every stock gets. `candles.history_need(minutes, sma_slow)` gives (calendar days, bars to
  keep): 5 days / 2,500 bars up to 15 minutes (unchanged), more from `LONG_MINUTES` 30 (8 days for 30 min and 10 for
  1 hour at SMA 21; at most `MAX_HISTORY_DAYS` 29 and `MAX_TAPE_BARS` 8,000, so a slow SMA over about 224 / 116 candles
  never forms, `formed_candles`; Settings warns). **Live**: `StrategyEngine.tick` tells the shared quote client what each
  stock is traded on (`candles.note_need`, per engine, memory; `asks_history`, off for replays) and keeps that many bars
  of the tape; `GrowwClient._load_quote` reads `candles.need_of(symbol)` and, for a stock that asks for more than five
  days, adds `_older_candles`: the days before the usual five-day download, read once per stock and day in 7-day requests
  and kept in memory (`_older`), so the 55-second refresh still downloads only five days; a failed older read keeps the
  usual candles and is asked again. **Replays**: `replay.warmup_days(settings)` loads that many days before the run's
  first day (the bot's candle or any stock's own; `WARMUP_DAYS` 4 otherwise). Settings → Candle interval and the
  terminal's Strategy card offer both. Candles only: no order path is touched.
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
  next candle's colour enters again). No Telegram for a refused candle. **Candles in a row** (`colour_count` 1-5,
  default 1) and **smallest body** (`colour_min_body`, % of the close, 0 = any; per bot and per stock, replays too): the
  last N closed colour candles must all be that colour to enter or turn (`colour_call`; a mixed run holds), and a candle
  whose body (the Heikin Ashi body with `ha_colours`) is under that % counts as no colour, like a doji. Colour / pattern
  candles can be 1, 3, 5, 10, 15, 30 or 60 minutes (`PATTERN_TFS`). The smallest body can be set **per colour candle size** (`colour_body_by_tf`, "5:0.05,15:0.15" in %,
  `parse_body_by_tf` / `clean_body_by_tf`; a size not listed uses `colour_min_body`) or **in ATRs** (`colour_body_unit` `PCT` default
  | `ATR`, `colour_body_atr` 0-5 × the Wilder ATR 14 of the colour candles themselves, `_atr_series`, real candles; no ATR yet = any
  body counts). **Each candle of the run its own size** (`colour_body_seq`, "0.1,0.5" oldest first in the unit picked, a blank
  place = the usual floor; `parse_body_seq` / `clean_body_seq`): with 2 in a row and 0.1 / 0.5 × ATR a small then a big candle
  turns the trade, a big then a small one is held ("candle 2 of 2 body … under its …"); entries while flat read it too. Read only with 2+ candles in a row (`need > 1`; the Settings form hides the boxes at 1): sizes left over from a longer run are ignored at 1.
  Settings: `StrategyConfigPanel.ColourBodySize` (unit switch, a box per size, the picked size ticked; `ColourBodySeq`, one
  box per candle in a row).
  **When the smaller candles inside agree** (`colour_inside_minutes` 1/2/3/5/10 dividing `pattern_tf`, 0 = off;
  `colour_inside_body` % of the close, 0 = any; per bot and per stock, PAPER and LIVE, replays too; `candle_patterns.
  inside_colour`): when every candle of that size inside the newest colour candle is one colour (Heikin Ashi colours with
  `ha_colours`; they set the colour even while the bigger HA candle still reads the other one) and the real colour candle
  closed that way, that one candle is enough for an entry or a turn (no run of `colour_count`) with this smallest body
  (on the real candle) instead of the usual sizes; mixed inside candles leave the normal rule to decide. Settings: `StrategyConfigPanel.ColourInside`.
  **A separate rule to close a trade** (`backend/colour_exit.py`; `colour_exit_on` off by default, `colour_exit_count` 1-5 (2),
  `colour_exit_body` % of the close, 0 = any (0.2), `colour_exit_inside` off; per bot and per stock, PAPER and LIVE, replays
  too; owner's request: one colour reading used to both open and close, so tightening exits changed entries too): while a
  trade is open `StrategyEngine._colour_minute` reads `colour_call` on `colour_exit.exit_cfg(cfg)` (that count and % body; no
  ATR / per-size / per-candle bodies; the smaller-candles rule only with `colour_exit_inside`; candle size and Heikin Ashi
  colours are the bot's own) instead of the entry rule; recovery hold reads that call too. When it closes a trade
  (`COLOUR_TURN`) the other side opens only if the entry rule asks for it on that candle, else the stock waits flat. Entries
  while flat are unchanged. Settings → 1. When to enter, under the candle colour rules (`StrategyConfigPanel.ColourExit`,
  anchor `#sma-colour-exit`).
  **Force order follows the colour** (`backend/colour_force.py`; `colour_force`, off by default; per bot and per stock, PAPER
  and LIVE, replays too): in this mode the bot's own candle is 1 minute, so Force used to read SMA fast vs slow on that
  candle. With it on, `StrategyEngine.force_order` reads the last *closed* `pattern_tf` colour candle with `colour_call`
  (`colour_force.side` cuts the tape after that candle's last minute; candles in a row, body, Heikin Ashi, inside candles
  all apply): green buys, red sells, no colour refuses ("no colour to follow … No order."); the forming candle is never
  read. Entry checks are skipped as for any Force; market hours, cut-off, Trade list, caps and flip apply. Settings →
  1. When to enter, under the colour exit rule (`StrategyConfigPanel.ColourForce`, anchor `#sma-colour-force`).
  **Wait for a pullback** (`backend/colour_pull.py`; `colour_pull` `OFF` default | `HALF` | `LOW` | `SMA9`, `colour_pull_candles`
  1-3 (1), `colour_pull_market` off; per bot and per stock, PAPER and LIVE, replays too; owner's request: a colour buy went in
  at the top of a strong green candle): instead of the market at the start of the next candle, `apply_signal` arms a
  `PullbackOrder` (`note` "colour pullback") at a level in the last closed colour candle (`_exit_frame`, real prices) on the
  real order's side: `HALF` its middle, `LOW` its low for a buy / high for a sell, `SMA9` the fast SMA on the colour candles
  when it sits inside the candle on the pullback side, else the low / high. `_watch_pullbacks` fills it on the first price at
  or past the level (entry reason suffix `:PULLBACK`); after `colour_pull_candles` more colour candles without a fill it is
  dropped (no trade; the next colour signal decides again) or, with `colour_pull_market`, sent at the market
  (`PullbackOrder.market_after`, suffix `:MARKET_AFTER_WAIT`). While it waits, `_colour_minute` keeps it (a same-side or
  no-colour candle does not re-arm it; the other colour replaces it). Turns after a colour exit wait too. Settings:
  `StrategyConfigPanel.ColourPull`.
  In both modes (`per_candle`) the bot's own candle is 1 minute (`candles.candle_minutes`),
  the order goes at the start of the next candle, and the tall-candle pullback, candle-extreme
  limit and entry-candle wait are skipped; gap mode and the 1-minute review do not apply.
  **Exits read the colour / pattern candles** (`StrategyEngine._exit_frame` / `_exit_minutes`): since the bot's own candle
  is 1 minute in these modes, every exit, trailing stop and hold that reads "the bot's candle" reads the `pattern_tf`
  candles instead (grouped from 09:15, forming one last): counter candles, Bollinger, EMA, MACD, CVD divergence,
  Supertrend exit, SMA 9 slope / V-turn / deceleration (when `slope_minutes` is 0), slow SMA exit, the exit-candle and
  smaller-candle exits' "own size" (so a 5-minute smaller-candle exit works under 15-minute colour candles), colours on
  smaller candles (entry check and turn confirm), the turn / reversed trades' colour exit, recovery hold, and the
  Supertrend, VWAP and stop-plan trailing stops. The ATR stop at entry, the TSL and the per-tick price watch are unchanged.
- `backend/nth_candle.py` – **Second candle entries** (`entry_mode = "NTH_CANDLE"`, per bot and per stock, PAPER and LIVE,
  replays too): the owner's Most active routine as a bot entry, the rule the Scalp page's Second candle test measures.
  Once per stock per day, when the day's Nth candle (`nth_candle_no` 1-6, default 2) of `nth_candle_minutes` (1/3/5/10/15,
  default 5) closes, `StrategyEngine._nth_minute` (in the `on_minute` chain after the colour mode) reads `nth_candle.call`:
  green buys, red sells at the start of the next candle through `apply_signal(..., alert=False, why="NTH_CANDLE")` (every
  entry check, the stop, flip, cut-off, caps and one LIVE bot per stock apply); a doji, a body under `nth_min_body_pct` (% of
  the close, 0 = any) or volume vs usual under `nth_min_rvol` (0 = off; `rvol_at`: Groww's running session volume at that
  candle against the same candle of the earlier sessions in the frame, unknown refuses) means no order that day. Nothing
  else opens a trade (no cross, no re-entry after a stop-out; `_nth_done` keeps the day read, memory only); the stop,
  trailing stop, target, the ticked exits and the square-off close it. In this mode the bot's own candle is the
  second-candle size (`candles.candle_minutes`), so the ATR, exits and chart read it, and it is a `per_candle` mode (no
  pullback / candle-extreme / entry-candle waits, no 1-minute review, always-in skips it). The 09:20 opening buffer still
  applies (on 1-minute candles the 6th candle is the first that can order). Settings → 1. When to enter, "Enter on: Second
  candle" (which candle, candle size, smallest body, volume vs usual at least); the terminal's Strategy card switches it
  and picks the candle. **Which way** (`nth_side_rule`: `NTH` default = that candle's colour, `MAJORITY` = the way
  most of candles 1..N went, a tie = no order, `AGREE` = that candle only when most agree; `nth_majority_by` `COUNT` green vs
  red | `SIZE` bodies added up; `nth_candle.majority`), **what makes a candle up / down** (`nth_dir_rule` `COLOUR` default = green / red | `CLOSES` = its
close vs the previous candle's close, candle 1 against the session before | `BOTH` = green and a higher close / red and a
lower one, anything else counts like a doji; `nth_candle.candle_moves`, the body floor then reads that move) and **enter at once** (`nth_skip_checks`, off by default): the call goes
  through `StrategyEngine.direct_order` (no entry check, ADX or flip; market hours, cut-off, Trade list, caps, loss lock and
  one LIVE bot per stock still apply). Per bot and per stock, PAPER and LIVE, replays too. The stock research baseline keeps
  `SMA`.
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
- `backend/small_candles.py` – watch smaller candles while in a trade (`small_exit_minutes`, CSV of sizes 1/2/3/5/10, "" =
  off by default; per bot and per stock, PAPER and LIVE, replays too; Settings → 3. When to exit, "Watch smaller candles
  while in a trade"): a 15-minute bot only sees a turn when its candle closes, so `StrategyEngine._watch_small_candles` (in
  the held-stock loop after `_watch_exit_candle`) groups the 1-minute tape (`_tapes`) into each ticked size smaller than the
  bot's own candle (`watched`; `exit_candle.closed_frame`) and, once per newly closed candle of that size (`pos.small_bars`),
  reads `verdict`: `small_exit_cross` (on; `exit_candle.crossed_against`, or SMAs already against on the first candle read
  after the entry), `small_exit_slope` (on; `slope_exit.slope` on that size ≥ `slope_turn_pct` against), `small_exit_counter`
  (N, 0 = off; `indicators._direction_reason` colour rule). Each size's verdict sits in `pos.small_state`; `decide` closes the
  trade (`SMALL_CANDLE_EXIT`, via `_exit_now`, LIVE stop cancelled first; a LIVE position restored without its stop id is
  left to that stop) once `small_exit_agree` (1) sizes say so and, with `small_exit_loss_pct` > 0, only while the trade is at
  least that % under water (`losing_pct`). **Only in trouble** (`small_exit_when` `ALWAYS` default | `TROUBLE`, `small_exit_giveback` 30 % of the best
  profit; owner's request): with `TROUBLE`, `decide` closes only while the trade is in a loss or has fallen back from its
  best price since the entry (`pos.high` / `low`) by that % of the best profit (`in_trouble`), and the latest readings are
  judged again on every pass while it holds. **Bigger candles too** (`small_exit_upper` CSV of 5/10/15/30/60 bigger than
  the bot's candle, `small_exit_upper_must` off): the same readings on bigger candles (`watched_upper`), counted toward
  `small_exit_agree`; with the must tick a bigger size has to say exit as well. Settings anchor `#sma-small-candles`. No reverse unless `reverse_on_exit` has `SMALL` (reverse_exit.py); entries unchanged. Counts as an exit for
  `strategyChecks.noExitOn`. The stock research baseline keeps it off.
- `backend/small_colour.py` – **colours on smaller candles** (`sc_minutes` CSV of 1/2/3/5/10 smaller than the bot's candle,
  "" = off; `sc_count` 1-5 candles in a row, default 2; `sc_join` `ALL` default | `ANY` | `AT_LEAST` `sc_need`; per bot and
  per stock, PAPER and LIVE, replays too; Settings → 3. When to exit, "Colours on smaller candles",
  `StrategyConfigPanel.SmallColourCard`): the 1-minute tape (`_tapes`) grouped into each size (`exit_candle.closed_frame`,
  Heikin Ashi colours with `ha_colours`), each size asked whether its last N closed candles were all green / red
  (`run_colour`, `agree`). **Entry check** (`sc_entry`, off): read in `StrategyEngine.apply_signal` next to `_entry_block`
  (signal side: green to buy, red to sell), else refused "smaller candles: …" (chart ✕ "Small candles"; late entry can wait
  it out). **Confirm a colour turn** (`sc_confirm`, off): `_watch_turn_colour`'s `COLOUR_TURN` (turn / reversed trades' other
  colour exit) sets `OpenPosition.sc_wait` and `_confirm_colour_turn`, every pass, closes once the smaller candles agree with
  the turn, drops the wait when they show the trade's own colour (`confirms_turn`: yes / back / wait); candle-colour entry
  mode keeps its own turns. The smaller-candle exit's counter candles (`small_candles.py`) read Heikin Ashi colours too.
  No order path of its own. The stock research baseline keeps it off.
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
- `backend/always_in.py` – always in a trade (`always_in`, off by default, per bot and per stock, PAPER and LIVE, replays
  too): every armed stock that is flat is entered at once on the side the SMAs point to now (`side`: SMA fast vs slow on
  the last closed candle), with no fresh cross needed, when the bot starts / the stock is armed and again straight after
  any exit. `StrategyEngine._always_in_pass` runs in the armed loop on every pass (after `on_minute` on a new candle, so a
  fresh cross goes first; between candles too), through `apply_signal(..., alert=False, why="ALWAYS_IN")`: every entry
  check, the stop, flip, market hours, cut-off, caps, loss limit and one LIVE bot per stock apply. A refusal is asked again
  on the next closed candle (`_always_tried`); a stock that was held at the last pass and is flat now is asked again at
  once (`_always_held`: symbol → (signal side, trade id)). Gap mode, candle patterns / colour and a waiting pullback skip
  it. **Reverse after a loss** (`always_in_reverse`, off, needs `always_in`): when the trade that just closed lost money
  before charges (`_closed_gross` reads its `TradeLog` row, `always_in.lost`), the next entry goes the other way of its
  signal side (`_always_reverse`, entry reason `ALWAYS_IN_REVERSE`), whatever the SMAs say; after a winning or flat trade
  the SMAs' side is used; kept until that entry opens, asked again each closed candle while a check says no. The chart's
  "why" line says "Always in a trade … buys / sells at once" (`chart_filters` carries `always_in`, `always_in_reverse`).
  Settings → 1. When to enter. The stock research baseline keeps both off.
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
- `backend/macd.py` – MACD check and exit (off by default, per bot and per stock, PAPER and LIVE, replays too): MACD line =
  EMA(close, `macd_fast` 12) − EMA(close, `macd_slow` 26), signal = EMA(MACD, `macd_signal` 9) (`ewm(adjust=False)`, 1-200),
  on the bot's own closed candles. **Entry** (`use_macd`): through `_entry_block` (check name `macd`, chart ✕ "MACD"): a buy
  only while the histogram (MACD − signal) is above zero, a sell only while below; refused until `slow + signal` closed
  candles. **Exit** (`macd_exit`): `StrategyEngine._watch_macd_exit` closes the trade (`MACD_CROSS`, via `_exit_now`, which
  cancels a LIVE stop first) on a candle closed after the entry on which MACD crossed its signal line against it; once per
  closed candle (`pos.macd_bar`); a LIVE position restored without its stop id is left to that stop. Settings → 2. Checks
  before entering (numbers) and 3. When to exit. The stock research baseline keeps both off.
  **Histogram growing / shrinking** (owner's request; per bot and per stock, PAPER and LIVE, replays too): **entry check**
  (`use_macd_grow`, `macd_grow_candles` 1-5, default 2; through `_entry_block`, check name `macd_grow`, chart ✕ "MACD"): a
  buy only after the histogram rose that many closed candles in a row (each bar higher than the one before), a sell only
  after it fell (`grow_reason`; the bars' side is the MACD check's job). **Exit** (`macd_shrink_exit`, `macd_shrink_candles`
  1-5, default 2): `StrategyEngine._watch_macd_shrink`, once per closed candle (`pos.macd_shrink_bar`), closes the trade
  (`MACD_SHRINK`, via `_exit_now`, LIVE stop cancelled first; a LIVE position restored without its stop id is left to that
  stop) once the histogram moved against it that many candles in a row, every step ending on a candle closed after the
  entry (`shrunk_against`): for a buy each bar lower (green shrinking or red growing), for a sell each higher. Groupable in
  "How exits combine" (key `MACD`), confirmable by the executed volume (`flow_exit.CONFIRM_REASONS`), counts as an exit for
  `strategyChecks.noExitOn`. Settings anchors `#sma-macd-hist` / `#sma-macd-shrink`. The Strategy lab does not know them.
- `backend/pattern_check.py` – **Candle patterns as an entry check and an exit** (owner's request: the "Candle pattern"
  entry mode's patterns as support for whatever opens the trade; `pc_entry` / `pc_exit` off by default; `pc_minutes` 0 =
  the candle the bot's exits read (`_exit_minutes`) or 1/3/5/10/15/30/60; `pc_set` `STRONG` default | `ALL` (adds the
  soft ones: hammer, inverted hammer, harami, tweezers, dragonfly / gravestone doji); `pc_within` 1-5; per bot and per
  stock, PAPER and LIVE, replays too). The pattern candles are grouped from 09:15 from the 1-minute tape (`_tapes`,
  `candle_patterns.tf_candles`, closed only) and named by `candle_patterns.candle_pattern`. **Entry check**: in
  `apply_signal` next to the smaller-candle check, a pattern of the signal's side must be on one of the last `pc_within`
  closed pattern candles (`entry_reason`), else refused like the other checks (joins `entry_block`, so late entry can
  wait for it); direct orders never read it. **Exit**: `StrategyEngine._watch_pattern_exit` (held-stock loop, after the
  MACD histogram exit), once per closed pattern candle (`pos.pc_bar`) that closed after the entry, closes on a pattern
  against the signal side (`PATTERN_EXIT` via `_exit_now`, LIVE stop cancelled first; a LIVE position restored without
  its stop id is left to that stop). Groupable in "How exits combine" (key `PATTERN`), confirmable by the executed volume
  (`flow_exit.CONFIRM_REASONS`), counts as an exit for `strategyChecks.noExitOn`. Settings → 2. Checks before entering
  (`StrategyConfigPanel.PatternCheckCard`, anchor `#sma-pattern-check`) and 3. When to exit (`#sma-pattern-exit`), shown
  with the "Indicator checks" or "Candle colour & patterns" style. The Strategy lab does not know them.
- `backend/triple_exit.py` – **Three lines: exit on the fast / medium cross** (owner's request: with SMA 9 / 21 / 50 a
  trade opened on the stack, `closed_candle_cross_triple`, and closed only on the opposite stack, giving back profit;
  `triple_exit`, off by default, read only while `sma_medium` is set; per bot and per stock, PAPER and LIVE, replays too):
  `StrategyEngine._watch_triple_exit` (held-stock loop, after the candle-pattern exit), once per candle closed after the
  entry (`pos.tx_bar`), closes the trade when SMA fast crossed SMA medium against its signal side on the bot's exit
  candles (`crossed_against`, its own rolling means of the closes): `FAST_MEDIUM_CROSS` via `_exit_now` (LIVE stop
  cancelled first; a LIVE position restored without its stop id is left to that stop). No reverse: a new trade still
  needs the whole stack. Groupable in "How exits combine" (key `TRIPLE`), confirmable by the executed volume
  (`flow_exit.CONFIRM_REASONS`), counts as an exit for `strategyChecks.noExitOn`. Settings → 1. When to enter, under
  Medium MA (shown once one is typed), anchor `#sma-triple-exit`. **The third line on the chart** (display only):
  `candle_rows` carries `smaMid` (the enriched `sma_medium`), `chart_filters` carries `sma_medium` / `sma_slow` /
  `triple_exit`; `StrategyChart` draws it amber (`MID_LINE_COLOR`, hidden with the slow line's Indicators switch) and
  names every line by its length on the reading line ("SMA 9 · SMA 21 · SMA 50").
- `backend/cum_delta.py` – cumulative delta (CVD) check and divergence exit (off by default, per bot and per stock, PAPER
  and LIVE, replays too). Groww gives no buy / sell split, so each candle's delta is estimated (owner's choice): volume ×
  (close − open) ÷ (high − low) (0 for no range or no volume), summed from each IST session's first candle (`series`).
  **Entry** (`use_cvd`, `cvd_lookback` 5, 1-50): through `_entry_block` (check name `cvd`, chart ✕ "CVD"): a buy only while
  today's cumulative delta has risen over the last N closed candles (since the open when fewer), a sell only while it fell.
  **Divergence exit** (`cvd_div_exit`, `cvd_div_lookback` 10, 3-100): `StrategyEngine._watch_cvd_exit`, once per candle
  closed after the entry (`pos.cvd_bar`), closes a long whose candle's high tops every high of the N candles before it
  today while the delta is below its value at that earlier high (a short mirrors on lows): `CVD_DIVERGENCE` via `_exit_now`
  (LIVE stop cancelled first; a LIVE position restored without its stop id is left to that stop). No reverse. Counts as an
  exit for `strategyChecks.noExitOn`. Settings → 2. Checks before entering and 3. When to exit. The chart's "Cumulative
  delta (strip)" (`chartStyle` key `cvd`, Auto = while either is on) is worked out in the browser (`chartStyle.cvdValues`,
  same arithmetic). The stock research baseline keeps both off.
- `backend/reverse_exit.py` – reverse a position (PAPER and LIVE). **Reverse instead of only closing** (`reverse_on_exit`,
  CSV of `VTURN` / `TURN` / `CANDLES` / `SMALL`, "" = off by default; per bot and per stock, replays too; validated by
  `ConfigUpdate._reverse_keys` → `clean`): right after the SMA 9 V-turn exit (`SMA9_VTURN`), the SMA 9 turn exit
  (`SMA9_TURN`), the counter-candle exit (`COUNTER_CANDLES`) or the smaller-candle exit (`SMALL_CANDLE_EXIT`,
  `_watch_small_candles`) closes a trade, `StrategyEngine._reverse_after_exit` asks for
  the other side of its signal side through `apply_signal(..., alert=False, why="EXIT_REVERSE")` (every check, the stop,
  flip, cut-off, caps, loss lock and one LIVE bot per stock apply; a refusal leaves the stock flat; no V-turn hand-off or
  counter re-entry follows a reverse). The exit itself must be on. Settings → 3. When to exit ("Reverse instead of only
  closing", `StrategyConfigPanel.ReverseOnExit`). **Close a reversed trade on the other colour** (`reverse_colour_exit`, off by default, same
  card): a trade opened by that reverse (`EXIT_REVERSE`) closes on the first candle of the other colour that opened with or
  after its entry (`StrategyEngine._watch_turn_colour`, shared with the turn trades' `turn_colour_exit`; `COLOUR_TURN` via
  `_exit_now`, no reverse after it; a manual Reverse is not covered). **Reversed trades' own settings** (`reverse_settings`, JSON, "" = none, same card;
  `reverse_exit.GROUPS` / `FIELDS`, checked by `ConfigUpdate`): the entry checks (VWAP, RSI ranges, ADX, cumulative delta),
  the counter-candle exit, the other exits (Bollinger, Supertrend, V-turn, SMA cross) and the TSL stop / target numbers used
  only for the reverse order (`_reverse_after_exit` → `reverse_exit.overlay`) and while that `EXIT_REVERSE` trade is open
  (`StrategyEngine._trade_cfg`; the armed loop overlays the exits group only, a waiting reverse pullback all of it); a field
  left out keeps the bot's value. UI `components/Terminal/ReverseOwnSettings.tsx` (a tick per group). **Reverse button** (`StrategyEngine.reverse_position`; `POST /api/bot/reverse`
  and the `/api/research/bot/`, `/api/bots/{n}/bot/`, `/api/replay/bot/` forms, body `symbol`, `confirm_live`): market hours,
  square-off, cut-off and Trade list are checked first (a refusal leaves the trade), then `close_symbol(reason=
  "MANUAL_REVERSE")` and an `_apply_locked` entry the real opposite way (flip not applied, no cross or filter read, entry
  reason `MANUAL_REVERSE`, `hold_for_next_cross`); caps, loss lock and one LIVE bot per stock still apply to the new side. A
  LIVE bot needs `confirm_live` (409 without). UI: the chart's position box (Close position / Reverse) and the Open positions
  card, each asking first (LIVE: "two real Groww orders"). The stock research baseline keeps it off.
- `backend/exit_join.py` – how exits combine (`exit_join` `OR` default | `AND`, `exit_group` CSV of `KEYS`: CROSS, COLOUR,
  VTURN, TURN, BEND, DECEL, COUNTER, SMALL, XCANDLE, ST, BB, EMA, MACD, CVD, SLOW; `exit_need` 0 = all; `exit_within` 3
  candles, 1-20; per bot and per stock, PAPER and LIVE, replays too; Settings → 3. When to exit, "How exits combine",
  `components/Terminal/ExitJoin.tsx`). OR: every exit closes on its own, the first wins. AND (with 2+ ticked): a grouped
  exit only votes in `StrategyEngine._exit_now` (`exit_join.vote` → `OpenPosition.exit_votes`, key → time, memory only;
  votes older than `exit_within` × the bot's candle are dropped) and the trade is held ("… exit noted, holding …" on the
  signal line) until `need` of them agree; the exit that completes it closes under its own reason, so reverse on exit
  still follows. The SMA cross votes in `_apply_locked` (only an `SMA_CROSS` signal; one that completes the group closes
  and reverses as usual, else the trade is held like the cross exit off). Never grouped: stops of every type, targets,
  gap fade, recovery, square-off, loss limit, kill switch, candle-pattern / candle-colour entry modes (`gate=False`).
- `backend/heikin_ashi.py` – Heikin Ashi colours (`ha_colours`, off by default, per bot and per stock, PAPER and LIVE,
  replays too): HA close = (O + H + L + C) / 4, HA open = (previous HA open + previous HA close) / 2 (`candles`). With it on,
  every rule that reads a candle's colour reads the HA one through `view(frame, cfg)` (the real close, with `open` moved so
  close − open is the HA body): the candle direction check (`_base_entry_reason`), counter candles (exit, early reversal,
  re-entry), candle colour entries (`candle_patterns.colour_call`), the SMA 9 turn's other-colour waits / holds and the
  turn-trade colour exit. Prices never change (orders, stops, targets, SMAs, P&L stay real); candle patterns keep the real
  candles. Settings → 2. Checks before entering. The chart's Indicators panel has its own display-only "Heikin Ashi" switch
  (`chartStyle.heikinAshi`, `lib/chartStyle.heikinAshi`). The stock research baseline keeps it off.
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
- `backend/atr_target.py` – ATR target (`atr_target_mult`, 0 = off by default, 0-50; per bot and per stock, PAPER and LIVE,
  replays too; Settings → 5. Stop-loss and target, "ATR target ×", anchor `#sma-atr-target`): like the ATR stop but on the
  profit side: target = entry + N × ATR for a buy, entry − N × ATR for a sell, set once in `StrategyEngine._open` from the
  same ATR the stop was sized from (`level`; no formed ATR = no target) and restored after a restart from the saved entry
  price and ATR (`restore_open_books`). Applies while the stop is on with stop type ATR, SUPERTREND or VWAP (`on`); the TSL
  and SMA-gap stops keep their own targets. Hit on the price in `_watch_stop` like any target (`TARGET_HIT`, via `_exit_now`,
  which cancels a LIVE stop first; a LIVE position restored without its stop id is left to that stop). In `SNAPSHOT_FIELDS` /
  `STOCK_FIELDS`, `stop_points`, the chat / MCP stop tools (`StopProposal`). The stock research baseline keeps it off.
- `backend/vwap_stop.py` – VWAP + ATR buffer stop (`stop_type = "VWAP"`, with the stop on; `vwap_stop_atr` buffer 0.5, 0-5;
  per bot and per stock, PAPER and LIVE, replays too; Settings → 5. Stop-loss and target, stop type "VWAP + ATR buffer", a
  slider plus box): the stop sits at the session VWAP (`indicators._session_vwap` on the bot's closed candles) − k × ATR 14
  under a long, or VWAP + k × ATR over a short (`level`); `stop_level` gives it only while it guards the real position
  (beyond the fill), else the ATR stop until it does. `StrategyEngine._trail_vwap_stop`, once per newly closed candle
  (`pos.vwap_stop_bar`), moves it to the new level, tighter only (`tighten`), through `_move_stop` (shared with the TSL /
  Supertrend stops: LIVE `modify_sl` 2 s apart, a refused modify keeps the old live stop, a LIVE position restored without
  its stop id stays at its saved stop; saved on `TradeLog.sl_trigger_price`). Hit on the price like any stop
  (`VWAP_SL_HIT`; in LIVE the exchange stop fills it, no second exit). A restart resumes it from the saved stop. Frames
  without `atr_14` (the stock research replayer) get a Wilder ATR worked out on the spot (`_atr`). `stop_points` carries
  `vwap_stop_atr`. The stock research baseline keeps the ATR stop.
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
- `backend/high52.py` – 52-week high (two read-only readers of Groww's daily candles, `interval_in_minutes` 1440; no
  order path). **Entry check** (`use_high52`, off by default; per bot and per stock, PAPER and LIVE, replays too; entries
  only; wired through `_entry_block`, check name `high52`, chart ✕ "52w high"): the closed signal candle must close at the
  stock's 52-week high, the highest daily high of the 52 weeks *before* that day (`levels_from`; today's own breakout
  counts). `high52_side` `LONG` (default: a buy needs the high, a sell is refused) | `SHORT` (the mirror at the 52-week low)
  | `BOTH`; `high52_pct` (0, 0-20): 0 = the close must reach the level, N = within N % of it counts. Levels are read once per
  stock and day: the live engine loads them in the background from `tick` (`ensure` → `_load_live` on the bots' Groww
  threads via `groww_pressure.run_bot_call`; a failure is retried after `RETRY_SEC`), a replay preloads them for its days
  (`ReplaySession._load_and_play` → `preload`, one download per stock, `_wants_high52`), kept in `_CACHE` (memory). Until
  loaded (or on a broker without a Groww session: research, replay brokers) the entry is refused ("52-week high not loaded
  yet"), like the other checks. Settings → 2. Checks before entering. The stock research baseline keeps it off.
  **Scan** (`High52Scanner`, mirrors `MorningScanner`: one pass at a time, the scanner process via
  `scan_worker.high52_job`, stopped by Pause scans; `POST /api/high52-scan/start` (symbols, force), `GET /api/high52-scan`):
  each stock's `analyze` row: last price, 52-week high / low and their dates, `dist_high_pct` / `dist_low_pct`,
  `broke_high` / `broke_low` (today's high / low reached the level), sessions since the high, day and year move; breakouts
  first, then nearest the high (`sort_key`). The Scalp page's "52-week high" tab (`components/Scalp/High52Scan.tsx`: scan
  lists, Show new high / within the band of the high or low / all, band %, tick boxes, Arm / Arm ticked). Nothing in the
  strategy reads the scan.
- `backend/fresh_entry.py` – **Fresh trend entry check** (`use_fresh`, off by default; `fresh_rvol` 1.5, 0-20, 0 = volume
  not checked; per bot and per stock, PAPER and LIVE, replays too, entries only): the Trend quality card's Fresh trend flag
  as a bot rule, so the owner can test "fresh cross + VWAP + volume" as one strategy. Wired through `_entry_block` (check
  name `fresh`, chart ✕ "Fresh"): on the bot's closed candles, for the signal's side, `cross` (the SMAs crossed to this
  side within `trend_strength.FRESH_CANDLES` 3 closed candles), `vwap` (the last close and the fast SMA beyond
  `indicators._session_vwap`), `gap` (wider than on the candle before) and `volume` (Groww's running session volume at
  the last closed candle at least `fresh_rvol` times the same candle of the earlier sessions in the frame,
  `nth_candle.rvol_at`; unknown refuses). Read like the other checks, so "Late entry" (15 minutes on 5-minute candles)
  lets a cross that fails on its own candle enter on the next two. Settings → 2. Checks before entering ("Enter only on a
  fresh trend"). **One-click set-up** (`StylePicker.FreshSetup`, under the Sideways set-up on Settings → Strategy;
  form-fill only, nothing saved until Save): 5-minute candles with the SMA cross entry, the check at 1.5x, late entry
  15 minutes, and the trailing stop in ATR multiples 1.5 / 0.5 with no target. Other checks and exits already on stay on.
  The Strategy lab does not know this check. The stock research baseline keeps it off.
- `backend/sideways_check.py` – **Skip sideways candles** (`use_sideways`, off by default; `sideways_candles` 6, 3-30;
  `sideways_eff` 0.5, 0.1-1; `sideways_atr` 1.0, 0-5, 0 = not checked; per bot and per stock, PAPER and LIVE, replays too;
  entries only; owner's case: SMAs still stacked bullish while the candles had gone flat, bought at the top of the cluster).
  Wired through `_entry_block` (check name `sideways`, chart ✕ "Sideways"): on the bot's own closed candles over the last N,
  Kaufman efficiency |net close move| / sum |close moves| at least `sideways_eff` and |net move| at least `sideways_atr` × the
  last closed `atr_14` (the `trend_phases` rule); either short refuses ("Sideways candles: …", late entry can retry). Direction
  is not read (the signal and other checks pick the side); not enough candles / no ATR refuses. Settings → 2. Checks before
  entering, `StrategyConfigPanel.SidewaysCheckCard`, anchor `#sma-sideways-check` (always shown); also a tick in the
  Sideways starting set-up. Direct orders never read it. The Strategy lab does not know it; the stock research baseline keeps
  it off.
- `backend/index_check.py` – **Nifty direction check** (`use_index`, off by default; `index_rule` `SMA` default | `DAY` |
  `BOTH`; per bot and per stock, PAPER and LIVE, replays too; entries only; owner's request: stop buying stocks into a
  falling market). The index is Nifty 50 (`INDEX` "NIFTY"), quotes only, never armed, held or ordered: while a watched
  stock has the check on, `StrategyEngine.tick` reads its 1-minute candles through the bot's quote client into
  `_index_tape` (a failed read keeps the last one); a replay loads them from the day cache (`index_check.wanted(settings)`
  → `ReplaySession.index_frame`, quoted by the `ReplayFeed` next to the stocks, not part of `frames` / the picks). Read
  in `apply_signal` next to the candle-pattern check (joins `entry_block`, so late entry can retry): `DAY` = Nifty above
  today's first open for a buy / below for a sell (`day_move`), `SMA` = Nifty's SMA fast vs slow (the bot's periods) on
  the last closed candle of `_exit_minutes` (`sma_side`), `BOTH` = both. An index has no volume, so no VWAP rule. Nifty
  not loaded refuses ("Nifty is not loaded yet"). Direct orders never read it. Settings → 2. Checks before entering,
  `StrategyConfigPanel.IndexCheckCard`, anchor `#sma-index-check` (always shown, whatever styles are ticked). The Strategy
  lab does not know it; the stock research baseline keeps it off.
  **vs Nifty and CPR on Trend quality** (display only): each pass reads Nifty once (`ChopScanner._run` →
  `chop_scan.with_index`; a failed read leaves it empty): rows carry `index_move_pct` and `rs_pct` (the stock's day move
  minus Nifty's), and from `chop_scan.day_levels` (`pivots.levels` on the download) `cpr_width_pct`, `cpr_high` /
  `cpr_low`, `cpr_side`, `prev_high` / `prev_low`, `prev_side` (ABOVE / INSIDE / BELOW). The card's "vs Nifty" and "CPR"
  columns (narrow ≤ `CPR_NARROW` 0.2 %, wide ≥ `CPR_WIDE` 0.5 %) and the "Market & levels" filters (stronger / weaker
  than Nifty by at least, CPR width at most, price above / below the CPR or yesterday's high / low;
  `chopFilter.chopPasses`).
- **CPR and previous-day levels** (`pivots.levels` also gives `cpr_high` / `cpr_low` (BC = (H + L) / 2, TC = 2P − BC),
  `cpr_width_pct` (% of P), `pdh` / `pdl` / `pdc`): the price level check's `level_line` takes `CPR` (buy above the CPR
  top, sell below its bottom) and `PREV_DAY` (buy above yesterday's high, sell below its low), anchor `#sma-level`; the
  chart's Pivot points also draw "CPR top / bottom" and "Yest. high / low".
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
  Supertrend / exits. The research baseline keeps both off. **Slope zones on the chart** (display only,
  `frontend/src/lib/slopeZone.ts`, `ZoneBands` in `StrategyChart.tsx`): from the last closed candle's SMA 9 and the close
  that drops out of its window, the chart works out the next candle's close at which each rule that is on fires (turn,
  bend, V-turn once its steep run is in; the deceleration guard has no single price) and shades the zone beyond it
  ("Exit zone · next close ≥ …", amber; while flat with the turn entry on, "Buy zone" / "Sell zone"); the same words go on
  the hover "why" line for any in-trade candle. `chart_filters` carries `sma_fast`, `slope_rules`, `slope_turn`,
  `decel_exit`, `turn_entry`, `turn_toward_cross` for it. Every level is a minimum ("at least"). The Settings slope editor
  (`components/Settings/SlopeEditor.tsx`) shows each slope rule's on/off tick (turn, bend, V-turn, deceleration guard,
  turn entry, U-turn), sliders for every number (lookback, bend, slope candle, the deceleration guard with its own
  picture in ATR per candle) and the bend leg on the V picture.
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
  reverse). **Gap range** (`turn_gap_on`, off; `turn_gap_long_min` / `_max`, `turn_gap_short_min` / `_max`, default
  −0.20 / +0.20 % each, −10..10; owner's TECHM case: turn buys both under and far over SMA 21): a turn opens a trade only
  while the signed SMA 9 / 21 gap % on the last closed candle (`turn_entry.gap_now`) is inside its side's range
  (`gap_refusal`; unknown = no order); outside it opens nothing and against an open trade only closes it (no reverse), like a
  turn away from the cross. Turn entries only (the SMA gap range check in step 2 reads every entry); Settings anchor
  `#sma-turn-gap`. **Close on a candle of the other colour** (`turn_exit_candle`, off; `turn_entry.candle_against`): a turn against an
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
- `backend/live_sma.py` – **Live SMA re-check at execution** (`live_sma_check`, off by default; `live_sma_min_gap` %, 0-5, 0 =
  SMA 9 only has to be on the trade's side; per bot and per stock, PAPER and LIVE, replays too; owner's SHREECEM case of 23 Nov:
  bought at the 09:45 open on the 09:40 close's checks, SMA 9 fell under SMA 21 inside that candle): SMA fast / slow worked out
  with the forming candle's close set to the live price (`live_gap`), judged on the signal's side (`refusal`). An entry that
  `apply_signal` orders at once is refused when they disagree (joins `entry_block`, so late entry can retry); a waiting
  `PullbackOrder` (entry-candle confirm, pullbacks, candle-extreme limit) is judged on every price in `_watch_pullbacks` and
  dropped the first time they turn against it. Direct orders never read it. Settings → 1. When to enter, anchor
  `#sma-live-sma` (with a one-click "also wait inside the entry candle").
- `backend/handoff_candle.py` – hand-off orders wait for the forming candle (`handoff_candle` `OFF` default | `WAIT` |
  `FOLLOW`, set on the **receiving** bot, per bot and per stock, PAPER and LIVE, replays too; Settings → 1. When to enter,
  "Hand-off orders to this bot"): a `HANDOFF` order through `StrategyEngine.direct_order` (a trend hand-off's
  `handoff_order`, Bot 2's `handoff_now`, a backtest's `_chain_order`) no longer goes at once: it waits in
  `StrategyEngine._handoff_wait` and `_watch_handoff_waits` (every pass, after the pullbacks) orders it once the forming
  candle (`_frames` last row) is `entry_candle_pct` % past its open: `WAIT` only the hand-off's way (green for a buy, red for
  a sell), `FOLLOW` either way (green buys, red sells, whatever the hand-off asked); dropped after `entry_candle_max` closed
  candles, at the cut-off or when unarmed. Sent through `direct_order` again (entry reason `HANDOFF:CANDLE_CONFIRM`), so its
  rules still apply. News / fast-mover orders never wait.
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
  `recovery_bar` and restored after a restart). **Candle-colour mode too** (`entry_mode = "COLOUR"`): in
  `StrategyEngine._colour_minute` a colour turn against a losing or flat trade enters the hold instead of closing and
  reversing (a winner turns as usual, `COLOUR_TURN`; a turn while held only holds), and `_watch_recovery` judges it on the
  colour candles of `pattern_tf` minutes (`_recovery_frame` / `_recovery_minutes`, the bot's own candle being 1 minute in
  that mode), so the target, emergency floors, candle count and hand-off to Bot 2 work there as on a cross. `_watch_recovery`, once per closed candle, runs `recovery.judge`:
  loss within `recovery_tolerance_pct` (0.15) % of entry or the candle touched the slow SMA → `RECOVERY_TARGET_HIT`;
  loss ≥ `recovery_emergency_mult` (1.5) × the loss at the cross, ≥ `recovery_atr_mult` (2.5) × ATR, or
  `recovery_max_candles` (6) candles → `EMERGENCY_RECOVERY_ABORT` and the stock is blocked on that bot for the day
  (`_recovery_blocked`); still held at the entry cut-off → `SQUARE_OFF_CUTOFF`. A LIVE position restored without its
  stop id is left to that stop. **Handoff** (`recovery_handoff`, on by default once recovery hold is on): after a
  recovery target, on the live bots (`bots.register` sets `engine.handoff`) and in replays (`ReplaySession` sets
  `ReplayEngine._hand_off`; with the replay's "Play hand-offs" off (`ReplayStart.play_handoffs`, on by default) it
  really moves: practice money, so it never arms a LIVE Bot 2; the stock leaves the rest of the run,
  `ReplaySession.handed_off`; with it on, see `backend/handoff_chain.py`; research never moves a stock), and in LIVE only once
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
- `backend/handoff_chain.py` – where handed-off stocks go back to, and backtests that play the hand-offs (owner's
  request: Bot 1's list only shrank, and a Bot 1 replay never showed Bot 2 / Bot 3 at work). **Send back at the end of
  the day** (`on`, off by default, `at` 15:35 IST; `handoff_return.json` next to the SMA DB, `HANDOFF_RETURN_FILE`):
  `recovery.handoff` notes every hand-off between Bots 1-4 (`note`: the stock's origin, kept through Bot 1 → Bot 2 →
  Bot 3; a stock handed home is dropped; Bot 5's fast-mover hand-offs are not noted); `run` (desk only, started in
  `boot_engine`, not with `server_role.no_orders()`, every `POLL_SEC` 60 s) once `due` (after `at`, before 09:00, or a
  weekend) moves each noted stock that its bot does not hold off that bot and back onto its origin's Trade list
  (`send_back` → `send_back_one`, source chip "Back from Bot N", one LIVE bot per stock checked; the origin
  `hold_for_next_cross`). Arming only, no order. **Unarm instead** (`mode` `UNARM` default | `BACK`; owner's request: handed-off stocks should
  not carry to the next day): `unarm_one` only takes the stock off the bot it was handed to (no bot gets it back); the
  card's menu "unarm them from Bot 2 / Bot 3" / "send them back", "Unarm now" (`HandoffReturnUpdate.mode`). **Backtests**
  (new runs): `handoff_eod` on the run's settings (`backtest_eod`: BACK only while the switch is on in BACK mode, else
  UNARM) — a stock that ends a replayed day on Bot 2 / Bot 3 is unarmed (`replay.unarm_at_day_end` adds it to
  `handed_off`; `day_symbols`: a plain replay never trades it again, a pick / auto-arm run starts each day only with that
  day's picks), and no stock starts a day on Bot 2 / Bot 3; runs saved before keep the old carry-over. `GET/PUT /api/bots/handoff-return`, `POST /api/bots/handoff-return/now`;
  Settings → Bots, "Hand-offs: send stocks back at the end of the day" (`components/Settings/HandoffReturn.tsx`).
  **Backtests** (`ReplayStart.play_handoffs`, on by default; the replay bar's "Play hand-offs (Bot 2 / Bot 3)"; saved as
  `play_handoffs` and the run's `chain` snapshot, `replay_snapshot`: Bot 2's and Bot 3's strategy fields and per-stock
  settings plus `return_eod`, so Resume plays the same): a recovery / losing V-turn hand-off to Bot 2 or a breakout to
  Bot 3 no longer drops the stock: `ReplayEngine._chain_hand_off` keeps it in the run on the target bot's settings
  (`overrides`: that bot's fields and its own per-stock settings, plus `recovery.HANDOFF_SETTINGS` for Bot 2; merged as
  a stock override in `load_config`, `_on_bot` / `_chain_fields`), waiting for its next cross; Bot 3's breakout
  arrival (`_chain_arrival` → `queue_breakout_entry`) and Bot 2's `handoff_now` order (`_chain_order`, run after the
  tick) are played on the same engine. The live Trade lists are not touched. Trades opened on another bot's settings
  carry that bot in their entry reason (`handoff_chain.tag`: "SMA_CROSS@B2"; `lib/entryReason.ts` shows "· as Bot 2"),
  the run's `handoffs` totals per bot (`replay._handoff_part`, shown on the Backtests run), and `ReplayInfo.chain`
  (`on_bot`, `log`) on the replay bar. Each replayed day carries the stocks where the last ended
  (`ReplaySession.chain_on`), or starts them all back on the replayed bot when send-back is on. A run saved without
  `chain` plays the old way.
- `backend/breakout.py` – trend breakout handoff, the reverse of the recovery handoff (`breakout_handoff`, off by
  default, per bot and per stock, PAPER and LIVE; meant for the chop bot, Bot 2; a hand-off does not tick it, so a
  moved stock follows Bot 2's own setting). On each newly closed candle of an armed stock (the bot's own candle; not the
  candle on the tape when the stock was armed), `StrategyEngine._watch_breakout` (before `on_minute`, in the armed loop)
  runs `breakout.read`: height ≥ `breakout_atr_mult` (1.8) × ATR and either |SMA fast − slow| / slow ≥ `breakout_gap_pct`
  (0.20) % or the candle's volume ≥ `breakout_volume_mult` (1.5) × the previous 20 candles' average (missing volume never
  counts; the day's first candle is never read, `opening_candle`: its volume is set against the quiet end of the day
  before, so nearly every stock looked like a breakout at 09:30). The chart's reading line shows each candle's ATR 14 and its size (high − low) in ATRs, against the
  breakout's `breakout_atr_mult` while the hand-off is on (`chart_filters` carries `breakout_handoff` / `breakout_atr_mult`). An open trade already on the breakout's side (its real side: a buy on a green breakout candle, a sell on a
  red one; `breakout.side`) is kept and the stock stays on this bot (no hand-off). Otherwise an open trade on the stock is
  cut (`BREAKOUT_STOP`, via `_exit_now`, which cancels a LIVE stop first; a
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
  **Owner's rework** (Settings → 7. Advanced, "Trend breakout (move the stock on)"): **what counts** (`breakout_rule`, per
  bot and per stock): `CANDLE` (default, the tall candle above) or `STRONG` (`breakout.strong`: `trend_strength.read`, the
  Scalp page's 7-check strong trend, midday rules, on the bot's closed candles with the session VWAP; its side is the
  trend's; meant for Bot 1, so one tall candle no longer sends a stock to Bot 3); `breakout.judge` picks. The strong-trend
  rule is adjustable (per bot and per stock): each of the 7 checks is `must` / `use` / `off` (`breakout_trend_checks`, CSV
  "stack:must,vwap:use,…", `breakout.parse_trend_rules`, checked by `ConfigUpdate._trend_checks`; "" = stack / VWAP / slope
  must pass, the rest count) and at least `breakout_trend_need` (1-7, default 6, never more than are on, `trend_need`) of the
  ones on must pass (`breakout.strong` reads `trend_strength`'s `trend_checks` and judges them itself); Settings →
  7. Advanced, `StrategyConfigPanel.TrendChecks` (Must pass / Counts / Off per check, "Need at least N"). **Where it goes**
  (`StrategyEngine._breakout_target`): a stock that reached this bot through a hand-off goes back to its origin
  (`_handoff_origin`: `handoff_chain.moved()`; in a replay with hand-offs played, the replayed bot for a stock played as
  another, `ReplayEngine._acting_bot` / `_handoff_origin`), anything else to Bot 3 (Bot 3's breakout arrival entry only
  then). **An open trade against it** (`breakout_cut`, off by default, owner's choice): not cut; it runs to its own exits
  (stop, target, colour, cross…) and the stock moves on the first closed candle after it is flat
  (`StrategyEngine._breakout_pending`, memory only); on = cut at once (`BREAKOUT_STOP`, `_move_on_breakout`), the old way.
  **Trade first, then sort** (owner's request: Bot 1 trades a stock first, then sends the trenders to Bot 3 and the choppy
  ones to Bot 2). **When to move it** (`breakout_when` `ANY` default | `IN_PROFIT`, per bot and per stock; `breakout.when`):
  with `IN_PROFIT` a breakout / strong trend moves the stock only while this bot holds a trade on its side that is in profit
  (`breakout.in_profit` on the last price, and with **smallest profit** `breakout_min_profit_pct` (% of the entry, 0-20) / `breakout_min_profit_rs` (₹ a share, 0-10000), 0 = any profit, both when both set: `breakout.profit_short`, below it the stock stays and is read again; `StrategyEngine._trend_on_trade`, once a day per stock, `_trend_sent`): the trade is
  **kept** (owner's choice), `recovery.handoff(..., keep_source=True)` arms the stock on the target (`_breakout_target`) too
  and `handoff_order` (`recovery.order_now`, live bots) has it open the same side at once (`direct_order`, entry reason
  `HANDOFF`; one LIVE bot per stock applies); once this bot's trade closes, `_leave_after_trend` takes it off this bot's Trade
  list (`recovery.unarm`) and notes it for the send-back (`handoff_chain.note`) (`_trend_kept`, memory only: a restart leaves
  it on both). A stock this bot is not holding, holds against the trend or at a loss stays; `breakout_cut` does not apply.
  Backtests hold one trade per stock (`ReplayEngine._keeps_both` False), so there the trade closes (`TREND_HANDOFF`) and
  the target takes the same side at once (`_chain_order`). Logged as `TREND_PROFIT` in `handoff_log`. **Choppy after losing
  trades** (`chop_losses` N, 0 = off by default, max 10; per bot and per stock, PAPER and LIVE, replays too;
  `recovery.chop_losses`): `StrategyEngine._watch_chop_losses` (armed loop, before the breakout) reads each closed trade of
  the stock once (`_chop_watch`, `_closed_gross`; also one a cross closed and reversed) and counts the losing ones before
  charges (`_stock_losses`, memory only, cleared with the day); at N, once flat, `_hand_to_chop_bot(kind="CHOP_LOSSES")` moves
  it to Bot 2 (chop recipe; `handoff_now` makes Bot 2 order the opposite of the last trade at once). Settings → 7. Advanced.
- `backend/fade_unarm.py` – **un-arm a stock once its trend fades** (`fade_unarm`, off by default; `fade_unarm_rule` `PHASE`
  default | `SCORE` | `EITHER`; `fade_unarm_min` 30, 5-240; per bot and per stock, PAPER and LIVE, replays too; owner's request:
  a stock armed on a strong 09:45 read stayed on the bot all day after it went sideways). In the armed loop, once per closed
  candle (before the chop-losses and breakout watchers), `StrategyEngine._watch_fade` reads the stock on the candles the bot
  trades (`_exit_minutes`: the bot's own, or the colour / pattern candles) with `stock_regime.cached` (the Stocks table's
  Trend column: `PHASE` = the trend phase now is CHOP, `SCORE` = today's crosses read CHOPPY; `fade_unarm.faded`, a read with
  `phase_early` never counts). Choppy for `fade_unarm_min` minutes in a row (`_fade_since`, memory only, reset by a trending
  read, cleared with the day) and this bot flat on it (an open trade is never cut; it runs to its own exits and the stock
  leaves once flat): `_fade_unarm` takes it off the Trade list (`fade_unarm.unarm_row` on the engine's own `config_id`, so
  the arm log records it; a replay's override only drops it from `replay_symbols` for the rest of that day). Signal line and
  trade-channel alert ("un-armed — trend faded …"). Arming only, no order. Settings → 7. Advanced, under "Several bots"
  (`#sma-fade-unarm`). The stock research baseline keeps it off.
- `backend/trend_trade.py` – **Trade on the trend score / phase** (owner's request: Trend quality auto-armed stocks entered
  and closed on the trend reading, not an SMA cross; `tq_entry` / `tq_exit` off by default; per bot and per stock, PAPER and
  LIVE, replays too; Settings → 1. When to enter, `StrategyConfigPanel.TrendTrade`, anchor `#sma-trend-trade`). The reading is
  the Trend quality card's on the candles the bot trades (`_exit_minutes`) from `_tapes` (`StrategyEngine._trend_read` →
  `stock_regime.cached`, which now also carries `trend_side` / `trend_score` / `trend` from `trend_strength.read`): the phase
  now (BULL / BEAR / CHOP) and the 0-100 trend score (share of the 7 checks passed for the SMAs' side). **Entry**
  (`tq_entry_score` 70, `tq_entry_phase` on): `StrategyEngine._trend_entry_pass` (armed loop, after `_pressure_entry_pass`),
  once per closed candle counted from 09:15 (`_tq_tried`), enters a flat stock on the trend's side when the score is at least
  the level and (ticked) the phase agrees (`entry_call`; `phase_early` waits) through `apply_signal(..., why="TREND_QUALITY")`
  (every entry check, stop, flip, cut-off, caps, wait after an exit, one LIVE bot per stock). **Only the trend opens trades**
  (`tq_only`, on): `_trend_only_hit` in `_apply_locked` refuses the bot's own signals on these stocks (`blocks`; a cross
  still closes, it does not reverse; Force, news, hand-offs, manual reverse pass). **Exit** (`tq_exit_score` 40,
  `tq_exit_phase` on): `_watch_trend_exit` (held-stock loop, after the fade exit), once per candle closed after the entry
  (`_tq_judged`), closes (`TREND_FADE` via `_exit_now`, LIVE stop cancelled first; a LIVE position restored without its stop
  id is left to that stop) when the phase is CHOP or the other way, or the score for the trade's side is under the level
  (SMAs the other way = 0) (`exit_call`). **Which stocks** (`tq_all`, off): the source chip "Auto-arm: Trend quality" (the
  auto-arm backtest's Trend quality picks carry it via `replay.pick_sources`), or every armed stock. Counts as an exit for
  `strategyChecks.noExitOn`. Normal candles (not Heikin Ashi). **Skip a doji** (`tq_skip_doji`, off; `tq_doji_pct` 10, 1-90; per bot and per stock, PAPER and LIVE, replays too; owner's GLENMARK case: bought at 10:25 on a doji 1.6 rupees above SMA 9): `entry_call` waits while the newest closed candle the score read (`stock_regime.read`'s `last_bar`, real candles) has a body under that % of its high-low range (`doji_note`); asked again next candle. Entries only. The stock research baseline keeps it off.
- `backend/stack_entry.py` – **Enter on three SMAs with widening gaps** (owner's request: the Trend quality card's
  "3 SMAs" read as a bot strategy; `stack_entry` off by default, `stack_widen` 1-10 (2), `stack_third` 50 (longer than
  the slow SMA, at most 200), `stack_min_gap` 0.05 % (0-5, 0 = any), `stack_only` off; per bot and per stock, PAPER and
  LIVE, replays too; Settings → 1. When to enter, `StrategyConfigPanel.StackEntry`, anchor `#sma-stack-entry`). The
  three lines are SMA fast / slow / `stack_third`, or the bot's own fast / medium / slow when `sma_medium` is set
  (`periods`). `StrategyEngine._stack_entry_pass` (armed loop, after `_flow_trigger_pass`, before the always-in pass),
  once per closed candle of the bot's candles (`_stack_tried`), for a flat stock: `stack_entry.call` (on
  `chop_scan.three_read` of the closed candles) gives LONG when fast > slow > third, SHORT for the mirror, once both
  gaps have widened `stack_widen` candles in a row and each is at least `stack_min_gap` %; the order goes through
  `apply_signal(..., why="STACK_WIDEN")`, so every entry check, the stop, flip, cut-off, caps, wait after an exit and
  one LIVE bot per stock apply. Each widening run enters once (`_stack_done`: side and the closed candle at the entry; a
  run that began at or before it is the same run), a refused run is asked again on its next candle, and one that has
  widened `RUN_CAP` (30) candles or more is too old to join. **Only a fresh line-up** (`stack_fresh`, off; `stack_fresh_candles` 6, 1-30; owner's request): the lines must have been stacked that way no more than N closed candles (`three_read`'s `stack3_candles`, `fresh_need`), so a stack standing since yesterday is not entered when its gaps widen again; the Strategy lab reads it too (`Choice.stack_fresh`, trades carry `stack_age`). `stack_only`: `stack_entry.blocks` in `_apply_locked` (next
  to `_trend_only_hit`) refuses the bot's own signals on the stock (a cross still closes, it does not reverse; Force,
  news, hand-offs pass). Not in gap mode or the per-candle / executed-volume-only modes. The tape keeps enough candles
  for the third line (`stack_entry.longest` in `tick`'s history need and `replay.warmup_days`). Exits are the bot's own.
  The Strategy lab does not know it; the stock research baseline keeps it off.
- `backend/trend_log.py` – **Trend readings of a backtest** (display only; owner's request: the auto-arm backtest works the
  Trend quality reading out by itself and kept none of it, so 10:45's score 86 could not be set against the trade at 12:00):
  `StrategyEngine._record_trend` (after `_trend_entry_pass` in the armed loop and `_watch_trend_exit` in the held loop),
  once per closed candle of `_exit_minutes`, for stocks `trend_trade.applies` (Trend quality source or `tq_all`): price,
  side, trend score, the checks (`stock_regime.read` now carries `trend_checks` / `trend_mode`), score label, crosses, phase
  and since when, and the bot's decision (the entry / exit pass's note, `_tq_exit_at` for a trend exit, else why it was not
  asked). Kept in memory per stock (`_tq_log`, `TQ_LOG_KEEP` 400; chart payload `trend_reads`, the why line's "Trend reading
  at HH:MM: …", `lib/trendReads.ts`) and, in a replay (`engine.trend_rows`, set by `ReplaySession`, saved in batches by
  `_flush_trend`), in `trend_reads.db` next to the SMA DB (`TREND_READS_FILE`); a run's rows go with `delete_run`, an
  unfinished day's with `_drop_unfinished_days`. `GET /api/replay/runs/{id}/trend-reads?symbol=&day=&work_out=` (one stock
  at a time; without a stock only the picker); `work_out` reads a run saved before again from the cached candles with the
  run's SMA periods, candle size and `tq_*` settings (`trend_log.work_out`, no positions: "would enter …", `source`
  "after"). UI: the run detail's "Trend readings" (`components/Terminal/TrendReadings.tsx`: day / stock, Only when it
  changed, What changed, CSV, Work it out now). Nothing in the strategy reads it; no order path.
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
  Switching a LIVE stock to `EXIT` asks for a confirm in the terminal. **`REVERSE`** ("Exit and reverse"): no card, no
  alert; the trade is closed and the other side opened at once (`StrategyEngine._review_reverse` → `reverse_position(...,
  close_reason=AUTO_REVIEW_REVERSE, why=AUTO_REVIEW_REVERSE, trade_id=)`: the Reverse button's path, so market hours,
  square-off, cut-off, Trade list, caps, loss lock and one LIVE bot per stock apply to the new side; a reverse refused
  before the close closes the trade as `AUTO_REVIEW_EXIT`); a LIVE stock asks for a confirm too ("two real Groww orders").
  **What counts as uncertain** (`review_conditions` CSV of `GAP` (default: the gap rule above) / `SLOPE` (the fast SMA's
  slope with the trade over `review_slope_candles` (3) closed candles, % per candle, `review.fast_slope`, at or under
  `review_slope_flat` (0.01); 0 or below = turned) / `STEEP` (that slope against the trade at least `review_steep_pct`
  (0.03)), joined by `review_join` `OR` (any) | `AND` (all); `review.uncertain`; per bot and per stock): the reasons that
  fired go in the evidence (`five_min.reasons`), the alert and the card ("Why uncertain"). With the gap rule alone a
  stretch ends when the gap moves clear of the band (as before); with any other condition ticked it ends on the first
  closed candle the conditions no longer hold. Settings → 7. Advanced, `StrategyConfigPanel.ReviewConditions`. The stop,
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
  strategy reads it. "Today" is the session of the last closed candle (on a holiday, a weekend or before the day's
  first candle closes the last session is read; minutes since a cross count to its last candle; owner's case: on the
  24 Nov holiday every stock read TRENDING with 0 crosses). Lifecycle mirrors `CrossScanner` (manual Scan now, one pass at a
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
  scan every `every_min` minutes (1/2/3/4/5/10/15/20/30/45/60, default 30; a pass still running is never doubled, so a short interval just starts the next pass as soon as one ends) while NSE is open, no browser
  needed, with the card's last choices (candle, Armed only read fresh each time: the desk's link on
  the scanner, `armed_lists()` on the desk; quiet minutes, price range, Telegram). Settings in
  `chop_auto.json` next to the terminal's SQLite file (the Fly volume); `PUT /api/chop-scan/auto`,
  and `GET /api/chop-scan` carries `auto` (next_at, last error). On by default only on the scanner
  server; never runs on a desk with `SCANS=off` or while scans are paused. A manual Scan now counts as
  a run.
  **30-minute and 1-hour candles, and the scan's own SMAs** (owner's request; Trend quality and Cross scan; nothing in the
  strategy reads them, the bots' own sizes still stop at 15): `cross_scan.SCAN_MINUTES` (1/2/3/5/10/15/30/60) is the scan
  candle and the sizes of every `*_by_minutes` read (so "Crosses today on" / "Trend on", the trend-rank alert and Scan & arm
  take 30 and 60). `sma_fast` / `sma_slow` on `POST /api/chop-scan/start`, `POST /api/cross-scan/start`, `ChopAutoUpdate`
  and `chop_auto.AutoSettings` (0 / 0 = the main desk's; `cross_scan.pick_smas`: both, `SMA_MIN` 2 to `SMA_MAX` 200, fast
  shorter, else 400 in plain words). **Days downloaded**: `window_days(minutes, slow)` = the calendar days a size needs for
  its slow SMA (never under `WARMUP_DAYS` 4, so SMA 21 on sizes up to 15 minutes reads the same days as before; at most
  `MAX_WINDOW_DAYS` 29); a Trend quality pass downloads `download_days` (its own window, widened to at most
  `OTHER_SIZES_DAYS` 15 for the row's 30-minute / 1-hour reads: 8 days at SMA 21, so the day's first scan asks Groww twice
  per stock; later scans download only today). `chop_scan.scan_frames` cuts the download back to the scan's own window for
  its own read and hands the whole of it to `analyze(..., wide=)`, which reads each bigger size on its own window. The day's
  last candle of a size that does not divide the session (the 15:15 candle of 30 minutes or 1 hour) closes at 15:30
  (`cross_scan.closed_bars`). The cards' Candle menu and the "Moving averages (SMA)" menu (`components/Scalp/SmaPick.tsx`,
  `lib/scanSizes.ts`: Bot 1's own, 9 / 21, 20 / 50, 50 / 200, Custom numbers…; hidden for the colour reads, which use no
  SMA); Trend quality keeps the choice in its auto re-scan settings, the Cross scan per browser (`scalp.cross.sma`). First
  seen compares only saved scans with the same SMA periods.
  **Three SMAs with widening gaps** (owner's request; display only, nothing in the strategy, an alert or auto-arm reads
  it): `sma_third` (0 = off, any length 2-200 other than the scan's fast / slow ones; the three are put in order shortest to longest, so a bot on 9 / 50 picks 21 for 9 / 21 / 50; `chop_scan.pick_third`, 400 in plain words) on
  `POST /api/chop-scan/start`, `ChopAutoUpdate` and `chop_auto.AutoSettings` (an automatic pass drops a saved third that
  no longer fits the desk's periods). `analyze(..., third=)` adds `chop_scan.three_read` on the scan's own candles (from
  `wide` when the third SMA needs more days, `scan_download_days`): `stack3` BULLISH (fast > slow > third) / BEARISH /
  None, `gap_fast_slow_pct`, `gap_slow_third_pct`, `stack3_candles` and `widen_candles` (closed candles in a row on which
  both gaps were wider than the candle before, up to `WIDEN_MAX` 30). Not on the colour reads. The card's "Third SMA
  (longer)" menu (Off / 50 / 100 / 200 / custom), the "3 SMAs" column and the "Three SMAs" filter group (stacked up / down,
  widening for at least N candles; `chopFilter.chopPasses` `stack3` / `minWiden`). The 7 strong-trend checks are unchanged.
  **Skip today's first candles** (`skip_candles` 0-12, `MAX_SKIP_CANDLES`, on `POST /api/chop-scan/start`, `ChopAutoUpdate`
  and `chop_auto.AutoSettings`; the card's "Skip today's first candles" menu; owner's case: big opening candles made a stock
  that went sideways after them read as a strong trend at 10:00): `analyze(..., skip=)` (minutes, `skip_minutes`) counts
  today's crosses, phases, the strong-trend / fresh / candle checks (`_skipped` view) and VWAP (`_vwap_today(..., start_ts)`)
  only from 09:15 + N candles; a strong trend also needs the price to have moved `SKIP_MOVE_ATR` (1) × ATR its way since the
  last skipped candle's close (`_moved_since_skip`, note `trend_notes.skip`), on every size (`_crosses_on(..., skip_from)`).
  Rows carry `trend_skip_min`. The SMAs still run over the opening candles. Display only; nothing in the strategy reads it.
  **Backtests too** (old days; saved scans keep their own read): `skip_candles` on `TrendPickRule` (`read_at` →
  `analyze(skip=)`), `AutoArmRule` (`strong_read(..., skip=)`: same view, VWAP and 1-ATR gate), `trend_picks.AutoSettings`
  and the comparison (`TrendPickStart`, `AutoArmStart` (so the backtest queue too), `TrendCompareStart`, `TrendPicksAuto`);
  saved in the run's `scalp_pick`, restored on Resume (`scalp_picks.rule_from_dict`), shown in the run detail / PDF. The
  Backtest tab's forms have "Skip today's first candles" (`lib/skipFirst.ts`).
  **Candles: Normal / Heikin Ashi** (`candles` `NORMAL` default | `HA` on `POST /api/chop-scan/start`, `ChopAutoUpdate` and
  `chop_auto.AutoSettings`; the card's "Candles" menu, kept by the auto re-scan; `Status.candles`, saved in the run's
  `meta.candles`): with `HA`, `chop_scan.ha_bars` turns the closed candles of every size into Heikin Ashi ones
  (`heikin_ashi.candles`; high / low widened to hold the HA open / close) before `enrich`, so the SMAs, crosses, score,
  strong-trend checks, phases, trend age and gap are all the Heikin Ashi read (`analyze(..., ha=)`, `_crosses_on`,
  `scan_worker.chop_job`); the last price, VWAP and day move stay real. **With Heikin Ashi the trend is read from the candles, not the SMAs** (owner's request; `backend/ha_trend.py`,
  `analyze(..., ha=True)` and `auto_arm.strong_read(..., ha=True)`): each HA candle is green / red / doji (body under
  `DOJI_SHARE` 10 % of its range); a colour change between non-doji candles is a **flip** (`flips`: `crosses_today`,
  `last_cross_*`); Score `ha_trend.score` (TRENDING 0-2 flips with a long run since, CHOPPY 5+ flips or runs under 3
  candles); the side and Trend age are the current colour run across days (`side`, `run_read`), and the gap columns carry
  the run's move % and its widest (`gap_pct_by_minutes` / `gap_peak_pct_by_minutes`, so "fading" still works); Strong trend
  = `checks` (trend_mode `HEIKIN_ASHI`): `streak` (3+ candles of one colour), `nowick` (no wick against the trend on 2 of
  the last 3), `body` (last body ≥ 0.8 × the run's average), `structure` (higher lows / lower highs), `vwap` (real price),
  `move` (≥ 1 ATR), `clean` (no doji in the last 3); streak / nowick / VWAP required, 6 of 7; phases = runs of 5+ candles of
  one colour (`phases`, `summary`); Fresh = the colour turned within 3 candles, no wick against it, beyond VWAP, volume
  1.5x (`fresh`). Every size of the row reads it the same way, so filters, rank, alerts (`scalp_alerts.CHECK_WORDS`),
  auto-arm and the pick backtests follow; the card relabels Crosses → Colour flips and SMA gap % → Run move %. Scan & arm, the Most active card's "Read trend
  quality" and MCP keep normal candles. **Normal (colours)** (`candles` `COLOUR`, `chop_scan.COLOUR` / `candle_kind` / `read_flags`; owner's request): the
  real candles read the Heikin Ashi way (`analyze(..., colour=True)`: `ha_trend` flips, score, runs, the 7 candle checks,
  phases, fresh), rows carry `trend_mode` `COLOUR`; `scan_worker.chop_job` takes the kind string. The card's Candles menu
  (`chopFilter.CANDLE_KIND_OPTIONS`) has Normal (SMA cross) / Normal (colours) / Heikin Ashi, and its filters and columns
  are worded for the picked mode (`chopFilter.modeWords`, `crossRulesFor`, `trendKindsFor`: Crosses today → Colour flips
  today, SMA side → Candle colour, SMA gap → Run move, each with its own help line); the History tab's Across days view
  too. The backtests and Scan & arm keep NORMAL / HA. **The backtests can read Heikin Ashi too** (`candles` `NORMAL` default | `HA` on
  `TrendPickRule`, `AutoArmRule`, `trend_picks.AutoSettings` and the comparison; `POST /api/replay/trend-picks`,
  `/api/replay/auto-arm`, `/api/trend-picks/compare`, `PUT /api/trend-picks/auto`; the Backtest tab's "Candles" menus):
  `trend_picks.read_at` passes `ha=` to `analyze` and `auto_arm.strong_read(..., ha=)` runs `ha_bars` before `enrich`, so
  the picks and arm minutes are the Heikin Ashi read; the bot then trades real prices with its own settings. Saved in the
  run's `scalp_pick` (`candles`) and shown in the run detail / PDF ("Trend candle … Heikin Ashi"). The live auto-arm
  follows the Trend quality scan it reads, so it is Heikin Ashi when the card's Candles menu is. First seen compares only saved scans of the same kind.
  **Fresh trend** (`trend_strength.fresh`, rows carry `fresh` BULLISH / BEARISH / null, `fresh_checks`, `fresh_rvol`;
  the Trend column's "Fresh ↑ / ↓" chip, `ChopScan.FreshChip`, and Show → Fresh trend both / up / down; plain word
  `fresh_trend`): a trend that has only just started, where Strong trend only confirms an old one. All of: `cross` (the
  SMAs crossed within `FRESH_CANDLES` 3 closed candles), `vwap` (the real last close and the fast SMA on the trend's side
  of today's VWAP), `gap` (wider than on the candle before), `volume` (today since 09:15 at least `FRESH_MIN_RVOL` 1.5x
  the same minutes of the earlier sessions, `_opening_rvol`, needs 2), and on Heikin Ashi candles `colour` (the last HA
  candle is the trend's colour). From the owner's 30-day test (218 F&O stocks, 5-minute candles), where it was the best
  early signal with a small edge. Information only: nothing in the strategy, an alert or Scan & arm reads it.
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
  `phase_candles`, `phases` (all of today's), `strong_phases` and `phase_early` (`too_early`: CHOP only because
  fewer than `MIN_RUN` of today's candles have closed, e.g. before 10:30 on 15-minute candles; shown as "Not enough
  candles yet", left out of "Phase now: choppy", and as "Too early" on the terminal's Trend chip via `stock_regime`); the
  card's Phase column shows the phase now plus a 09:15-15:30 strip, and Show has "Phase now: strong / up / down / choppy". Information only.
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
  **Never-empty columns and Buyers %** (display only, `ChopScan.tsx`): with no Trend on size ticked, Rank / Trend age / SMA gap
  are read on the scan's own candle size (SMA side, either way), so they are filled on every scan. A **Buyers %** column
  (`buyers`, sortable) shows each stock's waiting buy vs sell orders from the latest Most active scan (`api.scalpActive`, re-read
  with each Trend quality result; "—" until that scan has read the stock; not on a saved scan). The Most active card shows the
  last Trend quality scan's Trend / **Trend score** / Crosses / Phase columns by itself in the Live view (`smaApi.chopScan()`,
  no new scan, refreshed with each Most active scan; "Clear trend columns" hides them until Read trend quality is pressed).
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
- `backend/auto_arm.py` – **Auto-arm from the Scalp scans** (owner's request; arming only, no order). **Live** (settings in
  `scalp_alerts.json` with the alerts: `arm_strong_on` / `arm_strong_bot`, `arm_pressure_on` / `arm_pressure_bot`,
  `arm_max_day` 10, all off by default; `PUT /api/scalp-alerts`; the Alerts tab's "Also arm them on Bot N" under each
  alert, `ScalpAlertsCard.ArmRow`): after every finished Trend quality pass (`main._chop_done` → `auto_arm_after("strong")`)
  and Most active scan (`routes_scalp._pressure_alert` → terminal `auto_arm_after("pressure")`) in market hours, the stocks
  the alert's thresholds pick (`scalp_alerts.strong_picks` / `pressure_picks(..., alerting=False)`: the alert's own on
  switch and once-a-day memory do not matter) are armed on that bot (`after_scan`; source chips "Auto-arm: Trend quality" /
  "Auto-arm: Most active"), each stock once a day per source (`_DONE`, memory only), at most `arm_max_day` a day per source,
  a stock already armed left alone, nothing unarmed; **price range** (`arm_price_min` / `arm_price_max` ₹, 0 = no bound, swapped when typed the wrong way; `auto_arm.price_ok`, `candidates`): only stocks whose scan price is inside it, and in the replay the tape's last close before the arm minute / the saved scan row's `ltp` (`AutoArmRule.price_min` / `price_max`, `active_hits(..., price_min, price_max)`, passed over the desk link). **PAPER bots by default**: a LIVE bot is refused with a note
  unless **Also arm a LIVE bot** (`arm_live`, off by default; `PUT /api/scalp-alerts` turning it on needs `confirm_live`,
  409 without; the card asks for LIVE typed, `ScalpAlertsCard.LiveArmRow`) is on: then at most `arm_live_max_day` (3, 1-10)
  stocks a day per source go on the LIVE bot, counted apart from the PAPER arms of the same day (`_DONE_LIVE`; owner's case: arms made while the bot was still PAPER used up the day's limit and the LIVE bot got none) (`_auto_arm_arm(..., live=True)` → `confirm_live`; one LIVE bot per stock
  still applies; the Telegram says LIVE). **Three SMAs stacked** (`arm_strong_stack`, `arm_strong_widen` 0-30 candles, `arm_strong_third` 50; Trend quality source only, off by default; owner's request): a strong up pick is armed only while SMA fast > slow > third on the scan's candles (down: the mirror), and with widen N > 0 both gaps widening N closed candles too (`scalp_alerts.stack_refusal` on the row's `chop_scan.three_read` fields, `auto_arm.candidates`); while it is on each Trend quality pass reads the third SMA by itself (`main._start_chop`). The alert is unchanged. The auto-arm replay reads the same (`AutoArmRule.stack` / `widen` / `third`, `strong_read(..., third=)` → `_three_at`, the form's "Three SMAs stacked"). The Alerts tab's `ScalpAlertsCard.StackCondition`. The bot trades them at its own quantity. Arms go through `main._scan_arm_arm` (`_auto_arm_remote`: the desk's bots over the link from the
  scanner; the desk arms its own bots; the backtest server its own unless **Arm on: the main desk's bots** (`arm_on` `here`
  default | `desk`, needs `SCANNER_LINK_TOKEN`; the card's choice is shown when the view's `desk_link` is true; owner's case:
  auto-arm ran on the backtest server and the main desk got nothing)). One Telegram per run that armed something (quiet
  channel). `GET /api/scalp-alerts` carries `auto_arm` (recent runs, today's arms). **Force order when armed** (`arm_strong_force` /
  `arm_pressure_force`, off by default, per source; the tick box under each "Also arm them on" row; owner's request): right
  after each new auto-arm, `auto_arm.after_scan`'s `force` hook (`main._auto_arm_force`) has the bot enter at once through
  `StrategyEngine.force_order(symbol, auto=True)`: candle-colour mode (`entry_mode = "COLOUR"`) on the last closed colour
  candle (`colour_force.side`, whatever `colour_force` says), any other mode on the SMA side; entry checks skipped like
  Force, market hours / cut-off / caps / loss lock / one LIVE bot per stock apply, a bot that is not RUNNING is never started
  (refused, the stock stays armed); entry reason `AUTO_ARM_FORCE`; the run's record carries `forced` (placed, note) and the
  Telegram says so. Over the link the desk's `POST /api/link/force` (`routes_link.desk_force`, token, `confirm_live` for a
  LIVE bot) runs the bot's own Force path with `auto`. With `arm_live` on, turning it on needs `confirm_live` (409 without).
  The auto-arm replay plays it (`AutoArmRule.force_strong` / `force_pressure`, `auto_arm.force_symbols`,
  `ReplayEngine.force_on_arm`: once per stock at its arm minute); the form's "Force order when armed" tick box. **Auto-arm replay** (`AutoArmRule`,
  kind `AUTOARM`, dispatched by `scalp_picks.picks_for_day`; `POST /api/replay/auto-arm`, the Scalp Backtest tab's
  `components/Scalp/AutoArmReplay.tsx`; practice money, works after hours): `picks_for_day` gives each stock the minute it
  would have been armed: Trend quality every `every_min` minutes from `first` to `last` (`strong_read`: the same
  `trend_strength.read` on the candles closed before that minute as the card's Trend column, about 16 ms a read; the tape is cut to the scan's own `WARMUP_DAYS` window so the morning volume check and ATR match History, on the main desk's SMA periods like the scan whichever bot replays, and Heikin Ashi compares VWAP with the last closed candle's real close) and Most
  active from that day's saved scans (`active_hits`, the minute after each stock's own read, `read_at`, not the pass's
  start, which armed stocks read late in a pass minutes early; older rows without `read_at` use the start; `saved_scans` on this server, else on the
  backtest server desk `GET /api/link/backtest/active-scans?day=&pct=&side=&min_value_cr=` behind `BACKTEST_LINK_TOKEN`,
  `main._auto_arm_scans`); the alert thresholds are the defaults. `ReplaySession` gives the engine `arm_windows`
  (`auto_arm.arm_windows`, the "as armed" path: a stock armed mid-day waits for a fresh signal) and starts the day at the
  first arm; speeds 1× (as live) to 300×. The Trend quality reads stop once the day's `max_day` picks are in (nothing armed later can make the list), each tape is cut to the day once, and `AutoArmRule.on_step` feeds `ReplaySession.finding` ("day 1 of 5 (…): reading 10:35 · found so far (2): DIVISLAB 09:45 ↑, …"; `on_found` reports each Trend quality pick as it is found, the day's Most active arms come from the saved scans), shown on the replay bar while the arm minutes are found; the day plays once they all are. The run is a Scalp-pick run (`scalp_pick.kind = AUTOARM`, picks with `at` per day); a Trend quality pick's `why` (`auto_arm.strong_why`) names the checks passed, the candle size and kind, morning rules before 10:00 and the checks missed, shown in the run's armed log (Why column, `BacktestRuns`) and PDF.
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
- `backend/stock_regime.py` – **Choppy or trending, per armed stock** (display only; the terminal's Stocks table "Trend"
  column, `StockCard.RegimeChip`, and the "Armed now: N up · N down · N choppy" line, `Header.RegimeSummary`): each
  `books[]` row of the state carries `regime` (`StrategyEngine._regime_of` → `stock_regime.cached`): on the bot's own candle
  size (the stock's own SMA periods and `candle_minutes`) from the 1-minute tape it already holds (`_tapes`, no Groww call),
  the Trend quality card's arithmetic: `score` TRENDING / MIXED / CHOPPY from today's closed-candle crosses
  (`chop_scan.score_for`) and the phase now (`trend_phases`: BULL / BEAR / CHOP, since when, its move). Worked out once per
  newly closed candle (cache keyed by engine, stock, SMAs, candle). Nothing in the strategy reads it; no order path.
- `backend/stop_handoff.py` – move a stock after a stop-loss exit (`stop_handoff` `OFF` default | `BACK` | `BOT2` | `BOT3`,
  `stop_handoff_loss` on by default = only when the stop closed at a loss before charges; per bot and per stock, PAPER and
  LIVE, replays too; Settings → 5. Stop-loss and target). Right after a stop closes a trade (`STOP_REASONS`: ATR, trailing,
  Supertrend, VWAP, plan, SMA-gap stop; never a target) `StrategyEngine._watch_stop` → `_stop_handoff` moves the stock through
  the bot's `handoff` (`recovery.handoff`; a replay's `_hand_off`): back to the bot it first came from (`_origin_bot`:
  `handoff_chain.moved()` live, the replayed bot in a backtest that plays the hand-offs; a stock that never moved stays), to
  Bot 2 (chop recipe, like a recovery) or Bot 3 (its own settings); it waits for its next cross there. LIVE only once Groww
  shows no shares; the usual hand-off rules apply. Logged as `STOP_EXIT` in `handoff_log`. No order is placed by it.
- `backend/handoff_log.py` – **Hand-off trail and trend at entry** (bookkeeping / display only; nothing in the strategy reads
  it). **Trend at entry**: `StrategyEngine._open` saves the stock's `stock_regime` read on its own candle
  (`_regime_at`) on the trade (`TradeLog.entry_regime`, JSON: phase, score, crosses, since, move, candle minutes; NULL on older
  rows; PAPER, LIVE, replays). The blotter's "Trend at entry" column and filter (with / against a trend, choppy, not read;
  `lib/trendAtEntry.ts`), the CSVs (`trend_at_entry`), the backtest PDF (trade column and "By trend at entry",
  `lib/backtestPdf.ts`) and the run detail's **By trend at entry** table (`replay.get_run` → `by_regime`,
  `components/Terminal/HandoffTrail.ByTrend`). **Hand-offs**: every hand-off attempt between the bots, moved or not, is a
  `HandoffLog` row (`record`: time on the engine's clock, mode, run, from / to bot, stock, kind `RECOVERY` / `VTURN_LOSS` →
  Bot 2, `BREAKOUT` → Bot 3, `FAST_MOVE` → Bot 5 / `FAST_BACK`, why, result, the closed trade, the trend then), written by
  `StrategyEngine._log_handoff` (`_hand_to_chop_bot`, `_watch_breakout`), `FastBot._record` (live; the same result kept at
  most once per `LOG_REPEAT_SEC` 10 min) and `ReplayEngine._fast_move`. A run's rows go with `delete_run` and its unfinished
  days with a resume / rewind (`_drop_unfinished_days`). `GET /api/handoffs?run_id=|day=&bot=&mode=`; the run detail lists
  the run's hand-offs (`get_run` → `handoff_trail`, `HandoffTable`, also in the PDF; each row with the sending bot's cut
  trade and the receiving bot's first trade after it, `handoff_log.with_pnl` → `from_trade` / `to_trade`, shown as "Bot 1
  P&L" / "Bot 5 P&L" columns with totals on the backtest basis) and the Stocks bar's "Hand-offs" button
  (`HandoffTrail`) a market day's.
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
- `backend/carry_scan.py` – **Carry-over** scan (read-only; the Scalp page's "Carry-over" tab,
  `components/Scalp/CarryScan.tsx`; no order path, nothing in the strategy reads it): did yesterday's SMA trend carry on
  today? Per stock, on the picked candle (`minutes` 1/3/5/10/15; `closed_bars`, `enrich` with the main desk's SMA periods,
  the SMAs run straight across days), from `WARMUP_DAYS` (7) of 1-minute candles: yesterday's closing side (`prev_side`
  LONG / SHORT), since when (`prev_since`, the run's start across days), close, SMA fast / slow ("the last day's moving
  average") and gap %; today's gap at the open (`gap_pct`, `gap_with`), `status` `CONTINUED` (no cross against it on
  today's closed candles) / `REVERSED` (`reversed_at`) / `BACK` (`back_at`), the last price's deviation from yesterday's
  closing SMA fast in ₹ and % (`dev_rs`, `dev_pct`; slow `dev_slow_pct`), the move since yesterday's close signed the
  trend's way (`move_with_pct`) and the best / worst move with / against it (`best_pct` / `worst_pct` and times). "Today" is
  the latest session in the download, so after hours it reads the last two. `CarryScanner` mirrors `MorningScanner` (one
  pass at a time, scanner process via `scan_worker.carry_job`, stopped by Pause scans). `POST /api/carry-scan/start`
  (symbols, minutes, force), `GET /api/carry-scan`. Filters: status, yesterday's side, gap with / against, deviation at
  least, stock lists; tick boxes, Arm / Arm ticked.
- `backend/turn_scan.py` – **Turning** scan (read-only; the Scalp page's "Turning" tab, `components/Scalp/TurnScan.tsx`; no
  order path, nothing in the strategy reads it): candles turning against the SMA trend before the SMAs cross (owner's ICICI
  Bank example: SMA 9 under SMA 21 since the morning, green candles from 14:15 closing back above SMA 9, the cross at 14:40).
  Per stock, on the picked candle (`minutes` 1/3/5/10/15; `cross_scan.closed_bars`, `enrich` with the main desk's SMA
  periods, `WARMUP_DAYS` 4), `read`: the trend (`trend` BULLISH / BEARISH from SMA fast vs slow on the last closed candle,
  since the last cross across days, `trend_candles`), the counter run on today's session (`counter_candles` ≥ `count`, default
  3, max 10; `rule` COLOUR / CLOSES / BOTH; with `need_fast`, on, the last close beyond SMA fast on the turn's side), the SMA
  gap now and at the turn, how much has closed and the minutes to the cross at that pace (`minutes_to_cross`), the move since
  the turn and today's move, plus `cross_scan.activity`. Soonest cross first (`sort_key`). `TurnScanner` mirrors
  `CarryScanner` (one pass at a time, scanner process via `scan_worker.turn_job`, stopped by Pause scans).
  `POST /api/turn-scan/start` (symbols, minutes, count, rule, need_fast, alert, force), `GET /api/turn-scan`. **Alert**
  (`alert`, the tab's "Telegram me new ones"): after a pass in market hours, the stocks newly turning (each stock and way once
  per IST day, `AlertMemory`, memory only) go out as one Telegram through the quiet-runner channel (`main._turn_done` →
  `_chop_alert`, `alert_text`). Filters: way, trend at least N candles, cross within N min, stock lists; tick boxes, Arm /
  Arm ticked. The bot's own "Early reversal entry" (`counter_candles.py`) trades the same picture when ticked.
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
- `backend/app/services/delivery.py` – **Delivery %** (read-only; the Scalp page's Most active and Carry-over tables, trailing
  "Deliv %" / "vs usual" columns, `components/Scalp/delivery.tsx` `useDelivery`): NSE's evening full bhavcopy
  (`sec_bhavdata_full_DDMMYYYY.csv` on archives.nseindia.com, about 18:00 IST, EQ series) gives each stock's traded and
  delivered shares; the rest was intraday. `report(symbols, days)` reads the latest `days` (5, max 10) published sessions
  (weekends / holidays / not-yet-published days walked back, `MAX_LOOKBACK` 21) and gives the latest day's %, shares and
  its change against the average of the sessions before. Each day's file is kept in memory and next to the desk DB
  (`delivery_DDMMYYYY.csv`, last `KEEP_FILES` 15); a missing day is asked again after `MISS_RETRY_SEC` (30 min). Desk
  `GET /api/scalp/delivery?symbols=&days=`. End of day only (no intraday split during the session). Nothing in the
  strategy reads it; no order path.
- `backend/regime_study.py` – **Sideways test** (read-only research; the Scalp page's "Sideways test" tab,
  `components/Scalp/RegimeStudy.tsx`; no order path, nothing in the strategy reads it): does a "sideways" / "trending"
  reading hold? For past trading days (`trading_days`, 3-30) of the picked stocks (scan lists, up to `MAX_STOCKS` 200), at
  each `CHECK_TIMES` (10:00, 11:00, 12:00, 13:00, 14:00, 14:30 IST), `analyze_stock` reads on the bot's candle size
  (`candles.resample`, `indicators.enrich`, closed candles only) six signals over the `window` (30/60/90) minutes just
  finished: SMA crosses, SMA fast turns (slope over 2 candles changing sign, under `TURN_MIN_ATR` ignored), ADX 14,
  efficiency (|net| / path), candle overlap (intersection / union) and the SMA gap %, and the next window's outcome:
  `trended` (efficiency ≥ 0.5 and ≥ 1 ATR, the trend_phases rule), next crosses, move in ATRs and `cross_pnl_pct` (follow
  the SMA side, flip on each cross, before charges). `summarize` buckets each signal (`BUCKETS`) against the base rate,
  `spread` = points between the buckets' trended % (buckets under `min_bucket` 30 checks left out), `verdict` strong ≥ 20 /
  some ≥ 10 / weak, sorted strongest first, `best`. `RegimeStudy` (one at a time) downloads each stock once with
  `fetch_frame` on `main._scan_broker()` after `groww_pressure.wait_for_bots`, keeps the last report in `regime_study.json`
  next to the SMA DB (`REGIME_STUDY_FILE`) and keeps the backtest server awake while it runs. `GET /api/regime-study`,
  `POST /api/regime-study/start` (symbols, days, minutes 1/3/5/10/15, window; refused when scans are paused / off, and on
  the desk in market hours unless `desk_market_hours_free`), `POST /api/regime-study/stop`. Learn lesson `sideways-test`.
  **Per stock** (`by_stock`, the page's first table; the clue analysis above sits under "Advanced"): each stock's trend
  score (% of its windows that `trended`), choppy score (% with 2+ next-window SMA crosses), SMA-follow result, efficiency,
  ADX and trended % per check time, and `suggest`: Bot 3 (trend score ≥ max(base + 10, 40), choppy ≤ 30, SMA-follow > 0),
  Bot 2 (choppy ≥ 40, or trend ≤ base − 10 with a loss), else Bot 1; under `MIN_STOCK_WINDOWS` (8) windows "not enough
  data". `why` gives every number against its rule (the row's ? button, `RegimeStudy.Why`). Arm / Arm ticked open the usual
  arm prompt (arming only; nothing is moved or unarmed).
- `backend/morning_study.py` – **Morning test** (read-only research; the Scalp page's "Morning test" tab,
  `components/Scalp/MorningStudy.tsx`; no order path, nothing in the strategy reads it): the owner's idea that a stock
  bullish from the morning keeps going (intraday, delivery, MTF) and one bearish from the morning is bought back up before
  the close. For past trading days (`days` 3-30, `regime_study.trading_days`) of the picked stocks (scan lists, up to
  `MAX_STOCKS` 200), `analyze_stock` labels each morning (09:15 to `at`: 10:00 / 10:30 / 11:00 / 12:00, the owner's choice
  each run) `BULLISH` / `BEARISH` / `SIDEWAYS` (`label`: move from the open ≥ `min_move` % (0.5) its way, SMA fast vs slow on
  the last closed candle of `minutes` agreeing, morning efficiency ≥ `MIN_EFFICIENCY` 0.3) and measures from the price at
  `at`: the move to the close, the low / high after and when, the day's low / high times, the bounce from the day's low and
  give-back from the high, the next session's gap and close (`next_day_pct` only once it closed), and NSE's delivery % that
  day (`app.services.delivery.load_day`, skipped when unreadable). `summarize`: per label `_group` (closed-higher %, average /
  median move, bounce, % bouncing ≥ `BOUNCE_BIG` 1 %, half-hour low / high times, next-day gap and close, delivery and a
  closed-higher split at the median delivery), plain-words `answer` lines ("ended higher than at 10:30 on 7 of 10 days (70%)",
  a warning under 5 days) and `by_stock`. The tab has a "How to read these numbers" example, a per-stock table with sorting,
  search, "days at least", tick boxes, Arm / Arm ticked (arming only). `MorningStudy` mirrors
  `RegimeStudy` (one at a time, after `groww_pressure.wait_for_bots`, last report in `morning_study.json` next to the SMA DB,
  `MORNING_STUDY_FILE`, keeps the backtest server awake). `GET /api/morning-study`, `POST /api/morning-study/start` (symbols,
  days, at, minutes, min_move, delivery; same hours rule as the Sideways test), `POST /api/morning-study/stop`.
- `backend/second_candle.py` – **Second candle test** (read-only research; the Scalp page's "Second candle" tab,
  `components/Scalp/SecondCandle.tsx`; no order path, nothing in the strategy reads it): the owner's Most active routine
  measured on every stock and past day: sort by volume vs usual, read the day's Nth candle (`candle_no` 1-6, default 2;
  `minutes` 1/3/5/10/15, default 5), buy on green / sell on red at its close (the next candle's open; a doji or a body
  under `min_body_pct` is skipped). **Which way** (`side_rule`): `NTH` (default, that candle's colour), `MAJORITY` (the way most
  of candles 1..N went, `majority`; a tie is skipped, `no majority`) or `AGREE` (that candle's colour only when most agree,
  else `against the majority`); "most" counted by `majority_by` `COUNT` (green vs red) | `SIZE` (bodies added up). Rows
  carry `greens` / `reds` / `net_body`, the report `skipped_by`. **Up / down by** (`dir_rule`, the same `COLOUR` / `CLOSES` /
`BOTH` as the bot's `nth_dir_rule`, `nth_candle.candle_moves`; a candle that is neither is skipped as `no direction`).
**Heikin Ashi** (`ha`, the tab's tick box): colour, body and "most" read from the Heikin Ashi candles of the session
(`heikin_ashi.view`, like the bot's `ha_colours`); entry and every price stay real. **Which candle is best?** (the tab's third view, `components/Scalp/CandleCompare.tsx`, shared
  arithmetic in `secondCandleMath.ts`): with several candles in a run, each candle side by side (reached %, average with /
  against, held to the square-off, the best trailing stop, the best target + stop, the best stop and reverse, the best way per
  trade), each column's best boxed, a verdict naming the best candle, and each stock's best candle; follows every filter but
  the Candle one. Display only. Per stock and day (`analyze_day`, on the same cached 1-minute candles as the Strategy
  lab, `strategy_lab.load_tape`, `WARMUP_DAYS` 5): `rvol` at the signal candle's close (`active_picks._cum_at`) and the
  day's `rank` by it (`rank_days`; `top_n`, 0 = all, picks the rows the summary reads), `best_pts` / `best_at` (the most
  the price went with the trade, ₹ and %), `worst_pts` / `worst_at` (against), `hit` / `target_at` / `minutes_to_target`
  for `target_points` (₹10) and `against_before_target` (the stop a winner needed), `close_pts` at `square_off` (15:15),
  `trail` (₹ a trailing stop `trails` % of the entry behind the best price made, judged on the 1-minute candles, the stop
  in force when a minute starts tested on that minute: the cautious order) and `fav_min` / `adv_min` (the minute each ₹
  `LEVELS` 2-50 was first reached with / against the trade, so `target_stop_outcome` judges any target / stop pair later:
  target first, stop first (the same minute = the stop), else the square-off). `summarize`: hit rate, averages, by side,
  by rank bucket, by day, the trailing-stop table (`best_trail`), the target + stop table over `DEFAULT_STOPS`
  (`best_stop`), when the target / best / worst came per half hour (`by_half_hour`), plain-words `answer`; `by_stock` per
  stock. `SecondCandle` (one at a time, after `groww_pressure.wait_for_bots`, last report + rows in `second_candle.json`
  next to the SMA DB, `SECOND_CANDLE_FILE`, keeps the backtest server awake). `GET /api/second-candle[?rows=false]`,
  `POST /api/second-candle/start` (symbols, days 1-30, minutes, candle_no, top_n, target_points, square_off, trails,
  min_body_pct; same hours rule as the Sideways test), `POST /api/second-candle/stop`. The tab recomputes everything
  from the rows in the browser, so the filters (place by volume, side, day, reached, target / best time window, search,
  ticked only) change every number, and the target + stop test takes any level without a new run; sortable per
  stock-day and per-stock tables, CSV, Arm / Arm ticked (arming only).
  **Several candles in one run** (`Rule.candle_nos`, the tab's 1st-6th chips, `candle_nos` on the start body; empty = just
  `candle_no`): each candle is its own trade (`analyze_day(..., candle_no=)`, rows carry `candle_no`), ranked by volume vs
  usual within its own candle (`rank_days` keys on day and candle), `summarize` adds `by_candle`; the tab has a Candle filter, a
  "By candle" table and a Candle column. **Stop and reverse** (`rev` on each row, `_reverse_legs`, levels `REVERSE_STOPS`):
  for each stop the first trade reached, the other side entered at the stop price on that minute and followed from the next
  minute to the square-off ([entry minute, best, worst, square-off ₹, the minute each `LEVELS` target was reached, the minute
  the same stop went against it]); `reverse_outcome` / `stop_reverse_table` (`summarize` → `stop_reverse`, `best_reverse`) read
  it with the same target and stop, only for trades whose stop came first; the tab's "After the stop: take the reverse trade"
  table and "Reverse after stop" column. The tab reads level maps with either key form ("10.0" / "10", `levelMin`).
  **Per day (all stocks together)** (`components/Scalp/DayTotals.tsx`, display only): each day's filtered trades added up (with
  Top N, the day's N stocks), in ₹ a share or sized by shares / ₹ per trade (shares = ₹ ÷ entry): held to the square-off,
  the chosen target / stop, the same + reverse, the best trailing stop; the picked exit drives the running total, green / red
  days, best / worst day, biggest drop and most capital in a day; per day how many of its stocks reached the chosen target
  and how many before the stop ("3 of 5"), and a table of days by that count (0, 1, 2… and at least). Before charges.
  **How far the trades went** (`components/Scalp/MoveBars.tsx`, display only): bar charts from the filtered rows, in ₹ or %
  of the entry: % of trades whose best move reached at least each level, the same for the worst move, and % that closed
  at the square-off at least that much in profit / in loss. Top by volume and Place by volume also offer Top 1 / 2 / 3.
  **No stall while it runs**: the page polls `?rows=false` during a run and loads the rows once at the end; `GET
  /api/second-candle` sends `SecondCandle.view_json` (the rows encoded in a worker thread, cached per finished run), the
  report is saved off the loop, and `strategy_lab.load_tape` reads / writes / joins the cached day files in a thread.
- `backend/trend_hold.py` – **Trend hold test** (read-only research; the Scalp page's "Trend hold" tab, group 3 Test an idea,
  `components/Scalp/TrendHold.tsx`; no order path, nothing armed or read by the strategy; owner's request: does a strong
  trend / strong phase keep going to the end of the day?). Past days (From / To, at most `MAX_DAYS` 30 weekdays) of the
  picked stocks (scan lists, up to `MAX_STOCKS` 250) on the cached 1-minute candles (`strategy_lab.load_tape`), read on a
  bot's (1-5) SMA periods and candle (`minutes` 0 = the bot's) the way the Trend quality card reads them (`day_reads`: the
  7 checks of `trend_strength.read`, or `ha_trend.checks` on Heikin Ashi, and the phase now; the day is enriched once and
  each minute reads the candles closed by then, same answers as `auto_arm.strong_read`). Every `every_min` from `first`
  to `last`, `qualifies`: `kind` TREND (at least `min_checks` of 7 for the SMAs' side; `strong_only` = the card's Strong
  label too) / PHASE (BULL / BEAR, not too early, ≥ `phase_min` candles) / EITHER / BOTH, `side`, LTP inside
  `price_min`..`price_max`. Each stock once a day at its first qualifying read (at most `max_day` a day, `cap_days`),
  entered at that minute's open the trend's way with `qty` (0 = the bot's), held to `square_off` (`follow`: points, %,
  ₹ before / after charges, best / worst and when, `curve` = signed % per minute); the read keeps being taken: `lost_at`
  the first that no longer passes, `held_to_end`, `lost_pnl` (closing there). `summarize`: totals, by day, by checks
  passed, plain-words `answer`. `TrendHold` (one at a time, after `groww_pressure.wait_for_bots`, last run in
  `trend_hold.json` next to the SMA DB, `TREND_HOLD_FILE`, keeps the backtest server awake). `GET /api/trend-hold`,
  `POST /api/trend-hold/start` (same hours rule as the Second candle test), `POST /api/trend-hold/stop`,
  `GET /api/trend-hold/chart?symbol=&day=&start=&end=&seconds=` (the day's 1-minute candles; with `seconds` the bots'
  recorded per-second prices, `tick_store.between`, when there are any). The tab: summary cards, an average-move line
  (minutes after the pick, % or ₹, all / stayed strong / lost), running P&L by day, the clicked pick's price by minute or
  second with in / lost / best / worst / out marks, by-checks table, sortable picks table, filters, CSV, Arm / Arm ticked.
- `backend/first_candles.py` – **First candles** on the Most active card (read-only; `components/Scalp/FirstCandles.tsx`; no
  order path, nothing in the strategy reads it): "Read first candles" reads, for the stocks the card's filters show (up to
  200), the latest session's first N candles (`candle_no` 1-6, `minutes` 1/3/5/10/15, closed candles only) from the 1-minute
  download (`fetch_frame` on `_scan_broker()`, today plus `DOWNLOAD_DAYS` back for weekends): each candle's colour (G / R /
  D) and body %, greens / reds / bodies added up, and the call by the Second candle rule (`side_rule` NTH / MAJORITY /
  AGREE, `majority_by`, `min_body_pct`, `dir_rule` colour / closes / both, `dirs` U / D / 0; `second_candle.majority`; a
changed rule reads again at once, the same rule within 20 s returns the last read; `ha` reads Heikin Ashi candles of the
session like the bot's `ha_colours`; `ohlc` = each candle as drawn, real or HA, `_shapes`; `last_n` (3, 0-6) adds the
last N closed candles before the read time, `last_ohlc` / `last_at`, `_last`; `at` on the start body reads as of a saved
scan's time, the card passes the History scan's `as_of`), `matched` = candles 1..N that went the call's way, or why
  there is none (`waiting: candle N closes at HH:MM`, doji, tie, against the majority, small body). `FirstCandleScanner`
  mirrors the morning scan (one pass, paced, stopped by Pause scans and `POST /api/scan-stop/first`). `POST
  /api/first-candles/start`, `GET /api/first-candles`. The card adds First candles (tiny candles drawn to scale, body and wicks, one price scale per row at least 0.6 % of the
price, `FirstCandles.MiniCandles`; hover gives each candle's type (`candleType`: doji, marubozu, hammer, shooting star,
spinning top), body and range) / Matched / Call columns
  and a "First candles" filter (has a call / Buy / Sell).
- `backend/trend_picks.py` – **Strong-trend pick backtest** (practice money, no order path, nothing armed; the Scalp page's
  Backtest tab, `components/Scalp/TrendPickBacktest.tsx`): the owner's routine automated (Trend quality history at a time,
  strong bullish / bearish, top N, backtest). For each day of a replay range, at `pick_time` (09:20-14:30) every stock of the
  universe (scan lists, up to `MAX_UNIVERSE` 250) is read as the Trend quality card would have then (`read_at` →
  `chop_scan.analyze` on the 1-minute candles closed before that minute, `minutes` candles, the bot's SMA periods); the
  `STRONG_BULLISH` / `STRONG_BEARISH` ones in `direction` (BULLISH / BEARISH / BOTH) ranked by trend score, then the day's
  move, then the SMA gap; the top `top_n` (1-20) are the day's picks (`picks_for_day`, `TrendPickRule` kind `TREND`;
  `scalp_picks.picks_for_day` hands it over, read in a worker thread). `ReplaySession`'s Scalp-pick mode trades only them on
  the chosen bot's replay player and settings (`bot` 1-4, refused while LIVE) from the pick minute to the square-off; picks
  saved on the run (`scalp_pick`, `kind: TREND`). `POST /api/replay/trend-picks` (date, end_date, universe, pick_time, top_n,
  direction, minutes, bot, speed, `play_handoffs`; `main._start_trend_picks`). **Hand-offs played** (`play_handoffs`, on by
  default like the replay bar's "Play hand-offs (Bot 2 / Bot 3)"; the tab's tick box, the daily run's and the comparison's
  too): the run's settings carry `play_handoffs`, so the chosen bot's own hand-offs (recovery, losing V-turn, choppy after
  losses, stop-loss → Bot 2; breakout / strong trend → Bot 3) play inside the run on that bot's settings
  (`backend/handoff_chain.py`); off = the old way. **Daily run** (`AutoSettings`, `trend_picks_auto.json` next to
  the SMA DB, `TREND_PICKS_AUTO_FILE`, off by default): `run` (started in `boot_engine` and `_boot_backtest_server`,
  `main._start_trend_picks_loop`, every `CHECK_SEC` 60 s) once each weekday after `run_at` (15:31-23:59, default 15:45)
  backtests that day's picks (`main._trend_picks_daily`, full speed, waits while that bot's player is busy; `last_day`,
  `last_run_id`, `last_note`). `GET/PUT /api/trend-picks/auto`. To paper-trade the picks live during the day, Scan & arm.
  **Inside a time range** (owner's request; `trend_picks.parse_window`, `PICK_FROM` 09:15 to `PICK_TO` 15:25, `until`
  09:20-15:30 after the pick time, 15:30 = the close): `until` on `TrendPickRule`, `ActivePickRule`, `AutoArmRule`,
  `trend_picks.AutoSettings` and the comparison (`POST /api/replay/trend-picks`, `/api/replay/active-picks`,
  `/api/replay/auto-arm` (last arm time before it, arm times up to 15:25), `/api/trend-picks/compare` (every time before
  it), `PUT /api/trend-picks/auto`). `ReplaySession._day_end` ends each replayed day at the rule's `until` (positions squared
  off there as `EOD_SQUARE_OFF`, like 15:30). The "Pick at" / "Trade until" menus (`lib/pickWindow.ts`, quarter hours) on
  the Backtest tab, the Most active Backtest view, both comparisons and the auto-arm replay; saved in the run's `scalp_pick`
  and shown in the run detail / PDF ("Trade until").
  **Compare pick times** (`backend/trend_compare.py`, the tab's "Compare pick times" panel,
  `components/Scalp/TrendPickCompare.tsx`): 2-8 pick times (09:20-14:30) played one after another as ordinary strong-trend
  pick runs with the same days, stocks, bot, settings, direction, top N and candle (`play`: waits while the bot's player is
  busy, a refused time is `SKIPPED`, a replay that takes the player marks it `INTERRUPTED`; `Comparer`, one at a time,
  keeps the backtest server awake), then side by side (`summarize`: P&L before / after charges, trades, win %, green days,
  per day, best / worst day, biggest drop of the running total on gross, days each time did best, stocks picked, rank by
  gross then green days, plain-words `answer`, a warning when the bot's settings changed between the runs or under 10
  days). Saved in `trend_compare.json` next to the SMA DB (`TREND_COMPARE_FILE`, last 20; a comparison playing at a restart
  becomes `INTERRUPTED`). `POST /api/trend-picks/compare`, `GET /api/trend-picks/compare[/{id}]`,
  `POST /api/trend-picks/compare/stop`, `DELETE /api/trend-picks/compare/{id}` (the runs stay in Backtests).
- `backend/backtest_queue.py` – **Backtest queue and daily schedule** (practice money, no order path, nothing armed;
  owner's case: two auto-arm backtests played at once froze the backtest server). Under the auto-arm replay form
  (`components/Scalp/BacktestQueue.tsx`, `AutoArmReplay.form` builds the same body as Start). **Queue**: each item is one
  auto-arm start body (`AutoArmStart`: bot, days, source, thresholds), added per ticked bot (`POST /api/backtest-queue`,
  at most `MAX_QUEUED` 30 waiting); `Runner.step` (every `CHECK_SEC` 10 s, `main._start_backtest_queue`, desk and backtest
  server, never the scanner) starts the first waiting one through `replay_auto_arm` at full speed only while no bot's
  replay player is LOADING / PLAYING / PAUSED, follows it to DONE / FAILED / STOPPED (or INTERRUPTED after a restart once
  `RESTART_GRACE_SEC` passed without the auto-resume), and a refused start is FAILED with the reason and the queue moves
  on. Pause / resume (`PUT /api/backtest-queue/pause`), remove a waiting or finished item (`DELETE …/{id}`; a playing one
  is stopped on its replay bar), `POST …/clear`. **Schedule** (`PUT /api/backtest-queue/schedule`: `on`, `at` 15:31-23:59
  IST, `bots` 1-4, `template` = the form's settings, checked like a start): weekdays once after `at`, today's auto-arm
  backtest per ticked bot is queued (`due`, `schedule_today`, `last_day` / `last_note`). The queue keeps the backtest server
  awake (`runner.busy`); the desk wakes it for the schedule (`wake_loop`, BACKTEST_URL + BACKTEST_LINK_TOKEN on the desk:
  from 15:31, then at the time `GET /api/link/backtest/queue-due` answers). Kept in `backtest_queue.json` next to the SMA DB
  (`BACKTEST_QUEUE_FILE`). `GET /api/backtest-queue`.
- `backend/active_picks.py` – **Most active pick backtest** (practice money, no order path, nothing armed; the Most active
  card's fourth view "Backtest", `components/Scalp/ActivePickBacktest.tsx`): the strong-trend pick backtest done the Most
  active way. For each day of a replay range, at `pick_time` every stock of the universe (scan lists, up to `MAX_UNIVERSE`
  250) is read from its 1-minute candles closed before that minute as the card would have (`read_at`: move from the previous
  close, money traded so far from the per-minute volume × typical price, volume vs the same minutes of the earlier sessions
  in the download (`rvol`, needs `MIN_SESSIONS`; Groww's volume is the running session total), distance from today's VWAP,
  day range, speed over `SPEED_MIN` 10 minutes, bias LONG / SHORT like the live card); stocks under `min_value_cr` are
  dropped, `bias` keeps BOTH (LONG and SHORT) / LONG / SHORT / ANY, ranked by `rank` (`RANK_KEY`: value, rvol, change,
  vwap, speed, range; the card's buyers-vs-sellers column is left out, past order books are not kept); the top `top_n`
  (1-20) are the day's picks (`ActivePickRule` kind `ACTIVE`, dispatched by `scalp_picks.picks_for_day`) and the chosen bot
  trades them from the pick minute (`main._start_trend_picks(..., active=)`). `POST /api/replay/active-picks` (date,
  end_date, universe, pick_time, top_n, rank, bias, min_value_cr, bot, speed, play_handoffs). The same view's **Compare pick
  times** is `trend_compare` with `source: ACTIVE` (+ rank / bias / min_value_cr; `TrendPickCompare` `active` prop lists only
  its own source's comparisons).
- `backend/saved_picks.py` – **Saved-scan backtest, one click** (practice money, no order path, nothing armed; the Most
  active card's Backtest view, top box, `components/Scalp/SavedScanBacktest.tsx`; owner's routine: History → the day's
  09:15 scan → sort by vol vs usual → top 24 → Bot 1 at qty 100): `POST /api/replay/saved-scans` (date / end_date, `at`
  09:15-15:00, `rank_by` one or more of `COLUMNS` (rvol, value, buyers, sellers, imbalance, exec_buyers, exec_sellers,
  move, up, down, speed, vwap, range; several = average place, a stock missing a column is last in it, ties by money
  traded), `top_n` 1-50, `qty` (0 = the bot's own; `ReplayEngine.pick_qty` sets it as each picked stock's own qty),
  `side` ANY / BUYERS / SELLERS (waiting orders, one-sided books never pass), `min_value_cr`, `min_rvol`, price range,
  `until`, bot 1-4, `play_handoffs`). Before the run, per weekday, `choose_for_day` on the day's saved Most active scans
  (this server's, else the desk's over `/api/link/backtest/saved-picks`): the first in-session scan at or after `at` with
  at least `top_n` stocks passing the filters with every ranking column known (thinner ones skipped and named in each
  pick's `why`: "09:15 not available (09:15:10 (8 of 210 stocks usable)), so run from this scan"), the top N, traded from
  the minute after the latest `read_at` among them (kind `SAVED`, `SavedScanRule.chosen` saved in the run's `scalp_pick`
  with each day's `notes`, so Resume plays the same; picks read as Most active, `pick_sources`). The run detail / PDF
  list the scan used each day.
- `backend/strategy_lab.py` – **Strategy lab: which checks and exits make money?** (practice money, no order path,
  nothing armed; the Scalp page's "Strategy lab" tab, `components/Scalp/StrategyLab.tsx`): the owner's filter lab. One
  run (`StrategyLab.start`, one at a time, `POST /api/strategy-lab/start` with symbols / date / end_date / bot 1-4 /
  label; the same hours rule as the Sideways test; keeps the backtest server awake) reads each stock's 1-minute Groww
  candles for the range plus `WARMUP_DAYS` (5) once, kept on disk per day as gzip JSON (`load_tape`, `strategy_lab/candles/
  <SYMBOL>/<day>.json.gz` next to the SMA DB, `STRATEGY_LAB_DIR`; today is never cached), groups them into the bot's candle
  and works every indicator out once (`_build_series`: enrich, RSI, session VWAP, Bollinger, EMA, MACD, cumulative delta,
  Supertrend, SMA 9 slope, per-row counter-candle and CVD-divergence flags), then `analyze_stock` keeps every SMA cross
  on closed candles of the asked days as a **base trade** (entry at the open of the candle after the cross, 09:20 to the
  bot's entry cut-off, the stock's own settings via `_cfg_for`) with every rule pre-answered: `checks` (each entry check
  at the signal candle, `ENTRY_CHECKS`: VWAP, volume, density, RSI, Bollinger, SMA gap, candle direction, EMA, MACD, CVD,
  Supertrend, level, ADX), `exits` (each exit rule's first firing candle after the entry and its fill at the next
  candle's open, `EXIT_RULES`: cross, Bollinger band / middle, Supertrend, EMA, MACD, CVD divergence, counter candles,
  slow SMA, SMA 9 turn / bend / V-turn (the bot's own `slope_exit.judge` / `judge_vturn`), deceleration guard
  (`decel_guard.early_warning`)), `stops` (ATR, TSL, Supertrend, VWAP on the 1-minute candles' high / low, the stop in
  force when a minute starts judged on that minute, trailing after: the cautious order), `targets` (TSL target, pivot R1
  / R2) and the square-off. The base trades are kept per run (`strategy_lab/<id>.json.gz`, meta in `jobs.json`, last
  `KEEP_JOBS` 20). `evaluate(trades, choice)` (`POST /api/strategy-lab/{id}/evaluate`, `Choice`: entries, exits, stop,
  targets, entry_from / entry_to, qty 0 = the bot's) is arithmetic on that table: a trade a chosen check refused is
  skipped, one that starts while the previous kept trade on that stock is open is skipped (crosses only open trades while
  flat), the trade ends at the earliest of its chosen exits / stop / targets / square-off (same minute: stop, target,
  exit), P&L before charges and after (`charges.calculate_charges`), per stock, per day (running total), by exit reason,
  skipped counts, `per_stock_day` (each stock on each day: the By day rows open into their stocks and the By stock rows into their days, Open all / Close all), the trades (`MAX_TRADES_OUT`), and `settings` = the BotConfig fields of that choice
  (`settings_for_choice`). **Capital** (`capital`, `out["capital"]`; display only): each kept trade's full value
  (qty × entry price), the most in use at one moment (`peak_value`: trades open together add up, one closing at a minute
  frees its money before another opens then; `peak_at`, `peak_open`), the margin an intraday account blocks for it
  (`peak_margin` = `Choice.margin_pct`, default `DEFAULT_MARGIN_PCT` 20 = 5×), the return on both, the largest / average
  trade and each day's peak; the tab's Capital tiles and "Margin %" box. **Ticked stocks** (`Choice.symbols`, empty = every
  stock; `out["universe"]` = every stock of the run with its signals, `skipped.unticked`): the result's **Stocks** tab
  (`StrategyLab.StockPicker`: tick boxes, Tick all / Untick all / Invert / Only winners / losers / with trades / armed now /
  Top N by P&L, search, sortable; each stock's P&L with the same rules and every stock in) keeps only the ticked stocks in
  every number, "Backtest this in the replay" replays only them, and **Arm ticked** opens the usual Arm ticked prompt.
  **Backtest this in the replay** (`POST /api/strategy-lab/{id}/apply`, `StrategyLabApply`: the choice plus `reset`,
  `stop_replay`, `confirm_live`, `to_bot` 1-5 (none = the run's bot); then `replayStart` on the run's or ticked stocks and days; **Apply to bot** (a bot picker next to it) sends `to_bot` and starts no replay, so the bot trades the result on its next signals, a LIVE bot after the page asks): the switches of
  `settings_for_choice` go on the run's bot through `_import_desk` (dry run first, Save's checks; Trade list, PAPER/LIVE,
  name and per-stock settings stay); with **Reset the bot's other settings first** (`reset`, the tick box next to the
  button) every other strategy field goes back to its default (`strategy_defaults`) and the lab's numbers are put on
  (`numbers_for_bot`: SMAs, ATR, quantity, candle, cut-off / square-off, filter ranges, TSL / VWAP / decel numbers,
  `entry_mode` SMA), so the replay trades only what the lab tested. A backtest already playing on that bot is stopped only
  with `stop_replay` (the page asks), else 409. **Newer Settings rules** (owner's request): entry checks `fresh`, `or_volume` / `or_vwap` / `or_spread` / `or_break`,
  `flow` (executed volume) and `small_colour`; exits `exit_candle` (the bot's exit candle, else the next size up,
  `EXIT_CANDLE_DEFAULTS`) and `flow_exit`; stops `SMART` (room at entry, no live re-size) and `PLAN` (ATR + staged plan);
  targets `atr` (the bot's multiple, else `DEFAULT_ATR_TARGET` 3), `day_level` and `net`, each read through the bot's own
  module (`_extra_checks`, `_exit_candle_hit`, `_flow_exit_hit`, `_smart_stop`, `_plan_stop`, `_net_target`) and written
  back by `settings_for_choice`; a run read before has no answer for them (`missing_rules`, result `missing`): greyed on
  the page ("read the days again") and skipped by `recommend`. `recommend` (`POST …/recommend`, `judge` `NET` | `ROC`): every rule toggled one at a time against the choice ("what
  each rule adds", `delta_net`, and the capital it needs `capital_changed` = peak margin with the return on it `roc_changed` = net ÷ that, %) and a greedy climb (`SEARCH_STEPS`, `SEARCH_MIN_TRADES`) to the best found, judged by net or by return on capital (`_score`); the page shows it above the rule ticks (`StrategyLab.BestFound`: Judge by, default return on capital, Use it).
  `choice_from_cfg` is the bot's own switches, the run's starting choice. **What it cannot play** (`not_played` / `not_played_all`, job `not_played`; owner's case: a backtest of the same bot
  took 50 trades where the lab kept 21): at Start `main._lab_not_played` lists every strategy field of the bot (and of the
  picked stocks' own settings, `stocks`) that differs from its default, is in use (`backtest_compare.in_use`, `_SWITCH_OF`)
  and is not one the lab knows (`played_fields`: `settings_for_choice` + `numbers_for_bot` + `_READ_PREFIXES`), plus an
  entry mode other than SMA; the page's "Will a backtest match this?" box (`StrategyLab.NotPlayed`). A new strategy setting
  shows there by itself until the lab plays it. **Auto-arm picks** (`StrategyLabStart.auto_arm`, `LabAutoArm`; owner's request: test on the stocks the Trend quality
  auto-arm picks, not the armed ones): the stock lists are what the rule reads; `StrategyLab._pick` loads every tape, then
  each day's picks with `auto_arm.picks_for_day` (TREND source, the rule built by `main._auto_arm_rule` like the auto-arm
  replay, unset values = the live auto-arm's Scalp alerts settings), only the picked stocks are read on their own days and
  `armed_trades` keeps the trades entered after each stock's arm minute (`armed_at`). The job carries `pick` (the rule) and
  `picks` (per day: symbol, at, side, why); the page's "Which stocks" switch (`components/Scalp/LabAutoArmPick.tsx`,
  "Stocks auto-armed each day"), and "Backtest this in the replay" then starts the auto-arm replay with the same rule. `GET /api/strategy-lab` (runs, `rules` with
  labels), `GET /api/strategy-lab/{id}` (the run's choice evaluated), `POST /api/strategy-lab/stop`, `DELETE
  /api/strategy-lab/{id}`. UI: lists / days / bot / name, Read the days, the runs, three rule boxes (Select all / Clear),
  stop and targets, entry hours, qty, the totals, "What does each rule add?" (table + "Use the best found"), the equity
  line, By stock / By day / By exit reason / Trades, CSV, and **Backtest this in the replay** (`saveConfigOn(bot,
  result.settings)` then `replayStart` on the run's stocks and days: the real bot proves it). What it is not: the bot.
  Candle-close resolution, fills at candle opens, no pullback / candle-extreme / entry-candle waits, gap mode, hand-offs,
  reviews, fast movers, news, slope candle or Heikin Ashi; only the SMA cross or the three-SMA stack opens trades. **Three-SMA entries** (`Choice.source` `CROSS` default | `STACK`, `stack_gap` one of `STACK_GAPS` 0-0.5 %; owner's request): `analyze_stock` also keeps `_stack_signals` (the bot's `stack_entry` read vectorised: lines in order, both gaps widening `stack_widen` candles, each run once per gap level at the first candle its smaller gap reached it; trades carry `source` and `gap_steps`; `tests/test_strategy_lab.py` checks the same candles as `stack_entry.call`), `evaluate` keeps only the chosen source's trades, `settings_for_choice` writes `stack_entry` / `stack_only` / `stack_min_gap`; a run read before has `stack_missing`. The page's "Opens trades on" box (`StrategyLab.EntrySource`). **Three-line exit** (`EXIT_RULES` `triple`, `_triple_candidate`: the bot's `triple_exit.crossed_against` on each candle from the entry, the fast / medium pair, else the two shortest of the stack lines, `_triple_pair`; `settings_for_choice` writes `triple_exit`, or `cross_exit` with two lines). "Enter between" goes in 5-minute steps to 10:30. `tests/test_strategy_lab.py`
  compares it with the ReplayEngine on one day: the same trades, minutes and reasons, P&L within the fill minute's walk.
- `backend/stock_rank.py` – **Stock ranking: which stocks does my strategy win on?** (practice money, no order path,
  nothing armed until the owner presses Arm; the Scalp page's Backtest tab, `components/Scalp/StockRanking.tsx`): the chosen
  bot's own strategy (`bot` 1-4, refused while LIVE; `play_handoffs`, on by default) backtested on the picked stocks (scan
  lists, up to `MAX_STOCKS` 250) over the same past days, `batch` (10-50, default 25) stocks per run, one run after another
  on the bot's replay player at full speed (`trend_compare.play`, shared: `key="symbols"`, `save=stock_rank.put`;
  `main.replay_start` with `symbols`; one job at a time, never next to a pick-time comparison; keeps the backtest server
  awake). `rank` reads every closed trade of the job's runs (`trades_for_runs`; with `earlier_days` also the bot's other
  FINISHED runs ending in that window, `earlier_runs`; a stock-day played twice counts once, the newest run's) and gives each
  stock trades, win %, green days, P&L before / after charges, per trade / day, average return per trade in % of qty × entry
  price, profit factor, best / worst trade and day, and a verdict (`STRONG` profit factor ≥ 1.5 and ≥ 55 % green days, `GOOD`
  ≥ 1.2, `WEAK`, `AVOID` lost money, `FEW` under `MIN_TRADES` 5 trades or `MIN_DAYS` 3 days), sorted by verdict then P&L;
  `summary` gives plain-words lines (what trading only the strong and good ones would have made). Jobs in `stock_rank.json`
  next to the SMA DB (`STOCK_RANK_FILE`, last 20; a job playing at a restart becomes `INTERRUPTED`). `POST /api/stock-rank`,
  `GET /api/stock-rank[/{id}?earlier_days=]`, `POST /api/stock-rank/stop`, `DELETE /api/stock-rank/{id}`. The table has
  sorting, Show (keep / per verdict), search, tick boxes, Arm / Arm ticked (the usual arm prompt).
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
  **Trend quality across days** (`components/Scalp/ChopAcrossDays.tsx`, top of the History tab; owner's request: the card's
  filters over every scan of a day, not one scan): tick up to `MAX_ACROSS_DAYS` (10) days, a candle size and Normal / Heikin
  Ashi; `GET /api/scan-history/across?days=&minutes=&candles=` (`scan_history.chop_rows`: every saved Trend quality run of
  those days, rows cut to `CHOP_KEEP`) and the card's own filters (`lib/chopFilter.ts` `chopPasses`, shared with
  `ChopScan`) list each stock-day that passed in at least one scan: first / last time it passed, scans passed of the day's,
  price then, move to the day's last scan, trend then; Table "One row per stock (all days)" joins the days, and **"Every
  scan: how the trend formed"** (owner's request: watch a stock rise from a weak to a strong trend) lists one row per
  stock per saved scan that passed: time, price, trend, trend score, checks passed (`trend_checks`), score, phase,
  crosses / colour flips, day move, speed and "What changed" since that stock's previous scan of the same candle that day
  (`changeText`: became / lost strong, phase, score, trend score ±10; green stronger, red weaker), every column sortable,
  "Only when it changed", a stock's "timeline" link follows it; CSV, Arm, Arm these. Read-only.
  **Most active history**: the desk's Most active scan (`app/services/active_stocks.py`) saves every finished pass
  (`ActiveScanner.on_done` → `routes_scalp.keep_active_scan`, set in the desk lifespan) into the same file as kind
  `active`; the terminal's History tab never lists it (`SCAN_KINDS`). Desk `GET /api/scalp/active/history[?day=]`,
  `GET /api/scalp/active/history/{id}`; **Stocks not read** (`ActiveScanner.missed`, snapshot `missed` [{symbol, why}], saved in the run's
  `meta.missed`; `MostActive.MissedList`: "Not read in this scan: N stocks" under the filters, and a searched stock that is not
  in the scan is named with why; older saved scans only have the `failed` count); the card's Live / History switch (day, scan time, ‹ ›, CSV; Rank by, Bias, filters
  and column sorting work on the saved scan). **Most active across days and scans** (`components/Scalp/ActiveAcrossDays.tsx`, top of the History view; like
  Trend quality's `ChopAcrossDays`): tick up to 10 days; terminal `GET /api/scan-history/across-active?days=`
  (`scan_history.active_rows`: every saved `active` run of those days, each row a list in `ACTIVE_KEEP` order) and its own
  filters (who leads + share %, bias, vol vs usual, money traded, day move, speed, price) give one row per stock per day
  (first / last time it passed, scans passed, price then, move to the day's last scan, buyers / vol / money then), one row
  per stock, or **Every scan: how it built up** (each scan's price, day move, buyers / sellers %, vol vs usual, money,
  distance from the average price, speed, bias and "What changed" since that stock's previous scan: bias, buyers / sellers
  taking over, buyers ±10 pts, vol ±0.5×; "Only when it changed"); CSV, Arm, Arm these. Read-only. Each row also has `speed_pct_min` (`add_speed`: % per minute since the
  previous scan, signed; none on the day's first scan or after a gap over `SPEED_MAX_GAP_MIN` 30 min), a Speed column,
  "Min speed" filter and Rank by "Fastest move now" / "Furthest from average price". The card also has tick boxes (Arm
  ticked / Watch news, `TickArm`, `onArmMany`), Price from / to and "Vol vs usual at least" filters, **Read trend quality**
  (runs the shared Trend quality scan, `smaApi.chopScanStart(..., force)`, on the stocks the filters show, up to 200, on
  1/3/5/15-minute candles, and adds Trend / Crosses / Phase columns plus a "Trend quality" filter: strong, trending, not
  choppy; it replaces the Trend quality tab's last result) and, on a saved scan, **Check accuracy** (the terminal's
  `POST /api/scan-history/{id}/check` on the same saved run; `scan_accuracy` kind `active`: the call is the row's bias,
  else its day move's sign; groups all / bias long / bias short / volume 2× usual; Call / 30 min / 1 hour / At close / Best
  columns and the per-group summary). Read-only.
  **Executed buy vs sell** (`active_stocks.flow_read` / `ActiveScanner._read_flow`; owner's request: the waiting orders
  are only intentions): after the quotes, each pass downloads today's 1-minute candles (`get_candles_window`, after
  `groww_pressure.wait_for_bots`) for the busiest `flow_top` (60, 10-250) rows by money traded and reads the executed buy %
  with `trade_flow.minutes` (recorded seconds by the tick rule, else the candle shape; forming minute dropped) over the
  last `flow_minutes` (15; `FLOW_MINUTES` 0 = since 09:15) closed minutes (`flow_buy`, `flow_from` / `flow_to`, `flow_src`)
  and the whole day (`flow_day_buy`). `flow_on` (on by default), saved in `AutoSettings`, `PUT /api/scalp/active/auto`
  (`flow_on`, `flow_minutes`, `flow_top`); the card's Auto scan row "Read executed buy vs sell for the busiest N stocks over
  …". Column "Executed buy vs sell" (`MostActive.FlowCell`), CSV, Rank by "Buyer/seller imbalance (executed)" (`SORTS`
  `flow`), filters "Executed: one side at least" and "Waiting vs executed" (same / opposite sides). Saved with every scan,
  so History and the auto-arm replay see it. Market data only.
  **Auto scan** (`active_stocks.AutoSettings`, `active_auto.json` next to the desk DB, `ACTIVE_AUTO_FILE`): the server runs
  the Most active scan by itself every `every_min` minutes (`EVERY_CHOICES` 1-60: 1, 2, 3, 4, 5, 10…, default 5) while NSE is open, no browser
  needed (`ActiveScanner.start_background`, checked every `CHECK_SEC` 15 s; a manual Scan now counts as a run; Pause scans
  and `SCANS=off` stop it). On by default on the desk and scanner (the old fixed 5-minute loop), off on the backtest
  server, where while on in market hours it keeps the machine awake. `GET /api/scalp/active` carries `auto`; `PUT
  /api/scalp/active/auto` (`on`, `every_min`). The card's "Auto scan … every N min" row (`MostActive.tsx`).
  **Which column finds the trend?** (`backend/active_study.py`, the card's third view, `components/Scalp/ActiveTrendTest.tsx`;
  read-only, saved scans only, no Groww call): for the last `days` (1-60) days with saved Most active scans, at each check
  time (09:20-14:30, default 10:00-14:00) the scan within `TOLERANCE_MIN` (10) minutes is read; each stock is bullish (up
  on the day) or bearish; within each day / time / side the stocks are ranked by each column (`PARAMS`: volume vs usual,
  day move, distance from the average price and buyer / seller imbalance signed its way, speed its way, day range, money
  traded, bias agrees (a yes / no split), plus `combined` = average place over the numeric ones; a column where every stock
  reads the same picks nobody) and the top / bottom `top` (3-50) are followed in later saved scans: about an hour later
  (`HOUR_MIN`) and the day's last scan from `CLOSE_FROM` 15:10 (none = no close result), signed the stock's way.
  `summarize` per time (and all) × side: each column's top / bottom "kept going" % and average move, `edge` (points above
  all stocks), `spread` (top − bottom), verdict strong ≥ 10 / some ≥ 5 / weak / none / few (< 30 readings), plain-words
  `answer`. Desk `GET /api/scalp/active/study?days=&times=&top=` (cached per choice and newest saved scan).
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
  `replay_pnl` (keyed by run and replayed minute; `delete_run` drops it). **Day high / low** (`replay_swings`, `get_run` → `day_swings`; display only): each replayed day's highest and lowest P&L (closed + open, before and after charges) with the minute and which came first (`first` HIGH / LOW), shown in the run detail's day-wise "Day high / low" column and the Armed log's day rows (`BacktestRuns.SwingText`). `GET /api/open-pnl/replays?bot=`
  lists a bot's runs, `GET /api/open-pnl/replay/{run_id}?day=` gives one replayed day.
  UI: the Stocks bar's "P&L log" button (`OpenPnlLog`: Market days / Replays switch, run and day
  pickers, chart with hover, table, CSV; opens on Replays while the desk shows a replay).
  **Per stock** (`open_pnl_log.by_stock`, `by_stock` on both routes): each traded stock's P&L at every logged minute
  (its trades closed by that minute, `gross_pnl` from the trade book, plus its open P&L in the point). The log's Stocks
  chips pick one or more: one line each plus their total, and the summary, table and CSV follow the pick.
  No order path; nothing in the strategy reads it.
- `backend/pnl_dashboard.py` – **P&L dashboard** (the desk's home page `/`, `components/PnlDashboard/`; read-only, no
  order path, nothing in the strategy reads it): when in the day is the P&L best, to pick a time to close everything. From
  the open P&L log (closed + open, `total_gross` / `total_net`) of backtest runs (`replay_pnl`, one or more runs; default the
  latest) or market days (`open_pnl`, desks 1-5 / research added up, From / To): each day on one 09:15-15:30 minute grid
  (`day_curve`: last point at or before each minute, 0 before the first), its high / low / close and when (`build_day`; a
  day with no P&L at all is left out), and per time of day every `step` (1/5/10/15/30) minutes across the days: average,
  median, worst, best, all days added, % above 0 and % beating the close (`by_time`); `best_exit` (highest average and
  highest median vs holding to the close), half-hour histograms of when the high / low came, totals, and the same per
  stock (`stock_table`, gross: closed trades by that minute + open P&L, `open_pnl_log.by_stock`). Filters: stocks (only
  their P&L, gross), basis, step, weekdays, and **Qty per trade** (`qty`, 0 = as traded; As traded / 100 / 500 / 1,000 /
  any number): every trade restated as that many shares so runs sized differently compare (`pnl_dashboard.per_qty` on
  each closed trade's `TradeLog.qty`, `scale_points` on each held stock's logged qty; the day curve is then the sum of
  the stocks, always before charges). The Runs list has Select all / Finished only. `GET /api/pnl-dashboard/options` (`open_pnl_log.all_replay_runs`, market
  days), `GET /api/pnl-dashboard?source=replay|market&runs=&desks=&start=&end=&stocks=&basis=&step=&weekdays=`. Charts are
  plain SVG (`PnlDashboard/charts.tsx`: time chart with min-max band, day high/low/close ranges, bars, stock bars); CSV.
  The ORB desk's old dashboard (bot control, manual order form, AI expert, account) moved unchanged to `/orb`
  (nav "ORB desk").
- `backend/fast_move.py` – fast-move alerts (off by default; alerts only, never trades; `fast_moves.json` next to the
  SMA DB, `FAST_MOVE_FILE`): **armed and held** (`on`, desk only, `fast_move.watcher` started in `boot_engine`): every
  `POLL_SEC` (5 s) while NSE is open, the bots' own last prices (`main._fast_move_prices`, no extra Groww call) of every
  armed / held stock on Bots 1-4; a move of at least `pct` (0.5) % within `minutes` (3) alerts. **Scan** (`scan_on`):
  after each Trend quality pass in market hours (`main._chop_done` → `_fast_move_scan`), rows whose 10-minute speed is at
  least `scan_speed` (0.10) %/min either way, one message. Each stock once per `cooldown_min` (15); Telegram through
  `alert_notifier.send`, a call too with `call` (`place_call`: market hours, 5-minute cooldown). `GET/PUT /api/fast-moves`;
  Settings → "Fast-move alerts" (`components/Settings/FastMoveAlerts.tsx`).
- `backend/scalp_alerts.py` – **Scalp Telegram alerts** (off by default; alerts only, never trades or arms; `scalp_alerts.json`
  next to the SMA DB, `SCALP_ALERTS_FILE`). **Strong trend** (`strong_on`, `strong_min_checks` 6 or 7, `strong_side` BOTH / UP /
  DOWN): after each finished Trend quality pass in market hours (`main._chop_done` → `after_trend_scan`) every row with `trend`
  STRONG_BULLISH / STRONG_BEARISH and that many `trend_checks` passed goes out with its price, the checks passed and the ones
  missed in plain words (`CHECK_WORDS`). **Buyers or sellers in control** (`pressure_on`, `pressure_pct` 50-95 default 70,
  `pressure_side` BOTH / BUY / SELL, `pressure_min_value_cr`, `pressure_min_rvol` vol vs usual at least ×, 0 = any, an unknown
  one fails a set floor, `scalp_alerts.rvol_ok`; also read by the live auto-arm and the auto-arm replay's `active_hits(...,
  min_rvol)` / `AutoArmRule.min_rvol`, passed over the desk link): after each finished Most active scan
  (`routes_scalp.keep_active_scan` → `_pressure_alert` → `after_active_scan`) every row whose `buy_share` (waiting buy orders ÷
  all waiting orders) is ≥ that % (buyers) or ≤ 100 − that % (sellers), with price, share, day move and volume vs usual. Each
  stock and side once per IST day per alert (`_SENT`, memory), at most `MAX_NAMES` 15 a message, through
  `alert_notifier.send_quiet`. **Judge by** (`pressure_basis` `WAITING` default | `EXECUTED` | `BOTH`, `pressure_flow_pct`
  60, 50-95; `scalp_alerts.control_side`): the waiting orders as above, the Most active row's executed buy % (`flow_buy`) at
  least that % bought (buyers) / sold (sellers), or both on the same side; a row without an executed read never passes
  EXECUTED / BOTH. The live auto-arm (`pressure_picks`) and the auto-arm replay (`auto_arm.active_hits(..., basis,
  flow_pct)`, `AutoArmRule.basis` / `flow_pct`, `AutoArmStart`, the desk link's active-scans query) read the same rule;
  scans saved before carry no executed read. The Alerts card's "Judge by" menu and "Executed share at least" box.
  `GET/PUT /api/scalp-alerts`; the Scalp page's Alerts tab (`components/Scalp/ScalpAlertsCard.tsx`) **Last check** (`last`, `scalp_alerts_log.json` next to the settings with `recent`, so a restart keeps the
  day's story): each scan in market hours notes per alert the stocks read, how many met the thresholds, how many were new
  and whether Telegram took the message (`_delivery` on the `DeliveryResult`; the card's `LastCheck` line, amber with the
  reason when it did not). A message Telegram refused is not marked sent for the day (`_mark(..., failed=)`), so the next
  scan tries again.
- `backend/pressure_study.py` – **Buyers / sellers test** (read-only; the Most active card's "Buyers / sellers test" view,
  `components/Scalp/PressureTest.tsx`; saved scans only, no Groww call, no order; nothing in the strategy reads it): the
  owner's idea that buyers (sellers) holding 75 % of the waiting orders send the price up (down). From the saved Most active
  scans of the last `days` (1-60): a **signal** is the scan in which a stock's `buy_share` first reaches `pct` % (55-95,
  default 75) for a side after a scan in which it did not (never the day's first scan; the same stock and side again only
  after `gap_min`, 30); the real `ltp` is read again `HORIZONS` 5 / 15 / 30 / 60 minutes later (the scan nearest, within
  `TOLERANCE_MIN` 4) and at the close (the day's last scan from 15:10), signed the idea's way, plus the best / worst within
  an hour. `_side_stats`: per horizon right %, average / median move, the **base rate** (how often any stock at every 3rd
  scan went that way, `base_moves`), `edge` = right − base and a verdict (strong ≥ 10 / some ≥ 5 / weak / none / few under
  `MIN_SIGNALS` 30); `by_threshold` runs the same at 60-90 %; plain-words `answer`. Desk
  `GET /api/scalp/active/pressure-test?days=&pct=&gap_min=` (cached per choice and newest saved scan).
  **Only the session counts** (`pressure_study.in_session`, `frozen`): scans taken outside 09:15-15:30 IST (a Scan now in
  the evening) are left out of the test and the day list, and so is a day on which under `FROZEN_SHARE` 5 % of the stocks'
  prices ever changed (a holiday: the auto scan has no holiday list); the day list reports them as `left_out`. Rows carry
  `trade_at` (the minute after the read), and the hold test enters at the open of the first 1-minute candle after the read
  (`hold_test.follow`: `entry_on` / `entry_at` / `entry_price`; the profit % counts from that price).
  **Day list** (`pressure_study.reached` / `reached_for_day`, the same view's first box, `components/Scalp/PressureDay.tsx`;
  desk `GET /api/scalp/active/pressure-day?day=&days=&pct=&move_pct=`): for one day (or the last `days`), every stock and side
  whose `buy_share` reached `pct` % (50-99, typed) in any saved scan: the first time (the stock's own order-book read time,
  `ActiveRow.read_at`, saved on each row since; older scans only have the scan's start `as_of`) and the last read still
  under it (`below_at`: it crossed between the two, the book is only read at each scan), the share then, the highest share and
  in how many scans, the price then, and what the saved `ltp` did afterwards: whether it moved `move_pct` % (0.05-20, typed)
  its way before the day's last scan (`moved`, when, minutes), the best / worst move and the move at the end. `_day_stats`:
  per side and overall how many moved (`correct_pct`), average minutes and best move; plain-words `answer`. The % boxes on
  the alerts card, this view and "Trade a saved scan" take any typed number.
  The day list also filters by first-reached time and price then (the plain-words lines and the three "% correct" cards are
  worked out in the browser from the rows inside those two filters, `PressureDay.dayStats` / `answerLines`, the same
  arithmetic as `_day_stats`; Who / Show only narrow the table), sorts every column, and has two read-only extras:
  **Read the news** (desk `GET /api/news-impact/day?date=&symbols=&ai=`, per day up to 40 stocks; the "News that day"
  column: overall sentiment / impact, same way as or against the buyers / sellers, the strongest headline with its time,
  "after" when it came out after the share was reached; a split of how many moved by news fit) and **Keep for N days**
  (`backend/hold_test.py`, terminal `POST /api/hold-test` (rows symbol / day / at / side / price, `days` 1-20,
  `target_pct`; same hours rule as the Second candle test): from the price then, the real 1-minute candles
  (`strategy_lab.load_tape`) of that day and the next N trading sessions: the session the high / low first touched the
  profit % (`day_no` 0 = same day, `on`, `hit_at`), the drawdown before it, best and end move; `summarize` gives reached %
  by each day and plain-words lines). Under it, **carry it as delivery / MTF?** (`PressureDay.CarryBox`, browser only): the
  buys that missed the profit the same day (`day_no` ≠ 0), how many reached it by each later trading day, average sessions
  and calendar days held, the dip on the way, where the misses ended, and a rough holding cost (delivery STT about 0.2 % +
  MTF interest at a typed % a year × calendar days); sellers are left out (a cash short cannot be carried overnight).
  No order path; nothing in the strategy reads it.
- `backend/scan_trade_test.py` – **Trade a saved scan** (arithmetic on real candles; no order, nothing armed; the Most active
  card's "Trade a saved scan" view, `components/Scalp/ScanTradeTest.tsx`): one saved Most active scan gives the day, the time
  and the stocks. `picks` (`pick` `PRESSURE` = buyers ≥ `pct` % buys / sellers ≥ `pct` % sells, `BIAS` = the row's bias, `MOVE`
  = up / down on the day; the busiest `top_n` by money traded, at most `MAX_STOCKS` 40); `simulate` enters each at the open of
  the first 1-minute candle after the scan and follows that day's candles (`strategy_lab.load_tape`, cached per day) three
  ways: **normal candles** (out at the open of the minute after the first closed `minutes` candle of the other colour; a
  candle already running at the entry is not counted, a doji is not a turn), **Heikin Ashi candles** (the same on
  `heikin_ashi.candles` colours; prices stay real) and **hold** to `square_off` (which closes all three). Per stock: exit
  time, reason, points, %, ₹ for `qty`, best / worst; `summarize`: totals, winners / losers, the best way, plain-words
  `answer`. Terminal `POST /api/scan-trade-test` (`run_id`, pick, pct, top_n, minutes, square_off, qty; same hours rule as the
  Second candle test; 404 when the scan is not saved on this server). The view's "Replay with this bot's settings" starts the
  ordinary replay (`/api/replay/start` with the traded stocks, the day, the entry minute and the bot), so a bot's own
  strategy on the same stocks shows in the Backtests tab.
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
  **Stop one scan** (each card's Stop, shown while it runs; `components/Scalp/StopScan.tsx`): terminal
  `POST /api/scan-stop/{cross|chop|morning|high52|carry|turn}` (`main.scan_stop` → that scanner's `stop`; the last finished
  rows stay) and desk `POST /api/scalp/active/stop` (`ActiveScanner.stop_pass`: the running Most active pass reads no more
  stocks and keeps the last finished scan). The Sideways and Morning tests have their own Stop.
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
  one failed; the deploy goes through `scripts/fly_deploy.sh` like the other two (see Deployment; job limit 30 min).
- `backend/backtest_server.py` – backtest server (`PALTRA_ROLE=backtest`, third Fly app `paltra-backtest`,
  `fly.backtest.toml`, setup in `BACKTEST.md`): replays only, on a performance machine that starts on the first visit
  (`auto_start_machines`) and stops itself when idle (`run`: no replay `LOADING` / `PLAYING` and no page request,
  `note_request` via the desk app's middleware, for `idle_minutes()`, default 30, 10-240, `backtest_server.json` next to
  the SMA DB → SIGTERM → exit → Fly stops the machine; `[[restart]] on-failure`). **Never orders**
  (`server_role.no_orders` covers scanner and backtest: `refuse_orders`, the LIVE switch 403, the desk broker and manual
  desk) and runs no alerts or ORB feed (`main._boot_backtest_server`). **Practice bots here** (owner's choice): Start bot
  (`_start_bot` → `main._start_backtest_bots`) runs the bots on today's live prices, PAPER only, and is refused (409)
  until this server's own Groww login is saved (Settings → Broker keys, `groww_client._read_desk_token`); the first
  Start starts every bot's loop (bot 1 in `_bt_bot1_task`, bots 2-5 in `_bot_tasks`) and the fast-mover hand-off, and
  calls `backtest_server.use_own_login` (now only a marker: its own login is already used first, below), so the desk's
  Groww rate limit is never used by them. No alert or call is sent (`StrategyEngine._alert` returns on `server_role.no_orders()`), and a running,
  paused or holding bot keeps the machine awake (`main._bot_running_here` busy check). **Scalp scans
  on demand** (`server_role.scans_enabled` is true here; no background most-active loop; the Trend quality auto re-scan,
  off by default, runs here too once turned on (`chop_autos.start_loop` in `_boot_backtest_server`), and while it or a
  scheduled Scan & arm preset is on the machine stays awake in market hours (`main._auto_scans_due` busy check)): they download with **this server's own Groww login** (`scan_session.OwnSession`, the token saved by its own
  Settings → Broker keys in its own trading.db via `groww_client._read_desk_token`; `main._scan_broker`), never the desk's
  session, so a scan never shares the live bots' rate limit; without a login a scan says to log in here.
  Three scan workers (`CROSS_SCAN_CONCURRENCY` in `fly.backtest.toml`), and a running Cross / Trend quality / 09:30 pass keeps
  the machine awake like a playing replay (`backtest_server.add_busy_check`). The accuracy check runs here in market hours too
  (only the desk refuses it). Its key must not be the scanner server's: logging one key in on two servers can end the other's
  session. Copies
  every `BotConfig` row (1-5) from the desk (`main.export_bot_settings` → desk `GET /api/link/backtest/settings` →
  `main.import_bot_settings`, PAPER forced) only on "Sync from desk" (refused while a replay plays; owner's choice: a
  deploy, restart or wake keeps this server's own saved settings unless "Copy from desk at every start" is ticked,
  `backtest_server.auto_sync`, off by default, `PUT /api/backtest/auto-sync`); never writes back. **Arms from the scanner**: with `SCANNER_LINK_TOKEN` on this server and `BACKTEST_URL` on the scanner, the scanner's
  Arm / Arm ticked has a Main desk / Backtest server switch (`useArming`, `ArmServerSwitch`; scanner link routes take
  `?server=backtest`, longer timeout since the call may start the machine). The desk-side link routes answer here too
  (bots are PAPER); an arm waits for the start-up copy (`backtest_server.wait_first_sync`) and is remembered
  (`note_scanner_arm`, `backtest_server.json`), so the start-up copy keeps it, with its own source and per-stock settings,
  on top of the desk's Trade list while it is still armed here (`import_bot_settings(keep=)`); Sync from desk replaces
  them (`clear_scanner_armed`). Replays (past candles) use **this server's own Groww login first** (owner's choice: `groww_client.desk_session_token`
  reads the token saved by its own Settings → Broker keys, `_read_desk_token`), so backtests never share the desk's rate
  limit; only with no login saved here (or an expired one) does it borrow the **desk's Groww session**: desk
  `GET /api/link/backtest/groww` every 10 min, memory only (`remote_token`). It never logs in with the desk's API key, so
  it cannot end the desk's session. Both link routes need `BACKTEST_LINK_TOKEN` (not the scanner's token), desk role only.
  **Market hours only** (`backtest_server.market_hours_only` / `stop_at`, off by default, saved in `backtest_server.json`;
  `PUT /api/backtest/hours`, the bar's "Market hours only, stop after"): outside 09:00 to `stop_at` (15:45) IST Mon-Fri
  (`after_hours`) page requests no longer keep it up; it stops once no backtest or scan has run for `AFTER_HOURS_MIN` (5)
  minutes (`idle_seconds_left`). **Scans copied to the desk**: every finished Cross scan / Trend quality / Most active pass
  and each accuracy check made here is also sent to desk `POST /api/link/backtest/scan-history` (`push_scan` / `push_check`
  from `main._save_scan` / `_save_check` and `routes_scalp._save_active`, in a worker thread, failures only logged), kept
  once in the desk's `scan_history.db` (`save_copy`; a check is matched by kind and time, `find`), so the desk's Scalp
  History tab and Most active History show them while this server is stopped. **Pull** (`backend/scan_sync.py`): scans
  made before the link was set, or while the desk was down, never arrived, so the desk can ask for them: the backtest
  server answers `GET /api/link/backtest/scan-export?days=` (its saved scans' ids, kind, time; `scan_sync.export_runs`)
  and `GET /api/link/backtest/scan-export/{id}` (one scan with rows and check), both behind `BACKTEST_LINK_TOKEN` and
  only on the backtest role (`routes_link._backtest_export_authorized`, lock-exempt via `token_path`); the desk's
  `POST /api/scan-history/pull?days=` (desk only; needs `BACKTEST_URL` and the token on the desk) runs `scan_sync.pull`,
  which copies each run the desk lacks (`find` by kind and time) with `save_copy` + `save_check`, meta `server:
  backtest`. The Scalp History tab's "Pull from backtest server" button (shown on the desk when `/api/role` has
  `backtest_url` and `backtest_link`) calls it for the last 30 days and reloads the list.
  **Backtests carry on after a restart** (owner's case: "Can't reach the trading engine" mid-backtest, each merge to `main`
  re-deploys this server): at boot `close_orphan_replay_rows(interrupted=True)` lists the runs it found playing
  (`replay.cut_short_at_boot`) and `main._auto_resume_replays` resumes each `INTERRUPTED` one on its own bot's player at full
  speed about 20 s later (`ReplaySession.resume`: the unfinished day replays, the result is as if it never stopped), at most
  `backtest_server.AUTO_RESUME_MAX` (3) times per run (`take_auto_resume`, counted in `backtest_server.json`, so a run that
  itself brings the server down cannot loop); `BACKTEST_AUTO_RESUME=off` turns it off; a run stopped by hand is left alone.
  `stop_now` saves `last_stop` and `note_boot` reads it once at boot, so the status carries `boot` (kind `idle` = its own
  stop | `restart` = a deploy or a crash), `auto_resume` and `resumed`; the bar's "Last start …" line shows them.
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
  **Lock** (`PaperWallet.locked`, off by default; `PUT /api/wallet/lock`, the wallet dialog's "Lock the wallet (no
  loans)", a lock on the chip): `paper_wallet.cover` raises `WalletLocked` instead of borrowing, so `_open` refuses a PAPER
  entry the free balance cannot cover (`SlCancelFailed`, no order); replays too (owner's choice: a backtest trades only the
  money there is, so its trades depend on the balance; a run started locked saves `wallet_locked` / `wallet_free`).
  **Auto quantity when arming** (`auto_qty`, off by default; `auto_qty_pct` 10, 0.1-100 % of the free balance per stock;
  `PUT /api/wallet/auto-qty`): `main._set_trade_symbol` → `_auto_size` sets a stock newly armed on a PAPER bot that uses
  the wallet to `paper_wallet.qty_for` (free × pct ÷ margin % ÷ price, whole shares; price from the bots' `_ltps`, else the
  desk's `news_lists.prices`; none = quantity kept), so every arm path follows it; `TradeSymbolUpdate.auto_qty` false (the
  Arm prompt's Change, Arm ticked, also over the link's `ArmRequest.auto_qty`) keeps the typed quantity. `GET
  /api/wallet/size?symbols=` is the Arm dialogs' preview (`ArmPrompt` `walletAuto`, `BulkArm` "Quantity from the wallet").
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
  day_gross`, `open_gross_total`, `kpis.gross_wins`, `kpis.gross_profit` / `gross_loss` (the day's winning and losing
  trades added up, the terminal's first P&L card "Gross profit / loss", `PnlMetricsRow`), `/api/bots` `gross_today`,
  chart markers `gross_pnl`). Charges stay recorded and shown on their own; the
  daily loss limit, stored `net_pnl` and Telegram alerts still use the net.
  **Backtest results switch** (`pnlBasis.backtestBasis` / `setBacktestBasis`, `sma.backtest.basis` per browser, default
  before charges; `components/ui/BacktestBasisSwitch.tsx`): one "Before charges / After charges" switch on the Backtests
  tab's top line turns every backtest result to that basis: the runs list and run detail (`smaApi.replayRuns` / `replayRun`
  → `runOnBasis`, refetched on a change), days, stocks, day high / low (`DaySwing` `_net` fields), the run PDF
  (`tradesOnBasis`, trades keep the server's figure in `net_after`; `downloadBacktestPdf({ basis })`), the Compare page and
  the P&L dashboard (both read and set the same switch). Live screens stay before charges.
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
  one bot, so it is judged on that bot's P&L alone. Once a replayed bot is HALTED (this guard, the daily loss limit, a
  panic) and holds nothing, the rest of that day is not played (`ReplaySession._halted_flat`, owner's request): the day
  finishes and the next one starts; its halt reason adds "rest of the replayed day skipped at HH:MM". A halted bot still
  holding a stock plays on, so its square-off is retried.
  **Profit safety net** (`all_bots_trail`, off by default; `all_bots_trail_start` ₹5,000 / `all_bots_trail_keep` 70 %,
  `all_bots_trail_start2` ₹10,000 (0 = none) / `all_bots_trail_keep2` 75 %, row 1, in `COPY_SKIP`; owner's request): once
  the day's combined P&L (closed + open, before charges) has reached the start, `trail_floor` = that % of the day's best
  (`note_peak`, per IST day in memory, `seed_peak` from the open P&L log after a restart); the P&L falling to it trips the
  same panic (`kind` "trail", "profit safety net hit"). Backtests keep each replayed day's best on the engine
  (`_day_peak`). `PUT /api/bots/guard` takes `trail*` and `trail_only` (target / square-off left alone); Settings → Bots,
  "All bots: loss quit and profit safety net" (`components/Settings/ProfitSafetyNet.tsx`, its loss box is `all_bots_stop`).
  **In % of capital** (`all_bots_unit` `RS` default | `PCT`; `all_bots_target_pct`, `all_bots_stop_pct`,
  `all_bots_trail_start_pct`, `all_bots_trail_start2_pct`; row 1, `COPY_SKIP`): with `PCT` the target, stop and both Profit
  Arm levels are that % of the practice wallet's money loaded (`all_bots_guard.capital`, `PaperWallet.funds`; none = off),
  live and in backtests (`level`, `limits(cfg, cap)`, `trail_rule(cfg, cap)`); the ₹ fields are kept. `GET /api/bots/guard`
  carries `capital`, `unit`, the `*_pct` fields and `effective` (the ₹ in force); both cards have a "₹ amount / % of capital"
  switch and show the other unit under each box (`components/Settings/CapitalLevel.tsx`). The Backtests runs list's
  last-but-one column is **Capital** (the run's `capital.peak_value` × the wallet's margin %, with its P&L in %) in place of
  Max DD (still in its tooltip).
- `backend/pnl_alerts.py` – all-bots P&L alerts (off by default; alerts only, never trades): `pnl_alert_profit` /
  `pnl_alert_loss` ₹ on `BotConfig` row 1 (0 = off), `pnl_alert_telegram` / `pnl_alert_call` (both on by default) and
  `pnl_alert_basis` (`GROSS` before charges, as the screens; `NET` after). While NSE is open `run` (started in
  `boot_engine`, every `POLL_SEC` 5 s) adds bots 1-4's P&L like the all-bots guard (`all_bots_guard.closed_by_bot` /
  `open_by_bot`, `net=` for the basis) and, once per level per IST day (`PnlAlerts.sent`, memory only), sends the text
  through `alert_notifier.send` (trade channel) and / or rings through `place_call` (market hours, 5-minute cooldown).
  The record keeps the level it alerted at (`already`), so a level the owner changes afterwards is a new level and alerts
  again the same day; the same level never alerts twice a day.
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
  the usual Save then applies it with every check), Delete. **Reset to defaults** (`POST /api/config/reset`, the
  research and `/api/bots/{n}/config/reset` forms; `main.strategy_defaults` = every `ConfigUpdate` field minus
  `COPY_SKIP` at its `BotConfig` column default, `GET /api/config/defaults`): sets them on the row like Copy settings
  (same checks; an open position still blocks a quantity change; a LIVE bot needs `confirm_live`, 409 without;
  `stock_settings: true` also clears every stock's own settings) and keeps a history entry. The Trade list, mode, name
  and the all-bots rows stay. Settings Save bar "Reset to defaults" (asks first; LIVE asks again). No order is placed.
- Copy bot settings (`POST /api/bots/copy-settings` in `backend/main.py`: `from_desk` / `to_desk` "1"-"4" or
  "research", `stock_settings`, `confirm_live`): replaces the destination's strategy settings (every `BotConfig`
  column but `COPY_SKIP`: id, chart symbol, Trade list and sources, PAPER/LIVE, name, per-stock settings, the 1s feed,
  trade-count bookkeeping and the all-bots guard on row 1) with the source's, checked like Save; per-stock settings
  replace the destination's only when ticked. A LIVE destination needs `confirm_live` (409 without); an open trade
  blocks a qty change as on Save. Settings → SMA strategy, "Copy settings from" (`components/Settings/CopySettings.tsx`).
  No order is placed.
- **Export / import settings** (`main.export_settings` / `import_settings`; Settings → SMA strategy, "Export / import
  settings", `components/Settings/SettingsTransfer.tsx`): `GET /api/settings/export?desks=1,…,5,research&fast=` gives one
  JSON file (`kind: "paltra-settings"`, version 1): each desk's strategy fields (`_strategy_fields`, so never `COPY_SKIP`),
  its per-stock settings, Trade list, name and mode (for reading only), plus the fast-mover card. `POST /api/settings/import`
  (`file`, `desks`, `stock_settings`, `trade_lists`, `fast_bot`, `confirm_live`) writes each chosen desk onto the same desk:
  values through `ConfigUpdate` and Save's clean-ups and checks, per-stock fields only from `STOCK_FIELDS`; every desk is
  checked first in a dry run (`_import_desk(dry=True)`), so one bad value writes nothing; the PAPER/LIVE switch and names are
  never written; a LIVE bot (or turning the fast-mover card on with a LIVE Bot 5) needs `confirm_live` (409 without); Trade
  lists only gain stocks (`Imported` source chip, cap applies) and a LIVE bot's list is never changed; an open position still
  blocks a quantity change. Kept in each desk's settings history. No order is placed.
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
  terminal Stocks bar "Arm log" button (`components/Terminal/ArmLog.tsx`). The Stocks box's search takes a pasted list too (`Header.tsx` `listTokens`: two or more names split by commas, spaces, semicolons or new lines): each is looked up (exact NSE symbol ticked, else the closest unticked), with Select all and **Arm ticked** (each armed with the bot's own settings through `setTradeSymbol`, source "Manual"; one LIVE confirm; the arm limit applies). **Replay as armed** (`ReplayStart.as_armed`,
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
  start/stop or panic. A third tool, `propose_settings_change` (`chat_actions.check_settings`), takes any
  strategy setting from plain words ("turn on VWAP on bot 3", "5-minute candles for SBIN"): field names and
  values are checked by the terminal's own `ConfigUpdate` when it runs in the process (bounds, choices,
  unknown names refused), never the bookkeeping fields (`SETTINGS_NEVER`: Trade list, PAPER/LIVE, name,
  auto-start, the all-bots / P&L-alert / news-entry rows); the card (`kind: "settings"`, labels from
  `lib/settingLabels.ts`, shared with the Settings page) applies through `saveConfigOn` / `saveStockConfigOn`.
  **What the assistant reads** (`app/services/sma_context.sma_facts`): besides bot 1's settings and today's
  trades, `bots[]` for every bot 1-5 and the research desk (status, mode, armed stocks, open positions, the
  per-stock `signal_lines`, settings differing from the defaults, and `stocks_today[]`: `cross_timeline` =
  every closed-candle SMA cross today on the bot's own candle size from `engine._frames`, with
  `what_happened`: entered / closed which trade, refused by which entry check (`filter_blocks`), after the
  cut-off, not on the Trade list then (`arm_log.windows`), already holding, cross exit off, gap mode, per-stock
  cap, or no recorded reason), `recent_trades[]` of the last 7 days (every bot) with `entry_reason` and a
  `strategy_id` into `strategies_used` (the settings saved at the trade's entry, deduped), `settings_catalog`
  (every changeable field: kind, bounds, choices, `FIELD_HELP`) and `settings_history` (bot 1's saves with
  what changed). **Only what the question needs is sent** (`app/services/chat_scope.py`, `classify`): the question
  (plus the user's previous turn, so a follow-up keeps its subject) is matched to topics (today, past, settings, howto,
  change, crosses, backtests, orb), the bots it names and the stock names in it, and `sma_facts(scope)`,
  `build_context(scope)` and `screen_facts(..., scope)` include only those sections: the strategy guide and bot 1's full
  settings for settings / how-to / change questions, today's trades and KPIs for today / crosses, the cross timelines
  (`stocks_today`) only for the bots asked about (named, else the bots holding the stocks named, else the page's bot,
  `screen.bot`) and only the stocks named (every armed stock with `MAX_CROSSES_BROAD` crosses when none is named), past
  trades and their strategies for yesterday / last-week questions (that stock's only when one is named), the settings
  catalog and the tool schemas only for a change, the ORB desk's trades / logs / mechanics only for an ORB question or a
  desk page, and the page's `settings` block only for a settings question. A question nothing matches reads today's
  trades and the page bot's crosses. `full: true` on `POST /api/chat/ask` (the widget's "Read everything" tick box,
  kept per browser) sends everything as before. `facts_summary` carries `scope`, `sections`, `stocks`, `facts_chars`
  and OpenAI's `usage` (input / output tokens), shown in the widget's "Read: …" line under each answer. The reply is
  Markdown, rendered by `components/Chat/ChatMarkdown.tsx` (no HTML from the model); the widget has a wide mode, copy
  and follow-up chips.
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
  `from_date` / `to_date`), items outside it are dropped (`in_window`), cached 6 h. **Old results stories** (`mark_stale`):
  Google dates a story by when the outlet put it up, so a results write-up re-posted weeks after the filing looked fresh
  (owner's HAVELLS case: Q2 filed 22 Oct, a re-post on 7 Nov). When a headline reads like a quarter's results
  (`is_results_story`), the stock's NSE results filings of the last `RESULTS_LOOKBACK_DAYS` (100) are read once
  (`results_filed`, `filing_times`); a story more than `STALE_AFTER_DAYS` (2) after the latest filing before it gets
  `stale`: no weight in `news_impact.overall`, never alerted, critical or a news entry, left out of the news check,
  listed last in the day report, shown as "old story · filed …". A results story is also one with no quarter word
  (`_RESULT_MOVE`: "Profit rises 58.8%", a weekly recap; owner's HINDALCO case: filed 6 Nov 13:46, TradingView's recap
  9 Nov 06:30). Every matched story carries `filed_at` (when the news really came out) and `late_min`; the screens show
  "NSE filed … · story Nh later", and the news check measures a late (not stale) one from the filing time (`story_at`). No key, no order path; nothing in the
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
- `backend/app/services/results.py` – **Quarterly results** (the News page's "Quarterly results" tab,
  `components/News/ResultsPanel.tsx`, `/news/?tab=quarterly`, next to the "Results calendar" tab of `results_calendar.py`; read-only, never trades, nothing in the strategy reads it; owner's
  idea from MyAlgoMate's results calendar: companies file with NSE first, before the news channels). **Calendar**
  (`calendar`, desk `GET /api/results/calendar?days=7`): NSE's board meetings (`api/corporate-board-meetings`, else
  `api/event-calendar`) whose purpose is financial results, per day with an F&O flag (`parse_calendar`). **Filed**
  (`filed`, `GET /api/results/filed`): NSE's latest results filings (`api/corporates-financial-results`) with the filing
  time (`parse_filed`). **Numbers** (`stock`, `GET /api/results/stock/{symbol}`; `many`, `POST /api/results/analyze` up to
  `MAX_ANALYZE` 40, one NSE session, `GAP_SEC` apart): NSE's results comparison (`api/results-comparision`) read loosely
  (`_pick`: NSE renames fields) into quarters (revenue, total income, PBT, net profit in ₹ crore from NSE's ₹ lakh
  `LAKH_PER_CRORE`, basic EPS, `quarter_label` Indian FY). **Verdict** (`analyze`): the newest quarter against the same
  quarter a year before and the one before: growth of sales, PBT and net profit, net margin change, a loss turned into a
  profit (or back) (`_profit_points`), weighted (`WEIGHTS`, `FULL_GROWTH` 25 % / `FULL_MARGIN` 3 pts = a full point) into
  `score` -1..1: POSITIVE / NEGATIVE ≥ 0.15, MIXED when sales and profit point opposite ways, impact HIGH ≥ 0.5 / MEDIUM ≥
  0.25 / LOW, and a plain `why` line. NSE down = an `error` message, never a crash. `GET /api/results/probe?symbol=` (the
  tab's "Check what NSE answers") shows each source's keys and first row, to spot a renamed field. Cached 30 min per stock,
  10 min for the lists.
- `backend/app/services/news_validation.py` – news check, "Did it move?" (the News page's tab, `components/News/NewsCheck.tsx`;
  read-only, never trades): for a day, each headline (the `NewsAlert` rows saved that day via `from_alert`, plus the news of
  typed symbols and, with `armed`, every armed / held stock, `news_impact.analyze(use_ai=False, day=)`; out after the previous
  weekday's 15:30, `for_day`) is set against that day's 1-minute candles from the terminal's `main.news_candles` (today's armed
  stocks from the bots' `_tapes`, else `fetch_frame` on `_scan_broker()` one at a time after `groww_pressure.wait_for_bots`).
  `measure`: news before 09:15 from the previous close (the gap counts), in the session from the open of its minute; the move
  15 / 30 / 60 min later and at the close (in market hours: so far, `so_far`), best / worst move and minutes to the best;
  signed the news' way; verdict `WITH` / `AGAINST` (closed `min_move` %, default 0.5, either way), `FADED`, `NO_EFFECT`,
  `NEUTRAL`, `AFTER_CLOSE`, `PENDING`, `NO_DATA`. `summarize`: per positive / negative × high / medium / low (hit rate, average
  signed move at 30 min and the close, best, worst, minutes to the best). At most `MAX_STOCKS` 40 stocks a day. Desk
  `GET /api/news-impact/validate?date=&end=&symbols=&alerts=&armed=&min_move=` (503 without the terminal in the process).
  **Date range** (`end`, up to `MAX_RANGE_DAYS` 31 calendar days; the tab's From / To and quick picks): each weekday in
  turn (`trading_days`), rows carry `day`; `main.news_candles(..., until=, cache=)` downloads each stock once for the
  whole range and later days reuse it. **Which words move the price?** (`frontend/src/lib/newsKeywords.ts`, display
  only, in the browser on the rows already read): typed keywords (`a|b` = either word, `DEFAULT_KEYWORDS` the usual
  Indian corporate-news words, kept per browser as `news.check.keywords`) matched as whole words in each title, and
  per word the plain move (not signed by the news) 15 / 30 / 60 min after and to the close, its size, % closed up, the
  average high / low after and how often the news was right; clicking a word filters the headline list.
- `backend/app/services/results_calendar.py` – **Results calendar** (the News page's "Results calendar" tab,
  `components/News/ResultsCalendar.tsx`; read-only, no key, no order path, nothing in the strategy reads it): NSE's own event
  calendar (`api/event-calendar`, else `api/corporate-board-meetings`; `parse_calendar`, results meetings only) joined with
  the market-wide corporate announcements of the days passed (`api/corporate-announcements` with dates; `results_filings`:
  the filing's subject decides, "Financial Result…", "Integrated Filing- Financial" or "Outcome of Board Meeting", never
  delay / intimation notices or a general update). `build`: one row per stock and meeting, `status` UPCOMING / TODAY /
  FILED / NOT_SEEN, the first filing within `FILED_WITHIN_DAYS` (2) of the meeting as `filed_at` (`filings` = how many that
  day), `session` BEFORE_OPEN / IN_MARKET / AFTER_CLOSE and `trades_on` (the first session whose prices can carry it); a
  results filing with no meeting in the range is added (`from_filing`, once per stock and day). Cached `CACHE_SEC` (15 min).
  Desk `GET /api/news-impact/results?from=&to=&scope=fno|mine|all&force=` (default 7 days back to 21 ahead, at most
  `MAX_RANGE_DAYS` 62; `mine` = the bots' armed / held stocks and the watch lists; rows carry `armed`). The tab: From / To,
  quick picks, stocks, Refresh from NSE, counts, Show filter, search, rows grouped by day, the filing link, News (opens the
  stock's News panel above) and Arm (Trade list only), CSV.
- `backend/app/services/close_orders.py` – **After the close** (the Scalp page's "After the close" tab, group 4 Look back,
  `components/Scalp/CloseOrders.tsx`, guide `scalp-after-close`; read-only, no order path, nothing in the strategy reads it):
  the owner's ALICON case (far more buy orders than sell orders waiting after the close): real demand, or short sellers
  buying back? Per stock (`CloseOrderScanner.scan`, one pass at a time, up to `MAX_STOCKS` 250, after
  `groww_pressure.wait_for_bots`, paced `CALL_GAP_SEC`): Groww's quote on the desk's session (`book`: total buy / sell
  quantity waiting, and the order count at the best five prices when a depth level carries `orders`), the day's 1-minute
  candles (`tape`: move to 14:30, the last hour, where it closed in the day's range, the end-of-day volume rush after 14:45
  vs the day's pace; `minute_volumes` turns Groww's running volume into per minute), NSE's delivery % for that day
  (`delivery.report`, only when its day is the scanned day, about 18:00), NSE's F&O open interest change (`oi_changes`,
  `api/live-analysis-oi-spurts-underlyings`, live) and NSE's short-selling file (`shorts_for`: `shortselling_DDMMYYYY.csv`,
  published the next trading day, matched by its trade date). `read` adds plain points: real demand (buy orders waiting ≥
  `BUY_RATIO_STRONG` 3× / 2× the sell orders, ≥ `BUY_VS_VOLUME` 5 % of the day's volume, closed at the top of the range,
  delivery ≥ 1.2× usual, OI up with the price) and short sellers buying back (fell to 14:30 then ≥ `LATE_RISE` 0.7 % in the
  last hour, end rush ≥ 2×, delivery ≤ 0.8× usual, OI down ≥ 2 % with the price up, short sold ≥ 2 % of volume, a late jump
  with few buyers left waiting); `verdict` DEMAND / SHORT_COVER (≥ `NAME_AT` 3 points and `LEAD_BY` 2 ahead) / MIXED /
  QUIET, `unusual_buy`, each point's reason (`demand_why` / `cover_why`) and what is not known yet (`missing`); `notes` say
  when delivery / the short file are not out or the market is still open. Each pass is saved in the scan history as kind
  `closebook` ("After the close"). Desk `GET /api/scalp/close-orders`, `POST /api/scalp/close-orders/scan` (`symbols`),
  `POST /api/scalp/close-orders/stop`, `GET /api/scalp/close-orders/history[?day=]`, `GET /api/scalp/close-orders/history/{id}`.
  The tab: Scan now / Stop, stock lists plus typed stocks, Show (unusual buy orders / each answer), search, saved scans,
  the table, a Why? row per stock, tick boxes, Arm / Arm ticked.
- **One colour per server** (display only; `frontend/src/components/ServerAccent.tsx`, `app/globals.css` "One colour per
  server"): the desk keeps violet; the backtest server is **blue** and the scanner **teal** (owner's choice, so the three
  are told apart at a glance). `ServerAccent` (in the root layout) reads `/api/role` once and puts `data-server="backtest"`
  / `"scanner"` on `<html>` (none on the desk), keeping it in `localStorage` (`orb.server`) so the layout's inline script
  paints it before the first load; the theme file swaps `--c-violet` / `--c-indigo` under that attribute (the brand
  gradient, the page glow and `accentViolet` follow) and remaps every hard-coded `violet-*` utility in use, both themes.
  `serverBadge` gives the "Backtest server" / "Scanner" chip next to the logo (Navbar) and the terminal title. Green / red
  never change. Add a new `violet-*` class and add its line to that block.
- **Accessibility basics** (`frontend/src/app/globals.css` "accessibility"; display only): text on a filled accent uses
  `text-on-accent` (`--c-on-accent`: near-black in the dark theme, white in the light one; never `text-white` on
  `bg-accentSky`), dark-theme `text-slate-500` is lifted to 5.3:1, one `:focus-visible` ring for every control, and
  `prefers-reduced-motion` stops animations. The Scalp tab bar (`ScalpTabs`) and `SettingsTabs` follow the tab pattern:
  `role="tablist"` per group, the open tab filled and the only one in the Tab order, arrow keys / Home / End move.
  The `danger` button is `bg-rose-600` (white text 4.7:1). Check a page with axe (WCAG 2 A / AA) in both themes.
  **Light theme**: the components were written for the dark ground, so `globals.css` remaps them under
  `[data-theme="light"]`: the slate scale (inverted), accent text, `hover:text-*` (hovering goes darker, never to
  white), faded accent text (`text-amber-400/80` etc., made solid), hard-coded hex text (`text-[#10B981]` → profit),
  and filled buttons with white text. A new `hover:text-*`, `text-<colour>-<shade>/<alpha>` or hex text class needs its
  light line there; a chart legend shows a user-picked colour as a swatch (`StrategyChart.LineKey`), not as text colour.
- **Settings page** (desk `/settings`, `app/settings/page.tsx`; display only, every control saves through the same routes as
  before): one tab at a time (Strategy, Bots, Alerts & news, Account), each tab with an icon, a one-line hint and a plain
  intro line. A header ("Settings", the Learn page link, "What do these words mean?" opening the plain-words panel with the
  settings terms), a dismissible "New here? Three things to set first" strip (broker keys, stop and daily loss, Telegram;
  `settings.starthere.v1` in the browser) and pill jump links. Every card is `components/Settings/SettingsCard.tsx`
  (`SettingsCard` / `CardHeader`: title, an On / Off `StatusChip`, a "Start here" / "Advanced" `TierTag`, one plain
  `summary` line always shown, the long explanation folded under "How it works", then the controls); the Account cards
  (broker keys, AI key, live data check, ORB risk) share the same frame. The strategy form (`StrategyConfigPanel`) opens
  with "Settings for" (shared / one stock, with an ⓘ), tags groups 1, 5, 6 "Start here" and 4, 7 "Advanced"
  (`GROUP_INTRO`), and shows a plain intro line inside each open group; the bot tabs show each bot's PAPER / LIVE chip and a
  running dot. Add a card by wrapping it in `SettingsCard` with a one-line `summary` and the explanation as `help`.
- **What do you want to trade with?** (`components/Settings/StylePicker.tsx`, top of the strategy form; display only, never
  changes or saves a setting): multi-select cards for the ways of trading (`STYLES`: SMA cross, SMA gap (band), Uptrend /
  downtrend, Candle colour & patterns, Reversals (SMA 9 turn), Indicator checks, Several bots (hand-offs), Ask me first), each
  with what it does, when it suits and an "in use" chip (`on(form)`). The form then shows only the basics (averages, candle
  size, how a trade opens, stop-loss, size and times) plus the ticked styles: parts of `StrategyConfigPanel` are wrapped in
  `<Only s="trend ind">` (shown while any listed style is ticked, `StyleCtx`; a `display: contents` wrapper, so layout is
  untouched), and groups 4 / 7 and their chips hide with their style. Uncheck all, Tick what is in use (`detectStyles`),
  Show every setting; kept per browser (`sma.settings.styles`; until chosen, the styles in use are ticked). A search shows
  everything; a style that is on but hidden is named with "Show them", and "What's switched on" always lists it. **Ask the
  assistant to set this up** opens the chat widget with the question typed in (`paltra:ask` window event, `ChatWidget`); the
  assistant still only proposes. Add a setting to the form inside the matching `<Only>` (or leave it bare to always show).
- **Scalp tabs grouped by use** (`app/scalp/page.tsx` `TAB_GROUPS` / `TABS` / `ScalpTabs`; display only): the tab bar is four
  boxes in the order of a trading day: 1 At the open (09:30 Morning, Carry-over), 2 During the day (Most active, Trend quality,
  Cross scan, Turning, 52-week high, Monitor), 3 Test an idea (Second candle, Backtest, Strategy lab, Sideways test, Morning
  test), 4 Look back (After the close, History, Alerts). Each tab carries `ask` (the question it answers), `use` (what it does, plain words) and
  `next` (what to do with the answer), shown in a strip under the tabs for the open tab; "Which one do I use?" lists every
  question → tab. Add a tab by adding its `TABS` entry with those three lines and a group.
- **Best to trade now** (`components/Scalp/BestNow.tsx`, top of the Most active card's Live view; display only, no order):
  one list joining three tabs for the stocks the card's filters show: busy (`rvol` ≥ `BUSY_RVOL` 1.5), trending or sideways
  (the card's own "Read trend quality" rows, `tq`; the button "Find the best stocks now" runs that read) and news (the
  still-valid medium / high NSE filings from `api.newsFeed`). "Good to trade" = busy and strong trend / TRENDING (top 8, with
  the reasons and Arm); `CHOPPY` ones are named under "Sideways, skip for now"; other stocks with news are listed.
- **Sideways starting set-up** (`StylePicker.SidewaysGuard`, under the style picker on Settings → Strategy; form-fill only):
  five existing settings with the form's value now and a tick each: ADX check at least 20, skip sideways candles (`use_sideways`), `max_trades_per_stock` 3,
  `counter_exit_count` 3 with `counter_need_fast`, and (optional, unticked) `chop_losses` 2. "Put the ticked ones in the
  form" sets them through the form's `turnOff` setter; nothing is saved until Save. No new setting or engine change.
- **What's new page** (desk `/whats-new`, nav "What's new", `components/WhatsNew/WhatsNewPage.tsx`; display only): every
  feature of Trend quality, Most active, the SMA terminal and Settings from `frontend/src/lib/features.ts` (`FEATURES`,
  dated by the IST day its PR was merged; analysed from the git history when the page was built), newest first, grouped
  by day: title (a link to it in the app), area, where it is, its purpose, the day and its PR on GitHub. Area cards
  (count, "N new"), search, area filter, New only, and **New = added in the last 1 / 3 / 7 / 14 / 30 days**
  (`whatsnew.days`, default 7, per browser). The same rule shows **New** next to a feature's control (`NewTag`, links to
  `/whats-new/#f-<id>`, which lights it up) and **"N new"** on the Scalp tabs, the Most active views, the Settings tabs
  and the strategy form's numbered groups (`NewCountChip`, matching each feature's link: `#<tab>`, `?view=<view>#active`,
  `strategyGroupOf`). The menu's What's new link shows how many new features this browser has not seen yet
  (`useUnseenCount`; opening the page marks them seen, `whatsnew.seen`). Links: Scalp tabs by hash (`/scalp/#trend`),
  Most active views by `?view=` (`MostActive` reads it), Settings by section or `#sma-…` anchor (the strategy form opens
  the group and, when the style picker hides the setting, shows every setting first).
- **Learn page** (desk `/learn`, `components/Learn/LearnPage.tsx`, nav "Learn"; display only): the app's own guides read
  inside the portal. Lessons are listed in `frontend/src/lib/lessons.ts` (title, topic, pages, a link to the setting it
  explains) and their files kept in `frontend/public/guides/` (served with the static site); add a lesson by dropping the
  file there and adding an entry. Topic filter, search, a reader with the PDF embedded, Open in a new tab, Download, and
  "Mark as done" (kept per browser). `/learn/?lesson=<id>` opens one. Lessons now: `sma9-slope` (Turn exit vs V-turn,
  linked from the Settings slope editor), `day-high-low` (the day high/low guard, linked from that setting,
  anchor `#sma-day-extreme`), `smart-stop` (the smart stop and its Advanced settings, worked on an AMBER short; why it
  closes in profit and why candle colour mode still exits on the other colour; linked from the smart stop switch, anchor
  `#sma-smart-stop`; module `19-smart-stop.js`), `buyers-sellers-strategies` (every waiting-orders / executed-volume entry and exit told apart, OR vs AND, recipes, the
  BSE short of 6 Nov worked through; linked from the Buyers & sellers overview card; module `20-buyers-sellers-strategies.js`), `sideways-test` (the Scalp page's Sideways test, linked from its card) and
  `most-active-columns` (every column of the Most active card's "Which column finds the trend?" test, linked from it),
  `buyers-sellers` (the buyers / sellers % column, its day list, test and alert, linked from the day list),
  `settings-entries` and `settings-exits` (the strategy settings' look-alike names told apart, with pictures: part 1 the
  signal, checks and how the order is placed; part 2 exits, stops, targets, flip / reverse, hand-offs, limits and scope;
  linked from the Settings page header "Similar names explained (guide)"; modules `17-settings-entries.js` /
  `18-settings-exits.js`, their own figures in `frontend/scripts/guides/figs-settings.js`),
  and the **Scalp page's beginner guides** (topic "Scalp", one per tab plus `scalp-start-here`: `scalp-monitor`,
  `scalp-cross-scan`, `scalp-morning-0930`, `scalp-turning`, `scalp-carry-over`, `scalp-trend-quality`,
  `scalp-52-week-high`, `scalp-most-active`, `scalp-second-candle`, `scalp-backtest`, `scalp-strategy-lab`,
  `scalp-morning-test`, `scalp-history`, `scalp-alerts`, `scalp-after-close`; `sideways-test` covers that tab). They are written as small
  JavaScript modules in `frontend/scripts/guides/lessons/` (shared pieces and drawn SVG figures in `frontend/scripts/guides/lib.js`)
  and rendered to the PDFs by `frontend/scripts/guides/build.js` (Playwright); edit the module and rebuild, then update the
  page count in `lessons.ts`. Every Scalp tab shows `components/Scalp/GuideBar.tsx` under its heading: "Guide (PDF)"
  opens that tab's lesson and "What do these words mean?" opens the plain-words panel, whose entries live in
  `frontend/src/lib/plainWords.ts` (term, meaning, example, why it matters; add a key there and list it in the tab's
  `terms`). The tab bar has a "Start here (guide)" link. Display only.
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
  for the owner's phone. GitHub Actions asks `https://pritra.fly.dev/api/health/deep` (repo variable `HEALTH_URL`)
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

**Panic / square-off** (`StrategyEngine._square_off` → `_square_off_one`): every held stock is closed at the same time,
`SQUARE_OFF_PARALLEL` (4) at once (each close is its own chain: stop cancel verified, Groww position read, exit, fill
wait), and `POST /api/bots/kill-all` kills every bot together, so a panic on many stocks takes about as long as one.
A LIVE exit sent for a square-off reason (`SQUARE_OFF_REASONS`: KILL_SWITCH, EOD_SQUARE_OFF, SQUARE_OFF_CUTOFF) that
Groww leaves pending is cancelled (`_require_live_fill` → `ExitUnfilled`, nothing left working) and sent once more
`SQUARE_OFF_RETRY_PCT` (0.8 %) further through the touch; an ordinary exit is still refused after one try.

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

**LIVE exit safety** (audit of Nov 2026; `tests/test_live_exit_safety.py`):
- **One focus per task**: `_focus` / `ltp` are per task (`_own_view`, a context variable keyed by engine). `tick` keeps
  the shared copy the screens read; `direct_order`, `force_order`, `close_symbol`, `reverse_position`, `kill` and
  `answer_review` work on their own, so a news / hand-off / Force order running while the pass waits on Groww can no
  longer move the pass onto its stock.
- **Every bot exit cancels the exchange stop first** (`_exit_now`: every reason but `_STOP_FILLS`, the stop's own
  fills, `SOLD_ON_GROWW` and `STOP_LOST`). `_cancel_sl_verified` raises `SlAlreadyFilled` when the stop filled (booked
  at Groww's price as the stop, `_stop_reason`); a cancel not confirmed sends no exit, keeps the stop and sets
  `pending_exit`, and the exit goes when Groww reports the stop cancelled.
- **A failed exit after the stop was cancelled** (`_close_position` wrapper): `_reprotect` places the stop again at
  `sl_trigger` for the shares Groww holds (only a stop the bot itself took away, `stop_cancelled`; never next to a
  restored stop of unknown id). If that fails, `_mark_naked` (critical alert with a call) and `_mind_naked` every
  `NAKED_RETRY_SEC` (5 s): Groww flat → booked at Groww's price; price through the stop → `STOP_LOST`; else try again.
  An exit whose answer was lost (`ExitUnknown`) is not followed by a new stop at once.
- **Stop id kept**: `trade_log.sl_order_id` (LIVE only, `_save_stop_id`), restored by `restore_open_books`.
- **Triggered but unfilled stop** (`_stop_slipped`): price past the stop's limit (trigger ∓ `market_protection_pct`) for
  `STOP_SLIP_SEC` (5 s) → cancel (checked) and `STOP_LOST`.
- **Not RUNNING but holding** (paused, halted by a cap, after a square-off that left a position): `tick` →
  `_guard_held` keeps the trailing stops, stops and targets working (`_managing`), retries a square-off that left a
  position (`_squareoff_due`) and squares off LIVE at the square-off time; `_live_eod_square_off` does the LIVE
  square-off on every pass of a RUNNING bot. One held stock's error no longer stops the others (`_mind_held`,
  `_held_error`; `_square_off_one` catches per stock).
- The 15-second "not on Groww" sweep leaves positions with a stop id to `_watch_stop`; the flat-book fallback for an
  unanswered stop status reads Groww at most every `FLAT_CHECK_SEC` (10 s) and cancels the stop it finds orphaned.
- `_open` refuses when the order client is LIVE but the pass's settings say PAPER (a switch mid-pass); `/api/mode`
  refuses while an order is in flight and, switching to LIVE, drops waits armed in PAPER and holds for the next cross.
- No new entry before `OPENING_BUFFER_END` (09:20) by any path (cross, Force, news / hand-off orders, Reverse).
- Order-side Groww SDK calls carry `timeout=_ORDER_TIMEOUT_SEC` (8 s; positions 4 s); net quantity and closing price
  read only MIS rows (`_intraday_row`).

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
  session/`GROWW_ACCESS_TOKEN`. A HALTED bot may always switch back to PAPER (the safe way), never to LIVE the same
  day: a LIVE halt taken back to PAPER keeps `StrategyEngine.live_halt_day`, so `release_paper_halt` does not treat it as
  a practice halt on the next LIVE confirm. In PAPER the client fills locally at the
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
  2 CPUs, 4 GB; never throttled in market hours), a volume `data` mounted at `/data`, and `[http_service.concurrency]` requests 200 soft / 250 hard (Fly's default 25 was
  passed by a few open pages; the same block is in `fly.backtest.toml` and `fly.scanner.toml`). Change the machine size in
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
    `requirements-dev.txt`) and the frontend typecheck plus static export. The backend tests run in 4 parts on 4
    runners at once (`backend-part`, `scripts/test_parts.py` shares the test files out by size), each part on every
    CPU of its runner (pytest-xdist, `-n auto --dist loadfile`); the job named "Backend tests" passes once every part
    did. A deploy by hand (workflow_dispatch) skips them when that commit already passed "Backend tests" on its push to
    `main` (`tested-before`, read through the Actions API); otherwise they run as usual. Tests that write a shared file
    must use `tmp_path`, since several test files run side by side
  - **backtest server first** (owner's choice): a push to `main` that passes both deploys
    **only the backtest server** (`deploy-backtest`, `fly.backtest.toml`, needs `DEPLOY_BACKTEST`),
    at any hour, with the exported `frontend/out/` (built in CI, not committed)
  - **the main desk only by hand**: "Run workflow" (workflow_dispatch) on `main` with
    `target: main-desk` and optional `commit` (the version the backtest server shows; blank =
    latest main) deploys that commit to the desk (`deploy`) and then the scanner (`deploy-scanner`).
    Inside NSE market hours (09:00–15:45 IST, Mon–Fri) the `release-gate` job refuses it unless
    `market_hours_ok` is ticked. `target: backtest` re-deploys the backtest server.
  - every image carries its commit (`--build-arg APP_VERSION`, Dockerfile `ENV APP_VERSION`), shown
    by `/api/role` `version`: the backtest server's bar ("This server runs version …", Copy version,
    "Deploy this version to the main desk" link to the workflow page) and the desk's "Desk version …"
    (`components/Terminal/BacktestServerBar.tsx`)
  - every deploy job (backtest server, main desk, scanner) runs `scripts/fly_deploy.sh <config> <name>`:
    first on Fly's builder (`--remote-only`, stopped after `REMOTE_TIMEOUT` 480 s), and when that fails or
    stalls, once more built on the GitHub runner with its own Docker (`--local-only`, `LOCAL_TIMEOUT` 900 s)
    and pushed to registry.fly.io over the internet, which does not go through Fly's builder or its
    internal registry address (on 11 Nov every deploy failed for over an hour with "failed to push …
    `_api.internal:5000` … dial tcp [fdaa::3]:5000: connect: connection refused" while Fly's status page
    was green). Each job's limit is 30 minutes.
  - **second desk** (another Fly account, `fly.desk2.toml`, setup in `DESK2.md`): "Run workflow" with
    `target: desk2` (same `commit` / `market_hours_ok` rules) deploys only it (`deploy-desk2`), to the app
    named by the repo variable `DESK2_APP` (`scripts/fly_deploy.sh` passes `FLY_APP` as `-a`) with the
    secret `FLY_DESK2_API_TOKEN`; its own volume, database, keys and secrets, boots PAPER; two dedicated performance CPUs and
    4 GB like `fly.toml` (the shared CPUs tried first were throttled and froze it under backtests), so its Fly
    account must be on Pay As You Go
  - **moving off the old Fly account** (owner's plan: close the account running `paltra`, `paltra-backtest`
    and `paltra-scanner`; `pritra` becomes the desk): Run workflow's default target is `desk2`; with the repo
    variables `DEPLOY_BACKTEST` / `DEPLOY_SCANNER` false a merge deploys nothing (the release gate's note says
    so); the health watcher reads `https://pritra.fly.dev/api/health/deep` (`scripts/health_watch.sh`; the repo
    variable `HEALTH_URL` overrides it). Steps in `DESK2.md`, "Closing the old account"
  - **the backtest server's runs** (`backend/merge_backtests.py`, run on pritra: `python merge_backtests.py --sma …
    [--pnl …] [--trend …] [--write]`): copies another server's finished / stopped / interrupted `replay_run` rows with
    their `trade_log`, `review_log`, `handoff_log`, `replay_pnl` and `trend_reads` rows under new ids (dry run without
    `--write`; a run already here, same start / days / settings, is skipped; targets backed up as `*.before-merge`).
    Copied runs carry `copied_from` in their settings: `run_source.describe` says "copied from the backtest server"
    and `paper_wallet._counted` leaves them out; a later pass still adds the P&L log / trend readings of runs copied before (`known`, INSERT
    OR IGNORE), and a damaged source file (`_healthy`, PRAGMA quick_check; a copy cut short) stops it before any write
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
