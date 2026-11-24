"""Heikin Ashi colours for entries only or exits only (heikin_ashi.ha_scope). Replays on local fills only."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import heikin_ashi
from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
from tests.test_gap_mode import _settings, db  # noqa: F401


def _got(trades):
    return [(t["direction"], t["entry_time"].strftime("%H:%M"), t["exit_time"].strftime("%H:%M"), t["exit_reason"]) for t in trades]


def test_scope_and_role():
    off = SimpleNamespace(ha_colours=False, ha_scope="ENTRY")
    assert not heikin_ashi.on(off) and not heikin_ashi.on(off, "entry") and not heikin_ashi.split(off)
    both = SimpleNamespace(ha_colours=True, ha_scope=None)
    assert heikin_ashi.scope(both) == "BOTH" and heikin_ashi.on(both, "entry") and heikin_ashi.on(both, "exit")
    assert not heikin_ashi.split(both) and heikin_ashi.for_role(both, "exit") is both
    entry = SimpleNamespace(ha_colours=True, ha_scope="entry", colour_count=2)
    assert heikin_ashi.on(entry, "entry") and not heikin_ashi.on(entry, "exit") and heikin_ashi.on(entry)
    view = heikin_ashi.for_role(entry, "exit")
    assert not heikin_ashi.on(view) and not heikin_ashi.on(view, "exit") and view.colour_count == 2
    assert heikin_ashi.on(heikin_ashi.for_role(entry, "entry"))
    assert heikin_ashi.scope(SimpleNamespace(ha_colours=True, ha_scope="nonsense")) == "BOTH"


def test_view_follows_the_role():
    frame = pd.DataFrame({"open": [10.0, 11.0, 12.0], "high": [11.5, 12.5, 12.6], "low": [9.5, 10.5, 11.8], "close": [11.0, 12.0, 11.9]})
    exits = SimpleNamespace(ha_colours=True, ha_scope="EXIT")
    assert heikin_ashi.view(frame, exits, "entry") is frame
    assert heikin_ashi.view(frame, exits, "exit") is not frame


async def _run(db, day2, **over):  # noqa: F811
    from sma_research.replayer import replay_symbol

    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    return await replay_symbol(frame, "HAS", [DAY], _settings(db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, **over))


# A rise of greens from 09:45, one small real red at 09:49 (still green as Heikin Ashi), greens, then a fall.
_DAY = (
    _quiet(30)
    + [(1000.0, 1000.4), (1000.4, 1000.8), (1000.8, 1001.2), (1001.2, 1001.6), (1001.6, 1001.5), (1001.5, 1001.9), (1001.9, 1002.3)]
    + [(1002.3, 1001.5), (1001.5, 1000.7), (1000.7, 999.9), (999.9, 999.1)]
    + _quiet(334, 999.1)
)


@pytest.mark.asyncio
async def test_scopes(db):  # noqa: F811
    real = _got(await _run(db, _DAY))
    both = _got(await _run(db, _DAY, ha_colours=True))
    entry = _got(await _run(db, _DAY, ha_colours=True, ha_scope="ENTRY"))
    exits = _got(await _run(db, _DAY, ha_colours=True, ha_scope="EXIT"))
    # Real candles: the lone red at 09:49 closes the long and reverses it.
    assert real[:2] == [("LONG", "09:46", "09:50", "COLOUR_TURN"), ("SHORT", "09:50", "09:51", "COLOUR_TURN")]
    # Heikin Ashi for both: the lone red is still green as Heikin Ashi, so the long rides to the fall.
    assert both == [("LONG", "09:46", "09:54", "COLOUR_TURN"), ("SHORT", "09:54", "15:15", "EOD_SQUARE_OFF")]
    # Entries on Heikin Ashi, exits on real candles: the real red closes the long, but the Heikin Ashi entry still reads
    # green, so no short opens (flat); the next real red closes again; the short waits for a Heikin Ashi red.
    assert entry == [
        ("LONG", "09:46", "09:50", "COLOUR_TURN"),
        ("LONG", "09:51", "09:53", "COLOUR_TURN"),
        ("SHORT", "09:54", "15:15", "EOD_SQUARE_OFF"),
    ]
    # Entries on real candles, exits on Heikin Ashi: held through the lone red, closed when the Heikin Ashi turns red,
    # and the real candles (red) open the short at once.
    assert exits == [("LONG", "09:46", "09:54", "COLOUR_TURN"), ("SHORT", "09:54", "15:15", "EOD_SQUARE_OFF")]


def test_api_saves_the_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import SNAPSHOT_FIELDS, STOCK_FIELDS

    assert "ha_scope" in STOCK_FIELDS and "ha_scope" in SNAPSHOT_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["ha_scope"] == "BOTH"
        ok = client.put("/api/config", json={"ha_colours": True, "ha_scope": "EXIT"})
        assert ok.status_code == 200 and (ok.json()["ha_colours"], ok.json()["ha_scope"]) == (True, "EXIT")
        assert client.put("/api/config", json={"ha_scope": "SOMETIMES"}).status_code == 422
        assert client.put("/api/config/stock/SBIN", json={"ha_scope": "ENTRY"}).status_code == 200
    database.reset_engine()
