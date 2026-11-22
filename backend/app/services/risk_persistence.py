from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.exceptions import RedisError

from ..config import DEFAULT_MAX_DAILY_TRADES
from .risk import RiskGuardian, UserRiskState

logger = logging.getLogger(__name__)
RISK_CACHE_TTL_SECONDS = 2 * 24 * 60 * 60


def _expiry_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


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


async def _load_cached_risk_record(
    redis, subject: str, trading_day: str
) -> tuple[dict[str, Any] | None, bool]:
    today = datetime.fromisoformat(trading_day).date()
    keys = (
        f"risk:{subject}:state",
        f"risk:{subject}:{trading_day}",
        f"risk:{subject}:{(today - timedelta(days=1)).isoformat()}",
    )
    records: list[dict[str, Any]] = []
    read_failed = False
    for key in keys:
        try:
            cached = await redis.get_json(key)
            if isinstance(cached, dict):
                records.append(cached)
        except (RedisError, ValueError):
            logger.exception("Could not load cached risk record")
            read_failed = True
    if not records:
        return None, read_failed

    current = next(
        (record for record in records if record.get("trading_day") == trading_day),
        records[0],
    )
    result = dict(current)
    now = datetime.now(timezone.utc)
    for record in records:
        if record is current:
            continue
        same_day = record.get("trading_day") == result.get("trading_day")
        expiry = _expiry_datetime(record.get("risk_lock_expires_at"))
        active_loss_lock = bool(
            record.get("is_hard_locked")
            and record.get("locked_reason") == "DAILY_LOSS_LIMIT_BREACHED"
            and expiry
            and expiry > now
        )
        if not same_day and not active_loss_lock:
            continue
        for key in (
            "capital_inr",
            "max_daily_loss_amount",
            "max_per_trade_risk_pct",
            "max_trades_per_day",
        ):
            if key in record and key in result:
                result[key] = min(float(result[key]), float(record[key]))
        result["daily_pnl_inr"] = min(
            float(result.get("daily_pnl_inr", 0)),
            float(record.get("daily_pnl_inr", 0)),
        )
        result["trades_count"] = max(
            int(result.get("trades_count", 0)),
            int(record.get("trades_count", 0)),
        )
        if same_day:
            result["is_hard_locked"] = bool(
                result.get("is_hard_locked") or record.get("is_hard_locked")
            )
        if active_loss_lock:
            result["is_hard_locked"] = True
            result["locked_reason"] = "DAILY_LOSS_LIMIT_BREACHED"
            result["daily_loss_lock_started_at"] = (
                record.get("daily_loss_lock_started_at")
                or result.get("daily_loss_lock_started_at")
            )
        current_expiry = _expiry_datetime(result.get("risk_lock_expires_at"))
        if expiry and (not current_expiry or expiry > current_expiry):
            result["risk_lock_expires_at"] = expiry.isoformat()
    return result, read_failed


def _merge_cached_into_database_record(
    record: dict[str, Any],
    cached_record: dict[str, Any],
    *,
    now: datetime,
) -> None:
    same_day = cached_record.get("trading_day") == record.get("trading_day")
    cached_expiry = _expiry_datetime(cached_record.get("risk_lock_expires_at"))
    active_loss_lock = bool(
        cached_record.get("is_hard_locked")
        and cached_record.get("locked_reason") == "DAILY_LOSS_LIMIT_BREACHED"
        and cached_expiry
        and cached_expiry > now
    )
    if not same_day and not active_loss_lock:
        return
    for key in (
        "capital_inr",
        "max_daily_loss_amount",
        "max_per_trade_risk_pct",
        "max_trades_per_day",
    ):
        record[key] = min(record[key], cached_record.get(key, record[key]))
    record["daily_pnl_inr"] = min(
        record["daily_pnl_inr"],
        float(cached_record.get("daily_pnl_inr", record["daily_pnl_inr"])),
    )
    record["trades_count"] = max(
        record["trades_count"],
        int(cached_record.get("trades_count", record["trades_count"])),
    )
    if same_day:
        record["is_hard_locked"] = bool(
            record["is_hard_locked"] or cached_record.get("is_hard_locked")
        )
    if active_loss_lock:
        record["is_hard_locked"] = True
        record["locked_reason"] = "DAILY_LOSS_LIMIT_BREACHED"
        record["daily_loss_lock_started_at"] = (
            cached_record.get("daily_loss_lock_started_at")
            or record.get("daily_loss_lock_started_at")
        )
    stored_expiry = _expiry_datetime(record.get("risk_lock_expires_at"))
    if cached_expiry and (not stored_expiry or cached_expiry > stored_expiry):
        record["risk_lock_expires_at"] = cached_expiry.isoformat()


