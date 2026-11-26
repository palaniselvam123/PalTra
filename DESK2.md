# Second desk (another Fly account)

A full copy of the main desk (ORB desk, SMA terminal, Bots 1-5, research, replays) on its own Fly
account. It is the same image as `paltra`, with its **own** volume, database, Groww API key,
settings, Trade lists, practice wallet and secrets. Nothing is shared with `paltra`, so the two desks
never see each other's trades or settings, and the main desk is not touched by its deploys.

It boots in **PAPER** like every desk. LIVE needs its own saved Groww login and the usual
confirmation. **Two desks on one Groww account share that account's rate limit and can end each
other's Groww session.** Use a different Groww API key (or account) here.

```
GitHub "Test and deploy"  ── Run workflow → target desk2 ──►  <DESK2_APP>.fly.dev
   (repo variable DESK2_APP, secret FLY_DESK2_API_TOKEN)        volume `data` → /data/trading.db
```

## One-time setup

Run these from a computer with [flyctl](https://fly.io/docs/flyctl/install/), **logged in to the new
account**:

```bash
fly auth logout
fly auth login            # sign in with the NEW Fly account
```

1. **Create the app and its volume.** The app is called `pritra`. Fly app names are global: if
   `pritra` is taken, pick another name and use it everywhere below (and in `DESK2_APP`).

   ```bash
   fly apps create pritra
   fly volumes create data -a pritra -r sin -s 3
   ```

2. **Set its secrets.** Make a new `ENCRYPTION_KEY` (it also signs the Google sign-in cookie), not the
   main desk's:

   ```bash
   python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
   ```

   ```bash
   fly secrets set -a pritra ENCRYPTION_KEY=<the key> FRONTEND_ORIGIN=https://pritra.fly.dev
   ```

3. **Lock it with Google sign-in (strongly advised).** Without these the pages and APIs are open to
   anyone who finds the address. In Google Cloud Console → Credentials, add
   `https://pritra.fly.dev` to the **Authorised JavaScript origins** of the OAuth client the main desk
   uses (or make a new one), then:

   ```bash
   fly secrets set -a pritra GOOGLE_CLIENT_ID=<client id> GOOGLE_ALLOWED_EMAILS=<your gmail>
   ```

   Optional: `SCANS=off` to keep its Scalp scans off, as on the main desk. The OpenAI key and the
   Telegram / Twilio alert channels are saved on its own Settings page, not as secrets.

4. **Make the deploy token:**

   ```bash
   fly tokens create deploy -a pritra
   ```

5. **Give it to GitHub.** In the repository: Settings → Secrets and variables → Actions:
   - **Secrets** → New repository secret: `FLY_DESK2_API_TOKEN` = the whole token from step 4
     (starts with `FlyV1 `).
   - **Variables** → New repository variable: `DESK2_APP` = `pritra` (your app name).

## Deploying

Actions → **Test and deploy** → **Run workflow** on `main`:
- **target**: `desk2`
- **commit**: blank for the latest `main`, or the version the backtest server shows
- **market_hours_ok**: tick only to deploy during 09:00-15:45 IST (the desk restarts for about a
  minute; bots' in-memory state is lost)

The first deploy builds the image (a few minutes), starts one machine with the volume mounted, and
waits for `/api/health`. Then open `https://pritra.fly.dev`, sign in, and on Settings → Account save
the Groww API key and log in.

## Size and cost

`fly.desk2.toml` uses a shared-CPU machine (2 shared CPUs, 2 GB, always on): the new account is on
Fly's Free plan, which refuses performance machines. A few dollars a month plus about US$0.45 a month
for the 3 GB volume; a busy replay can slow its pages. After upgrading that account to Pay As You Go,
change `[[vm]]` in `fly.desk2.toml` to `cpu_kind = 'performance'`, `memory_mb = 4096` (like `fly.toml`,
about US$60-65 a month).

## Not set up for it

- The outside health watcher (`.github/workflows/health-watch.yml`) and the scanner / backtest server
  links (`SCANNER_LINK_TOKEN`, `BACKTEST_URL`) point at the main desk only.
- Settings, presets and trade history are not copied. Use Settings → Export / import settings on the
  main desk and import the file here.
