from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class BrokerType(Enum):
    SHOONYA = "SHOONYA"
    COINDCX = "COINDCX"


class SegmentType(Enum):
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"
    CRYPTO_FUTURES = "CRYPTO_FUTURES"


@dataclass(frozen=True)
class CostBreakdown:
    gross_reward: float
    gross_risk: float
    brokerage: float
    stt: float
    exchange_txn: float
    gst: float
    stamp_duty: float
    tds: float
    slippage_buffer: float
    total_costs: float
    net_reward: float
    net_risk: float
    net_rr_ratio: float
    is_viable: bool
    veto_reason: str | None = None


@dataclass(frozen=True)
class VetoResult:
    is_vetoed: bool
    reason: str | None = None
    spread_pct: float | None = None
    liquidity_score: float | None = None
    fake_breakout_detected: bool = False


class CostCalculator:
    SHOONYA_BROKERAGE_PER_ORDER = 20.0
    SHOONYA_STT_PCT = 0.025
    SHOONYA_EXCHANGE_TXN_PCT = 0.00345
    SHOONYA_GST_PCT = 18.0
    SHOONYA_STAMP_DUTY_PCT = 0.003

    COINDCX_TRADING_FEE_PCT = 0.05
    COINDCX_TDS_PCT = 1.0
    COINDCX_GST_PCT = 18.0

    MIN_NET_RR_THRESHOLD = 1.5
    MAX_SPREAD_PCT_VETO = 0.5
    MIN_LIQUIDITY_SCORE = 30.0
    FAKE_BREAKOUT_WICK_RATIO = 0.6
    FAKE_BREAKOUT_VOLUME_RATIO = 0.5
    SLIPPAGE_BPS_EQUITY = 5
    SLIPPAGE_BPS_FUTURES = 3
    SLIPPAGE_BPS_OPTIONS = 10
    SLIPPAGE_BPS_CRYPTO = 2

    @classmethod
    def calculate_shoonya_costs(
        cls,
        *,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float,
        lot_size: int,
        segment: SegmentType,
    ) -> CostBreakdown:
        if not all(math.isfinite(v) for v in (entry_price, stop_loss, take_profit, quantity)):
            return cls._invalid_breakdown("Non-finite price/quantity")

        risk_distance = abs(entry_price - stop_loss)
        reward_distance = abs(take_profit - entry_price)

        if risk_distance <= 0 or reward_distance <= 0:
            return cls._invalid_breakdown("Invalid risk/reward distance")

        gross_risk = risk_distance * quantity
        gross_reward = reward_distance * quantity

        brokerage = cls.SHOONYA_BROKERAGE_PER_ORDER * 2
        turnover = entry_price * quantity
        stt = turnover * cls.SHOONYA_STT_PCT / 100
        exchange_txn = turnover * cls.SHOONYA_EXCHANGE_TXN_PCT / 100
        gst = (brokerage + exchange_txn) * cls.SHOONYA_GST_PCT / 100
        stamp_duty = turnover * cls.SHOONYA_STAMP_DUTY_PCT / 100

        if segment == SegmentType.EQUITY:
            slippage_bps = cls.SLIPPAGE_BPS_EQUITY
        elif segment == SegmentType.FUTURES:
            slippage_bps = cls.SLIPPAGE_BPS_FUTURES
        else:
            slippage_bps = cls.SLIPPAGE_BPS_OPTIONS

        slippage_buffer = turnover * slippage_bps / 10000 * 2

        total_costs = brokerage + stt + exchange_txn + gst + stamp_duty + slippage_buffer
        net_reward = gross_reward - total_costs
        net_risk = gross_risk + total_costs

        net_rr = net_reward / net_risk if net_risk > 0 else 0
        is_viable = net_reward > 0 and net_rr >= cls.MIN_NET_RR_THRESHOLD

        veto_reason = None
        if not is_viable:
            if net_reward <= 0:
                veto_reason = f"NET_PROFIT_NON_POSITIVE: gross={gross_reward:.2f} costs={total_costs:.2f}"
            else:
                veto_reason = f"NET_RR_BELOW_THRESHOLD: {net_rr:.2f} < {cls.MIN_NET_RR_THRESHOLD}"

        return CostBreakdown(
            gross_reward=gross_reward,
            gross_risk=gross_risk,
            brokerage=brokerage,
            stt=stt,
            exchange_txn=exchange_txn,
            gst=gst,
            stamp_duty=stamp_duty,
            tds=0.0,
            slippage_buffer=slippage_buffer,
            total_costs=total_costs,
            net_reward=net_reward,
            net_risk=net_risk,
            net_rr_ratio=round(net_rr, 2),
            is_viable=is_viable,
            veto_reason=veto_reason,
        )

    @classmethod
    def calculate_coindcx_costs(
        cls,
        *,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float,
        fx_to_inr: float,
    ) -> CostBreakdown:
        if not all(math.isfinite(v) for v in (entry_price, stop_loss, take_profit, quantity, fx_to_inr)):
            return cls._invalid_breakdown("Non-finite price/quantity/fx")

        risk_distance = abs(entry_price - stop_loss)
        reward_distance = abs(take_profit - entry_price)

        if risk_distance <= 0 or reward_distance <= 0:
            return cls._invalid_breakdown("Invalid risk/reward distance")

        gross_risk = risk_distance * quantity * fx_to_inr
        gross_reward = reward_distance * quantity * fx_to_inr

        turnover = entry_price * quantity
        trading_fee = turnover * cls.COINDCX_TRADING_FEE_PCT / 100 * 2 * fx_to_inr
        tds = turnover * cls.COINDCX_TDS_PCT / 100 * fx_to_inr
        gst = trading_fee * cls.COINDCX_GST_PCT / 100

        slippage_buffer = turnover * cls.SLIPPAGE_BPS_CRYPTO / 10000 * 2 * fx_to_inr

        total_costs = trading_fee + tds + gst + slippage_buffer
        net_reward = gross_reward - total_costs
        net_risk = gross_risk + total_costs

        net_rr = net_reward / net_risk if net_risk > 0 else 0
        is_viable = net_reward > 0 and net_rr >= cls.MIN_NET_RR_THRESHOLD

        veto_reason = None
        if not is_viable:
            if net_reward <= 0:
                veto_reason = f"NET_PROFIT_NON_POSITIVE: gross={gross_reward:.2f} costs={total_costs:.2f}"
            else:
                veto_reason = f"NET_RR_BELOW_THRESHOLD: {net_rr:.2f} < {cls.MIN_NET_RR_THRESHOLD}"

        return CostBreakdown(
            gross_reward=gross_reward,
            gross_risk=gross_risk,
            brokerage=trading_fee,
            stt=0.0,
            exchange_txn=0.0,
            gst=gst,
            stamp_duty=0.0,
            tds=tds,
            slippage_buffer=slippage_buffer,
            total_costs=total_costs,
            net_reward=net_reward,
            net_risk=net_risk,
            net_rr_ratio=round(net_rr, 2),
            is_viable=is_viable,
            veto_reason=veto_reason,
        )

    @classmethod
    def _invalid_breakdown(cls, reason: str) -> CostBreakdown:
        return CostBreakdown(
            gross_reward=0,
            gross_risk=0,
            brokerage=0,
            stt=0,
            exchange_txn=0,
            gst=0,
            stamp_duty=0,
            tds=0,
            slippage_buffer=0,
            total_costs=0,
            net_reward=0,
            net_risk=0,
            net_rr_ratio=0,
            is_viable=False,
            veto_reason=reason,
        )

    @classmethod
    def check_risk_veto(
        cls,
        *,
        bid: float | None,
        ask: float | None,
        last_price: float,
        volume_24h: float | None,
        avg_volume_24h: float | None,
        high_24h: float | None,
        low_24h: float | None,
        open_price: float | None,
    ) -> VetoResult:
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return VetoResult(
                is_vetoed=True,
                reason="MISSING_BID_ASK: Cannot assess spread/liquidity",
                spread_pct=None,
                liquidity_score=None,
            )

        spread_pct = (ask - bid) / ((ask + bid) / 2) * 100

        if spread_pct > cls.MAX_SPREAD_PCT_VETO:
            return VetoResult(
                is_vetoed=True,
                reason=f"WIDE_SPREAD_VETO: {spread_pct:.2f}% > {cls.MAX_SPREAD_PCT_VETO}%",
                spread_pct=round(spread_pct, 2),
                liquidity_score=None,
            )

        fake_breakout = False
        if all(v is not None and v > 0 for v in (high_24h, low_24h, open_price, last_price, volume_24h, avg_volume_24h)):
            range_24h = high_24h - low_24h
            if range_24h > 0:
                upper_wick = high_24h - max(open_price, last_price)
                lower_wick = min(open_price, last_price) - low_24h
                body = abs(last_price - open_price)

                vol_ratio = volume_24h / avg_volume_24h if avg_volume_24h > 0 else 0

                if last_price > open_price:
                    if upper_wick / range_24h > cls.FAKE_BREAKOUT_WICK_RATIO and vol_ratio < cls.FAKE_BREAKOUT_VOLUME_RATIO:
                        fake_breakout = True
                elif last_price < open_price:
                    if lower_wick / range_24h > cls.FAKE_BREAKOUT_WICK_RATIO and vol_ratio < cls.FAKE_BREAKOUT_VOLUME_RATIO:
                        fake_breakout = True

        if fake_breakout:
            return VetoResult(
                is_vetoed=True,
                reason="FAKE_BREAKOUT_VETO: Wick rejection without volume confirmation",
                spread_pct=round(spread_pct, 2),
                liquidity_score=None,
                fake_breakout_detected=True,
            )

        liquidity_score = 100.0
        if volume_24h is not None and avg_volume_24h is not None and avg_volume_24h > 0:
            vol_ratio = volume_24h / avg_volume_24h
            liquidity_score = min(100.0, vol_ratio * 50)

        if liquidity_score < cls.MIN_LIQUIDITY_SCORE:
            return VetoResult(
                is_vetoed=True,
                reason=f"LOW_LIQUIDITY_VETO: score={liquidity_score:.1f} < {cls.MIN_LIQUIDITY_SCORE}",
                spread_pct=round(spread_pct, 2),
                liquidity_score=round(liquidity_score, 1),
            )

        return VetoResult(
            is_vetoed=False,
            reason=None,
            spread_pct=round(spread_pct, 2),
            liquidity_score=round(liquidity_score, 1),
            fake_breakout_detected=False,
        )