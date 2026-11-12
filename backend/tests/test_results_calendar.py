"""Results calendar (results_calendar.py): NSE's results meetings joined with the real filing times. No network."""
from __future__ import annotations

import datetime as dt

from app.services import results_calendar as rc
from app.services import stock_news as sn

IST = sn.IST
TODAY = dt.date(2026, 11, 12)

EVENTS = [
    {"symbol": "ALICON", "company": "Alicon Castalloy Limited", "purpose": "Financial Results",
     "bm_desc": "To consider and approve the financial results for the period ended September 30, 2026", "date": "12-Nov-2026"},
    {"symbol": "HINDALCO", "company": "Hindalco Industries Limited", "purpose": "Financial Results",
     "bm_desc": "To consider and approve the financial results", "date": "10-Nov-2026"},
    {"symbol": "BAJFINANCE", "company": "Bajaj Finance Limited", "purpose": "Other business matters",
     "bm_desc": "Fund raising", "date": "10-Nov-2026"},
    {"symbol": "TCS", "company": "TCS", "purpose": "Financial Results", "bm_desc": "", "date": "20-Nov-2026"},
    {"symbol": "OLDCO", "company": "Old", "purpose": "Financial Results", "bm_desc": "", "date": "09-Nov-2026"},
]
ANNOUNCEMENTS = [
    {"symbol": "ALICON", "desc": "Outcome of Board Meeting", "attchmntText": "results", "sort_date": "2026-11-12 16:20:04",
     "attchmntFile": "https://x/alicon1.pdf"},
    {"symbol": "ALICON", "desc": "Integrated Filing- Financial", "attchmntText": "Financial Results for the quarter",
     "sort_date": "2026-11-12 16:56:04", "attchmntFile": "https://x/alicon2.pdf"},
    {"symbol": "HINDALCO", "desc": "Financial Result Updates", "attchmntText": "", "sort_date": "2026-11-10 13:46:00"},
    {"symbol": "NOCAL", "desc": "Financial Result Updates", "attchmntText": "", "sort_date": "2026-11-11 08:40:00"},
    {"symbol": "NOCAL", "desc": "Press Release", "attchmntText": "", "sort_date": "2026-11-11 09:00:00"},
    {"symbol": "LATECO", "desc": "Reasons for Delayed/Non-submission of Financial Results", "sort_date": "2026-11-11 10:00:00"},
    {"symbol": "GENCO", "desc": "General Updates", "attchmntText": "results for the quarter discussed", "sort_date": "2026-11-11 11:00:00"},
]


def rows():
    meetings = rc.parse_calendar(EVENTS)
    filings = rc.results_filings(sn.parse_nse_market(ANNOUNCEMENTS))
    return {(r["symbol"], r["meeting_date"]): r for r in rc.build(meetings, filings, TODAY, end=dt.date(2026, 11, 20))}


def test_meetings_are_results_only_and_joined_with_the_first_filing():
    got = rows()
    assert ("BAJFINANCE", "2026-11-10") not in got  # not a results meeting
    alicon = got[("ALICON", "2026-11-12")]
    assert alicon["status"] == "FILED" and alicon["filed_at"] == "2026-11-12T16:20+05:30" and alicon["filings"] == 2
    assert alicon["session"] == "AFTER_CLOSE" and alicon["trades_on"] == "2026-11-13"  # the market sees it Friday
    hind = got[("HINDALCO", "2026-11-10")]
    assert hind["session"] == "IN_MARKET" and hind["trades_on"] == "2026-11-10"
    assert got[("TCS", "2026-11-20")]["status"] == "UPCOMING"
    assert got[("OLDCO", "2026-11-09")]["status"] == "NOT_SEEN"
    # A results filing with no meeting in the range is listed too, once per day; a press release is not.
    nocal = got[("NOCAL", "2026-11-11")]
    assert nocal["from_filing"] and nocal["session"] == "BEFORE_OPEN"
    assert sum(1 for k in got if k[0] == "ALICON") == 1
    assert not any(k[0] in ("LATECO", "GENCO") for k in got)  # a delay notice / general update is not a results filing


def test_sessions_and_the_first_trading_day():
    fri_eve = dt.datetime(2026, 11, 13, 18, 0, tzinfo=IST)
    assert rc.session_of(fri_eve) == "AFTER_CLOSE" and rc.first_trading_day(fri_eve) == dt.date(2026, 11, 16)
    sat = dt.datetime(2026, 11, 14, 11, 0, tzinfo=IST)
    assert rc.session_of(sat) == "AFTER_CLOSE" and rc.first_trading_day(sat) == dt.date(2026, 11, 16)
    morning = dt.datetime(2026, 11, 12, 8, 0, tzinfo=IST)
    assert rc.session_of(morning) == "BEFORE_OPEN" and rc.first_trading_day(morning) == dt.date(2026, 11, 12)


def test_board_meetings_key_set_is_read_too():
    got = rc.parse_calendar({"data": [{"bm_symbol": "abc", "bm_date": "12-Nov-2026", "bm_purpose": "Financial Results",
                                        "bm_desc": "Quarterly results", "sm_name": "ABC Ltd"}]})
    assert got == [{"symbol": "ABC", "company": "ABC Ltd", "meeting_date": "2026-11-12", "purpose": "Financial Results",
                    "desc": "Quarterly results"}]


def test_route_narrows_to_fno_and_refuses_bad_ranges(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes_news
    from app.main import app
    from app.services import news_impact

    async def fake(start, end, force=False, now=None):
        return {"from": start.isoformat(), "to": end.isoformat(), "rows": list(rows().values()), "errors": {}}

    async def watch():
        return {"ALICON": {"armed": ["Bot 1"], "held": []}}

    monkeypatch.setattr(rc, "calendar", fake)
    monkeypatch.setattr(news_impact, "_fno_set", lambda: {"HINDALCO", "TCS"})
    monkeypatch.setattr(routes_news, "_watch", watch)
    with TestClient(app) as client:
        out = client.get("/api/news-impact/results?from=2026-11-09&to=2026-11-20").json()
        assert {r["symbol"] for r in out["rows"]} == {"HINDALCO", "TCS"}
        mine = client.get("/api/news-impact/results?from=2026-11-09&to=2026-11-20&scope=mine").json()
        assert [r["symbol"] for r in mine["rows"]] == ["ALICON"] and mine["rows"][0]["armed"] is True
        assert client.get("/api/news-impact/results?from=2026-11-20&to=2026-11-09").status_code == 400
        assert client.get("/api/news-impact/results?from=2026-09-01&to=2026-11-20").status_code == 400
