"""Research data API.

Purely additive. These routes drive the research ingestion path and touch none
of the live pipeline's state — no feed switching, no candle_store writes, no
scanner or strategy calls. They exist because the logged-in broker session
lives in this process, and borrowing it is safer than minting a second session
that could invalidate the live one.

Ingestion runs as a background task: a multi-symbol backfill takes minutes, and
holding an HTTP request open for it would time out and give no way to watch
progress.
"""
from __future__ import annotations

import asyncio
import datetime as dt

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.research import ingestion, manifest
from app.research.store import store
from app.research.validator import format_report, validate_store

router = APIRouter(prefix="/api/research", tags=["research"])

# Progress for the currently running job, so a long backfill is observable.
_job: dict = {"running": False, "log": [], "report": None, "started": None}


def _client():
    from app.api.routes_auth import _active_clients

    client = _active_clients.get("groww")
    if client is None:
        raise HTTPException(400, "No Groww session — connect live data in Settings first.")
    return client


class BackfillRequest(BaseModel):
    symbols: list[str]
    interval: str = "5m"
    start_date: str
    end_date: str | None = None
    source: str = "live"


@router.get("/probe")
async def probe(symbol: str = Query("RELIANCE"), interval: str = Query("5m"),
                ladder: str = Query(""), widths: str = Query("")):
    """Measure the data source's real history limit, rather than assuming one."""
    steps = tuple(int(x) for x in ladder.split(",") if x.strip()) or None
    w = tuple(int(x) for x in widths.split(",") if x.strip()) or None
    return await ingestion.probe_max_history(_client(), symbol, interval, steps, w)


@router.get("/status")
async def status(interval: str = Query("5m"), source: str = Query("live")):
    return {
        "job": {k: v for k, v in _job.items() if k != "log"},
        "log_tail": _job["log"][-20:],
        "store": store.stats(interval, source),
        "symbols": store.symbols(interval, source),
    }


@router.post("/backfill")
async def backfill(req: BackfillRequest):
    if _job["running"]:
        raise HTTPException(409, "A research ingestion job is already running.")
    client = _client()
    start = dt.date.fromisoformat(req.start_date)
    end = dt.date.fromisoformat(req.end_date) if req.end_date else dt.date.today()

    async def run():
        _job.update(running=True, log=[], report=None, started=dt.datetime.now().isoformat())
        try:
            rep = await ingestion.backfill(
                req.symbols, req.interval, start, end, client, req.source,
                progress=lambda m: _job["log"].append(m),
            )
            _job["report"] = rep.as_dict()
        except Exception as exc:  # noqa: BLE001
            _job["log"].append(f"FAILED: {exc}")
        finally:
            _job["running"] = False

    asyncio.create_task(run())
    return {"started": True, "symbols": len(req.symbols), "range": [start.isoformat(), end.isoformat()]}


@router.post("/update")
async def update(req: BackfillRequest):
    if _job["running"]:
        raise HTTPException(409, "A research ingestion job is already running.")
    client = _client()

    async def run():
        _job.update(running=True, log=[], report=None, started=dt.datetime.now().isoformat())
        try:
            rep = await ingestion.update(
                req.symbols, req.interval, client, req.source,
                progress=lambda m: _job["log"].append(m),
            )
            _job["report"] = rep.as_dict()
        except Exception as exc:  # noqa: BLE001
            _job["log"].append(f"FAILED: {exc}")
        finally:
            _job["running"] = False

    asyncio.create_task(run())
    return {"started": True}


@router.get("/validate")
async def validate(interval: str = Query("5m"), source: str = Query("live")):
    res = validate_store(store, interval, source)
    return {"report_text": format_report(res), **res.as_dict()}


@router.post("/manifest")
async def create_manifest(dataset_id: str = Query(...), interval: str = Query("5m"),
                          source: str = Query("live")):
    return manifest.create(dataset_id, interval, source)


@router.get("/live-provenance")
async def live_provenance(interval: str = Query("5m")):
    """Live-store bars with their volume provenance and quality.

    Exists so the two ingestion paths can be compared honestly. A backfilled
    live bar is the same API response the research store ingested, so comparing
    those would compare one source against itself; only TICK-provenance bars
    are an independent comparator.
    """
    from app.services.candle_store import candle_store
    from app.services.market_data import market_data

    source = market_data.source.value
    out: dict[str, dict] = {}
    for (symbol, iv, src) in list(candle_store._bars):  # noqa: SLF001
        if iv != interval or src != source:
            continue
        bars = candle_store.get(symbol, iv, src, limit=1500)
        out[symbol] = {
            "provenance": {str(ts): p for ts, p in candle_store.provenance(symbol, iv, src).items()},
            "quality": {str(ts): q for ts, q in candle_store.volume_quality(symbol, iv, src).items()},
            "volume": {str(b.ts): b.volume for b in bars},
            "raw_cumulative": {str(ts): v for ts, v in candle_store.raw_cumulative(symbol, iv, src).items()},
        }
    return {"source": source, "interval": interval, "symbols": out}


@router.get("/manifests")
async def list_manifests():
    return {"manifests": store.list_manifests()}
