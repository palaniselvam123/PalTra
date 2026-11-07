"""Pick-time comparison (trend_compare.py): times played one after another, side-by-side result, API refusals."""
from __future__ import annotations

import asyncio

import pytest

import trend_compare as tc


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_COMPARE_FILE", str(tmp_path / "compare.json"))
    return tmp_path


def _item(times=("10:00", "11:00", "12:00")):
    return tc.new("2026-10-01", "2026-10-10", list(times), 10, "BOTH", 5, 1, 120)


def test_times_are_checked_sorted_and_unique():
    assert tc.clean_times(["11:00", "10:00", "10:00"]) == ["10:00", "11:00"]
    with pytest.raises(ValueError):
        tc.clean_times(["10:00"])
    with pytest.raises(ValueError):
        tc.clean_times(["10:00", "15:00"])
    with pytest.raises(ValueError):
        tc.clean_times(["10:00", "ten"])
    with pytest.raises(ValueError):
        tc.clean_times([f"1{h}:00" for h in range(0, 5)] + ["09:30", "09:45", "09:50", "09:55"])


class FakePlayer:
    """A replay player: each start finishes after `ticks` polls."""

    def __init__(self, ticks=2, refuse=()):
        self.status, self.run_id, self.left = "IDLE", None, 0
        self.ticks, self.refuse, self.started, self.next_id = ticks, set(refuse), [], 100

    async def start(self, at):
        if at in self.refuse:
            raise RuntimeError("no candles")
        self.started.append(at)
        self.next_id += 1
        self.status, self.run_id, self.left = "PLAYING", self.next_id, self.ticks
        return self.run_id

    def info(self):
        if self.status == "PLAYING":
            self.left -= 1
            if self.left <= 0:
                self.status = "FINISHED"
        return self.status, self.run_id


@pytest.mark.asyncio
async def test_each_time_plays_in_turn_and_a_refusal_skips_only_that_time(store):
    item = _item()
    player = FakePlayer(refuse={"11:00"})
    done = await tc.play(item, player.start, player.info, poll=0)
    assert player.started == ["10:00", "12:00"]
    assert [r["status"] for r in done["runs"]] == ["DONE", "SKIPPED", "DONE"]
    assert done["status"] == "DONE"
    assert tc.get(item["id"])["runs"][2]["run_id"] == 102


@pytest.mark.asyncio
async def test_it_waits_for_a_busy_player_and_notes_a_takeover(store):
    item = _item(("10:00", "11:00"))
    player = FakePlayer(ticks=50)
    player.status = "PLAYING"  # someone else's replay
    calls = {"n": 0}

    def info():
        calls["n"] += 1
        if player.run_id == 102:
            player.left = min(player.left, 1)
            return player.info()
        if calls["n"] == 3 and player.run_id is None:
            player.status = "FINISHED"
        if player.run_id == 101 and calls["n"] > 6:
            player.run_id = 999  # another replay took the player
            player.status = "PLAYING"
        if player.run_id == 999 and calls["n"] > 9:
            player.status = "FINISHED"
        return player.status, player.run_id

    done = await tc.play(item, player.start, info, poll=0)
    assert done["runs"][0]["status"] == "INTERRUPTED"
    assert player.started == ["10:00", "11:00"]


@pytest.mark.asyncio
async def test_stop_keeps_finished_times_and_stops_the_replay(store):
    item = _item()
    player = FakePlayer(ticks=10_000)
    stopped = []
    flag = {"stop": False}

    async def stop():
        stopped.append(True)

    task = asyncio.create_task(tc.play(item, player.start, player.info, stop_fn=stop, poll=0.001,
                                       should_stop=lambda: flag["stop"]))
    await asyncio.sleep(0.02)
    flag["stop"] = True
    done = await task
    assert done["status"] == "STOPPED" and stopped == [True]
    assert all(r["status"] == "STOPPED" for r in done["runs"])


