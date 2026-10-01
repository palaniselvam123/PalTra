from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.desk_lock import desk_lock_enabled, session_ok
from app.services.broadcaster import broadcaster

router = APIRouter(tags=["ws"])


@router.websocket("/ws/live")
async def live_feed(websocket: WebSocket):
    if desk_lock_enabled() and not session_ok(websocket):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    queue = broadcaster.subscribe()
    try:
        while True:
            message = await queue.get()
            await websocket.send_text(message)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        broadcaster.unsubscribe(queue)
