from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from threading import RLock

from ..config import APEX_MAX_DAILY_SETUPS, DEFAULT_MAX_DAILY_TRADES, get_settings
from .cost_calculator import CostCalculator, VetoResult, BrokerType, SegmentType

INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


def _parse_risk_expiry(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


@dataclass
class UserRiskState:
    capital_inr: float = 100000
    max_daily_loss_amount: float = 2500
    max_weekly_loss_amount: float = 10000
    max_per_trade_risk_pct: float = 2.0
    max_trades_per_day: int = DEFAULT_MAX_DAILY_TRADES
    daily_pnl_inr: float = 0
    weekly_pnl_inr: float = 0
    trades_count: int = 0
    weekly_trades_count: int = 0
    trading_day: str = ""
    trading_week: str = ""
    is_hard_locked: bool = False
    locked_reason: str = ""
    risk_lock_expires_at: datetime | None = None
    daily_loss_lock_started_at: datetime | None = None
    weekly_loss_lock_started_at: datetime | None = None
    last_order_signature: str | None = None
    last_order_time: datetime | None = None

    @property
    def daily_loss_limit_inr(self) -> float:
        return -abs(self.max_daily_loss_amount)

    @property
    def weekly_loss_limit_inr(self) -> float:
        return -abs(self.max_weekly_loss_amount)


class RiskGuardian:
    def __init__(
        self,
        *,
        max_daily_trades: int | None = None,
        max_per_trade_risk_pct: float | None = None,
        max_daily_loss_amount: float | None = None,
        max_weekly_loss_amount: float | None = None,
    ) -> None:
        settings = get_settings()
        self.max_daily_trades = min(
            APEX_MAX_DAILY_SETUPS,
            settings.max_daily_trades if max_daily_trades is None else max_daily_trades,
        )
        self.max_per_trade_risk_pct = max_per_trade_risk_pct or settings.max_per_trade_risk_pct
        self.max_daily_loss_amount = (
            max_daily_loss_amount or settings.max_daily_loss_amount
        )
        self.max_weekly_loss_amount = (
            max_weekly_loss_amount or getattr(settings, 'max_weekly_loss_amount', 10000)
        )
        self._states: dict[str, UserRiskState] = {}
        self._user_access: dict[str, bool] = {}
        self._lock = RLock()

    @staticmethod
    def _today() -> str:
        return datetime.now(INDIA_TIMEZONE).date().isoformat()

    @staticmethod
    def _current_week() -> str:
        now = datetime.now(INDIA_TIMEZONE)
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"

    @classmethod
    def current_trading_day(cls) -> str:
        return cls._today()

    def _state_for(self, subject: str) -> UserRiskState:
        today = self._today()
        current_week = self._current_week()
        state = self._states.setdefault(
            subject,
            UserRiskState(
                trading_day=today,
                trading_week=current_week,
                max_daily_loss_amount=min(2500, self.max_daily_loss_amount),
                max_weekly_loss_amount=min(10000, getattr(self, 'max_weekly_loss_amount', 10000)),
                max_per_trade_risk_pct=self.max_per_trade_risk_pct,
                max_trades_per_day=min(DEFAULT_MAX_DAILY_TRADES, self.max_daily_trades),
            ),
        )
        now = datetime.now(timezone.utc)
        lock_active = bool(
            state.risk_lock_expires_at and state.risk_lock_expires_at > now
        )
        if state.risk_lock_expires_at and not lock_active:
            state.risk_lock_expires_at = None
            state.daily_pnl_inr = 0
            state.weekly_pnl_inr = 0
            state.trades_count = 0
            state.weekly_trades_count = 0
            state.is_hard_locked = False
            state.locked_reason = ""
            state.daily_loss_lock_started_at = None
            state.weekly_loss_lock_started_at = None
        if state.trading_day != today:
            state.trading_day = today
            if not (lock_active and state.locked_reason == "DAILY_LOSS_LIMIT_BREACHED"):
                state.daily_pnl_inr = 0
                state.trades_count = 0
                state.is_hard_locked = False
                state.locked_reason = ""
        if state.trading_week != current_week:
            state.trading_week = current_week
            if not (lock_active and state.locked_reason == "WEEKLY_LOSS_LIMIT_BREACHED"):
                state.weekly_pnl_inr = 0
                state.weekly_trades_count = 0
                state.is_hard_locked = False
                state.locked_reason = ""
        state.max_daily_loss_amount = min(
            state.max_daily_loss_amount, self.max_daily_loss_amount
        )
        state.max_weekly_loss_amount = min(
            state.max_weekly_loss_amount, getattr(self, 'max_weekly_loss_amount', 10000)
        )
        state.max_per_trade_risk_pct = min(
            state.max_per_trade_risk_pct, self.max_per_trade_risk_pct
        )
        state.max_trades_per_day = min(state.max_trades_per_day, self.max_daily_trades)
        return state

    def configure(
        self,
        subject: str,
        capital_inr: float,
        max_daily_loss_amount: float,
        max_trades_per_day: int,
        max_per_trade_risk_pct: float,
        max_weekly_loss_amount: float | None = None,
    ) -> UserRiskState:
        with self._lock:
            state = self._state_for(subject)
            if (
                state.risk_lock_expires_at
                and state.risk_lock_expires_at > datetime.now(timezone.utc)
            ):
                raise PermissionError("Risk settings are locked for 24 hours after saving")
            if state.trades_count > 0 or state.is_hard_locked:
                raise PermissionError("Risk settings are locked after the first trade or hard lock")
            state.capital_inr = max(5000, min(capital_inr, 100000000))
            state.max_daily_loss_amount = max(
                1, min(max_daily_loss_amount, self.max_daily_loss_amount)
            )
            state.max_weekly_loss_amount = max(
                1, min(max_weekly_loss_amount or self.max_weekly_loss_amount, self.max_weekly_loss_amount)
            )
            state.max_trades_per_day = max(
                1, min(max_trades_per_day, self.max_daily_trades)
            )
            state.max_per_trade_risk_pct = max(
                0.1, min(max_per_trade_risk_pct, self.max_per_trade_risk_pct)
            )
            state.risk_lock_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            state.daily_loss_lock_started_at = None
            state.weekly_loss_lock_started_at = None
            return state

    def precheck_entry(
        self,
        *,
        subject: str,
        entry_price: float,
        stop_loss: float,
        quantity: float,
        is_auto_trade: bool,
        side: str | None = None,
        broker: str | None = None,
        symbol: str | None = None,
        exchange: str | None = None,
        bid: float | None = None,
        ask: float | None = None,
        last_price: float | None = None,
        volume_24h: float | None = None,
        avg_volume_24h: float | None = None,
        high_24h: float | None = None,
        low_24h: float | None = None,
        open_price: float | None = None,
        take_profit: float | None = None,
        fx_to_inr: float = 1.0,
        lot_size: float = 1.0,
        segment: str | None = None,
    ) -> tuple[bool, str, UserRiskState]:
        del is_auto_trade
        with self._lock:
            state = self._state_for(subject)
            if not self._user_access.get(subject, True):
                return False, "USER_ACCESS_NOT_ENTITLED", state
            if not all(math.isfinite(value) for value in (entry_price, stop_loss, quantity)):
                return False, "INVALID_ENTRY_OR_STOP_LOSS", state
            if state.is_hard_locked:
                return False, state.locked_reason or "RISK_HARD_LOCKED", state
            if state.daily_pnl_inr <= state.daily_loss_limit_inr:
                self._activate_daily_loss_lock(state)
                return False, state.locked_reason, state
            if state.weekly_pnl_inr <= state.weekly_loss_limit_inr:
                self._activate_weekly_loss_lock(state)
                return False, state.locked_reason, state
            if state.trades_count >= state.max_trades_per_day:
                state.is_hard_locked = True
                state.locked_reason = "DAILY_TRADES_LIMIT_REACHED"
                return False, state.locked_reason, state
            if entry_price <= 0 or stop_loss <= 0 or quantity <= 0:
                return False, "INVALID_ENTRY_OR_STOP_LOSS", state
            if (side == "BUY" and stop_loss >= entry_price) or (
                side == "SELL" and stop_loss <= entry_price
            ):
                return False, "STOP_LOSS_ON_WRONG_SIDE_OF_ENTRY", state

            if all(v is not None for v in (broker, symbol, side, take_profit)):
                broker_type = BrokerType.SHOONYA if broker.upper() == "SHOONYA" else BrokerType.COINDCX
                segment_type = SegmentType.EQUITY
                if segment:
                    segment_upper = segment.upper()
                    if "FUT" in segment_upper:
                        segment_type = SegmentType.FUTURES
                    elif "OPT" in segment_upper or "CE" in segment_upper or "PE" in segment_upper:
                        segment_type = SegmentType.OPTIONS
                    elif broker_type == BrokerType.COINDCX:
                        segment_type = SegmentType.CRYPTO_FUTURES

                if broker_type == BrokerType.SHOONYA:
                    cost_breakdown = CostCalculator.calculate_shoonya_costs(
                        side=side,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        quantity=quantity,
                        lot_size=int(lot_size) if lot_size > 0 else 1,
                        segment=segment_type,
                    )
                else:
                    cost_breakdown = CostCalculator.calculate_coindcx_costs(
                        side=side,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        quantity=quantity,
                        fx_to_inr=fx_to_inr,
                    )

                if not cost_breakdown.is_viable:
                    return False, f"COST_GUARD_VETO: {cost_breakdown.veto_reason}", state

            if all(v is not None for v in (bid, ask, last_price)):
                veto = CostCalculator.check_risk_veto(
                    bid=bid,
                    ask=ask,
                    last_price=last_price,
                    volume_24h=volume_24h,
                    avg_volume_24h=avg_volume_24h,
                    high_24h=high_24h,
                    low_24h=low_24h,
                    open_price=open_price,
                )
                if veto.is_vetoed:
                    return False, f"RISK_VETO: {veto.reason}", state

            if all(v is not None for v in (symbol, side)):
                order_signature = f"{symbol}:{side}"
                now = datetime.now(timezone.utc)
                if (
                    state.last_order_signature == order_signature
                    and state.last_order_time
                    and (now - state.last_order_time).total_seconds() < 5
                ):
                    return False, "DUPLICATE_ORDER_VETO: Same symbol/side within 5s cooldown", state

            risk_budget = state.capital_inr * state.max_per_trade_risk_pct / 100
            risk_amount = abs(entry_price - stop_loss) * quantity
            if risk_amount > risk_budget:
                return False, f"PER_TRADE_RISK_CAP: INR {risk_budget:.2f}", state

            state.trades_count += 1
            state.weekly_trades_count += 1
            if state.trades_count >= state.max_trades_per_day:
                state.is_hard_locked = True
                state.locked_reason = "DAILY_TRADES_LIMIT_REACHED"
            if all(v is not None for v in (symbol, side)):
                state.last_order_signature = f"{symbol}:{side}"
                state.last_order_time = datetime.now(timezone.utc)
            return True, "OK", state

    def sync_pnl(self, subject: str, daily_pnl_inr: float, weekly_pnl_inr: float | None = None) -> UserRiskState:
        if not math.isfinite(float(daily_pnl_inr)):
            raise ValueError("daily P&L must be finite")
        with self._lock:
            state = self._state_for(subject)
            state.daily_pnl_inr = float(daily_pnl_inr)
            if weekly_pnl_inr is not None and math.isfinite(weekly_pnl_inr):
                state.weekly_pnl_inr = float(weekly_pnl_inr)
            if state.daily_pnl_inr <= state.daily_loss_limit_inr:
                self._activate_daily_loss_lock(state)
            if state.weekly_pnl_inr <= state.weekly_loss_limit_inr:
                self._activate_weekly_loss_lock(state)
            return state

    def rollback_entry_reservation(self, subject: str) -> UserRiskState:
        with self._lock:
            state = self._state_for(subject)
            if state.trades_count > 0:
                state.trades_count -= 1
            if state.locked_reason == "DAILY_TRADES_LIMIT_REACHED":
                state.is_hard_locked = False
                state.locked_reason = ""
            return state

    def sync_user_access(self, subject: str, allowed: bool) -> None:
        with self._lock:
            self._user_access[subject] = bool(allowed)

    def user_access_allowed(self, subject: str) -> bool:
        with self._lock:
            return self._user_access.get(subject, True)

    @staticmethod
    def _activate_daily_loss_lock(
        state: UserRiskState, now: datetime | None = None
    ) -> None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        state.is_hard_locked = True
        state.locked_reason = "DAILY_LOSS_LIMIT_BREACHED"
        if state.daily_loss_lock_started_at is None:
            state.daily_loss_lock_started_at = current
        minimum_expiry = state.daily_loss_lock_started_at + timedelta(hours=24)
        if (
            state.risk_lock_expires_at is None
            or state.risk_lock_expires_at < minimum_expiry
        ):
            state.risk_lock_expires_at = minimum_expiry

    @staticmethod
    def _activate_weekly_loss_lock(
        state: UserRiskState, now: datetime | None = None
    ) -> None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        state.is_hard_locked = True
        state.locked_reason = "WEEKLY_LOSS_LIMIT_BREACHED"
        if state.weekly_loss_lock_started_at is None:
            state.weekly_loss_lock_started_at = current
        minimum_expiry = state.weekly_loss_lock_started_at + timedelta(days=7)
        if (
            state.risk_lock_expires_at is None
            or state.risk_lock_expires_at < minimum_expiry
        ):
            state.risk_lock_expires_at = minimum_expiry

    def get_state(self, subject: str) -> UserRiskState:
        with self._lock:
            return self._state_for(subject)

    def restore_persisted(self, subject: str, values: dict) -> UserRiskState:
        with self._lock:
            if subject in self._states:
                state = self._state_for(subject)
                try:
                    incoming_trading_day = str(values["trading_day"])
                    if incoming_trading_day == state.trading_day:
                        state.daily_pnl_inr = float(values["daily_pnl_inr"])
                        state.weekly_pnl_inr = float(values.get("weekly_pnl_inr", 0))
                        state.trades_count = max(state.trades_count, int(values["trades_count"]))
                        state.weekly_trades_count = max(state.weekly_trades_count, int(values.get("weekly_trades_count", 0)))
                        state.capital_inr = min(state.capital_inr, float(values["capital_inr"]))
                        state.max_daily_loss_amount = min(
                            state.max_daily_loss_amount,
                            float(values["max_daily_loss_amount"]),
                        )
                        state.max_weekly_loss_amount = min(
                            state.max_weekly_loss_amount,
                            float(values.get("max_weekly_loss_amount", self.max_weekly_loss_amount)),
                        )
                        state.max_per_trade_risk_pct = min(
                            state.max_per_trade_risk_pct,
                            float(values["max_per_trade_risk_pct"]),
                        )
                        state.max_trades_per_day = min(
                            state.max_trades_per_day,
                            int(values["max_trades_per_day"]),
                        )
                        state.is_hard_locked = bool(
                            state.is_hard_locked or values["is_hard_locked"]
                        )
                        if values["locked_reason"]:
                            state.locked_reason = str(values["locked_reason"])
                    elif incoming_trading_day:
                        state.trading_day = incoming_trading_day
                        state.daily_pnl_inr = float(values["daily_pnl_inr"])
                        state.weekly_pnl_inr = float(values.get("weekly_pnl_inr", 0))
                        state.trades_count = int(values["trades_count"])
                        state.weekly_trades_count = int(values.get("weekly_trades_count", 0))
                        state.is_hard_locked = bool(values["is_hard_locked"])
                        state.locked_reason = str(values["locked_reason"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("Persisted risk state contains invalid values") from exc
                return self._state_for(subject)
            required = {
                "capital_inr",
                "max_per_trade_risk_pct",
                "max_trades_per_day",
                "daily_pnl_inr",
                "trades_count",
                "trading_day",
                "is_hard_locked",
                "locked_reason",
            }
            if not required.issubset(values):
                raise ValueError("Persisted risk state is incomplete")
            stored_loss_amount = values.get("max_daily_loss_amount")
            if stored_loss_amount is None:
                # Upgrade Redis records written before the amount-based schema.
                stored_loss_amount = (
                    float(values["capital_inr"])
                    * float(values.get("max_daily_loss_pct", 2.5))
                    / 100
                )
            state = UserRiskState(
                capital_inr=float(values["capital_inr"]),
                max_daily_loss_amount=min(
                    float(stored_loss_amount), self.max_daily_loss_amount
                ),
                max_weekly_loss_amount=min(
                    float(values.get("max_weekly_loss_amount", self.max_weekly_loss_amount)),
                    self.max_weekly_loss_amount,
                ),
                max_per_trade_risk_pct=min(float(values["max_per_trade_risk_pct"]), self.max_per_trade_risk_pct),
                max_trades_per_day=min(int(values["max_trades_per_day"]), self.max_daily_trades),
                daily_pnl_inr=float(values["daily_pnl_inr"]),
                weekly_pnl_inr=float(values.get("weekly_pnl_inr", 0)),
                trades_count=int(values["trades_count"]),
                weekly_trades_count=int(values.get("weekly_trades_count", 0)),
                trading_day=str(values["trading_day"]),
                trading_week=str(values.get("trading_week", self._current_week())),
                is_hard_locked=bool(values["is_hard_locked"]),
                locked_reason=str(values["locked_reason"]),
                risk_lock_expires_at=(
                    _parse_risk_expiry(values["risk_lock_expires_at"])
                    if values.get("risk_lock_expires_at")
                    else None
                ),
                daily_loss_lock_started_at=(
                    _parse_risk_expiry(values["daily_loss_lock_started_at"])
                    if values.get("daily_loss_lock_started_at")
                    else None
                ),
                weekly_loss_lock_started_at=(
                    _parse_risk_expiry(values["weekly_loss_lock_started_at"])
                    if values.get("weekly_loss_lock_started_at")
                    else None
                ),
            )
            if (
                state.is_hard_locked
                and state.locked_reason == "DAILY_LOSS_LIMIT_BREACHED"
                and state.daily_loss_lock_started_at is None
            ):
                now = datetime.now(timezone.utc)
                if state.risk_lock_expires_at is None or state.risk_lock_expires_at > now:
                    state.daily_loss_lock_started_at = now
                    minimum_expiry = now + timedelta(hours=24)
                    if (
                        state.risk_lock_expires_at is None
                        or state.risk_lock_expires_at < minimum_expiry
                    ):
                        state.risk_lock_expires_at = minimum_expiry
            elif (
                state.is_hard_locked
                and state.locked_reason == "DAILY_LOSS_LIMIT_BREACHED"
                and state.daily_loss_lock_started_at is not None
            ):
                minimum_expiry = state.daily_loss_lock_started_at + timedelta(hours=24)
                if (
                    state.risk_lock_expires_at is None
                    or state.risk_lock_expires_at < minimum_expiry
                ):
                    state.risk_lock_expires_at = minimum_expiry
            elif (
                state.is_hard_locked
                and state.locked_reason == "WEEKLY_LOSS_LIMIT_BREACHED"
                and state.weekly_loss_lock_started_at is None
            ):
                now = datetime.now(timezone.utc)
                if state.risk_lock_expires_at is None or state.risk_lock_expires_at > now:
                    state.weekly_loss_lock_started_at = now
                    minimum_expiry = now + timedelta(days=7)
                    if (
                        state.risk_lock_expires_at is None
                        or state.risk_lock_expires_at < minimum_expiry
                    ):
                        state.risk_lock_expires_at = minimum_expiry
            elif (
                state.is_hard_locked
                and state.locked_reason == "WEEKLY_LOSS_LIMIT_BREACHED"
                and state.weekly_loss_lock_started_at is not None
            ):
                minimum_expiry = state.weekly_loss_lock_started_at + timedelta(days=7)
                if (
                    state.risk_lock_expires_at is None
                    or state.risk_lock_expires_at < minimum_expiry
                ):
                    state.risk_lock_expires_at = minimum_expiry
            if (
                not all(math.isfinite(value) for value in (state.capital_inr, state.max_daily_loss_amount, state.max_weekly_loss_amount, state.max_per_trade_risk_pct, state.daily_pnl_inr, state.weekly_pnl_inr))
                or state.capital_inr <= 0
                or not 1 <= state.max_daily_loss_amount <= self.max_daily_loss_amount
                or not 1 <= state.max_weekly_loss_amount <= self.max_weekly_loss_amount
                or not 0.1 <= state.max_per_trade_risk_pct <= self.max_per_trade_risk_pct
                or not 1 <= state.max_trades_per_day <= self.max_daily_trades
                or state.trades_count < 0
                or state.weekly_trades_count < 0
            ):
                raise ValueError("Persisted risk state contains invalid values")
            self._states[subject] = state
            return self._state_for(subject)

    @staticmethod
    def to_record(state: UserRiskState) -> dict:
        return {
            "capital_inr": state.capital_inr,
            "max_daily_loss_amount": state.max_daily_loss_amount,
            "max_weekly_loss_amount": state.max_weekly_loss_amount,
            "max_per_trade_risk_pct": state.max_per_trade_risk_pct,
            "max_trades_per_day": state.max_trades_per_day,
            "daily_pnl_inr": state.daily_pnl_inr,
            "weekly_pnl_inr": state.weekly_pnl_inr,
            "trades_count": state.trades_count,
            "weekly_trades_count": state.weekly_trades_count,
            "trading_day": state.trading_day,
            "trading_week": state.trading_week,
            "is_hard_locked": state.is_hard_locked,
            "locked_reason": state.locked_reason,
            "risk_lock_expires_at": (
                state.risk_lock_expires_at.isoformat()
                if state.risk_lock_expires_at
                else None
            ),
            "daily_loss_lock_started_at": (
                state.daily_loss_lock_started_at.isoformat()
                if state.daily_loss_lock_started_at
                else None
            ),
            "weekly_loss_lock_started_at": (
                state.weekly_loss_lock_started_at.isoformat()
                if state.weekly_loss_lock_started_at
                else None
            ),
        }

    def global_lock(self, subject: str, reason: str) -> UserRiskState:
        with self._lock:
            state = self._state_for(subject)
            state.is_hard_locked = True
            state.locked_reason = reason
            return state

    def reset(self, subject: str, *, admin_override: bool = False) -> UserRiskState:
        with self._lock:
            state = self._state_for(subject)
            if (
                (
                    state.locked_reason == "DAILY_LOSS_LIMIT_BREACHED"
                    and state.risk_lock_expires_at
                    and state.risk_lock_expires_at > datetime.now(timezone.utc)
                )
                or (
                    state.locked_reason == "WEEKLY_LOSS_LIMIT_BREACHED"
                    and state.risk_lock_expires_at
                    and state.risk_lock_expires_at > datetime.now(timezone.utc)
                )
                or state.daily_pnl_inr <= state.daily_loss_limit_inr
                or state.weekly_pnl_inr <= state.weekly_loss_limit_inr
                or state.trades_count >= state.max_trades_per_day
            ) and not admin_override:
                raise PermissionError("A daily/weekly risk limit has been reached and cannot be reset today")
            state.is_hard_locked = False
            state.locked_reason = ""
            if admin_override:
                state.daily_pnl_inr = 0
                state.weekly_pnl_inr = 0
                state.trades_count = 0
                state.weekly_trades_count = 0
                state.risk_lock_expires_at = None
                state.daily_loss_lock_started_at = None
                state.weekly_loss_lock_started_at = None
            return state
