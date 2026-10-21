"""Each trade saves why it opened (TradeLog.entry_reason). PAPER only."""
from __future__ import annotations

import pytest

from tests.test_stop_off import NOW, _cfg, eng  # noqa: F401  (eng is a fixture)


def _rows():
    import database
    from models import TradeLog
    from strategy_engine import _trade_dict

    with database.session_factory()() as db:
        return [_trade_dict(row) for row in db.query(TradeLog).order_by(TradeLog.id).all()]


@pytest.mark.asyncio
async def test_entry_reason_is_saved_and_sent(eng):  # noqa: F811
    cfg = _cfg(eng, use_stop=True)
    eng._focus = "RELIANCE"
    eng._ltps["RELIANCE"] = 1000.0
    await eng._apply_locked(signal="BULLISH", cross_price=1000.0, atr=2.0, adx_blocks_entry=False, cfg=cfg, now=NOW)
    await eng._apply_locked(
        signal="BEARISH", cross_price=999.0, atr=2.0, adx_blocks_entry=False, cfg=cfg, now=NOW, why="LATE_ENTRY"
    )
    rows = _rows()
    assert [r["entry_reason"] for r in rows] == ["SMA_CROSS", "LATE_ENTRY"]  # the reverse keeps its own reason
    assert rows[0]["exit_reason"] == "MA_CROSS"


def test_old_databases_get_the_column(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{path}")
    import database

    database.reset_engine()
    try:
        database.init_db()
        database.reset_engine()
        with sqlite3.connect(path) as conn:  # a database from before the column existed
            conn.execute("ALTER TABLE trade_log DROP COLUMN entry_reason")
        database.init_db()
        with sqlite3.connect(path) as conn:
            names = {row[1] for row in conn.execute("PRAGMA table_info(trade_log)")}
        assert "entry_reason" in names
    finally:
        database.reset_engine()
