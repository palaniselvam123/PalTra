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
