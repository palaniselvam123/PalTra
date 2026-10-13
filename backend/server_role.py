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

The two servers share one read-only link: the scanner asks the desk which
stocks each bot has armed (``SCANNER_LINK_TOKEN``, 24+ characters, the same
secret on both apps). No order, setting or mode crosses it.
"""
from __future__ import annotations

import os

MIN_TOKEN_LEN = 24
DEFAULT_DESK_URL = "https://paltra.fly.dev"

SCANNER_NO_ORDERS = "This is the scanner server: it never sends orders. Trade from the main desk."
SCANS_OFF_TEXT = "Scans are off on this server (SCANS=off): they run on the scanner server."


class ScannerNoOrders(RuntimeError):
    """An order path was reached on the scan-only server."""


def role() -> str:
    return "scanner" if os.environ.get("PALTRA_ROLE", "").strip().lower() == "scanner" else "desk"


def is_scanner() -> bool:
    return role() == "scanner"


def scans_enabled() -> bool:
    """False only on a desk with SCANS=off. The scanner always scans."""
    if is_scanner():
        return True
    return os.environ.get("SCANS", "on").strip().lower() not in ("off", "0", "false", "no")


def desk_url() -> str:
    return (os.environ.get("DESK_URL", "").strip() or DEFAULT_DESK_URL).rstrip("/")


def scanner_url() -> str:
    return os.environ.get("SCANNER_URL", "").strip().rstrip("/")


def link_token() -> str:
    """The shared read-only link secret, or "" when unset or too short."""
    token = os.environ.get("SCANNER_LINK_TOKEN", "").strip()
    return token if len(token) >= MIN_TOKEN_LEN else ""


def refuse_orders(what: str = "order") -> None:
    """Raise on the scanner before any Groww order, modify, cancel or square-off call."""
    if is_scanner():
        raise ScannerNoOrders(f"{SCANNER_NO_ORDERS} ({what} refused)")


def describe() -> dict:
    """What the page needs to know. No secrets."""
    return {
        "role": role(),
        "scans_enabled": scans_enabled(),
        "desk_url": desk_url(),
        "scanner_url": scanner_url() or None,
        "link": bool(link_token()),
    }
