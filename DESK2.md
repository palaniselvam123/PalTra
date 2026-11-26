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

1. **Create the app and its volume.** App names are global on Fly; `paltra2` is only an example, so
   pick any free name and use it everywhere below.

   ```bash
   fly apps create paltra2
   fly volumes create data -a paltra2 -r sin -s 3
   ```

2. **Set its secrets.** Make a new `ENCRYPTION_KEY` (it also signs the Google sign-in cookie), not the
   main desk's:

   ```bash
   python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
   ```

   ```bash
   fly secrets set -a paltra2 ENCRYPTION_KEY=<the key> FRONTEND_ORIGIN=https://paltra2.fly.dev
   ```

3. **Lock it with Google sign-in (strongly advised).** Without these the pages and APIs are open to
   anyone who finds the address. In Google Cloud Console → Credentials, add
   `https://paltra2.fly.dev` to the **Authorised JavaScript origins** of the OAuth client the main desk
   uses (or make a new one), then:

   ```bash
   fly secrets set -a paltra2 GOOGLE_CLIENT_ID=<client id> GOOGLE_ALLOWED_EMAILS=<your gmail>
   ```

   Optional: `SCANS=off` to keep its Scalp scans off, as on the main desk. The OpenAI key and the
   Telegram / Twilio alert channels are saved on its own Settings page, not as secrets.

4. **Make the deploy token:**

   ```bash
   fly tokens create deploy -a paltra2
   ```

5. **Give it to GitHub.** In the repository: Settings → Secrets and variables → Actions:
   - **Secrets** → New repository secret: `FLY_DESK2_API_TOKEN` = the whole token from step 4
     (starts with `FlyV1 `).
   - **Variables** → New repository variable: `DESK2_APP` = `paltra2` (your app name).

## Deploying

Actions → **Test and deploy** → **Run workflow** on `main`:
- **target**: `desk2`
- **commit**: blank for the latest `main`, or the version the backtest server shows
- **market_hours_ok**: tick only to deploy during 09:00-15:45 IST (the desk restarts for about a
  minute; bots' in-memory state is lost)

The first deploy builds the image (a few minutes), starts one machine with the volume mounted, and
waits for `/api/health`. Then open `https://paltra2.fly.dev`, sign in, and on Settings → Account save
the Groww API key and log in.

## Size and cost

`fly.desk2.toml` uses the main desk's machine (2 performance CPUs, 4 GB, always on), about
US$60-65 a month plus about US$0.45 a month for the 3 GB volume. For a lighter practice desk, change
`[[vm]]` in `fly.desk2.toml` to `cpu_kind = 'shared'`, `cpus = 2`, `memory_mb = 2048` (a few dollars a
month); a busy replay can then slow its pages.

## Not set up for it

- The outside health watcher (`.github/workflows/health-watch.yml`) and the scanner / backtest server
  links (`SCANNER_LINK_TOKEN`, `BACKTEST_URL`) point at the main desk only.
- Settings, presets and trade history are not copied. Use Settings → Export / import settings on the
  main desk and import the file here.
