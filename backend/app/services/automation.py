from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


OPPORTUNITY_TTL_SECONDS = 15


def calculate_trade_plan(
    *,
    side: str,
    entry: float,
    atr_value: float | None,
    capital_inr: float,
    daily_budget_inr: float,
    used_budget_inr: float = 0,
    max_risk_pct: float = 2,
    fx_to_inr: float = 1,
    lot_size: float = 1,
    strategy_stop: float | None = None,
    strategy_target: float | None = None,
) -> dict[str, float] | None:
    values = (entry, capital_inr, daily_budget_inr, used_budget_inr, max_risk_pct, fx_to_inr, lot_size)
    if not all(math.isfinite(value) for value in values):
        return None
    if entry <= 0 or capital_inr <= 0 or daily_budget_inr <= 0 or used_budget_inr < 0:
        return None
    if max_risk_pct <= 0 or fx_to_inr <= 0 or lot_size <= 0 or side not in {"BUY", "SELL"}:
        return None

    direction = 1 if side == "BUY" else -1
    stop = strategy_stop
    target = strategy_target
    if stop is None or target is None:
        if atr_value is None or not math.isfinite(atr_value) or atr_value <= 0:
            return None
        stop = entry - direction * 1.5 * atr_value
        target = entry + direction * 3 * atr_value
    if not math.isfinite(stop) or not math.isfinite(target):
        return None
    if direction * (entry - stop) <= 0 or direction * (target - entry) <= 0:
        return None
    risk_distance = abs(entry - stop)
    reward_distance = abs(target - entry)
    if reward_distance + 1e-12 < 2 * risk_distance:
        return None

    risk_per_unit_inr = risk_distance * fx_to_inr
    notional_per_unit_inr = entry * fx_to_inr
    risk_limit_inr = capital_inr * max_risk_pct / 100
    budget_remaining = daily_budget_inr - used_budget_inr
    if risk_per_unit_inr <= 0 or budget_remaining <= 0:
        return None
    quantity_limit = min(risk_limit_inr / risk_per_unit_inr, budget_remaining / notional_per_unit_inr)
    quantity = math.floor((quantity_limit + 1e-12) / lot_size) * lot_size
    if quantity <= 0:
        return None
    return {
        "entry": entry,
        "stop_loss": stop,
        "take_profit": target,
        "trailing_distance": (atr_value if atr_value and atr_value > 0 else abs(entry - stop) / 1.5),
        "quantity": quantity,
        "risk_inr": quantity * risk_per_unit_inr,
        "capital_used_inr": quantity * notional_per_unit_inr,
    }


def _bengali_reason(report: dict[str, Any]) -> str:
    reasons = [
        str(engine.get("explainBn", "")).strip()
        for engine in report.get("engines", [])
        if engine.get("required") and engine.get("status") == "PASS" and engine.get("explainBn")
    ][:5]
    summary = str(report.get("bengaliSummary") or "").strip()
    details = " ".join(reasons)
    return " ".join(part for part in (summary, details) if part)


