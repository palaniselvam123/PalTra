"""Picklable jobs for tests/test_scan_worker.py (they run in the scanner process)."""
from __future__ import annotations

import os

import scan_worker


def pid_job(token, symbol, start, end, *args):
    return {"pid": os.getpid(), "symbol": symbol, "args": list(args), "token_seen": bool(token)}


def auth_job(token, symbol, start, end, *args):
    if token == "old":
        raise scan_worker.AuthFailed("authentication failed")
    return token


def fail_job(token, symbol, start, end, *args):
    raise scan_worker.ScanFailed("Groww did not send candles: Rate limit has breached")
