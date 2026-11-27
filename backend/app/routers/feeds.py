import asyncio
import logging
from datetime import datetime, timezone
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse

from ..services.price_feeds import get_price_feed_manager, PriceTick

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("/health")
async def feeds_health() -> JSONResponse:
    """Health check for all feed workers."""
    manager = get_price_feed_manager()
    stats = manager.get_worker_stats()
    bus = manager.get_bus()

    all_healthy = all(s.get("connected", False) for s in stats)
    return JSONResponse({
        "status": "healthy" if all_healthy else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workers": stats,
        "bus": {
            "symbols_tracked": len(bus.get_all_latest()),
            "total_ticks": bus.get_stats().get("total_ticks", 0),
        },
    })


@router.get("/latest")
async def latest_prices(symbols: str = Query("", description="Comma-separated symbols")) -> JSONResponse:
    """Get latest prices for symbols."""
    manager = get_price_feed_manager()
    bus = manager.get_bus()

    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        result = {s: bus.get_latest(s).to_dict() if bus.get_latest(s) else None for s in sym_list}
    else:
        result = {s: t.to_dict() for s, t in bus.get_all_latest().items()}

    return JSONResponse({
        "success": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prices": result,
    })


@router.websocket("/ws/prices")
async def prices_websocket(
    websocket: WebSocket,
    symbols: str = Query(..., description="Comma-separated symbols to subscribe"),
) -> None:
    """WebSocket endpoint for real-time price streaming."""
    await websocket.accept()
    manager = get_price_feed_manager()
    bus = manager.get_bus()

    sym_set = {s.strip().upper() for s in symbols.split(",") if s.strip()}
    if not sym_set:
        await websocket.close(code=4000, reason="No symbols provided")
        return

    queue = bus.subscribe(sym_set)
    logger.info("WS client connected: %s", symbols)

    # Send initial snapshot
    snapshot = {s: bus.get_latest(s).to_dict() if bus.get_latest(s) else None for s in sym_set}
    await websocket.send_json({"type": "snapshot", "timestamp": datetime.now(timezone.utc).isoformat(), "data": snapshot})

    try:
        while True:
            # Wait for next tick with timeout to allow heartbeat
            try:
                tick = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json({
                    "type": "tick",
                    "data": tick.to_dict(),
                })
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({"type": "heartbeat", "timestamp": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        logger.info("WS client disconnected: %s", symbols)
    except Exception as e:
        logger.warning("WS error: %s", e)
    finally:
        bus.unsubscribe(queue)