"""Shared test settings."""
from __future__ import annotations

import os

# Tests must not download Groww's instrument list. Prices fall back to the
# NSE price-band tick unless a test sets the table itself.
os.environ.setdefault("SMA_TICK_SIZES", "off")
