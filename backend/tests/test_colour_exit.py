"""Candle colour: a separate rule for closing a trade (colour_exit.py). Replays on local fills only."""
from __future__ import annotations

import pandas as pd
import pytest

import colour_exit
from tests.test_candle_patterns import DAY, DAY0, _cfg, _quiet, _rows
from tests.test_gap_mode import _settings, db  # noqa: F401


def _got(trades):
    return [(t["direction"], t["entry_time"].strftime("%H:%M"), t["exit_time"].strftime("%H:%M"), t["exit_reason"]) for t in trades]


def test_exit_view_of_the_settings():
    cfg = _cfg(
        entry_mode="COLOUR", colour_count=3, colour_body_unit="ATR", colour_body_atr=2.5, colour_body_seq="2.5,1.5,1",
        colour_inside_minutes=5, colour_exit_on=True, colour_exit_count=2, colour_exit_body=0.3,
    )
    view = colour_exit.exit_cfg(cfg)
    assert colour_exit.on(cfg)
    assert (view.colour_count, view.colour_min_body, view.colour_body_unit, view.colour_body_seq) == (2, 0.3, "PCT", "")
    # The smaller-candles rule closes nothing unless ticked; the candle size and the rest read through.
    assert view.colour_inside_minutes == 0 and view.entry_mode == "COLOUR" and view.pattern_tf == cfg.pattern_tf
    cfg.colour_exit_inside = True
    assert colour_exit.exit_cfg(cfg).colour_inside_minutes == 5
    assert not colour_exit.on(_cfg(entry_mode="COLOUR"))
    assert colour_exit.count(_cfg(colour_exit_count=9)) == 5 and colour_exit.body(_cfg(colour_exit_body="x")) == colour_exit.DEFAULT_BODY


async def _run(db, day2, **over):  # noqa: F811
    from sma_research.replayer import replay_symbol

    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    return await replay_symbol(frame, "COL", [DAY], _settings(db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, **over))


# 09:45 green → long at 09:46; 09:46 a lone red; 09:47 green; 09:48-09:49 two reds.
_DAY = _quiet(30) + [(1000.0, 1000.3), (1000.3, 1000.2), (1000.2, 1000.5), (1000.5, 1000.2), (1000.2, 999.9)] + _quiet(340, 999.9)


@pytest.mark.asyncio
async def test_the_exit_rule_rides_through_a_lone_candle_and_closes_on_two(db):  # noqa: F811
    plain = _got(await _run(db, _DAY))
    assert plain[0] == ("LONG", "09:46", "09:47", "COLOUR_TURN")  # the entry rule turns on the lone red
    own = _got(await _run(db, _DAY, colour_exit_on=True, colour_exit_count=2, colour_exit_body=0))
    # Held through the lone red, closed on the second red in a row; the entry rule (1 red) agrees, so it reverses.
    assert own[0] == ("LONG", "09:46", "09:50", "COLOUR_TURN")
    assert own[1][:2] == ("SHORT", "09:50")


@pytest.mark.asyncio
async def test_the_exit_rule_closes_without_a_reverse_when_the_entry_rule_disagrees(db):  # noqa: F811
    day2 = _quiet(30) + [(1000.0, 1000.3), (1000.3, 1000.6), (1000.6, 1000.3), (1000.3, 1000.6)] + _quiet(341, 1000.6)
    trades = await _run(db, day2, colour_count=2, colour_exit_on=True, colour_exit_count=1, colour_exit_body=0)
    got = _got(trades)
    # Two greens open the long at 09:47; one red (the exit rule) closes it at 09:48; the entry rule needs two reds: flat.
    assert got[0] == ("LONG", "09:47", "09:48", "COLOUR_TURN")
    assert all(t["entry_time"].strftime("%H:%M") != "09:48" for t in trades[1:])


@pytest.mark.asyncio
async def test_the_exit_rule_body_floor(db):  # noqa: F811
    # Two small reds (bodies 0.03 %) under a 0.2 % floor: the long is held.
    trades = await _run(db, _DAY, colour_exit_on=True, colour_exit_count=2, colour_exit_body=0.2)
    assert _got(trades)[0][3] != "COLOUR_TURN"


def test_api_saves_the_exit_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"colour_exit_on", "colour_exit_count", "colour_exit_body", "colour_exit_inside"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert (got["colour_exit_on"], got["colour_exit_count"], got["colour_exit_body"], got["colour_exit_inside"]) == (False, 2, 0.2, False)
        ok = client.put("/api/config", json={"colour_exit_on": True, "colour_exit_count": 3, "colour_exit_body": 0.4, "colour_exit_inside": True})
        assert ok.status_code == 200
        j = ok.json()
        assert (j["colour_exit_on"], j["colour_exit_count"], j["colour_exit_body"], j["colour_exit_inside"]) == (True, 3, 0.4, True)
        assert client.put("/api/config", json={"colour_exit_count": 6}).status_code == 422
        assert client.put("/api/config/stock/SBIN", json={"colour_exit_on": True}).status_code == 200
    database.reset_engine()
