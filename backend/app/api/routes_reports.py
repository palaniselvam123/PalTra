from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.models.database import AiAnalysis, Trade, async_session
from app.services.reports import ReportFilters, build_row, export_csv, get_filter_options, get_report

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _parse_date(value: str | None, label: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, f"{label} must be an ISO date (YYYY-MM-DD)") from exc


def _filters(
    date_from: str | None,
    date_to: str | None,
    symbols: str | None,
    side: str | None,
    source: str | None,
    strategy: str | None,
    status: str | None,
    outcome: str | None,
    search: str | None,
    account: str | None = None,
) -> ReportFilters:
    return ReportFilters(
        date_from=_parse_date(date_from, "date_from"),
        date_to=_parse_date(date_to, "date_to"),
        symbols=[s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else [],
        side=side.upper() if side else None,
        source=source.upper() if source else None,
        strategy=strategy or None,
        status=status.upper() if status else None,
        outcome=outcome.upper() if outcome else None,
        search=search or None,
        account=account.upper() if account else None,
    )


@router.get("/options")
async def report_options():
    """Distinct values present in the ledger, so the UI's filter dropdowns
    only offer things that actually exist.
    """
    return await get_filter_options()


@router.get("/full")
async def full_report(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    symbols: str | None = Query(None, description="Comma-separated symbols"),
    side: str | None = Query(None),
    source: str | None = Query(None),
    strategy: str | None = Query(None),
    status: str | None = Query(None),
    outcome: str | None = Query(None),
    search: str | None = Query(None),
    account: str | None = Query(None, description="AUTO or MANUAL"),
):
    return await get_report(_filters(date_from, date_to, symbols, side, source, strategy, status, outcome, search, account))


@router.get("/export.csv", response_class=PlainTextResponse)
async def export_report_csv(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    symbols: str | None = Query(None),
    side: str | None = Query(None),
    source: str | None = Query(None),
    strategy: str | None = Query(None),
    status: str | None = Query(None),
    outcome: str | None = Query(None),
    search: str | None = Query(None),
    account: str | None = Query(None, description="AUTO or MANUAL"),
):
    body = await export_csv(
        _filters(date_from, date_to, symbols, side, source, strategy, status, outcome, search, account)
    )
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="transactions-{stamp}.csv"'},
    )


@router.get("/transaction/{trade_id}")
async def transaction_detail(trade_id: int):
    """One transaction with everything attached to it, including any expert
    views recorded against it — that is how a bot entry's reasoning is audited
    after the fact.
    """
    async with async_session() as session:
        trade = await session.get(Trade, trade_id)
        if trade is None:
            raise HTTPException(404, f"No transaction with id {trade_id}")
        analyses = (
            await session.execute(
                select(AiAnalysis).where(AiAnalysis.trade_id == trade_id).order_by(AiAnalysis.created_at.desc())
            )
        ).scalars().all()

    return {
        "transaction": build_row(trade),
        "ai_analyses": [
            {
                "id": a.id,
                "stance": a.stance,
                "conviction": a.conviction,
                "sentiment_label": a.sentiment_label,
                "sentiment_score": a.sentiment_score,
                "thesis": a.thesis,
                "catalysts": json.loads(a.catalysts or "[]"),
                "risks": json.loads(a.risks or "[]"),
                "sources": json.loads(a.sources or "[]"),
                "requested_by": a.requested_by,
                "gate_passed": a.gate_passed,
                "gate_reason": a.gate_reason,
                "model": a.model,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in analyses
        ],
    }