async def _cache_risk_state(redis, subject: str, state: UserRiskState) -> None:
    values = RiskGuardian.to_record(state)
    await redis.set_json(
        f"risk:{subject}:state",
        values,
        ttl_seconds=RISK_CACHE_TTL_SECONDS,
    )
    await redis.set_json(
        f"risk:{subject}:{state.trading_day}",
        values,
        ttl_seconds=RISK_CACHE_TTL_SECONDS,
    )


async def load_risk_state(request, subject: str, guardian: RiskGuardian) -> UserRiskState:
    redis = request.app.state.redis
    database = request.app.state.database
    trading_day = guardian.current_trading_day()
    cached_record: dict[str, Any] | None = None
    redis_read_failed = False

    if redis.enabled:
        cached_record, redis_read_failed = await _load_cached_risk_record(
            redis, subject, trading_day
        )

    record: dict[str, Any] | None = None
    if database.enabled:
        config = await database.get_risk_config(subject)
        state = await database.get_risk_state(subject, trading_day)
        if config or state:
            config_lock_expiry = (
                _expiry_datetime(config.risk_lock_expires_at)
                if config and config.risk_lock_expires_at
                else None
            )
            config_watermark = (
                float(config.risk_lock_watermark_inr)
                if config
                and config_lock_expiry
                and config_lock_expiry > datetime.now(timezone.utc)
                else 0
            )
            record = {
                "capital_inr": float(config.capital_inr) if config else 100000,
                "max_daily_loss_amount": (
                    float(config.max_daily_loss_amount) if config else 2500
                ),
                "max_per_trade_risk_pct": float(config.max_per_trade_risk_pct) if config else 2.0,
                "max_trades_per_day": (
                    int(config.max_trades_per_day)
                    if config
                    else min(DEFAULT_MAX_DAILY_TRADES, guardian.max_daily_trades)
                ),
                "daily_pnl_inr": min(
                    float(state.daily_pnl_inr) if state else 0,
                    config_watermark,
                ),
                "trades_count": int(state.trades_count) if state else 0,
                "trading_day": state.trading_day if state else trading_day,
                "is_hard_locked": bool(
                    (state and state.is_hard_locked)
                    or (
                        config
                        and config.is_locked
                        and config.lock_reason == "DAILY_LOSS_LIMIT_BREACHED"
                        and config_lock_expiry
                        and config_lock_expiry > datetime.now(timezone.utc)
                    )
                ),
                "locked_reason": (
                    (state.locked_reason if state else None)
                    or (config.lock_reason if config else None)
                    or ""
                ),
                "risk_lock_expires_at": (
                    config_lock_expiry.isoformat()
                    if config_lock_expiry
                    else None
                ),
                "daily_loss_lock_started_at": (
                    _expiry_datetime(config.daily_loss_lock_started_at).isoformat()
                    if config and config.daily_loss_lock_started_at
                    else None
                ),
            }
            if cached_record:
                _merge_cached_into_database_record(
                    record, cached_record, now=datetime.now(timezone.utc)
                )
    if redis_read_failed and not database.enabled:
        raise RuntimeError("Risk state cache is unavailable")
    if record is None:
        if cached_record is not None:
            risk_state = guardian.restore_persisted(subject, cached_record)
            if redis.enabled:
                try:
                    await _cache_risk_state(redis, subject, risk_state)
                except (RedisError, ValueError):
                    logger.exception("Could not cache restored risk state")
            return risk_state
        if redis_read_failed:
            raise RuntimeError("Risk state cache is unavailable")
        return guardian.get_state(subject)

    risk_state = guardian.restore_persisted(subject, record)
    if redis.enabled:
        try:
            await _cache_risk_state(redis, subject, risk_state)
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
            max_daily_loss_amount=state.max_daily_loss_amount,
            max_per_trade_risk_pct=state.max_per_trade_risk_pct,
            max_trades_per_day=state.max_trades_per_day,
            is_locked=state.is_hard_locked,
            lock_reason=state.locked_reason or None,
            risk_lock_expires_at=state.risk_lock_expires_at,
            risk_lock_watermark_inr=state.daily_pnl_inr,
            daily_loss_lock_started_at=state.daily_loss_lock_started_at,
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
                f"risk:{subject}:state",
                values,
                ttl_seconds=RISK_CACHE_TTL_SECONDS,
            )
            await redis.set_json(
                f"risk:{subject}:{state.trading_day}",
                values,
                ttl_seconds=RISK_CACHE_TTL_SECONDS,
            )
            report["redis"] = "stored"
        except (RedisError, ValueError):
            logger.exception("Could not persist cached risk state")
            report["redis"] = "error"
    else:
        report["redis"] = "not_configured"
    return report
