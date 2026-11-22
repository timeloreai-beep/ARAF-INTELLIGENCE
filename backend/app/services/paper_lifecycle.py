from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

MANUAL_EXIT_COOLDOWN = timedelta(hours=24)


def advance_paper_position(
    position: dict[str, Any],
    market_price: float,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    numeric = (
        market_price,
        float(position["entry_price"]),
        float(position["quantity"]),
        float(position["stop_loss"]),
        float(position["take_profit"]),
        float(position["trailing_distance"]),
        float(position["favorable_price"]),
    )
    if not all(math.isfinite(value) for value in numeric) or min(
        market_price, numeric[1], numeric[2], numeric[5], numeric[6]
    ) <= 0:
        raise ValueError("Paper position contains invalid market or risk values")
    if position.get("status") != "OPEN":
        return dict(position)

    updated = dict(position)
    side = str(position["side"]).upper()
    if side == "BUY":
        favorable = max(numeric[6], market_price)
        stop = max(numeric[3], favorable - numeric[5])
        stop_hit = market_price <= stop
        target_hit = market_price >= numeric[4]
        direction = 1
    elif side == "SELL":
        favorable = min(numeric[6], market_price)
        stop = min(numeric[3], favorable + numeric[5])
        stop_hit = market_price >= stop
        target_hit = market_price <= numeric[4]
        direction = -1
    else:
        raise ValueError("Paper position direction must be BUY or SELL")

    updated["favorable_price"] = favorable
    updated["stop_loss"] = stop
    updated["updated_at"] = now or datetime.now(timezone.utc)
    if stop_hit or target_hit:
        pnl = direction * (market_price - numeric[1]) * numeric[2]
        updated.update({
            "status": "CLOSED",
            "exit_price": market_price,
            "realized_pnl": pnl,
            "closed_at": now or datetime.now(timezone.utc),
        })
    return updated


def close_paper_position_manually(
    position: dict[str, Any],
    market_price: float,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    entry = float(position["entry_price"])
    quantity = float(position["quantity"])
    side = str(position["side"]).upper()
    if (
        position.get("status") != "OPEN"
        or side not in {"BUY", "SELL"}
        or not all(math.isfinite(value) for value in (market_price, entry, quantity))
        or min(market_price, entry, quantity) <= 0
    ):
        raise ValueError("Paper position cannot be manually closed with these values")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    pnl = (market_price - entry) * quantity * (1 if side == "BUY" else -1)
    updated = dict(position)
    updated.update({
        "status": "CLOSED",
        "exit_price": market_price,
        "realized_pnl": pnl,
        "closed_at": current,
        "updated_at": current,
        "exit_reason": "MANUAL_EXIT",
        "manual_exit_blocked_until": current + MANUAL_EXIT_COOLDOWN,
    })
    return updated
