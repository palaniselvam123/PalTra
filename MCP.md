# Connect PalTra to n8n, Gemini, Claude (MCP)

PalTra serves an MCP server at `https://paltra.fly.dev/mcp`. AI tools can read the
SMA bots and make two kinds of change on **PAPER** bots only.

| Tool | What it does |
|---|---|
| `list_bots` | All four bots: mode, status, armed and held stocks, today's trades and P&L |
| `get_bot_status` | One bot: running or halted, open positions, P&L, trade count and caps |
| `get_settings` | One bot's strategy and stop settings, or one stock's |
| `get_trades` | Newest trades of a bot's PAPER or LIVE book |
| `get_scans` | Last Cross scan and Trend quality results |
| `list_backtests` | Backtest (replay) runs, newest first, for one bot or all: dates, stocks, status, totals |
| `get_backtest` | One run's day-by-day P&L, per-stock results and the settings it used |
| `get_backtest_trades` | One run's trades with each trade's best / worst move from entry (₹ per share), to size a stop and target |
| `compare_backtests` | 2-6 runs side by side: totals, stop/target settings used, per-stock totals |
| `start_backtest` | Start a practice backtest on a PAPER bot (not during 09:00–15:45 IST, Mon–Fri, nor while that bot already has one running) |
| `set_trade_list` | Arm / unarm stocks (PAPER bots only) |
| `set_stop` | Stop type, stop / trail / target in ₹ or % (PAPER bots only) |

A LIVE bot is refused for every change, including starting a backtest. Stop settings and the Trade list can't change while that bot's backtest is running (later days would use the new settings). There is no tool for orders, closing a
position, the PAPER/LIVE switch, start/stop or panic.

## 1. Turn it on (once)

Make a long random key and store it as a Fly secret. It is never in the code.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
fly secrets set MCP_TOKEN=<the key> -a paltra
```

Without `MCP_TOKEN` the endpoint answers 404. To turn it off again:
`fly secrets unset MCP_TOKEN -a paltra`. To replace a leaked key, set a new one.

## 2. Connect a tool

**n8n** – add an *MCP Client Tool* node to an AI Agent:
- Endpoint: `https://paltra.fly.dev/mcp`
- Server transport: *HTTP Streamable*
- Authentication: *Bearer Auth*, token = your key

**Claude** (Settings → Connectors → Add custom connector) or any client that can
send a header: URL `https://paltra.fly.dev/mcp`, header
`Authorization: Bearer <key>`.

**Gemini custom apps** (gemini.google.com → Apps → Custom apps) and other clients
that only take a link: use `https://paltra.fly.dev/mcp/<key>`. Anyone with that
link can use the tools, so treat the whole link as the password.

## 3. Check it

```bash
curl -s https://paltra.fly.dev/mcp -H "Authorization: Bearer <key>" \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_bots","arguments":{}}}'
```

## Auto-tune stop-loss and target (Gemini, n8n, Claude)

Arm the stocks on a PAPER bot in the app, then ask, for example: *"Tune bot 2's stop
and target on 1-9 October."* The server tells the AI these steps:

1. Read the bot's armed stocks and current stop settings.
2. `start_backtest` over the dates; wait until `list_backtests` shows it FINISHED.
3. `get_backtest_trades`: per stock, pick a stop just beyond most winners' worst move
   and a target most winners reached.
4. `set_stop` per stock (fixed stop + target: TSL, POINTS, trail step = target).
5. `start_backtest` again on the same dates and stocks, then `compare_backtests`.
6. Keep the better settings (or set the old ones back) and report before / after.

Backtests only start outside 09:00-15:45 IST on weekdays. Settings tuned on the same days
they are tested on look better than they are; ask it to check a second date range.
