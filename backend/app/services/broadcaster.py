"""Fan-out hub: one asyncio queue per connected WebSocket client. Any part of
the app (tick feed, risk manager, strategy engine, order log) publishes here
and every connected browser tab gets it.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any


class Broadcaster:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, channel: str, payload: Any) -> None:
        if is_dataclass(payload):
            payload = asdict(payload)

        # Console lines are the bot's own account of what it decided and why.
        # They were previously ephemeral — gone on refresh — which made every
        # decision unexplainable after the fact. Only the `log` channel is
        # persisted; `tick` fires several times a second and must never touch
        # the database.
        if channel == "log" and isinstance(payload, dict):
            await self._persist_log(payload)

        message = json.dumps({"channel": channel, "data": payload}, default=str)
        for q in list(self._subscribers):
            if q.full():
                continue
            await q.put(message)

    @staticmethod
    async def _persist_log(payload: dict) -> None:
        # Imported lazily: models.database imports settings at module load, and
        # a top-level import here would make the broadcaster unusable in the
        # scripts and tests that construct it before the DB exists.
        from app.models.database import StrategyLog, async_session

        try:
            async with async_session() as session:
                session.add(
                    StrategyLog(
                        level=str(payload.get("level", "INFO")),
                        message=str(payload.get("message", "")),
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            # Logging must never take down the tick loop that publishes it.
            pass


broadcaster = Broadcaster()
