from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from threading import RLock

INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


@dataclass
class UserRiskState:
    capital_inr: float = 100000
    max_daily_loss_pct: float = 2.5
    max_per_trade_risk_pct: float = 2.0
    max_trades_per_day: int = 5
    daily_pnl_inr: float = 0
    trades_count: int = 0
    trading_day: str = ""
    is_hard_locked: bool = False
    locked_reason: str = ""

    @property
    def daily_loss_limit_inr(self) -> float:
        return -abs(self.capital_inr * self.max_daily_loss_pct / 100)


class RiskGuardian:
    def __init__(self) -> None:
        self._states: dict[str, UserRiskState] = {}
        self._lock = RLock()

    @staticmethod
    def _today() -> str:
        return datetime.now(INDIA_TIMEZONE).date().isoformat()

    @classmethod
    def current_trading_day(cls) -> str:
        return cls._today()

    def _state_for(self, subject: str) -> UserRiskState:
        today = self._today()
        state = self._states.setdefault(subject, UserRiskState(trading_day=today))
        if state.trading_day != today:
            state.trading_day = today
            state.daily_pnl_inr = 0
            state.trades_count = 0
            state.is_hard_locked = False
            state.locked_reason = ""
        return state

    def configure(
        self,
        subject: str,
        capital_inr: float,
        max_daily_loss_pct: float,
        max_trades_per_day: int,
        max_per_trade_risk_pct: float,
    ) -> UserRiskState:
        with self._lock:
            state = self._state_for(subject)
            if state.trades_count > 0 or state.is_hard_locked:
                raise PermissionError("Risk settings are locked after the first trade or hard lock")
            state.capital_inr = max(5000, min(capital_inr, 100000000))
            state.max_daily_loss_pct = max(0.5, min(max_daily_loss_pct, 10))
            state.max_trades_per_day = max(1, min(max_trades_per_day, 20))
            state.max_per_trade_risk_pct = max(0.1, min(max_per_trade_risk_pct, 2))
            return state

    def precheck_entry(
        self,
        *,
        subject: str,
        entry_price: float,
        stop_loss: float,
        quantity: float,
        is_auto_trade: bool,
    ) -> tuple[bool, str, UserRiskState]:
        del is_auto_trade
        with self._lock:
            state = self._state_for(subject)
            if state.is_hard_locked or state.daily_pnl_inr <= state.daily_loss_limit_inr:
                state.is_hard_locked = True
                state.locked_reason = "DAILY_LOSS_LIMIT_BREACHED"
                return False, state.locked_reason, state
            if state.trades_count >= state.max_trades_per_day:
                return False, "OVERTRADING_BLOCKED", state
            if entry_price <= 0 or stop_loss <= 0 or quantity <= 0:
                return False, "INVALID_ENTRY_OR_STOP_LOSS", state

            risk_budget = state.capital_inr * state.max_per_trade_risk_pct / 100
            risk_amount = abs(entry_price - stop_loss) * quantity
            if risk_amount > risk_budget:
                return False, f"PER_TRADE_RISK_CAP: INR {risk_budget:.2f}", state

            state.trades_count += 1
            return True, "OK", state

    def sync_pnl(self, subject: str, daily_pnl_inr: float) -> UserRiskState:
        with self._lock:
            state = self._state_for(subject)
            state.daily_pnl_inr = float(daily_pnl_inr)
            if state.daily_pnl_inr <= state.daily_loss_limit_inr:
                state.is_hard_locked = True
                state.locked_reason = "DAILY_LOSS_LIMIT_BREACHED"
            return state

    def get_state(self, subject: str) -> UserRiskState:
        with self._lock:
            return self._state_for(subject)

    def restore_persisted(self, subject: str, values: dict) -> UserRiskState:
        with self._lock:
            if subject in self._states:
                return self._state_for(subject)
            required = {
                "capital_inr",
                "max_daily_loss_pct",
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
            state = UserRiskState(
                capital_inr=float(values["capital_inr"]),
                max_daily_loss_pct=float(values["max_daily_loss_pct"]),
                max_per_trade_risk_pct=float(values["max_per_trade_risk_pct"]),
                max_trades_per_day=int(values["max_trades_per_day"]),
                daily_pnl_inr=float(values["daily_pnl_inr"]),
                trades_count=int(values["trades_count"]),
                trading_day=str(values["trading_day"]),
                is_hard_locked=bool(values["is_hard_locked"]),
                locked_reason=str(values["locked_reason"]),
            )
            if (
                not all(math.isfinite(value) for value in (state.capital_inr, state.max_daily_loss_pct, state.max_per_trade_risk_pct, state.daily_pnl_inr))
                or state.capital_inr <= 0
                or not 0.5 <= state.max_daily_loss_pct <= 10
                or not 0.1 <= state.max_per_trade_risk_pct <= 2
                or not 1 <= state.max_trades_per_day <= 20
                or state.trades_count < 0
            ):
                raise ValueError("Persisted risk state contains invalid values")
            self._states[subject] = state
            return self._state_for(subject)

    @staticmethod
    def to_record(state: UserRiskState) -> dict:
        return {
            "capital_inr": state.capital_inr,
            "max_daily_loss_pct": state.max_daily_loss_pct,
            "max_per_trade_risk_pct": state.max_per_trade_risk_pct,
            "max_trades_per_day": state.max_trades_per_day,
            "daily_pnl_inr": state.daily_pnl_inr,
            "trades_count": state.trades_count,
            "trading_day": state.trading_day,
            "is_hard_locked": state.is_hard_locked,
            "locked_reason": state.locked_reason,
        }

    def global_lock(self, subject: str, reason: str) -> UserRiskState:
        with self._lock:
            state = self._state_for(subject)
            state.is_hard_locked = True
            state.locked_reason = reason
            return state

    def reset(self, subject: str) -> UserRiskState:
        with self._lock:
            state = self._state_for(subject)
            state.is_hard_locked = False
            state.locked_reason = ""
            state.daily_pnl_inr = 0
            state.trades_count = 0
            return state