def test_interrupted_at_boot(store):
    item = _item()
    tc.interrupt_running()
    again = tc.get(item["id"])
    assert again["status"] == "INTERRUPTED" and again["runs"][0]["status"] == "INTERRUPTED"
    assert tc.delete(item["id"]) and tc.get(item["id"]) is None


def _run(days, settings=None, picks=None):
    gross = [g for _, g in days]
    return {
        "days": [{"date": d, "gross": g} for d, g in days],
        "totals": {"trades": 2 * len(days), "wins": sum(1 for g in gross if g > 0) * 2, "net": sum(gross) - 10,
                   "charges": 10, "win_rate": 50.0},
        "days_done": len(days), "days_total": len(days),
        "settings": {"qty": 10, "scalp_pick": {"picks": picks or {}}, **(settings or {})},
    }


def test_summary_ranks_the_times_and_picks_each_days_best():
    item = {"runs": [
        {"time": "10:00", "run_id": 1, "status": "DONE"},
        {"time": "11:00", "run_id": 2, "status": "DONE"},
        {"time": "12:00", "run_id": None, "status": "SKIPPED", "note": "not started: no candles"},
    ]}
    runs = {
        1: _run([("2026-10-01", 500), ("2026-10-02", -800), ("2026-10-05", 900)],
                picks={"2026-10-01": [{"symbol": "SBIN"}, {"symbol": "TCS"}], "2026-10-02": [{"symbol": "SBIN"}]}),
        2: _run([("2026-10-01", 100), ("2026-10-02", 200), ("2026-10-05", 300)]),
    }
    out = tc.summarize(item, runs)
    rows = {r["time"]: r for r in out["rows"]}
    assert rows["10:00"]["gross"] == 600 and rows["11:00"]["gross"] == 600
    assert rows["10:00"]["drawdown"] == -800 and rows["11:00"]["drawdown"] == 0
    assert rows["10:00"]["green_days"] == 2 and rows["11:00"]["green_days"] == 3
    assert rows["10:00"]["stocks_picked"] == 2
    assert rows["12:00"]["has_result"] is False
    # Same total: more green days ranks first.
    assert rows["11:00"]["rank"] == 1 and rows["10:00"]["rank"] == 2
    assert out["best"]["gross"] is None  # a tie names no time
    assert out["best"]["green_days"] == "11:00" and out["best"]["drawdown"] == "11:00"
    assert [d["best"] for d in out["days"]] == ["10:00", "11:00", "10:00"]
    assert rows["10:00"]["best_days"] == 2 and out["best"]["best_days"] == "10:00"
    assert not out["settings_differ"]
    assert any("Only 3 day(s)" in line for line in out["answer"])


def test_summary_warns_when_the_settings_changed_between_runs():
    item = {"runs": [{"time": "10:00", "run_id": 1}, {"time": "11:00", "run_id": 2}]}
    out = tc.summarize(item, {1: _run([("2026-10-01", 5)]), 2: _run([("2026-10-01", 5)], {"qty": 20})})
    assert out["settings_differ"] and any("settings changed" in line for line in out["answer"])
    assert tc.summarize({"runs": []}, {})["answer"] == ["No time has a result yet."]


def test_api_refuses_bad_requests_and_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TREND_COMPARE_FILE", str(tmp_path / "compare.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        assert client.get("/api/trend-picks/compare").json() == {"running": None, "items": []}
        base = {"date": "2026-10-01", "end_date": "2026-10-09", "universe": ["SBIN"]}
        assert client.post("/api/trend-picks/compare", json={**base, "times": ["10:00"]}).status_code == 422
        assert client.post("/api/trend-picks/compare", json={**base, "times": ["10:00", "15:00"]}).status_code == 400
        assert client.post("/api/trend-picks/compare", json={**base, "times": ["10:00", "11:00"]}).status_code == 400
        assert client.get("/api/trend-picks/compare/5").status_code == 404
        assert client.post("/api/trend-picks/compare/stop").json() == {"stopped": False}
    database.reset_engine()
