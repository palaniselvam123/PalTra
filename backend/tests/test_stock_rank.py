"""Stock ranking (stock_rank.py): batches played in turn, each stock ranked from its closed trades."""
from __future__ import annotations

import pytest

import stock_rank as sr


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_RANK_FILE", str(tmp_path / "rank.json"))
    return tmp_path


def _t(symbol, date, gross, run=1, price=100.0, qty=10):
    return {"run_id": run, "symbol": symbol, "date": date, "gross": gross, "net": gross - 5, "entry_price": price, "qty": qty}


def _days(n):
    return [f"2026-10-{d:02d}" for d in range(1, n + 1)]


def test_batches_and_a_new_job(store):
    item = sr.new("2026-10-01", "2026-10-10", [f"S{i}" for i in range(7)], 2, 3, True)
    assert [r["time"] for r in item["runs"]] == ["1-3", "4-6", "7-7"]
    assert item["runs"][2]["symbols"] == ["S6"]
    assert sr.get(item["id"])["play_handoffs"] is True
    sr.interrupt_running()
    assert sr.get(item["id"])["status"] == "INTERRUPTED"


def test_ranking_verdicts_and_numbers():
    trades = []
    for d in _days(6):  # wins most days, big wins, small losses
        trades += [_t("WIN", d, 300), _t("WIN", d, -100)]
    for d in _days(6):  # loses
        trades += [_t("LOSE", d, -200), _t("LOSE", d, 50)]
    for d in _days(6):  # barely positive
        trades += [_t("THIN", d, 110), _t("THIN", d, -100)]
    trades += [_t("RARE", "2026-10-01", 500)]
    rows = {r["symbol"]: r for r in sr.rank(trades)}
    w = rows["WIN"]
    assert w["verdict"] == "STRONG" and w["trades"] == 12 and w["win_rate"] == 50.0
    assert w["gross"] == 1200 and w["green_pct"] == 100.0 and w["profit_factor"] == 3.0
    assert w["avg_return_pct"] == pytest.approx(10.0)  # (300 − 100) / 2 trades over ₹1,000 each
    assert w["best_trade"] == 300 and w["worst_trade"] == -100
    assert rows["LOSE"]["verdict"] == "AVOID"
    assert rows["THIN"]["verdict"] == "WEAK"
    assert rows["RARE"]["verdict"] == "FEW"
    order = [r["symbol"] for r in sr.rank(trades)]
    assert order == ["WIN", "THIN", "LOSE", "RARE"]
    assert [r["rank"] for r in sr.rank(trades)] == [1, 2, 3, 4]


def test_a_stock_day_in_two_runs_counts_once_the_newest():
    trades = [_t("SBIN", "2026-10-01", -500, run=1), _t("SBIN", "2026-10-01", 200, run=2), _t("SBIN", "2026-10-02", 100, run=1)]
    row = sr.rank(trades)[0]
    assert row["trades"] == 2 and row["gross"] == 300 and row["days"] == 2


def test_summary_lines():
    rows = sr.rank([_t("A", d, 100) for d in _days(5)] + [_t("B", d, -50) for d in _days(5)])
    out = sr.summary(rows, 10)
    assert out["counts"]["STRONG"] == 1 and out["counts"]["AVOID"] == 1
    assert any("Trading only the 1 strong and good" in line for line in out["answer"])
    assert sr.summary([], 5)["answer"][0].startswith("No closed trades")


class Player:
    def __init__(self):
        self.status, self.run_id, self.started = "IDLE", None, []

    async def start(self, symbols):
        self.started.append(list(symbols))
        self.run_id = (self.run_id or 0) + 1
        self.status = "FINISHED"
        return self.run_id

    def info(self):
        return self.status, self.run_id


@pytest.mark.asyncio
async def test_each_batch_plays_in_turn_and_is_saved_in_the_ranking_file(store, tmp_path):
    item = sr.new("2026-10-01", "2026-10-10", ["A", "B", "C"], 1, 2, True)
    p = Player()
    done = await sr.play(item, p.start, p.info, poll=0)
    assert p.started == [["A", "B"], ["C"]]
    assert done["status"] == "DONE" and [r["run_id"] for r in sr.get(item["id"])["runs"]] == [1, 2]
    assert not (tmp_path / "compare.json").exists()


def test_view_reads_the_runs_trades(store, tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    import datetime as dt
    import json

    import database
    from database import session_factory
    from models import ReplayRun, TradeLog

    database.reset_engine()
    database.init_db()
    with session_factory()() as s:
        for rid, bot in ((1, 2), (2, 2), (3, 1)):
            s.add(ReplayRun(id=rid, created_at=dt.datetime(2026, 10, 1), start_date="2026-10-01", end_date="2026-10-05",
                            settings=json.dumps({"bot": bot}), status="FINISHED"))
        for rid in (1, 2, 3):
            for d in _days(5):
                s.add(TradeLog(date=d, symbol=f"S{rid}", direction="LONG", qty=10, ma_cross_price=100.0,
                               atr_at_entry=1.0, sl_trigger_price=99.0,
                               entry_time=dt.datetime(2026, 10, 1, 10), entry_price=100.0, exit_price=101.0,
                               exit_time=dt.datetime(2026, 10, 1, 11), gross_pnl=10.0, net_pnl=5.0, mode="REPLAY", run_id=rid))
        s.commit()
    item = {"id": 1, "bot": 2, "universe": 3, "runs": [{"run_id": 1}]}
    got = sr.view(item)
    assert [r["symbol"] for r in got["rows"]] == ["S1"] and got["runs_used"] == 1
    more = sr.view(item, earlier_days=90, today=dt.date(2026, 10, 6))
    assert {r["symbol"] for r in more["rows"]} == {"S1", "S2"}  # Bot 1's run 3 is left out
    database.reset_engine()


def test_api_refuses_and_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("STOCK_RANK_FILE", str(tmp_path / "rank.json"))
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(main.engine.broker, "token", None, raising=False)
    monkeypatch.setattr(main.engine.broker, "adopt_saved_session", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(main, "_refuse_desk_replay_in_market", lambda *a, **k: None)
    with TestClient(main.app) as client:
        assert client.get("/api/stock-rank").json() == {"running": None, "items": []}
        base = {"date": "2026-10-01", "end_date": "2026-10-09", "universe": ["SBIN", "TCS"]}
        assert client.post("/api/stock-rank", json={**base, "batch": 7}).status_code == 422
        assert client.post("/api/stock-rank", json={**base, "universe": ["??"]}).status_code == 400
        assert client.post("/api/stock-rank", json=base).status_code == 400  # no Groww login
        assert client.get("/api/stock-rank").json()["items"] == []
        assert client.get("/api/stock-rank/9").status_code == 404
        assert client.post("/api/stock-rank/stop").json() == {"stopped": False}
    database.reset_engine()
