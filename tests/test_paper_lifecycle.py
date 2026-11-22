from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.paper_lifecycle import (
    advance_paper_position,
    close_paper_position_manually,
)


def _position(side):
    return {
        "side": side,
        "entry_price": 100,
        "quantity": 2,
        "stop_loss": 98 if side == "BUY" else 102,
        "take_profit": 104 if side == "BUY" else 96,
        "trailing_distance": 1,
        "favorable_price": 100,
        "status": "OPEN",
    }


def test_long_position_trails_up_then_closes_at_trailing_stop():
    now = datetime.now(timezone.utc)
    trailed = advance_paper_position(_position("BUY"), 103, now=now)
    assert trailed["status"] == "OPEN"
    assert trailed["favorable_price"] == 103
    assert trailed["stop_loss"] == 102

    closed = advance_paper_position(trailed, 101.5, now=now)
    assert closed["status"] == "CLOSED"
    assert closed["exit_price"] == 101.5
    assert closed["realized_pnl"] == 3


def test_short_position_trails_down_and_respects_take_profit():
    trailed = advance_paper_position(_position("SELL"), 97)
    assert trailed["favorable_price"] == 97
    assert trailed["stop_loss"] == 98

    closed = advance_paper_position(trailed, 95)
    assert closed["status"] == "CLOSED"
    assert closed["realized_pnl"] == 10


def test_invalid_position_data_is_rejected():
    with pytest.raises(ValueError):
        advance_paper_position(_position("BUY"), float("nan"))


def test_manual_exit_closes_position_and_sets_setup_cooldown():
    now = datetime.now(timezone.utc)
    closed = close_paper_position_manually(_position("BUY"), 95, now=now)

    assert closed["status"] == "CLOSED"
    assert closed["exit_reason"] == "MANUAL_EXIT"
    assert closed["exit_price"] == 95
    assert closed["realized_pnl"] == -10
    assert closed["manual_exit_blocked_until"] == now.replace(
        microsecond=now.microsecond
    ) + timedelta(hours=24)

    with pytest.raises(ValueError):
        close_paper_position_manually(closed, 90, now=now)
