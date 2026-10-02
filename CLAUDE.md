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
  `place_entry`, `place_exit`, or `place_sl` against a real session.
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
- `backend/replay.py` – "Replay a past day": a separate `ReplayEngine`
  (subclass of `StrategyEngine`) plays a past session's Groww 1-minute
  candles on its own clock (`_now`), through `ReplayBroker`, which fills
  locally and has no Groww SDK path. Trades are tagged `REPLAY` (own book
  and P&L; no daily trade cap, the loss limit still applies), no alerts are
  sent, and the live engine keeps running.
  API: `/api/replay*`. Refused in LIVE mode.
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
   (MIS limit orders with a 0.20% protection buffer, plus an exchange `SL`).
   In PAPER these return local `PAPER…` ids and never touch the SDK.
   Called from `strategy_engine.py` (`_open`, `_close_position`,
   `_cancel_sl_verified`).
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

Trades are tagged with their mode (`PAPER`/`LIVE`, `paper`/`live`) so the
books stay separate in reports and CSV exports.

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

- `fly.toml`: app `paltra`, region `iad`, `internal_port = 3000`, HTTPS
  forced, one machine always running (`auto_stop_machines = 'off'`,
  `min_machines_running = 1`), 1 GB shared CPU, and a volume `data` mounted
  at `/data`.
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
