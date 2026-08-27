"""Persists paper (and eventually live) trades to the Trade table, and
computes the dashboard's summary stats from that history. Kept separate from
the FastAPI routers so both `main.py`'s background square-off task and the
order routes can call it without a request-scoped DB session.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app import state
from app.core.market_clock import ist_now
from app.models.database import Trade, async_session
from app.services.paper_engine import PaperCloseResult


async def record_open_trade(
    symbol: str,
    side: str,
    quantity: int,
    entry_price: float,
    stop_loss: float,
    target: float,
    source: str = "MANUAL",
    entry_charges: float = 0.0,
    account: str = state.ACCOUNT_AUTO,
    feed_source: str = "simulated",
) -> int:
    async with async_session() as session:
        trade = Trade(
            mode="paper",
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            status="OPEN",
            source=source,
            entry_charges=entry_charges,
            account=account,
            feed_source=feed_source,
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        return trade.id


async def _record_close(result: PaperCloseResult, exit_reason: str | None = None) -> None:
    if result.trade_id is None:
        return
    async with async_session() as session:
        trade = await session.get(Trade, result.trade_id)
        if trade:
            trade.exit_price = result.exit_price
            trade.pnl = result.pnl
            trade.status = "CLOSED"
            trade.exit_reason = exit_reason
            # The entry leg's charge was estimated at fill time; re-recording
            # it here keeps both legs consistent with the P&L actually booked.
            trade.entry_charges = result.entry_charges
            trade.exit_charges = result.exit_charges
            trade.closed_at = dt.datetime.utcnow()
            await session.commit()


async def close_symbol_and_persist(
    symbol: str, exit_reason: str | None = None, account: str = state.ACCOUNT_AUTO
) -> PaperCloseResult | None:
    """Closes a paper position at the current quote and writes the result to
    the trade history. Shared by the manual close endpoint, the kill switch,
    and the end-of-day IST hard cut-off scheduler.
    """
    from app.services.market_data import market_data

    quote = state.latest_quotes.get(symbol)
    if not quote:
        return None

    engine = state.engine_for(account)
    position = engine.positions.get(symbol)

    # A position must be closed against the SAME price series it was opened on.
    # Switching the data source mid-position and then closing books a P&L
    # computed across two unrelated worlds — that produced exits like
    # SHIPROCKET 136 -> 3309 and KOTAKBANK 416 -> 2428, six-figure "profits"
    # that never happened. Voiding is the honest outcome: the position existed,
    # but no valid exit price for it does.
    current_feed = market_data.source.value
    if position is not None and position.trade_id is not None:
        async with async_session() as session:
            trade = await session.get(Trade, position.trade_id)
            opened_on = (trade.feed_source if trade else None) or "unknown"
            if opened_on not in ("unknown", current_feed):
                if trade:
                    trade.status = "CANCELLED"
                    trade.pnl = 0.0
                    trade.exit_price = trade.entry_price
                    trade.exit_reason = (
                        f"VOIDED — opened against the {opened_on} feed but the source is now {current_feed}. "
                        "Closing across two price series would invent a P&L that never happened."
                    )
                    trade.closed_at = dt.datetime.utcnow()
                    await session.commit()
                engine.positions.pop(symbol, None)
                return None

    result = engine.close_position(symbol, quote["ltp"])
    if result is None:
        return None
    await _record_close(result, exit_reason)
    return result


async def restore_open_positions() -> tuple[list[str], list[str]]:
    """Rebuilds open positions from the ledger at startup.

    Positions lived only in `PaperEngine.positions` while their Trade row sat
    at status OPEN in the database. A restart therefore orphaned them twice
    over: the row stayed OPEN forever because nothing could ever close it, and
    the stop-loss and target silently stopped being enforced on a position the
    reports still showed as live.

    Restoring is only honest when the position's price series survived the
    restart. It does not on the simulated feed, whose prices are synthetic —
    a position opened at a simulated ₹180 and closed against the next run's
    ₹1,700 booked a six-figure profit that never happened. Those positions are
    voided instead: no outcome can be determined for them, and inventing one is
    worse than admitting it. Returns (restored, voided).
    """
    from app.services.market_data import market_data
    from app.services.paper_engine import PaperPosition

    current_feed = market_data.health().source

    async with async_session() as session:
        rows = (await session.execute(select(Trade).where(Trade.status == "OPEN"))).scalars().all()

        restored: list[str] = []
        voided: list[str] = []
        for trade in rows:
            opened_on = trade.feed_source or "unknown"
            # Void ONLY what is provably unrecoverable: a position opened
            # against the synthetic series, which is regenerated each run.
            # Live prices persist, so a live position always has a real
            # outcome — an earlier version of this check also voided anything
            # whose feed merely differed from the current one, which wrongly
            # destroyed five genuine NSE trades. When in doubt, restore: a
            # stale position can be closed by hand, a voided one cannot be
            # recovered.
            if opened_on == "simulated":
                trade.status = "CANCELLED"
                trade.pnl = 0.0
                trade.exit_price = trade.entry_price
                trade.exit_reason = (
                    f"VOIDED ON RESTART — opened against the {opened_on} feed, whose price series "
                    "does not survive a restart. No outcome could be determined."
                )
                trade.closed_at = dt.datetime.utcnow()
                voided.append(trade.symbol)
                continue

            engine = state.engine_for(trade.account or state.ACCOUNT_AUTO)
            if trade.symbol in engine.positions:
                continue
            engine.positions[trade.symbol] = PaperPosition(
                symbol=trade.symbol,
                side=trade.side,
                quantity=trade.quantity,
                entry_price=trade.entry_price,
                stop_loss=trade.stop_loss,
                target=trade.target,
                opened_at=trade.opened_at,
                order_id=f"RESTORED-{trade.id}",
                trade_id=trade.id,
            )
            restored.append(trade.symbol)

        await session.commit()

    return restored, voided


async def get_trade_history(
    limit: int = 100, account: str = state.ACCOUNT_AUTO, include_cancelled: bool = False
) -> list[dict]:
    """Finished trades for a wallet.

    `include_cancelled` also returns voided positions. They contribute nothing
    to P&L, but hiding them would misrepresent the record: a position was
    opened, and the fact that no outcome could be determined for it is itself
    worth seeing rather than silently dropping the row.
    """
    statuses = ["CLOSED", "CANCELLED"] if include_cancelled else ["CLOSED"]
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Trade)
                .where(Trade.status.in_(statuses), Trade.account == account)
                .order_by(Trade.closed_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "status": t.status,
                "exit_reason": t.exit_reason,
                "opened_at": t.opened_at,
                "closed_at": t.closed_at,
            }
            for t in rows
        ]


async def get_trade_summary(account: str = state.ACCOUNT_AUTO) -> dict:
    """Today's closed-trade stats, computed fresh from the DB so a page
    refresh never loses P&L — unlike a client-side WebSocket accumulator.
    """
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Trade).where(Trade.status == "CLOSED", Trade.account == account)
            )
        ).scalars().all()

    today = dt.date.today()
    todays = [t for t in rows if t.closed_at and t.closed_at.date() == today]
    todays.sort(key=lambda t: t.closed_at)

    total_pnl = sum(t.pnl or 0 for t in todays)
    wins = [t for t in todays if (t.pnl or 0) > 0]
    losses = [t for t in todays if (t.pnl or 0) < 0]
    win_rate_pct = (len(wins) / len(todays) * 100) if todays else 0.0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = gross_profit if gross_profit > 0 else 0.0

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for t in todays:
        equity += t.pnl or 0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    return {
        "total_pnl": round(total_pnl, 2),
        "trades_closed": len(todays),
        "win_rate_pct": round(win_rate_pct, 1),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_drawdown, 2),
    }


async def close_and_settle(
    symbol: str, reason: str, account: str = state.ACCOUNT_AUTO
) -> tuple[PaperCloseResult | None, object | None]:
    """The single close path used by manual closes, strategy auto-exits, the
    kill switch, and the end-of-day cut-off: square off, persist to history,
    charge the realized P&L against the daily risk state, and tell every
    connected UI. Returns (close_result, circuit_breaker_decision_or_None).
    """
    from app.services.broadcaster import broadcaster

    result = await close_symbol_and_persist(symbol, exit_reason=reason, account=account)
    if result is None:
        return None, None

    # The daily circuit breaker governs the strategy account only. The
    # manual desk is discretionary: the user is the risk manager there, and
    # letting their trades trip the bot's loss limit would halt the bot for
    # decisions it did not make.
    breaker = (
        state.risk_manager.record_realized_pnl(result.pnl)
        if account == state.ACCOUNT_AUTO
        else None
    )
    await broadcaster.publish(
        "log",
        {
            "level": "INFO" if result.pnl >= 0 else "WARN",
            "message": f"{reason}: closed {symbol} {result.side} x{result.quantity} "
            f"@ ₹{result.exit_price} → P&L ₹{result.pnl}",
        },
    )
    await broadcaster.publish("position_closed", {"symbol": symbol, "pnl": result.pnl, "reason": reason})

    from app.services import notifications

    await notifications.trade_closed(
        account=account,
        symbol=symbol,
        side=result.side,
        quantity=result.quantity,
        entry_price=result.entry_price,
        exit_price=result.exit_price,
        pnl=result.pnl,
        reason=_explain_exit(reason, result),
    )
    return result, breaker


def _explain_exit(reason: str, result: PaperCloseResult) -> str:
    """Turns a terse exit code into something readable in a notification.

    A bare "TARGET HIT" next to a negative P&L is the single most confusing
    thing this app can show, so when costs are what turned a winning move into
    a loss, the alert says so outright.
    """
    charges = (result.entry_charges or 0.0) + (result.exit_charges or 0.0)
    gross = result.pnl + charges
    detail = f"{reason}. Gross {gross:+,.2f} less {charges:,.2f} charges."
    if gross > 0 and result.pnl <= 0:
        detail += " The price moved your way but costs exceeded the gain."
    elif charges > abs(gross) and gross != 0:
        detail += " Costs were larger than the entire price move."
    return detail


async def get_account_summary(account: str = state.ACCOUNT_AUTO) -> dict:
    """The virtual wallet.

    Balance is starting capital plus ALL realised P&L to date (not just
    today's), so it carries across sessions. Open positions are reported as
    notional exposure rather than deducted from cash: intraday MIS is a
    leveraged product, so exposure routinely exceeds the cash balance and
    subtracting it would show a misleading negative.
    """
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Trade).where(Trade.status == "CLOSED", Trade.account == account)
            )
        ).scalars().all()

    starting_capital = state.capital_for(account)
    realised_all_time = round(sum(t.pnl or 0 for t in rows), 2)

    today = ist_now().date()
    realised_today = round(sum(t.pnl or 0 for t in rows if t.closed_at and t.closed_at.date() == today), 2)

    exposure = 0.0
    unrealised = 0.0
    for pos in state.engine_for(account).positions.values():
        quote = state.latest_quotes.get(pos.symbol)
        ltp = quote["ltp"] if quote else pos.entry_price
        exposure += pos.entry_price * pos.quantity
        if pos.side == "BUY":
            unrealised += (ltp - pos.entry_price) * pos.quantity
        else:
            unrealised += (pos.entry_price - ltp) * pos.quantity

    balance = round(starting_capital + realised_all_time, 2)
    equity = round(balance + unrealised, 2)

    return {
        "starting_capital": round(starting_capital, 2),
        "balance": balance,
        "realised_all_time": realised_all_time,
        "realised_today": realised_today,
        "unrealised": round(unrealised, 2),
        "equity": equity,
        "open_exposure": round(exposure, 2),
        # >1 means positions are larger than the cash backing them (MIS leverage).
        "exposure_ratio": round(exposure / balance, 2) if balance > 0 else 0.0,
        "open_positions": len(state.paper_engine.positions),
        "return_pct": round((balance - starting_capital) / starting_capital * 100, 2) if starting_capital else 0.0,
    }
