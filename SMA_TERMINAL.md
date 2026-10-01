# SMA(9, 21) + 1.5× ATR Intraday Terminal

A second process next to the ORB desk. It trades one NSE cash MIS name on
closed 1-minute candles: SMA 9 crossing SMA 21, stopped at 1.5× Wilder ATR(14),
and it can stop-and-reverse. Charges use the Groww intraday schedule.

The engine **boots in `PAPER`**. `LIVE` is refused unless the UI confirmation
modal sends `confirm_live=true` **and** `GROWW_ACCESS_TOKEN` is set.

## Safety rules

1. Crossover checks use `df.iloc[-3]` vs `df.iloc[-2]`. The forming bar (`iloc[-1]`) is never a signal.
2. An `asyncio.Lock` plus a PENDING/TRANSIT flag blocks a second order while one is in flight.
3. A Long↔Short flip cancels the resting exchange SL and waits until that cancel is confirmed. If the SL already filled, the reverse is aborted.
4. Paper is the default. Live needs the confirmation above.
5. `max_daily_loss` cancels the SL, flattens the MIS position, and locks the bot. `max_trades_per_day` refuses the next entry (a reverse closes flat and locks). At `square_off_time` (default 15:15 IST) the book is flattened and the status becomes `DAY_COMPLETED`. The first five minutes (09:15–09:20) are skipped on a live session.

PAPER uses Groww 1-minute candles and LTP when the token works and the cash session is open. Otherwise it runs the built-in 1-minute simulator. LIVE sends limit orders with a 0.20% protection buffer and an exchange `SL` — not raw market orders.

## Run

```bash
# backend (port 8001)
chmod +x scripts/run_sma_terminal.sh
GROWW_ACCESS_TOKEN=your_daily_token scripts/run_sma_terminal.sh

# frontend
cd frontend
echo 'NEXT_PUBLIC_SMA_API_URL=http://127.0.0.1:8001' >> .env.local
npm install
npm run dev
```

Open http://localhost:3000/terminal

## Open it on a phone

The phone and the computer must be on the same Wi‑Fi. `localhost` on the phone is the phone itself, so both servers have to listen on the computer’s LAN address.

1. On the computer, find that address. It looks like `192.168.1.42`.

```bash
# macOS
ipconfig getifaddr en0

# Linux
hostname -I

# Windows
ipconfig
```

2. Start the API so it accepts connections from the network (the startup script already binds `0.0.0.0`):

```bash
scripts/run_sma_terminal.sh
```

3. Start the UI with that address baked into the API URL, and listen on every interface:

```bash
cd frontend
NEXT_PUBLIC_SMA_API_URL=http://192.168.1.42:8001 npm run dev -- -H 0.0.0.0 -p 3000
```

Replace `192.168.1.42` with the address from step 1. Restart `npm run dev` if you change it — Next.js reads `NEXT_PUBLIC_*` only at startup.

4. On the phone browser open:

```text
http://192.168.1.42:3000/terminal
```

If the page loads but prices stay blank, the computer firewall is blocking ports **3000** and **8001**. Allow those two on private networks. Mobile data will not work; the phone has to be on that same Wi‑Fi.

REST and the 1-second WebSocket (`/ws/stream`) are served by `backend/main.py`.
Strategy logic is in `backend/strategy_engine.py`.
