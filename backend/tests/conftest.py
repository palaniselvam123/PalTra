"""Shared test settings."""
from __future__ import annotations

import os
import tempfile

# Tests must not download Groww's instrument list. Prices fall back to the
# NSE price-band tick unless a test sets the table itself.
os.environ.setdefault("SMA_TICK_SIZES", "off")

# Importing the research store writes its schema. Keep that off the real
# research_data/research.db.
os.environ.setdefault(
    "RESEARCH_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="paltra-research-"), "research.db")
)

import pytest


@pytest.fixture(autouse=True)
def _no_alert_delivery(monkeypatch):
    """Record alerts in memory instead of reading the desk DB and sending them.

    Fills and desk orders schedule `alert_notifier.send` as a fire-and-forget
    task. The real send opens the desk's aiosqlite engine to find the alert
    channel. On a test's event loop, that connection outlived the loop, and its
    worker thread later raised "Event loop is closed". The recorder keeps the
    scheduling code under test with no DB connection and no network.
    """
    from app.services.alert_notifier import DeliveryResult, alert_notifier

    sent: list[str] = []

    async def record(message: str) -> DeliveryResult:
        sent.append(message)
        return DeliveryResult(False, None, skipped_reason="tests do not send alerts")

    monkeypatch.setattr(alert_notifier, "send", record)
    return sent
