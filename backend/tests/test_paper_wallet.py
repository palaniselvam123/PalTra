"""Practice wallet for the PAPER bots: margin blocked per entry, loans when short.

PAPER only, local fills (stub Groww session). LIVE, replay and research
engines never touch the wallet, and with no money loaded PAPER trades as before.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, tzinfo=IST)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    import database

    database.reset_engine()
    database.init_db()
    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    engine = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    yield engine
    database.reset_engine()


def _cfg(eng, symbol: str = "RELIANCE", qty: int = 100):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.symbol = symbol
        row.trade_symbols = symbol
        row.qty = qty
        row.use_stop = False
        db.commit()
    return eng.load_config()


def _load(amount: float):
    """Load money, with the account starting before the test's trades."""
    import database
    import paper_wallet
    from models import PaperWallet

    paper_wallet.add(amount)
    with database.session_factory()() as db:
        db.get(PaperWallet, 1).since = dt.datetime(2026, 1, 1)
        db.commit()


async def _buy(eng, cfg, price: float):
    eng._focus = cfg.symbol
    eng._ltps[cfg.symbol] = price
    await eng._open("LONG", price, 2.0, cfg, NOW)


@pytest.mark.asyncio
async def test_an_entry_blocks_20_percent_and_the_rest_stays_free(eng):
    import paper_wallet

    _load(1000)
    await _buy(eng, _cfg(eng, qty=100), 10.0)  # 100 x Rs 10 = Rs 1,000 -> Rs 200 margin
    w = paper_wallet.summary()
    assert w["blocked"] == pytest.approx(200.0)
    assert w["available"] == pytest.approx(800.0)
    assert w["loan"] == 0
    assert eng.snapshot()["wallet_loan"] is None


@pytest.mark.asyncio
async def test_a_short_balance_borrows_the_gap_and_still_places_the_order(eng):
    import paper_wallet

    _load(100)
    await _buy(eng, _cfg(eng, qty=100), 10.0)  # needs Rs 200, Rs 100 free -> borrow Rs 100
    assert "RELIANCE" in eng.positions  # the order went
    w = paper_wallet.summary()
    assert w["loan"] == pytest.approx(100.0)
    assert w["available"] == pytest.approx(0.0)
    loan = eng.snapshot()["wallet_loan"]
    assert loan["borrowed"] == pytest.approx(100.0) and loan["loan"] == pytest.approx(100.0)
    assert "Loan to pay back" in loan["text"]


@pytest.mark.asyncio
async def test_closing_returns_the_margin_and_the_pnl_before_charges(eng):
    import paper_wallet

    _load(1000)
    cfg = _cfg(eng, qty=100)
    await _buy(eng, cfg, 10.0)
    await eng._exit_now(cfg, 12.0, "MANUAL_CLOSE")  # +Rs 2 x 100 = +Rs 200 before charges
    w = paper_wallet.summary()
    assert w["blocked"] == 0
    assert w["realized"] == pytest.approx(200.0)
    assert w["available"] == pytest.approx(1200.0)


@pytest.mark.asyncio
async def test_repay_pays_the_loan_from_the_free_balance(eng):
    import paper_wallet

    _load(100)
    cfg = _cfg(eng, qty=100)
    await _buy(eng, cfg, 10.0)  # loan Rs 100
    await eng._exit_now(cfg, 10.0, "MANUAL_CLOSE")  # margin back, flat P&L: Rs 200 free, Rs 100 owed
    w = paper_wallet.repay()
    assert w["loan"] == 0
    assert w["available"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_no_money_loaded_trades_as_before(eng):
    import paper_wallet

    await _buy(eng, _cfg(eng, qty=100), 10.0)
    assert "RELIANCE" in eng.positions
    assert paper_wallet.summary()["active"] is False
    assert eng.snapshot()["wallet_loan"] is None


def test_replay_and_research_engines_never_use_the_wallet():
    from bots import BotEngine
    from replay import ReplayEngine
    from research import ResearchEngine
    from strategy_engine import StrategyEngine

    assert StrategyEngine.uses_wallet and BotEngine.uses_wallet
    assert not ReplayEngine.uses_wallet and not ResearchEngine.uses_wallet


def test_load_limits_and_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/wallet").json()["active"] is False
        assert client.post("/api/wallet/add", json={"amount": 0}).status_code == 422
        assert client.post("/api/wallet/add", json={"amount": 1_000_000_001}).status_code == 422
        w = client.post("/api/wallet/add", json={"amount": 1_000_000_000}).json()
        assert w["active"] and w["available"] == 1_000_000_000
        assert client.put("/api/wallet/margin", json={"margin_pct": 100}).json()["margin_pct"] == 100
        assert client.post("/api/wallet/withdraw", json={"amount": 2_000_000_000}).status_code == 422
        assert client.post("/api/wallet/withdraw", json={"amount": 1}).json()["available"] == 999_999_999
        assert client.post("/api/wallet/repay", json={}).status_code == 422  # nothing owed
        assert client.post("/api/wallet/reset").json()["active"] is False
    database.reset_engine()


@pytest.mark.asyncio
async def test_loans_are_kept_as_records_and_repaid_oldest_first(eng):
    import paper_wallet

    _load(100)
    await _buy(eng, _cfg(eng, "RELIANCE", qty=100), 10.0)  # needs 200, 100 free -> loan #1 Rs 100
    await _buy(eng, _cfg(eng, "TCS", qty=10), 30.0)  # needs 60, 0 free -> loan #2 Rs 60
    loans = paper_wallet.loans()
    assert [(l["symbol"], l["amount"], l["status"]) for l in loans] == [("TCS", 60.0, "OPEN"), ("RELIANCE", 100.0, "OPEN")]
    first = loans[-1]
    assert first["bot"] == 1 and first["qty"] == 100 and first["price"] == 10.0 and first["need"] == 200.0
    assert paper_wallet.summary()["loan"] == pytest.approx(160.0)
    assert paper_wallet.summary()["open_loans"] == 2

    paper_wallet.add(130)  # free 130
    paper_wallet.repay(130)  # clears loan #1 (100) and 30 of loan #2
    by_symbol = {l["symbol"]: l for l in paper_wallet.loans()}
    assert by_symbol["RELIANCE"]["status"] == "REPAID" and by_symbol["RELIANCE"]["due"] == 0
    assert by_symbol["TCS"]["repaid"] == pytest.approx(30.0) and by_symbol["TCS"]["due"] == pytest.approx(30.0)
    assert paper_wallet.summary()["loan"] == pytest.approx(30.0)

    lines = paper_wallet.statement()
    assert [l["kind"] for l in lines] == ["REPAY", "ADD", "LOAN", "LOAN", "ADD"]
    assert lines[0]["amount"] == 130.0 and lines[0]["loan_after"] == pytest.approx(30.0)
    assert "RELIANCE" in lines[0]["note"] and "TCS" in lines[0]["note"]


def test_close_writes_off_the_loan_on_the_statement(eng):
    import paper_wallet

    paper_wallet.add(50)
    paper_wallet.set_margin(50)
    paper_wallet.reset()
    kinds = [l["kind"] for l in paper_wallet.statement()]
    assert kinds == ["RESET", "MARGIN", "ADD"]
    assert "Margin 20% -> 50%" in paper_wallet.statement()[1]["note"]
