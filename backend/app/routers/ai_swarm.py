from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import current_subject
from ..services.ai_swarm import get_ai_swarm_service

router = APIRouter(prefix="/ai_swarm", tags=["ai_swarm"])


@router.get("/status")
async def get_swarm_status(
    request: Request,
    subject: str = Depends(current_subject),
    order_id: str | None = None,
) -> dict:
    swarm = await get_ai_swarm_service(request.app)

    if order_id:
        status = await swarm.get_swarm_status(order_id)
        if not status:
            raise HTTPException(status_code=404, detail="Swarm not found for order")
        return {"success": True, "swarm": status}

    swarms = await swarm.list_active_swarms()
    return {"success": True, "swarms": swarms, "count": len(swarms)}


@router.post("/activate")
async def activate_swarm_for_order(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    body = await request.json()
    order_id = body.get("order_id")
    symbol = body.get("symbol")
    side = body.get("side")
    entry_price = body.get("entry_price")
    stop_loss = body.get("stop_loss")
    take_profit = body.get("take_profit")
    trailing_sl = body.get("trailing_sl")
    quantity = body.get("quantity")
    exchange = body.get("exchange")
    broker_token = body.get("broker_token")
    is_auto_trade = body.get("is_auto_trade", False)

    if not all([order_id, symbol, side, entry_price, stop_loss, take_profit, trailing_sl, quantity, exchange]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    swarm = await get_ai_swarm_service(request.app)
    state = await swarm.start_swarm_for_order(
        order_id=order_id,
        symbol=symbol,
        side=side,
        entry_price=float(entry_price),
        stop_loss=float(stop_loss),
        take_profit=float(take_profit),
        trailing_sl=float(trailing_sl),
        quantity=float(quantity),
        exchange=exchange,
        broker_token=broker_token or "",
        is_auto_trade=is_auto_trade,
    )

    return {
        "success": True,
        "message": f"100-Agent AI Swarm activated for {side} {symbol}",
        "agents_active": state.agents_active,
        "order_id": state.order_id,
    }


@router.post("/terminate")
async def terminate_swarm(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    body = await request.json()
    order_id = body.get("order_id")
    reason = body.get("reason", "MANUAL")

    if not order_id:
        raise HTTPException(status_code=400, detail="order_id required")

    swarm = await get_ai_swarm_service(request.app)
    terminated = await swarm.terminate_swarm(order_id, reason)

    if not terminated:
        raise HTTPException(status_code=404, detail="Swarm not found")

    return {"success": True, "message": f"AI Swarm terminated for order {order_id}", "reason": reason}


@router.post("/update")
async def update_swarm(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    body = await request.json()
    order_id = body.get("order_id")
    trailing_sl = body.get("trailing_sl")
    market_price = body.get("market_price")

    if not order_id:
        raise HTTPException(status_code=400, detail="order_id required")

    swarm = await get_ai_swarm_service(request.app)
    updated = await swarm.update_swarm_state(order_id, trailing_sl, market_price)

    if not updated:
        raise HTTPException(status_code=404, detail="Swarm not found")

    return {"success": True, "message": "Swarm state updated"}