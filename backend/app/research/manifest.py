"""Dataset manifests — reproducibility, minimally.

An experiment result is only meaningful alongside the data it ran on. A
manifest pins that: which symbols, which interval, which dates, how many
candles, what the validator said, and the code version at the time.

Deliberately not an experiment-tracking platform. It records what is needed to
answer "what exactly was this number computed from" and nothing more.
"""
from __future__ import annotations

import datetime as dt
import subprocess

from app.research.store import ResearchStore, store as default_store
from app.research.validator import ValidationResult, validate_store


def git_commit() -> str | None:
    """Current commit, so a result can be tied to the code that produced it."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def build(
    dataset_id: str,
    interval: str,
    source: str = "live",
    symbols: list[str] | None = None,
    store: ResearchStore | None = None,
    validation: ValidationResult | None = None,
) -> dict:
    st = store or default_store
    syms = symbols or st.symbols(interval, source)
    res = validation or validate_store(st, interval, source, syms)
    return {
        "dataset_id": dataset_id,
        "created_at": int(dt.datetime.now(dt.timezone.utc).timestamp()),
        "symbols": syms,
        "interval": interval,
        "source": source,
        "start_date": res.start or "",
        "end_date": res.end or "",
        "trading_days": res.trading_days,
        "total_candles": res.total_candles,
        "validation_status": res.status,
        "git_commit": git_commit(),
        "report": res.as_dict(),
    }


def create(dataset_id: str, interval: str, source: str = "live",
           symbols: list[str] | None = None, store: ResearchStore | None = None) -> dict:
    st = store or default_store
    m = build(dataset_id, interval, source, symbols, st)
    st.save_manifest(m)
    return m