class OpportunityHub:
    def __init__(self) -> None:
        self._opportunities: dict[str, list[dict[str, Any]]] = {}
        self._settings: dict[str, dict[str, Any]] = {}
        self._manual_exit_blocks: dict[
            str, dict[tuple[str, str, str], datetime]
        ] = {}
        self._lock = asyncio.Lock()

    async def get_settings(self, subject: str) -> dict[str, Any]:
        async with self._lock:
            return dict(self._settings.get(subject, {
                "enabled": False,
                "dailyBudgetInr": 10000,
                "allowedMarkets": "ALL",
            }))

    async def set_settings(self, subject: str, values: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_settings(subject)
        current.update(values)
        async with self._lock:
            self._settings[subject] = current
        return dict(current)

    async def publish(
        self,
        *,
        subject: str,
        report: dict[str, Any],
        trusted_feed: bool,
        scanner_gate_passed: bool,
        news_guard_active: bool,
        global_auto_trade_enabled: bool,
        global_kill_switch: bool,
        lifecycle_ready: bool = False,
        capital_inr: float,
        risk_pct: float,
        atr_value: float | None,
        fx_to_inr: float = 1,
        lot_size: float = 1,
        used_budget_inr: float = 0,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        score = report.get("fusionScore")
        side = report.get("side")
        engines = report.get("engines")
        required_engines = [
            engine for engine in engines or [] if engine.get("required") is True
        ]
        all_engines_pass = bool(required_engines) and all(
            engine.get("status") == "PASS" for engine in required_engines
        )
        if (
            not isinstance(score, int)
            or score < 70
            or side not in {"BUY", "SELL"}
            or not report.get("allRequiredPass")
            or not report.get("isLiveVerified")
            or not trusted_feed
            or not scanner_gate_passed
            or news_guard_active
            or not all_engines_pass
        ):
            return None

        entry = report.get("entry")
        settings = await self.get_settings(subject)
        market = "CRYPTO" if report.get("category") == "crypto" else "INDIA"
        if settings["allowedMarkets"] not in {"ALL", market}:
            return None
        plan = calculate_trade_plan(
            side=side,
            entry=float(entry or 0),
            atr_value=atr_value,
            capital_inr=capital_inr,
            daily_budget_inr=float(settings["dailyBudgetInr"]),
            used_budget_inr=used_budget_inr,
            max_risk_pct=risk_pct,
            fx_to_inr=fx_to_inr,
            lot_size=lot_size,
            strategy_stop=report.get("sl"),
            strategy_target=report.get("target"),
        )
        if not plan:
            return None

        current = now or datetime.now(timezone.utc)
        expires_at = current + timedelta(seconds=OPPORTUNITY_TTL_SECONDS)
        execution_eligible = (
            score >= 70
            and bool((report.get("apexSetup") or {}).get("allowed"))
            and opportunity_rr(plan) >= 2
        )
        is_auto_eligible = (
            execution_eligible
            and score >= 90
            and report.get("autoTradeEligible") is True
            and settings["enabled"]
            and global_auto_trade_enabled
            and not global_kill_switch
            and lifecycle_ready
        )
        opportunity = {
            "id": str(uuid.uuid4()),
            "subject": subject,
            "symbol": report.get("symbol"),
            "orderKey": report.get("orderKey"),
            "exchange": report.get("exchange"),
            "brokerToken": report.get("brokerToken"),
            "category": report.get("category"),
            "side": side,
            "direction": report.get("signalSide") or side,
            "optionType": report.get("optionType"),
            "underlying": report.get("underlying"),
            "strike": (report.get("optionContract") or {}).get("strike"),
            "expiry": (report.get("optionContract") or {}).get("expiry"),
            "fusionScore": score,
            "tierLabel": (
                "Standard Opportunity (70%+)"
                if score < 80
                else "High-Conviction Opportunity (80%+)"
                if score < 90
                else "Prime Apex Opportunity (90%+)"
            ),
            "entry": plan["entry"],
            "sl": plan["stop_loss"],
            "target": plan["take_profit"],
            "quantity": plan["quantity"],
            "riskInr": plan["risk_inr"],
            "capitalUsedInr": plan["capital_used_inr"],
            "fxToInr": fx_to_inr,
            "trailingDistance": plan["trailing_distance"],
            "rrRatio": round(
                abs(plan["take_profit"] - plan["entry"])
                / abs(plan["entry"] - plan["stop_loss"]),
                2,
            ),
            "bengaliReason": _bengali_reason(report),
            "createdAt": current.isoformat(),
            "expiresAt": expires_at.isoformat(),
            "autoTradeEligible": is_auto_eligible,
            "executionEligible": execution_eligible,
            "serverGatesPassed": True,
            "mode": "PAPER",
        }
        if not opportunity["bengaliReason"] or not opportunity["orderKey"]:
            return None
        async with self._lock:
            setup = (
                str(opportunity["orderKey"]),
                str(opportunity["exchange"]),
                str(opportunity["side"]),
            )
            blocked_until = self._manual_exit_blocks.setdefault(subject, {}).get(setup)
            if blocked_until and blocked_until > current:
                return None
            queue = self._opportunities.setdefault(subject, [])
            queue[:] = [
                item for item in queue
                if datetime.fromisoformat(item["expiresAt"]) > current
                and item["orderKey"] != opportunity["orderKey"]
            ]
            queue.append(opportunity)
        return dict(opportunity)

    async def block_manual_exit_setup(
        self,
        subject: str,
        *,
        order_key: str,
        exchange: str,
        side: str,
        blocked_until: datetime,
    ) -> None:
        setup = (str(order_key), str(exchange), str(side))
        async with self._lock:
            blocks = self._manual_exit_blocks.setdefault(subject, {})
            now = datetime.now(timezone.utc)
            for key, expiry in list(blocks.items()):
                if expiry <= now:
                    blocks.pop(key, None)
            blocks[setup] = blocked_until
            queue = self._opportunities.setdefault(subject, [])
            queue[:] = [
                item for item in queue
                if (
                    str(item.get("orderKey")),
                    str(item.get("exchange")),
                    str(item.get("side")),
                ) != setup
            ]

    async def is_manual_exit_setup_blocked(
        self,
        subject: str,
        *,
        order_key: str,
        exchange: str,
        side: str,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        setup = (str(order_key), str(exchange), str(side))
        async with self._lock:
            expiry = self._manual_exit_blocks.get(subject, {}).get(setup)
            return bool(expiry and expiry > current)

    async def list_active(self, subject: str, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or datetime.now(timezone.utc)
        async with self._lock:
            queue = self._opportunities.setdefault(subject, [])
            queue[:] = [
                item for item in queue
                if datetime.fromisoformat(item["expiresAt"]) > current
            ]
            return [dict(item) for item in queue]

    async def claim(
        self,
        subject: str,
        opportunity_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        current = now or datetime.now(timezone.utc)
        async with self._lock:
            queue = self._opportunities.setdefault(subject, [])
            for index, item in enumerate(queue):
                if item["id"] != opportunity_id:
                    continue
                queue.pop(index)
                if datetime.fromisoformat(item["expiresAt"]) <= current:
                    return None
                return dict(item)
        return None

    async def restore_manual_alert(
        self,
        subject: str,
        opportunity: dict[str, Any],
        *,
        auto_trade_state: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        current = now or datetime.now(timezone.utc)
        if datetime.fromisoformat(opportunity["expiresAt"]) <= current:
            return None
        alert = {
            **opportunity,
            "id": str(uuid.uuid4()),
            "autoTradeEligible": False,
            "executionEligible": bool(opportunity.get("executionEligible")),
            "autoTradeState": auto_trade_state,
        }
        async with self._lock:
            queue = self._opportunities.setdefault(subject, [])
            queue.append(alert)
        return dict(alert)


def trading_day_ist(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    india_timezone = timezone(timedelta(hours=5, minutes=30))
    return current.astimezone(india_timezone).date().isoformat()


def opportunity_rr(plan: dict[str, float]) -> float:
    risk = abs(plan["entry"] - plan["stop_loss"])
    reward = abs(plan["take_profit"] - plan["entry"])
    return reward / risk if risk > 0 else 0
