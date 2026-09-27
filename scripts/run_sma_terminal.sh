#!/usr/bin/env bash
# SMA(9, 21) + 1.5x ATR terminal.
# Backend on :8001, then the Next.js app (existing frontend) at :3000/terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv || true
fi
if [[ -x .venv/bin/pip ]]; then
  .venv/bin/pip install -q -r requirements.txt
  PY=".venv/bin/python"
else
  python3 -m pip install --user -q -r requirements.txt
  PY="python3"
fi

export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export TRADING_MODE="${TRADING_MODE:-PAPER}"
exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "${SMA_PORT:-8001}" --reload
