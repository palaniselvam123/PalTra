"""Scalp monitor API: rank the streaming stocks for scalping, and optionally
send a Telegram when one becomes scalp-ready.

Watch only. Nothing in this module places, changes or cancels an order.
"""
from __future__ import annotations

import asyncio
import datetime as dt

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import state
from app.core.market_clock import ist_now, is_market_open
from app.services.active_stocks import SORTS, active_scanner
from app.services.alert_notifier import alert_notifier
from app.services.backfill import ensure_backfilled
from app.services.candle_store import candle_store
from app.services.market_data import DataSource, market_data
from app.services.scalp_monitor import (
    DEFAULT_MAX_SPREAD_PCT,
    DEFAULT_MIN_ATR_PCT,
    DEFAULT_MIN_VALUE_CR,
    ScalpRow,
    alert_text,
    score_row,
)

router = APIRouter(prefix="/api/scalp", tags=["scalp"])

BARS = 240                  # four hours of 1-minute candles is plenty for ATR / VWAP
BACKFILL_RETRY_SEC = 600    # one Groww history call per stock per 10 minutes at most
ALERT_EVERY_SEC = 60


class _Monitor:
    """Backfill throttle and the optional alert loop. Memory only: alerts are
    off again after a restart, which is the safe default for a new process."""

    def __init__(self) -> None:
        self.backfill_tried: dict[str, dt.datetime] = {}
        self.notes: dict[str, str] = {}
        self._backfill_task: asyncio.Task | None = None
        self.alerts_enabled = False
        self.alert_min_score = 60.0
        self.alert_cooldown_min = 30
        self.last_sent: dict[str, dt.datetime] = {}
        self.last_alert_run: str | None = None
        self.last_alert_error: str | None = None
        self._alert_task: asyncio.Task | None = None

    def rows(
        self,
        min_atr_pct: float = DEFAULT_MIN_ATR_PCT,
        max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT,
        min_value_cr: float = DEFAULT_MIN_VALUE_CR,
    ) -> list[ScalpRow]:
        source = market_data.source.value
        out = []
        for symbol in list(market_data.symbols):
            bars = candle_store.get(symbol, "1m", source, limit=BARS)
            out.append(
                score_row(
                    symbol,
                    bars,
                    state.latest_quotes.get(symbol),
                    min_atr_pct=min_atr_pct,
                    max_spread_pct=max_spread_pct,
                    min_value_cr=min_value_cr,
                )
            )
        out.sort(key=lambda r: (r.ready, r.score), reverse=True)
        return out

    def backfill_soon(self) -> None:
        """Fill thin 1-minute history from Groww in the background (LIVE only).

        Never awaited by a request: a page refresh must not wait on dozens of
        broker calls.
        """
        if market_data.source is not DataSource.LIVE:
            return
        if self._backfill_task is not None and not self._backfill_task.done():
            return
        now = ist_now()
        due = [
            s
            for s in list(market_data.symbols)
            if (now - self.backfill_tried.get(s, now - dt.timedelta(days=1))).total_seconds() >= BACKFILL_RETRY_SEC
        ]
        if not due:
            return

        async def _run() -> None:
            for symbol in due:
                self.backfill_tried[symbol] = ist_now()
                note = await ensure_backfilled(symbol, "1m")
                if note:
                    self.notes[symbol] = note
                else:
                    self.notes.pop(symbol, None)
                await asyncio.sleep(0.2)  # gentle on Groww's rate limits

        self._backfill_task = asyncio.create_task(_run())

    # ---- alerts ---------------------------------------------------------

    def due_alerts(self, rows: list[ScalpRow]) -> list[ScalpRow]:
        now = ist_now()
        out = []
        for row in rows:
            if not row.ready or row.score < self.alert_min_score:
                continue
            last = self.last_sent.get(row.symbol)
            if last is not None and (now - last).total_seconds() < self.alert_cooldown_min * 60:
                continue
            out.append(row)
        return out

    async def alert_once(self) -> list[str]:
        self.last_alert_run = ist_now().isoformat()
        # Simulated prices are invented, and a closed market is frozen: neither
        # is worth a phone buzz.
        if market_data.source is not DataSource.LIVE or not is_market_open():
            return []
        sent = []
        for row in self.due_alerts(self.rows()):
            result = await alert_notifier.send(alert_text(row))
            if result.ok:
                self.last_sent[row.symbol] = ist_now()
                sent.append(row.symbol)
                self.last_alert_error = None
            else:
                self.last_alert_error = result.error or result.skipped_reason or "not delivered"
                # Still back off, so a broken channel is not retried every minute.
                self.last_sent[row.symbol] = ist_now()
        return sent

    def set_alerts(self, enabled: bool, min_score: float, cooldown_min: int) -> None:
        self.alert_min_score = min_score
        self.alert_cooldown_min = cooldown_min
        self.alerts_enabled = enabled
        if enabled and (self._alert_task is None or self._alert_task.done()):
            self._alert_task = asyncio.create_task(self._alert_loop())
        if not enabled and self._alert_task is not None:
            self._alert_task.cancel()
            self._alert_task = None

    async def _alert_loop(self) -> None:
        while self.alerts_enabled:
            try:
                await self.alert_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_alert_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(ALERT_EVERY_SEC)

    def alert_status(self) -> dict:
        return {
            "enabled": self.alerts_enabled,
            "min_score": self.alert_min_score,
            "cooldown_minutes": self.alert_cooldown_min,
            "last_run": self.last_alert_run,
            "last_error": self.last_alert_error,
            "sent_today": sorted(
                s for s, t in self.last_sent.items() if t.date() == ist_now().date()
            ),
        }


