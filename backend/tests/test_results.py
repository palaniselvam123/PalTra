"""Quarterly results (app/services/results.py): NSE fields read loosely, YoY / QoQ, verdict, calendar, filings."""
import datetime as dt

from app.services import results

# MyAlgoMate's Alkem page (₹ crore): Q2 FY27 back to Q1 FY26. NSE gives ₹ lakh, so × 100 here.
ALKEM = [
    ("30-Sep-2026", 4551.99, 4686.2, 999.65, 667.13, 56.48),
    ("30-Jun-2026", 3740.15, 3892.9, 771.99, 520.98, 43.5),
    ("31-Mar-2026", 3603.32, 3803.39, 422.85, 251.11, 21.0),
    ("31-Dec-2025", 3736.82, 3879.92, 781.2, 653.03, 54.6),
    ("30-Sep-2025", 4000.99, 4104.68, 895.83, 779.11, 65.1),
    ("30-Jun-2025", 3371.14, 3507.63, 771.01, 667.91, 55.8),
]


def nse_rows(data=ALKEM):
    return {"resCmpData": [
        {"re_to_dt": end, "re_from_dt": None, "re_net_sale": f"{rev * 100:,.2f}", "re_total_inc": tot * 100,
         "re_pro_loss_bef_tax": pbt * 100, "re_net_profit": str(net * 100), "re_basic_eps_for_cont_dic_opr": eps,
         "re_res_type": "U", "re_oth_inc_new": 1.0}
        for end, rev, tot, pbt, net, eps in data
    ]}


def test_reads_nse_comparison_in_crore_and_labels_quarters():
    q = results.parse_comparison(nse_rows())
    assert [x["label"] for x in q] == ["Q2 FY27", "Q1 FY27", "Q4 FY26", "Q3 FY26", "Q2 FY26", "Q1 FY26"]
    assert q[0]["revenue"] == 4551.99 and q[0]["net_profit"] == 667.13 and q[0]["pbt"] == 999.65
    assert q[0]["total_income"] == 4686.2 and q[0]["eps"] == 56.48 and q[0]["audited"] is False


def test_alkem_is_mixed_sales_up_profit_down():
    a = results.analyze(results.parse_comparison(nse_rows()))
    g = a["growth"]
    assert g["revenue_yoy"] == 13.8 and g["profit_yoy"] == -14.4 and g["profit_qoq"] == 28.1
    assert a["compared_yoy"] == "Q2 FY26" and a["compared_qoq"] == "Q1 FY27"
    assert a["sentiment"] == "MIXED" and "sales +13.8%" in a["why"] and "net profit -14.4%" in a["why"]


def test_strong_growth_is_positive_high_and_a_fall_negative():
    good = [("30-Sep-2026", 1300, 1320, 260, 200, 10), ("30-Jun-2026", 1100, 1120, 200, 150, 7.5),
            ("30-Sep-2025", 1000, 1010, 170, 130, 6.5)]
    a = results.analyze(results.parse_comparison(nse_rows(good)))
    assert a["sentiment"] == "POSITIVE" and a["impact"] == "HIGH" and a["verdict"] == "POSITIVE_HIGH"
    bad = [("30-Sep-2026", 800, 810, 60, 40, 2), ("30-Jun-2026", 1000, 1010, 150, 110, 5.5),
           ("30-Sep-2025", 1000, 1010, 170, 130, 6.5)]
    b = results.analyze(results.parse_comparison(nse_rows(bad)))
    assert b["sentiment"] == "NEGATIVE" and b["impact"] == "HIGH"


def test_loss_to_profit_is_a_turnaround():
    rows = [("30-Sep-2026", 500, 510, 30, 20, 1), ("30-Jun-2026", 480, 490, -10, -12, -0.6),
            ("30-Sep-2025", 450, 460, -40, -35, -1.8)]
    a = results.analyze(results.parse_comparison(nse_rows(rows)))
    assert a["points"]["profit_yoy"] == 1.0 and "turned a loss into a profit vs last year" in a["why"]
    assert a["growth"]["profit_yoy"] is None and a["sentiment"] == "POSITIVE"


def test_no_quarters_is_no_data():
    assert results.analyze([])["verdict"] == "NO_DATA"
    one = results.analyze(results.parse_comparison(nse_rows(ALKEM[:1])))
    assert one["verdict"] == "NO_DATA" and one["quarter"] == "Q2 FY27"


def test_calendar_keeps_results_meetings_only_and_flags_fno():
    payload = [
        {"bm_symbol": "ALKEM", "sm_name": "Alkem Laboratories Limited", "bm_date": "12-Nov-2026",
         "bm_purpose": "Financial Results", "bm_desc": "To consider and approve the financial results"},
        {"bm_symbol": "ACE", "sm_name": "Action Construction", "bm_date": "13-Nov-2026", "bm_purpose": "Financial Results/Dividend"},
        {"bm_symbol": "XYZ", "sm_name": "Xyz Ltd", "bm_date": "13-Nov-2026", "bm_purpose": "Fund Raising", "bm_desc": "Bonus issue"},
    ]
    rows = results.parse_calendar(payload, {"ALKEM"})
    assert [(r["symbol"], r["date"], r["fno"]) for r in rows] == [("ALKEM", "2026-11-12", True), ("ACE", "2026-11-13", False)]
    ev = results.parse_calendar([{"symbol": "AMBER", "company": "Amber", "purpose": "Financial Results", "date": "12-Nov-2026"}], set())
    assert ev[0]["symbol"] == "AMBER" and ev[0]["date"] == "2026-11-12"


def test_filed_reads_symbol_period_and_time():
    rows = results.parse_filed([{"symbol": "ALKEM", "companyName": "Alkem", "toDate": "30-Sep-2026",
                                 "filingDate": "12-Nov-2026 14:05", "consolidated": "Consolidated", "audited": "Un-Audited"},
                                {"symbol": "ACE", "toDate": "30-Sep-2026", "filingDate": "12-Nov-2026 15:40"}], {"ALKEM"})
    assert rows[0]["symbol"] == "ACE" and rows[1]["quarter"] == "Q2 FY27" and rows[1]["filed_at"].startswith("2026-11-12T14:05")
    assert rows[1]["consolidated"] is True and rows[1]["fno"] is True


def test_quarter_labels_follow_the_indian_financial_year():
    assert results.quarter_label(dt.date(2026, 6, 30)) == "Q1 FY27"
    assert results.quarter_label(dt.date(2026, 12, 31)) == "Q3 FY27"
    assert results.quarter_label(dt.date(2027, 3, 31)) == "Q4 FY27"


def test_nse_down_is_an_error_message_not_a_crash(monkeypatch):
    import asyncio

    import httpx

    def refuse(request):
        raise httpx.ConnectError("blocked", request=request)

    real = httpx.AsyncClient

    def client(*a, **k):
        return real(*a, transport=httpx.MockTransport(refuse), **{x: y for x, y in k.items() if x != "transport"})

    monkeypatch.setattr(results.httpx, "AsyncClient", client)
    results._cache.clear()
    cal = asyncio.run(results.calendar(3))
    assert cal["error"] and cal["total"] == 0 and len(cal["days"]) == 3
    assert asyncio.run(results.filed())["error"]
    one = asyncio.run(results.stock("ALKEM"))
    assert one["error"] and one["analysis"]["verdict"] == "NO_DATA"
    assert asyncio.run(results.many(["ALKEM", "ACE"]))[1]["error"]
