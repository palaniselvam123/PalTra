"""Morning trend test (morning_study.py): label the morning, measure the rest of the day. Read-only."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import morning_study as ms
from tests.test_gap_mode import _day

DAYS = [dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)]


def _falls_then_bounces(i, p):
    # 09:15-10:30 (75 min) falls 1.5 %, keeps falling to 11:30, then is bought back up to the close.
    if i < 135:
        return p - 0.2
    return p + 0.113


def _rises_all_day(i, p):
    return p + 0.1


def _tape() -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731
    rows = _day(dt.date(2026, 10, 30), flat)
    rows += _day(DAYS[0], _falls_then_bounces)
    rows += _day(DAYS[1], _rises_all_day)
    rows += _day(DAYS[2], flat)
    rows += _day(dt.date(2026, 11, 5), _rises_all_day)  # the next day, for the gap / next close
    return pd.DataFrame(rows)


def test_each_morning_is_labelled_and_the_rest_of_the_day_measured():
    got = {s["day"]: s for s in ms.analyze_stock("SBIN", _tape(), DAYS, "10:30", 5, 9, 21)}
    bear, bull, flat = got["2026-11-02"], got["2026-11-03"], got["2026-11-04"]
    assert bear["label"] == "BEARISH" and bear["morning_move_pct"] < -0.5
    assert bear["rest_pct"] > 0 and bear["day_low_at"] == "11:29" and bear["bounce_pct"] > 1
    assert bull["label"] == "BULLISH" and bull["rest_pct"] > 0 and bull["day_high_at"] >= "15:00"
    assert flat["label"] == "SIDEWAYS"
    assert bull["next_gap_pct"] is not None and flat["next_day_pct"] is not None


def test_summary_answers_in_plain_words():
    samples = ms.analyze_stock("SBIN", _tape(), DAYS, "10:30", 5, 9, 21)
    rep = ms.summarize(samples, "10:30")
    assert rep["groups"]["BEARISH"]["days"] == 1 and rep["groups"]["BEARISH"]["closed_higher_pct"] == 100.0
    assert rep["groups"]["BULLISH"]["closed_higher_pct"] == 100.0
    text = " ".join(rep["answer"])
    assert "fell in the morning" in text and "1 of 1 days (100%)" in text and "bought back up" in text
    assert rep["by_stock"][0]["symbol"] == "SBIN" and rep["by_stock"][0]["bearish"]["days"] == 1


def test_a_short_session_is_skipped_and_labels_need_all_three_signs():
    tape = _tape()
    short = tape[tape["ts"] < int(dt.datetime(2026, 11, 3, 11, 0, tzinfo=ms.IST).timestamp())]
    days = {s["day"] for s in ms.analyze_stock("SBIN", short, DAYS, "10:30", 5, 9, 21)}
    assert "2026-11-03" not in days
    assert ms.label(1.0, "UP", 0.8, 0.5) == "BULLISH"
    assert ms.label(1.0, "DOWN", 0.8, 0.5) == "SIDEWAYS"  # the SMAs disagree
    assert ms.label(1.0, "UP", 0.1, 0.5) == "SIDEWAYS"  # a zig-zag morning
    assert ms.label(-0.6, "DOWN", 0.5, 0.5) == "BEARISH"


@pytest.mark.asyncio
async def test_the_study_runs_and_keeps_its_report(tmp_path, monkeypatch):
    monkeypatch.setenv("MORNING_STUDY_FILE", str(tmp_path / "m.json"))
    monkeypatch.setattr(ms, "trading_days", lambda end, count: DAYS)
    tape = _tape()

    async def fetch(name, start, end):
        return tape

    s = ms.MorningStudy()
    with pytest.raises(ValueError):
        s.start(["SBIN"], fetch, 3, "09:45", 5, 9, 21)
    s.start(["SBIN", "TCS"], fetch, 3, "10:30", 5, 9, 21, with_delivery=False)
    await s._task
    view = s.view()
    assert view["status"] == "DONE" and view["report"]["samples"] == 6
    assert ms.MorningStudy().report["samples"] == 6  # read back from the file


def test_api_reads_and_refuses_bad_choices(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("MORNING_STUDY_FILE", str(tmp_path / "m.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/morning-study").json()
        assert got["cut_times"] == ["10:00", "10:30", "11:00", "12:00"]
        assert client.post("/api/morning-study/start", json={"symbols": ["SBIN"], "at": "09:45"}).status_code == 422
        assert client.post("/api/morning-study/start", json={"symbols": ["SBIN"], "days": 99}).status_code == 422
    database.reset_engine()