monitor = _Monitor()


@router.get("/monitor")
async def scalp_monitor(
    min_atr_pct: float = Query(DEFAULT_MIN_ATR_PCT, ge=0, le=5),
    max_spread_pct: float = Query(DEFAULT_MAX_SPREAD_PCT, ge=0, le=5),
    min_value_cr: float = Query(DEFAULT_MIN_VALUE_CR, ge=0, le=100000),
    only_ready: bool = Query(False),
    top: int = Query(50, ge=1, le=500),
):
    monitor.backfill_soon()
    rows = monitor.rows(min_atr_pct, max_spread_pct, min_value_cr)
    shown = [r for r in rows if r.ready] if only_ready else rows
    health = market_data.health()
    return {
        "as_of": ist_now().isoformat(),
        "source": market_data.source.value,
        "market_open": health.market_open,
        "universe": len(rows),
        "ready": sum(1 for r in rows if r.ready),
        "settings": {
            "min_atr_pct": min_atr_pct,
            "max_spread_pct": max_spread_pct,
            "min_value_cr": min_value_cr,
        },
        "rows": [r.as_dict() for r in shown[:top]],
        "notes": sorted(set(monitor.notes.values()))[:4],
        "alerts": monitor.alert_status(),
    }


class AlertSettings(BaseModel):
    enabled: bool
    min_score: float = Field(60.0, ge=0, le=100)
    cooldown_minutes: int = Field(30, ge=1, le=600)


@router.post("/alerts")
async def scalp_alerts(body: AlertSettings):
    """Turn the Telegram scalp watch on or off. Watch only, never orders."""
    monitor.set_alerts(body.enabled, body.min_score, body.cooldown_minutes)
    return monitor.alert_status()


@router.get("/alerts/preview")
async def scalp_alerts_preview():
    """The messages the next alert pass would send, without sending them."""
    rows = monitor.due_alerts(monitor.rows())
    return {"messages": [{"symbol": r.symbol, "message": alert_text(r)} for r in rows]}


# ---- most active stocks on NSE ----------------------------------------------


def _groww_session():
    from app.services.groww_funds import groww_client

    return groww_client()


@router.get("/active")
async def most_active(
    sort: str = Query("value"),
    top: int = Query(40, ge=1, le=250),
    bias: str | None = Query(None),
    min_value_cr: float = Query(0.0, ge=0, le=100000),
):
    """The F&O stocks ranked by where traders are: ₹ traded, volume vs usual,
    buy/sell quantity. From the last scan; market data only."""
    if sort not in SORTS:
        raise HTTPException(400, f"sort must be one of {', '.join(SORTS)}")
    out = active_scanner.snapshot(sort=sort, top=top, bias=(bias or "").upper() or None, min_value_cr=min_value_cr)
    out["connected"] = _groww_session() is not None
    out["market_open"] = is_market_open()
    return out


@router.get("/universe")
async def fno_universe():
    """The F&O stock names, for scans on the SMA terminal (it downloads the candles itself).

    Same list as the most-active scan. Empty, with the reason, when the list could not be loaded."""
    symbols = active_scanner.universe_symbols()
    return {"symbols": symbols, "error": None if symbols else active_scanner.error}


@router.post("/active/scan")
async def most_active_scan():
    """Start a scan now (at most once a minute). Returns at once; the page polls."""
    client = _groww_session()
    if client is None:
        raise HTTPException(428, "Connect Groww in Settings (Connect Live Data) to scan NSE.")
    if not active_scanner.can_scan_now():
        raise HTTPException(429, "A scan ran less than a minute ago or is still running.")
    # Kept on the scanner so the task is not garbage-collected mid-scan.
    active_scanner.manual_task = asyncio.create_task(active_scanner.scan(client))
    return {"started": True}
