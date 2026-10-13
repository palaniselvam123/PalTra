# Scanner server (`paltra-scanner`)

The Scalp page's scans (Cross scan, Trend quality, the Scalp monitor, the
most-active scan and their alerts) download a lot of Groww candles. On the same
machine and Groww account as the bots, a heavy scan could slow the bots or stop
them. The scanner server runs those scans on **their own Fly machine with their
own Groww API key**, so the bots on `paltra` keep their machine and their Groww
rate limit to themselves.

```
paltra-scanner.fly.dev  (PALTRA_ROLE=scanner)         paltra.fly.dev  (the desk)
  Scalp page: scans, monitor, alerts                     bots 1-4, desk, orders
  its own Groww key + Telegram channels                  SCANS=off: no scans here
  never sends an order, refuses LIVE                     Scalp page still arms stocks
        |  Arm → opens paltra.fly.dev/scalp/?arm=SYMBOL in a new tab
        |  "Armed only" ← GET /api/link/armed  (read-only, SCANNER_LINK_TOKEN)
```

## What the scanner role does

- **No orders, ever.** Every Groww order, stop modify, cancel and square-off
  call refuses (`server_role.refuse_orders`), and the SMA terminal's LIVE
  switch and the manual desk's Groww execution answer 403. Its bots can only
  run in PAPER.
- **Arm opens the desk.** On the scanner, Arm opens the desk's Scalp page in a
  new tab with `?arm=SYMBOL`. The usual prompt runs there against the real bots,
  with the LIVE confirmation and the desk's Google sign-in.
- **Armed stocks come from the desk** over a read-only link: the scanner calls
  `GET {DESK_URL}/api/link/armed` with `SCANNER_LINK_TOKEN`. That answer only
  holds each bot's name, mode and Trade list. Nothing else crosses the link.
- **No shutdown call.** A scanner deploy does not ring you: only the desk
  announces bots going down.

`SCANS=off` on the desk keeps its own scans paused for good (no resume) and shows
a link to the scanner on its Scalp page. It stays **on** until you set it, so
turn it off only once the scanner is working.

## Setup (one time, outside market hours)

1. **Create the app and its volume**, from a checkout of `main`:

   ```bash
   fly apps create paltra-scanner
   fly volumes create scanner_data -a paltra-scanner -r sin -s 1
   ```

2. **Set its secrets.** Use a new `ENCRYPTION_KEY`, not the desk's. The link token
   must be the same on both apps:

   ```bash
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"   # → ENCRYPTION_KEY
   python -c "import secrets;print(secrets.token_urlsafe(32))"                                # → SCANNER_LINK_TOKEN

   fly secrets set -a paltra-scanner ENCRYPTION_KEY=... SCANNER_LINK_TOKEN=... \
       FRONTEND_ORIGIN=https://paltra-scanner.fly.dev
   # Optional Google sign-in, the same as the desk (add https://paltra-scanner.fly.dev
   # as an authorised JavaScript origin on the Google OAuth client first):
   fly secrets set -a paltra-scanner GOOGLE_CLIENT_ID=... GOOGLE_ALLOWED_EMAILS=...
   ```

3. **Deploy it once by hand**, with the frontend built first:

   ```bash
   cd frontend && NEXT_OUTPUT=export npm run build && cd ..
   fly deploy -c fly.scanner.toml
   ```

4. **Log in to Groww on the scanner** with the **new** API key: open
   `https://paltra-scanner.fly.dev/settings`, save the key there and log in. It is
   stored encrypted on the scanner's own volume. Add the scanner's Telegram
   channels (trade and quiet-runner alerts) on the same page. If you changed the
   main desk's SMA periods, set the same ones on the scanner's Settings so the
   scans match.

5. **Link the desk** to the scanner, then check the Scalp page on both:

   ```bash
   fly secrets set -a paltra SCANNER_LINK_TOKEN=... SCANNER_URL=https://paltra-scanner.fly.dev
   ```

   The scanner's Scalp page should show "Scanner server", and "Armed only"
   should list the desk's armed stocks.

6. **Turn the desk's scans off** once the scanner works:

   ```bash
   fly secrets set -a paltra SCANS=off
   ```

   To undo this, run `fly secrets unset -a paltra SCANS`.

7. **Deploy both from CI** from now on. In GitHub → Settings → Secrets and
   variables → Actions:
   - secret `FLY_SCANNER_API_TOKEN` = `fly tokens create deploy -a paltra-scanner`
   - variable `DEPLOY_SCANNER` = `true`

   Every push to `main` then deploys the scanner too, with the same
   market-hours gate as the desk.

## Size and cost

`shared-cpu-2x`, 1 GB (`fly.scanner.toml`). Measured: about 0.4 GB in use while
scanning 213 stocks, and about 0.3 s of CPU per stock. This costs about US$8–10 a
month on top of the desk.
