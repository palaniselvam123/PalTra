from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.broadcaster import broadcaster

router = APIRouter(tags=["ws"])


@router.websocket("/ws/live")
async def live_feed(websocket: WebSocket):
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
