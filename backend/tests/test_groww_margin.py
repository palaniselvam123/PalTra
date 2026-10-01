"""Groww margin parsing for the manual desk wallet."""
from app.brokers.groww_client import parse_margin_payload


def test_margin_payload_reads_cash_and_mis_buying_power():
    parsed = parse_margin_payload(
        {
            "status": "SUCCESS",
            "payload": {
                "clear_cash": 5000,
                "net_margin_used": 35000,
                "equity_margin_details": {
                    "cnc_balance_available": 9000,
                    "mis_balance_available": 1000.5,
                },
            },
        }
    )
    assert parsed["clear_cash"] == 5000.0
    assert parsed["mis_balance_available"] == 1000.5
    assert parsed["cnc_balance_available"] == 9000.0
    assert parsed["net_margin_used"] == 35000.0


def test_margin_payload_accepts_an_unwrapped_body():
    parsed = parse_margin_payload(
        {
            "clear_cash": 96.21,
            "net_margin_used": 1.8,
            "equity_margin_details": {"mis_balance_available": 94.41, "cnc_balance_available": 94.41},
        }
    )
    assert parsed["clear_cash"] == 96.21
    assert parsed["mis_balance_available"] == 94.41


def test_missing_fields_are_zero():
    parsed = parse_margin_payload({"payload": {}})
    assert parsed == {
        "clear_cash": 0.0,
        "mis_balance_available": 0.0,
        "cnc_balance_available": 0.0,
        "net_margin_used": 0.0,
    }
