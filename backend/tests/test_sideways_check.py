"""Skip sideways candles (sideways_check.py): no entry while the last candles went nowhere. Entries only."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import sideways_check
from strategy_engine import _CHECK_NAMES, STOCK_FIELDS, _entry_block, _short_block_label

CFG = SimpleNamespace(use_sideways=True, sideways_candles=6, sideways_eff=0.5, sideways_atr=1.0)


def _frame(closes: list[float], atr: float = 1.0) -> pd.DataFrame:
    """Closed candles with these closes, then one forming candle (never read)."""
    rows = [{"ts": 1_700_000_000 + 300 * i, "close": c, "atr_14": atr} for i, c in enumerate(closes + [closes[-1] + 50])]
    return pd.DataFrame(rows)


TREND = [100, 100.5, 101, 101.6, 102.1, 102.5, 103]      # 3 points up in a straight line: efficiency 1, 3 ATR
CHOP = [100, 101, 100.2, 101.1, 100.3, 101, 100.4]        # back and forth: efficiency 0.08
SLOW = [100, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6]    # straight but only 0.6 ATR


def test_a_moving_market_passes_either_way():
    assert sideways_check.entry_reason(_frame(TREND), "LONG", CFG) is None
    assert sideways_check.entry_reason(_frame(TREND), "SHORT", CFG) is None  # the cross / other checks say which way
    assert "efficiency 1.00" in sideways_check.passed_text(_frame(TREND), "LONG", CFG)


def test_back_and_forth_candles_are_refused():
    why = sideways_check.entry_reason(_frame(CHOP), "LONG", CFG)
    assert why.startswith("Sideways candles:") and "went back and forth" in why


def test_a_straight_but_small_move_is_refused_unless_the_atr_part_is_off():
    assert "moved only 0.60 ATR, needs 1" in sideways_check.entry_reason(_frame(SLOW), "LONG", CFG)
    off = SimpleNamespace(use_sideways=True, sideways_candles=6, sideways_eff=0.5, sideways_atr=0)
    assert sideways_check.entry_reason(_frame(SLOW), "LONG", off) is None


def test_the_forming_candle_is_never_read_and_short_frames_refuse():
    # The forming candle jumps 50 points; only the closed ones count.
    assert sideways_check.entry_reason(_frame(CHOP), "LONG", CFG) is not None
    assert "not ready" in sideways_check.entry_reason(_frame(CHOP[:4]), "LONG", CFG)
    assert "not ready" in sideways_check.entry_reason(None, "LONG", CFG)
    assert "ATR not known yet" in sideways_check.entry_reason(_frame(TREND, atr=float("nan")), "LONG", CFG)


def test_settings_are_kept_in_bounds():
    wild = SimpleNamespace(sideways_candles=99, sideways_eff=5, sideways_atr=-3)
    assert sideways_check.candles(wild) == 30 and sideways_check.min_eff(wild) == 1.0 and sideways_check.min_atr(wild) == 0
    plain = SimpleNamespace()
    assert (sideways_check.candles(plain), sideways_check.min_eff(plain), sideways_check.min_atr(plain)) == (6, 0.5, 1.0)
    assert not sideways_check.on(plain)


def test_entry_block_reads_it_only_when_ticked_and_names_it_on_the_chart():
    chop = _frame(CHOP)
    reason = _entry_block(chop, "LONG", CFG)
    assert "Sideways candles" in reason and _short_block_label(reason) == "Sideways"
    assert _entry_block(chop, "LONG", SimpleNamespace(use_sideways=False)) is None
    assert _entry_block(_frame(TREND), "LONG", CFG) is None
    assert ("sideways", "Sideways") in _CHECK_NAMES
    assert {"use_sideways", "sideways_candles", "sideways_eff", "sideways_atr"} <= set(STOCK_FIELDS)


def test_api_saves_the_sideways_check(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["use_sideways"] is False and got["sideways_candles"] == 6
        saved = client.put("/api/config", json={"use_sideways": True, "sideways_candles": 8, "sideways_eff": 0.6, "sideways_atr": 1.5})
        assert saved.status_code == 200, saved.text
        got = client.get("/api/config").json()
        assert got["use_sideways"] is True and got["sideways_candles"] == 8
        assert got["sideways_eff"] == 0.6 and got["sideways_atr"] == 1.5
        assert client.put("/api/config", json={"sideways_candles": 99}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"use_sideways": True, "sideways_candles": 4})
        assert own.status_code == 200 and own.json()["use_sideways"] is True and own.json()["sideways_candles"] == 4
        assert client.get("/api/config/defaults").json()["use_sideways"] is False
    database.reset_engine()
