"""Which job this server does: the trading desk, or the scan-only server.

PalTra can run as two Fly apps from one image:

* ``desk`` (default): the bots, the desk and orders, as before.
* ``scanner`` (``PALTRA_ROLE=scanner``): the Scalp page's scans and alerts on
  their own machine and their own Groww API key. It never sends an order:
  every Groww order call refuses, and LIVE switches are refused. Arm opens the
  desk (``DESK_URL``) in a new tab, where the usual arm prompt runs.

``SCANS=off`` on the desk turns its own Scalp scans off (and keeps them off),
so the bots get the machine and the Groww rate limit to themselves. The default
is on, so nothing changes until the owner sets it.

* ``backtest`` (``PALTRA_ROLE=backtest``): backtests (replays) only, on their own
  machine that starts when the page is opened and stops itself when idle
  (``backtest_server.py``). It never sends an order either, runs no live bots and
  no scans, and copies the bots' settings and the desk's Groww session from the
  desk over its own link (``BACKTEST_LINK_TOKEN``).

The two servers share one read-only link: the scanner asks the desk which
stocks each bot has armed (``SCANNER_LINK_TOKEN``, 24+ characters, the same
secret on both apps). No order, setting or mode crosses it.
"""
from __future__ import annotations

import os

MIN_TOKEN_LEN = 24
DEFAULT_DESK_URL = "https://paltra.fly.dev"

SCANNER_NO_ORDERS = "This is the scanner server: it never sends orders. Trade from the main desk."
BACKTEST_NO_ORDERS = "This is the backtest server: it never sends orders. Trade from the main desk."
SCANS_OFF_TEXT = "Scans are off on this server (SCANS=off): they run on the scanner server."


class ScannerNoOrders(RuntimeError):
    """An order path was reached on the scan-only server."""


def role() -> str:
    value = os.environ.get("PALTRA_ROLE", "").strip().lower()
    return value if value in ("scanner", "backtest") else "desk"


def is_scanner() -> bool:
    return role() == "scanner"


def is_backtest() -> bool:
    return role() == "backtest"


def is_desk() -> bool:
    return role() == "desk"


def no_orders() -> bool:
    """The scanner and the backtest server never send an order."""
    return role() in ("scanner", "backtest")


def no_orders_text() -> str:
    return BACKTEST_NO_ORDERS if is_backtest() else SCANNER_NO_ORDERS


def scans_enabled() -> bool:
    """False on a desk with SCANS=off. The scanner always scans; the backtest server scans on demand
    with its own Groww login (scan_session.py), never the desk's session its replays borrow."""
    if is_scanner() or is_backtest():
        return True
    return os.environ.get("SCANS", "on").strip().lower() not in ("off", "0", "false", "no")


def desk_url() -> str:
    return (os.environ.get("DESK_URL", "").strip() or DEFAULT_DESK_URL).rstrip("/")


def scanner_url() -> str:
    return os.environ.get("SCANNER_URL", "").strip().rstrip("/")


def backtest_url() -> str:
    return os.environ.get("BACKTEST_URL", "").strip().rstrip("/")


def backtest_link_token() -> str:
    """The desk ↔ backtest server secret, or "" when unset or too short. Not the scanner's token:
    this link also carries the desk's Groww session, so only the backtest server holds it."""
    token = os.environ.get("BACKTEST_LINK_TOKEN", "").strip()
    return token if len(token) >= MIN_TOKEN_LEN else ""


def link_token() -> str:
    """The shared read-only link secret, or "" when unset or too short."""
    token = os.environ.get("SCANNER_LINK_TOKEN", "").strip()
    return token if len(token) >= MIN_TOKEN_LEN else ""


def refuse_orders(what: str = "order") -> None:
    """Raise on the scanner or the backtest server before any Groww order, modify, cancel or square-off call."""
    if no_orders():
        raise ScannerNoOrders(f"{no_orders_text()} ({what} refused)")


def describe() -> dict:
    """What the page needs to know. No secrets."""
    return {
        "role": role(),
        "scans_enabled": scans_enabled(),
        "desk_url": desk_url(),
        "scanner_url": scanner_url() or None,
        "link": bool(link_token()),
        "backtest_url": backtest_url() or None,
        "backtest_link": bool(backtest_link_token()),
        # Scanner only: its Arm can also arm on the backtest server (BACKTEST_URL + the link token).
        "backtest_arm": is_scanner() and bool(backtest_url()) and bool(link_token()),
        # Backtest server only: its Arm can also arm the desk's bots (the link token set here too).
        "desk_arm": is_backtest() and bool(link_token()),
    }
