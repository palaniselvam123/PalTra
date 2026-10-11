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
| `set_trade_list` | Arm / unarm stocks (PAPER bots only) |
| `set_stop` | Stop type, stop / trail / target in ₹ or % (PAPER bots only) |

A LIVE bot is refused for every change. There is no tool for orders, closing a
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
