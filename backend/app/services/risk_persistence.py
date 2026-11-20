from __future__ import annotations

import logging
from typing import Any

from redis.exceptions import RedisError

from .risk import RiskGuardian, UserRiskState

logger = logging.getLogger(__name__)


def is_persistence_degraded(report: dict[str, Any]) -> bool:
    for value in report.values():
        if isinstance(value, dict):
            if value.get("health") not in {None, "healthy"}:
                return True
            if is_persistence_degraded(value):
                return True
        elif isinstance(value, str) and value in {
            "error",
            "not_configured",
            "partial",
            "degraded",
            "offline",
        }:
            return True
    return False


async def load_risk_state(request, subject: str, guardian: RiskGuardian) -> UserRiskState:
    redis = request.app.state.redis
    database = request.app.state.database
    cache_key = f"risk:{subject}:{guardian.current_trading_day()}"

    if redis.enabled:
        try:
            cached = await redis.get_json(cache_key)
            if isinstance(cached, dict):
                return guardian.restore_persisted(subject, cached)
        except (RedisError, ValueError):
            logger.exception("Could not load cached risk state")

    record: dict[str, Any] | None = None
    if database.enabled:
        config = await database.get_risk_config(subject)
        state = await database.get_risk_state(subject, guardian.current_trading_day())
        if config or state:
            record = {
                "capital_inr": float(config.capital_inr) if config else 100000,
                "max_daily_loss_pct": float(config.max_daily_loss_pct) if config else 2.5,
                "max_per_trade_risk_pct": float(config.max_per_trade_risk_pct) if config else 2.0,
                "max_trades_per_day": int(config.max_trades_per_day) if config else 5,
                "daily_pnl_inr": float(state.daily_pnl_inr) if state else 0,
                "trades_count": int(state.trades_count) if state else 0,
                "trading_day": state.trading_day if state else guardian.current_trading_day(),
                "is_hard_locked": bool(state and state.is_hard_locked),
                "locked_reason": (state.locked_reason if state else None) or "",
            }
    if record is None:
        return guardian.get_state(subject)

    risk_state = guardian.restore_persisted(subject, record)
    if redis.enabled:
        try:
            await redis.set_json(cache_key, guardian.to_record(risk_state), ttl_seconds=86400)
        except (RedisError, ValueError):
            logger.exception("Could not cache restored risk state")
    return risk_state


async def persist_risk_state(request, subject: str, state: UserRiskState) -> dict[str, Any]:
    redis = request.app.state.redis
    database = request.app.state.database
    values = RiskGuardian.to_record(state)
    report: dict[str, Any] = {}

    if database.enabled:
        config_status = await database.upsert_risk_config(
            user_id=subject,
            capital_inr=state.capital_inr,
            max_daily_loss_pct=state.max_daily_loss_pct,
            max_per_trade_risk_pct=state.max_per_trade_risk_pct,
            max_trades_per_day=state.max_trades_per_day,
            is_locked=state.is_hard_locked,
            lock_reason=state.locked_reason or None,
        )
        state_status = await database.upsert_risk_state(
            user_id=subject,
            trading_day=state.trading_day,
            daily_pnl_inr=state.daily_pnl_inr,
            trades_count=state.trades_count,
            is_hard_locked=state.is_hard_locked,
            locked_reason=state.locked_reason or None,
        )
        report["postgres"] = {
            "config": config_status.as_dict(),
            "state": state_status.as_dict(),
        }
    else:
        report["postgres"] = {"health": "not_configured"}

    if redis.enabled:
        try:
            await redis.set_json(
                f"risk:{subject}:{state.trading_day}",
                values,
                ttl_seconds=86400,
            )
            report["redis"] = "stored"
        except (RedisError, ValueError):
            logger.exception("Could not persist cached risk state")
            report["redis"] = "error"
    else:
        report["redis"] = "not_configured"
    return report
