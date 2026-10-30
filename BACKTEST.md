# Backtest server (`paltra-backtest`)

Backtests (replays) are CPU-heavy. On the desk they share one machine with the live
bots, so a long backtest can freeze the bots and the pages. The backtest server runs
backtests on **their own machine**, which starts when you open its page and stops
itself when idle, so you pay only while you use it.

```
paltra-backtest.fly.dev  (PALTRA_ROLE=backtest)        paltra.fly.dev  (the desk)
  backtests only, practice money                          bots 1-4, desk, orders
  never sends an order, refuses LIVE                       unchanged
  starts on visit, stops itself when idle
        |  bots' settings + armed stocks ← GET /api/link/backtest/settings  (BACKTEST_LINK_TOKEN)
        |  the desk's Groww session      ← GET /api/link/backtest/groww     (memory only)
```

## What the backtest role does

- **No orders, ever.** Every Groww order, stop, cancel and square-off call refuses
  (`server_role.refuse_orders`), the LIVE switch answers 403, and the bots' Start
  answers 409: no live bot, scan, alert or ORB price feed runs here.
- **Settings come from the desk.** At start-up, and when you press **Sync from desk**
  on its terminal page, it copies every bot's settings row (bots 1-4 and research:
  strategy, Trade list, per-stock settings) from the desk, with PAPER forced. Nothing
  is ever written back to the desk. Sync is refused while a backtest plays, so a run
  keeps the settings it started with.
- **The desk's Groww session, not a second login.** Past candles come from Groww with
  the desk's own access token, fetched over the link every 10 minutes and kept in
  memory only (never saved, logged or shown). Logging in again with the same API key
  on another server could end the desk's session, so the backtest server never logs
  in to Groww itself. Log in to Groww on the desk as usual each morning.
- **Stops itself when idle.** With no backtest loading or playing and no page open for
  the idle time (30 minutes by default; 10-240, set on the page), the app exits and Fly
  stops the machine. Opening the page starts it again (about 10-20 s). A backtest that
  is still playing keeps it on even with the tab closed. **Stop server** on the page
  stops it at once; a backtest stopped that way can be resumed next time.
- **Results stay on the backtest server** (its own volume) and show in its Backtests tab.
- **The scanner can arm stocks here.** The scanner's Arm (one stock or Arm ticked) has a
  **Main desk / Backtest server** switch. On Backtest server the stocks go on this server's
  copies of the bots (always PAPER) with the quantity and stop you set, and the desk is not
  touched. If this server is stopped, the arm starts it (allow 20-30 s) and waits for its
  start-up copy from the desk, so the copy does not wipe it. Stocks armed this way are kept
  each time this server starts again, while they are still armed here; **Sync from desk**
  replaces them with the desk's lists (it asks first). The bar on the terminal shows how
  many there are.

## Setup (one time, outside market hours)

1. **Create the app and its volume**, from a checkout of `main`:

   ```bash
   fly apps create paltra-backtest
   fly volumes create backtest_data -a paltra-backtest -r sin -s 3
   ```

2. **Make the link token** and set the secrets. Use a new `ENCRYPTION_KEY`, not the
   desk's. The link token must be the same on both apps, and must **not** be the
   scanner's token:

   ```bash
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"   # → ENCRYPTION_KEY
   python -c "import secrets;print(secrets.token_urlsafe(32))"                                # → BACKTEST_LINK_TOKEN

   fly secrets set -a paltra-backtest ENCRYPTION_KEY=... BACKTEST_LINK_TOKEN=... \
       FRONTEND_ORIGIN=https://paltra-backtest.fly.dev
   # Google sign-in, the same as the desk (add https://paltra-backtest.fly.dev as an
   # authorised JavaScript origin on the Google OAuth client first):
   fly secrets set -a paltra-backtest GOOGLE_CLIENT_ID=... GOOGLE_ALLOWED_EMAILS=...
   ```

3. **Link the desk** (restarts the desk, so do it with the bots stopped):

   ```bash
   fly secrets set -a paltra BACKTEST_LINK_TOKEN=... BACKTEST_URL=https://paltra-backtest.fly.dev
   ```

   The desk's terminal then shows **Run backtests on the backtest server**.

4. **Deploy it once by hand**, with the frontend built first:

   ```bash
   cd frontend && NEXT_OUTPUT=export npm run build && cd ..
   fly deploy -c fly.backtest.toml
   ```

5. **Let the scanner arm here (optional)**. Give the backtest server the scanner's link
   token (the same `SCANNER_LINK_TOKEN` value the desk and the scanner already have), and
   tell the scanner where the backtest server is:

   ```bash
   fly secrets set -a paltra-backtest SCANNER_LINK_TOKEN=<the scanner's token>
   fly secrets set -a paltra-scanner BACKTEST_URL=https://paltra-backtest.fly.dev
   ```

   If you no longer have the token, make a new one and set it on all three apps (the desk
   restarts, so do it outside market hours):
   `fly secrets set -a paltra SCANNER_LINK_TOKEN=...`, then the same on `paltra-scanner` and
   `paltra-backtest`. The scanner's Arm then shows **Main desk / Backtest server**.

6. **Check it**: open `https://paltra-backtest.fly.dev/terminal/`, sign in. The violet
   **Backtest server** bar should say "Settings copied just now" and "Groww: desk
   session in use" (log in to Groww on the desk first if it says no session). Run a
   backtest as on the desk.

7. **Deploy it from CI** from now on. In GitHub → Settings → Secrets and variables →
   Actions:
   - secret `FLY_BACKTEST_API_TOKEN` = `fly tokens create deploy -a paltra-backtest`
   - variable `DEPLOY_BACKTEST` = `true`

   Every push to `main` then deploys the backtest server too, after the desk and the
   scanner, with the same market-hours gate.

## Market hours only (optional)

Tick **Market hours only, stop after 15:45** on this server's bar to use it only while NSE is open. After the stop time
(and on weekends) it stops itself once no backtest or scan has run for 5 minutes, even with the page open, so it is only
billed in market hours. Opening it after hours wakes it for those few minutes only. Run evening backtests on the main
desk instead: the desk starts replays only after 15:45 (and at weekends), so they never slow the live bots.

Every Cross scan, Trend quality and Most active scan finished here, and each accuracy check, is also copied to the desk,
so the desk's Scalp page (History tab, Most active → History) shows them while this server is stopped.

## Size and cost

`performance-2x`, 4 GB (`fly.backtest.toml`): two dedicated cores, since backtests are
CPU-heavy and nothing else runs there. Billed only while the machine runs: roughly
US$0.10-0.15 an hour, plus about US$0.45 a month for the 3 GB volume while stopped.
For example, two hours a day on weekdays is about US$5-7 a month. To change the size,
edit `[[vm]]` in `fly.backtest.toml` (a change in the Fly dashboard is undone by the
next deploy).
